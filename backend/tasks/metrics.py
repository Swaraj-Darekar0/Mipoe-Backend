import asyncio
import os
import re
import time
from datetime import datetime

from instagrapi import Client
from pydantic import ValidationError
from sqlalchemy import select

from backend.core.config import get_settings
from backend.db.models import AcceptedClip, Campaign
from backend.db.session import AsyncSessionLocal
from backend.tasks.celery_app import celery_app


settings = get_settings()


def extract_media_id_from_url(url: str) -> str | None:
    match = re.search(r"/(?:p|reel)/([a-zA-Z0-9_-]+)", url)
    return match.group(1) if match else None


@celery_app.task(name="metrics.fetch_and_update_metrics")
def fetch_and_update_metrics():
    if not settings.instagram_username or not settings.instagram_password:
        return

    client = Client()
    if os.path.exists("instagrapi.json"):
        client.load_settings("instagrapi.json")
    client.login(settings.instagram_username, settings.instagram_password)
    client.dump_settings("instagrapi.json")

    async def _get_clips():
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(AcceptedClip).where(AcceptedClip.clip_url.is_not(None)))
            return list(result.scalars().all())

    clips = asyncio.run(_get_clips())
    processed_campaigns: set[int] = set()

    for clip in clips:
        media_code = extract_media_id_from_url(clip.clip_url)
        if not media_code:
            continue
        try:
            media_pk = client.media_pk_from_code(media_code)
            try:
                media = client.media_info(media_pk)
                view_count = media.play_count or 0
                like_count = media.like_count or 0
                comment_count = media.comment_count or 0
                caption = media.caption_text or ""
                posted_at = media.taken_at.isoformat() if media.taken_at else None
            except ValidationError:
                raw = client.private_request(f"media/{media_pk}/info/")
                item = raw["items"][0]
                view_count = item.get("play_count") or item.get("view_count", 0)
                like_count = item.get("like_count", 0) or 0
                comment_count = item.get("comment_count", 0) or 0
                caption = (item.get("caption") or {}).get("text", "")
                timestamp = item.get("taken_at")
                posted_at = datetime.fromtimestamp(timestamp).isoformat() if timestamp else None

            async def _update_clip():
                async with AsyncSessionLocal() as db:
                    db_clip = await db.get(AcceptedClip, clip.id)
                    if not db_clip:
                        return
                    db_clip.view_count = view_count
                    db_clip.like_count = like_count
                    db_clip.comment_count = comment_count
                    db_clip.media_id = media_code
                    db_clip.caption = caption
                    if posted_at:
                        db_clip.instagram_posted_at = datetime.fromisoformat(posted_at)
                    await db.commit()

            asyncio.run(_update_clip())
            processed_campaigns.add(clip.campaign_id)
            time.sleep(2)
        except Exception:
            continue

    async def _update_campaigns():
        async with AsyncSessionLocal() as db:
            for campaign_id in processed_campaigns:
                result = await db.execute(select(AcceptedClip).where(AcceptedClip.campaign_id == campaign_id))
                campaign = await db.get(Campaign, campaign_id)
                if campaign:
                    campaign.total_view_count = sum(int(item.view_count or 0) for item in result.scalars().all())
            await db.commit()

    asyncio.run(_update_campaigns())
