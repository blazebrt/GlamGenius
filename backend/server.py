"""
GlamGenius API — Personal Stylist & Skin/Hair Wellness Coach (India)
"""
from fastapi import FastAPI, APIRouter, HTTPException
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
import json
import hashlib
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import uuid
from datetime import datetime, timedelta
import base64
import asyncio

try:
    from google import genai as google_genai
    from google.genai import types as google_genai_types
    HAS_GOOGLE_GENAI = True
except ImportError:
    google_genai = None  # type: ignore
    google_genai_types = None  # type: ignore
    HAS_GOOGLE_GENAI = False

# Optional legacy Emergent wrapper (not required when GEMINI_API_KEY is set)
try:
    from emergentintegrations.llm.chat import LlmChat, UserMessage, FileContentWithMimeType
    HAS_EMERGENT = True
except ImportError:
    LlmChat = None  # type: ignore
    UserMessage = None  # type: ignore
    FileContentWithMimeType = None  # type: ignore
    HAS_EMERGENT = False

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
db_name = os.environ.get("DB_NAME", "glamgenius")
client = AsyncIOMotorClient(mongo_url)
db = client[db_name]

# Secrets — never log or return these values
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""
EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY", "")
FREE_SCANS_PER_MONTH = int(os.environ.get("FREE_SCANS_PER_MONTH", "2"))
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

_gemini_client = None


def _get_gemini_client():
    """Lazy Gemini client. Key stays in process memory from env only."""
    global _gemini_client
    if not GEMINI_API_KEY or not HAS_GOOGLE_GENAI:
        return None
    if _gemini_client is None:
        _gemini_client = google_genai.Client(api_key=GEMINI_API_KEY)
    return _gemini_client


def _llm_configured() -> bool:
    return bool(GEMINI_API_KEY and HAS_GOOGLE_GENAI) or bool(EMERGENT_LLM_KEY and HAS_EMERGENT)

app = FastAPI(title="GlamGenius — Personal Stylist & Wellness Coach", version="2.0.0")
api_router = APIRouter(prefix="/api")

logger = logging.getLogger(__name__)

# ============== MODELS ==============


