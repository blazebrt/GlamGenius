"""Auth and user profile routes."""
from fastapi import APIRouter, HTTPException, Depends, Request
from typing import Dict, Any
from datetime import datetime

from database import db
from models import UserProfile, UserProfileCreate, UserLogin, UserProfileUpdate
from security import (
    get_current_user,
    _hash_password,
    _verify_password,
    _create_access_token,
    _login_keys,
    _assert_not_locked_out,
    _record_failed_login,
    _clear_failed_logins,
    _require_same_user,
    _month_key,
    _public_user,
    _user_view,
)
from ai import _ai_calls_remaining, _ai_limit_for

router = APIRouter()

@router.post("/users")
async def create_user(user: UserProfileCreate):
    # Anonymous guest accounts are gone. An account with no password can never
    # be proven to belong to anyone, and signed-out people get the teaser
    # instead (POST /scan/preview).
    if not user.email or not user.password:
        raise HTTPException(status_code=400, detail="Email and password required")

    existing = await db.users.find_one({"email": user.email.lower().strip()})
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered. Please log in.")

    user_obj = UserProfile(
        name=user.name,
        email=(user.email or "").lower().strip(),
        phone=user.phone or "",
        age=user.age,
        city=user.city,
        diet=user.diet,
        password_hash=_hash_password(user.password),
        scan_month_key=_month_key(),
    )
    doc = user_obj.dict()
    await db.users.insert_one(doc)
    pub = _public_user(doc)
    # Creating an account signs you in, so the caller gets a usable token.
    pub["token"] = _create_access_token(doc["id"])
    return pub


@router.post("/auth/register")
async def register(user: UserProfileCreate):
    if not user.email or not user.password:
        raise HTTPException(status_code=400, detail="Email and password required")
    created = await create_user(user)
    return {"user": {k: v for k, v in created.items() if k != "token"}, "token": created["token"]}


@router.post("/auth/login")
async def login(body: UserLogin, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    keys = _login_keys(body.email, client_ip)

    # Stop password guessing before we check anything.
    await _assert_not_locked_out(keys)

    user = await db.users.find_one({"email": body.email.lower().strip()})
    if not user or not user.get("password_hash"):
        await _record_failed_login(keys)
        raise HTTPException(status_code=401, detail="Invalid email or password")

    valid, needs_rehash = _verify_password(body.password, user["password_hash"])
    if not valid:
        await _record_failed_login(keys)
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # A correct password wipes the slate so a locked-out genuine user recovers.
    await _clear_failed_logins(keys)

    if needs_rehash:
        # Pre-bcrypt account: upgrade the stored hash now, without bothering the user.
        await db.users.update_one(
            {"id": user["id"]},
            {"$set": {"password_hash": _hash_password(body.password), "updated_at": datetime.utcnow()}},
        )

    return {"user": _public_user(user), "token": _create_access_token(user["id"])}



# NOTE: /users/me must be declared before /users/{user_id}, otherwise "me"
# would be captured as a user id by the parameterised route.
@router.get("/users/me")
async def get_me(user: Dict[str, Any] = Depends(get_current_user)):
    pub = _user_view(user)
    # So the app can warn before someone hits the hourly wall, rather than
    # only telling them once they have.
    pub["ai_calls_remaining_this_hour"] = await _ai_calls_remaining(user)
    pub["ai_calls_per_hour"] = _ai_limit_for(user)
    expires = user.get("plan_expires_at")
    if user.get("plan") == "plus" and expires and expires < datetime.utcnow():
        await db.users.update_one({"id": user["id"]}, {"$set": {"plan": "free"}})
        pub["plan"] = "free"
    return pub


@router.put("/users/me")
async def update_me(
    update: UserProfileUpdate, user: Dict[str, Any] = Depends(get_current_user)
):
    update_data = {k: v for k, v in update.dict().items() if v is not None}
    update_data["updated_at"] = datetime.utcnow()
    await db.users.update_one({"id": user["id"]}, {"$set": update_data})
    updated = await db.users.find_one({"id": user["id"]})
    return _public_user(updated)


@router.get("/users/{user_id}")
async def get_user(user_id: str, user: Dict[str, Any] = Depends(get_current_user)):
    _require_same_user(user, user_id)
    return await get_me(user)


@router.put("/users/{user_id}")
async def update_user(
    user_id: str,
    update: UserProfileUpdate,
    user: Dict[str, Any] = Depends(get_current_user),
):
    _require_same_user(user, user_id)
    return await update_me(update, user)
