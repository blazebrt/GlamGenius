"""Login, tokens, password hashing, and user/access helpers."""
from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import hashlib
import hmac
import bcrypt
import jwt
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta

from database import db
from settings import (
    JWT_SECRET,
    JWT_ALGORITHM,
    JWT_EXPIRY_DAYS,
    MAX_LOGIN_ATTEMPTS,
    LOGIN_LOCKOUT_MINUTES,
    MAX_PREVIEWS_PER_WINDOW,
    PREVIEW_WINDOW_MINUTES,
    FREE_SCANS_PER_MONTH,
)

def _hash_password(password: str) -> str:
    """Hash a password with bcrypt (salted, slow by design)."""
    # bcrypt only reads the first 72 bytes; truncate explicitly so long
    # passwords hash deterministically instead of raising.
    raw = password.encode("utf-8")[:72]
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("utf-8")


def _legacy_hash_password(password: str) -> str:
    """The old unsalted SHA-256 scheme. Only used to verify pre-bcrypt users."""
    return hashlib.sha256(f"{JWT_SECRET}:{password}".encode()).hexdigest()


def _verify_password(password: str, stored_hash: str) -> tuple[bool, bool]:
    """Check a password against a stored hash.

    Returns (is_valid, needs_rehash). needs_rehash is True when the password
    was verified against the old SHA-256 scheme, so the caller can silently
    upgrade the stored hash to bcrypt.
    """
    if not stored_hash:
        return False, False

    if stored_hash.startswith(("$2a$", "$2b$", "$2y$")):
        try:
            return bcrypt.checkpw(password.encode("utf-8")[:72], stored_hash.encode("utf-8")), False
        except ValueError:
            return False, False

    # Pre-bcrypt user: fall back to the old scheme, and flag for upgrade.
    if hmac.compare_digest(_legacy_hash_password(password), stored_hash):
        return True, True
    return False, False


def _create_access_token(user_id: str) -> str:
    """Signed token proving who the caller is. Valid for 30 days."""
    now = datetime.utcnow()
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + timedelta(days=JWT_EXPIRY_DAYS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> Dict[str, Any]:
    """Resolve the caller from their token. Rejects with 401 on any problem.

    This is the single gate for every route that touches user data. Identity
    always comes from the signed token — never from the URL or request body.
    """
    unauthorized = HTTPException(
        status_code=401,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if credentials is None or not credentials.credentials:
        raise unauthorized

    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        # Never log the token itself.
        raise unauthorized

    user_id = payload.get("sub")
    if not user_id:
        raise unauthorized

    user = await db.users.find_one({"id": user_id})
    if not user:
        raise unauthorized

    return user


def _login_keys(email: str, client_ip: str) -> List[str]:
    """The two things we count failed logins against."""
    return [f"email:{(email or '').lower().strip()}", f"ip:{client_ip or 'unknown'}"]


async def _assert_not_locked_out(keys: List[str]) -> None:
    """Refuse with 429 once too many recent failures are on record.

    Each key is counted on its own limit. The address limit is deliberately
    looser than the account limit: many genuine users share one address behind
    a mobile network or office router, and one person fumbling their password
    must not lock out everyone around them.
    """
    since = datetime.utcnow() - timedelta(minutes=LOGIN_LOCKOUT_MINUTES)
    for key in keys:
        limit = MAX_LOGIN_ATTEMPTS if key.startswith("email:") else MAX_LOGIN_ATTEMPTS * 4
        count = await db.login_attempts.count_documents(
            {"key": key, "created_at": {"$gte": since}}
        )
        if count >= limit:
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Too many failed sign-in attempts. Please wait "
                    f"{LOGIN_LOCKOUT_MINUTES} minutes and try again."
                ),
                headers={"Retry-After": str(LOGIN_LOCKOUT_MINUTES * 60)},
            )


async def _assert_preview_quota(client_ip: str) -> None:
    """Cap free teasers per address so the signed-out route cannot be farmed."""
    since = datetime.utcnow() - timedelta(minutes=PREVIEW_WINDOW_MINUTES)
    count = await db.preview_attempts.count_documents(
        {"key": f"ip:{client_ip}", "created_at": {"$gte": since}}
    )
    if count >= MAX_PREVIEWS_PER_WINDOW:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "PREVIEW_LIMIT",
                "message": "You have used your free previews. Create a free account to keep going.",
                "signup_required": True,
            },
            headers={"Retry-After": str(PREVIEW_WINDOW_MINUTES * 60)},
        )


async def _record_preview(client_ip: str) -> None:
    await db.preview_attempts.insert_one(
        {"key": f"ip:{client_ip}", "created_at": datetime.utcnow()}
    )


async def _record_failed_login(keys: List[str]) -> None:
    """Never store the attempted password — only that an attempt failed."""
    now = datetime.utcnow()
    await db.login_attempts.insert_many([{"key": k, "created_at": now} for k in keys])


async def _clear_failed_logins(keys: List[str]) -> None:
    await db.login_attempts.delete_many({"key": {"$in": keys}})


def _require_same_user(user: Dict[str, Any], user_id: str) -> None:
    """For routes that still carry an id in the URL: it must be the caller's own."""
    if user_id != user["id"]:
        raise HTTPException(status_code=403, detail="Not allowed to access another user's data")


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

    for key in (
        "skin_type",
        "hair_type",
        "face_shape",
        "budget_range",
        "skin_tone",
        "undertone",
        "diet",
        "city",
        "plan",
        "body_type",
        "style_vibe",
    ):
        if key in doc and doc[key] is not None and not isinstance(doc[key], str):
            doc[key] = str(doc[key])

    return doc

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

def _user_view(user: Dict[str, Any]) -> Dict[str, Any]:
    pub = _public_user(user)
    pub["scans_remaining_free"] = max(
        0, FREE_SCANS_PER_MONTH - int(user.get("scans_used_this_month") or 0)
    ) if pub.get("plan") != "plus" else None
    pub["free_scans_per_month"] = FREE_SCANS_PER_MONTH
    return pub
