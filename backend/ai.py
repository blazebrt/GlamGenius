"""Coach prompts, analysis entry points, and AI rate limits.

The Gemini transport used to live here. It now lives in
``app/domains/ai_gateway/providers/gemini.py``, behind a gateway that records
every call and validates every response. This module keeps the prompts — a
prompt belongs with the feature that owns it — and the V1-facing function names,
so no route had to be rewired.
"""
from fastapi import HTTPException
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta

from database import db
from settings import (
    AI_REQUESTS_PER_HOUR,
    AI_REQUESTS_PER_HOUR_PLUS,
    AI_RATE_WINDOW_MINUTES,
)

from app.domains.ai_gateway import gateway
from app.domains.ai_gateway.providers import gemini as _gemini
from app.domains.ai_gateway.schemas import (
    DISCLAIMER,
    SCHEMA_VERSION_COACH,
    SCHEMA_VERSION_STYLE_PLAN,
    CoachAnalysis,
    StylePlan,
)
from app.domains.entitlements.models import FEATURE_SCAN_ANALYZE, FEATURE_STYLE_PLAN

logger = logging.getLogger(__name__)

# Bump when the wording of a prompt changes. Recorded on every ai_runs row, so
# a change in result quality can be traced to the prompt that caused it.
PROMPT_VERSION_COACH = "coach.v2"
PROMPT_VERSION_STYLE_PLAN = "style_plan.v1"

# Re-exported so health.py and anything else importing from `ai` keeps working.
HAS_GOOGLE_GENAI = _gemini.HAS_GOOGLE_GENAI


def _get_gemini_client():
    """Deprecated. The gateway owns the provider client now."""
    return _gemini.get_client()


def _llm_configured() -> bool:
    return _gemini.is_configured()


COACH_SYSTEM = """You are GlamGenius — the fashion stylist, wellness coach, and skin & hair advisor in someone's pocket for India.
These roles are connected: skin/hair appearance informs colours and grooming; body profile + occasion + trends inform clothing; food and care habits support how skin and hair look.

You help people who cannot afford celebrity stylists with practical, affordable guidance on:
1) Fashion styling — colours, silhouettes, fits, fabrics, Indian + fusion outfits for occasions
2) Skin & hair wellness — visible observations only (not medical diagnosis)
3) Label ingredients + Indian foods that support healthier-looking skin/hair
4) Optional salon ideas (never prices, never booking)

Use profile fields when provided: skin tone/undertone, height_cm, weight_kg, body_type, style_vibe, city/climate, diet, budget, occasion, trends interest.

STRICT RULES:
1. You are NOT a doctor. Never diagnose disease. Never invent medical conditions.
2. Use everyday language — not clinical disease names.
3. Base photo observations on what is VISIBLE. If unclear, lower confidence and say so.
4. Never include prices, cart, payment, or "book now".
5. India-first: fair→deep tones, warm/cool/olive, Indian occasions (office, festive, wedding guest, interview), desi foods, climate.
6. Clothing advice must combine skin tone + body profile (height/weight/body_type when given) + occasion + current wearable trends in India (practical, not celebrity-only).
7. Be kind and inclusive about body — suggest flattering fits/silhouettes, never body-shame.
8. Suggest 2–4 skin and 2–4 hair care ingredients max when relevant.
9. Always respond in valid JSON only matching the schema requested.
10. Include meta.disclaimer every time.
"""

