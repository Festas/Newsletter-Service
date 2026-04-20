"""Tests for FastAPI endpoints using TestClient."""

import importlib
import os
import tempfile
import unittest
from base64 import b64encode
from unittest.mock import AsyncMock, patch

os.environ["SMTP_HOST"] = ""
os.environ["ADMIN_USER"] = "testadmin"
os.environ["ADMIN_PASSWORD"] = "testpass"
os.environ["SESSION_SECRET"] = "test-secret"
os.environ["BRAND_NAME"] = "TestNewsletter"


class APITestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["DATABASE_PATH"] = os.path.join(self.tempdir.name, "test.db")

        import app.database
        importlib.reload(app.database)
        app.database.DATABASE_PATH = os.environ["DATABASE_PATH"]
        app.database.init_db()

        # Clear rate limiters between tests
        import app.auth
        app.auth._login_attempts.clear()

        # Import after setting env
        from fastapi.testclient import TestClient

        import app.main
        importlib.reload(app.main)
        app.main._rate_bucket.clear()
        self.client = TestClient(app.main.app)
        self.db = app.database

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _admin_auth(self) -> dict[str, str]:
        creds = b64encode(b"testadmin:testpass").decode()
        return {"Authorization": f"Basic {creds}"}

    def _login_session(self) -> None:
        self.client.post("/admin/login", data={"username": "testadmin", "password": "testpass"})


class SubscribeTests(APITestBase):
    def test_subscribe_form(self) -> None:
        with patch("app.main.email_service.send_confirmation_email", new_callable=AsyncMock):
            resp = self.client.post("/subscribe", data={"email": "test@example.com"})
        self.assertEqual(resp.status_code, 201)
        self.assertIn("confirm via email", resp.json()["message"])

    def test_subscribe_json(self) -> None:
        with patch("app.main.email_service.send_confirmation_email", new_callable=AsyncMock):
            resp = self.client.post(
                "/subscribe",
                json={"email": "json@example.com"},
                headers={"content-type": "application/json"},
            )
        self.assertEqual(resp.status_code, 201)

    def test_subscribe_invalid_email(self) -> None:
        resp = self.client.post("/subscribe", data={"email": "notanemail"})
        self.assertEqual(resp.status_code, 400)

    def test_subscribe_empty_email(self) -> None:
        resp = self.client.post("/subscribe", data={"email": ""})
        self.assertEqual(resp.status_code, 400)

    def test_subscribe_already_confirmed(self) -> None:
        self.db.add_subscriber_manual("exists@example.com")
        with patch("app.main.email_service.send_confirmation_email", new_callable=AsyncMock):
            resp = self.client.post("/subscribe", data={"email": "exists@example.com"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("already subscribed", resp.json()["message"])


class ConfirmTests(APITestBase):
    def test_confirm_valid(self) -> None:
        self.db.create_or_update_subscriber("user@example.com", "valid-token")
        resp = self.client.get("/confirm?token=valid-token")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Confirmed", resp.text)

    def test_confirm_invalid(self) -> None:
        resp = self.client.get("/confirm?token=bad-token")
        self.assertEqual(resp.status_code, 400)

    def test_confirm_rotates_token(self) -> None:
        self.db.create_or_update_subscriber("rot@example.com", "rot-token")
        self.client.get("/confirm?token=rot-token")
        # Old token should no longer work for unsubscribe
        sub = self.db.get_subscriber_by_email("rot@example.com")
        self.assertNotEqual(sub["token"], "rot-token")


class UnsubscribeTests(APITestBase):
    def test_unsubscribe_valid(self) -> None:
        self.db.add_subscriber_manual("unsub@example.com")
        sub = self.db.get_subscriber_by_email("unsub@example.com")
        resp = self.client.get(f"/unsubscribe?token={sub['token']}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Unsubscribed", resp.text)

    def test_unsubscribe_invalid(self) -> None:
        resp = self.client.get("/unsubscribe?token=bad-token")
        self.assertEqual(resp.status_code, 400)


class HealthTests(APITestBase):
    def test_health(self) -> None:
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "healthy")


class AdminAuthTests(APITestBase):
    def test_admin_requires_login(self) -> None:
        resp = self.client.get("/admin")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Login", resp.text)

    def test_admin_login_success(self) -> None:
        resp = self.client.post(
            "/admin/login",
            data={"username": "testadmin", "password": "testpass"},
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 303)

    def test_admin_login_failure(self) -> None:
        resp = self.client.post(
            "/admin/login",
            data={"username": "wrong", "password": "wrong"},
        )
        self.assertEqual(resp.status_code, 401)

    def test_admin_logout(self) -> None:
        self._login_session()
        resp = self.client.post("/admin/logout", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)

    def test_admin_dashboard_logged_in(self) -> None:
        self._login_session()
        resp = self.client.get("/admin")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Dashboard", resp.text)


class APIEndpointTests(APITestBase):
    def test_subscribers_api(self) -> None:
        self.db.add_subscriber_manual("api@example.com")
        resp = self.client.get("/subscribers", headers=self._admin_auth())
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["count"], 1)

    def test_subscribers_api_unauthorized(self) -> None:
        resp = self.client.get("/subscribers")
        self.assertEqual(resp.status_code, 401)

    def test_get_subscriber_api(self) -> None:
        self.db.add_subscriber_manual("single@example.com")
        sub = self.db.get_subscriber_by_email("single@example.com")
        resp = self.client.get(f"/subscribers/{sub['id']}", headers=self._admin_auth())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["email"], "single@example.com")

    def test_get_subscriber_not_found(self) -> None:
        resp = self.client.get("/subscribers/9999", headers=self._admin_auth())
        self.assertEqual(resp.status_code, 404)

    def test_delete_subscriber_api(self) -> None:
        self.db.add_subscriber_manual("del@example.com")
        sub = self.db.get_subscriber_by_email("del@example.com")
        resp = self.client.delete(f"/subscribers/{sub['id']}", headers=self._admin_auth())
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["deleted"])

    def test_newsletters_api(self) -> None:
        self.db.create_newsletter(subject="Test NL", status="sent")
        resp = self.client.get("/newsletters", headers=self._admin_auth())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["count"], 1)

    def test_get_newsletter_api(self) -> None:
        nl_id = self.db.create_newsletter(subject="Detail NL", status="sent")
        resp = self.client.get(f"/newsletters/{nl_id}", headers=self._admin_auth())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["subject"], "Detail NL")


