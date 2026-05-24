"""Reusable email composition and delivery services."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

import httpx

from backend.core.config import Settings, get_settings


logger = logging.getLogger(__name__)
RESEND_API_URL = "https://api.resend.com/emails"


@dataclass(frozen=True)
class EmailMessage:
    """Structured transactional email payload."""

    to: list[str]
    subject: str
    html: str
    text: str
    tags: list[dict[str, str]] | None = None


class EmailService:
    """Send transactional emails through Resend."""

    def __init__(
        self,
        api_key: str | None,
        from_email: str | None,
        reply_to: str | None = None,
        api_url: str = RESEND_API_URL,
    ) -> None:
        self.api_key = api_key
        self.from_email = from_email
        self.reply_to = reply_to
        self.api_url = api_url

    def is_configured(self) -> bool:
        """Return whether the email service has the minimum required configuration."""

        return bool(self.api_key and self.from_email)

    def send(self, message: EmailMessage, *, idempotency_key: str | None = None) -> dict[str, Any]:
        """Deliver an email message through Resend."""

        if not self.is_configured():
            raise RuntimeError("Resend email service is not configured.")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "mipoe-backend/1.0",
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        payload: dict[str, Any] = {
            "from": self.from_email,
            "to": message.to,
            "subject": message.subject,
            "html": message.html,
            "text": message.text,
        }
        if self.reply_to:
            payload["reply_to"] = self.reply_to
        if message.tags:
            payload["tags"] = message.tags

        with httpx.Client(timeout=15.0) as client:
            response = client.post(self.api_url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

        logger.info("Transactional email sent successfully to %s", message.to)
        return data


def get_email_service(settings: Settings | None = None) -> EmailService:
    """Build the configured email service instance."""

    resolved_settings = settings or get_settings()
    return EmailService(
        api_key=resolved_settings.resend_api_key,
        from_email=resolved_settings.resend_from_email,
        reply_to=resolved_settings.resend_reply_to,
    )


def build_password_reset_email(
    *,
    recipient_email: str,
    recipient_name: str,
    reset_url: str,
    expiry_minutes: int,
) -> EmailMessage:
    """Create the password reset email content."""

    safe_name = recipient_name.strip() or "there"
    subject = "Reset your Mipoe password"
    text = (
        f"Hi {safe_name},\n\n"
        "We received a request to reset your Mipoe password.\n"
        f"Use the secure link below within {expiry_minutes} minutes:\n\n"
        f"{reset_url}\n\n"
        "If you did not request this, you can safely ignore this email.\n"
    )
    html = f"""
    <div style="font-family:Arial,sans-serif;line-height:1.6;color:#111827;max-width:560px;margin:0 auto;padding:24px;">
      <h2 style="margin:0 0 16px;color:#111827;">Reset your Mipoe password</h2>
      <p style="margin:0 0 16px;">Hi {safe_name},</p>
      <p style="margin:0 0 16px;">We received a request to reset your Mipoe password.</p>
      <p style="margin:0 0 24px;">Use the secure button below within <strong>{expiry_minutes} minutes</strong>.</p>
      <p style="margin:0 0 24px;">
        <a href="{reset_url}" style="display:inline-block;background:#FF5C00;color:#ffffff;text-decoration:none;padding:12px 20px;border-radius:8px;font-weight:700;">
          Reset Password
        </a>
      </p>
      <p style="margin:0 0 16px;">If the button does not work, copy and paste this link into your browser:</p>
      <p style="margin:0 0 24px;word-break:break-all;color:#FF5C00;">{reset_url}</p>
      <p style="margin:0;color:#6B7280;">If you did not request this, you can safely ignore this email.</p>
    </div>
    """.strip()

    return EmailMessage(
        to=[recipient_email],
        subject=subject,
        html=html,
        text=text,
        tags=[{"name": "workflow", "value": "password_reset"}],
    )
