from datetime import datetime, timezone

from sqlalchemy import delete, select, update

from backend.db.models import Campaign, SubmittedClip
from backend.db.session import AsyncSessionLocal
from backend.tasks.celery_app import celery_app


@celery_app.task(name="maintenance.deactivate_expired_campaigns")
def deactivate_expired_campaigns():
    import asyncio

    async def _run():
        async with AsyncSessionLocal() as db:
            today = datetime.now(timezone.utc).date()
            await db.execute(
                update(Campaign)
                .where(Campaign.deadline < today, Campaign.is_active.is_(True))
                .values(is_active=False)
            )
            await db.commit()

    asyncio.run(_run())


@celery_app.task(name="maintenance.delete_rejected_clips")
def delete_rejected_clips():
    import asyncio

    async def _run():
        async with AsyncSessionLocal() as db:
            await db.execute(delete(SubmittedClip).where(SubmittedClip.is_deleted_by_admin.is_(True)))
            await db.commit()

    asyncio.run(_run())
