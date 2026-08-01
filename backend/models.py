"""Pydantic request/response models."""
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import uuid
from datetime import datetime

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
    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None
    body_type: Optional[str] = None  # slim | average | athletic | curvy | plus | prefer_not
    style_vibe: Optional[str] = None  # natural | polished | festive | classic | trendy
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
    invite_code: Optional[str] = None  # which invite was used at signup
    scans_used_this_month: int = 0
    scan_month_key: str = ""  # YYYY-MM
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class UserProfileCreate(BaseModel):
    name: str
    email: str = ""
    phone: str = ""
    password: str = ""
    invite_code: str = ""
    age: Optional[int] = None
    city: Optional[str] = None
    diet: Optional[str] = None
    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None
    body_type: Optional[str] = None
    style_vibe: Optional[str] = None


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
    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None
    body_type: Optional[str] = None
    style_vibe: Optional[str] = None
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


class ScanPreviewRequest(BaseModel):
    """Signed-out teaser. No user fields — nothing about this is saved."""
    image_base64: str
    scan_type: str = "face"  # face | hair | hands | full
    # Required while invite-only — stops open internet from burning Gemini spend.
    invite_code: str = ""


class InviteCreateRequest(BaseModel):
    code: Optional[str] = None  # auto-generated when omitted
    label: str = ""
    max_uses: int = 1
    active: bool = True


class ScanAnalysisRequest(BaseModel):
    # No user_id. Scanning always requires a login token, and the caller's
    # identity comes from that token, so there is nothing to send here.
    image_base64: str
    scan_type: str = "face"  # face | hair | hands | full
    city: Optional[str] = None
    diet: Optional[str] = None
    budget_range: Optional[str] = None
    occasion: Optional[str] = None
    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None
    body_type: Optional[str] = None
    style_vibe: Optional[str] = None


class QuizAnswer(BaseModel):
    question_id: str
    answer: str


class QuizSubmission(BaseModel):
    # Ignored — the caller's identity comes from their token.
    user_id: Optional[str] = None
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
    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None
    body_type: Optional[str] = None
    style_vibe: Optional[str] = None
    follow_trends: bool = True


class StylePlan(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: Optional[str] = None
    occasion: Optional[str] = None
    mood: Optional[str] = None
    plan: Dict[str, Any] = {}
    created_at: datetime = Field(default_factory=datetime.utcnow)


class SubscriptionOrderRequest(BaseModel):
    # Ignored — the caller's identity comes from their token.
    user_id: Optional[str] = None
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
