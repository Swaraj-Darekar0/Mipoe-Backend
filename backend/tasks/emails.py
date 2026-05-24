"""Celery tasks for transactional email delivery."""

from __future__ import annotations

import logging

from backend.core.config import get_settings
from backend.services.email import build_password_reset_email, get_email_service
from backend.tasks.celery_app import celery_app


logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="emails.send_password_reset_email",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def send_password_reset_email(
    self,
    recipient_email: str,
    recipient_name: str,
    reset_url: str,
    reset_token: str,
) -> dict[str, str]:
    """Send a password reset email through Resend."""

    settings = get_settings()
    expiry_minutes = max(int(settings.password_reset_token_ttl_seconds / 60), 1)
    email_service = get_email_service(settings)
    email_message = build_password_reset_email(
        recipient_email=recipient_email,
        recipient_name=recipient_name,
        reset_url=reset_url,
        expiry_minutes=expiry_minutes,
    )
    response = email_service.send(email_message, idempotency_key=f"password-reset:{reset_token}")
    logger.info("Password reset email task completed for %s", recipient_email)
    return {"email_id": str(response.get("id", ""))}
