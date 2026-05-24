from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import CurrentUser, require_role
from backend.db.models import AcceptedClip, Campaign, Creator, SubmittedClip
from backend.db.session import get_db
from backend.schemas.common import UpdateClipStatusRequest, UpdateViewCountRequest
from backend.services.campaigns import serialize_accepted_clip, serialize_campaign, serialize_submitted_clip
from backend.services.notifications import append_creator_notification


router = APIRouter()


@router.get("/api/admin/campaigns")
async def get_admin_campaigns(
    current_user: CurrentUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    campaigns_result = await db.execute(select(Campaign))
    campaigns = list(campaigns_result.scalars().all())
    campaign_ids = [campaign.id for campaign in campaigns]

    submitted_result = await db.execute(select(SubmittedClip).where(SubmittedClip.campaign_id.in_(campaign_ids))) if campaign_ids else None
    accepted_result = await db.execute(select(AcceptedClip).where(AcceptedClip.campaign_id.in_(campaign_ids))) if campaign_ids else None

    submitted_by_campaign: dict[int, list[dict]] = defaultdict(list)
    accepted_by_campaign: dict[int, list[dict]] = defaultdict(list)

    if submitted_result:
        for clip in submitted_result.scalars().all():
            submitted_by_campaign[clip.campaign_id].append(serialize_submitted_clip(clip))
    if accepted_result:
        accepted_clips = accepted_result.scalars().all()
        creator_ids = list({clip.creator_id for clip in accepted_clips})
        creator_result = await db.execute(select(Creator.id, Creator.username).where(Creator.id.in_(creator_ids))) if creator_ids else None
        creator_names = {row.id: row.username for row in creator_result.all()} if creator_result else {}
        for clip in accepted_clips:
            accepted_by_campaign[clip.campaign_id].append(serialize_accepted_clip(clip, creator_names.get(clip.creator_id)))

    response = []
    for campaign in campaigns:
        payload = serialize_campaign(campaign)
        payload["submitted_clips"] = submitted_by_campaign.get(campaign.id, [])
        payload["accepted_clips"] = accepted_by_campaign.get(campaign.id, [])
        response.append(payload)
    return response


@router.put("/api/admin/clip/{clip_id}")
async def admin_update_clip(
    clip_id: int,
    payload: UpdateClipStatusRequest,
    current_user: CurrentUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    clip = await db.get(SubmittedClip, clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")

    creator = await db.get(Creator, clip.creator_id)

    if payload.status == "accepted":
        accepted = AcceptedClip(
            creator_id=clip.creator_id,
            campaign_id=clip.campaign_id,
            clip_url=clip.clip_url,
            submitted_at=clip.submitted_at,
            media_id=None,
            view_count=None,
            caption=None,
            instagram_posted_at=None,
            last_view_count=0,
            amount_paid=0.0,
        )
        db.add(accepted)
        await db.flush()
        await db.delete(clip)
        if creator:
            await append_creator_notification(
                db,
                creator.id,
                message=f"Your clip was approved for campaign {accepted.campaign_id}.",
                notification_type="clip_approved",
                campaign_id=accepted.campaign_id,
                clip_id=accepted.id,
            )
        await db.commit()
        return {"msg": "Clip updated successfully"}

    clip.is_deleted_by_admin = True
    clip.feedback = payload.feedback
    if creator:
        await append_creator_notification(
            db,
            creator.id,
            message=f"Your clip was rejected for campaign {clip.campaign_id}.",
            notification_type="clip_rejected",
            campaign_id=clip.campaign_id,
            clip_id=clip.id,
        )
    await db.commit()
    return {"msg": "Clip updated successfully"}


@router.delete("/api/admin/clip/{clip_id}")
async def admin_delete_clip(
    clip_id: int,
    current_user: CurrentUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    submitted = await db.get(SubmittedClip, clip_id)
    if submitted:
        await db.delete(submitted)
        await db.commit()
        return {"msg": "Clip deleted successfully"}

    accepted = await db.get(AcceptedClip, clip_id)
    if accepted:
        await db.delete(accepted)
        await db.commit()
        return {"msg": "Clip deleted successfully"}

    raise HTTPException(status_code=404, detail="Clip not found")


@router.put("/api/admin/clip/{clip_id}/view-count")
async def update_clip_view_count(
    clip_id: int,
    payload: UpdateViewCountRequest,
    current_user: CurrentUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    clip = await db.get(AcceptedClip, clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")

    old_view_count = clip.view_count or 0
    new_view_count = payload.view_count
    diff = new_view_count - old_view_count
    clip.last_view_count = old_view_count
    clip.view_count = new_view_count

    campaign = await db.get(Campaign, clip.campaign_id)
    if campaign:
        campaign.total_view_count = max((campaign.total_view_count or 0) + diff, 0)

    await db.commit()
    return {
        "msg": "View count updated successfully",
        "clip_id": clip_id,
        "campaign_id": clip.campaign_id,
        "old_view_count": old_view_count,
        "new_view_count": new_view_count,
        "view_count_diff": diff,
    }


@router.put("/api/admin/campaign/{campaign_id}/update-views")
async def update_campaign_view_count(
    campaign_id: int,
    payload: dict,
    current_user: CurrentUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    campaign = await db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    accepted_result = await db.execute(select(AcceptedClip).where(AcceptedClip.campaign_id == campaign_id))
    clips = accepted_result.scalars().all()
    new_total_views = payload.get("total_view_count")
    if new_total_views is None:
        new_total_views = sum(int(clip.view_count or 0) for clip in clips)

    old_total = campaign.total_view_count or 0
    campaign.total_view_count = int(new_total_views)
    await db.commit()
    return {
        "msg": "Campaign view count updated successfully",
        "campaign_id": campaign_id,
        "old_total_views": old_total,
        "new_total_views": campaign.total_view_count,
        "view_diff": campaign.total_view_count - old_total,
        "clip_count": len(clips),
    }


@router.get("/api/admin/analytics/campaign-performance/{campaign_id}")
async def get_campaign_performance_analytics(
    campaign_id: int,
    current_user: CurrentUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    campaign = await db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    accepted_result = await db.execute(select(AcceptedClip).where(AcceptedClip.campaign_id == campaign_id))
    clips = list(accepted_result.scalars().all())
    creator_ids = list({clip.creator_id for clip in clips})
    creator_result = await db.execute(select(Creator.id, Creator.username).where(Creator.id.in_(creator_ids))) if creator_ids else None
    creator_names = {row.id: row.username for row in creator_result.all()} if creator_result else {}

    creator_performance: dict[int, dict] = {}
    for clip in clips:
        creator_id = clip.creator_id
        if creator_id not in creator_performance:
            creator_performance[creator_id] = {
                "creator_id": creator_id,
                "creator_name": creator_names.get(creator_id, f"Creator {creator_id}"),
                "total_views": 0,
                "clips": 0,
                "total_earned": 0,
                "total_paid": 0,
                "pending": 0,
            }
        creator_performance[creator_id]["total_views"] += int(clip.view_count or 0)
        creator_performance[creator_id]["clips"] += 1
        creator_performance[creator_id]["total_paid"] += float(clip.amount_paid or 0.0)

    for creator_id, perf in creator_performance.items():
        earned = (perf["total_views"] / campaign.view_threshold) * campaign.cpv if campaign.view_threshold else 0
        perf["total_earned"] = earned
        perf["pending"] = earned - perf["total_paid"]

    sorted_creators = sorted(creator_performance.values(), key=lambda item: item["total_views"], reverse=True)
    total_earned = sum(item["total_earned"] for item in sorted_creators)
    total_pending = sum(item["pending"] for item in sorted_creators)
    utilization = (total_earned / campaign.funds_allocated * 100) if campaign.funds_allocated else 0

    return {
        "msg": "Campaign performance analytics retrieved successfully",
        "campaign_id": campaign_id,
        "overview": {
            "total_clips": len(clips),
            "total_creators": len(sorted_creators),
            "total_views": campaign.total_view_count or 0,
            "milestones_reached": (campaign.total_view_count or 0) // campaign.view_threshold if campaign.view_threshold else 0,
            "cpv": campaign.cpv,
            "view_threshold": campaign.view_threshold,
        },
        "financial": {
            "funds_allocated": campaign.funds_allocated or 0,
            "funds_distributed": campaign.funds_distributed or 0,
            "total_earned": total_earned,
            "total_pending": total_pending,
            "utilization_percentage": utilization,
            "remaining_budget": (campaign.funds_allocated or 0) - (campaign.funds_distributed or 0),
        },
        "creator_performance": sorted_creators,
    }
