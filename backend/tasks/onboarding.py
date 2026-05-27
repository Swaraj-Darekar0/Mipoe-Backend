import asyncio
from backend.db.models import Brand
from backend.db.session import AsyncSessionLocal
from backend.core.security import encrypt_data
from backend.services.cashfree_verify import verify_pan_with_cashfree
from backend.tasks.celery_app import celery_app

@celery_app.task(name="onboarding.verify_brand_pan_task")
def verify_brand_pan_task(brand_id: int, pan_number: str, holder_name: str, business_address: str):
    """
    Celery task that validates a brand's PAN card using the Cashfree API,
    encrypts the PAN, saves address details, and updates onboarding status.
    """
    async def _run():
        try:
            async with AsyncSessionLocal() as db:
                brand = await db.get(Brand, brand_id)
                if not brand:
                    print(f"Error: Brand with ID {brand_id} not found in database.")
                    return
                
                print(f"Starting Celery PAN verification for Brand ID: {brand_id}")
                
                # Trigger Cashfree verification
                result = await verify_pan_with_cashfree(pan_number, holder_name)
                
                if result["valid"]:
                    brand.pan_verification_status = "SUCCESS"
                    brand.pan_holder_name = result["registered_name"] or holder_name
                    # Store encrypted version of PAN
                    brand.pan_number = encrypt_data(pan_number)
                    brand.business_address = business_address
                    brand.onboarding_status = "pan_verified"
                    brand.rejection_reason = None
                    print(f"PAN Verification succeeded for Brand ID: {brand_id}")
                else:
                    brand.pan_verification_status = "FAILED"
                    brand.onboarding_status = "pan_failed"
                    brand.rejection_reason = result["message"]
                    print(f"PAN Verification failed for Brand ID: {brand_id}. Reason: {result['message']}")
                    
                await db.commit()
        finally:
            # Cleanly dispose of connection pools before event loop shutdown
            from backend.db.session import engine
            await engine.dispose()
            
    asyncio.run(_run())


@celery_app.task(name="onboarding.fetch_and_save_clip_thumbnail_task")
def fetch_and_save_clip_thumbnail_task(clip_id: int, clip_url: str):
    """
    Celery background task to extract, compress, and save a clip's thumbnail to Supabase storage.
    """
    import asyncio
    from backend.api.routers.creators import fetch_and_save_clip_thumbnail
    asyncio.run(fetch_and_save_clip_thumbnail(clip_id, clip_url))

