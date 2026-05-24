from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import CurrentUser, require_role
from backend.db.models import AcceptedClip, Brand, BrandTransaction, BrandTransactionType, Campaign, TransactionStatus
from backend.db.session import get_db
from backend.schemas.common import CreateCampaignRequest, UpdateBrandProfileRequest
from backend.services.campaigns import serialize_campaign


router = APIRouter()


async def get_brand_campaign_or_404(db: AsyncSession, brand_id: int, campaign_id: int) -> Campaign:
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id, Campaign.brand_id == brand_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found or not authorized")
    return campaign


@router.post("/api/brand/campaigns", status_code=201)
async def create_campaign(
    payload: CreateCampaignRequest,
    current_user: CurrentUser = Depends(require_role("brand")),
    db: AsyncSession = Depends(get_db),
):
    campaign = Campaign(
        brand_id=current_user.id,
        platform=payload.platform,
        budget=float(payload.budget),
        cpv=float(payload.cpv),
        hashtag=payload.hashtag,
        audio=payload.audio,
        deadline=payload.deadline,
        name=payload.name,
        category=payload.category,
        requirements=payload.requirements,
        view_threshold=payload.view_threshold,
        asset_link=payload.asset_link,
        image_url=str(payload.image_url) if payload.image_url else None,
        is_active=False,
        total_view_count=0,
        funds_allocated=0.0,
        funds_distributed=0.0,
    )
    db.add(campaign)
    await db.commit()
    await db.refresh(campaign)
    return {"msg": "Campaign created successfully", "campaign_id": campaign.id}


@router.get("/api/brand/campaigns")
async def list_brand_campaigns(
    current_user: CurrentUser = Depends(require_role("brand")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Campaign).where(Campaign.brand_id == current_user.id))
    return [serialize_campaign(campaign) for campaign in result.scalars().all()]


@router.delete("/api/brand/campaigns/{campaign_id}")
async def delete_campaign(
    campaign_id: int,
    current_user: CurrentUser = Depends(require_role("brand")),
    db: AsyncSession = Depends(get_db),
):
    campaign = await get_brand_campaign_or_404(db, current_user.id, campaign_id)
    refundable = max((campaign.funds_allocated or 0) - (campaign.funds_distributed or 0), 0)

    brand = await db.get(Brand, current_user.id)
    if refundable > 0 and brand:
        brand.wallet_balance = float(brand.wallet_balance or 0) + refundable
        db.add(
            BrandTransaction(
                brand_id=current_user.id,
                campaign_id=campaign.id,
                type=BrandTransactionType.refund,
                amount=refundable,
                status=TransactionStatus.completed,
                description=f"Campaign deletion refund for campaign {campaign.id}",
            )
        )

    await db.delete(campaign)
    await db.commit()
    return {"msg": "Campaign deleted successfully"}


async def update_campaign_field(
    db: AsyncSession,
    brand_id: int,
    campaign_id: int,
    field_name: str,
    field_value,
    success_message: str,
):
    campaign = await get_brand_campaign_or_404(db, brand_id, campaign_id)
    setattr(campaign, field_name, field_value)
    await db.commit()
    return {"msg": success_message}


@router.put("/api/brand/campaigns/{campaign_id}/image")
async def update_campaign_image(
    campaign_id: int,
    payload: dict,
    current_user: CurrentUser = Depends(require_role("brand")),
    db: AsyncSession = Depends(get_db),
):
    image_url = payload.get("image_url")
    if not image_url:
        raise HTTPException(status_code=400, detail="Missing image_url field")
    return await update_campaign_field(db, current_user.id, campaign_id, "image_url", image_url, "Campaign image updated successfully")


@router.put("/api/brand/campaigns/{campaign_id}/budget")
async def update_campaign_budget(
    campaign_id: int,
    payload: dict,
    current_user: CurrentUser = Depends(require_role("brand")),
    db: AsyncSession = Depends(get_db),
):
    budget = payload.get("budget")
    if budget is None:
        raise HTTPException(status_code=400, detail="Missing budget field")
    return await update_campaign_field(db, current_user.id, campaign_id, "budget", float(budget), "Campaign budget updated successfully")


@router.put("/api/brand/campaigns/{campaign_id}/requirements")
async def update_campaign_requirements(
    campaign_id: int,
    payload: dict,
    current_user: CurrentUser = Depends(require_role("brand")),
    db: AsyncSession = Depends(get_db),
):
    return await update_campaign_field(
        db,
        current_user.id,
        campaign_id,
        "requirements",
        payload.get("requirements"),
        "Campaign requirements updated successfully",
    )


@router.put("/api/brand/campaigns/{campaign_id}/status")
async def update_campaign_status(
    campaign_id: int,
    payload: dict,
    current_user: CurrentUser = Depends(require_role("brand")),
    db: AsyncSession = Depends(get_db),
):
    new_status = payload.get("is_active")
    if not isinstance(new_status, bool):
        raise HTTPException(status_code=400, detail="Missing or invalid is_active field (must be boolean)")
    return await update_campaign_field(db, current_user.id, campaign_id, "is_active", new_status, "Campaign status updated successfully")


