import importlib
import os
import tempfile
import unittest


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["DATABASE_PATH"] = os.path.join(self.tempdir.name, "test.db")

        import app.database
        importlib.reload(app.database)
        self.db = app.database
        self.db.DATABASE_PATH = os.environ["DATABASE_PATH"]
        self.db.init_db()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_create_confirm_unsubscribe_flow(self) -> None:
        token = "token-1"
        created = self.db.create_or_update_subscriber("user@example.com", token)
        self.assertEqual(created["status"], "created")

        confirmed = self.db.confirm_by_token(token)
        self.assertTrue(confirmed)

        subscribers, total = self.db.list_subscribers()
        self.assertEqual(total, 1)
        self.assertEqual(subscribers[0]["confirmed"], 1)

        # After confirm, original token is rotated — get new token
        sub = self.db.get_subscriber_by_email("user@example.com")
        self.assertIsNotNone(sub)
        new_token = sub["token"]
        self.assertNotEqual(new_token, token)

        unsubscribed = self.db.unsubscribe_by_token(new_token)
        self.assertTrue(unsubscribed)

        subscribers_after, _ = self.db.list_subscribers()
        self.assertEqual(subscribers_after[0]["confirmed"], 0)

    def test_duplicate_subscribe(self) -> None:
        self.db.create_or_update_subscriber("dup@example.com", "t1")
        result = self.db.create_or_update_subscriber("dup@example.com", "t2")
        self.assertEqual(result["status"], "updated")

    def test_already_confirmed(self) -> None:
        self.db.create_or_update_subscriber("conf@example.com", "t1")
        self.db.confirm_by_token("t1")
        result = self.db.create_or_update_subscriber("conf@example.com", "t3")
        self.assertEqual(result["status"], "already_confirmed")

    def test_invalid_token(self) -> None:
        self.assertFalse(self.db.confirm_by_token("nonexistent"))
        self.assertFalse(self.db.unsubscribe_by_token("nonexistent"))

    def test_list_subscribers_pagination(self) -> None:
        for i in range(10):
            self.db.create_or_update_subscriber(f"user{i}@example.com", f"tok{i}")

        page1, total = self.db.list_subscribers(page=1, per_page=3)
        self.assertEqual(total, 10)
        self.assertEqual(len(page1), 3)

        page4, _ = self.db.list_subscribers(page=4, per_page=3)
        self.assertEqual(len(page4), 1)

    def test_list_subscribers_search(self) -> None:
        self.db.create_or_update_subscriber("alice@example.com", "t1")
        self.db.create_or_update_subscriber("bob@example.com", "t2")

        results, total = self.db.list_subscribers(search="alice")
        self.assertEqual(total, 1)
        self.assertEqual(results[0]["email"], "alice@example.com")

    def test_add_subscriber_manual(self) -> None:
        result = self.db.add_subscriber_manual("manual@example.com", tags="vip", notes="Test")
        self.assertEqual(result["status"], "created")
        sub = self.db.get_subscriber_by_email("manual@example.com")
        self.assertIsNotNone(sub)
        self.assertEqual(sub["confirmed"], 1)
        self.assertEqual(sub["tags"], "vip")

    def test_add_subscriber_manual_duplicate(self) -> None:
        self.db.add_subscriber_manual("dup@example.com")
        result = self.db.add_subscriber_manual("dup@example.com")
        self.assertEqual(result["status"], "duplicate")

    def test_delete_subscriber(self) -> None:
        self.db.create_or_update_subscriber("del@example.com", "t1")
        sub = self.db.get_subscriber_by_email("del@example.com")
        self.assertTrue(self.db.delete_subscriber(sub["id"]))
        self.assertIsNone(self.db.get_subscriber_by_email("del@example.com"))

    def test_delete_nonexistent_subscriber(self) -> None:
        self.assertFalse(self.db.delete_subscriber(9999))

    def test_tags(self) -> None:
        self.db.add_subscriber_manual("tagged@example.com", tags="alpha,beta")
        tags = self.db.get_all_tags()
        self.assertIn("alpha", tags)
        self.assertIn("beta", tags)

        results, _ = self.db.list_subscribers(tag="alpha")
        self.assertEqual(len(results), 1)

    def test_update_subscriber_tags(self) -> None:
        self.db.add_subscriber_manual("upd@example.com", tags="old")
        sub = self.db.get_subscriber_by_email("upd@example.com")
        self.db.update_subscriber_tags(sub["id"], "new,updated")
        sub2 = self.db.get_subscriber(sub["id"])
        self.assertEqual(sub2["tags"], "new,updated")

    def test_subscriber_notes(self) -> None:
        self.db.add_subscriber_manual("notes@example.com")
        sub = self.db.get_subscriber_by_email("notes@example.com")
        self.db.update_subscriber_notes(sub["id"], "Important customer")
        sub2 = self.db.get_subscriber(sub["id"])
        self.assertEqual(sub2["notes"], "Important customer")

    def test_newsletter_crud(self) -> None:
        nl_id = self.db.create_newsletter(subject="Test", body_text="Hello", status="draft")
        self.assertIsNotNone(nl_id)

        nl = self.db.get_newsletter(nl_id)
        self.assertEqual(nl["subject"], "Test")
        self.assertEqual(nl["status"], "draft")

        self.db.update_newsletter(nl_id, subject="Updated", status="sent", recipient_count=5)
        nl2 = self.db.get_newsletter(nl_id)
        self.assertEqual(nl2["subject"], "Updated")
        self.assertEqual(nl2["recipient_count"], 5)

    def test_newsletter_list_filter(self) -> None:
        self.db.create_newsletter(subject="Draft 1", status="draft")
        self.db.create_newsletter(subject="Sent 1", status="sent")

        drafts, total_d = self.db.list_newsletters(status_filter="draft")
        self.assertEqual(total_d, 1)
        self.assertEqual(drafts[0]["subject"], "Draft 1")

        sent, total_s = self.db.list_newsletters(status_filter="sent")
        self.assertEqual(total_s, 1)

    def test_delete_newsletter_draft_only(self) -> None:
        nl_id = self.db.create_newsletter(subject="Draft", status="draft")
        self.assertTrue(self.db.delete_newsletter(nl_id))

        nl_id2 = self.db.create_newsletter(subject="Sent", status="sent")
        self.assertFalse(self.db.delete_newsletter(nl_id2))

    def test_analytics(self) -> None:
        self.db.add_subscriber_manual("analytics@example.com")
        sub = self.db.get_subscriber_by_email("analytics@example.com")
        nl_id = self.db.create_newsletter(subject="NL", status="sent")

        self.db.record_analytics_event(nl_id, sub["id"], "open")
        self.db.record_analytics_event(nl_id, sub["id"], "click", url="https://example.com")

        stats = self.db.get_newsletter_analytics(nl_id)
        self.assertEqual(stats["open"], 1)
        self.assertEqual(stats["click"], 1)

    def test_delivery_log(self) -> None:
        self.db.add_subscriber_manual("delivery@example.com")
        sub = self.db.get_subscriber_by_email("delivery@example.com")
        nl_id = self.db.create_newsletter(subject="NL", status="sent")

        self.db.record_delivery(nl_id, sub["id"], "sent")
        self.db.record_delivery(nl_id, sub["id"], "failed", "Connection timeout")

        failures = self.db.get_delivery_failures(nl_id)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["error_message"], "Connection timeout")

    def test_webhooks(self) -> None:
        wh_id = self.db.create_webhook("https://example.com/hook", "all")
        hooks = self.db.list_webhooks()
        self.assertEqual(len(hooks), 1)
        self.assertEqual(hooks[0]["url"], "https://example.com/hook")

        self.assertTrue(self.db.delete_webhook(wh_id))
        self.assertEqual(len(self.db.list_webhooks()), 0)

    def test_subscriber_growth_data(self) -> None:
        self.db.add_subscriber_manual("g1@example.com")
        self.db.add_subscriber_manual("g2@example.com")
        data = self.db.get_subscriber_count_by_date()
        self.assertGreater(len(data), 0)
        self.assertEqual(data[0]["count"], 2)

    def test_confirmed_subscribers(self) -> None:
        self.db.create_or_update_subscriber("c1@example.com", "t1")
        self.db.create_or_update_subscriber("c2@example.com", "t2")
        self.db.confirm_by_token("t1")

        confirmed = self.db.list_confirmed_subscribers()
        self.assertEqual(len(confirmed), 1)
        self.assertEqual(confirmed[0]["email"], "c1@example.com")


if __name__ == "__main__":
    unittest.main()
