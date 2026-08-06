"""Server-side Supabase client.

Uses the **service-role** key. This client is **never** exposed to the frontend
or serialised into any response body. It is used only for:

* Supabase Storage administrative operations (upload, delete, signed URL).
* Supabase Auth admin operations (delete user, invite email — but we do
  invite codes in Postgres, not Supabase's own invite flow).

The service-role key bypasses every RLS policy on the project, so every
call site must have already verified the caller's identity with
``get_current_supabase_user`` and their ownership of the resource.
"""
from __future__ import annotations

import logging

from supabase import Client, create_client

from app.config import SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL

logger = logging.getLogger(__name__)

_client: Client | None = None


def get_supabase_admin() -> Client:
    """Return a lazily-built service-role client.

    Raises ``RuntimeError`` when Supabase is not configured — the caller
    should fail closed on that, never silently degrade.
    """
    global _client
    if _client is None:
        if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
            raise RuntimeError(
                "Supabase is not configured. Set SUPABASE_URL and "
                "SUPABASE_SERVICE_ROLE_KEY in backend/.env."
            )
        _client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    return _client


def reset_supabase_admin() -> None:
    """Reset the cached client. Used only by tests."""
    global _client
    _client = None


__all__ = ["get_supabase_admin", "reset_supabase_admin"]
