"""Opaque credential storage for Google Calendar refresh tokens.

Tests inject :class:`InMemoryCredentialStore`. Production uses Supabase Vault
through its SQL functions and never falls back to an application-table column.
"""
from __future__ import annotations

from collections.abc import MutableMapping
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class CalendarCredentialStore(Protocol):
    async def store(self, refresh_token: str) -> str: ...
    async def read(self, credential_ref: str) -> str | None: ...
    async def replace(self, credential_ref: str, refresh_token: str) -> str: ...
    async def delete(self, credential_ref: str) -> None: ...


class InMemoryCredentialStore:
    """Deterministic test store; values exist only in process memory."""

    def __init__(self, values: MutableMapping[str, str] | None = None) -> None:
        self.values = values if values is not None else {}
        self._counter = 0

    async def store(self, refresh_token: str) -> str:
        self._counter += 1
        ref = f"memory:{self._counter}"
        self.values[ref] = refresh_token
        return ref

    async def read(self, credential_ref: str) -> str | None:
        return self.values.get(credential_ref)

    async def replace(self, credential_ref: str, refresh_token: str) -> str:
        self.values[credential_ref] = refresh_token
        return credential_ref

    async def delete(self, credential_ref: str) -> None:
        self.values.pop(credential_ref, None)


class SupabaseVaultCredentialStore:
    """Supabase Vault-backed store using the documented ``vault.*`` SQL API."""

    prefix = "supabase-vault:"

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @classmethod
    def _id(cls, ref: str) -> str:
        if not ref.startswith(cls.prefix):
            raise ValueError("unsupported credential reference")
        return ref.removeprefix(cls.prefix)

    async def store(self, refresh_token: str) -> str:
        result = await self.session.execute(
            # An unnamed secret avoids a global name collision between accounts.
            text("SELECT vault.create_secret(:secret)"),
            {"secret": refresh_token},
        )
        value = result.scalar_one()
        return f"{self.prefix}{value}"

    async def read(self, credential_ref: str) -> str | None:
        result = await self.session.execute(
            text("SELECT decrypted_secret FROM vault.decrypted_secrets WHERE id = CAST(:id AS uuid)"),
            {"id": self._id(credential_ref)},
        )
        return result.scalar_one_or_none()

    async def replace(self, credential_ref: str, refresh_token: str) -> str:
        # Vault updates in place, preserving the opaque UUID reference and
        # avoiding a create-then-delete window or orphaned secret.
        await self.session.execute(
            text("SELECT vault.update_secret(CAST(:id AS uuid), :secret, NULL, NULL)"),
            {"id": self._id(credential_ref), "secret": refresh_token},
        )
        return credential_ref

    async def delete(self, credential_ref: str) -> None:
        await self.session.execute(text("SELECT vault.delete_secret(CAST(:id AS uuid))"), {"id": self._id(credential_ref)})


def credential_store(session: AsyncSession) -> CalendarCredentialStore:
    from app.config import GOOGLE_CALENDAR_CREDENTIAL_STORE

    if GOOGLE_CALENDAR_CREDENTIAL_STORE == "supabase_vault":
        return SupabaseVaultCredentialStore(session)
    raise RuntimeError("Google Calendar credential store is not configured")


__all__ = ["CalendarCredentialStore", "InMemoryCredentialStore", "SupabaseVaultCredentialStore", "credential_store"]
