from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


class CreateCampaignRequest(BaseModel):
    platform: str
    budget: float
    cpv: float
    hashtag: str | None = None
    audio: str | None = None
    deadline: date
    name: str
    category: Literal["fashion_clothing", "beauty_products", "youtube"]
    requirements: str | None = None
    view_threshold: int = 0
    asset_link: str | None = None
    image_url: HttpUrl | None = None


class UpdateCampaignRequest(BaseModel):
    platform: str | None = None
    budget: float | None = None
    cpv: float | None = None
    hashtag: str | None = None
    audio: str | None = None
    deadline: date | None = None
    name: str | None = None
    category: Literal["fashion_clothing", "beauty_products", "youtube"] | None = None
    requirements: str | None = None
    view_threshold: int | None = None
    asset_link: str | None = None
    image_url: HttpUrl | None = None
    is_active: bool | None = None


class SubmitClipRequest(BaseModel):
    campaign_id: int
    clip_url: HttpUrl


class UpdateClipStatusRequest(BaseModel):
    status: Literal["accepted", "rejected"]
    feedback: str | None = None


class UpdateViewCountRequest(BaseModel):
    view_count: int = Field(ge=0)


class UpdateBrandProfileRequest(BaseModel):
    username: str | None = None
    phone: str | None = None


class UpdateCreatorProfileRequest(BaseModel):
    nickname: str | None = None
    bio: str | None = None
    phone: str | None = None
    instagram_username: str | None = None
    instagram_verified: bool | None = None


class VerifyInstagramRequest(BaseModel):
    username: str
