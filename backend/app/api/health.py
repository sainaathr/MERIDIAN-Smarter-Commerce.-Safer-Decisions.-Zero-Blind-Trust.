from fastapi import APIRouter
from app.core.config import settings
import time

router = APIRouter(tags=["Health"])

START_TIME = time.time()

@router.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "MERIDIAN Core API",
        "version": "0.1.0",
        "environment": settings.APP_ENV,
        "uptime_seconds": round(time.time() - START_TIME, 2),
        "integrations": {
            "gemini_configured": bool(settings.GEMINI_API_KEY),
            "paypal_configured": bool(settings.PAYPAL_CLIENT_ID and settings.PAYPAL_CLIENT_SECRET),
            "paypal_mode": settings.PAYPAL_MODE
        }
    }
