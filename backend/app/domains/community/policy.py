"""When a pile of shopper reports may become one public sentence.

This is a **product display policy**, not scientific evidence and not a
regulatory finding. It is versioned so a future policy can re-read the same
retained rows and reach a different answer without rewriting history.

Nothing here produces customer copy — the caller receives semantic keys and
counts, and the string file decides how a person reads them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from urllib.parse import urlparse

COMMUNITY_POLICY_VERSION = "community-observations-v1"

#: How long a report stays current for display. A pack condition seen last
#: winter says little about what is on the shelf today, and the row is retained
#: either way — only its effect on the public signal expires.
ACTIVE_WINDOW_DAYS = 90

#: Three separate people, each with their own photograph. One vivid report is
#: still one report: the threshold does not bend because a code sounds serious,
#: because that is exactly when a single mistaken or malicious report does the
#: most damage to a brand that has done nothing wrong.
MIN_PUBLIC_REPORTERS = 3
MIN_UNIQUE_PHOTOS = 3

REASON_BELOW_REPORTER_THRESHOLD = "below_reporter_threshold"
REASON_BELOW_PHOTO_THRESHOLD = "below_photo_threshold"
REASON_THRESHOLD_MET = "threshold_met"
REASON_PUBLIC_DISPLAY_DISABLED = "public_display_disabled"
REASON_BRAND_REPLY_URL_MISSING = "brand_reply_url_missing"


@dataclass(frozen=True)
class AggregateEvidence:
    """Normalised, already-deduplicated evidence for one aggregate key."""

    observation_code: str
    scope: str
    batch_number: str | None
    reporter_account_ids: frozenset[str] = field(default_factory=frozenset)
    supporting_photo_hashes: frozenset[str] = field(default_factory=frozenset)
    first_reported_at: datetime | None = None
    last_reported_at: datetime | None = None


@dataclass(frozen=True)
class SignalDecision:
    """What the policy concluded, in keys. Never a sentence."""

    public: bool
    observation_code: str
    scope: str
    batch_number: str | None
    independent_reporters: int
    unique_supporting_photos: int
    first_reported_at: datetime | None
    last_reported_at: datetime | None
    reason_keys: tuple[str, ...]
    #: Two invariants restated in every decision so no caller can mistake a
    #: shopper observation for a graded fact or a government finding.
    analysis_score_eligible: bool = False
    official_finding: bool = False


def active_window_start(now: datetime) -> datetime:
    return now - timedelta(days=ACTIVE_WINDOW_DAYS)


def evaluate(evidence: AggregateEvidence) -> SignalDecision:
    """Decide one aggregate key. Counts people and photographs, never rows."""
    reporters = len(evidence.reporter_account_ids)
    photos = len(evidence.supporting_photo_hashes)
    reasons: list[str] = []
    if reporters < MIN_PUBLIC_REPORTERS:
        reasons.append(REASON_BELOW_REPORTER_THRESHOLD)
    # Three accounts uploading one identical image is one observation wearing
    # three coats, so photographs are counted by content, not by upload.
    if photos < MIN_UNIQUE_PHOTOS:
        reasons.append(REASON_BELOW_PHOTO_THRESHOLD)
    return SignalDecision(
        public=not reasons,
        observation_code=evidence.observation_code,
        scope=evidence.scope,
        batch_number=evidence.batch_number,
        independent_reporters=reporters,
        unique_supporting_photos=photos,
        first_reported_at=evidence.first_reported_at,
        last_reported_at=evidence.last_reported_at,
        reason_keys=tuple(reasons) if reasons else (REASON_THRESHOLD_MET,),
    )


def brand_reply_url_is_valid(url: str | None) -> bool:
    """An openable HTTPS address, or the brand has no visible way to answer."""
    if not url:
        return False
    parsed = urlparse(url.strip())
    return parsed.scheme == "https" and bool(parsed.netloc)


def public_display_state(*, enabled: bool, brand_reply_url: str | None) -> tuple[bool, tuple[str, ...]]:
    """Fail closed. Publishing shoppers' claims about a brand without giving the
    brand a visible way to answer is the thing the Constitution forbids, so a
    missing or malformed reply URL silently switches public display off rather
    than publishing anyway."""
    reasons: list[str] = []
    if not enabled:
        reasons.append(REASON_PUBLIC_DISPLAY_DISABLED)
    if not brand_reply_url_is_valid(brand_reply_url):
        reasons.append(REASON_BRAND_REPLY_URL_MISSING)
    return (not reasons, tuple(reasons))
