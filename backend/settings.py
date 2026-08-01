"""Environment-backed settings for the GlamGenius API."""
from pathlib import Path
from dotenv import load_dotenv
import os

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
db_name = os.environ.get("DB_NAME", "glamgenius")

# Secrets — never log or return these values
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""
FREE_SCANS_PER_MONTH = int(os.environ.get("FREE_SCANS_PER_MONTH", "1"))
PLUS_PRICE_INR = int(os.environ.get("PLUS_PRICE_INR", "249"))
PLUS_YEARLY_INR = int(os.environ.get("PLUS_YEARLY_INR", "1999"))
JWT_SECRET = os.environ.get("JWT_SECRET", "glamgenius-dev-secret-change-me")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_FALLBACK_MODELS = [
    m.strip()
    for m in os.environ.get(
        "GEMINI_FALLBACK_MODELS",
        "gemini-2.5-flash,gemini-2.5-pro",
    ).split(",")
    if m.strip()
]

JWT_ALGORITHM = "HS256"
JWT_EXPIRY_DAYS = 30

# Brute-force protection on login. Counted per email and per source address,
# so one attacker cannot grind through passwords for an account, and one
# address cannot grind through many accounts.
MAX_LOGIN_ATTEMPTS = int(os.environ.get("MAX_LOGIN_ATTEMPTS", "5"))
LOGIN_LOCKOUT_MINUTES = int(os.environ.get("LOGIN_LOCKOUT_MINUTES", "15"))

# The signed-out teaser calls Gemini, which costs money, so it is capped per
# source address. How much of the result a signed-out person gets to see.
MAX_PREVIEWS_PER_WINDOW = int(os.environ.get("MAX_PREVIEWS_PER_WINDOW", "3"))
PREVIEW_WINDOW_MINUTES = int(os.environ.get("PREVIEW_WINDOW_MINUTES", "60"))
PREVIEW_COLOR_COUNT = int(os.environ.get("PREVIEW_COLOR_COUNT", "3"))

# Every route that calls Google's AI costs money per request. This is a speed
# limit against runaway or automated use — separate from the monthly free-scan
# allowance, and it applies to paying users too.
AI_REQUESTS_PER_HOUR = int(os.environ.get("AI_REQUESTS_PER_HOUR", "10"))
# Plus is sold as "unlimited checks". That means no monthly cap, not no burst
# protection — but a paying user should not hit the same ceiling as a free one.
AI_REQUESTS_PER_HOUR_PLUS = int(os.environ.get("AI_REQUESTS_PER_HOUR_PLUS", "60"))
AI_RATE_WINDOW_MINUTES = int(os.environ.get("AI_RATE_WINDOW_MINUTES", "60"))

# Which websites may call this API from a browser. Defaults to local
# development only, so a fresh deployment is closed until it is told otherwise.
DEV_ORIGINS = "http://localhost:8081,http://localhost:19006,http://127.0.0.1:8081"
_allowed_origins_raw = os.environ.get("ALLOWED_ORIGINS", DEV_ORIGINS)
ALLOWED_ORIGINS = [o.strip().rstrip("/") for o in _allowed_origins_raw.split(",") if o.strip()]
# True when nobody has configured this, which is fine locally and a problem
# once deployed — a live website would silently fail to load any data.
ALLOWED_ORIGINS_IS_DEFAULT = "ALLOWED_ORIGINS" not in os.environ
