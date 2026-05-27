import httpx
import logging
from backend.core.config import get_settings

logger = logging.getLogger(__name__)

async def verify_pan_with_cashfree(pan: str, name: str) -> dict:
    """
    Verify PAN using Cashfree Identity Verification API (Sync).
    API Ref: https://www.cashfree.com/docs/api-reference/vrs/v2/pan/verify-pan-sync
    
    Returns a dict with verification results:
    {
        "valid": bool,
        "registered_name": str or None,
        "message": str,
        "status": str  # "SUCCESS", "FAILED"
    }
    """
    settings = get_settings()
    
    # Deriving the verification URL from Cashfree PG URL
    if "sandbox" in (settings.cashfree_api_url or "").lower():
        base_url = "https://sandbox.cashfree.com/verification"
    else:
        base_url = "https://api.cashfree.com/verification"
        
    url = f"{base_url}/pan"
    
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "x-client-id": settings.cashfree_app_id or "",
        "x-client-secret": settings.cashfree_secret_key or "",
    }
    
    payload = {
        "pan": pan,
        "name": name
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(url, headers=headers, json=payload)
            
        if response.status_code == 200:
            data = response.json()
            cf_status = data.get("status", "")
            is_valid = data.get("valid", False) or cf_status == "VALID"
            
            return {
                "valid": is_valid,
                "registered_name": data.get("registered_name") or data.get("name"),
                "message": data.get("message") or "PAN verification completed successfully.",
                "status": "SUCCESS" if is_valid else "FAILED"
            }
        else:
            error_msg = f"Cashfree validation failed with HTTP {response.status_code}"
            try:
                err_data = response.json()
                error_msg = err_data.get("message") or error_msg
            except Exception:
                pass
            logger.error("Cashfree PAN API Error (%s): %s", response.status_code, response.text)
            return {
                "valid": False,
                "registered_name": None,
                "message": error_msg,
                "status": "FAILED"
            }
    except Exception as exc:
        logger.exception("Exception occurred during Cashfree PAN verification")
        return {
            "valid": False,
            "registered_name": None,
            "message": f"Network or API communication error: {exc}",
            "status": "FAILED"
        }
