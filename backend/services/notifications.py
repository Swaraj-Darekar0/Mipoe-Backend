import json
import uuid
from datetime import datetime, UTC

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def append_creator_notification(
    db: AsyncSession,
    creator_id: int,
    message: str,
    notification_type: str,
    campaign_id: int | None = None,
    clip_id: int | None = None,
    amount: float | None = None,
    payout_method: str | None = None,
    clip_thumbnail: str | None = None,
    feedback: str | None = None,
) -> None:
    notification = {
        "id": str(uuid.uuid4()),
        "message": message,
        "type": notification_type,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    if campaign_id is not None:
        notification["campaign_id"] = campaign_id
    if clip_id is not None:
        notification["clip_id"] = clip_id
    if amount is not None:
        notification["amount"] = amount
    if payout_method is not None:
        notification["payout_method"] = payout_method
    if clip_thumbnail is not None:
        notification["clip_thumbnail"] = clip_thumbnail
    if feedback is not None:
        notification["feedback"] = feedback

    await db.execute(
        text(
            """
            UPDATE creator
            SET notifications = COALESCE(notifications, '{}'::jsonb[]) || ARRAY[CAST(:notification AS jsonb)]
            WHERE id = :creator_id
            """
        ),
        {"notification": json.dumps(notification), "creator_id": creator_id},
    )
