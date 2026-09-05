"""Step 8A — trusted personal context, before evidence or decisions."""

from __future__ import annotations

import ast
import dataclasses
import inspect
import uuid
from pathlib import Path
from types import MappingProxyType

import pytest
from app.domains.identity import service as identity_service
from app.domains.personal_lens import service
from app.domains.personal_lens.enums import (
    PersonalFactKind,
    PersonalFactMissingReason,
    PersonalLensCategory,
    PersonalLensStatus,
)
from app.domains.personal_lens.service import (
    BODY_FACT_KEYS_BY_CATEGORY,
    PREFERENCE_FACT_KEYS,
    PersonalLensSafetyInput,
    build_personal_lens_context,
)
from app.domains.profile import service as profile_service
from app.domains.profile.models import AppearanceProfile, ProfileAttribute, ProfileChangeEvent
from app.domains.routines.hard_handoff import HandoffDecision, HandoffReason
from app.shared.database import sql
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import event, func, select

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DOMAIN_DIR = BACKEND_ROOT / "app" / "domains" / "personal_lens"

SKIN_VALUES = {
    "care_skin_usual_feel": "comfortable",
    "care_skin_sensitivity": "rarely_reactive",
}

HAIR_VALUES = {
    "care_hair_pattern": "curly",
    "care_hair_strand_characteristic": "medium",
    "care_hair_density": "high",
    "care_hair_wash_frequency": "weekly",
    "care_hair_processing": ["coloured"],
    "care_heat_styling_frequency": "occasional",
    "care_scalp_usual_feel": "comfortable",
    "care_humidity_frizz_sensitivity": "moderate",
}

PREFERENCE_VALUES = {
    "care_fragrance_preference": "likes_fragrance",
    "care_routine_effort": "balanced",
}

EXCLUDED_VALUES = {
    "skin_tone": "medium",
    "undertone": "warm",
    "face_shape": "oval",
    "preferred_style": "classic",
    "favourite_colours": ["blue"],
    "disliked_colours": ["orange"],
    "height_cm": 170,
    "appearance_goals": ["Everyday appearance"],
    "sleep_pattern": "regular",
    "hydration_habits": "regular",
    "stress_level": "moderate",
    "workout_frequency": "weekly",
    "activity_level": "moderate",
    "allergies": ["fragrance"],
}


async def _create_profile(
    session,
    values: dict[str, object] | None = None,
    *,
    source: str = "user_declared",
    verification_state: str = "confirmed",
    confidence: float = 1.0,
    account_id: uuid.UUID | None = None,
) -> tuple[uuid.UUID, AppearanceProfile]:
    owner = account_id or uuid.uuid4()
    await identity_service.register_account(session, owner)
    profile = AppearanceProfile(account_id=owner)
    session.add(profile)
    await session.flush()
    for key, value in (values or {}).items():
        session.add(ProfileAttribute(
            profile_id=profile.id,
            key=key,
            value=value,
            source=source,
            confidence=confidence,
            verification_state=verification_state,
        ))
    await session.commit()
    return owner, profile


def _fact_keys(context) -> tuple[tuple[str, ...], tuple[str, ...]]:
    return (
        tuple(fact.key for fact in context.body_facts),
        tuple(fact.key for fact in context.preference_facts),
    )


def _missing(context, key: str) -> object:
    return next(row for row in context.missing_information if row.key == key)


def _record_statements():
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lstrip())

    return statements, record


def test_category_vocabulary_is_exact_and_strings_are_not_categories():
    assert {row.value for row in PersonalLensCategory} == {
        "packaged_food", "skin_care", "hair_care", "cosmetics",
    }


@pytest.mark.asyncio
async def test_category_is_validated_before_the_handoff_gate(monkeypatch):
    def must_not_run(*args, **kwargs):
        raise AssertionError("handoff gate ran before category validation")

    monkeypatch.setattr(service.hard_handoff, "evaluate", must_not_run)
    with pytest.raises(ValueError, match="PersonalLensCategory"):
        await build_personal_lens_context(
            object(), account_id=uuid.uuid4(), category="skin_care",
        )


