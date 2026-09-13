"""Every AI-written string that reaches a user is swept by the medical boundary.

``CLAUDE.md`` §5 states the rule plainly: ``narrative_is_safe`` "screens every
AI-written string before it is stored or shown" and "fails closed". Two paths
did not.

What this protects against
--------------------------
* The style and purchase narratives being swept by a same-named but weaker
  list that names no skin condition, no dosage and no deficiency — so
  "this will calm your eczema" and "take 500 mg daily" were shown as written.
* The onboarding baseline analysis persisting a condition name into a profile
  attribute. ``visible_skin_characteristics`` is a free-text list in the
  registry, so nothing else in that path stopped it.
* The baseline disclaimer being whatever the model returned, including an
  empty string. A disclaimer is ours; a model must not be able to soften it.
"""
from __future__ import annotations

import base64
import json

import pytest
from app.domains.consent.models import CONSENT_PHOTO_ANALYSIS
from app.domains.profile import baseline
from app.domains.profile.models import AttributeObservation
from app.domains.recommendation.schemas import BANNED_NARRATIVE_TERMS
from app.domains.recommendation.schemas import narrative_is_safe as style_is_safe
from app.domains.routines.safety import BANNED_TERMS, first_violation
from app.domains.routines.safety import narrative_is_safe as boundary_is_safe
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import select

from tests.conftest import auth, png_bytes

pytestmark = pytest.mark.asyncio


def _image() -> str:
    return base64.b64encode(png_bytes()).decode("ascii")


async def _grant_photo_consent(client, token) -> None:
    resp = await client.post(
        "/api/v2/consent",
        headers=auth(token),
        json={"consent_type": CONSENT_PHOTO_ANALYSIS, "granted": True},
    )
    assert resp.status_code in (200, 201), resp.text


def _baseline_payload(**overrides) -> str:
    payload = {
        "image_quality": "good",
        "image_quality_notes": "Clear and well lit.",
        "observations": [
            {
                "key": "skin_tone",
                "value": "medium",
                "confidence": 0.9,
                "why": "Visible in even light across the cheeks.",
            }
        ],
        "colour_palette": [
            {"name": "olive", "hex": "#556B2F", "why": "Sits close to the undertone."}
        ],
        "summary": "Warm, muted colours sit well with what is visible here.",
        "disclaimer": "Style guidance from visible photo details, not medical advice.",
    }
    payload.update(overrides)
    return json.dumps(payload)


# ---------------------------------------------------------------------------
# The style narrative sweep
# ---------------------------------------------------------------------------

class TestStyleNarrativeSweep:
    """``recommendation.schemas.narrative_is_safe`` is the sweep applied to the
    look explanations and the purchase summary. It has to be at least as strict
    as the product-wide boundary, not a second opinion with its own shorter list."""

    @pytest.mark.parametrize("term", BANNED_TERMS)
    def test_every_boundary_term_is_rejected_by_the_style_sweep(self, term):
        sentence = f"A good choice here. {term} something, so wear the linen shirt."
        assert boundary_is_safe(sentence) is False, "test sentence must trip the boundary"
        assert style_is_safe(sentence) is False, (
            f"the style sweep let {term!r} through; the boundary catches it"
        )

    @pytest.mark.parametrize(
        "sentence",
        [
            "This kurta will help calm your eczema flare-ups before the wedding.",
            "Pair it with the serum and take 500 mg daily for a clearer look.",
            "You have rosacea, so stick to the cotton shirt.",
            "You are deficient in vitamin D, so choose brighter colours.",
            "Start taking the zinc tablets before the event.",
            "Wear the jacket. It heals your skin over the week.",
        ],
    )
    def test_medical_drift_in_a_style_narrative_is_rejected(self, sentence):
        assert style_is_safe(sentence) is False, first_violation(sentence)

    @pytest.mark.parametrize("term", BANNED_NARRATIVE_TERMS)
    def test_the_styling_half_of_the_list_still_applies(self, term):
        assert style_is_safe(f"Some wording with {term} inside it.") is False

    @pytest.mark.parametrize(
        "sentence",
        [
            "A crisp linen shirt reads well for a daytime Delhi wedding.",
            "The olive trousers repeat a colour you already wear often.",
            "Soft cotton over the kurta keeps the look light in humid weather.",
            "You already own shoes that finish this, so nothing new is needed.",
        ],
    )
    def test_ordinary_style_wording_still_passes(self, sentence):
        assert style_is_safe(sentence) is True

    def test_empty_and_missing_text_is_handled(self):
        assert style_is_safe("") is True
        assert style_is_safe(None) is True  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The onboarding baseline
# ---------------------------------------------------------------------------

