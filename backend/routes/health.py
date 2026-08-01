"""Root, health, and public config routes."""
from fastapi import APIRouter
from datetime import datetime

from settings import FREE_SCANS_PER_MONTH, PLUS_PRICE_INR, PLUS_YEARLY_INR, GEMINI_API_KEY, INVITE_SCANS_PER_MONTH, INVITE_ONLY
from ai import _llm_configured, HAS_GOOGLE_GENAI
from app.config import SUBSCRIPTIONS_AVAILABLE

router = APIRouter()

@router.get("/")
async def root():
    return {
        "message": "GlamGenius — Personal Stylist & Skin/Hair Wellness Coach",
        "version": "2.0.0",
        "market": "India",
    }


@router.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "llm_configured": _llm_configured(),
        # Never return key material — boolean only
        "gemini_ready": bool(GEMINI_API_KEY and HAS_GOOGLE_GENAI),
    }


@router.get("/config/public")
async def public_config():
    return {
        "free_scans_per_month": FREE_SCANS_PER_MONTH,
        "invite_scans_per_month": INVITE_SCANS_PER_MONTH,
        "invite_only": INVITE_ONLY,
        "plus_price_inr": PLUS_PRICE_INR,
        "plus_yearly_inr": PLUS_YEARLY_INR,
        # The audit found the app selling subscriptions the backend refuses.
        # This flag is now on the public config too, so a signed-out screen can
        # tell before it offers anyone a price.
        "subscriptions_available": SUBSCRIPTIONS_AVAILABLE,
        "currency": "INR",
        "tagline": "Skin · Hair · Style",
        "disclaimer": "General wellness and style guidance — not medical advice.",
    }
