from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import AcceptedClip, Campaign
from backend.db.session import get_db
from backend.services.campaigns import build_ranked_clips, fetch_creator_names, serialize_accepted_clip, serialize_campaign


router = APIRouter()


@router.get("/api/campaigns")
async def get_all_campaigns(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Campaign).where(Campaign.is_active.is_(True)))
    campaigns = result.scalars().all()
    return [serialize_campaign(campaign) for campaign in campaigns]


@router.get("/api/campaigns/{campaign_id}")
async def get_campaign_by_id(campaign_id: int, db: AsyncSession = Depends(get_db)):
    campaign = await db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    accepted_result = await db.execute(select(AcceptedClip).where(AcceptedClip.campaign_id == campaign_id))
    accepted_clips = list(accepted_result.scalars().all())
    creator_names = await fetch_creator_names(db, list({clip.creator_id for clip in accepted_clips}))

    clip_payloads = [
        serialize_accepted_clip(clip, creator_names.get(clip.creator_id, "Unknown Creator")) for clip in accepted_clips
    ]
    ranked_clips, creator_rankings = build_ranked_clips(clip_payloads)

    payload = serialize_campaign(campaign)
    payload["accepted_clips"] = ranked_clips
    payload["creator_rankings"] = creator_rankings
    return payload
