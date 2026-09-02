"""When a pile of shopper reports may become one public sentence.

This is a **product display policy**, not scientific evidence and not a
regulatory finding. It is versioned so a future policy can re-read the same
retained rows and reach a different answer without rewriting history.

Nothing here produces customer copy — the caller receives semantic keys and
counts, and the string file decides how a person reads them.
"""
from __future__ import annotations

from collections.abc import Mapping
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
    """Normalised, already-deduplicated evidence for one aggregate key.

    ``reporter_photo_hashes`` maps each reporter to the photographs *they*
    supplied. The mapping matters: counting accounts and hashes as two separate
    sets says three people and three photographs even when one person supplied
    all three and the other two passed round a copy of the first.
    """

    observation_code: str
    scope: str
    batch_number: str | None
    reporter_photo_hashes: Mapping[str, frozenset[str]] = field(default_factory=dict)
    first_reported_at: datetime | None = None
    last_reported_at: datetime | None = None

    @property
    def reporter_account_ids(self) -> frozenset[str]:
        return frozenset(self.reporter_photo_hashes)

    @property
    def supporting_photo_hashes(self) -> frozenset[str]:
        return frozenset().union(*self.reporter_photo_hashes.values()) if self.reporter_photo_hashes else frozenset()


def max_independent_pairs(reporter_photo_hashes: Mapping[str, frozenset[str]]) -> int:
    """The largest set of reporters that can each be given a photograph of their own.

    A maximum bipartite matching, by augmenting paths — small, deterministic,
    and no dependency. It answers the question the threshold actually asks: how
    many people independently *evidenced* this, rather than how many people
    spoke and how many pictures exist anywhere among them.

    The exploit it closes: one account uploads three distinct photographs and
    two friends each re-upload the first. Three accounts, three hashes, and a
    public signal — but only one person ever photographed anything. Matched,
    the two friends compete for the one hash they share and only one can take
    it, so the pairing is two, not three.
    """
    assigned: dict[str, str] = {}  # photo hash -> reporter holding it

    def _assign(reporter: str, seen: set[str]) -> bool:
        for photo in sorted(reporter_photo_hashes[reporter]):
            if photo in seen:
                continue
            seen.add(photo)
            holder = assigned.get(photo)
            if holder is None or _assign(holder, seen):
                assigned[photo] = reporter
                return True
        return False

    for reporter in sorted(reporter_photo_hashes):
        _assign(reporter, set())
    return len(assigned)


@dataclass(frozen=True)
class SignalDecision:
    """What the policy concluded, in keys. Never a sentence."""

    public: bool
    observation_code: str
    scope: str
    batch_number: str | None
    independent_reporters: int
    #: The size of the largest reporter-to-photograph pairing, not a raw hash count.
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
    """Decide one aggregate key. Counts people and their own photographs."""
    reporters = len(evidence.reporter_account_ids)
    pairs = max_independent_pairs(evidence.reporter_photo_hashes)
    reasons: list[str] = []
    if reporters < MIN_PUBLIC_REPORTERS:
        reasons.append(REASON_BELOW_REPORTER_THRESHOLD)
    # Not "three accounts and three hashes somewhere between them" — three
    # accounts that can each be paired with a photograph nobody else is using.
    if pairs < MIN_UNIQUE_PHOTOS:
        reasons.append(REASON_BELOW_PHOTO_THRESHOLD)
    return SignalDecision(
        public=not reasons,
        observation_code=evidence.observation_code,
        scope=evidence.scope,
        batch_number=evidence.batch_number,
        independent_reporters=reporters,
        unique_supporting_photos=pairs,
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
