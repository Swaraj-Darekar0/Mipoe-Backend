from datetime import datetime, UTC
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import CurrentUser, require_role
from backend.core.config import get_settings
from backend.db.models import (
    AcceptedClip,
    Brand,
    BrandTransaction,
    BrandTransactionType,
    Campaign,
    Creator,
    CreatorTransaction,
    CreatorTransactionType,
    PlatformWallet,
    RefundAudit,
    TransactionStatus,
)
from backend.db.session import get_db
from backend.services.notifications import append_creator_notification


router = APIRouter()
settings = get_settings()


def to_legacy_status(status) -> str:
    value = status.value if hasattr(status, "value") else str(status)
    if value == "completed":
        return "success"
    return value


def get_cashfree_headers() -> dict[str, str]:
    return {
        "accept": "application/json",
        "content-type": "application/json",
        "x-api-version": "2023-08-01",
        "x-client-id": settings.cashfree_app_id or "",
        "x-client-secret": settings.cashfree_secret_key or "",
    }


async def get_brand_campaign(db: AsyncSession, brand_id: int, campaign_id: int) -> Campaign | None:
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id, Campaign.brand_id == brand_id))
    return result.scalar_one_or_none()


async def fetch_creator_names(db: AsyncSession, creator_ids: list[int]) -> dict[int, str]:
    if not creator_ids:
        return {}
    result = await db.execute(select(Creator.id, Creator.username).where(Creator.id.in_(creator_ids)))
    return {row.id: row.username for row in result.all()}