class TestBaselineConstants:
    def test_the_withheld_summary_passes_its_own_sweep(self):
        assert boundary_is_safe(baseline.SUMMARY_WITHHELD) is True
        assert style_is_safe(baseline.SUMMARY_WITHHELD) is True

    def test_the_disclaimer_passes_its_own_sweep(self):
        assert boundary_is_safe(baseline.BASELINE_DISCLAIMER) is True

    def test_an_observation_naming_a_condition_is_refused(self):
        class _Suggestion:
            key = "visible_skin_characteristics"
            value = ["eczema", "mild dryness"]
            why = "Visible across both cheeks."

        assert baseline._observation_is_safe(_Suggestion()) is False

    def test_an_observation_with_a_diagnostic_reason_is_refused(self):
        class _Suggestion:
            key = "skin_tone"
            value = "medium"
            why = "You have psoriasis, so the tone reads lighter here."

        assert baseline._observation_is_safe(_Suggestion()) is False

    def test_an_ordinary_observation_is_kept(self):
        class _Suggestion:
            key = "visible_skin_characteristics"
            value = ["mild dryness", "even tone"]
            why = "Visible across both cheeks in this light."

        assert baseline._observation_is_safe(_Suggestion()) is True


class TestBaselineThroughTheRoute:
    """The whole path, with the provider returning what a model actually could."""

    async def test_a_condition_name_is_never_persisted_as_an_attribute(
        self, app_client, db_clean, registered_supabase_user, fake_provider
    ):
        token, _ = await registered_supabase_user()
        await _grant_photo_consent(app_client, token)
        fake_provider.text = _baseline_payload(
            observations=[
                {
                    "key": "visible_skin_characteristics",
                    "value": ["eczema", "fungal acne"],
                    "confidence": 0.95,
                    "why": "You have eczema on the cheeks.",
                },
                {
                    "key": "skin_tone",
                    "value": "medium",
                    "confidence": 0.9,
                    "why": "Even across the face in this light.",
                },
            ]
        )

        resp = await app_client.post(
            "/api/v2/profile/baseline-analysis",
            headers=auth(token),
            json={"image_base64": _image()},
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        keys = [row["key"] for row in body["observations"]]
        assert "visible_skin_characteristics" not in keys
        assert "skin_tone" in keys, "the clean observation survives"

        factory = get_sessionmaker()
        async with factory() as session:
            rows = (await session.execute(select(AttributeObservation))).scalars().all()
        stored = json.dumps([{"k": r.key, "v": r.proposed_value, "w": r.why} for r in rows])
        assert "eczema" not in stored
        assert "fungal acne" not in stored
        assert "you have" not in stored.lower()

    async def test_the_disclaimer_is_ours_not_the_models(
        self, app_client, db_clean, registered_supabase_user, fake_provider
    ):
        token, _ = await registered_supabase_user()
        await _grant_photo_consent(app_client, token)
        fake_provider.text = _baseline_payload(disclaimer="")

        resp = await app_client.post(
            "/api/v2/profile/baseline-analysis",
            headers=auth(token),
            json={"image_base64": _image()},
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["disclaimer"] == baseline.BASELINE_DISCLAIMER

    async def test_a_summary_that_names_a_condition_is_not_shown(
        self, app_client, db_clean, registered_supabase_user, fake_provider
    ):
        token, _ = await registered_supabase_user()
        await _grant_photo_consent(app_client, token)
        fake_provider.text = _baseline_payload(
            summary="Your rosacea means warm tones work best, so take 20 mg daily."
        )

        resp = await app_client.post(
            "/api/v2/profile/baseline-analysis",
            headers=auth(token),
            json={"image_base64": _image()},
        )

        assert resp.status_code == 200, resp.text
        message = resp.json()["message"]
        assert message == baseline.SUMMARY_WITHHELD
        assert "rosacea" not in message.lower()

    async def test_an_unsafe_palette_entry_is_dropped(
        self, app_client, db_clean, registered_supabase_user, fake_provider
    ):
        token, _ = await registered_supabase_user()
        await _grant_photo_consent(app_client, token)
        fake_provider.text = _baseline_payload(
            colour_palette=[
                {"name": "olive", "hex": "#556B2F", "why": "Sits close to the undertone."},
                {"name": "grey", "hex": "#808080", "why": "Calms the dermatitis on your jaw."},
            ]
        )

        resp = await app_client.post(
            "/api/v2/profile/baseline-analysis",
            headers=auth(token),
            json={"image_base64": _image()},
        )

        assert resp.status_code == 200, resp.text
        palette = resp.json()["colour_palette"]
        assert [item["name"] for item in palette] == ["olive"]

    async def test_a_clean_baseline_passes_through_untouched(
        self, app_client, db_clean, registered_supabase_user, fake_provider
    ):
        token, _ = await registered_supabase_user()
        await _grant_photo_consent(app_client, token)
        fake_provider.text = _baseline_payload()

        resp = await app_client.post(
            "/api/v2/profile/baseline-analysis",
            headers=auth(token),
            json={"image_base64": _image()},
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["message"] == "Warm, muted colours sit well with what is visible here."
        assert [item["name"] for item in body["colour_palette"]] == ["olive"]
        assert [row["key"] for row in body["observations"]] == ["skin_tone"]
        assert body["photo_stored"] is False


def test_no_test_name_is_defined_twice_in_a_class():
    """A repeated method name silently replaces the earlier one, and the
    stronger assertion is usually the one that disappears."""
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path(__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            names = [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            assert len(names) == len(set(names)), f"{node.name} defines a name twice"
