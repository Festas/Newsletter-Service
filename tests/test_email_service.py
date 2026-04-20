"""Tests for email service (link injection, message construction)."""

import os
import unittest

os.environ["SMTP_HOST"] = ""
os.environ["BRAND_NAME"] = "TestBrand"


class EmailServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        from app.email_service import EmailService
        self.service = EmailService()

    def test_inject_tracking_links(self) -> None:
        html = '<p>Click <a href="https://example.com">here</a></p>'
        result = self.service.inject_tracking_links(html, "https://base.com", 42)
        self.assertIn("/track/click?newsletter_id=42", result)
        self.assertIn("https%3A%2F%2Fexample.com", result)

    def test_inject_tracking_skips_unsubscribe(self) -> None:
        html = '<a href="https://base.com/unsubscribe?token=abc">Unsub</a>'
        result = self.service.inject_tracking_links(html, "https://base.com", 42)
        # Should NOT wrap unsubscribe links
        self.assertNotIn("/track/click", result)

    def test_inject_tracking_skips_tracking_links(self) -> None:
        html = '<a href="https://base.com/track/click?url=test">Already tracked</a>'
        result = self.service.inject_tracking_links(html, "https://base.com", 42)
        # Should not double-wrap
        self.assertNotIn("track/click?newsletter_id=42", result)

    def test_inject_tracking_no_links(self) -> None:
        html = "<p>No links here</p>"
        result = self.service.inject_tracking_links(html, "https://base.com", 42)
        self.assertEqual(html, result)

    def test_brand_name(self) -> None:
        service = self.service
        self.assertEqual(service.brand_name, "TestBrand")


if __name__ == "__main__":
    unittest.main()
