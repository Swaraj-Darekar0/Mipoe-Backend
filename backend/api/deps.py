from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import get_settings
from backend.core.security import create_access_token, decode_token
from backend.db.session import get_db, redis_client


@dataclass
class CurrentUser:
    id: int
    role: str
    username: str
    claims: dict


async def get_current_user(
    request: Request,
    response: Response,
    authorization: str = Header(default="")
) -> CurrentUser:
    token = request.cookies.get("access_token")
    if not token:
        if authorization.startswith("Bearer "):
            token = authorization.split(" ", 1)[1].strip()
        else:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing authorization header or cookie")

    try:
        payload = decode_token(token)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Invalid token: {exc}") from exc

    jti = payload.get("jti")
    if jti and await redis_client.get(f"blocklist:{jti}"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has been revoked")

    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")

    try:
        user_id = int(payload["sub"])
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid subject") from exc

    # Sliding Session Check:
    # If the token is more than halfway expired, reissue it
    now = datetime.now(UTC).timestamp()
    iat = payload.get("iat", 0)
    exp = payload.get("exp", 0)
    total_lifetime = exp - iat
    elapsed = now - iat
    
    if total_lifetime > 0 and elapsed > (total_lifetime / 2):
        settings = get_settings()
        new_token = create_access_token(
            subject=payload["sub"],
            role=payload.get("role", ""),
            username=payload.get("username", ""),
            extra_claims=None
        )
        response.set_cookie(
            key="access_token",
            value=new_token,
            httponly=True,
            secure=settings.cookie_secure,
            samesite=settings.cookie_samesite,
            domain=settings.cookie_domain,
            max_age=settings.access_token_expire_minutes * 60,
            path="/"
        )

    return CurrentUser(id=user_id, role=payload.get("role", ""), username=payload.get("username", ""), claims=payload)


async def get_refresh_payload(authorization: str = Header(default="")) -> dict:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing authorization header")

    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = decode_token(token)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Invalid token: {exc}") from exc

    if payload.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token required")

    return payload


def require_role(*allowed_roles: str):
    async def dependency(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if current_user.role not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Unauthorized")
        return current_user

    return dependency


DbSession = Depends(get_db)
