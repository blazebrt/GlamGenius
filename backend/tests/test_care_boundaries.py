"""Trust, precedence, and non-behavior boundaries for Care foundation."""
from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.domains.care.service import _assemble_profile_facts


def _row(key: str, value, *, source: str = "user_declared", verification_state: str = "confirmed"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        key=key,
        value=value,
        source=source,
        confidence=0.8,
        verification_state=verification_state,
    )


def test_trusted_explicit_care_fact_is_usable_and_not_sure_is_present():
    facts, missing = _assemble_profile_facts(
        {"care_hair_pattern": _row("care_hair_pattern", "not_sure")},
        ("care_hair_pattern",),
        area="hair",
    )
    assert facts["care_hair_pattern"].value == "not_sure"
    assert facts["care_hair_pattern"].explicit_unknown is True
    assert missing == []


def test_untrusted_explicit_candidate_does_not_block_confirmed_legacy_fallback():
    facts, missing = _assemble_profile_facts(
        {
            "care_hair_pattern": _row("care_hair_pattern", "wavy", source="photo_observed"),
            "hair_type": _row("hair_type", " Curly ", source="photo_observed"),
        },
        ("care_hair_pattern",),
        area="hair",
    )
    assert facts["care_hair_pattern"].value == "curly"
    assert facts["care_hair_pattern"].fact_source == "legacy_profile_confirmed"
    assert missing == []


def test_untrusted_explicit_candidate_without_legacy_is_not_usable():
    facts, missing = _assemble_profile_facts(
        {"care_hair_pattern": _row("care_hair_pattern", "curly", source="photo_observed")},
        ("care_hair_pattern",),
        area="hair",
    )
    assert facts == {}
    assert missing[0].reason == "untrusted"


def test_legacy_fallback_is_exact_only_and_processing_has_no_fallback():
    facts, missing = _assemble_profile_facts(
        {
            "hair_type": _row("hair_type", "thin"),
            "hair_texture": _row("hair_texture", "Fine"),
        },
        ("care_hair_pattern", "care_hair_strand_characteristic", "care_hair_processing"),
        area="hair",
    )
    assert "care_hair_pattern" not in facts
    assert facts["care_hair_strand_characteristic"].value == "fine"
    assert any(row.key == "care_hair_processing" and row.reason == "missing" for row in missing)
