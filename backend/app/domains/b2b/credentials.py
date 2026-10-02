"""B2B API credentials: format, generation, hashing and verification.

Format::

    ggb_<prefix>_<secret>
    ggb_3f9c0a1b7e2d_<64 lowercase hex characters>

* ``ggb_`` — recognisable on sight, and to the log and Sentry scrubbers, which
  redact anything of this shape wherever it appears.
* ``<prefix>`` — 12 hex characters (48 random bits) from :mod:`secrets`.
  Public. It is what the credential is looked up by, through a unique index,
  and it is what an operator and a log line may name.
* ``<secret>`` — 64 hex characters: 256 random bits from :mod:`secrets`.

Only ``SHA-256(whole credential)`` is stored. A fast hash is the right tool
for a 256-bit random secret — there is nothing to brute-force and no password
to stretch — and hashing the *whole* credential binds the prefix to the
secret, so a caller who edits the prefix of one valid key to another client's
prefix is presenting a credential whose hash matches nothing.

The raw credential exists in exactly one place: the single successful
issuance response. It is never stored, never logged, and never derivable
again; a lost key is replaced, not recovered.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass, field

SCHEME = "ggb"
PREFIX_BYTES = 6
SECRET_BYTES = 32
PREFIX_LENGTH = PREFIX_BYTES * 2
SECRET_LENGTH = SECRET_BYTES * 2
#: ``ggb_`` + prefix + ``_`` + secret.
CREDENTIAL_LENGTH = len(SCHEME) + 1 + PREFIX_LENGTH + 1 + SECRET_LENGTH

_CREDENTIAL = re.compile(rf"{SCHEME}_([0-9a-f]{{{PREFIX_LENGTH}}})_([0-9a-f]{{{SECRET_LENGTH}}})")

#: A hash nothing hashes to in practice, compared against when no stored key
#: exists, so a missing prefix costs the same comparison as a present one.
_ABSENT_HASH = "0" * 64


@dataclass(frozen=True)
class IssuedCredential:
    """A freshly generated credential. ``raw`` is shown once and then dropped."""

    prefix: str
    key_hash: str
    raw: str = field(repr=False)


def hash_credential(raw: str) -> str:
    """SHA-256 of the whole presented credential, lowercase hex."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def generate() -> IssuedCredential:
    """A new credential from the operating system's CSPRNG."""
    prefix = secrets.token_hex(PREFIX_BYTES)
    secret = secrets.token_hex(SECRET_BYTES)
    raw = f"{SCHEME}_{prefix}_{secret}"
    return IssuedCredential(prefix=prefix, key_hash=hash_credential(raw), raw=raw)


def prefix_of(presented: object) -> str | None:
    """The lookup prefix of a well-formed credential, else ``None``.

    Shape only: a well-formed credential may still be unknown, wrong, expired,
    revoked or belong to a suspended client. Anything that is not exactly this
    shape — a consumer Supabase JWT, an empty string, a key with a space in
    it — never reaches the database.
    """
    if not isinstance(presented, str) or len(presented) != CREDENTIAL_LENGTH:
        return None
    match = _CREDENTIAL.fullmatch(presented)
    return match.group(1) if match else None


def matches(presented: str, stored_hash: str | None) -> bool:
    """Constant-time comparison of the presented credential with a stored hash.

    Always performs one comparison, including when there is no stored hash, so
    the timing of a refusal does not depend on whether the prefix exists.
    """
    candidate = hash_credential(presented)
    expected = stored_hash if stored_hash is not None else _ABSENT_HASH
    return hmac.compare_digest(candidate, expected) and stored_hash is not None


__all__ = [
    "CREDENTIAL_LENGTH",
    "PREFIX_LENGTH",
    "SCHEME",
    "SECRET_BYTES",
    "IssuedCredential",
    "generate",
    "hash_credential",
    "matches",
    "prefix_of",
]
