from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import CurrentUser, require_role
from backend.db.models import AcceptedClip, Campaign, Creator, SubmittedClip
from backend.db.session import get_db
from backend.schemas.common import SubmitClipRequest, UpdateCreatorProfileRequest, VerifyInstagramRequest
from backend.services.campaigns import fetch_campaigns_by_ids, serialize_accepted_clip, serialize_campaign, serialize_submitted_clip
from routes.instagramVerifier import verify_instagram_username


router = APIRouter()


@router.post("/verify-instagram")
@router.post("/verify-instagram/")
async def verify_instagram(
    payload: VerifyInstagramRequest,
    current_user: CurrentUser = Depends(require_role("creator")),
    db: AsyncSession = Depends(get_db),
):
    result = verify_instagram_username(payload.username)
    if result.get("exists"):
        await db.execute(
            update(Creator)
            .where(Creator.id == current_user.id)
            .values(instagram_username=payload.username, instagram_verified=True)
        )
        await db.commit()
        result["msg"] = "Instagram account verified and linked."
        return result

    status = result.get("status")
    if status == "not_found":
        message = f"Instagram user '{payload.username}' not found."
    elif status == "blocked":
        message = "Could not verify at this time. Please try again later."
    else:
        message = "An unknown error occurred during verification."
    raise HTTPException(status_code=400, detail=message)


@router.get("/api/creator/your-campaigns")
async def get_creator_campaigns(
    current_user: CurrentUser = Depends(require_role("creator")),
    db: AsyncSession = Depends(get_db),
):
    submitted_result = await db.execute(select(SubmittedClip.campaign_id).where(SubmittedClip.creator_id == current_user.id))
    accepted_result = await db.execute(select(AcceptedClip.campaign_id).where(AcceptedClip.creator_id == current_user.id))
    campaign_ids = sorted({row[0] for row in submitted_result.all()} | {row[0] for row in accepted_result.all()})

    campaigns = await fetch_campaigns_by_ids(db, campaign_ids, only_active=True)
    if not campaigns:
        return []

    submitted_rows = await db.execute(
        select(SubmittedClip).where(
            SubmittedClip.creator_id == current_user.id,
            SubmittedClip.campaign_id.in_([campaign.id for campaign in campaigns]),
        )
    )
    accepted_rows = await db.execute(
        select(AcceptedClip).where(
            AcceptedClip.creator_id == current_user.id,
            AcceptedClip.campaign_id.in_([campaign.id for campaign in campaigns]),
        )
    )

    submitted_by_campaign: dict[int, list[dict]] = defaultdict(list)
    accepted_by_campaign: dict[int, list[dict]] = defaultdict(list)

    for clip in submitted_rows.scalars().all():
        submitted_by_campaign[clip.campaign_id].append(serialize_submitted_clip(clip))
    for clip in accepted_rows.scalars().all():
        accepted_by_campaign[clip.campaign_id].append(serialize_accepted_clip(clip))

    response = []
    for campaign in campaigns:
        payload = serialize_campaign(campaign)
        payload["submitted_clips"] = submitted_by_campaign.get(campaign.id, [])
        payload["accepted_clips"] = accepted_by_campaign.get(campaign.id, [])
        if payload["submitted_clips"] or payload["accepted_clips"]:
            response.append(payload)
    return response


@router.post("/api/creator/submit-clip", status_code=201)
async def submit_clip(
    payload: SubmitClipRequest,
    current_user: CurrentUser = Depends(require_role("creator")),
    db: AsyncSession = Depends(get_db),
):
    campaign = await db.get(Campaign, payload.campaign_id)
    if not campaign or not campaign.is_active:
        raise HTTPException(status_code=404, detail="Campaign not found or not active")

    count_result = await db.execute(
        select(func.count())
        .select_from(SubmittedClip)
        .where(SubmittedClip.creator_id == current_user.id, SubmittedClip.campaign_id == payload.campaign_id)
    )
    if (count_result.scalar_one() or 0) >= 5:
        raise HTTPException(status_code=400, detail="You have reached the maximum limit of 5 submissions for this campaign.")

    clip = SubmittedClip(
        creator_id=current_user.id,
        campaign_id=payload.campaign_id,
        clip_url=str(payload.clip_url),
        is_deleted_by_admin=False,
        feedback=None,
    )
    db.add(clip)
    await db.commit()
    await db.refresh(clip)
    return {"msg": "Clip submitted successfully", "clip_id": clip.id}