COACH_JSON_SCHEMA = """
Respond in this EXACT JSON format:
{
  "meta": {
    "scan_focus": "face|hair|hands|full",
    "confidence": 0.0,
    "image_quality_notes": "short note",
    "disclaimer": "General fashion, wellness and style guidance — not medical advice."
  },
  "profile": {
    "face_shape": "oval|round|square|heart|oblong|diamond|unclear",
    "skin_type_visible": "oily|dry|combination|normal|unclear",
    "skin_tone": "fair|wheatish|medium|dusky|deep",
    "undertone": "warm|cool|olive|neutral|unclear",
    "hair_type_visible": "straight|wavy|curly|coily|unclear",
    "hair_texture": "fine|medium|coarse|unclear",
    "hair_density_visible": "thin|medium|thick|unclear",
    "estimated_build_note": "only if visible and helpful; else unclear — never invent exact height/weight from photo"
  },
  "observations": [
    {
      "area": "face|t_zone|cheeks|under_eyes|hair|scalp|ends|hands|nails",
      "what_i_see": "plain language",
      "level": "mild|moderate|noticeable",
      "why_it_matters": "how it connects to style or everyday care"
    }
  ],
  "fashion": {
    "best_clothing_colors": [
      { "color": "name", "hex_hint": "#RRGGBB", "why": "ties to skin tone/undertone" }
    ],
    "colors_to_go_easy_on": [
      { "color": "name", "why": "reason" }
    ],
    "silhouettes_for_you": [
      { "silhouette": "e.g. straight kurta, A-line dress, structured blazer", "why": "uses height/body_type/occasion" }
    ],
    "fits_and_proportions": [
      "practical fit tip using height/weight/body_type when provided"
    ],
    "wardrobe_ideas_india": [
      {
        "occasion": "office|festive|wedding_guest|casual|interview|date|travel",
        "outfit_idea": "specific outfit with colours + cut",
        "why_it_works": "tone + body + occasion",
        "trend_note": "how it nods to a current India-wearable trend without being costume-y"
      }
    ],
    "current_trends_to_try": [
      { "trend": "name", "how_to_wear_it_for_you": "personalised take", "skip_if": "optional caution" }
    ],
    "metal_and_accessories": "gold|silver|both — short reason",
    "fabric_texture_tips": ["tip for climate + look"]
  },
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
    "headline": "one line connecting fashion + skin/hair wellness",
    "top_3_actions_this_week": ["style action", "care action", "food or habit action"],
    "recheck_in_days": 14
  }
}
"""

def _ai_limit_for(user: Dict[str, Any]) -> int:
    """Paying subscribers get a higher hourly ceiling than free users."""
    expires = user.get("plan_expires_at")
    is_plus = user.get("plan") == "plus" and (not expires or expires > datetime.utcnow())
    return AI_REQUESTS_PER_HOUR_PLUS if is_plus else AI_REQUESTS_PER_HOUR


async def _ai_calls_remaining(user: Dict[str, Any]) -> int:
    """How many AI calls are left in the current window, for showing a warning."""
    since = datetime.utcnow() - timedelta(minutes=AI_RATE_WINDOW_MINUTES)
    used = await db.ai_usage.count_documents(
        {"user_id": user["id"], "created_at": {"$gte": since}}
    )
    return max(0, _ai_limit_for(user) - used)


async def _assert_ai_quota(user: Dict[str, Any]) -> None:
    """Speed limit on the routes that call Google's AI.

    Counted per user over a rolling window. This is deliberately separate from
    the monthly free-scan allowance: that one decides how much someone gets for
    free, this one stops any single account — free or paying — running up an
    unbounded bill in a short burst.
    """
    limit = _ai_limit_for(user)
    since = datetime.utcnow() - timedelta(minutes=AI_RATE_WINDOW_MINUTES)
    used = await db.ai_usage.count_documents(
        {"user_id": user["id"], "created_at": {"$gte": since}}
    )
    if used >= limit:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "AI_RATE_LIMIT",
                "message": (
                    f"You've used {limit} style checks in the last hour. "
                    "Take a short break and try again in a little while — everything "
                    "you've already saved is still here."
                ),
                "retry_after_minutes": AI_RATE_WINDOW_MINUTES,
            },
            headers={"Retry-After": str(AI_RATE_WINDOW_MINUTES * 60)},
        )
    await db.ai_usage.insert_one(
        {"user_id": user["id"], "created_at": datetime.utcnow()}
    )


# ---------------------------------------------------------------------------
# Analysis entry points
#
# These used to fall back to a hardcoded plan whenever Gemini was missing or
# threw an error: the user saw invented facts about their own face, was charged
# a check for it, and those invented facts were written into their profile.
#
# The fallback is deleted. These now raise AnalysisUnavailableError, and a
# failed analysis never touches the caller's allowance.
# ---------------------------------------------------------------------------


