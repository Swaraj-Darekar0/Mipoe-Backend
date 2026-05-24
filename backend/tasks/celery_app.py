from celery import Celery

from backend.core.config import get_settings


settings = get_settings()

celery_app = Celery(
    "mipoe",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "backend.tasks.maintenance",
        # "backend.tasks.metrics",
        "backend.tasks.payouts",
        "backend.tasks.emails",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
)