@router.post("/create-deposit-order")
async def create_deposit_order(
    payload: dict,
    current_user: CurrentUser = Depends(require_role("brand")),
    db: AsyncSession = Depends(get_db),
):
    amount = payload.get("amount")
    if not amount or float(amount) < 1:
        raise HTTPException(status_code=400, detail="Invalid amount")

    brand = await db.get(Brand, current_user.id)
    if not brand:
        raise HTTPException(status_code=404, detail="Brand not found")

    order_id = f"order_{current_user.id}_{uuid4().hex[:8]}"
    request_payload = {
        "order_amount": float(amount),
        "order_currency": "INR",
        "order_id": order_id,
        "customer_details": {
            "customer_id": str(current_user.id),
            "customer_name": brand.username or "Brand User",
            "customer_email": brand.email,
            "customer_phone": brand.phone or "9999999999",
        },
        "order_meta": {
            "return_url": f"http://localhost:8080/brand/dashboard?order_id={order_id}",
        },
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(f"{settings.cashfree_api_url}/orders", headers=get_cashfree_headers(), json=request_payload)
        if response.status_code == 200:
            data = response.json()
            return {"payment_session_id": data.get("payment_session_id"), "order_id": order_id}
        error_data = response.json() if response.content else {}
        raise HTTPException(status_code=400, detail=error_data.get("message", "Failed to create order"))
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Payment Gateway Error: {exc}") from exc


@router.post("/verify-deposit")
async def verify_deposit(
    payload: dict,
    current_user: CurrentUser = Depends(require_role("brand")),
    db: AsyncSession = Depends(get_db),
):
    order_id = payload.get("order_id")
    if not order_id:
        raise HTTPException(status_code=400, detail="Missing order_id")

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(f"{settings.cashfree_api_url}/orders/{order_id}", headers=get_cashfree_headers())
    if response.status_code != 200:
        raise HTTPException(status_code=400, detail="Could not fetch order status")

    cf_data = response.json()
    if cf_data.get("order_status") != "PAID":
        raise HTTPException(status_code=400, detail="Payment not completed")

    existing_result = await db.execute(select(BrandTransaction).where(BrandTransaction.external_txn_id == order_id))
    if existing_result.scalar_one_or_none():
        return {"msg": "Order already processed", "status": "PAID"}

    brand = await db.get(Brand, current_user.id)
    if not brand:
        raise HTTPException(status_code=404, detail="Brand not found")

    amount_paid = float(cf_data.get("order_amount") or 0)
    brand.wallet_balance = float(brand.wallet_balance or 0) + amount_paid
    db.add(
        BrandTransaction(
            brand_id=current_user.id,
            amount=amount_paid,
            type=BrandTransactionType.deposit,
            status=TransactionStatus.completed,
            external_txn_id=order_id,
            description=f"Deposit via Cashfree Order {order_id}",
        )
    )
    await db.commit()
    return {"msg": "Deposit verified", "new_balance": brand.wallet_balance}


@router.get("/virtual-account")
async def get_virtual_account(
    current_user: CurrentUser = Depends(require_role("brand")),
    db: AsyncSession = Depends(get_db),
):
    brand = await db.get(Brand, current_user.id)
    if not brand:
        raise HTTPException(status_code=404, detail="Brand not found")
    if brand.virtual_acc_number:
        return {
            "account_number": brand.virtual_acc_number,
            "ifsc": brand.virtual_acc_ifsc,
            "vpa_id": brand.virtual_vpa_id,
        }

    new_acc_num = f"KIPP{current_user.id}"
    brand.virtual_acc_number = new_acc_num
    brand.virtual_acc_ifsc = "UTIB0CCH274"
    brand.virtual_vpa_id = f"{new_acc_num}@cfree"
    await db.commit()
    return {
        "account_number": brand.virtual_acc_number,
        "ifsc": brand.virtual_acc_ifsc,
        "vpa_id": brand.virtual_vpa_id,
    }


@router.get("/wallet-balance")
async def get_wallet_balance(
    current_user: CurrentUser = Depends(require_role("brand", "creator")),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role == "brand":
        user = await db.get(Brand, current_user.id)
    else:
        user = await db.get(Creator, current_user.id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {"role": current_user.role, "balance": float(user.wallet_balance or 0.0), "currency": "INR"}


@router.post("/allocate-budget")
async def allocate_budget(
    payload: dict,
    current_user: CurrentUser = Depends(require_role("brand")),
    db: AsyncSession = Depends(get_db),
):
    campaign_id = payload.get("campaign_id")
    amount = payload.get("amount")
    if not campaign_id or not amount or float(amount) <= 0:
        raise HTTPException(status_code=400, detail="Missing or invalid campaign_id or amount")

    brand = await db.get(Brand, current_user.id)
    campaign_result = await db.execute(select(Campaign).where(Campaign.id == campaign_id, Campaign.brand_id == current_user.id))
    campaign = campaign_result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found or not authorized")
    if not brand:
        raise HTTPException(status_code=404, detail="Brand not found")
    if float(brand.wallet_balance or 0) < float(amount):
        raise HTTPException(status_code=400, detail="Insufficient wallet balance")

    brand.wallet_balance = float(brand.wallet_balance or 0) - float(amount)
    campaign.funds_allocated = float(campaign.funds_allocated or 0) + float(amount)
    campaign.budget = float(campaign.budget or 0) + float(amount)
    db.add(
        BrandTransaction(
            brand_id=current_user.id,
            campaign_id=campaign.id,
            amount=float(amount),
            type=BrandTransactionType.campaign_funding,
            status=TransactionStatus.completed,
            description=f"Allocated Rs.{amount} to campaign {campaign.id}",
        )
    )
    await db.commit()
    return {
        "msg": "Funds allocated successfully",
        "allocated_amount": float(amount),
        "new_wallet_balance": brand.wallet_balance,
        "new_funds_allocated": campaign.funds_allocated,
        "new_budget": campaign.budget,
        "campaign_id": campaign.id,
    }


@router.post("/reclaim-budget")
async def reclaim_budget(
    payload: dict,
    current_user: CurrentUser = Depends(require_role("brand")),
    db: AsyncSession = Depends(get_db),
):
    campaign_id = payload.get("campaign_id")
    amount = payload.get("amount")
    if not campaign_id or not amount or float(amount) <= 0:
        raise HTTPException(status_code=400, detail="Missing or invalid campaign_id or amount")

    brand = await db.get(Brand, current_user.id)
    campaign_result = await db.execute(select(Campaign).where(Campaign.id == campaign_id, Campaign.brand_id == current_user.id))
    campaign = campaign_result.scalar_one_or_none()
    if not campaign or not brand:
        raise HTTPException(status_code=404, detail="Campaign not found or not authorized")

    reclaimable = float(campaign.funds_allocated or 0) - float(campaign.funds_distributed or 0)
    if reclaimable < float(amount):
        raise HTTPException(status_code=400, detail=f"Cannot reclaim Rs.{amount}. Only Rs.{reclaimable} available")

    brand.wallet_balance = float(brand.wallet_balance or 0) + float(amount)
    campaign.funds_allocated = float(campaign.funds_allocated or 0) - float(amount)
    campaign.budget = float(campaign.budget or 0) - float(amount)
    db.add(
        BrandTransaction(
            brand_id=current_user.id,
            campaign_id=campaign.id,
            amount=float(amount),
            type=BrandTransactionType.refund,
            status=TransactionStatus.completed,
            description=f"Reclaimed Rs.{amount} from campaign {campaign.id}",
        )
    )
    await db.commit()
    return {
        "msg": "Funds reclaimed successfully",
        "reclaimed_amount": float(amount),
        "new_wallet_balance": brand.wallet_balance,
        "new_funds_allocated": campaign.funds_allocated,
        "campaign_id": campaign.id,
    }


@router.post("/distribute-to-creator")
async def distribute_to_creator(
    payload: dict,
    current_user: CurrentUser = Depends(require_role("brand")),
    db: AsyncSession = Depends(get_db),
):
    campaign_id = payload.get("campaign_id")
    creator_id = payload.get("creator_id")
    view_count = payload.get("view_count")
    cpv = payload.get("cpv")
    view_threshold = payload.get("view_threshold")
    if not all(value is not None for value in [campaign_id, creator_id, view_count, cpv, view_threshold]):
        raise HTTPException(status_code=400, detail="Missing required fields")

    campaign_result = await db.execute(select(Campaign).where(Campaign.id == campaign_id, Campaign.brand_id == current_user.id))
    campaign = campaign_result.scalar_one_or_none()
    creator = await db.get(Creator, int(creator_id))
    platform_wallet = await db.get(PlatformWallet, 1)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found or not authorized")
    if not creator:
        raise HTTPException(status_code=404, detail="Creator not found")
    if not platform_wallet:
        raise HTTPException(status_code=404, detail="Platform wallet not found")

    earnings = (float(view_count) / float(view_threshold)) * float(cpv) if float(view_threshold) > 0 else 0
    available = float(campaign.funds_allocated or 0) - float(campaign.funds_distributed or 0)
    if available < earnings:
        raise HTTPException(status_code=400, detail=f"Insufficient funds. Required: Rs.{earnings:.2f}, Available: Rs.{available:.2f}")

    creator_share = earnings * 0.9
    platform_commission = earnings * 0.1
    creator.wallet_balance = float(creator.wallet_balance or 0) + creator_share
    campaign.funds_distributed = float(campaign.funds_distributed or 0) + earnings
    platform_wallet.balance = float(platform_wallet.balance or 0) + platform_commission

    db.add(
        CreatorTransaction(
            creator_id=creator.id,
            campaign_id=campaign.id,
            amount=creator_share,
            type=CreatorTransactionType.earning,
            status=TransactionStatus.completed,
            description=f"Earned Rs.{creator_share:.2f} from {view_count} views",
        )
    )
    db.add(
        BrandTransaction(
            brand_id=current_user.id,
            campaign_id=campaign.id,
            amount=earnings,
            type=BrandTransactionType.distribution,
            status=TransactionStatus.completed,
            description=f"Distributed Rs.{earnings:.2f} to creator {creator.id}",
        )
    )
    await append_creator_notification(
        db,
        creator.id,
        message=f"You earned Rs.{creator_share:.2f} from campaign '{campaign.name}'.",
        notification_type="earning_payout",
        campaign_id=campaign.id,
        amount=creator_share,
    )
    await db.commit()
    return {
        "msg": "Distribution completed successfully",
        "campaign_id": campaign.id,
        "creator_id": creator.id,
        "creator_share": creator_share,
        "platform_commission": platform_commission,
        "new_creator_balance": creator.wallet_balance,
        "new_funds_distributed": campaign.funds_distributed,
    }


@router.post("/creator-withdraw")
async def creator_withdraw(
    payload: dict,
    current_user: CurrentUser = Depends(require_role("creator")),
    db: AsyncSession = Depends(get_db),
):
    amount = payload.get("amount")
    payout_method = payload.get("payout_method")
    if not amount or float(amount) <= 0:
        raise HTTPException(status_code=400, detail="Invalid amount")
    if payout_method not in {"upi", "bank"}:
        raise HTTPException(status_code=400, detail="Invalid payout method")

    creator = await db.get(Creator, current_user.id)
    if not creator:
        raise HTTPException(status_code=404, detail="Creator not found")
    if float(creator.wallet_balance or 0) < float(amount):
        raise HTTPException(status_code=400, detail="Insufficient balance")

    creator.wallet_balance = float(creator.wallet_balance or 0) - float(amount)
    reference_id = f"WD_{uuid4().hex[:12]}"
    txn = CreatorTransaction(
        creator_id=creator.id,
        amount=float(amount),
        type=CreatorTransactionType.withdrawal,
        status=TransactionStatus.pending,
        payout_method=payout_method,
        external_txn_id=reference_id,
        description=f"Withdrawal to {payout_method}",
    )
    db.add(txn)
    await append_creator_notification(
        db,
        creator.id,
        message=f"Your withdrawal of Rs.{amount} has been initiated.",
        notification_type="withdrawal_initiated",
        amount=float(amount),
        payout_method=payout_method,
    )
    await db.commit()
    await db.refresh(txn)
    return {
        "msg": "Withdrawal initiated successfully",
        "amount": float(amount),
        "new_balance": creator.wallet_balance,
        "payout_method": payout_method,
        "reference_id": reference_id,
        "utr": txn.utr,
        "status": txn.status.value,
    }


@router.post("/creator/payout-details")
@router.put("/creator/payout-details")
async def save_payout_details(
    payload: dict,
    current_user: CurrentUser = Depends(require_role("creator")),
    db: AsyncSession = Depends(get_db),
):
    creator = await db.get(Creator, current_user.id)
    if not creator:
        raise HTTPException(status_code=404, detail="Creator not found")

    payout_method = payload.get("payout_method")
    if payout_method not in {"upi", "bank"}:
        raise HTTPException(status_code=400, detail="Invalid payout method")

    creator.payout_method = payout_method
    if payout_method == "upi":
        creator.upi_id = payload.get("upi_id")
        creator.bank_account = None
        creator.ifsc = None
        creator.account_holder_name = None
        await db.commit()
        return {"msg": "Payout details saved successfully", "payout_method": "upi", "upi_id": creator.upi_id}

    creator.bank_account = payload.get("bank_account")
    creator.ifsc = payload.get("ifsc")
    creator.account_holder_name = payload.get("account_holder_name")
    creator.upi_id = None
    await db.commit()
    return {
        "msg": "Payout details saved successfully",
        "payout_method": "bank",
        "bank_account": creator.bank_account,
        "ifsc": creator.ifsc,
        "account_holder_name": creator.account_holder_name,
    }


@router.get("/creator/payout-details")
async def get_payout_details(
    current_user: CurrentUser = Depends(require_role("creator")),
    db: AsyncSession = Depends(get_db),
):
    creator = await db.get(Creator, current_user.id)
    if not creator:
        raise HTTPException(status_code=404, detail="Creator not found")
    if creator.payout_method == "upi":
        return {"msg": "Payout details retrieved successfully", "payout_method": "upi", "upi_id": creator.upi_id}
    if creator.payout_method == "bank":
        return {
            "msg": "Payout details retrieved successfully",
            "payout_method": "bank",
            "bank_account": creator.bank_account,
            "ifsc": creator.ifsc,
            "account_holder_name": creator.account_holder_name,
        }
    return {"msg": "No payout details found", "payout_method": None}


@router.post("/creator/verify-payout-details")
async def verify_payout_details(
    current_user: CurrentUser = Depends(require_role("creator")),
    db: AsyncSession = Depends(get_db),
):
    creator = await db.get(Creator, current_user.id)
    if not creator:
        raise HTTPException(status_code=404, detail="Creator not found")
    if creator.payout_method == "upi":
        missing = [field for field in ["upi_id"] if not getattr(creator, field)]
    elif creator.payout_method == "bank":
        missing = [field for field in ["bank_account", "ifsc", "account_holder_name"] if not getattr(creator, field)]
    else:
        missing = ["payout_method"]
    return {
        "msg": "Payout details verification completed",
        "verified": not missing,
        "payout_method": creator.payout_method,
        "missing": missing,
    }


@router.get("/creator/withdrawals")
async def get_withdrawal_history(
    status: str | None = Query(default=None),
    limit: int = Query(default=20),
    offset: int = Query(default=0),
    current_user: CurrentUser = Depends(require_role("creator")),
    db: AsyncSession = Depends(get_db),
):
    query = select(CreatorTransaction).where(
        CreatorTransaction.creator_id == current_user.id,
        CreatorTransaction.type == CreatorTransactionType.withdrawal,
    )
    if status:
        query = query.where(CreatorTransaction.status == status)
    query = query.order_by(CreatorTransaction.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    txns = result.scalars().all()
    return {
        "msg": "Withdrawal history retrieved",
        "withdrawals": [
            {
                "id": txn.id,
                "amount": txn.amount,
                "status": to_legacy_status(txn.status),
                "payout_method": txn.payout_method,
                "reference_id": txn.external_txn_id,
                "utr": txn.utr,
                "created_at": txn.created_at.isoformat() if txn.created_at else None,
                "type": txn.type.value if hasattr(txn.type, "value") else txn.type,
            }
            for txn in txns
        ],
        "count": len(txns),
        "limit": limit,
        "offset": offset,
    }


@router.get("/creator/notifications/{creator_id}")
async def get_creator_notifications(
    creator_id: int,
    current_user: CurrentUser = Depends(require_role("creator")),
    db: AsyncSession = Depends(get_db),
):
    if current_user.id != creator_id:
        raise HTTPException(status_code=403, detail="Unauthorized")
    creator = await db.get(Creator, creator_id)
    if not creator:
        raise HTTPException(status_code=404, detail="Creator not found")
    notifications = list(creator.notifications or [])
    notifications.sort(key=lambda item: item.get("timestamp", ""), reverse=True)
    return {"msg": "Notifications retrieved successfully", "notifications": notifications}


@router.get("/transactions/{user_type}/{user_id}")
async def get_transactions(
    user_type: str,
    user_id: str,
    campaign_id: int | None = Query(default=None),
    txn_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=50),
    offset: int = Query(default=0),
    current_user: CurrentUser = Depends(require_role("brand", "creator")),
    db: AsyncSession = Depends(get_db),
):
    if str(current_user.id) != str(user_id):
        raise HTTPException(status_code=403, detail="Unauthorized")

    if user_type == "brand":
        sql = """
            SELECT * FROM detailed_brand_creator_transaction_view
            WHERE brand_id = :user_id
        """
        params = {"user_id": int(user_id), "limit": limit, "offset": offset}
        if campaign_id is not None:
            sql += " AND campaign_id = :campaign_id"
            params["campaign_id"] = campaign_id
        if txn_type:
            sql += " AND type = :txn_type"
            params["txn_type"] = txn_type
        if status:
            sql += " AND status::text = :status"
            params["status"] = status
        sql += " ORDER BY created_at DESC LIMIT :limit OFFSET :offset"
        result = await db.execute(text(sql), params)
        rows = [dict(row._mapping) for row in result]
    elif user_type == "creator":
        query = select(CreatorTransaction).where(CreatorTransaction.creator_id == int(user_id))
        if campaign_id is not None:
            query = query.where(CreatorTransaction.campaign_id == campaign_id)
        if txn_type:
            query = query.where(CreatorTransaction.type == txn_type)
        if status:
            query = query.where(CreatorTransaction.status == status)
        query = query.order_by(CreatorTransaction.created_at.desc()).limit(limit).offset(offset)
        result = await db.execute(query)
        rows = [
            {
                "id": txn.id,
                "creator_id": txn.creator_id,
                "campaign_id": txn.campaign_id,
                "created_at": txn.created_at.isoformat() if txn.created_at else None,
                "description": txn.description,
                "type": txn.type.value if hasattr(txn.type, "value") else txn.type,
                "amount": txn.amount,
                "status": to_legacy_status(txn.status),
                "external_txn_id": txn.external_txn_id,
                "payout_method": txn.payout_method,
                "utr": txn.utr,
            }
            for txn in result.scalars().all()
        ]
    else:
        raise HTTPException(status_code=400, detail="Invalid user type")

    return {
        "msg": "Transactions retrieved successfully",
        "user_type": user_type,
        "user_id": str(user_id),
        "count": len(rows),
        "transactions": rows,
        "limit": limit,
        "offset": offset,
    }


@router.post("/creator/revert-withdrawal")
async def revert_failed_withdrawal(
    payload: dict,
    current_user: CurrentUser = Depends(require_role("creator")),
    db: AsyncSession = Depends(get_db),
):
    transaction_id = payload.get("transaction_id")
    if not transaction_id:
        raise HTTPException(status_code=400, detail="Missing transaction_id")

    txn = await db.get(CreatorTransaction, int(transaction_id))
    creator = await db.get(Creator, current_user.id)
    if not txn or txn.creator_id != current_user.id or txn.status != TransactionStatus.failed:
        raise HTTPException(status_code=404, detail="Failed transaction not found, or it cannot be reverted")
    if not creator:
        raise HTTPException(status_code=404, detail="Creator not found")

    revert_amount = float(txn.amount or 0)
    creator.wallet_balance = float(creator.wallet_balance or 0) + revert_amount
    txn.status = TransactionStatus.cancelled
    txn.description = f"{txn.description or 'Withdrawal'} (reverted manually)"
    db.add(
        CreatorTransaction(
            creator_id=creator.id,
            amount=revert_amount,
            type=CreatorTransactionType.earning,
            status=TransactionStatus.completed,
            description=f"Reverted failed withdrawal (Txn ID: {transaction_id})",
            external_txn_id=f"REVERT_{transaction_id}",
        )
    )
    await db.commit()
    return {"msg": "Failed withdrawal successfully reverted.", "reverted_amount": revert_amount, "new_balance": creator.wallet_balance}


@router.post("/refund-campaign")
async def refund_campaign(
    payload: dict,
    current_user: CurrentUser = Depends(require_role("brand")),
    db: AsyncSession = Depends(get_db),
):
    campaign_id = payload.get("campaign_id")
    if not campaign_id:
        raise HTTPException(status_code=400, detail="Missing campaign_id")

    campaign = await get_brand_campaign(db, current_user.id, int(campaign_id))
    brand = await db.get(Brand, current_user.id)
    if not campaign or not brand:
        raise HTTPException(status_code=404, detail="Campaign not found or not authorized")

    funds_allocated = float(campaign.funds_allocated or 0)
    funds_distributed = float(campaign.funds_distributed or 0)
    refundable = funds_allocated - funds_distributed
    if refundable <= 0:
        raise HTTPException(status_code=400, detail="No funds to refund. All allocated amounts have been distributed.")

    brand.wallet_balance = float(brand.wallet_balance or 0) + refundable
    campaign.funds_allocated = 0
    campaign.funds_distributed = 0
    db.add(
        BrandTransaction(
            brand_id=current_user.id,
            campaign_id=campaign.id,
            amount=refundable,
            type=BrandTransactionType.refund,
            status=TransactionStatus.completed,
            description=f"Refunded Rs.{refundable:.2f} from campaign {campaign.id}",
        )
    )
    await db.commit()
    return {
        "msg": "Campaign refunded successfully",
        "campaign_id": campaign.id,
        "refundable_amount": refundable,
        "funds_allocated": funds_allocated,
        "funds_distributed": funds_distributed,
        "new_wallet_balance": brand.wallet_balance,
    }


@router.get("/campaign-summary/{campaign_id}")
async def get_campaign_summary(
    campaign_id: int,
    current_user: CurrentUser = Depends(require_role("brand", "creator", "admin")),
    db: AsyncSession = Depends(get_db),
):
    campaign = await db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if current_user.role == "brand" and campaign.brand_id != current_user.id:
        raise HTTPException(status_code=403, detail="Unauthorized")

    clips_result = await db.execute(select(AcceptedClip).where(AcceptedClip.campaign_id == campaign_id))
    clips = clips_result.scalars().all()
    creator_count = len({clip.creator_id for clip in clips})
    funds_allocated = float(campaign.funds_allocated or 0)
    funds_distributed = float(campaign.funds_distributed or 0)
    refundable = funds_allocated - funds_distributed

    return {
        "msg": "Campaign summary retrieved successfully",
        "campaign_id": campaign_id,
        "budget": campaign.budget,
        "cpv": campaign.cpv,
        "view_threshold": campaign.view_threshold,
        "total_view_count": campaign.total_view_count or 0,
        "deadline": campaign.deadline.isoformat() if campaign.deadline else None,
        "financial_summary": {
            "funds_allocated": funds_allocated,
            "funds_distributed": funds_distributed,
            "refundable": refundable,
            "platform_earnings": funds_distributed * 0.1,
            "utilization_percentage": (funds_distributed / funds_allocated * 100) if funds_allocated > 0 else 0,
        },
        "participation": {
            "creator_count": creator_count,
            "total_clips": len(clips),
        },
    }


@router.get("/calculate-earnings/{campaign_id}/{creator_id}")
async def calculate_earnings(
    campaign_id: int,
    creator_id: int,
    include_clips: bool = Query(default=False),
    current_user: CurrentUser = Depends(require_role("brand", "creator")),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role == "creator" and current_user.id != creator_id:
        raise HTTPException(status_code=403, detail="Unauthorized")

    campaign = await db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    clips_result = await db.execute(
        select(AcceptedClip).where(AcceptedClip.campaign_id == campaign_id, AcceptedClip.creator_id == creator_id)
    )
    clips = clips_result.scalars().all()
    if current_user.role == "creator" and not clips:
        raise HTTPException(status_code=404, detail="You have no clips on this campaign")

    total_views = sum(int(clip.view_count or 0) for clip in clips)
    total_paid = sum(float(clip.amount_paid or 0) for clip in clips)
    total_earnings = (total_views / campaign.view_threshold) * campaign.cpv if campaign.view_threshold else 0
    pending_earnings = total_earnings - total_paid

    return {
        "msg": "Earnings calculated successfully",
        "campaign_id": campaign_id,
        "creator_id": creator_id,
        "campaign_metrics": {
            "cpv": campaign.cpv,
            "view_threshold": campaign.view_threshold,
            "brand_id": campaign.brand_id,
        },
        "performance": {
            "total_clips": len(clips),
            "total_views": total_views,
            "milestones_reached": total_views // campaign.view_threshold if campaign.view_threshold else 0,
        },
        "earnings": {
            "total_earned": total_earnings,
            "creator_share": total_earnings * 0.9,
            "platform_commission": total_earnings * 0.1,
            "total_already_paid": total_paid,
            "pending_amount": pending_earnings,
            "pending_creator_share": pending_earnings * 0.9,
        },
        "clips": [
            {
                "id": clip.id,
                "view_count": clip.view_count,
                "amount_paid": clip.amount_paid,
                "clip_url": clip.clip_url,
                "submitted_at": clip.submitted_at.isoformat() if clip.submitted_at else None,
            }
            for clip in clips
        ] if include_clips else None,
    }


@router.post("/bulk-distribute")
async def bulk_distribute(
    payload: dict,
    current_user: CurrentUser = Depends(require_role("brand")),
    db: AsyncSession = Depends(get_db),
):
    distributions = payload.get("distributions", [])
    if not isinstance(distributions, list) or not distributions:
        raise HTTPException(status_code=400, detail="Invalid distributions payload")

    results = []
    total_distributed = 0.0
    successful = 0
    failed = 0

    for dist in distributions:
        try:
            campaign = await get_brand_campaign(db, current_user.id, int(dist.get("campaign_id")))
            creator = await db.get(Creator, int(dist.get("creator_id")))
            platform_wallet = await db.get(PlatformWallet, 1)
            if not campaign:
                raise ValueError("Campaign not found or not authorized")
            if not creator:
                raise ValueError("Creator not found")
            if not platform_wallet:
                raise ValueError("Platform wallet not found")

            view_count = float(dist.get("view_count") or 0)
            cpv = float(dist.get("cpv") or 0)
            view_threshold = float(dist.get("view_threshold") or 0)
            earnings = (view_count / view_threshold) * cpv if view_threshold > 0 else 0
            available = float(campaign.funds_allocated or 0) - float(campaign.funds_distributed or 0)
            if available < earnings:
                raise ValueError(f"Insufficient funds. Required: Rs.{earnings:.2f}, Available: Rs.{available:.2f}")

            creator_share = earnings * 0.9
            platform_commission = earnings * 0.1
            creator.wallet_balance = float(creator.wallet_balance or 0) + creator_share
            campaign.funds_distributed = float(campaign.funds_distributed or 0) + earnings
            platform_wallet.balance = float(platform_wallet.balance or 0) + platform_commission
            db.add(
                CreatorTransaction(
                    creator_id=creator.id,
                    campaign_id=campaign.id,
                    amount=creator_share,
                    type=CreatorTransactionType.earning,
                    status=TransactionStatus.completed,
                    description=f"Earned Rs.{creator_share:.2f} from {view_count} views on campaign {campaign.id}",
                )
            )
            db.add(
                BrandTransaction(
                    brand_id=current_user.id,
                    campaign_id=campaign.id,
                    amount=earnings,
                    type=BrandTransactionType.distribution,
                    status=TransactionStatus.completed,
                    description=f"Distributed Rs.{earnings:.2f} to creator {creator.id}",
                )
            )
            await append_creator_notification(
                db,
                creator.id,
                message=f"You earned Rs.{creator_share:.2f} from campaign '{campaign.name}'.",
                notification_type="earning_payout",
                campaign_id=campaign.id,
                amount=creator_share,
            )
            results.append(
                {
                    "campaign_id": campaign.id,
                    "creator_id": creator.id,
                    "status": "success",
                    "total_earnings": earnings,
                    "creator_share": creator_share,
                    "platform_commission": platform_commission,
                    "new_creator_wallet": creator.wallet_balance,
                }
            )
            total_distributed += earnings
            successful += 1
        except Exception as exc:
            results.append(
                {
                    "campaign_id": dist.get("campaign_id"),
                    "creator_id": dist.get("creator_id"),
                    "status": "failed",
                    "reason": str(exc),
                }
            )
            failed += 1

    await db.commit()
    return {
        "msg": "Bulk distribution completed",
        "summary": {
            "total_requested": len(distributions),
            "successful": successful,
            "failed": failed,
            "total_distributed": total_distributed,
        },
        "results": results,
    }


@router.post("/request-refund")
async def request_refund(
    payload: dict,
    current_user: CurrentUser = Depends(require_role("brand")),
    db: AsyncSession = Depends(get_db),
):
    campaign_id = payload.get("campaign_id")
    requested_amount = float(payload.get("requested_amount") or 0)
    reason = payload.get("reason", "Mid-campaign refund requested")
    campaign = await get_brand_campaign(db, current_user.id, int(campaign_id))
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    allocated = float(campaign.funds_allocated or 0)
    distributed = float(campaign.funds_distributed or 0)
    refundable = allocated - distributed
    if requested_amount > refundable:
        raise HTTPException(status_code=400, detail="Requested refund exceeds refundable amount")
    if requested_amount <= 0:
        raise HTTPException(status_code=400, detail="Refund amount must be greater than 0")

    audit = RefundAudit(
        brand_id=current_user.id,
        campaign_id=campaign.id,
        refund_type="mid_campaign",
        requested_amount=requested_amount,
        allocated_amount=allocated,
        distributed_amount=distributed,
        refundable_amount=refundable,
        status="pending",
        reason=reason,
        audit_metadata={
            "requested_at": datetime.now(UTC).isoformat(),
            "campaign_name": campaign.name,
        },
    )
    db.add(audit)
    await db.commit()
    await db.refresh(audit)
    return {
        "msg": "Refund request submitted for admin approval",
        "refund_id": audit.id,
        "campaign_id": campaign.id,
        "requested_amount": requested_amount,
        "refundable_amount": refundable,
        "status": audit.status,
        "created_at": audit.created_at.isoformat() if audit.created_at else None,
    }


@router.get("/refund-requests")
async def get_refund_requests(
    status: str | None = Query(default=None),
    campaign_id: int | None = Query(default=None),
    limit: int = Query(default=20),
    offset: int = Query(default=0),
    current_user: CurrentUser = Depends(require_role("brand")),
    db: AsyncSession = Depends(get_db),
):
    query = select(RefundAudit).where(RefundAudit.brand_id == current_user.id)
    if status:
        query = query.where(RefundAudit.status == status)
    if campaign_id:
        query = query.where(RefundAudit.campaign_id == campaign_id)
    query = query.order_by(RefundAudit.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(query)
    audits = result.scalars().all()
    return {
        "msg": "Refund requests retrieved successfully",
        "refund_requests": [
            {
                "refund_id": audit.id,
                "campaign_id": audit.campaign_id,
                "type": audit.refund_type,
                "requested_amount": audit.requested_amount,
                "approved_amount": audit.approved_amount,
                "refundable_amount": audit.refundable_amount,
                "status": audit.status,
                "reason": audit.reason,
                "created_at": audit.created_at.isoformat() if audit.created_at else None,
                "updated_at": audit.updated_at.isoformat() if audit.updated_at else None,
                "completed_at": audit.completed_at.isoformat() if audit.completed_at else None,
            }
            for audit in audits
        ],
        "count": len(audits),
        "limit": limit,
        "offset": offset,
    }


@router.post("/admin/approve-refund")
async def approve_refund(
    payload: dict,
    current_user: CurrentUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    refund_id = payload.get("refund_id")
    approved_amount = payload.get("approved_amount")
    approval_reason = payload.get("approval_reason", "Admin approved refund")
    audit = await db.get(RefundAudit, int(refund_id))
    if not audit:
        raise HTTPException(status_code=404, detail="Refund request not found")
    if audit.status != "pending":
        raise HTTPException(status_code=400, detail=f"Refund already {audit.status}")

    approved_amount = float(approved_amount if approved_amount is not None else audit.requested_amount or 0)
    if approved_amount > float(audit.refundable_amount or 0):
        raise HTTPException(status_code=400, detail="Approved amount exceeds refundable amount")
    if approved_amount <= 0:
        raise HTTPException(status_code=400, detail="Approved amount must be greater than 0")

    brand = await db.get(Brand, audit.brand_id)
    if not brand:
        raise HTTPException(status_code=404, detail="Brand not found")

    brand.wallet_balance = float(brand.wallet_balance or 0) + approved_amount
    audit.status = "completed"
    audit.approved_amount = approved_amount
    audit.processed_by_admin_id = current_user.id
    audit.updated_at = datetime.now(UTC).replace(tzinfo=None)
    audit.completed_at = datetime.now(UTC).replace(tzinfo=None)
    audit.audit_metadata = {**(audit.audit_metadata or {}), "approval_reason": approval_reason, "processed_at": datetime.now(UTC).isoformat()}
    db.add(
        BrandTransaction(
            brand_id=audit.brand_id,
            campaign_id=audit.campaign_id,
            amount=approved_amount,
            type=BrandTransactionType.refund,
            status=TransactionStatus.completed,
            description=f"Refund approved: {audit.refund_type} - {approval_reason}",
            refund_audit_id=audit.id,
        )
    )
    await db.commit()
    return {
        "msg": "Refund approved and processed successfully",
        "refund_id": audit.id,
        "campaign_id": audit.campaign_id,
        "approved_amount": approved_amount,
        "brand_wallet_updated": brand.wallet_balance,
        "status": audit.status,
        "processed_at": datetime.now(UTC).isoformat(),
    }


@router.post("/admin/reject-refund")
async def reject_refund(
    payload: dict,
    current_user: CurrentUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    refund_id = payload.get("refund_id")
    rejection_reason = payload.get("rejection_reason", "Refund rejected by admin")
    audit = await db.get(RefundAudit, int(refund_id))
    if not audit:
        raise HTTPException(status_code=404, detail="Refund request not found")
    if audit.status != "pending":
        raise HTTPException(status_code=400, detail=f"Refund already {audit.status}")

    audit.status = "rejected"
    audit.rejection_reason = rejection_reason
    audit.processed_by_admin_id = current_user.id
    audit.updated_at = datetime.now(UTC).replace(tzinfo=None)
    audit.audit_metadata = {**(audit.audit_metadata or {}), "rejected_at": datetime.now(UTC).isoformat()}
    await db.commit()
    return {
        "msg": "Refund rejected successfully",
        "refund_id": audit.id,
        "status": audit.status,
        "rejection_reason": rejection_reason,
    }


@router.get("/refund-status/{refund_id}")
async def get_refund_status(
    refund_id: int,
    current_user: CurrentUser = Depends(require_role("brand", "admin")),
    db: AsyncSession = Depends(get_db),
):
    audit = await db.get(RefundAudit, refund_id)
    if not audit:
        raise HTTPException(status_code=404, detail="Refund not found")
    if current_user.role == "brand" and audit.brand_id != current_user.id:
        raise HTTPException(status_code=403, detail="Unauthorized")

    response = {
        "msg": "Refund status retrieved successfully",
        "refund_id": audit.id,
        "campaign_id": audit.campaign_id,
        "status": audit.status,
        "type": audit.refund_type,
        "requested_amount": audit.requested_amount,
        "refundable_amount": audit.refundable_amount,
        "approved_amount": audit.approved_amount,
        "reason": audit.reason,
        "rejection_reason": audit.rejection_reason,
        "timeline": {
            "created_at": audit.created_at.isoformat() if audit.created_at else None,
            "updated_at": audit.updated_at.isoformat() if audit.updated_at else None,
            "completed_at": audit.completed_at.isoformat() if audit.completed_at else None,
        },
    }
    if audit.status == "completed":
        txn_result = await db.execute(select(BrandTransaction).where(BrandTransaction.refund_audit_id == audit.id))
        txn = txn_result.scalar_one_or_none()
        if txn:
            response["transaction"] = {
                "id": txn.id,
                "amount": txn.amount,
                "status": to_legacy_status(txn.status),
                "created_at": txn.created_at.isoformat() if txn.created_at else None,
            }
    return response


@router.get("/admin/refund-audit-trail")
async def get_refund_audit_trail(
    brand_id: int | None = Query(default=None),
    campaign_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=50),
    offset: int = Query(default=0),
    current_user: CurrentUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    query = select(RefundAudit)
    if brand_id:
        query = query.where(RefundAudit.brand_id == brand_id)
    if campaign_id:
        query = query.where(RefundAudit.campaign_id == campaign_id)
    if status:
        query = query.where(RefundAudit.status == status)
    query = query.order_by(RefundAudit.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(query)
    audits = result.scalars().all()

    total_refunded = sum(float(audit.approved_amount or 0) for audit in audits if audit.status == "completed")
    pending_amount = sum(float(audit.requested_amount or 0) for audit in audits if audit.status == "pending")
    return {
        "msg": "Refund audit trail retrieved successfully",
        "audit_trail": [
            {
                "refund_id": audit.id,
                "brand_id": audit.brand_id,
                "campaign_id": audit.campaign_id,
                "type": audit.refund_type,
                "requested_amount": audit.requested_amount,
                "approved_amount": audit.approved_amount,
                "status": audit.status,
                "reason": audit.reason,
                "created_at": audit.created_at.isoformat() if audit.created_at else None,
                "completed_at": audit.completed_at.isoformat() if audit.completed_at else None,
            }
            for audit in audits
        ],
        "count": len(audits),
        "summary": {
            "total_refunded": total_refunded,
            "pending_approval": pending_amount,
            "limit": limit,
            "offset": offset,
        },
    }