async def analyze_image_with_gemini(
    image_base64: str,
    scan_type: str,
    context: Optional[Dict[str, Any]] = None,
    v1_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Analyse a photo, or raise.

    Returns the validated analysis with a ``meta.provenance`` block recording
    which model, prompt and schema produced it.

    Raises:
        AnalysisUnavailableError: provider unavailable, timed out, returned
            unparseable JSON, or returned something that failed validation.
    """
    context = context or {}
    focus_note = {
        "face": "Focus on face skin, tone, undertone, and visible texture. Still give basic hair style tips if hair is visible.",
        "hair": "Focus on hair and scalp-visible areas, shine, dryness, ends. Still estimate skin tone if face is visible for colour advice.",
        "hands": "Focus on hands, nails, and hand skin. Still give general style colour tips if face tone is unknown - say unclear.",
        "full": "Cover face skin, hair if visible, tone, and full coach plan.",
    }.get(scan_type, "Cover what is clearly visible.")

    prompt = f"""Analyse this photo as a pocket fashion stylist + skin & hair wellness coach for an Indian customer.
Scan focus: {scan_type}
{focus_note}

User context (may be empty - use what is provided; do not invent exact height/weight from the photo):
- City/climate hint: {context.get('city') or 'not specified'}
- Diet: {context.get('diet') or 'not specified'}
- Budget comfort: {context.get('budget_range') or 'not specified'}
- Occasion interest: {context.get('occasion') or 'everyday'}
- Height (cm): {context.get('height_cm') or 'not specified'}
- Weight (kg): {context.get('weight_kg') or 'not specified'}
- Body type: {context.get('body_type') or 'not specified'}
- Style vibe: {context.get('style_vibe') or 'not specified'}

{COACH_JSON_SCHEMA}

Fill BOTH "fashion" (primary stylist block with silhouettes, fits, trends) AND "style" (compat colours/outfits).
Set meta.scan_focus to "{scan_type}". Prefer common Indian foods (dal, palak, amla, guava, curd, paneer, eggs if non-veg, flax/alsi, walnuts, coconut).
For oily look / pimples prefer salicylic acid among skin ingredients when appropriate.
No prices. No booking. No disease diagnosis. Be body-inclusive."""

    result = await gateway.run_structured(
        feature=FEATURE_SCAN_ANALYZE,
        prompt=prompt,
        system=COACH_SYSTEM,
        schema=CoachAnalysis,
        prompt_version=PROMPT_VERSION_COACH,
        schema_version=SCHEMA_VERSION_COACH,
        v1_user_id=v1_user_id,
        image_base64=image_base64,
    )

    analysis = result.data.model_dump(mode="json", exclude_none=False)
    meta = analysis.setdefault("meta", {})
    meta["scan_focus"] = scan_type
    meta["provider"] = result.provider
    meta.setdefault("disclaimer", DISCLAIMER)
    # Provenance travels with the result so the app can show where a claim came
    # from, and let the user confirm or reject it.
    meta["provenance"] = result.provenance()
    return analysis


async def generate_style_plan(
    user_data: Dict,
    occasion: str,
    mood: str = None,
    budget: str = "mid",
    v1_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a style plan from the stored profile, or raise.

    Raises:
        AnalysisUnavailableError: same conditions as analyze_image_with_gemini.
    """
    prompt = f"""Create a pocket fashion stylist + wellness plan for an Indian customer (no photo).
Profile:
- age={user_data.get('age')}, skin_type={user_data.get('skin_type')}, hair_type={user_data.get('hair_type')}
- face_shape={user_data.get('face_shape')}, skin_tone={user_data.get('skin_tone')}, undertone={user_data.get('undertone')}
- height_cm={user_data.get('height_cm')}, weight_kg={user_data.get('weight_kg')}, body_type={user_data.get('body_type')}
- style_vibe={user_data.get('style_vibe')}, skin_concerns={user_data.get('skin_concerns')}, hair_concerns={user_data.get('hair_concerns')}
- diet={user_data.get('diet')}, city={user_data.get('city')}, budget={budget}, follow_trends={user_data.get('follow_trends', True)}
Occasion: {occasion}
Mood: {mood or 'fresh and confident'}

Return JSON with:
{{
  "headline": "one line connecting fashion + skin/hair",
  "fashion": {{ same as fashion block in coach schema - colours, silhouettes, fits, outfits, trends }},
  "style": {{ compat colours/outfits block }},
  "daily_care": {{ morning, evening, weekly, climate_note }},
  "care_ingredients": {{ for_skin, for_hair, ingredients_to_go_easy_on, simple_shopping_rule }},
  "nutrition": {{ goal, ingredients with indian_foods, simple_plate_ideas, hydration, diet_fit }},
  "salon_suggestions": [{{ service, for, why_suggest, how_often_idea, priority }}],
  "top_3_actions_this_week": ["style", "care", "food/habit"],
  "disclaimer": "General fashion, wellness and style guidance - not medical advice."
}}
Combine skin tone + body profile + occasion + wearable India trends. No prices. No booking. No diagnosis. Be body-inclusive."""

    result = await gateway.run_structured(
        feature=FEATURE_STYLE_PLAN,
        prompt=prompt,
        system=COACH_SYSTEM,
        schema=StylePlan,
        prompt_version=PROMPT_VERSION_STYLE_PLAN,
        schema_version=SCHEMA_VERSION_STYLE_PLAN,
        v1_user_id=v1_user_id,
    )

    plan = result.data.model_dump(mode="json", exclude_none=False)
    plan["provenance"] = result.provenance()
    return plan
