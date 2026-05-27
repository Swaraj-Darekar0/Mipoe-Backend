from typing import Literal

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    role: Literal["brand", "creator", "admin"]
    consent_given: bool = Field(default=False)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    role: Literal["brand", "creator", "admin"] | None = None
    consent_given: bool = Field(default=False)


class PasswordResetRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    password: str = Field(min_length=6, max_length=128)
