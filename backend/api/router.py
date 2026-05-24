from fastapi import APIRouter

from backend.api.routers import admin, auth, brands, campaigns, creators, payments, system


api_router = APIRouter()
api_router.include_router(auth.router, tags=["auth"])
api_router.include_router(creators.router, tags=["creators"])
api_router.include_router(brands.router, tags=["brands"])
api_router.include_router(campaigns.router, tags=["campaigns"])
api_router.include_router(admin.router, tags=["admin"])
api_router.include_router(payments.router, prefix="/api/payments", tags=["payments"])
api_router.include_router(system.router, tags=["system"])
