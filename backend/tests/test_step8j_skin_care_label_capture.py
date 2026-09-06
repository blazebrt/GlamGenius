"""Step 8J — confirmed skin-care label capture, and the category bound to it.

The milestone builds the physical bridge between a phone camera and the
governed Step 7/8 chain: photograph, transcribe visible facts, review, confirm,
and persist one category-bound ``LabelSnapshot``. Nothing here decides
anything, and a large part of this module exists to prove that.

The decisive integration test mocks only the model boundary. Everything after
the gateway result is real — the persisted ``AIRun``/``AIRunOutput`` pair, the
confirmation route, the ``ScanEvent``, the ``LabelSnapshot``, the pack context,
and the Step 8B projection — because the bridge itself is the thing this
milestone has to prove, and a hand-built snapshot would prove only that the
test author can build one.
"""

from __future__ import annotations

import ast
import uuid
from pathlib import Path
from typing import Any

import pytest
from app.bootstrap import run as run_reference_seed
from app.domains.ai_gateway.gateway import AIResult
from app.domains.ai_gateway.models import (
    AI_STATUS_FAILED,
    AI_STATUS_SUCCEEDED,
    VERIFICATION_USER_CONFIRMED,
    AIRun,
    AIRunOutput,
)
from app.domains.evidence.models import EvidenceClaim
from app.domains.personal_applicability.enums import PersonalApplicabilityCategory
from app.domains.personal_applicability.service import interpret_label_snapshot_for_account
from app.domains.personal_decision_explanation.rules import PERSONAL_DECISION_EXPLANATION_RULES
from app.domains.personal_decision_policy.rules import PERSONAL_DECISION_POLICY_RULES
from app.domains.personal_decision_release.models import PersonalDecisionRelease
from app.domains.personal_decision_semantics.rules import PERSONAL_DECISION_SEMANTIC_RULES
from app.domains.product import care_capture, care_extraction, pack_context, service
from app.domains.product.care_extraction import ExtractedSkinCareLabel
from app.domains.product.models import LabelSnapshot, ProductRecord, ScanDevice, ScanEvent
from app.domains.profile.models import AppearanceProfile, ProfileAttribute
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import func, select

from tests.conftest import auth, png_bytes

BACKEND_ROOT = Path(__file__).resolve().parents[1]
EXTRACTION_PATH = BACKEND_ROOT / "app" / "domains" / "product" / "care_extraction.py"
CAPTURE_PATH = BACKEND_ROOT / "app" / "domains" / "product" / "care_capture.py"
API_PATH = BACKEND_ROOT / "app" / "api" / "v2" / "skin_care_scan.py"
STEP8J_MODULES = (EXTRACTION_PATH, CAPTURE_PATH, API_PATH)

BARCODE = "8901030000011"
OTHER_BARCODE = "8901030000028"

TRANSCRIBE_URL = "/api/v2/scan/skin-care/label/transcribe"
CONFIRM_URL = "/api/v2/scan/skin-care/label/confirm"
FOOD_CONFIRM_URL = "/api/v2/scan/label/confirm"

#: The synthetic pack every positive test photographs.
PACK = {
    "product_name": "Synthetic Petrolatum Ointment",
    "brand": "Synthetic Brand",
    "ingredients_text": "Petrolatum",
}


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------
@pytest.fixture
def payload() -> dict[str, Any]:
    """A model payload at the production skin-care schema."""
    return {**PACK, "product_type": "Ointment", "confidence": 0.91, "uncertain_fields": []}


async def _register_device(app_client) -> tuple[dict[str, str], uuid.UUID]:
    response = await app_client.post(
        "/api/v2/scan/device", json={"device_key": uuid.uuid4().hex, "platform": "android"},
    )
    assert response.status_code == 201, response.text
    return {"X-Device-Token": response.json()["token"]}, uuid.UUID(response.json()["device_id"])


async def _claim(app_client, headers: dict[str, str], token: str) -> None:
    claimed = await app_client.post(
        "/api/v2/scan/device/claim", headers={**headers, **auth(token)},
    )
    assert claimed.status_code == 200, claimed.text


async def _seed_run(
    payload: dict[str, Any],
    account_id: uuid.UUID | None,
    *,
    feature: str = care_extraction.FEATURE,
    run_schema: str = care_extraction.SCHEMA_VERSION,
    output_schema: str | None = None,
    status: str = AI_STATUS_SUCCEEDED,
    validation_passed: bool = True,
) -> uuid.UUID:
    """One stored transcription, exactly as the gateway would have written it."""
    factory = get_sessionmaker()
    async with factory() as session:
        run = AIRun(
            account_id=account_id, feature=feature, provider="test", model="test-model",
            prompt_version=care_extraction.PROMPT_VERSION, schema_version=run_schema,
            status=status, validation_passed=validation_passed,
        )
        session.add(run)
        await session.flush()
        session.add(AIRunOutput(
            ai_run_id=run.id, schema_version=output_schema or run_schema, payload=payload,
        ))
        await session.commit()
        return run.id


async def _confirm(app_client, headers, token, run_id, *, barcode=BARCODE, client_scan_id=None):
    return await app_client.post(
        CONFIRM_URL,
        headers={**headers, **auth(token)},
        json={
            "barcode": barcode,
            "ai_run_id": str(run_id),
            "client_scan_id": client_scan_id or uuid.uuid4().hex,
        },
    )


async def _capture(app_client, registered_supabase_user, payload, *, barcode=BARCODE):
    """A whole real capture: account, claimed device, stored run, confirmation."""
    token, account_id = await registered_supabase_user()
    headers, device_id = await _register_device(app_client)
    await _claim(app_client, headers, token)
    run_id = await _seed_run(payload, account_id)
    response = await _confirm(app_client, headers, token, run_id, barcode=barcode)
    assert response.status_code == 201, response.text
    return {
        "token": token, "account_id": account_id, "headers": headers,
        "device_id": device_id, "run_id": run_id, "body": response.json(),
    }


