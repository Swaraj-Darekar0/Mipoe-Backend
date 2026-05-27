import enum
from datetime import date, datetime

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, Enum, Float, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base


class TransactionStatus(str, enum.Enum):
    pending = "pending"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class BrandTransactionType(str, enum.Enum):
    deposit = "deposit"
    withdrawal = "withdrawal"
    campaign_funding = "campaign_funding"
    refund = "refund"
    distribution = "distribution"


class CreatorTransactionType(str, enum.Enum):
    earning = "earning"
    withdrawal = "withdrawal"
    bonus = "bonus"
    penalty = "penalty"


class Admin(Base):
    __tablename__ = "admin"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)


class Brand(Base):
    __tablename__ = "brand"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    wallet_balance: Mapped[float] = mapped_column(Float, nullable=True, server_default=text("0.0"))
    virtual_acc_number: Mapped[str | None] = mapped_column(Text)
    virtual_acc_ifsc: Mapped[str | None] = mapped_column(Text)
    virtual_vpa_id: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(String(20))
    
    # Onboarding and Compliance fields
    onboarding_status: Mapped[str | None] = mapped_column(String(50), default="not_started", server_default=text("'not_started'"))
    pan_number: Mapped[str | None] = mapped_column(Text)
    pan_verification_status: Mapped[str | None] = mapped_column(String(50))
    pan_holder_name: Mapped[str | None] = mapped_column(String(255))
    business_address: Mapped[str | None] = mapped_column(Text)
    logo_url: Mapped[str | None] = mapped_column(Text)
    banner_url: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    instagram_url: Mapped[str | None] = mapped_column(Text)
    youtube_url: Mapped[str | None] = mapped_column(Text)
    website_url: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(100))
    consent_given: Mapped[bool | None] = mapped_column(Boolean, default=False, server_default=text("false"))
    rejection_reason: Mapped[str | None] = mapped_column(Text)

    campaigns: Mapped[list["Campaign"]] = relationship(back_populates="brand")


class Creator(Base):
    __tablename__ = "creator"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    nickname: Mapped[str | None] = mapped_column(String(80))
    bio: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(String(20))
    join_date: Mapped[date] = mapped_column(Date, nullable=False, default=date.today)
    profile_completed: Mapped[bool] = mapped_column(Boolean, nullable=True, default=False, server_default=text("false"))
    wallet_balance: Mapped[float] = mapped_column(Float, nullable=True, server_default=text("0.0"))
    payout_method: Mapped[str | None] = mapped_column(String(10))
    upi_id: Mapped[str | None] = mapped_column(String(255))
    bank_account: Mapped[str | None] = mapped_column(String(20))
    ifsc: Mapped[str | None] = mapped_column(String(11))
    account_holder_name: Mapped[str | None] = mapped_column(String(255))
    notifications: Mapped[list[dict] | None] = mapped_column(ARRAY(JSONB), nullable=True, server_default=text("'{}'::jsonb[]"))
    instagram_username: Mapped[str | None] = mapped_column(String)
    instagram_verified: Mapped[bool] = mapped_column(Boolean, nullable=True, default=False, server_default=text("false"))
    consent_given: Mapped[bool | None] = mapped_column(Boolean, default=False, server_default=text("false"))

