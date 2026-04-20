"""Tests for auth module."""

import os
import unittest

os.environ["ADMIN_USER"] = "testadmin"
os.environ["ADMIN_PASSWORD"] = "testpass"


class AuthTests(unittest.TestCase):
    def test_verify_correct_credentials(self) -> None:
        from app.auth import verify_admin_credentials
        self.assertTrue(verify_admin_credentials("testadmin", "testpass"))

    def test_verify_wrong_credentials(self) -> None:
        from app.auth import verify_admin_credentials
        self.assertFalse(verify_admin_credentials("wrong", "wrong"))
        self.assertFalse(verify_admin_credentials("testadmin", "wrong"))
        self.assertFalse(verify_admin_credentials("wrong", "testpass"))

    def test_login_rate_limit(self) -> None:
        from app.auth import _login_attempts, check_login_rate_limit
        _login_attempts.clear()

        # Should not raise for first 5 attempts
        for _ in range(5):
            check_login_rate_limit("10.0.0.1")

        # 6th should raise
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as ctx:
            check_login_rate_limit("10.0.0.1")
        self.assertEqual(ctx.exception.status_code, 429)

    def test_login_rate_limit_different_ips(self) -> None:
        from app.auth import _login_attempts, check_login_rate_limit
        _login_attempts.clear()

        for _ in range(5):
            check_login_rate_limit("10.0.0.2")

        # Different IP should still work
        check_login_rate_limit("10.0.0.3")

    def test_api_key_auth(self) -> None:
        os.environ["API_KEY"] = "test-api-key-123"
        import importlib

        import app.auth
        importlib.reload(app.auth)

        from unittest.mock import MagicMock
        request = MagicMock()
        request.headers = {"X-API-Key": "test-api-key-123"}
        self.assertTrue(app.auth.verify_api_key(request))

        request.headers = {"X-API-Key": "wrong-key"}
        self.assertFalse(app.auth.verify_api_key(request))

        os.environ["API_KEY"] = ""
        importlib.reload(app.auth)

    def test_api_key_empty(self) -> None:
        os.environ["API_KEY"] = ""
        import importlib

        import app.auth
        importlib.reload(app.auth)

        from unittest.mock import MagicMock
        request = MagicMock()
        request.headers = {"X-API-Key": "anything"}
        self.assertFalse(app.auth.verify_api_key(request))


if __name__ == "__main__":
    unittest.main()
