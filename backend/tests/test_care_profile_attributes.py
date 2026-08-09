"""V3-03.1 registry and profile-write contracts."""
from __future__ import annotations

import pytest
from app.domains.privacy import deletion_service
from app.domains.privacy import export as export_service
from app.domains.profile.models import AppearanceProfile, ProfileAttribute
from app.domains.profile.registry import ATTRIBUTE_REGISTRY, AttributeSpec, validate_attribute
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import func, select

from tests.conftest import auth

CARE_SCALARS = {
    "care_skin_usual_feel": ("comfortable", "often_dry_or_tight", "often_oily", "mixed", "not_sure"),
    "care_skin_sensitivity": ("rarely_reactive", "sometimes_reactive", "often_reactive", "not_sure"),
    "care_hair_pattern": ("straight", "wavy", "curly", "coily", "not_sure"),
    "care_hair_strand_characteristic": ("fine", "medium", "coarse", "not_sure"),
    "care_hair_density": ("low", "medium", "high", "not_sure"),
    "care_hair_wash_frequency": ("daily", "several_times_week", "weekly", "less_than_weekly", "variable", "not_sure"),
    "care_heat_styling_frequency": ("never", "occasional", "frequent", "daily", "not_sure"),
    "care_scalp_usual_feel": ("comfortable", "often_dry_or_tight", "often_oily", "sometimes_uncomfortable", "not_sure"),
    "care_humidity_frizz_sensitivity": ("low", "moderate", "high", "not_sure"),
    "care_hair_styling_preference": ("air_dry", "heat_style", "protective_style", "mixed", "not_sure"),
    "care_routine_effort": ("minimal", "balanced", "detailed", "not_sure"),
    "care_fragrance_preference": ("fragrance_free_preferred", "no_preference", "likes_fragrance", "not_sure"),
    "care_event_preparation_effort": ("minimal", "balanced", "detailed", "not_sure"),
}


def test_care_registry_has_exact_scalar_vocabularies_and_is_not_ai_observable():
    for key, choices in CARE_SCALARS.items():
        spec = ATTRIBUTE_REGISTRY[key]
        assert spec.kind == "text"
        assert spec.choices == choices
        assert spec.ai_observable is False


def test_processing_is_a_non_empty_controlled_list():
    spec = ATTRIBUTE_REGISTRY["care_hair_processing"]
    assert spec.kind == "list"
    assert spec.choices == ("none", "not_sure", "coloured", "bleached", "relaxed", "permed_or_texturised")
    assert spec.min_items == 1
    assert spec.exclusive_choices == ("none", "not_sure")

    assert validate_attribute("care_hair_processing", ["Coloured", "RELAXED", "coloured"]) == ["coloured", "relaxed"]
    assert validate_attribute("care_hair_processing", ["none"]) == ["none"]
    assert validate_attribute("care_hair_processing", ["not_sure"]) == ["not_sure"]
    for invalid in ([], ["none", "coloured"], ["not_sure", "bleached"], ["unknown-value"]):
        with pytest.raises(ValueError):
            validate_attribute("care_hair_processing", invalid)


def test_scalar_choice_canonicalization_preserves_existing_compatibility():
    assert validate_attribute("style_experimentation", "Balanced") == "balanced"
    assert validate_attribute("care_routine_effort", "BALANCED") == "balanced"


def test_uncontrolled_legacy_lists_remain_flexible():
    assert validate_attribute("allergies", ["Latex", "custom ingredient"]) == ["Latex", "custom ingredient"]


def test_min_items_applies_to_uncontrolled_lists_too():
    from app.domains.profile import registry

    key = "_test_uncontrolled_min_items"
    registry.ATTRIBUTE_REGISTRY[key] = AttributeSpec(
        key=key, section="test", label="Test list", kind="list", min_items=1
    )
    try:
        with pytest.raises(ValueError):
            validate_attribute(key, [])
        assert validate_attribute(key, ["free form"]) == ["free form"]
    finally:
        registry.ATTRIBUTE_REGISTRY.pop(key, None)


@pytest.mark.asyncio
async def test_profile_patch_uses_existing_care_write_path(
    app_client, db_clean, registered_supabase_user
):
    token, _ = await registered_supabase_user()
    response = await app_client.patch(
        "/api/v2/profile",
        headers=auth(token),
        json={"attributes": [{"key": "care_hair_pattern", "value": "Curly"}]},
    )
    assert response.status_code == 200
    attribute = next(row for row in response.json()["attributes"] if row["key"] == "care_hair_pattern")
    assert attribute["value"] == "curly"
    assert attribute["source"] == "user_declared"
    assert attribute["verification_state"] == "confirmed"

    metadata = (await app_client.get("/api/v2/profile/attributes", headers=auth(token))).json()
    processing = next(row for row in metadata["registry"] if row["key"] == "care_hair_processing")
    assert processing["choices"] == ["none", "not_sure", "coloured", "bleached", "relaxed", "permed_or_texturised"]
    assert processing["min_items"] == 1
    assert processing["exclusive_choices"] == ["none", "not_sure"]


@pytest.mark.asyncio
async def test_care_attribute_export_and_account_deletion_cascade(
    app_client, db_clean, registered_supabase_user, media_root, monkeypatch
):
    token, account_id = await registered_supabase_user()
    response = await app_client.patch(
        "/api/v2/profile",
        headers=auth(token),
        json={"attributes": [{"key": "care_hair_pattern", "value": "curly"}]},
    )
    assert response.status_code == 200, response.text

    factory = get_sessionmaker()
    async with factory() as session:
        exported = await export_service.build_export(session, account_id)
        rows = exported["domains"]["profile"]["attributes"]
        care = next(row for row in rows if row["key"] == "care_hair_pattern")
        assert care["value"] == "curly"
        assert care["source"] == "user_declared"
        assert care["verification_state"] == "confirmed"

    class _Admin:
        class auth:
            class admin:
                @staticmethod
                def delete_user(_uid):
                    return None

    monkeypatch.setattr(deletion_service, "get_supabase_admin", lambda: _Admin())
    requested = await app_client.delete(
        "/api/v2/privacy/account", headers=auth(token)
    )
    assert requested.status_code == 202
    async with factory() as session:
        await deletion_service.drain_all(session)
        await session.commit()

    async with factory() as session:
        count = await session.scalar(
            select(func.count(ProfileAttribute.id)).join(
                AppearanceProfile,
                ProfileAttribute.profile_id == AppearanceProfile.id,
            ).where(AppearanceProfile.account_id == account_id)
        )
        assert count == 0
