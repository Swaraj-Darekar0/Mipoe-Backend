from datetime import date
from secrets import token_urlsafe

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_refresh_payload
from backend.core.config import get_settings
from backend.core.security import create_access_token, create_refresh_token, decode_token, hash_password, verify_password
from backend.db.models import Admin, Brand, Creator
from backend.db.session import get_db, redis_client
from backend.schemas.auth import LoginRequest, PasswordResetRequest, RegisterRequest, ResetPasswordRequest
from backend.services.supabase_auth import get_supabase_user
from backend.tasks.emails import send_password_reset_email


router = APIRouter()
settings = get_settings()


ROLE_MODEL_MAP = {
    "brand": Brand,
    "creator": Creator,
    "admin": Admin,
}


def build_auth_response(user, role: str):
    access_token = create_access_token(str(user.id), role, user.username)
    refresh_token = create_refresh_token(str(user.id), role, user.username)
    payload = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "role": role,
        "username": user.username,
        "user_id": str(user.id),
    }
    if role == "creator":
        payload["profile_completed"] = bool(getattr(user, "profile_completed", False))
    return payload


async def get_user_by_email(db: AsyncSession, role: str, email: str):
    model = ROLE_MODEL_MAP[role]
    result = await db.execute(select(model).where(model.email == email))
    return result.scalar_one_or_none()


@router.post("/register")
async def register(payload: RegisterRequest, db: AsyncSession = Depends(get_db)):
    existing = await get_user_by_email(db, payload.role, payload.email)
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User already exists")

    duplicate_username = await db.execute(select(ROLE_MODEL_MAP[payload.role]).where(ROLE_MODEL_MAP[payload.role].username == payload.username))
    if duplicate_username.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username already exists")

    if payload.role == "creator":
        user = Creator(
            username=payload.username,
            email=payload.email,
            password_hash=hash_password(payload.password),
            join_date=date.today(),
            profile_completed=False,
        )
    elif payload.role == "brand":
        user = Brand(
            username=payload.username,
            email=payload.email,
            password_hash=hash_password(payload.password),
        )
    else:
        user = Admin(
            username=payload.username,
            email=payload.email,
            password_hash=hash_password(payload.password),
        )

    db.add(user)
    await db.commit()
    await db.refresh(user)
    response = build_auth_response(user, payload.role)
    response["msg"] = "User registered successfully"
    return response


@router.post("/login")
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)):
    if payload.role:
        roles_to_try = [payload.role]
    else:
        roles_to_try = ["brand", "creator", "admin"]

    user = None
    resolved_role = None
    for role in roles_to_try:
        candidate = await get_user_by_email(db, role, payload.email)
        if candidate:
            user = candidate
            resolved_role = role
            break

    if not user or not resolved_role:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    return build_auth_response(user, resolved_role)


@router.post("/request-password-reset")
async def request_password_reset(payload: PasswordResetRequest, db: AsyncSession = Depends(get_db)):
    if not settings.resend_api_key or not settings.resend_from_email:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Email service is not configured")

    for role in ("brand", "creator", "admin"):
        user = await get_user_by_email(db, role, payload.email)
        if user:
            token = token_urlsafe(32)
            await redis_client.setex(
                f"reset:{token}",
                settings.password_reset_token_ttl_seconds,
                f"{role}:{user.id}",
            )
            reset_url = f"{settings.frontend_url.rstrip('/')}/reset-password?token={token}"
            send_password_reset_email.delay(user.email, user.username, reset_url, token)
            return {"msg": "Password reset email sent"}
    return {"msg": "Password reset email sent"}


@router.post("/reset-password")
async def reset_password(payload: ResetPasswordRequest, db: AsyncSession = Depends(get_db)):
    reset_payload = await redis_client.get(f"reset:{payload.token}")
    if not reset_payload:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired reset token")

    role, user_id = reset_payload.split(":")
    model = ROLE_MODEL_MAP[role]
    user = await db.get(model, int(user_id))
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    user.password_hash = hash_password(payload.password)
    await db.commit()
    await redis_client.delete(f"reset:{payload.token}")
    return {"msg": "Password reset successful"}


@router.delete("/logout")
async def logout(authorization: str = Header(default="")):
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing authorization header")
    token = authorization.split(" ", 1)[1].strip()
    payload = decode_token(token)
    exp = payload.get("exp")
    jti = payload.get("jti")
    if jti and exp:
        from time import time

        ttl = max(int(exp - time()), 1)
        await redis_client.setex(f"blocklist:{jti}", ttl, "1")
    return {"msg": f"{payload.get('type', 'Token').capitalize()} token successfully revoked"}


@router.post("/refresh")
async def refresh(payload: dict = Depends(get_refresh_payload)):
    access_token = create_access_token(payload["sub"], payload["role"], payload.get("username", ""))
    return {"access_token": access_token}


@router.post("/api/auth/google-sync")
async def google_sync(authorization: str = Header(default=""), db: AsyncSession = Depends(get_db)):
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing authorization header")

    supabase_token = authorization.split(" ", 1)[1].strip()
    supabase_user = await get_supabase_user(supabase_token)
    email = supabase_user.get("email")
    user_metadata = supabase_user.get("user_metadata") or {}
    role = user_metadata.get("role") or "creator"
    username = user_metadata.get("username") or email.split("@")[0]

    if role not in ("brand", "creator", "admin"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing role or email")
    if not email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing role or email")

    user = await get_user_by_email(db, role, email)
    if not user:
        if role == "creator":
            user = Creator(
                username=username,
                email=email,
                password_hash="google_oauth_managed",
                join_date=date.today(),
                profile_completed=False,
            )
        elif role == "brand":
            user = Brand(username=username, email=email, password_hash="google_oauth_managed")
        else:
            user = Admin(username=username, email=email, password_hash="google_oauth_managed")
        db.add(user)
        await db.commit()
        await db.refresh(user)

    response = build_auth_response(user, role)
    response["msg"] = "User profile created successfully"
    return response
