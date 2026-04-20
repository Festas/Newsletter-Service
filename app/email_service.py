import os
from email.message import EmailMessage
from pathlib import Path

import aiosmtplib
from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)


class EmailService:
    def __init__(self) -> None:
        self.smtp_host = os.getenv("SMTP_HOST", "")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_user = os.getenv("SMTP_USER", "")
        self.smtp_password = os.getenv("SMTP_PASSWORD", "")
        self.smtp_from = os.getenv("SMTP_FROM", "newsletter@example.com")

    async def _send_message(self, message: EmailMessage) -> None:
        if not self.smtp_host:
            raise RuntimeError("SMTP_HOST is not configured")

        await aiosmtplib.send(
            message,
            hostname=self.smtp_host,
            port=self.smtp_port,
            username=self.smtp_user or None,
            password=self.smtp_password or None,
            start_tls=self.smtp_port in {587, 25},
            use_tls=self.smtp_port == 465,
        )

    async def send_confirmation_email(self, recipient: str, confirm_url: str) -> None:
        html_template = jinja_env.get_template("emails/confirmation.html")
        html_body = html_template.render(confirm_url=confirm_url)
        text_body = f"Please confirm your newsletter subscription: {confirm_url}"

        message = EmailMessage()
        message["Subject"] = "Confirm your newsletter subscription"
        message["From"] = self.smtp_from
        message["To"] = recipient
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
    ) -> None:
        final_text = (text_body or "") + f"\n\nUnsubscribe: {unsubscribe_url}"
        final_html = (
            (html_body or "<p></p>")
            + f"<hr><p><a href=\"{unsubscribe_url}\">Unsubscribe</a></p>"
        )

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.smtp_from
        message["To"] = recipient
        message.set_content(final_text)
        message.add_alternative(final_html, subtype="html")

        await self._send_message(message)
