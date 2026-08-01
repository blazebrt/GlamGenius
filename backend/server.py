"""
GlamGenius API — Personal Stylist & Skin/Hair Wellness Coach (India)
"""
from fastapi import FastAPI, APIRouter
from starlette.middleware.cors import CORSMiddleware
import logging

from settings import (
    ALLOWED_ORIGINS,
    ALLOWED_ORIGINS_IS_DEFAULT,
    AI_REQUESTS_PER_HOUR,
    AI_REQUESTS_PER_HOUR_PLUS,
    AI_RATE_WINDOW_MINUTES,
    LOGIN_LOCKOUT_MINUTES,
    PREVIEW_WINDOW_MINUTES,
)
from database import client, db
from routes import health, users, scan, quiz, plans, recommendations, services, subscription, admin

app = FastAPI(title="GlamGenius — Personal Stylist & Wellness Coach", version="2.0.0")
api_router = APIRouter(prefix="/api")

api_router.include_router(health.router)
api_router.include_router(users.router)
api_router.include_router(scan.router)
api_router.include_router(quiz.router)
api_router.include_router(plans.router)
api_router.include_router(recommendations.router)
api_router.include_router(services.router)
api_router.include_router(subscription.router)
api_router.include_router(admin.router)

app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    # Only these websites may call the API from a browser. Set ALLOWED_ORIGINS
    # to your real site before going live. This does not affect the phone app,
    # which is not a browser and sends no Origin header.
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


@app.on_event("startup")
async def startup_db_client():
    logger.info("Starting GlamGenius Wellness Coach API v2...")
    await db.users.create_index("id", unique=True)
    await db.users.create_index("email")
    await db.scans.create_index("user_id")
    await db.style_plans.create_index("user_id")
    await db.subscription_orders.create_index("user_id")
    await db.invite_codes.create_index("code", unique=True)
    await db.invite_codes.create_index("active")
    await db.login_attempts.create_index("key")
    # Failed attempts clean themselves up once the lockout window has passed.
    await db.login_attempts.create_index(
        "created_at", expireAfterSeconds=LOGIN_LOCKOUT_MINUTES * 60
    )
    await db.preview_attempts.create_index("key")
    await db.preview_attempts.create_index(
        "created_at", expireAfterSeconds=PREVIEW_WINDOW_MINUTES * 60
    )
    await db.ai_usage.create_index("user_id")
    # Usage records clean themselves up once the window has passed.
    await db.ai_usage.create_index(
        "created_at", expireAfterSeconds=AI_RATE_WINDOW_MINUTES * 60
    )
    logger.info(
        "CORS allowed origins: %s | AI rate limit: %s/hour free, %s/hour Plus",
        ALLOWED_ORIGINS, AI_REQUESTS_PER_HOUR, AI_REQUESTS_PER_HOUR_PLUS,
    )
    if ALLOWED_ORIGINS_IS_DEFAULT:
        # Loud on purpose. Without this, a deployed website fails to load any
        # data with no server-side error, which looks like a website bug.
        logger.warning(
            "ALLOWED_ORIGINS is not set, so only local development addresses can "
            "call this API from a browser. That is correct on your own machine. "
            "If this server is deployed, your website will load but show no data "
            "until you set ALLOWED_ORIGINS to your site address "
            "(for example https://yoursite.com,https://www.yoursite.com). "
            "The phone app is not affected."
        )
    for origin in ALLOWED_ORIGINS:
        if not origin.startswith(("http://", "https://")):
            logger.warning(
                "ALLOWED_ORIGINS entry %r has no http:// or https:// prefix and will "
                "never match. Browsers compare the full address.", origin,
            )


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
