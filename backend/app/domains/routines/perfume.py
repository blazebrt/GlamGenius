"""Choosing a perfume from the ones you own.

Context in, owned bottle out. Occasion, weather, time of day, season, your
stated style, and what you wore recently.

One thing this deliberately does **not** do: claim anything about chemistry.
We do not know how a fragrance interacts with anyone's skin, and there is no
data in the app that could tell us. Where the reason is genuinely just
convention — heavier families in cooler weather — it says so in those words.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from app.domains.recommendation.context import OwnedItem
from app.domains.recommendation.occasions import OCCASIONS
from app.domains.routines.ontology import PERFUME_RULES, normalise_fragrance_family

# Occasions grouped onto the buckets the perfume rules speak in.
OCCASION_BUCKET: dict[str, str] = {
    "office": "office", "conference": "office", "business_meeting": "formal",
    "interview": "formal", "wedding": "festive", "festival": "festive",
    "party": "festive", "birthday": "festive", "date": "formal",
    "everyday": "office", "college": "office", "travel": "office",
    "vacation": "office", "photoshoot": "formal", "gym": "office", "home": "office",
}

# Days before a scent stops counting as "just worn".
RECENT_DAYS = 3


@dataclass
class PerfumeCandidate:
    item: OwnedItem
    family: Optional[str]
    score: float
    reasons: list[dict[str, Any]] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "inventory_item_id": str(self.item.id),
            "display_name": self.item.display_name,
            "brand": self.item.brand,
            "fragrance_family": self.family,
            "concentration": self.item.details.get("concentration"),
            "remaining_percent": self.item.details.get("remaining_percent"),
            "score": round(self.score, 4),
            "reasons": self.reasons,
            "missing_information": self.missing,
            "owned": True,
        }


def _matching_rules(factor: str, value: Optional[str]) -> list[Any]:
    if not value:
        return []
    return [rule for rule in PERFUME_RULES if rule.factor == factor and rule.match == value]


def recommend(
    perfumes: Sequence[OwnedItem],
    *,
    occasion_key: Optional[str] = None,
    weather: Optional[str] = None,
    time_of_day: Optional[str] = None,
    season: Optional[str] = None,
    preferred_style: Optional[str] = None,
    recently_used_item_ids: Sequence[str] = (),
    today: Optional[date] = None,
) -> dict[str, Any]:
    """Rank owned perfumes for a context. Never invents a bottle."""
    _now = today or date.today()
    bucket = OCCASION_BUCKET.get(occasion_key or "", None)

    candidates: list[PerfumeCandidate] = []
    for item in perfumes:
        details = item.details or {}
        family = normalise_fragrance_family(details.get("fragrance_family"))
        candidate = PerfumeCandidate(item=item, family=family, score=0.5)

        if family is None:
            candidate.missing.append(
                "No fragrance family recorded, so this was ranked on your own notes only."
            )

        matched = 0
        for factor, value in (("weather", weather), ("time_of_day", time_of_day), ("occasion", bucket)):
            for rule in _matching_rules(factor, value):
                if family and family in rule.families:
                    matched += 1
                    candidate.score += 0.18
                    candidate.reasons.append({
                        "rule_id": rule.rule_id, "factor": factor, "note": rule.note,
                    })

        # The user's own tags beat any convention we could apply.
        tagged_occasions = [str(v).lower() for v in (details.get("occasion") or [])]
        if occasion_key and any(occasion_key.replace("_", " ") in value for value in tagged_occasions):
            candidate.score += 0.30
            candidate.reasons.append({
                "rule_id": "perfume.user_tagged_occasion", "factor": "occasion",
                "note": f"You tagged this one for {OCCASIONS[occasion_key].label.lower()}.",
            })
        tagged_seasons = [str(v).lower() for v in (details.get("season") or [])]
        if season and any(season in value for value in tagged_seasons):
            candidate.score += 0.22
            candidate.reasons.append({
                "rule_id": "perfume.user_tagged_season", "factor": "season",
                "note": f"You tagged this one for {season}.",
            })

        if preferred_style and family and str(preferred_style).lower() in (family, ""):
            candidate.score += 0.1

        # Something worn in the last few days goes down the list, so the
        # collection actually gets used.
        if str(item.id) in set(recently_used_item_ids):
            candidate.score -= 0.25
            candidate.reasons.append({
                "rule_id": "perfume.recently_worn", "factor": "recent_usage",
                "note": "You wore this one recently, so it is lower down today.",
            })

        remaining = details.get("remaining_percent")
        if isinstance(remaining, int) and remaining <= 15:
            candidate.reasons.append({
                "rule_id": "perfume.running_low", "factor": "inventory",
                "note": f"About {remaining}% left in the bottle.",
            })

        if not matched and family:
            candidate.reasons.append({
                "rule_id": "perfume.no_convention_match", "factor": "context",
                "note": f"Nothing in the context particularly calls for a {family} scent, but nothing rules it out either.",
            })

        candidates.append(candidate)

    candidates.sort(key=lambda row: (-row.score, row.item.display_name))

    missing: list[str] = []
    if not weather:
        missing.append("No weather given, so that was not used.")
    if not occasion_key:
        missing.append("No occasion given, so this is general.")
    if perfumes and all(row.family is None for row in candidates):
        missing.append("None of your perfumes have a fragrance family recorded. Adding one sharpens this a lot.")

    return {
        "recommendations": [row.as_dict() for row in candidates[:3]],
        "considered": len(candidates),
        "context": {
            "occasion_key": occasion_key, "weather": weather,
            "time_of_day": time_of_day, "season": season,
        },
        "missing_information": missing,
        "note": (
            "Ranked from the perfumes you own, using convention about weather, time and occasion. "
            "We make no claim about how a fragrance behaves on your skin — we have no way to know that."
        ),
        "message": None if perfumes else "No perfumes recorded yet. Add one and this starts working.",
    }
