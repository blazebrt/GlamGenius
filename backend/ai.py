"""Gemini client, coach prompts, analysis, and AI rate limits."""
from fastapi import HTTPException
import logging
import json
import base64
import asyncio
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta

from database import db
from settings import (
    GEMINI_API_KEY,
    GEMINI_MODEL,
    GEMINI_FALLBACK_MODELS,
    AI_REQUESTS_PER_HOUR,
    AI_REQUESTS_PER_HOUR_PLUS,
    AI_RATE_WINDOW_MINUTES,
)

try:
    from google import genai as google_genai
    from google.genai import types as google_genai_types
    HAS_GOOGLE_GENAI = True
except ImportError:
    google_genai = None  # type: ignore
    google_genai_types = None  # type: ignore
    HAS_GOOGLE_GENAI = False

logger = logging.getLogger(__name__)

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
    return bool(GEMINI_API_KEY and HAS_GOOGLE_GENAI)


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
  "wellness_scores": {
    "skin_score": 0,
    "hair_score": 0,
    "style_readiness_score": 0,
    "overall_score": 0,
    "score_notes": "one short sentence"
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
            "style_readiness_score": 74,
            "overall_score": 72,
            "score_notes": "Starting point for everyday skin, hair, and outfit confidence — add height/weight in profile for sharper clothing fits.",
        },
        "observations": [
            {
                "area": "face",
                "what_i_see": "Mild uneven tone and everyday dullness are common; focus on gentle cleansing and SPF.",
                "level": "mild",
                "why_it_matters": "Clearer-looking skin makes your clothing colours read better in photos and real life.",
            }
        ],
        "fashion": {
            "best_clothing_colors": [
                {"color": "deep teal", "hex_hint": "#0F766E", "why": "Works well with warm wheatish tones"},
                {"color": "mustard", "hex_hint": "#E1A95F", "why": "Adds warmth without washing you out"},
                {"color": "maroon", "hex_hint": "#7F1D1D", "why": "Strong festive and office option"},
            ],
            "colors_to_go_easy_on": [
                {"color": "icy pastels", "why": "Can look washed-out on warm medium tones"}
            ],
            "silhouettes_for_you": [
                {"silhouette": "Straight or lightly tapered kurta with clean neckline", "why": "Elongates the frame and keeps focus on face colours"},
                {"silhouette": "Mid-rise trousers or palazzo with defined waist", "why": "Balances proportions for everyday Indian + fusion wear"},
            ],
            "fits_and_proportions": [
                "If you're on the shorter side, keep hemlines cleaner and avoid overwhelming volume on both top and bottom.",
                "If taller, you can carry longer kurtas, long shirts, and vertical colour blocking more easily.",
            ],
            "wardrobe_ideas_india": [
                {
                    "occasion": "office",
                    "outfit_idea": "Deep teal cotton kurta + cream trousers + simple gold studs",
                    "why_it_works": "Tone-flattering colour with a neat professional silhouette",
                    "trend_note": "Quiet-luxury neutrals with one rich accent colour — very wearable in Indian offices",
                },
                {
                    "occasion": "festive",
                    "outfit_idea": "Maroon kurta set or saree with gold jewellery and soft eyes",
                    "why_it_works": "Classic festive pairing for warm undertones",
                    "trend_note": "Jewel tones remain strong for weddings and festivals without chasing costume trends",
                },
            ],
            "current_trends_to_try": [
                {
                    "trend": "Elevated basics + one statement colour",
                    "how_to_wear_it_for_you": "Keep base in cream/beige and add teal or maroon in kurta, dupatta, or bag",
                    "skip_if": "Skip ultra-micro trends that need constant replacement on a tight budget",
                }
            ],
            "metal_and_accessories": "gold — suits warm undertones",
            "fabric_texture_tips": ["Prefer breathable cotton or linen in humid weather", "Matte fabrics if skin looks oily in photos"],
        },
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
            "headline": "Your pocket stylist: colours for your tone, fits for your frame, and simple skin–hair habits.",
            "top_3_actions_this_week": [
                "Wear one outfit in deep teal or mustard",
                "Add your height & weight in Profile for sharper fit tips",
                "SPF every morning + one amla or guava serving most days",
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

    prompt = f"""Analyse this photo as a pocket fashion stylist + skin & hair wellness coach for an Indian customer.
Scan focus: {scan_type}
{focus_note}

User context (may be empty — use what is provided; do not invent exact height/weight from the photo):
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
            "fashion": fb.get("fashion"),
            "style": fb["style"],
            "daily_care": fb["daily_care"],
            "care_ingredients": fb["care_ingredients"],
            "nutrition": fb["nutrition"],
            "salon_suggestions": fb["salon_suggestions"],
            "top_3_actions_this_week": fb["coach_summary"]["top_3_actions_this_week"],
            "disclaimer": fb["meta"]["disclaimer"],
        }

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
  "fashion": {{ same as fashion block in coach schema — colours, silhouettes, fits, outfits, trends }},
  "style": {{ compat colours/outfits block }},
  "daily_care": {{ morning, evening, weekly, climate_note }},
  "care_ingredients": {{ for_skin, for_hair, ingredients_to_go_easy_on, simple_shopping_rule }},
  "nutrition": {{ goal, ingredients with indian_foods, simple_plate_ideas, hydration, diet_fit }},
  "salon_suggestions": [{{ service, for, why_suggest, how_often_idea, priority }}],
  "top_3_actions_this_week": ["style", "care", "food/habit"],
  "disclaimer": "General fashion, wellness and style guidance — not medical advice."
}}
Combine skin tone + body profile + occasion + wearable India trends. No prices. No booking. No diagnosis. Be body-inclusive."""

    try:
        if GEMINI_API_KEY and HAS_GOOGLE_GENAI:
            response_text = await _gemini_generate_text(prompt, COACH_SYSTEM)
            return _parse_llm_json(response_text)

        return _fb()
    except Exception as e:
        logger.error(f"Style plan error: {type(e).__name__}: {e}")
        return _fb()