class Campaign(Base):
    __tablename__ = "campaign"
    __table_args__ = (
        CheckConstraint("funds_distributed <= funds_allocated", name="check_funds_distributed"),
        CheckConstraint(
            "category IN ('fashion_clothing', 'beauty_products', 'youtube')",
            name="valid_category",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brand.id", ondelete="CASCADE"), nullable=False)
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    budget: Mapped[float] = mapped_column(Float, nullable=False)
    cpv: Mapped[float] = mapped_column(Float, nullable=False)
    hashtag: Mapped[str | None] = mapped_column(String(100))
    audio: Mapped[str | None] = mapped_column(String(200))
    deadline: Mapped[date] = mapped_column(Date, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=True, default=True, server_default=text("true"))
    total_view_count: Mapped[int] = mapped_column(Integer, nullable=True, default=0, server_default=text("0"))
    requirements: Mapped[str | None] = mapped_column(Text)
    view_threshold: Mapped[int] = mapped_column(Integer, nullable=True, default=0, server_default=text("0"))
    asset_link: Mapped[str | None] = mapped_column(String)
    category: Mapped[str] = mapped_column(String(20), nullable=False, default="fashion_clothing", server_default=text("'fashion_clothing'"))
    image_url: Mapped[str | None] = mapped_column(Text)
    funds_allocated: Mapped[float] = mapped_column(Float, nullable=True, default=0.0, server_default=text("0.0"))
    funds_distributed: Mapped[float] = mapped_column(Float, nullable=True, default=0.0, server_default=text("0.0"))

    brand: Mapped["Brand"] = relationship(back_populates="campaigns")


class PlatformWallet(Base):
    __tablename__ = "platform_wallet"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    balance: Mapped[float] = mapped_column(Float, nullable=True, default=0.0, server_default=text("0.0"))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class RefundAudit(Base):
    __tablename__ = "refund_audits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brand.id", ondelete="CASCADE"), nullable=False)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaign.id", ondelete="CASCADE"), nullable=False)
    refund_type: Mapped[str] = mapped_column(String(50), nullable=False)
    requested_amount: Mapped[float] = mapped_column(Float, nullable=False)
    allocated_amount: Mapped[float] = mapped_column(Float, nullable=False)
    distributed_amount: Mapped[float] = mapped_column(Float, nullable=False)
    refundable_amount: Mapped[float] = mapped_column(Float, nullable=False)
    approved_amount: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str | None] = mapped_column(String(20), default="pending", server_default=text("'pending'"))
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    rejection_reason: Mapped[str | None] = mapped_column(String(500))
    processed_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin.id", ondelete="SET NULL"))
    external_txn_id: Mapped[str | None] = mapped_column(String(255))
    audit_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=text("CURRENT_TIMESTAMP"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)


class BrandTransaction(Base):
    __tablename__ = "brand_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brand.id", ondelete="CASCADE"), nullable=False)
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("campaign.id", ondelete="SET NULL"))
    type: Mapped[BrandTransactionType] = mapped_column(Enum(BrandTransactionType, name="brand_transaction_type"), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[TransactionStatus] = mapped_column(
        Enum(TransactionStatus, name="transaction_status"),
        nullable=False,
        default=TransactionStatus.pending,
        server_default=text("'pending'"),
    )
    description: Mapped[str | None] = mapped_column(Text)
    external_txn_id: Mapped[str | None] = mapped_column(Text)
    refund_audit_id: Mapped[int | None] = mapped_column(ForeignKey("refund_audits.id", ondelete="SET NULL"))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=text("(now() AT TIME ZONE 'utc')"))


class CreatorTransaction(Base):
    __tablename__ = "creator_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("creator.id", ondelete="CASCADE"), nullable=False)
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("campaign.id", ondelete="SET NULL"))
    type: Mapped[CreatorTransactionType] = mapped_column(Enum(CreatorTransactionType, name="creator_transaction_type"), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[TransactionStatus] = mapped_column(
        Enum(TransactionStatus, name="transaction_status"),
        nullable=False,
        default=TransactionStatus.pending,
        server_default=text("'pending'"),
    )
    description: Mapped[str | None] = mapped_column(Text)
    external_txn_id: Mapped[str | None] = mapped_column(Text)
    payout_method: Mapped[str | None] = mapped_column(Text)
    utr: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=text("(now() AT TIME ZONE 'utc')"))


class AcceptedClip(Base):
    __tablename__ = "accepted_clips"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("creator.id", ondelete="CASCADE"), nullable=False)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaign.id", ondelete="CASCADE"), nullable=False)
    clip_url: Mapped[str] = mapped_column(String(300), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=text("CURRENT_TIMESTAMP"))
    media_id: Mapped[str | None] = mapped_column(String(100))
    view_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    like_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    comment_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    caption: Mapped[str | None] = mapped_column(Text)
    instagram_posted_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_view_count: Mapped[int | None] = mapped_column(Integer, default=0, server_default=text("0"))
    amount_paid: Mapped[float | None] = mapped_column(Float, default=0.0, server_default=text("0.0"))
    clip_thumbnail: Mapped[str | None] = mapped_column(Text)


class SubmittedClip(Base):
    __tablename__ = "submitted_clips"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("creator.id", ondelete="CASCADE"), nullable=False)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaign.id", ondelete="CASCADE"), nullable=False)
    clip_url: Mapped[str] = mapped_column(String(300), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=text("CURRENT_TIMESTAMP"))
    view_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    like_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    comment_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    is_deleted_by_admin: Mapped[bool | None] = mapped_column(Boolean, default=False, server_default=text("false"))
    feedback: Mapped[str | None] = mapped_column(String(255))
    clip_thumbnail: Mapped[str | None] = mapped_column(Text)
