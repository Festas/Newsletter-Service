import asyncio
import logging
import os
import re
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path

import aiosmtplib
from jinja2 import Environment, FileSystemLoader, select_autoescape

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)

MAX_RETRIES = 3
RETRY_BASE_DELAY = 2  # seconds


class EmailService:
    def __init__(self) -> None:
        self.smtp_host = os.getenv("SMTP_HOST", "")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_user = os.getenv("SMTP_USER", "")
        self.smtp_password = os.getenv("SMTP_PASSWORD", "")
        self.smtp_from = os.getenv("SMTP_FROM", "newsletter@example.com")
        self.reply_to = os.getenv("REPLY_TO", self.smtp_from)
        self.brand_name = os.getenv("BRAND_NAME", "Newsletter")

    async def _send_message(self, message: EmailMessage) -> None:
        if not self.smtp_host:
            raise RuntimeError("SMTP_HOST is not configured")

        last_error: Exception = RuntimeError("SMTP send failed")
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                await aiosmtplib.send(
                    message,
                    hostname=self.smtp_host,
                    port=self.smtp_port,
                    username=self.smtp_user or None,
                    password=self.smtp_password or None,
                    start_tls=self.smtp_port in {587, 25},
                    use_tls=self.smtp_port == 465,
                )
                return
            except Exception as exc:
                last_error = exc
                if attempt < MAX_RETRIES:
                    delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    logger.warning(
                        "SMTP send attempt %d/%d failed: %s — retrying in %ds",
                        attempt, MAX_RETRIES, exc, delay,
                    )
                    await asyncio.sleep(delay)

        logger.error("SMTP send failed after %d attempts: %s", MAX_RETRIES, last_error)
        raise last_error

    async def send_confirmation_email(self, recipient: str, confirm_url: str) -> None:
        html_template = jinja_env.get_template("emails/confirmation.html")
        html_body = html_template.render(confirm_url=confirm_url, brand_name=self.brand_name)
        text_body = f"Please confirm your newsletter subscription: {confirm_url}"

        message = EmailMessage()
        message["Subject"] = "Confirm your newsletter subscription"
        message["From"] = self.smtp_from
        message["To"] = recipient
        message["Reply-To"] = self.reply_to
        message["Message-ID"] = make_msgid()
        message.set_content(text_body)
        message.add_alternative(html_body, subtype="html")

        await self._send_message(message)

    async def send_newsletter(
        self,
        recipient: str,
        subject: str,
        text_body: str | None,
        html_body: str | None,
        unsubscribe_url: str,
        tracking_pixel_url: str | None = None,
        newsletter_id: int | None = None,
    ) -> None:
        # Wrap HTML body in a chosen email template
        template_html = jinja_env.get_template("emails/newsletter_base.html")
        final_html = template_html.render(
            body_html=html_body or "",
            unsubscribe_url=unsubscribe_url,
            tracking_pixel_url=tracking_pixel_url,
            brand_name=self.brand_name,
        )

        final_text = (text_body or "") + f"\n\nUnsubscribe: {unsubscribe_url}"

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.smtp_from
        message["To"] = recipient
        message["Reply-To"] = self.reply_to
        message["Message-ID"] = make_msgid()
        message["List-Unsubscribe"] = f"<{unsubscribe_url}>"
        message["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
        message.set_content(final_text)
        message.add_alternative(final_html, subtype="html")

        await self._send_message(message)

    def inject_tracking_links(self, html: str, base_url: str, newsletter_id: int) -> str:
        """Replace <a href="..."> links in HTML with tracking redirect URLs."""
        def _replace(match: re.Match[str]) -> str:
            original_url = match.group(1)
            # Don't wrap unsubscribe links or tracking links
            if "/unsubscribe" in original_url or "/track/" in original_url:
                return match.group(0)
            from urllib.parse import quote
            tracked = f"{base_url}/track/click?newsletter_id={newsletter_id}&url={quote(original_url, safe='')}"
            return f'href="{tracked}"'
        return re.sub(r'href="([^"]+)"', _replace, html)
