"""Optional email. Without SMTP settings nothing is sent and nothing is logged: the admin gets the link in the API response.
Links hold secrets, so they are never written to a log."""
from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from .config import Settings


class Mailer:
    def __init__(self, s: Settings):
        self.s = s

    @property
    def enabled(self) -> bool:
        return bool(self.s.smtp_host)

    def send(self, to: str, subject: str, body: str) -> bool:
        """True when the message was handed to the SMTP server; False when mail is off or the server refused it."""
        if not self.enabled:
            return False
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.s.smtp_from, to, subject
        msg.set_content(body)
        try:
            with smtplib.SMTP(self.s.smtp_host, self.s.smtp_port, timeout=15) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                if self.s.smtp_user and self.s.smtp_password:
                    smtp.login(self.s.smtp_user, self.s.smtp_password.get_secret_value())
                smtp.send_message(msg)
            return True
        except (OSError, smtplib.SMTPException):
            return False