class TrackingTests(APITestBase):
    def test_tracking_pixel(self) -> None:
        self.db.add_subscriber_manual("track@example.com")
        sub = self.db.get_subscriber_by_email("track@example.com")
        nl_id = self.db.create_newsletter(subject="Track", status="sent")
        resp = self.client.get(f"/track/open?newsletter_id={nl_id}&subscriber_id={sub['id']}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["content-type"], "image/png")

        stats = self.db.get_newsletter_analytics(nl_id)
        self.assertEqual(stats.get("open"), 1)

    def test_click_tracking(self) -> None:
        self.db.add_subscriber_manual("click@example.com")
        sub = self.db.get_subscriber_by_email("click@example.com")
        nl_id = self.db.create_newsletter(subject="Click", status="sent")
        resp = self.client.get(
            f"/track/click?newsletter_id={nl_id}&subscriber_id={sub['id']}&url=https://example.com",
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 307)

        stats = self.db.get_newsletter_analytics(nl_id)
        self.assertEqual(stats.get("click"), 1)


class ArchiveTests(APITestBase):
    def test_archive_page(self) -> None:
        self.db.create_newsletter(subject="Public NL", status="sent")
        resp = self.client.get("/archive")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Public NL", resp.text)

    def test_archive_detail(self) -> None:
        from app.database import _now_iso
        nl_id = self.db.create_newsletter(subject="Detail", body_html="<p>Hello</p>", status="sent")
        self.db.update_newsletter(nl_id, sent_at=_now_iso())
        resp = self.client.get(f"/archive/{nl_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Detail", resp.text)

    def test_archive_detail_not_found(self) -> None:
        resp = self.client.get("/archive/9999")
        self.assertEqual(resp.status_code, 404)

    def test_archive_draft_not_visible(self) -> None:
        nl_id = self.db.create_newsletter(subject="Draft", status="draft")
        resp = self.client.get(f"/archive/{nl_id}")
        self.assertEqual(resp.status_code, 404)


class EmbedTests(APITestBase):
    def test_embed_page(self) -> None:
        resp = self.client.get("/embed")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Subscribe", resp.text)


class AdminFunctionalTests(APITestBase):
    def test_admin_add_subscriber(self) -> None:
        self._login_session()
        resp = self.client.post(
            "/admin/subscriber/add",
            data={"email": "added@example.com", "tags": "vip", "notes": "test"},
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 303)
        sub = self.db.get_subscriber_by_email("added@example.com")
        self.assertIsNotNone(sub)

    def test_admin_delete_subscriber(self) -> None:
        self._login_session()
        self.db.add_subscriber_manual("todel@example.com")
        sub = self.db.get_subscriber_by_email("todel@example.com")
        resp = self.client.post(f"/admin/subscriber/{sub['id']}/delete", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)

    def test_admin_save_draft(self) -> None:
        self._login_session()
        resp = self.client.post(
            "/admin/draft/save",
            data={"subject": "My Draft", "body_text": "Content", "body_html": "", "template": "minimal"},
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 303)
        drafts, _ = self.db.list_newsletters(status_filter="draft")
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0]["subject"], "My Draft")

    def test_admin_delete_draft(self) -> None:
        self._login_session()
        nl_id = self.db.create_newsletter(subject="To Del", status="draft")
        resp = self.client.post(f"/admin/draft/{nl_id}/delete", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertIsNone(self.db.get_newsletter(nl_id))

    def test_admin_export_csv(self) -> None:
        self._login_session()
        self.db.add_subscriber_manual("csv@example.com")
        resp = self.client.get("/admin/subscribers/export")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("csv@example.com", resp.text)
        self.assertEqual(resp.headers["content-type"], "text/csv; charset=utf-8")


class RateLimitTests(APITestBase):
    def test_subscribe_rate_limit(self) -> None:
        with patch("app.main.email_service.send_confirmation_email", new_callable=AsyncMock):
            for i in range(5):
                self.client.post("/subscribe", data={"email": f"rl{i}@example.com"})
            resp = self.client.post("/subscribe", data={"email": "rl99@example.com"})
        self.assertEqual(resp.status_code, 429)


if __name__ == "__main__":
    unittest.main()