def _fake_result(data: ExtractedSkinCareLabel) -> AIResult[ExtractedSkinCareLabel]:
    return AIResult(
        data=data, run_id=uuid.uuid4(), provider="test", model="test-model",
        prompt_version=care_extraction.PROMPT_VERSION,
        schema_version=care_extraction.SCHEMA_VERSION,
        confidence=data.confidence, latency_ms=11, estimated_cost_usd=None,
    )


async def _upload(app_client, token) -> str:
    asset = await app_client.post(
        "/api/v2/media/upload", headers=auth(token),
        files={"file": ("label.png", png_bytes(), "image/png")},
    )
    assert asset.status_code in (200, 201), asset.text
    return asset.json()["id"]


def _module_tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _imported_modules(path: Path) -> set[str]:
    """Every module name this file imports, however it phrases the import."""
    names: set[str] = set()
    for node in ast.walk(_module_tree(path)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def _pydantic_model_fields(path: Path) -> dict[str, set[str]]:
    """Every field declared on every class in one module, by class name.

    Read from the source rather than from the imported classes so an inherited
    or dynamically added field cannot hide from the guard behind a base class
    this test did not think to check.
    """
    fields: dict[str, set[str]] = {}
    for node in ast.walk(_module_tree(path)):
        if not isinstance(node, ast.ClassDef):
            continue
        declared = {
            statement.target.id
            for statement in node.body
            if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name)
        }
        fields[node.name] = declared
    return fields


# ---------------------------------------------------------------------------
# 1. Transcription — a draft, and only a draft
# ---------------------------------------------------------------------------
class TestTranscription:
    """The model reads a label. Nothing is written and nothing is decided."""

    async def _transcribe(self, app_client, monkeypatch, token, data: dict[str, Any]):
        asset_id = await _upload(app_client, token)

        async def fake_run(**kwargs):
            return _fake_result(kwargs["schema"](**data))

        monkeypatch.setattr("app.domains.ai_gateway.gateway.run_structured", fake_run)
        return await app_client.post(
            TRANSCRIBE_URL, headers=auth(token),
            json={"barcode": BARCODE, "media_asset_id": asset_id},
        )

    async def test_a_readable_label_returns_a_draft_and_stores_nothing(
        self, db_clean, app_client, registered_supabase_user, media_root, monkeypatch,
    ):
        token, _ = await registered_supabase_user()
        response = await self._transcribe(app_client, monkeypatch, token, {
            **PACK, "product_type": "Ointment", "confidence": 0.9,
        })
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["stored"] is False
        assert body["ingredients_readable"] is True
        assert body["message"] is None
        assert body["facts"] == {**PACK, "product_type": "Ointment"}
        assert body["provenance"]["schema_version"] == care_extraction.SCHEMA_VERSION

        factory = get_sessionmaker()
        async with factory() as session:
            for model in (ScanEvent, LabelSnapshot, ProductRecord):
                assert (await session.execute(
                    select(func.count()).select_from(model)
                )).scalar_one() == 0

    async def test_a_draft_survives_a_missing_product_name(
        self, db_clean, app_client, registered_supabase_user, media_root, monkeypatch,
    ):
        token, _ = await registered_supabase_user()
        response = await self._transcribe(app_client, monkeypatch, token, {
            "ingredients_text": "Petrolatum, Glycerin",
        })
        assert response.status_code == 200, response.text
        body = response.json()
        assert "product_name" not in body["facts"]
        assert body["ingredients_readable"] is True

    async def test_a_draft_survives_a_missing_brand(
        self, db_clean, app_client, registered_supabase_user, media_root, monkeypatch,
    ):
        token, _ = await registered_supabase_user()
        response = await self._transcribe(app_client, monkeypatch, token, {
            "product_name": "Synthetic Petrolatum Ointment", "ingredients_text": "Petrolatum",
        })
        assert response.status_code == 200, response.text
        assert "brand" not in response.json()["facts"]

    async def test_an_unreadable_ingredient_list_is_still_a_draft_and_says_so(
        self, db_clean, app_client, registered_supabase_user, media_root, monkeypatch,
    ):
        """The person tried. Telling them so beats an error they cannot act on."""
        token, _ = await registered_supabase_user()
        response = await self._transcribe(app_client, monkeypatch, token, {
            "product_name": "Synthetic Petrolatum Ointment",
            "uncertain_fields": ["ingredients_text"],
            "photo_quality_notes": "The back of the tube is out of focus.",
        })
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["ingredients_readable"] is False
        assert body["message"] == care_capture.UNREADABLE_INGREDIENTS_MESSAGE
        assert body["capture_quality"]["uncertain_fields"] == ["ingredients_text"]

    async def test_a_blank_ingredient_string_is_not_readable(self):
        assert care_capture.has_readable_ingredients(
            ExtractedSkinCareLabel(ingredients_text="   ")
        ) is False
        assert care_capture.has_readable_ingredients(
            ExtractedSkinCareLabel(ingredients_text="Petrolatum")
        ) is True

    async def test_an_extra_model_field_fails_schema_validation(self):
        with pytest.raises(Exception):
            ExtractedSkinCareLabel.model_validate({
                "ingredients_text": "Petrolatum", "verdict": "buy",
            })

    async def test_transcription_needs_an_account(self, db_clean, app_client):
        headers, _ = await _register_device(app_client)
        response = await app_client.post(
            TRANSCRIBE_URL, headers=headers,
            json={"barcode": BARCODE, "media_asset_id": str(uuid.uuid4())},
        )
        assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# 2. Confirmation revalidates the stored run, and fails closed
