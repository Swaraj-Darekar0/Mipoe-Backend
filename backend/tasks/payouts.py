import asyncio
import math
from datetime import datetime, UTC

from sqlalchemy import select

from backend.db.models import AcceptedClip, BrandTransaction, BrandTransactionType, Campaign, Creator, CreatorTransaction, CreatorTransactionType, PlatformWallet, TransactionStatus
from backend.db.session import AsyncSessionLocal
from backend.services.notifications import append_creator_notification
from backend.tasks.celery_app import celery_app


@celery_app.task(name="payouts.run_hourly_payouts")
def run_hourly_payouts():
    async def _run():
        async with AsyncSessionLocal() as db:
            campaigns_result = await db.execute(
                select(Campaign).where(Campaign.is_active.is_(True), Campaign.funds_allocated > 0)
            )
            campaigns = campaigns_result.scalars().all()

            for campaign in campaigns:
                clips_result = await db.execute(select(AcceptedClip).where(AcceptedClip.campaign_id == campaign.id))
                clips = clips_result.scalars().all()
                funds_available = float(campaign.funds_allocated or 0)

                for clip in clips:
                    current_views = int(clip.view_count or 0)
                    total_milestones = math.floor(current_views / campaign.view_threshold) if campaign.view_threshold else 0
                    if total_milestones <= 0:
                        continue

                    total_earnings_should_be = total_milestones * campaign.cpv
                    amount_paid_so_far = float(clip.amount_paid or 0.0)
                    amount_due_now = total_earnings_should_be - amount_paid_so_far
                    if amount_due_now <= 0 or funds_available < amount_due_now:
                        continue

                    creator_share = amount_due_now * 0.9
                    platform_share = amount_due_now * 0.1
                    creator = await db.get(Creator, clip.creator_id)
                    platform_wallet = await db.get(PlatformWallet, 1)

                    if not creator or not platform_wallet:
                        continue

                    creator.wallet_balance = float(creator.wallet_balance or 0) + creator_share
                    platform_wallet.balance = float(platform_wallet.balance or 0) + platform_share
                    clip.amount_paid = total_earnings_should_be
                    campaign.funds_allocated = funds_available - amount_due_now
                    funds_available = float(campaign.funds_allocated or 0)

                    db.add(
                        CreatorTransaction(
                            creator_id=creator.id,
                            campaign_id=campaign.id,
                            amount=creator_share,
                            type=CreatorTransactionType.earning,
                            status=TransactionStatus.completed,
                            description=f"Earned {creator_share:.2f} from campaign {campaign.id}",
                        )
                    )
                    db.add(
                        BrandTransaction(
                            brand_id=campaign.brand_id,
                            campaign_id=campaign.id,
                            amount=amount_due_now,
                            type=BrandTransactionType.distribution,
                            status=TransactionStatus.completed,
                            description=f"Distributed {amount_due_now:.2f} to creator {creator.id}",
                        )
                    )
                    await append_creator_notification(
                        db,
                        creator.id,
                        message=f"You earned {creator_share:.2f} from campaign '{campaign.name}'.",
                        notification_type="earning_payout",
                        campaign_id=campaign.id,
                        clip_id=clip.id,
                        amount=creator_share,
                    )

            await db.commit()

    asyncio.run(_run())
