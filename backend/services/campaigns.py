from collections import defaultdict

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import AcceptedClip, Campaign, Creator, SubmittedClip


def serialize_campaign(campaign: Campaign) -> dict:
    return {
        "id": campaign.id,
        "name": campaign.name,
        "platform": campaign.platform,
        "budget": campaign.budget,
        "cpv": campaign.cpv,
        "hashtag": campaign.hashtag,
        "audio": campaign.audio,
        "deadline": campaign.deadline.isoformat() if campaign.deadline else None,
        "brand_id": campaign.brand_id,
        "is_active": campaign.is_active,
        "category": campaign.category,
        "asset_link": campaign.asset_link,
        "total_view_count": campaign.total_view_count,
        "requirements": campaign.requirements,
        "image_url": campaign.image_url,
        "view_threshold": campaign.view_threshold,
        "funds_allocated": campaign.funds_allocated,
        "funds_distributed": campaign.funds_distributed,
    }


def serialize_submitted_clip(clip: SubmittedClip, status: str = "pending") -> dict:
    return {
        "id": clip.id,
        "campaign_id": clip.campaign_id,
        "creator_id": clip.creator_id,
        "clip_url": clip.clip_url,
        "submitted_at": clip.submitted_at.isoformat() if clip.submitted_at else None,
        "status": status,
        "is_deleted_by_admin": bool(clip.is_deleted_by_admin),
        "feedback": clip.feedback,
        "view_count": None,
    }


def serialize_accepted_clip(clip: AcceptedClip, creator_name: str | None = None) -> dict:
    payload = {
        "id": clip.id,
        "campaign_id": clip.campaign_id,
        "creator_id": clip.creator_id,
        "clip_url": clip.clip_url,
        "submitted_at": clip.submitted_at.isoformat() if clip.submitted_at else None,
        "media_id": clip.media_id,
        "view_count": clip.view_count,
        "caption": clip.caption,
        "instagram_posted_at": clip.instagram_posted_at.isoformat() if clip.instagram_posted_at else None,
        "status": "accepted",
    }
    if creator_name is not None:
        payload["creator_name"] = creator_name
    return payload


def build_ranked_clips(clips: list[dict]) -> tuple[list[dict], list[dict]]:
    view_count_map: dict[int, list[dict]] = defaultdict(list)
    ranked_candidates: list[tuple[int, dict]] = []
    unranked: list[dict] = []

    for clip in clips:
        view_count = clip.get("view_count")
        if view_count in (None, 0):
            unranked.append(clip)
            continue
        view_count_map[view_count].append(clip)

    for view_count, grouped in view_count_map.items():
        if len(grouped) == 1:
            ranked_candidates.append((view_count, grouped[0]))
        else:
            unranked.extend(grouped)

    ranked = [clip for _, clip in sorted(ranked_candidates, key=lambda item: item[0], reverse=True)]
    creator_rankings: dict[int, dict] = {}
    for clip in ranked:
        creator_id = clip["creator_id"]
        if creator_id not in creator_rankings:
            creator_rankings[creator_id] = {
                "creator_id": creator_id,
                "creator_name": clip.get("creator_name", "Unknown Creator"),
                "total_views": 0,
                "clip_count": 0,
            }
        creator_rankings[creator_id]["total_views"] += clip.get("view_count") or 0
        creator_rankings[creator_id]["clip_count"] += 1

    return ranked + unranked, sorted(creator_rankings.values(), key=lambda item: item["total_views"], reverse=True)


async def fetch_creator_names(db: AsyncSession, creator_ids: list[int]) -> dict[int, str]:
    if not creator_ids:
        return {}
    result = await db.execute(select(Creator.id, Creator.username).where(Creator.id.in_(creator_ids)))
    return {row.id: row.username for row in result.all()}


async def fetch_campaigns_by_ids(db: AsyncSession, campaign_ids: list[int], only_active: bool = False) -> list[Campaign]:
    if not campaign_ids:
        return []
    query: Select[tuple[Campaign]] = select(Campaign).where(Campaign.id.in_(campaign_ids))
    if only_active:
        query = query.where(Campaign.is_active.is_(True))
    result = await db.execute(query)
    return list(result.scalars().all())