@router.get("/api/creator/campaign-clips")
async def get_creator_clips_for_campaign(
    campaign_id: int = Query(...),
    current_user: CurrentUser = Depends(require_role("creator")),
    db: AsyncSession = Depends(get_db),
):
    submitted_result = await db.execute(
        select(SubmittedClip).where(SubmittedClip.creator_id == current_user.id, SubmittedClip.campaign_id == campaign_id)
    )
    accepted_result = await db.execute(
        select(AcceptedClip).where(AcceptedClip.creator_id == current_user.id, AcceptedClip.campaign_id == campaign_id)
    )

    response = []
    for clip in submitted_result.scalars().all():
        status = "rejected" if clip.is_deleted_by_admin else "in_review"
        response.append(serialize_submitted_clip(clip, status=status) | {"media_id": None, "caption": None, "instagram_posted_at": None})
    for clip in accepted_result.scalars().all():
        response.append(
            {
                **serialize_accepted_clip(clip),
                "is_deleted_by_admin": False,
                "feedback": None,
            }
        )
    return response


@router.delete("/api/creator/clip/{clip_id}")
async def delete_clip(
    clip_id: int,
    current_user: CurrentUser = Depends(require_role("creator")),
    db: AsyncSession = Depends(get_db),
):
    submitted_clip = await db.get(SubmittedClip, clip_id)
    if submitted_clip and submitted_clip.creator_id == current_user.id:
        await db.delete(submitted_clip)
        await db.commit()
        return {"msg": "Clip deleted successfully"}

    accepted_clip = await db.get(AcceptedClip, clip_id)
    if accepted_clip and accepted_clip.creator_id == current_user.id:
        view_count = accepted_clip.view_count or 0
        await db.execute(
            update(Campaign)
            .where(Campaign.id == accepted_clip.campaign_id)
            .values(total_view_count=func.greatest(Campaign.total_view_count - view_count, 0))
        )
        await db.delete(accepted_clip)
        await db.commit()
        return {"msg": "Clip deleted successfully"}

    raise HTTPException(status_code=404, detail="Clip not found")


@router.get("/api/creator/profile")
async def get_creator_profile(
    current_user: CurrentUser = Depends(require_role("creator")),
    db: AsyncSession = Depends(get_db),
):
    creator = await db.get(Creator, current_user.id)
    if not creator:
        raise HTTPException(status_code=404, detail="Creator not found")

    return {
        "id": creator.id,
        "username": creator.username,
        "email": creator.email,
        "nickname": creator.nickname,
        "bio": creator.bio,
        "phone": creator.phone,
        "join_date": creator.join_date.isoformat() if creator.join_date else None,
        "profile_completed": bool(creator.profile_completed),
        "instagram_username": creator.instagram_username,
        "instagram_verified": bool(creator.instagram_verified),
    }


@router.put("/api/creator/profile")
async def update_creator_profile(
    payload: UpdateCreatorProfileRequest,
    current_user: CurrentUser = Depends(require_role("creator")),
    db: AsyncSession = Depends(get_db),
):
    creator = await db.get(Creator, current_user.id)
    if not creator:
        raise HTTPException(status_code=404, detail="Creator not found")

    changed = False
    for field in ("nickname", "bio", "phone", "instagram_username", "instagram_verified"):
        value = getattr(payload, field)
        if value is not None:
            setattr(creator, field, value)
            changed = True

    if not changed:
        raise HTTPException(status_code=400, detail="No fields to update")

    if any(getattr(payload, field) is not None for field in ("nickname", "bio", "phone")):
        creator.profile_completed = True

    await db.commit()
    return {"msg": "Creator profile updated successfully"}