# ---------------------------------------------------------------------------
class TestConfirmationAuthority:
    """Nothing in the request is trusted. Everything is re-read and re-checked."""

    async def test_a_missing_ingredient_list_cannot_be_confirmed(
        self, db_clean, app_client, registered_supabase_user,
    ):
        token, account_id = await registered_supabase_user()
        headers, _ = await _register_device(app_client)
        await _claim(app_client, headers, token)
        run_id = await _seed_run({"product_name": "Synthetic Petrolatum Ointment"}, account_id)
        response = await _confirm(app_client, headers, token, run_id)
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["field"] == "ingredients_text"

        factory = get_sessionmaker()
        async with factory() as session:
            assert (await session.execute(
                select(func.count()).select_from(ScanEvent)
            )).scalar_one() == 0

    async def test_a_blank_ingredient_list_cannot_be_confirmed(
        self, db_clean, app_client, registered_supabase_user, payload,
    ):
        token, account_id = await registered_supabase_user()
        headers, _ = await _register_device(app_client)
        await _claim(app_client, headers, token)
        run_id = await _seed_run({**payload, "ingredients_text": "  \n "}, account_id)
        assert (await _confirm(app_client, headers, token, run_id)).status_code == 422

    async def test_confirmation_needs_an_account(self, db_clean, app_client, payload):
        headers, _ = await _register_device(app_client)
        response = await app_client.post(
            CONFIRM_URL, headers=headers,
            json={
                "barcode": BARCODE, "ai_run_id": str(uuid.uuid4()),
                "client_scan_id": uuid.uuid4().hex,
            },
        )
        assert response.status_code in (401, 403)

    async def test_confirmation_needs_a_device_token(
        self, db_clean, app_client, registered_supabase_user, payload,
    ):
        token, account_id = await registered_supabase_user()
        run_id = await _seed_run(payload, account_id)
        response = await app_client.post(
            CONFIRM_URL, headers=auth(token),
            json={
                "barcode": BARCODE, "ai_run_id": str(run_id),
                "client_scan_id": uuid.uuid4().hex,
            },
        )
        assert response.status_code == 401, response.text
        assert response.json()["detail"]["code"] == "DEVICE_UNKNOWN"

    async def test_an_unclaimed_device_is_refused_and_not_auto_claimed(
        self, db_clean, app_client, registered_supabase_user, payload,
    ):
        """Silently attaching a borrowed handset to whoever confirmed on it first
        is how a shared phone acquires the wrong owner."""
        token, account_id = await registered_supabase_user()
        headers, device_id = await _register_device(app_client)
        run_id = await _seed_run(payload, account_id)
        response = await _confirm(app_client, headers, token, run_id)
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["reason"] == "device_unclaimed"

        factory = get_sessionmaker()
        async with factory() as session:
            device = await session.get(ScanDevice, device_id)
            assert device.claimed_by_account_id is None

    async def test_a_device_claimed_by_someone_else_is_refused(
        self, db_clean, app_client, registered_supabase_user, payload,
    ):
        owner_token, _ = await registered_supabase_user()
        headers, _ = await _register_device(app_client)
        await _claim(app_client, headers, owner_token)

        stranger_token, stranger_id = await registered_supabase_user()
        run_id = await _seed_run(payload, stranger_id)
        response = await _confirm(app_client, headers, stranger_token, run_id)
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["reason"] == "device_claimed_by_another_account"

    async def test_a_run_belonging_to_another_account_is_refused(
        self, db_clean, app_client, registered_supabase_user, payload,
    ):
        token, _ = await registered_supabase_user()
        _, stranger_id = await registered_supabase_user()
        headers, _ = await _register_device(app_client)
        await _claim(app_client, headers, token)
        run_id = await _seed_run(payload, stranger_id)
        assert (await _confirm(app_client, headers, token, run_id)).status_code == 422

    async def test_a_failed_run_is_refused(
        self, db_clean, app_client, registered_supabase_user, payload,
    ):
        token, account_id = await registered_supabase_user()
        headers, _ = await _register_device(app_client)
        await _claim(app_client, headers, token)
        run_id = await _seed_run(
            payload, account_id, status=AI_STATUS_FAILED, validation_passed=False,
        )
        assert (await _confirm(app_client, headers, token, run_id)).status_code == 422

    async def test_a_run_that_did_not_pass_validation_is_refused(
        self, db_clean, app_client, registered_supabase_user, payload,
    ):
        token, account_id = await registered_supabase_user()
        headers, _ = await _register_device(app_client)
        await _claim(app_client, headers, token)
        run_id = await _seed_run(payload, account_id, validation_passed=False)
        assert (await _confirm(app_client, headers, token, run_id)).status_code == 422

    async def test_a_run_from_another_feature_is_refused(
        self, db_clean, app_client, registered_supabase_user, payload,
    ):
        token, account_id = await registered_supabase_user()
        headers, _ = await _register_device(app_client)
        await _claim(app_client, headers, token)
        run_id = await _seed_run(payload, account_id, feature="product_label_transcribe")
        assert (await _confirm(app_client, headers, token, run_id)).status_code == 422

    async def test_a_run_and_output_disagreeing_on_schema_are_refused(
        self, db_clean, app_client, registered_supabase_user, payload,
    ):
        token, account_id = await registered_supabase_user()
        headers, _ = await _register_device(app_client)
        await _claim(app_client, headers, token)
        run_id = await _seed_run(payload, account_id, output_schema="skin-care-label.v0")
        assert (await _confirm(app_client, headers, token, run_id)).status_code == 422

    async def test_an_unconfirmable_schema_is_refused(
        self, db_clean, app_client, registered_supabase_user, payload,
    ):
        token, account_id = await registered_supabase_user()
        headers, _ = await _register_device(app_client)
        await _claim(app_client, headers, token)
        run_id = await _seed_run(
            payload, account_id, run_schema="skin-care-label.v9",
            output_schema="skin-care-label.v9",
        )
        assert (await _confirm(app_client, headers, token, run_id)).status_code == 422

    async def test_a_corrupted_stored_payload_is_refused(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Direct database corruption fails closed rather than becoming a pack fact."""
        token, account_id = await registered_supabase_user()
        headers, _ = await _register_device(app_client)
        await _claim(app_client, headers, token)
        run_id = await _seed_run(
            {"ingredients_text": "Petrolatum", "verdict": "buy"}, account_id,
        )
        assert (await _confirm(app_client, headers, token, run_id)).status_code == 422

    async def test_a_missing_run_is_refused(
        self, db_clean, app_client, registered_supabase_user,
    ):
        token, _ = await registered_supabase_user()
        headers, _ = await _register_device(app_client)
        await _claim(app_client, headers, token)
        assert (await _confirm(app_client, headers, token, uuid.uuid4())).status_code == 422

    async def test_the_confirmation_body_refuses_a_category(
        self, db_clean, app_client, registered_supabase_user, payload,
    ):
        """The one thing a client must never be able to say."""
        token, account_id = await registered_supabase_user()
        headers, _ = await _register_device(app_client)
        await _claim(app_client, headers, token)
        run_id = await _seed_run(payload, account_id)
        for extra in (
            {"category": "hair_care"},
            {"product_category": "hair_care"},
            {"decision_category": "hair_care"},
            {"ingredients_text": "Dimethicone"},
        ):
            response = await app_client.post(
                CONFIRM_URL, headers={**headers, **auth(token)},
                json={
                    "barcode": BARCODE, "ai_run_id": str(run_id),
                    "client_scan_id": uuid.uuid4().hex, **extra,
                },
            )
            assert response.status_code == 422, (extra, response.text)


# ---------------------------------------------------------------------------
# 3. The confirmed capture itself
# ---------------------------------------------------------------------------
class TestConfirmedCapture:
    async def test_one_confirmation_writes_one_categorised_pack_label(
        self, db_clean, app_client, registered_supabase_user, payload,
    ):
        captured = await _capture(app_client, registered_supabase_user, payload)
        body = captured["body"]
        assert body["created"] is True
        assert body["barcode"] == BARCODE
        assert body["product_category"] == "skin_care"
        assert body["label_snapshot"]["version_number"] == 1

        factory = get_sessionmaker()
        async with factory() as session:
            events = (await session.execute(select(ScanEvent))).scalars().all()
            assert len(events) == 1
            event = events[0]
            assert event.outcome == service.OUTCOME_LABEL
            assert event.account_id == captured["account_id"]
            assert event.device_id == captured["device_id"]
            assert event.ai_run_id == captured["run_id"]

            snapshots = (await session.execute(select(LabelSnapshot))).scalars().all()
            assert len(snapshots) == 1
            snapshot = snapshots[0]
            assert snapshot.scan_event_id == event.id
            assert snapshot.facts == {
                "product_name": "Synthetic Petrolatum Ointment",
                "brand": "Synthetic Brand",
                "ingredients_text": "Petrolatum",
                "product_category": "skin_care",
            }
            assert str(snapshot.id) == body["label_snapshot"]["id"]
            assert snapshot.content_fingerprint == body["label_snapshot"]["content_fingerprint"]

            output = (await session.execute(
                select(AIRunOutput).where(AIRunOutput.ai_run_id == captured["run_id"])
            )).scalar_one()
            assert output.verification_status == VERIFICATION_USER_CONFIRMED

    async def test_the_model_hesitancy_fields_are_never_persisted(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """A canonical label version is a statement about the pack, not the photo."""
        await _capture(app_client, registered_supabase_user, {
            **PACK, "product_type": "Ointment", "confidence": 0.4,
            "uncertain_fields": ["brand"],
            "photo_quality_notes": "Slight glare on the tube.",
        })
        factory = get_sessionmaker()
        async with factory() as session:
            snapshot = (await session.execute(select(LabelSnapshot))).scalar_one()
        for forbidden in ("confidence", "uncertain_fields", "photo_quality_notes", "product_type"):
            assert forbidden not in snapshot.facts

    async def test_an_unread_brand_stays_absent_rather_than_empty(
        self, db_clean, app_client, registered_supabase_user,
    ):
        await _capture(app_client, registered_supabase_user, {
            "product_name": "Synthetic Petrolatum Ointment", "ingredients_text": "Petrolatum",
        })
        factory = get_sessionmaker()
        async with factory() as session:
            snapshot = (await session.execute(select(LabelSnapshot))).scalar_one()
        assert "brand" not in snapshot.facts
        assert snapshot.facts["product_category"] == "skin_care"

    async def test_the_confirmation_result_carries_no_personal_decision(
        self, db_clean, app_client, registered_supabase_user, payload,
    ):
        """Capture makes the input trustworthy. It does not answer the question."""
        body = (await _capture(app_client, registered_supabase_user, payload))["body"]
        rendered = repr(body).lower()
        for word in ("buy", "wait", "skip", "verdict", "action", "reason_key", "release"):
            assert word not in rendered


# ---------------------------------------------------------------------------
# 4. The category is part of what a label version *is*
# ---------------------------------------------------------------------------
class TestCategoryFingerprint:
    def test_the_same_formula_under_two_categories_is_two_label_versions(self):
        skin = {"ingredients_text": "Petrolatum", "product_category": "skin_care"}
        hair = {"ingredients_text": "Petrolatum", "product_category": "hair_care"}
        assert service.label_content_fingerprint(skin) != service.label_content_fingerprint(hair)

    def test_identical_facts_fingerprint_identically(self):
        facts = {"ingredients_text": "Petrolatum", "product_category": "skin_care"}
        assert service.label_content_fingerprint(facts) == service.label_content_fingerprint(dict(facts))

    def test_a_category_change_is_reported_as_a_changed_field(self):
        skin = {"ingredients_text": "Petrolatum", "product_category": "skin_care"}
        hair = {"ingredients_text": "Petrolatum", "product_category": "hair_care"}
        assert service.label_changed_fields(skin, hair) == ["product_category"]

    def test_the_category_is_a_canonical_content_field(self):
        assert "product_category" in service.CONTENT_FACT_FIELDS

    def test_legacy_food_facts_gain_no_category_and_keep_their_fingerprint(self):
        """Existing food snapshots are not rewritten, backfilled, or reinterpreted."""
        food = {
            "product_name": "Regional namkeen",
            "brand": "Acme",
            "ingredients_text": "besan, edible oil, salt",
            "nutrition_per_100g": {"energy_kcal": "520"},
            "nutrition_basis": "per_100g",
        }
        canonical = service.canonical_label_facts(food)
        assert "product_category" not in canonical
        # Deterministic, and identical to the value the field's absence produces
        # on a dict that has never heard of it.
        assert service.label_content_fingerprint(food) == service.label_content_fingerprint(dict(food))
        assert service.label_changed_fields(food, dict(food)) == []


# ---------------------------------------------------------------------------
# 5. Idempotency: a replay, and never a disguise
# ---------------------------------------------------------------------------
class TestIdempotency:
    async def _setup(self, app_client, registered_supabase_user, payload):
        token, account_id = await registered_supabase_user()
        headers, _ = await _register_device(app_client)
        await _claim(app_client, headers, token)
        run_id = await _seed_run(payload, account_id)
        key = uuid.uuid4().hex
        first = await _confirm(app_client, headers, token, run_id, client_scan_id=key)
        assert first.status_code == 201, first.text
        return token, account_id, headers, run_id, key, first.json()

    async def test_an_exact_replay_changes_nothing(
        self, db_clean, app_client, registered_supabase_user, payload,
    ):
        token, _, headers, run_id, key, first = await self._setup(
            app_client, registered_supabase_user, payload,
        )
        replay = await _confirm(app_client, headers, token, run_id, client_scan_id=key)
        assert replay.status_code == 201, replay.text
        body = replay.json()
        assert body["created"] is False
        assert body["scan_id"] == first["scan_id"]
        assert body["label_snapshot"] == first["label_snapshot"]
        assert body["confirmations"] == first["confirmations"]
        assert body["product_category"] == "skin_care"

        factory = get_sessionmaker()
        async with factory() as session:
            assert (await session.execute(
                select(func.count()).select_from(ScanEvent)
            )).scalar_one() == 1
            assert (await session.execute(
                select(func.count()).select_from(LabelSnapshot)
            )).scalar_one() == 1
            record = (await session.execute(select(ProductRecord))).scalar_one()
            assert record.confirmation_count == first["confirmations"]

    async def test_the_same_key_with_a_different_barcode_conflicts(
        self, db_clean, app_client, registered_supabase_user, payload,
    ):
        token, _, headers, run_id, key, _ = await self._setup(
            app_client, registered_supabase_user, payload,
        )
        response = await _confirm(
            app_client, headers, token, run_id, barcode=OTHER_BARCODE, client_scan_id=key,
        )
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["conflicting_field"] == "barcode"

    async def test_the_same_key_with_a_different_run_conflicts(
        self, db_clean, app_client, registered_supabase_user, payload,
    ):
        token, account_id, headers, _, key, _ = await self._setup(
            app_client, registered_supabase_user, payload,
        )
        second_run = await _seed_run(payload, account_id)
        response = await _confirm(app_client, headers, token, second_run, client_scan_id=key)
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["conflicting_field"] == "ai_run_id"

    async def test_the_same_key_with_different_facts_conflicts(
        self, db_clean, app_client, registered_supabase_user, payload,
    ):
        """No idempotency key may conceal changed physical evidence."""
        token, account_id, headers, run_id, key, _ = await self._setup(
            app_client, registered_supabase_user, payload,
        )
        factory = get_sessionmaker()
        async with factory() as session:
            event = (await session.execute(select(ScanEvent))).scalar_one()
            event.label_facts = {**event.label_facts, "ingredients_text": "Petrolatum, Glycerin"}
            await session.commit()
        response = await _confirm(app_client, headers, token, run_id, client_scan_id=key)
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["conflicting_field"] == "label_facts"


# ---------------------------------------------------------------------------
# 6. Versioning
# ---------------------------------------------------------------------------
class TestVersioning:
    async def test_the_same_content_twice_is_one_semantic_version(
        self, db_clean, app_client, registered_supabase_user, payload,
    ):
        captured = await _capture(app_client, registered_supabase_user, payload)
        second_run = await _seed_run(payload, captured["account_id"])
        again = await _confirm(app_client, captured["headers"], captured["token"], second_run)
        assert again.status_code == 201, again.text
        assert again.json()["label_snapshot"]["version_number"] == 1

        factory = get_sessionmaker()
        async with factory() as session:
            assert (await session.execute(
                select(func.count()).select_from(LabelSnapshot)
            )).scalar_one() == 1

    async def test_changed_ingredients_create_a_new_version(
        self, db_clean, app_client, registered_supabase_user, payload,
    ):
        captured = await _capture(app_client, registered_supabase_user, payload)
        changed = await _seed_run(
            {**payload, "ingredients_text": "Petrolatum, Glycerin"}, captured["account_id"],
        )
        response = await _confirm(app_client, captured["headers"], captured["token"], changed)
        assert response.status_code == 201, response.text
        assert response.json()["label_snapshot"]["version_number"] == 2

        factory = get_sessionmaker()
        async with factory() as session:
            latest = await service.latest_label_snapshot(session, BARCODE)
        assert latest.changed_fields == ["ingredients"]

    async def test_a_category_change_versions_at_the_persistence_layer(self, db_clean):
        """Not reachable through the V1 API. Proven where the identity is decided."""
        first = {"ingredients_text": "Petrolatum", "product_category": "skin_care"}
        second = {"ingredients_text": "Petrolatum", "product_category": "hair_care"}
        factory = get_sessionmaker()
        async with factory() as session:
            event, _ = await service.record_scan(
                session, barcode=BARCODE, outcome=service.OUTCOME_LABEL,
                client_scan_id=uuid.uuid4().hex, ai_run_id=None,
            )
            one = await service.store_label_snapshot(
                session, barcode=BARCODE, facts=first, device_id=None, scan_event_id=event.id,
            )
            other, _ = await service.record_scan(
                session, barcode=BARCODE, outcome=service.OUTCOME_LABEL,
                client_scan_id=uuid.uuid4().hex, ai_run_id=None,
            )
            two = await service.store_label_snapshot(
                session, barcode=BARCODE, facts=second, device_id=None, scan_event_id=other.id,
            )
            await session.commit()
        assert one.content_fingerprint != two.content_fingerprint
        assert two.version_number == 2
        assert two.changed_fields == ["product_category"]


# ---------------------------------------------------------------------------
# 7. Physical pack authority
# ---------------------------------------------------------------------------
class TestCurrentPack:
    async def test_a_confirmed_skin_care_capture_proves_this_pack(
        self, db_clean, app_client, registered_supabase_user, payload,
    ):
        captured = await _capture(app_client, registered_supabase_user, payload)
        factory = get_sessionmaker()
        async with factory() as session:
            pack = await pack_context.current_pack(
                session, barcode=BARCODE, device_id=captured["device_id"],
            )
        assert pack.is_proven is True
        assert pack.label_facts["product_category"] == "skin_care"
        assert pack.label_facts["ingredients_text"] == "Petrolatum"

    async def test_a_later_plain_scan_withdraws_pack_authority_and_a_new_capture_restores_it(
        self, db_clean, off_clean, app_client, registered_supabase_user, payload,
    ):
        """A plain scan means a different packet is in the hand now. Never reach back."""
        captured = await _capture(app_client, registered_supabase_user, payload)
        plain = await app_client.post(
            "/api/v2/scan/events", headers=captured["headers"],
            json={"barcode": BARCODE, "client_scan_id": uuid.uuid4().hex},
        )
        assert plain.status_code == 201, plain.text

        factory = get_sessionmaker()
        async with factory() as session:
            pack = await pack_context.current_pack(
                session, barcode=BARCODE, device_id=captured["device_id"],
            )
        assert pack.has_scan is True
        assert pack.is_proven is False

        again = await _seed_run(payload, captured["account_id"])
        response = await _confirm(app_client, captured["headers"], captured["token"], again)
        assert response.status_code == 201, response.text
        async with factory() as session:
            pack = await pack_context.current_pack(
                session, barcode=BARCODE, device_id=captured["device_id"],
            )
        assert pack.is_proven is True

    async def test_a_stranger_capture_never_becomes_another_devices_pack(
        self, db_clean, app_client, registered_supabase_user, payload,
    ):
        """A global snapshot answers a question about the product, not about a hand."""
        await _capture(app_client, registered_supabase_user, payload)
        onlooker_headers, onlooker_device = await _register_device(app_client)

        factory = get_sessionmaker()
        async with factory() as session:
            assert await service.latest_label_snapshot(session, BARCODE) is not None
            pack = await pack_context.current_pack(
                session, barcode=BARCODE, device_id=onlooker_device,
            )
        assert pack.is_proven is False
        assert pack.label_facts is None

    async def test_a_forged_capture_without_a_run_proves_nothing(self, db_clean):
        """Populated facts and the right outcome are not provenance."""
        factory = get_sessionmaker()
        async with factory() as session:
            device = ScanDevice(device_key=uuid.uuid4().hex, token_hash="x" * 64)
            session.add(device)
            await session.flush()
            session.add(ScanEvent(
                device_id=device.id, barcode=BARCODE, outcome=service.OUTCOME_LABEL,
                client_scan_id=uuid.uuid4().hex,
                label_facts={"ingredients_text": "Petrolatum", "product_category": "skin_care"},
                ai_run_id=None,
            ))
            await session.commit()
            pack = await pack_context.current_pack(
                session, barcode=BARCODE, device_id=device.id,
            )
        assert pack.is_proven is False
        assert pack.label_facts is None

    async def test_a_category_alone_is_not_confirmation_provenance(self):
        forged = ScanEvent(
            barcode=BARCODE, outcome=service.OUTCOME_LABEL, client_scan_id="x" * 8,
            label_facts={"product_category": "skin_care"}, ai_run_id=None,
        )
        assert pack_context.is_confirmed_label_capture(forged) is False


# ---------------------------------------------------------------------------
# 8. Food capture is untouched, and the two cannot cross-confirm
# ---------------------------------------------------------------------------
class TestFoodRegression:
    async def test_food_transcription_and_confirmation_still_work(
        self, db_clean, off_clean, app_client, registered_supabase_user, media_root, monkeypatch,
    ):
        from app.domains.product import extraction as food_extraction

        token, account_id = await registered_supabase_user()
        headers, _ = await _register_device(app_client)
        asset_id = await _upload(app_client, token)

        async def fake_run(**kwargs):
            data = kwargs["schema"](
                product_name="Regional namkeen",
                ingredients_text="besan, edible oil, salt",
                nutrition_per_100g={"energy_kcal": "520"},
                nutrition_basis="per_100g",
            )
            return AIResult(
                data=data, run_id=uuid.uuid4(), provider="test", model="test-model",
                prompt_version=food_extraction.PROMPT_VERSION,
                schema_version=food_extraction.SCHEMA_VERSION,
                confidence=None, latency_ms=9, estimated_cost_usd=None,
            )

        monkeypatch.setattr("app.domains.ai_gateway.gateway.run_structured", fake_run)
        draft = await app_client.post(
            "/api/v2/scan/label/transcribe", headers=auth(token),
            json={"barcode": OTHER_BARCODE, "media_asset_id": asset_id},
        )
        assert draft.status_code == 200, draft.text
        assert draft.json()["stored"] is False

        factory = get_sessionmaker()
        async with factory() as session:
            run = AIRun(
                account_id=account_id, feature=food_extraction.FEATURE, provider="test",
                model="test-model", prompt_version=food_extraction.PROMPT_VERSION,
                schema_version=food_extraction.SCHEMA_VERSION,
                status=AI_STATUS_SUCCEEDED, validation_passed=True,
            )
            session.add(run)
            await session.flush()
            session.add(AIRunOutput(
                ai_run_id=run.id, schema_version=food_extraction.SCHEMA_VERSION,
                payload={"product_name": "Regional namkeen", "ingredients_text": "besan, salt"},
            ))
            await session.commit()
            food_run = run.id

        confirmed = await app_client.post(
            FOOD_CONFIRM_URL, headers={**headers, **auth(token)},
            json={
                "barcode": OTHER_BARCODE, "ai_run_id": str(food_run),
                "client_scan_id": uuid.uuid4().hex,
            },
        )
        assert confirmed.status_code == 201, confirmed.text

        async with factory() as session:
            snapshot = await service.latest_label_snapshot(session, OTHER_BARCODE)
        assert "product_category" not in snapshot.facts

    async def test_a_food_run_cannot_confirm_a_skin_care_pack(
        self, db_clean, app_client, registered_supabase_user,
    ):
        token, account_id = await registered_supabase_user()
        headers, _ = await _register_device(app_client)
        await _claim(app_client, headers, token)
        run_id = await _seed_run(
            {"product_name": "Regional namkeen", "ingredients_text": "besan, salt"},
            account_id, feature="product_label_transcribe",
            run_schema="scan-label.v2", output_schema="scan-label.v2",
        )
        assert (await _confirm(app_client, headers, token, run_id)).status_code == 422

    async def test_a_skin_care_run_cannot_confirm_a_food_pack(
        self, db_clean, off_clean, app_client, registered_supabase_user, payload,
    ):
        token, account_id = await registered_supabase_user()
        headers, _ = await _register_device(app_client)
        run_id = await _seed_run(payload, account_id)
        response = await app_client.post(
            FOOD_CONFIRM_URL, headers={**headers, **auth(token)},
            json={
                "barcode": BARCODE, "ai_run_id": str(run_id),
                "client_scan_id": uuid.uuid4().hex,
            },
        )
        assert response.status_code == 422, response.text


# ---------------------------------------------------------------------------
# 9. The bridge reaches the governed engine — and stops there
# ---------------------------------------------------------------------------
class TestStep8BReadiness:
    async def test_the_captured_snapshot_enters_step8b_with_its_own_provenance(
        self, db_clean, app_client, registered_supabase_user, payload,
    ):
        """Real capture, real snapshot, real projection. Only the model was mocked.

        The category handed to Step 8B is read from the snapshot itself, never
        supplied by this test: a hard-coded category here would prove the
        opposite of what the milestone claims.
        """
        captured = await _capture(app_client, registered_supabase_user, payload)
        account_id = captured["account_id"]

        factory = get_sessionmaker()
        async with factory() as session:
            profile = AppearanceProfile(account_id=account_id)
            session.add(profile)
            await session.flush()
            session.add(ProfileAttribute(
                profile_id=profile.id, key="care_skin_sensitivity",
                value="sometimes_reactive", source="user_declared",
                confidence=1.0, verification_state="confirmed",
            ))
            await session.commit()

            snapshot = await service.latest_label_snapshot(session, BARCODE)
            category = care_capture.personal_applicability_category_from_label(snapshot)
            assert category is PersonalApplicabilityCategory.SKIN_CARE

            result = await interpret_label_snapshot_for_account(
                session, snapshot, account_id=account_id, category=category,
            )

        assert result.category is PersonalApplicabilityCategory.SKIN_CARE
        assert result.provenance is not None
        assert result.provenance.label_snapshot_id == snapshot.id
        assert result.provenance.content_fingerprint == snapshot.content_fingerprint
        assert result.provenance.barcode == BARCODE
        assert result.provenance.version_number == snapshot.version_number
        assert result.provenance.scan_event_id == snapshot.scan_event_id

    async def test_the_category_adapter_reads_the_label_and_never_guesses(self, db_clean):
        def snapshot(facts):
            return LabelSnapshot(
                barcode=BARCODE, facts=facts, content_fingerprint="f" * 64,
                scan_event_id=uuid.uuid4(),
            )

        adapt = care_capture.personal_applicability_category_from_label
        assert adapt(snapshot({"product_category": "skin_care"})) is (
            PersonalApplicabilityCategory.SKIN_CARE
        )
        for refused in (
            {},                                             # legacy food capture
            {"product_category": None},
            {"product_category": "Skin Care"},              # not exact
            {"product_category": "skin-care"},              # not an alias
            {"product_category": " skin_care "},            # not trimmed into a match
            {"product_category": "hair_care"},
            {"product_category": "packaged_food"},
            {"product_name": "Petrolatum Skin Cream"},      # never from the name
            {"ingredients_text": "Petrolatum"},             # never from the formula
        ):
            assert adapt(snapshot(refused)) is None, refused
        assert adapt(snapshot(None)) is None


# ---------------------------------------------------------------------------
# 10. Static boundaries — what this code may not contain
# ---------------------------------------------------------------------------
class TestStaticBoundaries:
    """Read from the syntax tree, never from raw lines.

    A line scan cannot tell a docstring that *forbids* a word from production
    code that uses it, so a prose explanation of the boundary would trip the
    guard meant to enforce it.
    """

    def test_the_extraction_schema_has_no_category_field(self):
        """The model transcribes the label. The person's action supplies category."""
        declared = _pydantic_model_fields(EXTRACTION_PATH)["ExtractedSkinCareLabel"]
        assert declared == {
            "product_name", "brand", "product_type", "ingredients_text",
            "confidence", "uncertain_fields", "photo_quality_notes",
        }
        assert set(ExtractedSkinCareLabel.model_fields) == declared
        for banned in ("category", "product_category", "recommended_category", "decision_category"):
            assert banned not in ExtractedSkinCareLabel.model_fields

    def test_no_step8j_schema_field_can_carry_a_decision(self):
        banned = {
            "verdict", "action", "buy", "wait", "skip", "safe", "unsafe",
            "risk_score", "suitability", "recommended", "score", "advice",
        }
        for path in STEP8J_MODULES:
            for class_name, declared in _pydantic_model_fields(path).items():
                assert not (declared & banned), (path.name, class_name, declared & banned)

    def test_no_request_model_accepts_a_category(self):
        """The category enters once, at confirmation, from the route itself."""
        banned = {
            "category", "product_category", "decision_category",
            "personal_category", "interpretation_category",
        }
        for class_name, declared in _pydantic_model_fields(API_PATH).items():
            assert not (declared & banned), (class_name, declared & banned)

    def test_no_step8j_route_takes_a_category_parameter(self):
        """Not as a body field, and not as a query parameter either."""
        banned = {
            "category", "product_category", "decision_category",
            "personal_category", "interpretation_category",
        }
        tree = _module_tree(API_PATH)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            args = node.args
            names = {
                arg.arg
                for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]
            }
            assert not (names & banned), (node.name, names & banned)

    def test_step8j_never_imports_the_step8i_knowledge_pack(self):
        """Capture is generic skin-care infrastructure. It knows no substance."""
        for path in STEP8J_MODULES:
            for module in _imported_modules(path):
                assert not module.startswith("app.knowledge_packs"), (path.name, module)

    def test_step8j_never_loads_a_personal_decision_release(self):
        """Capture works identically with zero or one active release."""
        for path in STEP8J_MODULES:
            for module in _imported_modules(path):
                assert "personal_decision_release" not in module, (path.name, module)

    def test_step8j_imports_no_personal_decision_layer_at_all(self):
        for path in STEP8J_MODULES:
            for module in _imported_modules(path):
                assert "personal_decision_" not in module, (path.name, module)

    def test_step8j_is_not_routed_through_the_legacy_care_verdict_systems(self):
        for path in STEP8J_MODULES:
            for module in _imported_modules(path):
                assert not module.startswith("app.domains.purchase"), (path.name, module)
                assert not module.startswith("app.domains.recommendation"), (path.name, module)

    def test_the_only_category_this_milestone_can_write_is_skin_care(self):
        assert care_capture.SKIN_CARE_CATEGORY == "skin_care"
        source = CAPTURE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        assignments = {
            node.targets[0].id: node.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        }
        assert isinstance(assignments["SKIN_CARE_CATEGORY"], ast.Constant)
        assert assignments["SKIN_CARE_CATEGORY"].value == "skin_care"

    def test_the_ai_boundary_states_every_prohibition(self):
        system = care_extraction.SYSTEM.lower()
        for clause in (
            "recommend the product",
            "decide buy / wait / skip",
            "diagnose a condition",
            "safe or unsafe",
            "good or bad",
            "infer an ingredient that is not visible",
            "infer a concentration",
            "infer efficacy",
            "infer treatment benefit",
            "classify a medical condition",
            "add ingredients from general product knowledge",
        ):
            assert clause in system, clause
        assert "use only text visibly present in the image" in system

    def test_the_prompt_never_asks_the_model_what_kind_of_product_this_is(self):
        prompt = care_extraction.prompt().lower()
        assert "category" not in prompt
        assert "product_category" not in prompt

    def test_the_food_and_skin_care_features_and_schemas_are_disjoint(self):
        from app.domains.product import extraction as food_extraction

        assert care_extraction.FEATURE != food_extraction.FEATURE
        assert care_extraction.SCHEMA_VERSION != food_extraction.SCHEMA_VERSION
        assert not (
            care_extraction.CONFIRMABLE_SCHEMA_VERSIONS
            & food_extraction.CONFIRMABLE_SCHEMA_VERSIONS
        )

    def test_the_confirmable_schema_set_is_exactly_v1(self):
        assert frozenset({"skin-care-label.v1"}) == care_extraction.CONFIRMABLE_SCHEMA_VERSIONS

    def test_the_confirmed_fact_fields_are_exactly_the_governed_three_plus_category(self):
        assert care_capture.CONFIRMED_FACT_FIELDS == ("product_name", "brand", "ingredients_text")
        assert care_capture.CATEGORY_FACT_KEY == "product_category"
        facts = care_capture.confirmed_label_facts(ExtractedSkinCareLabel(
            product_name="N", brand="B", ingredients_text="Petrolatum",
            product_type="Ointment", confidence=0.5,
            uncertain_fields=["brand"], photo_quality_notes="glare",
        ))
        assert facts == {
            "product_name": "N", "brand": "B",
            "ingredients_text": "Petrolatum", "product_category": "skin_care",
        }

    def test_the_capture_module_persists_no_image_bytes(self):
        for path in STEP8J_MODULES:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            names = {
                node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
            } | {
                node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
            }
            for banned in ("image_bytes", "image_hash", "photo_copy", "ocr_hash"):
                assert banned not in names, (path.name, banned)