@pytest.mark.asyncio
async def test_builder_calls_the_exact_handoff_authority(monkeypatch):
    calls: list[tuple[object, object, object]] = []

    def evaluate(text, *, stated_age, subject_is_child):
        calls.append((text, stated_age, subject_is_child))
        return HandoffDecision(handoff=False)

    async def no_profile(session, account_id):
        return None

    monkeypatch.setattr(service.hard_handoff, "evaluate", evaluate)
    monkeypatch.setattr(service.profile_service, "get_profile", no_profile)
    safety = PersonalLensSafetyInput(text="ordinary context", stated_age=25, subject_is_child=False)
    await build_personal_lens_context(
        object(), account_id=uuid.uuid4(), category=PersonalLensCategory.SKIN_CARE, safety=safety,
    )
    assert calls == [("ordinary context", 25, False)]


@pytest.mark.parametrize(
    ("safety", "reason"),
    [
        (PersonalLensSafetyInput(text="I'm pregnant"), HandoffReason.PREGNANCY),
        (PersonalLensSafetyInput(text="I am breastfeeding"), HandoffReason.BREASTFEEDING),
        (PersonalLensSafetyInput(text="I take metformin"), HandoffReason.MEDICATION),
        (PersonalLensSafetyInput(text="I was diagnosed with eczema"), HandoffReason.CLINICAL_CONDITION),
        (PersonalLensSafetyInput(stated_age=11), HandoffReason.AGE_UNDER_MINIMUM),
        (PersonalLensSafetyInput(subject_is_child=True), HandoffReason.CHILD_SUBJECT),
        (PersonalLensSafetyInput(text="Is this safe for me?"), HandoffReason.UNCERTAIN),
    ],
)
@pytest.mark.asyncio
async def test_every_safety_boundary_hands_off_before_profile_reads(monkeypatch, safety, reason):
    async def must_not_read(*args, **kwargs):
        raise AssertionError("profile read occurred after handoff")

    monkeypatch.setattr(service.profile_service, "get_profile", must_not_read)
    context = await build_personal_lens_context(
        object(), account_id=uuid.uuid4(), category=PersonalLensCategory.SKIN_CARE, safety=safety,
    )
    assert context.status is PersonalLensStatus.HANDOFF_REQUIRED
    assert context.handoff is not None
    assert context.handoff.reason == reason.value
    assert context.handoff.message
    assert context.profile_id is None
    assert context.profile_version is None
    assert context.body_facts == ()
    assert context.preference_facts == ()
    assert context.missing_information == ()


@pytest.mark.asyncio
async def test_ordinary_non_medical_context_continues_to_profile_read(monkeypatch):
    calls: list[uuid.UUID] = []

    async def no_profile(session, account_id):
        calls.append(account_id)
        return None

    monkeypatch.setattr(service.profile_service, "get_profile", no_profile)
    owner = uuid.uuid4()
    context = await build_personal_lens_context(
        object(),
        account_id=owner,
        category=PersonalLensCategory.SKIN_CARE,
        safety=PersonalLensSafetyInput(text="My skin usually feels comfortable"),
    )
    assert context.status is PersonalLensStatus.NOT_ENOUGH_PERSONAL_CONTEXT
    assert calls == [owner]