@router.put("/api/brand/campaigns/{campaign_id}/view_threshold")
async def update_campaign_view_threshold(
    campaign_id: int,
    payload: dict,
    current_user: CurrentUser = Depends(require_role("brand")),
    db: AsyncSession = Depends(get_db),
):
    threshold = payload.get("view_threshold")
    if threshold is None:
        raise HTTPException(status_code=400, detail="Missing or invalid view_threshold field (must be non-negative number)")
    return await update_campaign_field(
        db, current_user.id, campaign_id, "view_threshold", int(threshold), "Campaign view threshold updated successfully"
    )


@router.put("/api/brand/campaigns/{campaign_id}/deadline")
async def update_campaign_deadline(
    campaign_id: int,
    payload: dict,
    current_user: CurrentUser = Depends(require_role("brand")),
    db: AsyncSession = Depends(get_db),
):
    deadline = payload.get("deadline")
    if not deadline:
        raise HTTPException(status_code=400, detail="Missing deadline field")
    campaign = await get_brand_campaign_or_404(db, current_user.id, campaign_id)
    from datetime import datetime

    try:
        campaign.deadline = datetime.strptime(deadline, "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid deadline format. Use YYYY-MM-DD.") from exc
    await db.commit()
    return {"msg": "Campaign deadline updated successfully"}


@router.get("/api/brand/campaigns/{campaign_id}/pending-payouts")
async def get_pending_payouts(
    campaign_id: int,
    current_user: CurrentUser = Depends(require_role("brand")),
    db: AsyncSession = Depends(get_db),
):
    campaign = await get_brand_campaign_or_404(db, current_user.id, campaign_id)
    clips_result = await db.execute(select(AcceptedClip).where(AcceptedClip.campaign_id == campaign_id))
    clips = clips_result.scalars().all()

    if not clips:
        return {"msg": "No clips submitted for this campaign", "campaign_id": campaign_id, "pending_payouts": []}

    creator_views: dict[int, int] = {}
    creator_paid: dict[int, float] = {}
    for clip in clips:
        creator_views[clip.creator_id] = creator_views.get(clip.creator_id, 0) + int(clip.view_count or 0)
        creator_paid[clip.creator_id] = creator_paid.get(clip.creator_id, 0.0) + float(clip.amount_paid or 0.0)

    creator_result = await db.execute(select(Creator.id, Creator.username).where(Creator.id.in_(list(creator_views.keys()))))
    creator_names = {row.id: row.username for row in creator_result.all()}

    pending_payouts = []
    for creator_id, total_views in creator_views.items():
        earnings = (total_views / campaign.view_threshold) * campaign.cpv if campaign.view_threshold else 0
        already_paid = creator_paid[creator_id]
        pending = earnings - already_paid
        if pending > 0:
            pending_payouts.append(
                {
                    "creator_id": creator_id,
                    "creator_name": creator_names.get(creator_id, f"Creator {creator_id}"),
                    "total_views": total_views,
                    "total_earnings": earnings,
                    "already_paid": already_paid,
                    "pending_amount": pending,
                    "creator_share": pending * 0.9,
                    "platform_commission": pending * 0.1,
                }
            )

    return {
        "msg": "Pending payouts retrieved successfully",
        "campaign_id": campaign_id,
        "campaign_metrics": {
            "cpv": campaign.cpv,
            "view_threshold": campaign.view_threshold,
            "total_campaign_views": campaign.total_view_count,
        },
        "pending_count": len(pending_payouts),
        "total_pending_amount": sum(item["pending_amount"] for item in pending_payouts),
        "pending_payouts": pending_payouts,
    }


@router.get("/api/brand/profile")
async def get_brand_profile(
    current_user: CurrentUser = Depends(require_role("brand")),
    db: AsyncSession = Depends(get_db),
):
    brand = await db.get(Brand, current_user.id)
    if not brand:
        raise HTTPException(status_code=404, detail="Brand not found")
    return {"id": brand.id, "username": brand.username, "email": brand.email, "phone": brand.phone}


@router.put("/api/brand/profile")
async def update_brand_profile(
    payload: UpdateBrandProfileRequest,
    current_user: CurrentUser = Depends(require_role("brand")),
    db: AsyncSession = Depends(get_db),
):
    brand = await db.get(Brand, current_user.id)
    if not brand:
        raise HTTPException(status_code=404, detail="Brand not found")

    changed = False
    if payload.username is not None:
        brand.username = payload.username
        changed = True
    if payload.phone is not None:
        brand.phone = payload.phone
        changed = True

    if not changed:
        raise HTTPException(status_code=400, detail="No fields to update")

    await db.commit()
    return {"msg": "Brand profile updated successfully"}
