"""One failing domain must not take the rest of the export with it.

``build_export`` walks fourteen domain handlers and wraps each in its own
``try``/``except`` so that, as the comment there says, "one domain must not sink
the export". The wrapping is real, but the isolation was not: every handler
shares one ``AsyncSession``, and a failed statement leaves that session's
transaction in a failed state. PostgreSQL then refuses every statement that
follows until somebody rolls back — so the first domain to hit a database error
took all the domains after it down too, each with the same marker.

The user's copy of their own data is what is at stake. An export missing
thirteen of fourteen domains still answers 200, and the markers do not say the
other domains were never really tried.

This file forces a database error inside one handler and asserts the domains
after it still come back with their data.
"""
from __future__ import annotations

import uuid

import pytest
from app.domains.identity import service as identity
from app.domains.privacy import export as export_mod
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import text

pytestmark = pytest.mark.asyncio

FAILING_DOMAIN = "profile"


async def _account_id() -> uuid.UUID:
    factory = get_sessionmaker()
    async with factory() as session:
        account = await identity.register_account(session, str(uuid.uuid4()))
        await session.commit()
        return account.id


@pytest.fixture
def one_broken_domain(monkeypatch):
    """Make one handler fail the way a real database error would."""

    async def _broken(session, account_id):
        # Not ``raise``: the point is a failed *statement*, which is what puts
        # the shared transaction into the state the later domains inherit.
        await session.execute(text("SELECT 1 / 0"))

    handlers = dict(export_mod.DOMAIN_HANDLERS)
    handlers[FAILING_DOMAIN] = _broken
    monkeypatch.setattr(export_mod, "DOMAIN_HANDLERS", handlers)
    return FAILING_DOMAIN


async def test_only_the_broken_domain_reports_a_failure(db_clean, one_broken_domain):
    account_id = await _account_id()
    factory = get_sessionmaker()
    async with factory() as session:
        payload = await export_mod.build_export(session, account_id)

    failed = [
        name for name, value in payload["domains"].items()
        if isinstance(value, dict) and value.get("error") == "domain_export_failed"
    ]
    assert failed == [one_broken_domain], (
        "a database error in one domain must not fail the domains after it; "
        f"these failed: {failed}"
    )


async def test_the_domains_after_the_broken_one_still_carry_their_data(
    db_clean, one_broken_domain
):
    account_id = await _account_id()
    factory = get_sessionmaker()
    async with factory() as session:
        payload = await export_mod.build_export(session, account_id)

    names = list(export_mod.DOMAIN_HANDLERS)
    after = names[names.index(one_broken_domain) + 1:]
    assert after, "the broken domain must not be the last one, or this proves nothing"
    for name in after:
        assert payload["domains"][name].get("error") != "domain_export_failed", (
            f"{name} runs after the broken domain and inherited its failure"
        )


async def test_the_export_is_still_well_formed_after_a_domain_fails(
    db_clean, one_broken_domain
):
    account_id = await _account_id()
    factory = get_sessionmaker()
    async with factory() as session:
        payload = await export_mod.build_export(session, account_id)

    assert payload["schema_version"]
    assert payload["account"]["id"] == str(account_id)
    assert set(payload["domains"]) == set(export_mod.DOMAIN_HANDLERS)


# ---------------------------------------------------------------------------
# What an AI provider said, and whether the app said it
# ---------------------------------------------------------------------------

class TestAiOutputsAreLabelledInTheExport:
    """The export is complete, and honest about what it contains.

    ``ai_run_outputs`` keeps the provider's reply as it arrived — that is what
    makes it a record. The language boundary runs later, on the way to a
    screen, so wording the app refused to show is still in that table. Both
    facts have to survive into an export: removing the text would make the
    export incomplete, and handing it over unlabelled would mean a sentence the
    product declined to say arriving as though the product had said it.
    """

    async def test_wording_the_app_would_refuse_is_marked(self):
        from app.domains.privacy.export import AI_OUTPUT_BOUNDARY_KEY, _ai_output_dict

        class _Row:
            payload = {
                "summary": "Your rosacea means warm tones work best.",
                "observations": [{"why": "You have eczema on the cheeks."}],
            }
            id = uuid.uuid4()
            ai_run_id = uuid.uuid4()
            schema_version = "v1"
            confidence = 0.9
            verification_status = "unverified"
            created_at = None
            updated_at = None

        assert _ai_output_dict(_Row())[AI_OUTPUT_BOUNDARY_KEY] is False

    async def test_ordinary_wording_is_marked_as_passing(self):
        from app.domains.privacy.export import AI_OUTPUT_BOUNDARY_KEY, _ai_output_dict

        class _Row:
            payload = {"summary": "Warm, muted colours suit what is visible here."}
            id = uuid.uuid4()
            ai_run_id = uuid.uuid4()
            schema_version = "v1"
            confidence = 0.9
            verification_status = "unverified"
            created_at = None
            updated_at = None

        assert _ai_output_dict(_Row())[AI_OUTPUT_BOUNDARY_KEY] is True

    async def test_the_text_itself_is_still_exported(self):
        """Labelled, not withheld. It is the person's own data."""
        from app.domains.privacy.export import _ai_output_dict

        class _Row:
            payload = {"summary": "Your rosacea means warm tones work best."}
            id = uuid.uuid4()
            ai_run_id = uuid.uuid4()
            schema_version = "v1"
            confidence = 0.9
            verification_status = "unverified"
            created_at = None
            updated_at = None

        assert "rosacea" in str(_ai_output_dict(_Row())["payload"])

    async def test_every_string_in_a_nested_payload_is_examined(self):
        from app.domains.privacy.export import _payload_strings

        payload = {"a": "one", "b": [{"c": "two"}, ["three"]], "d": {"e": {"f": "four"}}}
        assert sorted(_payload_strings(payload)) == ["four", "one", "three", "two"]

    async def test_the_note_passes_the_boundary_it_describes(self):
        """A disclaimer the sweep rejects is a disclaimer that cannot be shown."""
        from app.domains.privacy.export import AI_OUTPUT_NOTE
        from app.domains.routines.safety import first_violation, narrative_is_safe

        assert narrative_is_safe(AI_OUTPUT_NOTE) is True, first_violation(AI_OUTPUT_NOTE)
