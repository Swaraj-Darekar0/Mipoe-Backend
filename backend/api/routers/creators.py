from collections import defaultdict
import io
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
import httpx
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from yt_dlp import YoutubeDL

from backend.api.deps import CurrentUser, require_role
from backend.core.config import get_settings
from backend.db.models import AcceptedClip, Campaign, Creator, SubmittedClip
from backend.db.session import AsyncSessionLocal, get_db
from backend.schemas.common import SubmitClipRequest, UpdateCreatorProfileRequest, VerifyInstagramRequest
from backend.services.campaigns import fetch_campaigns_by_ids, serialize_accepted_clip, serialize_campaign, serialize_submitted_clip


router = APIRouter()
settings = get_settings()


def compress_image_bytes(image_bytes: bytes) -> bytes | None:
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(image_bytes))
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA")
        if img.mode == "RGBA":
            background = Image.new("RGBA", img.size, (255, 255, 255, 255))
            background.alpha_composite(img)
            img = background.convert("RGB")
        else:
            img = img.convert("RGB")
        img.thumbnail((640, 640))
        out = io.BytesIO()
        img.save(out, format="WEBP", quality=72, method=6)
        return out.getvalue()
    except Exception as e:
        print(f"[Thumbnail] WebP conversion/compression failed: {e}")
        return None


def _extract_thumbnail_url_with_ytdlp(clip_url: str) -> str | None:
    ydl_opts = {
        "quiet": True,
        "skip_download": True,
        "no_warnings": True,
        "extract_flat": False,
        "socket_timeout": 15,
    }
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(clip_url, download=False)

    if not info:
        return None

    thumbnails = info.get("thumbnails") or []
    if thumbnails:
        best = max(
            (thumb for thumb in thumbnails if thumb.get("url")),
            key=lambda thumb: (thumb.get("width") or 0, thumb.get("height") or 0),
            default=None,
        )
        if best:
            return best["url"]

    return info.get("thumbnail")


async def extract_og_image_url(clip_url: str) -> str | None:
    return await asyncio.to_thread(_extract_thumbnail_url_with_ytdlp, clip_url)


async def fetch_and_save_clip_thumbnail(clip_id: int, clip_url: str):
    if not settings.supabase_url or not settings.supabase_key:
        print("[Thumbnail] Supabase URL or Key not set, skipping thumbnail generation")
        return

    # Step 1: Ensure bucket exists
    try:
        bucket_url = f"{settings.supabase_url}/storage/v1/bucket"
        headers = {
            "Authorization": f"Bearer {settings.supabase_key}",
            "apikey": settings.supabase_key,
            "Content-Type": "application/json"
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            payload = {
                "id": "thumnail_folder",
                "name": "thumnail_folder",
                "public": True
            }
            res = await client.post(bucket_url, headers=headers, json=payload)
            print(f"[Thumbnail] Ensure bucket exists status: {res.status_code}")
    except Exception as e:
        print(f"[Thumbnail] Error ensuring bucket: {e}")

    # Step 2: Resolve thumbnail metadata with yt-dlp
    image_bytes = None
    content_type = "image/webp"
    try:
        print(f"[Thumbnail] Extracting thumbnail metadata with yt-dlp for URL: {clip_url}")
        thumbnail_url = await extract_og_image_url(clip_url)
        if thumbnail_url:
            print(f"[Thumbnail] Found thumbnail url: {thumbnail_url}")
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                img_res = await client.get(thumbnail_url)
                img_res.raise_for_status()
                image_bytes = img_res.content
        else:
            print("[Thumbnail] No thumbnail metadata found")
    except Exception as e:
        print(f"[Thumbnail] Thumbnail extraction failed: {e}")

    if not image_bytes:
        print("[Thumbnail] Failed to acquire any thumbnail image bytes")
        return

    compressed_bytes = compress_image_bytes(image_bytes)
    if not compressed_bytes:
        print("[Thumbnail] Failed to normalize thumbnail into WebP")
        return

    # Step 3: Upload to Supabase Storage
    uploaded_url = None
    try:
        file_name = f"clip_{clip_id}.webp"
        upload_url = f"{settings.supabase_url}/storage/v1/object/thumnail_folder/{file_name}"
        upload_headers = {
            "Authorization": f"Bearer {settings.supabase_key}",
            "apikey": settings.supabase_key,
            "Content-Type": content_type
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            res = await client.post(upload_url, headers=upload_headers, content=compressed_bytes)
            if res.status_code == 200:
                uploaded_url = f"{settings.supabase_url}/storage/v1/object/public/thumnail_folder/{file_name}"
            elif res.status_code == 400 and "already exists" in res.text:
                uploaded_url = f"{settings.supabase_url}/storage/v1/object/public/thumnail_folder/{file_name}"
            else:
                # Try PUT request
                res_put = await client.put(upload_url, headers=upload_headers, content=compressed_bytes)
                if res_put.status_code == 200:
                    uploaded_url = f"{settings.supabase_url}/storage/v1/object/public/thumnail_folder/{file_name}"

            if uploaded_url:
                print(f"[Thumbnail] Uploaded successfully: {uploaded_url}")
            else:
                print(f"[Thumbnail] Upload failed: {res.text}")
    except Exception as e:
        print(f"[Thumbnail] Error uploading image: {e}")

    # Step 4: Update DB model
    if uploaded_url:
        try:
            async with AsyncSessionLocal() as db:
                db_clip = await db.get(SubmittedClip, clip_id)
                if db_clip:
                    db_clip.clip_thumbnail = uploaded_url
                    await db.commit()
                    print(f"[Thumbnail] Database updated for clip {clip_id}")
        except Exception as e:
            print(f"[Thumbnail] Error saving to DB: {e}")


@router.post("/verify-instagram")
@router.post("/verify-instagram/")
async def verify_instagram(
    payload: VerifyInstagramRequest,
    current_user: CurrentUser = Depends(require_role("creator")),
    db: AsyncSession = Depends(get_db),
):
    from routes.instagramVerifier import verify_instagram_username

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
    
    from backend.tasks.onboarding import fetch_and_save_clip_thumbnail_task
    fetch_and_save_clip_thumbnail_task.delay(clip.id, str(payload.clip_url))
    
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
        response.append(serialize_submitted_clip(clip) | {"media_id": None, "caption": None, "instagram_posted_at": None})
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
