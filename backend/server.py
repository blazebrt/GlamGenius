from fastapi import FastAPI, APIRouter, HTTPException
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
import json
import re
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import uuid
from datetime import datetime, timezone
import base64
from contextlib import asynccontextmanager
from emergentintegrations.llm.chat import LlmChat, UserMessage, FileContentWithMimeType


def utcnow() -> datetime:
    return datetime.now(timezone.utc)

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ.get('MONGO_URL')
db_name = os.environ.get('DB_NAME')
if not mongo_url or not db_name:
    raise RuntimeError("MONGO_URL and DB_NAME environment variables are required")
client = AsyncIOMotorClient(mongo_url)
db = client[db_name]

# Emergent LLM Key
EMERGENT_LLM_KEY = os.environ.get('EMERGENT_LLM_KEY', '')
GEMINI_MODEL = "gemini-2.5-flash"

# Configure logging early so helpers can log safely
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")

# ============== MODELS ==============

class UserProfile(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    email: str = ""
    age: Optional[int] = None
    budget_range: Optional[str] = None  # "budget", "standard", "premium", "luxury" or INR ranges
    hair_type: Optional[str] = None  # "straight", "wavy", "curly", "coily"
    skin_type: Optional[str] = None  # "oily", "dry", "combination", "normal", "sensitive"
    face_shape: Optional[str] = None  # "oval", "round", "square", "heart", "oblong"
    skin_concerns: List[str] = Field(default_factory=list)
    hair_concerns: List[str] = Field(default_factory=list)
    preferences: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

class UserProfileCreate(BaseModel):
    name: str
    email: str = ""
    age: Optional[int] = None

class UserProfileUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    age: Optional[int] = None
    budget_range: Optional[str] = None
    hair_type: Optional[str] = None
    skin_type: Optional[str] = None
    face_shape: Optional[str] = None
    skin_concerns: Optional[List[str]] = None
    hair_concerns: Optional[List[str]] = None
    preferences: Optional[Dict[str, Any]] = None

class ScanResult(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: Optional[str] = None
    scan_type: str  # "face", "hair", "scalp", "full"
    image_base64: Optional[str] = None
    analysis: Dict[str, Any] = Field(default_factory=dict)
    recommendations: List[Any] = Field(default_factory=list)  # Can be strings or dicts
    created_at: datetime = Field(default_factory=utcnow)

class ScanAnalysisRequest(BaseModel):
    user_id: Optional[str] = None
    image_base64: str
    scan_type: str = "full"  # "face", "hair", "scalp", "full"

class QuizAnswer(BaseModel):
    question_id: str
    answer: str

class QuizSubmission(BaseModel):
    user_id: str
    answers: List[QuizAnswer]
    occasion: Optional[str] = None
    budget: Optional[str] = None

class Recommendation(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str = "anonymous"
    occasion: Optional[str] = None
    budget: Optional[str] = None
    services: List[Dict[str, Any]] = Field(default_factory=list)
    stylist_level: str = ""
    add_ons: List[str] = Field(default_factory=list)
    expected_outcome: str = ""
    aftercare_tips: List[str] = Field(default_factory=list)
    maintenance_tips: List[str] = Field(default_factory=list)
    upsell_suggestions: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)

class RecommendationRequest(BaseModel):
    user_id: Optional[str] = None
    occasion: str
    budget: str
    mood: Optional[str] = None
    specific_needs: Optional[str] = None
    budget_range: Optional[str] = None  # Alias for budget

class SalonService(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    category: str
    description: str
    price_range: str
    base_price: int = 999
    duration_minutes: int
    suitable_for: List[str] = Field(default_factory=list)
    benefits: List[str] = Field(default_factory=list)

class Visit(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    services: List[str]
    date: datetime = Field(default_factory=utcnow)
    notes: Optional[str] = None
    rating: Optional[int] = None
    feedback: Optional[str] = None
    status: str = "completed"  # booked, completed, cancelled

class VisitCreate(BaseModel):
    user_id: str
    services: List[str]
    notes: Optional[str] = None

class VisitFeedback(BaseModel):
    rating: int = Field(ge=1, le=5)
    feedback: str = ""

# ============== PAYMENT MODELS ==============

class PaymentOrderRequest(BaseModel):
    user_id: Optional[str] = None
    amount: int  # Amount in paise (multiply INR by 100)
    currency: str = "INR"
    items: List[Dict[str, Any]] = Field(default_factory=list)
    
class PaymentVerifyRequest(BaseModel):
    order_id: str
    payment_id: str
    payment_method: str  # 'upi', 'card', 'netbanking', 'cod'
    
class Order(BaseModel):
    id: str = Field(default_factory=lambda: f"order_{uuid.uuid4().hex[:16]}")
    user_id: str = "guest"
    amount: int
    currency: str = "INR"
    status: str = "created"  # created, paid, failed
    items: List[Dict[str, Any]] = Field(default_factory=list)
    payment_id: Optional[str] = None
    payment_method: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

# ============== SALON SERVICES DATA (Indian Market - INR) ==============

SALON_SERVICES = [
    {
        "id": "1",
        "name": "Haircut & Styling",
        "category": "Hair",
        "description": "Professional haircut with styling, includes wash and blow-dry",
        "price_range": "₹499-899",
        "base_price": 699,
        "duration_minutes": 45,
        "suitable_for": ["all hair types"],
        "benefits": ["Fresh look", "Professional styling", "Personalized cut"],
        "value_deal": True
    },
    {
        "id": "2",
        "name": "Gold Facial",
        "category": "Skin",
        "description": "Luxurious gold facial for instant glow and radiance",
        "price_range": "₹799-1299",
        "base_price": 1049,
        "duration_minutes": 60,
        "suitable_for": ["all skin types"],
        "benefits": ["Instant glow", "Anti-aging", "Deep nourishment"],
        "value_deal": True
    },
    {
        "id": "3",
        "name": "Head Massage & Oil Treatment",
        "category": "Hair",
        "description": "Relaxing champi with warm oil therapy for scalp health",
        "price_range": "₹399-699",
        "base_price": 549,
        "duration_minutes": 30,
        "suitable_for": ["all hair types"],
        "benefits": ["Stress relief", "Hair growth", "Scalp nourishment"],
        "value_deal": True
    },
    {
        "id": "4",
        "name": "Bridal Makeup Package",
        "category": "Makeup",
        "description": "Complete dulhan makeup with HD finish, includes trial",
        "price_range": "₹8000-25000",
        "base_price": 16500,
        "duration_minutes": 150,
        "suitable_for": ["all skin types"],
        "benefits": ["Long-lasting 12hrs", "Photo-ready", "Includes trial session"],
        "value_deal": False
    },
    {
        "id": "5",
        "name": "Keratin Treatment",
        "category": "Hair",
        "description": "Brazilian keratin smoothing for frizz-free silky hair",
        "price_range": "₹3500-7000",
        "base_price": 5250,
        "duration_minutes": 180,
        "suitable_for": ["wavy", "curly", "frizzy"],
        "benefits": ["Frizz control 3-4 months", "Shine", "Easy management"],
        "value_deal": False
    },
    {
        "id": "6",
        "name": "Anti-Tan Facial",
        "category": "Skin",
        "description": "De-tan treatment to remove sun damage and brighten skin",
        "price_range": "₹699-1199",
        "base_price": 949,
        "duration_minutes": 50,
        "suitable_for": ["all skin types"],
        "benefits": ["Tan removal", "Brightening", "Even skin tone"],
        "value_deal": True
    },
    {
        "id": "7",
        "name": "Hair Color/Highlights",
        "category": "Hair",
        "description": "Global color or highlights with premium ammonia-free products",
        "price_range": "₹1500-4500",
        "base_price": 3000,
        "duration_minutes": 120,
        "suitable_for": ["all hair types"],
        "benefits": ["Vibrant color", "Low damage", "Long lasting"],
        "value_deal": False
    },
    {
        "id": "8",
        "name": "Party Makeup",
        "category": "Makeup",
        "description": "Glamorous makeup for parties, sangeet and events",
        "price_range": "₹1500-3000",
        "base_price": 2250,
        "duration_minutes": 60,
        "suitable_for": ["all skin types"],
        "benefits": ["Glamorous look", "Photo-ready", "Long lasting"],
        "value_deal": True
    },
    {
        "id": "9",
        "name": "Cleanup Facial",
        "category": "Skin",
        "description": "Basic cleanup with extraction and glow pack",
        "price_range": "₹399-599",
        "base_price": 499,
        "duration_minutes": 30,
        "suitable_for": ["all skin types"],
        "benefits": ["Deep cleansing", "Blackhead removal", "Fresh skin"],
        "value_deal": True
    },
    {
        "id": "10",
        "name": "Hair Spa & Treatment",
        "category": "Hair",
        "description": "Deep conditioning spa with steam for damaged hair repair",
        "price_range": "₹799-1499",
        "base_price": 1149,
        "duration_minutes": 45,
        "suitable_for": ["damaged", "dry", "all hair types"],
        "benefits": ["Deep repair", "Shine restoration", "Frizz control"],
        "value_deal": True
    },
    {
        "id": "11",
        "name": "Blow Dry & Styling",
        "category": "Hair",
        "description": "Professional blow dry with curls or straight styling",
        "price_range": "₹299-599",
        "base_price": 449,
        "duration_minutes": 30,
        "suitable_for": ["all hair types"],
        "benefits": ["Volume", "Shine", "Event-ready"],
        "value_deal": True
    },
    {
        "id": "12",
        "name": "Manicure & Pedicure Combo",
        "category": "Nails",
        "description": "Spa mani-pedi with scrub, massage and polish",
        "price_range": "₹699-1299",
        "base_price": 999,
        "duration_minutes": 75,
        "suitable_for": ["all"],
        "benefits": ["Soft hands & feet", "Relaxation", "Groomed nails"],
        "value_deal": True
    },
    {
        "id": "13",
        "name": "Threading & Waxing Combo",
        "category": "Grooming",
        "description": "Full face threading with arms/legs wax",
        "price_range": "₹499-899",
        "base_price": 699,
        "duration_minutes": 45,
        "suitable_for": ["all"],
        "benefits": ["Smooth skin", "Clean look", "Long lasting"],
        "value_deal": True
    },
    {
        "id": "14",
        "name": "Fruit Facial",
        "category": "Skin",
        "description": "Natural fruit facial with vitamin boost for glowing skin",
        "price_range": "₹599-999",
        "base_price": 799,
        "duration_minutes": 45,
        "suitable_for": ["all skin types"],
        "benefits": ["Natural glow", "Vitamin boost", "Gentle on skin"],
        "value_deal": True
    },
    {
        "id": "15",
        "name": "Smoothening Treatment",
        "category": "Hair",
        "description": "Hair smoothening for straight, manageable hair",
        "price_range": "₹2500-5000",
        "base_price": 3750,
        "duration_minutes": 150,
        "suitable_for": ["wavy", "curly", "frizzy"],
        "benefits": ["Straight hair", "Low maintenance", "6-8 months lasting"],
        "value_deal": False
    }
]

# ============== QUIZ QUESTIONS ==============

QUIZ_QUESTIONS = [
    {
        "id": "q1",
        "question": "What's your primary hair concern?",
        "options": ["Frizz & Dryness", "Thinning & Volume", "Damage & Breakage", "Color Maintenance", "No major concerns"]
    },
    {
        "id": "q2",
        "question": "How would you describe your skin?",
        "options": ["Oily with shine", "Dry and flaky", "Combination", "Normal", "Sensitive"]
    },
    {
        "id": "q3",
        "question": "What's your main skin concern?",
        "options": ["Acne & Breakouts", "Aging & Wrinkles", "Dullness", "Uneven Tone", "Hydration"]
    },
    {
        "id": "q4",
        "question": "How often do you visit a salon?",
        "options": ["Weekly", "Bi-weekly", "Monthly", "Every 2-3 months", "Rarely"]
    },
    {
        "id": "q5",
        "question": "What's your styling preference?",
        "options": ["Natural & Effortless", "Polished & Professional", "Trendy & Bold", "Classic & Elegant", "Low maintenance"]
    }
]

# ============== HELPER FUNCTIONS ==============

def coerce_str_list(value) -> List[str]:
    """Coerce concern entries (which may be dicts) into plain strings."""
    out: List[str] = []
    if isinstance(value, list):
        for v in value:
            if isinstance(v, dict):
                out.append(str(v.get("concern") or v.get("name") or v.get("issue") or "").strip())
            elif v is not None:
                out.append(str(v))
    elif isinstance(value, str) and value.strip():
        out = [value.strip()]
    return [o for o in out if o]


def extract_json_object(text: str) -> Dict[str, Any]:
    """Parse JSON from an LLM response, tolerating markdown fences and prose."""
    clean = (text or "").strip()
    if clean.startswith("```json"):
        clean = clean[7:]
    elif clean.startswith("```"):
        clean = clean[3:]
    if clean.endswith("```"):
        clean = clean[:-3]
    clean = clean.strip()
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", clean)
        if not match:
            raise
        return json.loads(match.group(0))


def normalize_recommendations(recs: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure recommendation payloads expose both backend and frontend field aliases."""
    if not isinstance(recs, dict):
        return recs

    cost = recs.get("total_estimated_cost", recs.get("estimated_total_cost", 2000))
    if isinstance(cost, str):
        digits = re.findall(r"\d+", cost.replace(",", ""))
        cost = int(digits[0]) if digits else 2000
    try:
        cost = int(cost)
    except (TypeError, ValueError):
        cost = 2000

    duration = recs.get("total_duration", recs.get("estimated_duration", 90))
    if isinstance(duration, str):
        digits = re.findall(r"\d+", duration)
        duration = int(digits[0]) if digits else 90
    try:
        duration = int(duration)
    except (TypeError, ValueError):
        duration = 90

    services = recs.get("services") or []
    for service in services:
        if isinstance(service, dict) and "price" in service:
            try:
                service["price"] = int(service["price"])
            except (TypeError, ValueError):
                service["price"] = 999

    recs["total_estimated_cost"] = cost
    recs["estimated_total_cost"] = cost
    recs["total_duration"] = duration
    recs["estimated_duration"] = duration
    if not recs.get("appointment_duration"):
        hours = duration / 60
        if hours >= 1:
            recs["appointment_duration"] = f"{hours:.1f}".rstrip("0").rstrip(".") + " hours"
        else:
            recs["appointment_duration"] = f"{duration} minutes"
    return recs


def mock_scan_analysis(scan_type: str) -> Dict[str, Any]:
    """Fallback analysis matching the expected clinical schema per scan_type."""
    if scan_type == "face":
        return {
            "analysis_status": "partial",
            "message": "Analysis completed with default recommendations",
            "face_shape": "oval",
            "fitzpatrick_type": "Type IV",
            "skin_type": "combination",
            "skin_tone": "wheatish",
            "overall_score": 72,
            "health_scores": {
                "overall_skin_health": 72,
                "hydration_level": 68,
                "elasticity_score": 70,
                "pore_health": 65,
                "pigmentation_evenness": 70,
                "texture_smoothness": 74,
                "radiance_score": 71,
            },
            "skin_concerns": [
                {
                    "concern": "Mild dullness",
                    "severity": "mild",
                    "location": "overall",
                    "clinical_note": "Fallback analysis used; upload a clearer face photo for clinical precision.",
                }
            ],
            "recommended_treatments": [
                {
                    "treatment": "Gold Facial",
                    "reason": "Boosts radiance and hydration",
                    "frequency": "Every 3-4 weeks",
                    "expected_results": "Visible glow and smoother texture after 1 session",
                    "price_range_inr": "₹799 - ₹1299",
                },
                {
                    "treatment": "Cleanup Facial",
                    "reason": "Deep cleansing for combination skin",
                    "frequency": "Monthly",
                    "expected_results": "Clearer pores and fresher look",
                    "price_range_inr": "₹399 - ₹599",
                },
            ],
            "expected_outcomes": [
                {"timeframe": "After 1st session", "improvement": "Fresher, more hydrated appearance"},
                {"timeframe": "2-4 weeks", "improvement": "10-15% improvement in radiance with consistent care"},
                {"timeframe": "2-3 months", "improvement": "More even tone with regular treatments"},
            ],
            "confidence_score": 0.4,
        }
    if scan_type == "hair":
        return {
            "analysis_status": "partial",
            "message": "Analysis completed with default recommendations",
            "hair_type": "2B wavy",
            "hair_texture": "medium",
            "hair_density": "medium",
            "hair_porosity": "medium",
            "hair_condition": "slightly damaged",
            "scalp_condition": "healthy",
            "overall_score": 70,
            "health_scores": {
                "overall_hair_health": 70,
                "scalp_health": 75,
                "shine_level": 65,
                "moisture_retention": 62,
                "strength_elasticity": 68,
                "cuticle_health": 66,
                "root_health": 74,
            },
            "hair_concerns": [
                {
                    "concern": "Light frizz",
                    "severity": "mild",
                    "location": "ends",
                    "clinical_note": "Fallback analysis used; upload a clearer hair photo for clinical precision.",
                }
            ],
            "recommended_treatments": [
                {
                    "treatment": "Hair Spa & Treatment",
                    "reason": "Restores moisture and shine",
                    "duration": "45 min",
                    "frequency": "Every 3-4 weeks",
                    "expected_results": "Softer, shinier hair after 1 session",
                    "price_range_inr": "₹799 - ₹1499",
                },
                {
                    "treatment": "Head Massage & Oil Treatment",
                    "reason": "Supports scalp health and reduces stress",
                    "duration": "30 min",
                    "frequency": "Weekly or bi-weekly",
                    "expected_results": "Improved scalp comfort and manageability",
                    "price_range_inr": "₹399 - ₹699",
                },
            ],
            "expected_outcomes": [
                {"timeframe": "After 1st session", "improvement": "Improved softness and reduced frizz"},
                {"timeframe": "2-4 weeks", "improvement": "10-20% better moisture retention with care"},
                {"timeframe": "2-3 months", "improvement": "Stronger, healthier-looking hair"},
            ],
            "confidence_score": 0.4,
        }
    if scan_type == "scalp":
        return {
            "analysis_status": "partial",
            "message": "Analysis completed with default recommendations",
            "scalp_type": "balanced",
            "scalp_condition": "healthy",
            "overall_score": 74,
            "health_scores": {
                "overall_scalp_health": 74,
                "follicle_density": 72,
                "sebum_balance": 70,
                "hydration_level": 68,
                "circulation_health": 70,
                "microbiome_balance": 72,
            },
            "scalp_concerns": [
                {
                    "concern": "Mild dryness",
                    "severity": "mild",
                    "affected_area": "crown",
                    "clinical_note": "Fallback analysis used.",
                }
            ],
            "recommended_treatments": [
                {
                    "treatment": "Head Massage & Oil Treatment",
                    "mechanism": "Improves circulation and nourishes scalp",
                    "frequency": "Weekly",
                    "duration": "30 min",
                    "price_range_inr": "₹399 - ₹699",
                }
            ],
            "expected_outcomes": [
                {"timeframe": "After 1st session", "improvement": "More comfortable scalp feel"},
                {"timeframe": "2-4 weeks", "improvement": "Better sebum balance"},
                {"timeframe": "2-3 months", "improvement": "Healthier follicle environment"},
            ],
            "confidence_score": 0.4,
        }
    # full
    return {
        "analysis_status": "partial",
        "message": "Analysis completed with default recommendations",
        "face_shape": "oval",
        "skin_type": "combination",
        "skin_tone": "wheatish",
        "overall_score": 71,
        "overall_health_scores": {
            "skin_health": 72,
            "hair_health": 70,
            "overall_wellness_indicator": 71,
        },
        "health_scores": {
            "overall_skin_health": 72,
            "overall_hair_health": 70,
        },
        "skin_concerns": ["Mild dullness"],
        "hair_concerns": ["Light frizz"],
        "recommended_treatments": [
            {
                "treatment": "Gold Facial",
                "reason": "Instant glow",
                "expected_results": "Brighter complexion",
                "price_range_inr": "₹799 - ₹1299",
            },
            {
                "treatment": "Hair Spa & Treatment",
                "reason": "Restore moisture",
                "expected_results": "Softer hair",
                "price_range_inr": "₹799 - ₹1499",
            },
        ],
        "top_recommendations": [
            {
                "service": "Gold Facial",
                "priority": "high",
                "reason": "Boosts radiance",
                "price_range_inr": "₹799 - ₹1299",
            },
            {
                "service": "Hair Spa & Treatment",
                "priority": "medium",
                "reason": "Improves hair health",
                "price_range_inr": "₹799 - ₹1499",
            },
        ],
        "expected_outcomes": [
            {"timeframe": "After 1st session", "improvement": "Fresher skin and softer hair"},
            {"timeframe": "2-4 weeks", "improvement": "Noticeable improvement with consistent care"},
            {"timeframe": "2-3 months", "improvement": "Longer-lasting glow and hair health"},
        ],
        "personalized_tips": ["Stay hydrated", "Use sunscreen daily", "Deep condition weekly"],
        "confidence_score": 0.4,
    }


def default_recommendations() -> Dict[str, Any]:
    """Fallback recommendations using real catalog services and numeric INR costs."""
    by_name = {s["name"]: s for s in SALON_SERVICES}
    haircut = by_name.get("Haircut & Styling", SALON_SERVICES[0])
    facial = by_name.get("Gold Facial", SALON_SERVICES[1])
    return normalize_recommendations({
        "services": [
            {
                "name": haircut["name"],
                "reason": "Clean, polished base for any occasion",
                "expected_result": "Fresh, styled hair",
                "price": haircut.get("base_price", 699),
            },
            {
                "name": facial["name"],
                "reason": "Instant glow suitable for most budgets",
                "expected_result": "Radiant, hydrated skin",
                "price": facial.get("base_price", 1049),
            },
        ],
        "stylist_level": "Senior Stylist",
        "add_ons": ["Deep Conditioning", "Head Massage & Oil Treatment"],
        "expected_outcome": "You'll leave looking refreshed and polished, ready for your occasion.",
        "aftercare_tips": ["Use sulfate-free shampoo", "Apply moisturizer daily", "Drink plenty of water"],
        "maintenance_tips": ["Schedule touch-up in 4-6 weeks", "Use recommended products"],
        "upsell_suggestions": ["Keratin Treatment", "Anti-Tan Facial"],
        "total_estimated_cost": haircut.get("base_price", 699) + facial.get("base_price", 1049),
        "total_duration": haircut.get("duration_minutes", 45) + facial.get("duration_minutes", 60),
        "appointment_duration": "1.5-2 hours",
    })


async def analyze_image_with_gemini(image_base64: str, scan_type: str) -> Dict[str, Any]:
    """Analyze image using Gemini Vision API with medical-grade accuracy"""
    temp_path = f"/tmp/scan_{uuid.uuid4()}.jpg"
    try:
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=f"scan-{uuid.uuid4()}",
            system_message="""You are a highly trained dermatologist and trichologist AI assistant with expertise in:
- Clinical skin analysis and dermatological assessment
- Hair and scalp health evaluation (trichology)
- Indian skin tones and common concerns specific to South Asian skin
- Evidence-based skincare and haircare recommendations

CRITICAL INSTRUCTIONS:
1. Analyze the image with CLINICAL PRECISION - identify specific conditions, not generic descriptions
2. Use proper medical terminology alongside layman explanations
3. Provide SPECIFIC percentage scores (0-100) for each health metric
4. Consider factors like: Fitzpatrick skin type, TEWL indicators, sebum levels, melasma patterns, follicular health
5. Be CONSERVATIVE with scores - most people have some concerns; perfect skin/hair is rare
6. Identify SPECIFIC problem areas with location references (e.g., "T-zone", "hairline", "crown area")
7. ACCURACY RULE: Base every observation strictly on what is VISIBLE in the image. Do NOT invent conditions. If the image is unclear, low-light, or a body part is not visible, lower the confidence_score and say so in clinical_note.
8. Provide REALISTIC, EVIDENCE-BASED outcomes - state expected improvement percentages and realistic timeframes (e.g., "15-20% reduction in 4 weeks").
9. Always respond in valid JSON format with detailed nested objects"""
        ).with_model("gemini", GEMINI_MODEL)
        
        import aiofiles
        
        async with aiofiles.open(temp_path, 'wb') as f:
            await f.write(base64.b64decode(image_base64))
        
        prompt = ""
        if scan_type == "face":
            prompt = """Perform a DETAILED CLINICAL SKIN ANALYSIS of this face image. 

Evaluate with medical precision and provide scores out of 100 for each metric.
Consider Indian skin tones (Fitzpatrick III-V) and common concerns like hyperpigmentation, melasma, and uneven tone.

Respond in this EXACT JSON format:
{
    "face_shape": "oval/round/square/heart/oblong/diamond",
    "fitzpatrick_type": "Type III/IV/V/VI",
    "skin_type": "oily/dry/combination/normal/sensitive",
    "skin_tone": "fair/wheatish/medium/dusky/deep",
    "overall_score": 0-100,
    
    "health_scores": {
        "overall_skin_health": 0-100,
        "hydration_level": 0-100,
        "elasticity_score": 0-100,
        "pore_health": 0-100,
        "pigmentation_evenness": 0-100,
        "texture_smoothness": 0-100,
        "radiance_score": 0-100
    },
    
    "skin_concerns": [
        {
            "concern": "specific condition name (e.g., mild acne, PIH, periorbital darkening)",
            "severity": "mild/moderate/severe",
            "location": "specific area (e.g., forehead, T-zone, under-eyes, cheeks)",
            "clinical_note": "brief medical explanation"
        }
    ],
    
    "detailed_analysis": {
        "forehead": "condition description",
        "t_zone": "condition description",
        "cheeks": "condition description",
        "under_eyes": "condition description",
        "chin_area": "condition description",
        "jawline": "condition description"
    },
    
    "recommended_treatments": [
        {
            "treatment": "specific salon treatment name",
            "reason": "why this helps",
            "frequency": "how often recommended",
            "expected_results": "what to expect",
            "price_range_inr": "₹XXX - ₹XXX"
        }
    ],
    
    "home_care_routine": {
        "morning": ["step 1", "step 2", "step 3"],
        "evening": ["step 1", "step 2", "step 3"],
        "weekly": ["treatment 1", "treatment 2"]
    },
    
    "ingredients_to_look_for": ["ingredient 1 with benefit", "ingredient 2 with benefit"],
    "ingredients_to_avoid": ["ingredient 1 with reason", "ingredient 2 with reason"],
    
    "makeup_suggestions": {
        "foundation_type": "recommendation",
        "concealer_focus_areas": ["area 1", "area 2"],
        "color_palette": "warm/cool/neutral undertone recommendations"
    },
    
    "expected_outcomes": [
        {"timeframe": "After 1st session", "improvement": "specific visible change to expect"},
        {"timeframe": "2-4 weeks", "improvement": "specific measurable improvement with % if possible"},
        {"timeframe": "2-3 months", "improvement": "long-term transformation expected"}
    ],
    
    "red_flags": ["any concerns that need dermatologist attention"],
    "confidence_score": 0.0-1.0
}"""
        elif scan_type == "hair":
            prompt = """Perform a DETAILED TRICHOLOGICAL ANALYSIS of this hair image.

Evaluate hair shaft health, cuticle condition, and overall hair wellness with clinical precision.
Consider common issues in Indian hair like heat damage, hard water effects, and pollution damage.

Respond in this EXACT JSON format:
{
    "hair_type": "1A-4C classification (e.g., 2B wavy, 3A curly)",
    "hair_texture": "fine/medium/coarse",
    "hair_density": "low/medium/high",
    "hair_porosity": "low/medium/high",
    "hair_condition": "healthy/slightly damaged/moderately damaged/severely damaged",
    "scalp_condition": "healthy/oily/dry/flaky/sensitive",
    "overall_score": 0-100,
    
    "health_scores": {
        "overall_hair_health": 0-100,
        "scalp_health": 0-100,
        "shine_level": 0-100,
        "moisture_retention": 0-100,
        "strength_elasticity": 0-100,
        "cuticle_health": 0-100,
        "root_health": 0-100
    },
    
    "hair_concerns": [
        {
            "concern": "specific issue (e.g., mid-shaft splits, cuticle lifting, hygral fatigue)",
            "severity": "mild/moderate/severe",
            "location": "roots/mid-length/ends",
            "clinical_note": "brief explanation"
        }
    ],
    
    "detailed_analysis": {
        "root_area": "condition at roots",
        "mid_shaft": "condition at middle",
        "ends": "condition at ends",
        "overall_pattern": "growth pattern observations"
    },
    
    "recommended_treatments": [
        {
            "treatment": "specific salon treatment",
            "reason": "why recommended",
            "duration": "treatment time",
            "frequency": "how often",
            "expected_results": "what to expect",
            "price_range_inr": "₹XXX - ₹XXX"
        }
    ],
    
    "styling_suggestions": {
        "safe_heat_settings": "temperature recommendations",
        "best_hairstyles": ["style 1", "style 2"],
        "avoid_styles": ["harmful style 1", "harmful style 2"]
    },
    
    "product_recommendations": {
        "shampoo_type": "recommendation with reason",
        "conditioner_focus": "what to look for",
        "leave_in_products": ["product type 1", "product type 2"],
        "weekly_treatments": ["treatment 1", "treatment 2"]
    },
    
    "expected_outcomes": [
        {"timeframe": "After 1st session", "improvement": "specific visible change to expect"},
        {"timeframe": "2-4 weeks", "improvement": "specific measurable improvement with % if possible"},
        {"timeframe": "2-3 months", "improvement": "long-term hair transformation expected"}
    ],
    
    "red_flags": ["any concerns needing trichologist attention"],
    "confidence_score": 0.0-1.0
}"""
        elif scan_type == "scalp":
            prompt = """Perform a DETAILED SCALP HEALTH ANALYSIS of this scalp image.

Evaluate follicular health, sebum production, and scalp ecosystem with clinical precision.
Consider Indian climate factors like humidity, hard water, and pollution effects.

Respond in this EXACT JSON format:
{
    "scalp_type": "oily/dry/balanced/combination",
    "scalp_condition": "healthy/mildly compromised/moderately compromised/severely compromised",
    
    "health_scores": {
        "overall_scalp_health": 0-100,
        "follicle_density": 0-100,
        "sebum_balance": 0-100,
        "hydration_level": 0-100,
        "circulation_health": 0-100,
        "microbiome_balance": 0-100
    },
    
    "scalp_concerns": [
        {
            "concern": "specific issue (e.g., seborrheic dermatitis, follicular inflammation)",
            "severity": "mild/moderate/severe",
            "affected_area": "location on scalp",
            "clinical_note": "explanation"
        }
    ],
    
    "hair_loss_assessment": {
        "visible_thinning": true/false,
        "pattern_type": "diffuse/patterned/localized",
        "severity_stage": "Stage 1-7 (if applicable)",
        "likely_cause": "possible reason"
    },
    
    "recommended_treatments": [
        {
            "treatment": "specific scalp treatment",
            "mechanism": "how it helps",
            "frequency": "how often",
            "duration": "treatment time",
            "price_range_inr": "₹XXX - ₹XXX"
        }
    ],
    
    "scalp_care_routine": {
        "washing_frequency": "recommendation",
        "massage_technique": "specific method",
        "ingredients_needed": ["ingredient 1", "ingredient 2"],
        "ingredients_to_avoid": ["ingredient 1", "ingredient 2"]
    },
    
    "red_flags": ["any concerns needing dermatologist/trichologist attention"],
    "confidence_score": 0.0-1.0
}"""
        else:  # full analysis
            prompt = """Perform a COMPREHENSIVE BEAUTY ANALYSIS of this image.

Provide a complete assessment covering skin, hair, and overall wellness with clinical precision.
Consider Indian skin/hair characteristics and lifestyle factors.

Respond in this EXACT JSON format:
{
    "face_shape": "oval/round/square/heart/oblong/diamond",
    "skin_type": "oily/dry/combination/normal/sensitive",
    "skin_tone": "fair/wheatish/medium/dusky/deep",
    
    "overall_health_scores": {
        "skin_health": 0-100,
        "hair_health": 0-100,
        "overall_wellness_indicator": 0-100
    },
    
    "skin_analysis": {
        "concerns": ["specific concern 1", "specific concern 2"],
        "strengths": ["positive aspect 1", "positive aspect 2"],
        "priority_area": "most important area to address"
    },
    
    "hair_analysis": {
        "hair_type": "type classification",
        "concerns": ["concern 1", "concern 2"],
        "condition_summary": "brief assessment"
    },
    
    "skin_concerns": ["detailed list of skin concerns"],
    "hair_concerns": ["detailed list of hair concerns"],
    
    "overall_assessment": "comprehensive 2-3 sentence assessment",
    
    "top_recommendations": [
        {
            "service": "salon service name",
            "priority": "high/medium/low",
            "reason": "why recommended",
            "price_range_inr": "₹XXX - ₹XXX"
        }
    ],
    
    "personalized_tips": [
        {
            "tip": "specific actionable advice",
            "benefit": "expected benefit",
            "timeline": "when to see results"
        }
    ],
    
    "lifestyle_recommendations": {
        "diet": ["food suggestion 1", "food suggestion 2"],
        "hydration": "water intake recommendation",
        "sleep": "sleep recommendation",
        "stress_management": "stress tip"
    },
    
    "red_flags": ["any medical concerns to address"],
    "confidence_score": 0.0-1.0
}"""
        
        file_content = FileContentWithMimeType(
            file_path=temp_path,
            mime_type="image/jpeg"
        )
        
        user_message = UserMessage(
            text=prompt,
            file_contents=[file_content]
        )
        
        response = await chat.send_message(user_message)
        return extract_json_object(response)
        
    except Exception as e:
        logger.error(f"Gemini analysis error: {str(e)}")
        return mock_scan_analysis(scan_type)
    finally:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass

async def generate_ai_recommendations(user_data: Dict, occasion: str, budget: str, mood: str = None) -> Dict[str, Any]:
    """Generate personalized recommendations using Gemini"""
    try:
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=f"recommend-{uuid.uuid4()}",
            system_message="""You are GlamGenius, a premium salon beauty advisor AI.
            Based on the user's profile, budget, occasion, and mood, recommend the best salon service bundle.
            Optimize for visible results, value for money, and client retention.
            Always respond in valid JSON format.
            Use only service names from the provided catalog. Prices must be numeric INR values."""
        ).with_model("gemini", GEMINI_MODEL)
        
        services_info = "\n".join([
            f"- {s['name']} ({s['category']}): {s['description']} - {s['price_range']} (base ₹{s.get('base_price', 999)}, {s['duration_minutes']} min)"
            for s in SALON_SERVICES
        ])
        
        mood_text = f"Desired Mood/Feeling: {mood}" if mood else ""
        
        prompt_text = f"""Based on this client profile:
- Age: {user_data.get('age', 'Not specified')}
- Skin Type: {user_data.get('skin_type', 'Not specified')}
- Hair Type: {user_data.get('hair_type', 'Not specified')}
- Face Shape: {user_data.get('face_shape', 'Not specified')}
- Skin Concerns: {user_data.get('skin_concerns', [])}
- Hair Concerns: {user_data.get('hair_concerns', [])}

Occasion: {occasion}
{mood_text}
Budget Level: {budget} (budget/500-1500=₹500-1500, standard/1500-3000=₹1500-3000, premium/3000-5000 or 3000-6000=₹3000-6000, luxury/5000+=₹5000+)

Available Services:
{services_info}

Provide personalized recommendations in this JSON format:
{{
    "services": [
        {{
            "name": "service name from catalog",
            "reason": "why this is recommended",
            "expected_result": "what to expect",
            "price": estimated_price_number
        }}
    ],
    "stylist_level": "Junior/Senior/Master Stylist recommendation",
    "add_ons": ["list of complementary add-on services"],
    "expected_outcome": "detailed description of the expected look/result",
    "aftercare_tips": ["list of aftercare recommendations"],
    "maintenance_tips": ["list of maintenance tips for lasting results"],
    "upsell_suggestions": ["future services to consider"],
    "total_estimated_cost": estimated_total_number,
    "total_duration": estimated_minutes_number,
    "appointment_duration": "total estimated time in text"
}}"""
        
        user_message = UserMessage(text=prompt_text)
        response = await chat.send_message(user_message)
        return normalize_recommendations(extract_json_object(response))
        
    except Exception as e:
        logger.error(f"Recommendation generation error: {str(e)}")
        return default_recommendations()

# ============== API ROUTES ==============

@api_router.get("/")
async def root():
    return {"message": "GlamGenius API - Premium Salon Advisor", "version": "1.0"}

@api_router.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": utcnow().isoformat()}

def sanitize_user_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce legacy/malformed fields so UserProfile validation never 500s."""
    if not doc:
        return doc

    clean = dict(doc)
    clean.pop("_id", None)

    for key in ("skin_concerns", "hair_concerns"):
        if key in clean:
            clean[key] = coerce_str_list(clean[key])

    for key in ("skin_type", "hair_type", "face_shape", "budget_range", "name", "email"):
        if key in clean and clean[key] is not None and not isinstance(clean[key], str):
            clean[key] = str(clean[key])

    if "preferences" in clean and not isinstance(clean["preferences"], dict):
        clean["preferences"] = {}

    if "age" in clean and clean["age"] is not None:
        try:
            clean["age"] = int(clean["age"])
        except (TypeError, ValueError):
            clean["age"] = None

    return clean

# User Profile Routes
@api_router.post("/users", response_model=UserProfile)
async def create_user(user: UserProfileCreate):
    user_obj = UserProfile(**user.model_dump())
    await db.users.insert_one(user_obj.model_dump())
    return user_obj

@api_router.get("/users/{user_id}", response_model=UserProfile)
async def get_user(user_id: str):
    user = await db.users.find_one({"id": user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return UserProfile(**sanitize_user_doc(user))

@api_router.put("/users/{user_id}", response_model=UserProfile)
async def update_user(user_id: str, update: UserProfileUpdate):
    update_data = {k: v for k, v in update.model_dump().items() if v is not None}
    update_data["updated_at"] = utcnow()
    
    result = await db.users.update_one(
        {"id": user_id},
        {"$set": update_data}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="User not found")
    
    user = await db.users.find_one({"id": user_id})
    return UserProfile(**sanitize_user_doc(user))

# Scan Analysis Routes
@api_router.post("/scan/analyze")
async def analyze_scan(request: ScanAnalysisRequest):
    """Analyze uploaded image and return beauty assessment"""
    analysis = await analyze_image_with_gemini(request.image_base64, request.scan_type)
    
    # Save scan result
    scan_result = ScanResult(
        user_id=request.user_id,
        scan_type=request.scan_type,
        image_base64=request.image_base64[:100] + "...",  # Store truncated for reference
        analysis=analysis,
        recommendations=analysis.get("top_recommendations", analysis.get("recommended_treatments", []))
    )
    
    await db.scans.insert_one(scan_result.model_dump())
    
    update_data = {}
    if "face_shape" in analysis and isinstance(analysis["face_shape"], str):
        update_data["face_shape"] = analysis["face_shape"]
    if "skin_type" in analysis and isinstance(analysis["skin_type"], str):
        update_data["skin_type"] = analysis["skin_type"]
    if "hair_type" in analysis and isinstance(analysis["hair_type"], str):
        update_data["hair_type"] = analysis["hair_type"]
    if "skin_concerns" in analysis:
        update_data["skin_concerns"] = coerce_str_list(analysis["skin_concerns"])
    if "hair_concerns" in analysis:
        update_data["hair_concerns"] = coerce_str_list(analysis["hair_concerns"])
    
    profile_updated = False
    if update_data and request.user_id and request.user_id not in ("", "anonymous"):
        update_data["updated_at"] = utcnow()
        result = await db.users.update_one(
            {"id": request.user_id},
            {"$set": update_data},
            upsert=False,
        )
        profile_updated = result.matched_count > 0
    
    return {
        "scan_id": scan_result.id,
        "analysis": analysis,
        "profile_updated": profile_updated
    }

@api_router.get("/scan/history/{user_id}")
async def get_scan_history(user_id: str):
    """Get scan history for a user"""
    scans = await db.scans.find({"user_id": user_id}).sort("created_at", -1).to_list(50)
    return [{"id": s["id"], "scan_type": s["scan_type"], "analysis": s["analysis"], "created_at": s["created_at"]} for s in scans]

# Quiz Routes
@api_router.get("/quiz/questions")
async def get_quiz_questions():
    """Get quiz questions"""
    return QUIZ_QUESTIONS

@api_router.post("/quiz/submit")
async def submit_quiz(submission: QuizSubmission):
    """Submit quiz and get recommendations"""
    # Process answers to update user profile
    answer_map = {a.question_id: a.answer for a in submission.answers}
    
    update_data = {"updated_at": utcnow()}
    
    # Map quiz answers to profile fields
    if "q1" in answer_map:  # Hair concern
        hair_concern_map = {
            "Frizz & Dryness": "frizz",
            "Thinning & Volume": "thinning",
            "Damage & Breakage": "damage",
            "Color Maintenance": "color_treated"
        }
        if answer_map["q1"] in hair_concern_map:
            update_data["hair_concerns"] = [hair_concern_map[answer_map["q1"]]]
    
    if "q2" in answer_map:  # Skin type
        skin_map = {
            "Oily with shine": "oily",
            "Dry and flaky": "dry",
            "Combination": "combination",
            "Normal": "normal",
            "Sensitive": "sensitive"
        }
        if answer_map["q2"] in skin_map:
            update_data["skin_type"] = skin_map[answer_map["q2"]]
    
    if "q3" in answer_map:  # Skin concern
        skin_concern_map = {
            "Acne & Breakouts": "acne",
            "Aging & Wrinkles": "aging",
            "Dullness": "dullness",
            "Uneven Tone": "uneven_tone",
            "Hydration": "dehydration"
        }
        if answer_map["q3"] in skin_concern_map:
            update_data["skin_concerns"] = [skin_concern_map[answer_map["q3"]]]

    if submission.budget:
        update_data["budget_range"] = submission.budget
    
    profile_updated = False
    # Update user profile with full defaults on upsert
    if submission.user_id:
        existing = await db.users.find_one({"id": submission.user_id})
        if existing:
            result = await db.users.update_one(
                {"id": submission.user_id},
                {"$set": update_data},
            )
            profile_updated = result.matched_count > 0
        else:
            profile = UserProfile(
                id=submission.user_id,
                name="Guest User",
                skin_type=update_data.get("skin_type"),
                skin_concerns=update_data.get("skin_concerns", []),
                hair_concerns=update_data.get("hair_concerns", []),
                budget_range=update_data.get("budget_range"),
            )
            await db.users.insert_one(profile.model_dump())
            profile_updated = True
    
    # Get user data for recommendations
    user = await db.users.find_one({"id": submission.user_id}) if submission.user_id else None
    user_data = sanitize_user_doc(user) if user else {}
    
    # Generate recommendations
    recommendations = await generate_ai_recommendations(
        user_data,
        submission.occasion or "everyday",
        submission.budget or "standard"
    )
    
    # Save recommendation
    rec = Recommendation(
        user_id=submission.user_id or "anonymous",
        occasion=submission.occasion,
        budget=submission.budget,
        services=recommendations.get("services", []),
        stylist_level=recommendations.get("stylist_level", ""),
        add_ons=recommendations.get("add_ons", []),
        expected_outcome=recommendations.get("expected_outcome", ""),
        aftercare_tips=recommendations.get("aftercare_tips", []),
        maintenance_tips=recommendations.get("maintenance_tips", []),
        upsell_suggestions=recommendations.get("upsell_suggestions", [])
    )
    
    await db.recommendations.insert_one(rec.model_dump())
    
    return {
        "recommendation_id": rec.id,
        "recommendations": recommendations,
        "profile_updated": profile_updated
    }

# Recommendations Routes
@api_router.post("/recommendations/advice")
async def get_advice(request: RecommendationRequest):
    """Get AI advice based on mood, occasion and budget"""
    user = await db.users.find_one({"id": request.user_id}) if request.user_id else None
    user_data = sanitize_user_doc(user) if user else {}
    
    # Use budget_range if budget not provided
    budget = request.budget or request.budget_range or "1500-3000"
    
    recommendations = await generate_ai_recommendations(
        user_data,
        request.occasion,
        budget,
        request.mood
    )
    
    return {
        "recommendations": recommendations,
        "estimated_total_cost": recommendations.get("estimated_total_cost", 2000),
        "estimated_duration": recommendations.get("estimated_duration", 90)
    }

@api_router.post("/recommendations/generate")
async def generate_recommendations(request: RecommendationRequest):
    """Generate personalized recommendations"""
    user = await db.users.find_one({"id": request.user_id}) if request.user_id else None
    user_data = sanitize_user_doc(user) if user else {}
    
    recommendations = await generate_ai_recommendations(
        user_data,
        request.occasion,
        request.budget or request.budget_range or "standard",
        request.mood,
    )
    
    # Save recommendation
    rec = Recommendation(
        user_id=request.user_id or "anonymous",
        occasion=request.occasion,
        budget=request.budget,
        services=recommendations.get("services", []),
        stylist_level=recommendations.get("stylist_level", ""),
        add_ons=recommendations.get("add_ons", []),
        expected_outcome=recommendations.get("expected_outcome", ""),
        aftercare_tips=recommendations.get("aftercare_tips", []),
        maintenance_tips=recommendations.get("maintenance_tips", []),
        upsell_suggestions=recommendations.get("upsell_suggestions", [])
    )
    
    await db.recommendations.insert_one(rec.model_dump())
    
    return {
        "recommendation_id": rec.id,
        "recommendations": recommendations,
        "estimated_total_cost": recommendations.get("estimated_total_cost", 2000),
        "estimated_duration": recommendations.get("estimated_duration", 90),
    }

@api_router.get("/recommendations/history/{user_id}")
async def get_recommendation_history(user_id: str):
    """Get recommendation history for a user"""
    recs = await db.recommendations.find({"user_id": user_id}).sort("created_at", -1).to_list(20)
    return [Recommendation(**{k: v for k, v in r.items() if k != "_id"}) for r in recs]

# Services Routes
@api_router.get("/services")
async def get_services(category: Optional[str] = None):
    """Get all salon services"""
    if category:
        return [s for s in SALON_SERVICES if s["category"].lower() == category.lower()]
    return SALON_SERVICES

@api_router.get("/services/{service_id}")
async def get_service(service_id: str):
    """Get specific service"""
    for service in SALON_SERVICES:
        if service["id"] == service_id:
            return service
    raise HTTPException(status_code=404, detail="Service not found")

# Visit History Routes
@api_router.post("/visits", response_model=Visit)
async def create_visit(visit: VisitCreate):
    """Log a salon visit"""
    visit_obj = Visit(**visit.model_dump(), status="completed")
    await db.visits.insert_one(visit_obj.model_dump())
    return visit_obj

@api_router.get("/visits/{user_id}")
async def get_visits(user_id: str):
    """Get visit history for a user"""
    visits = await db.visits.find({"user_id": user_id}).sort("date", -1).to_list(50)
    cleaned = []
    for v in visits:
        v = {k: val for k, val in v.items() if k != "_id"}
        v.setdefault("status", "completed")
        cleaned.append(Visit(**v))
    return cleaned

@api_router.put("/visits/{visit_id}/feedback")
async def add_visit_feedback(visit_id: str, body: VisitFeedback):
    """Add feedback to a visit"""
    result = await db.visits.update_one(
        {"id": visit_id},
        {"$set": {"rating": body.rating, "feedback": body.feedback}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Visit not found")
    return {"status": "success"}

# ============== PAYMENT ROUTES (MOCK - Replace with Razorpay later) ==============

@api_router.post("/payments/create-order")
async def create_payment_order(request: PaymentOrderRequest):
    """Create a payment order (Mock - simulates Razorpay order creation)"""
    if request.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be greater than 0")

    order = Order(
        user_id=request.user_id or "guest",
        amount=request.amount,
        currency=request.currency,
        items=request.items,
        status="created"
    )
    
    await db.orders.insert_one(order.model_dump())
    
    # Return Razorpay-like response structure
    return {
        "id": order.id,
        "amount": order.amount,
        "currency": order.currency,
        "status": order.status,
        "created_at": order.created_at.isoformat(),
        # Mock key for frontend (would be real RAZORPAY_KEY_ID in production)
        "key": "rzp_mock_glamgenius"
    }

@api_router.post("/payments/verify")
async def verify_payment(request: PaymentVerifyRequest):
    """Verify payment (Mock - simulates Razorpay payment verification)"""
    # Find the order
    order = await db.orders.find_one({"id": request.order_id})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    # Idempotent: already paid → return existing booking without duplicating visits
    if order.get("status") == "paid":
        existing_visit = await db.visits.find_one({
            "user_id": order["user_id"],
            "notes": {"$regex": re.escape(request.order_id)},
        })
        booking_id = existing_visit["id"] if existing_visit else order.get("booking_id") or order.get("payment_id")
        return {
            "success": True,
            "payment_id": order.get("payment_id") or request.payment_id,
            "order_id": request.order_id,
            "booking_id": booking_id,
            "message": "Payment already verified. Your appointment is booked.",
        }
    
    payment_id = request.payment_id or f"pay_{uuid.uuid4().hex[:16]}"
    
    # Create a visit record for the booking
    visit = Visit(
        user_id=order.get("user_id") or "guest",
        services=[item.get("name", "Service") for item in order.get("items", [])],
        notes=f"Booked via app. Order: {request.order_id}. Payment: {request.payment_method.upper()}",
        status="booked",
    )
    await db.visits.insert_one(visit.model_dump())

    # Update order status
    await db.orders.update_one(
        {"id": request.order_id},
        {"$set": {
            "status": "paid",
            "payment_id": payment_id,
            "payment_method": request.payment_method,
            "booking_id": visit.id,
            "updated_at": utcnow()
        }}
    )
    
    return {
        "success": True,
        "payment_id": payment_id,
        "order_id": request.order_id,
        "booking_id": visit.id,
        "message": "Payment successful! Your appointment has been booked."
    }

@api_router.get("/payments/order/{order_id}")
async def get_order(order_id: str):
    """Get order details"""
    order = await db.orders.find_one({"id": order_id})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    order.pop("_id", None)
    return Order(**{k: v for k, v in order.items() if k in Order.model_fields})

@api_router.get("/payments/history/{user_id}")
async def get_payment_history(user_id: str):
    """Get payment history for a user"""
    orders = await db.orders.find({"user_id": user_id}).sort("created_at", -1).to_list(50)
    cleaned = []
    for o in orders:
        o.pop("_id", None)
        cleaned.append(Order(**{k: v for k, v in o.items() if k in Order.model_fields}))
    return cleaned


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting GlamGenius API...")
    if not EMERGENT_LLM_KEY:
        logger.warning("EMERGENT_LLM_KEY is empty — AI endpoints will use fallback mocks")
    await db.users.create_index("id", unique=True)
    await db.scans.create_index("user_id")
    await db.recommendations.create_index("user_id")
    await db.visits.create_index("user_id")
    await db.orders.create_index("id", unique=True)
    yield
    client.close()


# Create the main app
app = FastAPI(title="GlamGenius - Premium Salon Advisor", lifespan=lifespan)

# Include the router in the main app
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=False,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
