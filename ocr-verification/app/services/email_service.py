from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from typing import Optional

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


class EmailService:
    """
    Free email sender using Gmail SMTP only (no paid email APIs).

    Setup:
    1. Use a free Gmail account
    2. Enable 2-Step Verification
    3. Create an App Password: Google Account → Security → App passwords
    4. Set SMTP_USERNAME / SMTP_PASSWORD / SMTP_ENABLED=true in .env
    """

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()

    def send_batch_summary(
        self,
        *,
        subject: str,
        body: str,
        to_address: Optional[str] = None,
    ) -> tuple[bool, Optional[str]]:
        recipient = to_address or self.settings.mail_to
        sender = self.settings.mail_from or self.settings.smtp_username or "noreply@gmail.com"

        draft_path = self.settings.temp_path / "last_batch_email.txt"
        draft_path.write_text(
            f"To: {recipient}\nFrom: {sender}\nSubject: {subject}\n\n{body}",
            encoding="utf-8",
        )

        if not self.settings.smtp_enabled:
            reason = (
                "Email skipped for now (SMTP_ENABLED=false). "
                f"Summary draft saved to {draft_path}. Credentials can be added later."
            )
            logger.info(reason)
            return False, reason

        if not self.settings.smtp_username or not self.settings.smtp_password:
            reason = (
                "Email skipped (Gmail username/app-password not set yet). "
                f"Summary draft saved to {draft_path}."
            )
            logger.info(reason)
            return False, reason

        host = self.settings.smtp_host or "smtp.gmail.com"
        port = self.settings.smtp_port or 587

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = sender
        message["To"] = recipient
        message.set_content(body)

        try:
            with smtplib.SMTP(host, port, timeout=30) as smtp:
                smtp.ehlo()
                if self.settings.smtp_use_tls:
                    smtp.starttls()
                    smtp.ehlo()
                smtp.login(self.settings.smtp_username, self.settings.smtp_password)
                smtp.send_message(message)
            logger.info("Batch summary email sent via free Gmail SMTP to %s", recipient)
            return True, None
        except Exception as exc:  # noqa: BLE001
            reason = f"Failed to send Gmail SMTP email: {exc}. Draft saved to {draft_path}"
            logger.exception(reason)
            return False, reason