@pytest.mark.parametrize(
    ("source", "verification_state", "reason"),
    [
        ("photo_observed", "confirmed", PersonalFactMissingReason.UNTRUSTED_SOURCE),
        ("inventory_inferred", "confirmed", PersonalFactMissingReason.UNTRUSTED_SOURCE),
        ("behavior_inferred", "confirmed", PersonalFactMissingReason.UNTRUSTED_SOURCE),
        ("integration", "confirmed", PersonalFactMissingReason.UNTRUSTED_SOURCE),
        ("stylist_verified", "confirmed", PersonalFactMissingReason.UNTRUSTED_SOURCE),
        ("user_declared", "unverified", PersonalFactMissingReason.NOT_CONFIRMED),
        ("user_declared", "rejected", PersonalFactMissingReason.NOT_CONFIRMED),
        ("user_declared", "superseded", PersonalFactMissingReason.NOT_CONFIRMED),
    ],
)
@pytest.mark.asyncio
async def test_only_confirmed_user_declarations_are_trusted(
    db_clean, source, verification_state, reason,
):
    factory = get_sessionmaker()
    async with factory() as session:
        owner, _ = await _create_profile(
            session,
            {"care_skin_usual_feel": "comfortable"},
            source=source,
            verification_state=verification_state,
            confidence=0.999,
        )
        context = await build_personal_lens_context(
            session, account_id=owner, category=PersonalLensCategory.SKIN_CARE,
        )
    assert context.body_facts == ()
    assert _missing(context, "care_skin_usual_feel").reason is reason
    assert context.status is PersonalLensStatus.NOT_ENOUGH_PERSONAL_CONTEXT