class UserProfile(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    email: str = ""
    phone: str = ""
    password_hash: Optional[str] = None
    age: Optional[int] = None
    city: Optional[str] = None
    diet: Optional[str] = None  # veg | egg | non-veg
    budget_range: Optional[str] = None  # budget | mid | comfortable
    hair_type: Optional[str] = None
    skin_type: Optional[str] = None
    face_shape: Optional[str] = None
    skin_tone: Optional[str] = None
    undertone: Optional[str] = None
    skin_concerns: List[str] = []
    hair_concerns: List[str] = []
    preferences: Dict[str, Any] = {}
    plan: str = "free"  # free | plus
    plan_expires_at: Optional[datetime] = None
    scans_used_this_month: int = 0
    scan_month_key: str = ""  # YYYY-MM
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class UserProfileCreate(BaseModel):
    name: str
    email: str = ""
    phone: str = ""
    password: str = ""
    age: Optional[int] = None
    city: Optional[str] = None
    diet: Optional[str] = None


class UserLogin(BaseModel):
    email: str
    password: str


class UserProfileUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    age: Optional[int] = None
    city: Optional[str] = None
    diet: Optional[str] = None
    budget_range: Optional[str] = None
    hair_type: Optional[str] = None
    skin_type: Optional[str] = None
    face_shape: Optional[str] = None
    skin_tone: Optional[str] = None
    undertone: Optional[str] = None
    skin_concerns: Optional[List[str]] = None
    hair_concerns: Optional[List[str]] = None
    preferences: Optional[Dict[str, Any]] = None


class ScanResult(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: Optional[str] = None
    scan_type: str
    image_base64: Optional[str] = None
    analysis: Dict[str, Any] = {}
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ScanAnalysisRequest(BaseModel):
    user_id: Optional[str] = None
    image_base64: str
    scan_type: str = "face"  # face | hair | hands | full
    city: Optional[str] = None
    diet: Optional[str] = None
    budget_range: Optional[str] = None
    occasion: Optional[str] = None


class QuizAnswer(BaseModel):
    question_id: str
    answer: str


class QuizSubmission(BaseModel):
    user_id: str
    answers: List[QuizAnswer]
    occasion: Optional[str] = None
    budget: Optional[str] = None


class StylePlanRequest(BaseModel):
    user_id: Optional[str] = None
    occasion: str = "everyday"
    mood: Optional[str] = None
    budget_range: Optional[str] = "mid"
    diet: Optional[str] = None
    city: Optional[str] = None


class StylePlan(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: Optional[str] = None
    occasion: Optional[str] = None
    mood: Optional[str] = None
    plan: Dict[str, Any] = {}
    created_at: datetime = Field(default_factory=datetime.utcnow)


class SubscriptionOrderRequest(BaseModel):
    user_id: str
    plan: str = "plus_monthly"  # plus_monthly | plus_yearly
    payment_method: str = "upi"  # mock: upi | card | netbanking


class SalonIdea(BaseModel):
    id: str
    name: str
    category: str
    for_goal: str
    description: str
    how_often_idea: str
    suitable_for: List[str] = []


# ============== STATIC DATA (no prices / no booking) ==============

SALON_IDEAS = [
    {
        "id": "1",
        "name": "Hair Spa",
        "category": "Hair",
        "for_goal": "Healthier-looking hair length and shine",
        "description": "Deep conditioning with steam — useful when hair looks dry or rough at the ends.",
        "how_often_idea": "Every 4–6 weeks",
        "suitable_for": ["dry", "damaged", "frizz"],
    },
    {
        "id": "2",
        "name": "Cleanup / Basic Facial",
        "category": "Skin",
        "for_goal": "Fresher-looking face skin",
        "description": "Gentle cleanse and glow pack — a salon idea when skin looks dull or congested.",
        "how_often_idea": "Every 3–4 weeks",
        "suitable_for": ["all skin types"],
    },
    {
        "id": "3",
        "name": "Head Oil Massage (Champi)",
        "category": "Hair",
        "for_goal": "Relaxed scalp and cared-for hair roots",
        "description": "Warm oil massage for scalp comfort — popular across India.",
        "how_often_idea": "Weekly or bi-weekly",
        "suitable_for": ["all hair types"],
    },
    {
        "id": "4",
        "name": "Manicure",
        "category": "Hands & Nails",
        "for_goal": "Healthier-looking hand skin and nails",
        "description": "Nail care, cuticle tidy, and hand moisturise — not a medical treatment.",
        "how_often_idea": "Every 2–3 weeks",
        "suitable_for": ["all"],
    },
    {
        "id": "5",
        "name": "Pedicure",
        "category": "Hands & Nails",
        "for_goal": "Softer-looking feet and neat nails",
        "description": "Foot scrub and nail care for everyday comfort and grooming.",
        "how_often_idea": "Every 3–4 weeks",
        "suitable_for": ["all"],
    },
    {
        "id": "6",
        "name": "Fruit / Vitamin Facial",
        "category": "Skin",
        "for_goal": "Brighter-looking face skin",
        "description": "Gentle vitamin-forward facial idea when skin looks tired or dull.",
        "how_often_idea": "Monthly",
        "suitable_for": ["dull", "normal", "combination"],
    },
    {
        "id": "7",
        "name": "Anti-Tan Facial (salon idea)",
        "category": "Skin",
        "for_goal": "More even-looking tone after sun exposure",
        "description": "Salon idea if face looks tanned or uneven from sun — lifestyle care, not diagnosis.",
        "how_often_idea": "As needed after heavy sun",
        "suitable_for": ["sun-exposed"],
    },
    {
        "id": "8",
        "name": "Keratin / Smoothening consult",
        "category": "Hair",
        "for_goal": "Easier everyday hair manageability",
        "description": "Ask a stylist only if frizz or manageability is a daily struggle — discuss aftercare first.",
        "how_often_idea": "Discuss with stylist (months apart)",
        "suitable_for": ["wavy", "curly", "frizz"],
    },
    {
        "id": "9",
        "name": "Threading / Grooming",
        "category": "Grooming",
        "for_goal": "Neat brow and face framing",
        "description": "Simple grooming visit for a polished everyday look.",
        "how_often_idea": "Every 2–3 weeks",
        "suitable_for": ["all"],
    },
    {
        "id": "10",
        "name": "Blow-dry & Style",
        "category": "Hair",
        "for_goal": "Occasion-ready hair shape",
        "description": "Styling visit for office, festive, or wedding-guest days — pair with your colour palette.",
        "how_often_idea": "For occasions",
        "suitable_for": ["all hair types"],
    },
]

QUIZ_QUESTIONS = [
    {
        "id": "q1",
        "question": "What bothers you most about your hair right now?",
        "options": ["Frizz & dryness", "Thin-looking / low volume", "Breakage & damage", "Oily roots", "No major concern"],
    },
    {
        "id": "q2",
        "question": "How does your face skin usually feel?",
        "options": ["Oily with shine", "Dry or tight", "Combination", "Balanced", "Easily uncomfortable"],
    },
    {
        "id": "q3",
        "question": "What would you like your skin to look more like?",
        "options": ["Fewer pimples", "More even tone", "Less dullness", "Better hydration", "Smoother texture"],
    },
    {
        "id": "q4",
        "question": "Your diet style?",
        "options": ["Vegetarian", "Eggetarian", "Non-vegetarian", "Prefer not to say"],
    },
    {
        "id": "q5",
        "question": "Your everyday style preference?",
        "options": ["Natural & easy", "Polished & neat", "Festive & bold", "Classic Indian wear", "Low maintenance"],
    },
]

COACH_SYSTEM = """You are GlamGenius — an Indian personal stylist and skin & hair wellness coach.
You help people who cannot afford celebrity stylists with practical guidance on:
- healthier-looking skin and hair (visible observations only)
- clothing colours for Indian skin tones and undertones
- label ingredients to look for / go easy on
- food ingredients mapped to common Indian foods
- optional salon visit ideas (never prices, never booking)

STRICT RULES:
1. You are NOT a doctor. Never diagnose disease. Never invent medical conditions.
2. Use everyday language: "pimples", "dryness", "uneven tone", "rough ends" — not clinical disease names.
3. Base every observation strictly on what is VISIBLE. If unclear, lower confidence and say so.
4. Never include prices, cart, payment, or "book now".
5. India-first: fair→deep tones, warm/cool/olive, Indian occasions, desi foods, climate.
6. Suggest 2–4 skin and 2–4 hair care ingredients max, each tied to a visible observation.
7. Always respond in valid JSON only matching the schema requested.
8. Include the disclaimer string in meta.disclaimer every time.
"""

COACH_JSON_SCHEMA = """
Respond in this EXACT JSON format:
{
  "meta": {
    "scan_focus": "face|hair|hands|full",
    "confidence": 0.0,
    "image_quality_notes": "short note",
    "disclaimer": "General wellness and style guidance from a photo — not medical advice."
  },
  "profile": {
    "face_shape": "oval|round|square|heart|oblong|diamond|unclear",
    "skin_type_visible": "oily|dry|combination|normal|unclear",
    "skin_tone": "fair|wheatish|medium|dusky|deep",
    "undertone": "warm|cool|olive|neutral|unclear",
    "hair_type_visible": "straight|wavy|curly|coily|unclear",
    "hair_texture": "fine|medium|coarse|unclear",
    "hair_density_visible": "thin|medium|thick|unclear"
  },
  "wellness_scores": {
    "skin_score": 0,
    "hair_score": 0,
    "overall_score": 0,
    "score_notes": "one short sentence"
  },
  "observations": [
    {
      "area": "face|t_zone|cheeks|under_eyes|hair|scalp|ends|hands|nails",
      "what_i_see": "plain language",
      "level": "mild|moderate|noticeable",
      "why_it_matters": "everyday impact"
    }
  ],
  "style": {
    "best_clothing_colors": [
      { "color": "name", "hex_hint": "#RRGGBB", "why": "reason" }
    ],
    "colors_to_go_easy_on": [
      { "color": "name", "why": "reason" }
    ],
    "wardrobe_ideas_india": [
      {
        "occasion": "office|festive|wedding_guest|casual|interview",
        "outfit_idea": "short outfit idea",
        "why_it_works": "reason"
      }
    ],
    "metal_and_accessories": "gold|silver|both — short reason",
    "fabric_texture_tips": ["tip"]
  },
  "daily_care": {
    "morning": ["step"],
    "evening": ["step"],
    "weekly": ["habit"],
    "climate_note": "India climate tip"
  },
  "care_ingredients": {
    "for_skin": [
      {
        "ingredient": "salicylic acid (BHA)",
        "why": "why it helps healthier-looking skin",
        "best_when_you_see": ["oily T-zone", "pimples"],
        "where_to_use": "cleanser or leave-on on oily areas",
        "how_often_start": "2–3 nights/week",
        "india_label_names": ["salicylic acid", "BHA"],
        "pairing_tips": "moisturise after; SPF in morning",
        "caution": "may dry — patch test"
      }
    ],
    "for_hair": [
      {
        "ingredient": "name",
        "why": "why",
        "best_when_you_see": ["rough ends"],
        "where_to_use": "mask / leave-in",
        "how_often_start": "1× week",
        "india_label_names": ["label names"],
        "pairing_tips": "tip",
        "caution": "caution"
      }
    ],
    "ingredients_to_go_easy_on": [
      { "ingredient": "name", "why": "why", "best_when_you_see": ["sign"] }
    ],
    "simple_shopping_rule": "Prefer short lists with 1–2 hero ingredients."
  },
  "nutrition": {
    "goal": "support healthier-looking skin and hair",
    "ingredients": [
      {
        "ingredient": "iron",
        "why_for_skin_or_hair": "why",
        "indian_foods": [
          { "food": "palak", "how_often": "3–4×/week", "serving_idea": "1 katori sabzi" }
        ]
      }
    ],
    "simple_plate_ideas": ["Breakfast: …", "Lunch: …"],
    "hydration": "practical water tip",
    "diet_fit": "veg|egg|non-veg note"
  },
  "salon_suggestions": [
    {
      "service": "hair spa",
      "for": "healthier-looking hair",
      "why_suggest": "visible dryness",
      "how_often_idea": "every 4–6 weeks",
      "priority": "high|medium|low"
    }
  ],
  "coach_summary": {
    "headline": "one line",
    "top_3_actions_this_week": ["a1", "a2", "a3"],
    "recheck_in_days": 14
  }
}
"""


# ============== HELPERS ==============


def _hash_password(password: str) -> str:
    return hashlib.sha256(f"{JWT_SECRET}:{password}".encode()).hexdigest()


def _month_key(dt: Optional[datetime] = None) -> str:
    d = dt or datetime.utcnow()
    return f"{d.year:04d}-{d.month:02d}"


def _public_user(doc: Dict[str, Any]) -> Dict[str, Any]:
    if not doc:
        return doc
    out = {k: v for k, v in doc.items() if k not in ("_id", "password_hash")}
    return sanitize_user_doc(out)


def sanitize_user_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    if not doc:
        return doc

    def _coerce_str_list(value):
        out = []
        if isinstance(value, list):
            for v in value:
                if isinstance(v, dict):
                    out.append(str(v.get("concern") or v.get("name") or v.get("what_i_see") or "").strip())
                elif v is not None:
                    out.append(str(v))
        elif isinstance(value, str):
            out = [value]
        return [o for o in out if o]

    for key in ("skin_concerns", "hair_concerns"):
        if key in doc:
            doc[key] = _coerce_str_list(doc[key])

    for key in ("skin_type", "hair_type", "face_shape", "budget_range", "skin_tone", "undertone", "diet", "city", "plan"):
        if key in doc and doc[key] is not None and not isinstance(doc[key], str):
            doc[key] = str(doc[key])

    return doc


def _parse_llm_json(response: str) -> Dict[str, Any]:
    clean = (response or "").strip()
    if clean.startswith("```json"):
        clean = clean[7:]
    if clean.startswith("```"):
        clean = clean[3:]
    if clean.endswith("```"):
        clean = clean[:-3]
    return json.loads(clean.strip())


def _fallback_coach(scan_type: str) -> Dict[str, Any]:
    return {
        "meta": {
            "scan_focus": scan_type,
            "confidence": 0.55,
            "image_quality_notes": "Used a gentle default plan because AI analysis was unavailable.",
            "disclaimer": "General wellness and style guidance from a photo — not medical advice.",
        },
        "profile": {
            "face_shape": "unclear",
            "skin_type_visible": "combination",
            "skin_tone": "wheatish",
            "undertone": "warm",
            "hair_type_visible": "wavy",
            "hair_texture": "medium",
            "hair_density_visible": "medium",
        },
        "wellness_scores": {
            "skin_score": 72,
            "hair_score": 70,
            "overall_score": 71,
            "score_notes": "Starting point for everyday skin and hair care — recheck in good light for better accuracy.",
        },
        "observations": [
            {
                "area": "face",
                "what_i_see": "Mild uneven tone and everyday dullness are common; focus on gentle cleansing and SPF.",
                "level": "mild",
                "why_it_matters": "Simple habits improve how skin looks day to day.",
            }
        ],
        "style": {
            "best_clothing_colors": [
                {"color": "deep teal", "hex_hint": "#0F766E", "why": "Works well with warm wheatish tones"},
                {"color": "mustard", "hex_hint": "#E1A95F", "why": "Adds warmth without washing you out"},
                {"color": "maroon", "hex_hint": "#7F1D1D", "why": "Strong festive and office option"},
            ],
            "colors_to_go_easy_on": [
                {"color": "icy pastels", "why": "Can look washed-out on warm medium tones"}
            ],
            "wardrobe_ideas_india": [
                {
                    "occasion": "office",
                    "outfit_idea": "Cotton kurta in deep teal with cream bottom",
                    "why_it_works": "Clean contrast that flatters warm undertones",
                },
                {
                    "occasion": "festive",
                    "outfit_idea": "Maroon saree or suit with gold jewellery",
                    "why_it_works": "Classic Indian festive pairing for warm tones",
                },
            ],
            "metal_and_accessories": "gold — suits warm undertones",
            "fabric_texture_tips": ["Prefer breathable cotton or linen in humid weather"],
        },
        "daily_care": {
            "morning": ["Gentle cleanse", "Light moisturiser", "SPF 30+"],
            "evening": ["Cleanse", "Simple leave-on suited to your skin feel", "Moisturise"],
            "weekly": ["Hair mask once", "Lip and hand balm daily"],
            "climate_note": "In humid cities keep routines light; in dry winters add richer cream.",
        },
        "care_ingredients": {
            "for_skin": [
                {
                    "ingredient": "niacinamide",
                    "why": "Supports more even-looking tone and oil balance",
                    "best_when_you_see": ["uneven tone", "oiliness"],
                    "where_to_use": "serum after cleanse",
                    "how_often_start": "daily",
                    "india_label_names": ["niacinamide", "vitamin B3"],
                    "pairing_tips": "Works under moisturiser and SPF",
                    "caution": "Start low % if skin feels sensitive",
                },
                {
                    "ingredient": "salicylic acid (BHA)",
                    "why": "Helps with oiliness and clogged-looking pores / pimples",
                    "best_when_you_see": ["oily T-zone", "pimples"],
                    "where_to_use": "cleanser or leave-on on oily areas",
                    "how_often_start": "2–3 nights/week",
                    "india_label_names": ["salicylic acid", "BHA"],
                    "pairing_tips": "Moisturise after; SPF in the morning",
                    "caution": "Can dry — skip on broken skin; patch test",
                },
            ],
            "for_hair": [
                {
                    "ingredient": "glycerin / aloe",
                    "why": "Supports softer-looking hair when dry or frizzy",
                    "best_when_you_see": ["frizz", "dry lengths"],
                    "where_to_use": "conditioner or leave-in",
                    "how_often_start": "most washes",
                    "india_label_names": ["glycerin", "aloe vera"],
                    "pairing_tips": "Focus on mid-lengths to ends",
                    "caution": "In very humid weather use lightly",
                }
            ],
            "ingredients_to_go_easy_on": [
                {
                    "ingredient": "heavy face oils if skin looks oily",
                    "why": "May feel heavier on already shiny skin",
                    "best_when_you_see": ["oily shine", "pimples"],
                }
            ],
            "simple_shopping_rule": "On the label, prefer 1–2 hero ingredients that match your check.",
        },
        "nutrition": {
            "goal": "support healthier-looking skin and hair",
            "ingredients": [
                {
                    "ingredient": "vitamin C",
                    "why_for_skin_or_hair": "Supports brighter-looking skin tone over time",
                    "indian_foods": [
                        {"food": "amla", "how_often": "3–4×/week", "serving_idea": "1 fresh or as murabba/pickle side"},
                        {"food": "guava", "how_often": "few times/week", "serving_idea": "1 medium fruit"},
                    ],
                },
                {
                    "ingredient": "protein",
                    "why_for_skin_or_hair": "Supports hair strength from the inside",
                    "indian_foods": [
                        {"food": "dal", "how_often": "daily", "serving_idea": "1–2 katori"},
                        {"food": "paneer / eggs / curd", "how_often": "as per diet", "serving_idea": "1 serving with meals"},
                    ],
                },
            ],
            "simple_plate_ideas": [
                "Breakfast: vegetable poha + curd",
                "Lunch: dal + roti + seasonal sabzi + salad",
            ],
            "hydration": "Aim for about 8 glasses of water across the day",
            "diet_fit": "Adapt proteins to veg / egg / non-veg preference",
        },
        "salon_suggestions": [
            {
                "service": "cleanup / basic facial",
                "for": "fresher-looking face skin",
                "why_suggest": "Helpful when skin looks dull from city dust and routine stress",
                "how_often_idea": "every 3–4 weeks",
                "priority": "medium",
            },
            {
                "service": "hair spa",
                "for": "healthier-looking hair shine",
                "why_suggest": "Useful if lengths feel dry",
                "how_often_idea": "every 4–6 weeks",
                "priority": "medium",
            },
        ],
        "coach_summary": {
            "headline": "Simple daily care, colours that suit you, and Indian foods for skin & hair.",
            "top_3_actions_this_week": [
                "Wear SPF every morning",
                "Add one amla or guava serving most days",
                "Try one outfit in deep teal or mustard",
            ],
            "recheck_in_days": 14,
        },
    }


async def _gemini_generate_text(prompt: str, system: str, image_base64: Optional[str] = None) -> str:
    """Call Gemini securely on the server. Never expose the API key to clients."""
    client = _get_gemini_client()
    if client is None:
        raise RuntimeError("Gemini client not configured")

    parts: List[Any] = []
    if image_base64:
        raw = image_base64
        if "," in raw and raw.strip().startswith("data:"):
            raw = raw.split(",", 1)[1]
        image_bytes = base64.b64decode(raw)
        mime = "image/jpeg"
        if image_bytes.startswith(b"\x89PNG"):
            mime = "image/png"
        elif image_bytes.startswith(b"RIFF"):
            mime = "image/webp"
        parts.append(google_genai_types.Part.from_bytes(data=image_bytes, mime_type=mime))
    parts.append(prompt)

    models_to_try = []
    for m in [GEMINI_MODEL, *GEMINI_FALLBACK_MODELS]:
        if m and m not in models_to_try:
            models_to_try.append(m)

    last_err: Optional[Exception] = None
    for model_name in models_to_try:
        def _run(model=model_name):
            return client.models.generate_content(
                model=model,
                contents=parts,
                config=google_genai_types.GenerateContentConfig(
                    system_instruction=system,
                    temperature=0.4,
                    response_mime_type="application/json",
                ),
            )

        try:
            response = await asyncio.to_thread(_run)
            text = getattr(response, "text", None) or ""
            if not text and getattr(response, "candidates", None):
                try:
                    text = response.candidates[0].content.parts[0].text
                except Exception:
                    text = ""
            if text:
                return text
        except Exception as e:
            last_err = e
            msg = str(e)
            # Try next model on quota / not-found; otherwise fail fast
            if "429" in msg or "RESOURCE_EXHAUSTED" in msg or "404" in msg or "NOT_FOUND" in msg:
                logger.warning(f"Gemini model unavailable ({model_name}): {type(e).__name__}")
                continue
            raise

    if last_err:
        raise last_err
    raise RuntimeError("Gemini returned empty response")


async def analyze_image_with_gemini(
    image_base64: str,
    scan_type: str,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    context = context or {}
    focus_note = {
        "face": "Focus on face skin, tone, undertone, and visible texture. Still give basic hair style tips if hair is visible.",
        "hair": "Focus on hair and scalp-visible areas, shine, dryness, ends. Still estimate skin tone if face is visible for colour advice.",
        "hands": "Focus on hands, nails, and hand skin. Still give general style colour tips if face tone is unknown — say unclear.",
        "full": "Cover face skin, hair if visible, tone, and full coach plan.",
    }.get(scan_type, "Cover what is clearly visible.")

    prompt = f"""Analyse this photo as a personal stylist + skin & hair wellness coach for an Indian customer.
Scan focus: {scan_type}
{focus_note}

User context (may be empty):
- City/climate hint: {context.get('city') or 'not specified'}
- Diet: {context.get('diet') or 'not specified'}
- Budget comfort: {context.get('budget_range') or 'not specified'}
- Occasion interest: {context.get('occasion') or 'everyday'}

{COACH_JSON_SCHEMA}

Set meta.scan_focus to "{scan_type}". Prefer common Indian foods (dal, palak, amla, guava, curd, paneer, eggs if non-veg, flax/alsi, walnuts, coconut).
For oily look / pimples prefer salicylic acid among skin ingredients when appropriate.
No prices. No booking. No disease diagnosis."""

    try:
        # Prefer direct Gemini API key (server-side only)
        if GEMINI_API_KEY and HAS_GOOGLE_GENAI:
            response_text = await _gemini_generate_text(prompt, COACH_SYSTEM, image_base64=image_base64)
            data = _parse_llm_json(response_text)
            if "meta" not in data:
                data["meta"] = {}
            data["meta"].setdefault(
                "disclaimer",
                "General wellness and style guidance from a photo — not medical advice.",
            )
            data["meta"]["scan_focus"] = scan_type
            data["meta"]["provider"] = "gemini"
            return data

        # Optional Emergent fallback
        if EMERGENT_LLM_KEY and HAS_EMERGENT:
            chat = LlmChat(
                api_key=EMERGENT_LLM_KEY,
                session_id=f"scan-{uuid.uuid4()}",
                system_message=COACH_SYSTEM,
            ).with_model("gemini", "gemini-2.5-flash")

            import aiofiles

            temp_path = f"/tmp/scan_{uuid.uuid4()}.jpg"
            async with aiofiles.open(temp_path, "wb") as f:
                await f.write(base64.b64decode(image_base64))

            file_content = FileContentWithMimeType(file_path=temp_path, mime_type="image/jpeg")
            user_message = UserMessage(text=prompt, file_contents=[file_content])
            response = await chat.send_message(user_message)
            if os.path.exists(temp_path):
                os.remove(temp_path)

            data = _parse_llm_json(response)
            if "meta" not in data:
                data["meta"] = {}
            data["meta"].setdefault(
                "disclaimer",
                "General wellness and style guidance from a photo — not medical advice.",
            )
            data["meta"]["scan_focus"] = scan_type
            return data

        return _fallback_coach(scan_type)
    except Exception as e:
        # Never log secrets; only error class/message
        logger.error(f"Coach analysis error: {type(e).__name__}: {e}")
        return _fallback_coach(scan_type)


async def generate_style_plan(user_data: Dict, occasion: str, mood: str = None, budget: str = "mid") -> Dict[str, Any]:
    def _fb():
        fb = _fallback_coach("full")
        return {
            "headline": fb["coach_summary"]["headline"],
            "style": fb["style"],
            "daily_care": fb["daily_care"],
            "care_ingredients": fb["care_ingredients"],
            "nutrition": fb["nutrition"],
            "salon_suggestions": fb["salon_suggestions"],
            "top_3_actions_this_week": fb["coach_summary"]["top_3_actions_this_week"],
            "disclaimer": fb["meta"]["disclaimer"],
        }

    prompt = f"""Create a personal style + wellness plan for an Indian customer (no photo).
Profile: age={user_data.get('age')}, skin_type={user_data.get('skin_type')}, hair_type={user_data.get('hair_type')},
face_shape={user_data.get('face_shape')}, skin_tone={user_data.get('skin_tone')}, undertone={user_data.get('undertone')},
skin_concerns={user_data.get('skin_concerns')}, hair_concerns={user_data.get('hair_concerns')},
diet={user_data.get('diet')}, city={user_data.get('city')}, budget={budget}
Occasion: {occasion}
Mood: {mood or 'fresh and confident'}

Return JSON:
{{
  "headline": "one line",
  "style": {{ same shape as style block in coach schema }},
  "daily_care": {{ morning, evening, weekly, climate_note }},
  "care_ingredients": {{ for_skin, for_hair, ingredients_to_go_easy_on, simple_shopping_rule }},
  "nutrition": {{ goal, ingredients with indian_foods, simple_plate_ideas, hydration, diet_fit }},
  "salon_suggestions": [{{ service, for, why_suggest, how_often_idea, priority }}],
  "top_3_actions_this_week": ["a","b","c"],
  "disclaimer": "General wellness and style guidance — not medical advice."
}}
No prices. No booking. No diagnosis."""

    try:
        if GEMINI_API_KEY and HAS_GOOGLE_GENAI:
            response_text = await _gemini_generate_text(prompt, COACH_SYSTEM)
            return _parse_llm_json(response_text)

        if EMERGENT_LLM_KEY and HAS_EMERGENT:
            chat = LlmChat(
                api_key=EMERGENT_LLM_KEY,
                session_id=f"plan-{uuid.uuid4()}",
                system_message=COACH_SYSTEM,
            ).with_model("gemini", "gemini-2.5-flash")
            response = await chat.send_message(UserMessage(text=prompt))
            return _parse_llm_json(response)

        return _fb()
    except Exception as e:
        logger.error(f"Style plan error: {type(e).__name__}: {e}")
        return _fb()


async def _ensure_scan_quota(user: Dict[str, Any]) -> Dict[str, Any]:
    """Reset monthly counter; raise if free limit hit. Plus users unlimited while active."""
    month = _month_key()
    updates = {}
    if user.get("scan_month_key") != month:
        updates["scan_month_key"] = month
        updates["scans_used_this_month"] = 0
        user["scan_month_key"] = month
        user["scans_used_this_month"] = 0

    plan = user.get("plan", "free")
    expires = user.get("plan_expires_at")
    plus_active = plan == "plus" and (expires is None or expires > datetime.utcnow())
    if isinstance(expires, str):
        try:
            expires_dt = datetime.fromisoformat(expires.replace("Z", ""))
            plus_active = plan == "plus" and expires_dt > datetime.utcnow()
        except Exception:
            plus_active = plan == "plus"

    if not plus_active:
        used = int(user.get("scans_used_this_month") or 0)
        if used >= FREE_SCANS_PER_MONTH:
            raise HTTPException(
                status_code=402,
                detail={
                    "code": "SCAN_LIMIT",
                    "message": f"Free plan includes {FREE_SCANS_PER_MONTH} checks per month. Upgrade to Plus for unlimited checks.",
                    "free_limit": FREE_SCANS_PER_MONTH,
                    "used": used,
                },
            )

    if updates and user.get("id"):
        await db.users.update_one({"id": user["id"]}, {"$set": updates})

    return {"plus_active": plus_active, "month": month}


# ============== ROUTES ==============


@api_router.get("/")
async def root():
    return {
        "message": "GlamGenius — Personal Stylist & Skin/Hair Wellness Coach",
        "version": "2.0.0",
        "market": "India",
    }


@api_router.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "llm_configured": _llm_configured(),
        # Never return key material — boolean only
        "gemini_ready": bool(GEMINI_API_KEY and HAS_GOOGLE_GENAI),
    }


@api_router.get("/config/public")
async def public_config():
    return {
        "free_scans_per_month": FREE_SCANS_PER_MONTH,
        "plus_price_inr": PLUS_PRICE_INR,
        "plus_yearly_inr": PLUS_YEARLY_INR,
        "currency": "INR",
        "tagline": "Skin · Hair · Style",
        "disclaimer": "General wellness and style guidance — not medical advice.",
    }


# ---- Auth / Users ----


@api_router.post("/users")
async def create_user(user: UserProfileCreate):
    if user.email:
        existing = await db.users.find_one({"email": user.email.lower().strip()})
        if existing and user.password:
            raise HTTPException(status_code=400, detail="Email already registered. Please log in.")

    user_obj = UserProfile(
        name=user.name,
        email=(user.email or "").lower().strip(),
        phone=user.phone or "",
        age=user.age,
        city=user.city,
        diet=user.diet,
        password_hash=_hash_password(user.password) if user.password else None,
        scan_month_key=_month_key(),
    )
    doc = user_obj.dict()
    await db.users.insert_one(doc)
    return _public_user(doc)


@api_router.post("/auth/register")
async def register(user: UserProfileCreate):
    if not user.email or not user.password:
        raise HTTPException(status_code=400, detail="Email and password required")
    return await create_user(user)


@api_router.post("/auth/login")
async def login(body: UserLogin):
    user = await db.users.find_one({"email": body.email.lower().strip()})
    if not user or not user.get("password_hash"):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if user["password_hash"] != _hash_password(body.password):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return {"user": _public_user(user), "token": user["id"]}


@api_router.get("/users/{user_id}")
async def get_user(user_id: str):
    user = await db.users.find_one({"id": user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    # refresh plus expiry view
    pub = _public_user(user)
    expires = user.get("plan_expires_at")
    if user.get("plan") == "plus" and expires and expires < datetime.utcnow():
        await db.users.update_one({"id": user_id}, {"$set": {"plan": "free"}})
        pub["plan"] = "free"
    pub["scans_remaining_free"] = max(
        0, FREE_SCANS_PER_MONTH - int(user.get("scans_used_this_month") or 0)
    ) if pub.get("plan") != "plus" else None
    pub["free_scans_per_month"] = FREE_SCANS_PER_MONTH
    return pub


@api_router.put("/users/{user_id}")
async def update_user(user_id: str, update: UserProfileUpdate):
    update_data = {k: v for k, v in update.dict().items() if v is not None}
    update_data["updated_at"] = datetime.utcnow()
    result = await db.users.update_one({"id": user_id}, {"$set": update_data})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="User not found")
    user = await db.users.find_one({"id": user_id})
    return _public_user(user)


# ---- Scan ----


@api_router.post("/scan/analyze")
async def analyze_scan(request: ScanAnalysisRequest):
    user = None
    if request.user_id:
        user = await db.users.find_one({"id": request.user_id})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        await _ensure_scan_quota(user)

    context = {
        "city": request.city or (user or {}).get("city"),
        "diet": request.diet or (user or {}).get("diet"),
        "budget_range": request.budget_range or (user or {}).get("budget_range"),
        "occasion": request.occasion,
    }
    analysis = await analyze_image_with_gemini(request.image_base64, request.scan_type, context)

    scan_result = ScanResult(
        user_id=request.user_id,
        scan_type=request.scan_type,
        image_base64=(request.image_base64[:80] + "...") if request.image_base64 else None,
        analysis=analysis,
    )
    await db.scans.insert_one(scan_result.dict())

    update_data: Dict[str, Any] = {"updated_at": datetime.utcnow()}
    profile = analysis.get("profile") or {}
    if isinstance(profile.get("face_shape"), str) and profile["face_shape"] != "unclear":
        update_data["face_shape"] = profile["face_shape"]
    if isinstance(profile.get("skin_type_visible"), str) and profile["skin_type_visible"] != "unclear":
        update_data["skin_type"] = profile["skin_type_visible"]
    if isinstance(profile.get("hair_type_visible"), str) and profile["hair_type_visible"] != "unclear":
        update_data["hair_type"] = profile["hair_type_visible"]
    if isinstance(profile.get("skin_tone"), str):
        update_data["skin_tone"] = profile["skin_tone"]
    if isinstance(profile.get("undertone"), str):
        update_data["undertone"] = profile["undertone"]

    observations = analysis.get("observations") or []
    skin_obs = [o.get("what_i_see") for o in observations if isinstance(o, dict) and o.get("area") in ("face", "t_zone", "cheeks", "under_eyes", "hands", "nails")]
    hair_obs = [o.get("what_i_see") for o in observations if isinstance(o, dict) and o.get("area") in ("hair", "scalp", "ends")]
    if skin_obs:
        update_data["skin_concerns"] = skin_obs[:5]
    if hair_obs:
        update_data["hair_concerns"] = hair_obs[:5]

    if request.user_id:
        month = _month_key()
        await db.users.update_one(
            {"id": request.user_id},
            {
                "$set": {**update_data, "scan_month_key": month},
                "$inc": {"scans_used_this_month": 1},
            },
        )

    return {
        "scan_id": scan_result.id,
        "analysis": analysis,
        "profile_updated": bool(request.user_id),
    }


@api_router.get("/scan/history/{user_id}")
async def get_scan_history(user_id: str):
    scans = await db.scans.find({"user_id": user_id}).sort("created_at", -1).to_list(50)
    return [
        {
            "id": s["id"],
            "scan_type": s["scan_type"],
            "analysis": s.get("analysis", {}),
            "created_at": s["created_at"],
            "scores": (s.get("analysis") or {}).get("wellness_scores", {}),
        }
        for s in scans
    ]


@api_router.get("/scan/trends/{user_id}")
async def get_scan_trends(user_id: str):
    scans = await db.scans.find({"user_id": user_id}).sort("created_at", 1).to_list(30)
    points = []
    for s in scans:
        scores = (s.get("analysis") or {}).get("wellness_scores") or {}
        points.append(
            {
                "date": s["created_at"],
                "skin_score": scores.get("skin_score"),
                "hair_score": scores.get("hair_score"),
                "overall_score": scores.get("overall_score"),
                "scan_id": s["id"],
            }
        )
    return {"points": points}


# ---- Quiz ----


@api_router.get("/quiz/questions")
async def get_quiz_questions():
    return QUIZ_QUESTIONS


@api_router.post("/quiz/submit")
async def submit_quiz(submission: QuizSubmission):
    answer_map = {a.question_id: a.answer for a in submission.answers}
    update_data: Dict[str, Any] = {"updated_at": datetime.utcnow()}

    hair_map = {
        "Frizz & dryness": "frizz",
        "Thin-looking / low volume": "thinning",
        "Breakage & damage": "damage",
        "Oily roots": "oily_roots",
    }
    if answer_map.get("q1") in hair_map:
        update_data["hair_concerns"] = [hair_map[answer_map["q1"]]]

    skin_map = {
        "Oily with shine": "oily",
        "Dry or tight": "dry",
        "Combination": "combination",
        "Balanced": "normal",
        "Easily uncomfortable": "sensitive",
    }
    if answer_map.get("q2") in skin_map:
        update_data["skin_type"] = skin_map[answer_map["q2"]]

    concern_map = {
        "Fewer pimples": "pimples",
        "More even tone": "uneven_tone",
        "Less dullness": "dullness",
        "Better hydration": "dryness",
        "Smoother texture": "texture",
    }
    if answer_map.get("q3") in concern_map:
        update_data["skin_concerns"] = [concern_map[answer_map["q3"]]]

    diet_map = {
        "Vegetarian": "veg",
        "Eggetarian": "egg",
        "Non-vegetarian": "non-veg",
    }
    if answer_map.get("q4") in diet_map:
        update_data["diet"] = diet_map[answer_map["q4"]]

    if answer_map.get("q5"):
        update_data["preferences"] = {"style_preference": answer_map["q5"]}

    if submission.user_id:
        await db.users.update_one({"id": submission.user_id}, {"$set": update_data}, upsert=True)

    user = await db.users.find_one({"id": submission.user_id}) or {}
    plan = await generate_style_plan(
        user,
        submission.occasion or "everyday",
        None,
        submission.budget or user.get("budget_range") or "mid",
    )
    rec = StylePlan(user_id=submission.user_id, occasion=submission.occasion, plan=plan)
    await db.style_plans.insert_one(rec.dict())
    return {"plan_id": rec.id, "plan": plan, "profile_updated": True}


# ---- Style plans (replaces salon booking recommendations) ----


@api_router.post("/plans/style")
async def create_style_plan(request: StylePlanRequest):
    user = await db.users.find_one({"id": request.user_id}) if request.user_id else {}
    user = user or {}
    if request.diet:
        user["diet"] = request.diet
    if request.city:
        user["city"] = request.city

    plan = await generate_style_plan(
        user,
        request.occasion,
        request.mood,
        request.budget_range or user.get("budget_range") or "mid",
    )
    rec = StylePlan(
        user_id=request.user_id,
        occasion=request.occasion,
        mood=request.mood,
        plan=plan,
    )
    await db.style_plans.insert_one(rec.dict())
    return {"plan_id": rec.id, "plan": plan}


# Back-compat aliases (no pricing fields)
@api_router.post("/recommendations/advice")
async def get_advice(request: StylePlanRequest):
    return await create_style_plan(request)


@api_router.post("/recommendations/generate")
async def generate_recommendations(request: StylePlanRequest):
    return await create_style_plan(request)


@api_router.get("/recommendations/history/{user_id}")
async def get_recommendation_history(user_id: str):
    recs = await db.style_plans.find({"user_id": user_id}).sort("created_at", -1).to_list(20)
    return [
        {
            "id": r["id"],
            "occasion": r.get("occasion"),
            "mood": r.get("mood"),
            "plan": r.get("plan"),
            "created_at": r.get("created_at"),
        }
        for r in recs
    ]


# ---- Salon ideas (browse only — no cart/checkout) ----


@api_router.get("/services")
async def get_services(category: Optional[str] = None):
    if category:
        return [s for s in SALON_IDEAS if s["category"].lower() == category.lower()]
    return SALON_IDEAS


@api_router.get("/services/{service_id}")
async def get_service(service_id: str):
    for service in SALON_IDEAS:
        if service["id"] == service_id:
            return service
    raise HTTPException(status_code=404, detail="Idea not found")


@api_router.get("/salon-ideas")
async def get_salon_ideas(category: Optional[str] = None):
    return await get_services(category)


# ---- Subscription (app Plus — not salon pay) ----


@api_router.post("/subscription/create-order")
async def create_subscription_order(request: SubscriptionOrderRequest):
    user = await db.users.find_one({"id": request.user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if request.plan == "plus_yearly":
        amount = PLUS_YEARLY_INR
        days = 365
        label = "GlamGenius Plus (Yearly)"
    else:
        amount = PLUS_PRICE_INR
        days = 30
        label = "GlamGenius Plus (Monthly)"

    order = {
        "id": f"sub_{uuid.uuid4().hex[:16]}",
        "user_id": request.user_id,
        "plan": request.plan,
        "amount_inr": amount,
        "currency": "INR",
        "label": label,
        "days": days,
        "status": "created",
        "created_at": datetime.utcnow(),
    }
    await db.subscription_orders.insert_one(order)
    return {
        "order_id": order["id"],
        "amount_inr": amount,
        "currency": "INR",
        "label": label,
        "plan": request.plan,
        "mock": True,
        "message": "Demo billing — replace with Razorpay keys for production.",
    }


@api_router.post("/subscription/confirm")
async def confirm_subscription(order_id: str, payment_method: str = "upi"):
    order = await db.subscription_orders.find_one({"id": order_id})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    expires = datetime.utcnow() + timedelta(days=int(order.get("days", 30)))
    await db.subscription_orders.update_one(
        {"id": order_id},
        {
            "$set": {
                "status": "paid",
                "payment_method": payment_method,
                "paid_at": datetime.utcnow(),
            }
        },
    )
    await db.users.update_one(
        {"id": order["user_id"]},
        {
            "$set": {
                "plan": "plus",
                "plan_expires_at": expires,
                "updated_at": datetime.utcnow(),
            }
        },
    )
    return {
        "success": True,
        "plan": "plus",
        "expires_at": expires.isoformat(),
        "message": "Plus activated. Unlimited skin & hair checks unlocked.",
    }


@api_router.get("/subscription/status/{user_id}")
async def subscription_status(user_id: str):
    user = await db.users.find_one({"id": user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    plan = user.get("plan", "free")
    expires = user.get("plan_expires_at")
    active = plan == "plus" and (not expires or expires > datetime.utcnow())
    used = int(user.get("scans_used_this_month") or 0)
    if user.get("scan_month_key") != _month_key():
        used = 0
    return {
        "plan": "plus" if active else "free",
        "expires_at": expires,
        "free_scans_per_month": FREE_SCANS_PER_MONTH,
        "scans_used_this_month": used,
        "scans_remaining": None if active else max(0, FREE_SCANS_PER_MONTH - used),
        "plus_price_inr": PLUS_PRICE_INR,
        "plus_yearly_inr": PLUS_YEARLY_INR,
    }


app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")


@app.on_event("startup")
async def startup_db_client():
    logger.info("Starting GlamGenius Wellness Coach API v2...")
    await db.users.create_index("id", unique=True)
    await db.users.create_index("email")
    await db.scans.create_index("user_id")
    await db.style_plans.create_index("user_id")
    await db.subscription_orders.create_index("user_id")


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
