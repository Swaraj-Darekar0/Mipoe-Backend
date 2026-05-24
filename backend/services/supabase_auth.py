import httpx
from fastapi import HTTPException, status

from backend.core.config import get_settings


settings = get_settings()


async def get_supabase_user(access_token: str) -> dict:
    if not settings.supabase_url:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Supabase is not configured")

    headers = {
        "Authorization": f"Bearer {access_token}",
        "apikey": settings.supabase_anon_key or settings.supabase_key or "",
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(f"{settings.supabase_url}/auth/v1/user", headers=headers)

    if response.status_code != 200:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Supabase access token")

    return response.json()