@pytest.mark.asyncio
async def test_confirmed_user_declaration_is_included_with_provenance(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        owner, _ = await _create_profile(
            session,
            {"care_skin_sensitivity": "sometimes_reactive"},
            confidence=0.01,
        )
        context = await build_personal_lens_context(
            session, account_id=owner, category=PersonalLensCategory.SKIN_CARE,
        )
    fact = context.body_facts[0]
    assert fact.key == "care_skin_sensitivity"
    assert fact.value == "sometimes_reactive"
    assert fact.source == "user_declared"
    assert fact.verification_state == "confirmed"
    assert fact.profile_attribute_id
    assert context.status is PersonalLensStatus.PARTIAL_CONTEXT


@pytest.mark.asyncio
async def test_categories_are_closed_and_never_leak_cross_category_facts(db_clean):
    values = {**SKIN_VALUES, **HAIR_VALUES, **PREFERENCE_VALUES, **EXCLUDED_VALUES}
    factory = get_sessionmaker()
    async with factory() as session:
        owner, _ = await _create_profile(session, values)
        contexts = {
            category: await build_personal_lens_context(session, account_id=owner, category=category)
            for category in PersonalLensCategory
        }

    assert _fact_keys(contexts[PersonalLensCategory.SKIN_CARE]) == (
        BODY_FACT_KEYS_BY_CATEGORY[PersonalLensCategory.SKIN_CARE], PREFERENCE_FACT_KEYS,
    )
    assert _fact_keys(contexts[PersonalLensCategory.COSMETICS]) == (
        BODY_FACT_KEYS_BY_CATEGORY[PersonalLensCategory.COSMETICS], PREFERENCE_FACT_KEYS,
    )
    assert _fact_keys(contexts[PersonalLensCategory.HAIR_CARE]) == (
        BODY_FACT_KEYS_BY_CATEGORY[PersonalLensCategory.HAIR_CARE], PREFERENCE_FACT_KEYS,
    )
    food = contexts[PersonalLensCategory.PACKAGED_FOOD]
    assert _fact_keys(food) == ((), ())
    assert food.status is PersonalLensStatus.NOT_ENOUGH_PERSONAL_CONTEXT

    returned_keys = {
        fact.key
        for context in contexts.values()
        for fact in (*context.body_facts, *context.preference_facts)
    }
    assert returned_keys.isdisjoint(EXCLUDED_VALUES)


@pytest.mark.asyncio
async def test_preferences_are_separate_and_do_not_control_body_readiness(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        owner, _ = await _create_profile(session, SKIN_VALUES)
        without_preferences = await build_personal_lens_context(
            session, account_id=owner, category=PersonalLensCategory.SKIN_CARE,
        )
    assert without_preferences.status is PersonalLensStatus.CONTEXT_AVAILABLE
    assert without_preferences.preference_facts == ()
    assert all(row.kind is PersonalFactKind.PREFERENCE for row in without_preferences.missing_information)

    async with factory() as session:
        profile = await profile_service.get_profile(session, owner)
        assert profile is not None
        await profile_service.apply_attributes(
            session,
            profile,
            [{"key": key, "value": value} for key, value in PREFERENCE_VALUES.items()],
        )
        await session.commit()
        with_preferences = await build_personal_lens_context(
            session, account_id=owner, category=PersonalLensCategory.SKIN_CARE,
        )
    assert with_preferences.status is PersonalLensStatus.CONTEXT_AVAILABLE
    assert tuple(fact.key for fact in with_preferences.preference_facts) == PREFERENCE_FACT_KEYS
    assert not ({fact.key for fact in with_preferences.body_facts} & set(PREFERENCE_FACT_KEYS))


@pytest.mark.asyncio
async def test_appearance_wellness_and_allergy_data_are_never_projected(db_clean):
    values = {**SKIN_VALUES, **EXCLUDED_VALUES}
    factory = get_sessionmaker()
    async with factory() as session:
        owner, _ = await _create_profile(session, values)
        skin = await build_personal_lens_context(
            session, account_id=owner, category=PersonalLensCategory.SKIN_CARE,
        )
        food = await build_personal_lens_context(
            session, account_id=owner, category=PersonalLensCategory.PACKAGED_FOOD,
        )
    assert {fact.key for fact in (*skin.body_facts, *skin.preference_facts)} == set(SKIN_VALUES)
    assert food.body_facts == ()
    assert food.preference_facts == ()
    assert food.missing_information == ()
    assert food.status is PersonalLensStatus.NOT_ENOUGH_PERSONAL_CONTEXT
    projected = (*skin.body_facts, *skin.preference_facts)
    assert all(fact.key != "allergies" for fact in projected)
    assert all(fact.value != ("fragrance",) for fact in projected)


@pytest.mark.asyncio
async def test_explicit_unknown_is_preserved_but_not_counted_as_usable(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        owner, _ = await _create_profile(session, {
            "care_skin_usual_feel": "comfortable",
            "care_skin_sensitivity": "not_sure",
        })
        context = await build_personal_lens_context(
            session, account_id=owner, category=PersonalLensCategory.SKIN_CARE,
        )
    unknown = next(fact for fact in context.body_facts if fact.key == "care_skin_sensitivity")
    assert unknown.value == "not_sure"
    assert unknown.explicit_unknown is True
    missing = _missing(context, "care_skin_sensitivity")
    assert missing.kind is PersonalFactKind.BODY
    assert missing.reason is PersonalFactMissingReason.EXPLICIT_UNKNOWN
    assert context.status is PersonalLensStatus.PARTIAL_CONTEXT


@pytest.mark.asyncio
async def test_only_explicit_unknown_body_fact_is_not_enough_context(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        owner, _ = await _create_profile(session, {"care_skin_sensitivity": "not_sure"})
        context = await build_personal_lens_context(
            session, account_id=owner, category=PersonalLensCategory.SKIN_CARE,
        )
    assert context.status is PersonalLensStatus.NOT_ENOUGH_PERSONAL_CONTEXT


@pytest.mark.asyncio
async def test_missing_profile_is_normal_and_does_not_create_rows(db_clean):
    owner = uuid.uuid4()
    factory = get_sessionmaker()
    async with factory() as session:
        await identity_service.register_account(session, owner)
        await session.commit()
        context = await build_personal_lens_context(
            session, account_id=owner, category=PersonalLensCategory.SKIN_CARE,
        )
        counts = (
            await session.scalar(select(func.count()).select_from(AppearanceProfile)),
            await session.scalar(select(func.count()).select_from(ProfileAttribute)),
            await session.scalar(select(func.count()).select_from(ProfileChangeEvent)),
        )
    assert context.status is PersonalLensStatus.NOT_ENOUGH_PERSONAL_CONTEXT
    assert context.profile_id is None
    assert context.profile_version is None
    assert counts == (0, 0, 0)


@pytest.mark.asyncio
async def test_partial_and_complete_skin_context_are_deterministic(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        owner, profile = await _create_profile(
            session, {"care_skin_sensitivity": "rarely_reactive"},
        )
        partial = await build_personal_lens_context(
            session, account_id=owner, category=PersonalLensCategory.SKIN_CARE,
        )
        assert partial.status is PersonalLensStatus.PARTIAL_CONTEXT
        assert [(row.key, row.reason) for row in partial.missing_information if row.kind is PersonalFactKind.BODY] == [
            ("care_skin_usual_feel", PersonalFactMissingReason.MISSING),
        ]

        await profile_service.apply_attributes(
            session, profile, [{"key": "care_skin_usual_feel", "value": "comfortable"}],
        )
        await session.commit()
        complete = await build_personal_lens_context(
            session, account_id=owner, category=PersonalLensCategory.SKIN_CARE,
        )
    assert complete.status is PersonalLensStatus.CONTEXT_AVAILABLE
    assert tuple(fact.key for fact in complete.body_facts) == BODY_FACT_KEYS_BY_CATEGORY[PersonalLensCategory.SKIN_CARE]


@pytest.mark.asyncio
async def test_profile_version_is_live_provenance_without_personal_lens_persistence(db_clean):
    factory = get_sessionmaker()
    owner = uuid.uuid4()
    async with factory() as session:
        await identity_service.register_account(session, owner)
        profile = await profile_service.get_or_create_profile(session, owner)
        await profile_service.apply_attributes(
            session,
            profile,
            [{"key": "care_skin_sensitivity", "value": "sometimes_reactive"}],
        )
        await session.commit()
        first = await build_personal_lens_context(
            session, account_id=owner, category=PersonalLensCategory.SKIN_CARE,
        )
        first_version = first.profile_version

        await profile_service.apply_attributes(
            session,
            profile,
            [{"key": "care_skin_sensitivity", "value": "rarely_reactive"}],
        )
        await session.commit()
        second = await build_personal_lens_context(
            session, account_id=owner, category=PersonalLensCategory.SKIN_CARE,
        )
    assert first.profile_id == second.profile_id == profile.id
    assert first_version is not None
    assert second.profile_version == first_version + 1
    assert first.body_facts[0].value == "sometimes_reactive"
    assert second.body_facts[0].value == "rarely_reactive"


@pytest.mark.asyncio
async def test_query_budget_and_runtime_no_write_contract(db_clean):
    factory = get_sessionmaker()
    existing_owner = uuid.uuid4()
    no_profile_owner = uuid.uuid4()
    async with factory() as session:
        await identity_service.register_account(session, no_profile_owner)
        await _create_profile(session, SKIN_VALUES, account_id=existing_owner)
        await session.commit()

    engine = sql.get_engine().sync_engine
    cases = (
        (uuid.uuid4(), PersonalLensSafetyInput(text="I take metformin"), 0),
        (no_profile_owner, None, 1),
        (existing_owner, None, 2),
    )
    for owner, safety, expected_selects in cases:
        statements, record = _record_statements()
        async with factory() as session:
            event.listen(engine, "before_cursor_execute", record)
            try:
                await build_personal_lens_context(
                    session,
                    account_id=owner,
                    category=PersonalLensCategory.SKIN_CARE,
                    safety=safety,
                )
                assert not session.new and not session.dirty and not session.deleted
            finally:
                event.remove(engine, "before_cursor_execute", record)
        selects = [statement for statement in statements if statement.upper().startswith("SELECT")]
        mutations = [
            statement for statement in statements
            if statement.upper().startswith(("INSERT", "UPDATE", "DELETE"))
        ]
        assert len(selects) == expected_selects
        assert mutations == []


@pytest.mark.asyncio
async def test_safety_input_is_never_returned_or_persisted(db_clean):
    private_text = "I take novaformin-private-marker-8a"
    factory = get_sessionmaker()
    async with factory() as session:
        context = await build_personal_lens_context(
            session,
            account_id=uuid.uuid4(),
            category=PersonalLensCategory.HAIR_CARE,
            safety=PersonalLensSafetyInput(text=private_text),
        )
        counts = (
            await session.scalar(select(func.count()).select_from(AppearanceProfile)),
            await session.scalar(select(func.count()).select_from(ProfileAttribute)),
            await session.scalar(select(func.count()).select_from(ProfileChangeEvent)),
        )
    assert context.status is PersonalLensStatus.HANDOFF_REQUIRED
    assert private_text not in repr(context)
    assert "novaformin" not in repr(context)
    assert counts == (0, 0, 0)


@pytest.mark.asyncio
async def test_projected_values_and_context_contract_are_immutable(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        owner, _ = await _create_profile(session, HAIR_VALUES)
        context = await build_personal_lens_context(
            session, account_id=owner, category=PersonalLensCategory.HAIR_CARE,
        )
    processing = next(fact for fact in context.body_facts if fact.key == "care_hair_processing")
    assert processing.value == ("coloured",)
    with pytest.raises(dataclasses.FrozenInstanceError):
        context.status = PersonalLensStatus.PARTIAL_CONTEXT
    assert isinstance(service._freeze({"nested": ["value"]}), MappingProxyType)


def _package_of(path: Path) -> str:
    return ".".join(path.relative_to(BACKEND_ROOT).parts[:-1])


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = _package_of(path).split(".")
                base = base[: len(base) - (node.level - 1)] if node.level > 1 else base
                prefix = ".".join([*base, node.module] if node.module else base)
            else:
                prefix = node.module or ""
            if prefix:
                found.add(prefix)
                found.update(f"{prefix}.{alias.name}" for alias in node.names)
    return found


def test_domain_owns_only_the_deliberate_modules():
    assert {path.name for path in DOMAIN_DIR.glob("*.py")} == {
        "__init__.py", "enums.py", "service.py",
    }


@pytest.mark.parametrize("path", sorted(DOMAIN_DIR.glob("*.py")), ids=lambda path: path.name)
def test_personal_lens_imports_only_intentional_dependencies(path):
    allowed = (
        "app.domains.personal_lens",
        "app.domains.profile",
        "app.domains.routines.hard_handoff",
        "sqlalchemy",
        "dataclasses",
        "enum",
        "typing",
        "uuid",
        "datetime",
        "types",
        "__future__",
    )
    for module in _imported_modules(path):
        assert any(module == prefix or module.startswith(f"{prefix}.") for prefix in allowed), (
            f"{path.name} imports {module}"
        )


@pytest.mark.parametrize("path", sorted(DOMAIN_DIR.glob("*.py")), ids=lambda path: path.name)
def test_personal_lens_never_imports_product_evidence_or_decision_domains(path):
    imported = _imported_modules(path)
    forbidden = (
        "app.domains.substance_interpretation",
        "app.domains.substances",
        "app.domains.formulas",
        "app.domains.product",
        "app.domains.off",
        "app.domains.ai_gateway",
        "app.domains.family",
        "app.domains.purchase",
        "app.domains.recommendation",
        "app.domains.supplements",
        "httpx",
        "requests",
        "aiohttp",
        "urllib",
        "socket",
    )
    for prefix in forbidden:
        assert not any(module == prefix or module.startswith(f"{prefix}.") for module in imported)


def test_runtime_has_no_write_or_product_decision_api():
    body = inspect.getsource(service)
    for forbidden in (
        "session.add",
        "session.flush",
        "session.commit",
        "session.delete",
        "get_or_create_profile",
        "personal_score",
        "for_you_score",
    ):
        assert forbidden not in body
    fields = set(service.PersonalLensContext.__dataclass_fields__)
    assert fields.isdisjoint({"score", "grade", "verdict", "recommendation", "risk", "benefit"})