# ---------------------------------------------------------------------------
# 11. The Step 8I production hold survives this milestone
# ---------------------------------------------------------------------------
class TestProductionHold:
    def test_the_three_production_registries_are_still_empty(self):
        assert PERSONAL_DECISION_SEMANTIC_RULES == ()
        assert PERSONAL_DECISION_POLICY_RULES == ()
        assert PERSONAL_DECISION_EXPLANATION_RULES == ()

    async def test_the_ordinary_seed_still_creates_no_release_and_no_step8i_claim(
        self, db_clean,
    ):
        factory = get_sessionmaker()
        async with factory() as session:
            await run_reference_seed(session)
            claims = (await session.execute(
                select(func.count()).select_from(EvidenceClaim)
                .where(EvidenceClaim.claim_key.like("personal-applicability:%"))
            )).scalar_one()
            releases = (await session.execute(
                select(func.count()).select_from(PersonalDecisionRelease)
            )).scalar_one()
            active = (await session.execute(
                select(func.count()).select_from(PersonalDecisionRelease)
                .where(PersonalDecisionRelease.status == "active")
            )).scalar_one()
        assert claims == 0
        assert releases == 0
        assert active == 0

    async def test_importing_the_capture_code_activates_nothing(
        self, db_clean, app_client, registered_supabase_user, payload,
    ):
        await _capture(app_client, registered_supabase_user, payload)
        factory = get_sessionmaker()
        async with factory() as session:
            releases = (await session.execute(
                select(func.count()).select_from(PersonalDecisionRelease)
            )).scalar_one()
        assert releases == 0
        assert PERSONAL_DECISION_SEMANTIC_RULES == ()
        assert PERSONAL_DECISION_POLICY_RULES == ()
        assert PERSONAL_DECISION_EXPLANATION_RULES == ()
