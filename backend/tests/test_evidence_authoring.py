"""The knowledge authoring tool: the approval gate, versioning, and CSV import.

Written against the API a person actually uses, so these prove the workflow an
operator gets, not just that the service functions return the right thing.
"""
from __future__ import annotations

import uuid

import pytest
from app.domains.evidence.enums import EvidenceTier, ReviewStatus
from app.domains.evidence.models import EvidenceClaim
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import select

from tests.conftest import auth

ADMIN = "/api/v2/admin/knowledge"


def entry_body(**overrides):
    body = {
        "subject_type": "ingredient",
        "subject": "turmeric",
        "claim": "Curcumin absorption rises when taken with black pepper.",
        "value": "20",
        "unit": "x",
        "source_name": "Planta Medica 1998",
        "source_url": "https://example.org/planta-medica-1998",
        "evidence_tier": EvidenceTier.CLINICALLY_STUDIED.value,
        "notes": "Piperine inhibits glucuronidation.",
        "domain": "nutrition",
    }
    body.update(overrides)
    return body


@pytest.fixture
async def admin_token(registered_supabase_user):
    token, _ = await registered_supabase_user(admin=True)
    return token


# ---------------------------------------------------------------------------
# Adding, reviewing, approving, publishing
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_an_entry_goes_draft_to_approved_to_published(db_clean, app_client, admin_token):
    created = await app_client.post(ADMIN + "/entries", headers=auth(admin_token), json=entry_body())
    assert created.status_code == 201, created.text
    entry = created.json()
    assert entry["status"] == ReviewStatus.DRAFT.value, "a new entry must start as a draft"
    assert entry["version"] == 1
    entry_id = entry["id"]

    approved = await app_client.post(f"{ADMIN}/entries/{entry_id}/approve", headers=auth(admin_token))
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == ReviewStatus.APPROVED.value
    assert approved.json()["reviewed_by"]

    published = await app_client.post(f"{ADMIN}/entries/{entry_id}/publish", headers=auth(admin_token))
    assert published.status_code == 200, published.text
    assert published.json()["status"] == ReviewStatus.PUBLISHED.value
    assert published.json()["published_at"]


@pytest.mark.asyncio
async def test_the_queue_filters_by_subject_type_and_status(db_clean, app_client, admin_token):
    await app_client.post(ADMIN + "/entries", headers=auth(admin_token), json=entry_body())
    await app_client.post(ADMIN + "/entries", headers=auth(admin_token),
                          json=entry_body(subject_type="cookware", subject="cast iron"))

    by_type = await app_client.get(ADMIN + "/entries?subject_type=cookware", headers=auth(admin_token))
    assert [e["subject_type"] for e in by_type.json()["entries"]] == ["cookware"]

    by_status = await app_client.get(ADMIN + "/entries?status=published", headers=auth(admin_token))
    assert by_status.json()["entries"] == []

    both = await app_client.get(ADMIN + "/entries?subject_type=ingredient&status=draft",
                                headers=auth(admin_token))
    assert len(both.json()["entries"]) == 1


@pytest.mark.asyncio
async def test_rejecting_records_the_reason(db_clean, app_client, admin_token):
    created = await app_client.post(ADMIN + "/entries", headers=auth(admin_token), json=entry_body())
    entry_id = created.json()["id"]

    rejected = await app_client.post(
        f"{ADMIN}/entries/{entry_id}/reject", headers=auth(admin_token),
        json={"reason": "The source is a blog summarising the study, not the study."},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == ReviewStatus.REJECTED.value
    assert "blog" in rejected.json()["rejection_reason"]


@pytest.mark.asyncio
async def test_rejecting_without_a_reason_is_refused(db_clean, app_client, admin_token):
    created = await app_client.post(ADMIN + "/entries", headers=auth(admin_token), json=entry_body())
    entry_id = created.json()["id"]
    refused = await app_client.post(
        f"{ADMIN}/entries/{entry_id}/reject", headers=auth(admin_token), json={"reason": "  "},
    )
    assert refused.status_code in (400, 422)


# ---------------------------------------------------------------------------
# The approval gate: no source URL, no approval
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_approval_without_a_source_url_is_refused(db_clean, app_client, admin_token):
    """The acceptance criterion. A citation nobody can open is not a source."""
    created = await app_client.post(
        ADMIN + "/entries", headers=auth(admin_token), json=entry_body(source_url=None),
    )
    assert created.status_code == 201
    entry_id = created.json()["id"]

    refused = await app_client.post(f"{ADMIN}/entries/{entry_id}/approve", headers=auth(admin_token))
    assert refused.status_code == 422, refused.text
    assert "source url" in refused.text.lower()

    # And it really did not move.
    still = await app_client.get(f"{ADMIN}/entries/{entry_id}", headers=auth(admin_token))
    assert still.json()["status"] == ReviewStatus.DRAFT.value


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["", "   ", "not-a-url", "example.org/no-scheme", "ftp://files.example.org/x"])
async def test_a_source_url_that_cannot_be_opened_does_not_count(db_clean, app_client, admin_token, url):
    created = await app_client.post(
        ADMIN + "/entries", headers=auth(admin_token), json=entry_body(source_url=url),
    )
    entry_id = created.json()["id"]
    refused = await app_client.post(f"{ADMIN}/entries/{entry_id}/approve", headers=auth(admin_token))
    assert refused.status_code == 422, f"{url!r} was accepted as a source URL"


@pytest.mark.asyncio
async def test_publishing_an_unapproved_entry_is_refused(db_clean, app_client, admin_token):
    created = await app_client.post(ADMIN + "/entries", headers=auth(admin_token), json=entry_body())
    entry_id = created.json()["id"]
    refused = await app_client.post(f"{ADMIN}/entries/{entry_id}/publish", headers=auth(admin_token))
    assert refused.status_code == 409, refused.text


@pytest.mark.asyncio
async def test_a_not_enough_information_entry_is_never_recorded_as_supported(
    db_clean, app_client, admin_token,
):
    """A tier that describes an absence must not become a supported claim."""
    created = await app_client.post(
        ADMIN + "/entries", headers=auth(admin_token),
        json=entry_body(evidence_tier=EvidenceTier.NOT_ENOUGH_INFORMATION.value),
    )
    entry_id = created.json()["id"]
    await app_client.post(f"{ADMIN}/entries/{entry_id}/approve", headers=auth(admin_token))

    factory = get_sessionmaker()
    async with factory() as session:
        claim = (await session.execute(
            select(EvidenceClaim).where(EvidenceClaim.id == uuid.UUID(entry_id))
        )).scalar_one()
    assert claim.claim_status == "unsupported"


# ---------------------------------------------------------------------------
# Versioning: editing never overwrites
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_editing_a_published_entry_creates_a_version_and_keeps_the_old_one(
    db_clean, app_client, admin_token,
):
    """The acceptance criterion. The published wording must remain readable."""
    created = await app_client.post(ADMIN + "/entries", headers=auth(admin_token), json=entry_body())
    entry_id = created.json()["id"]
    original_claim = created.json()["claim"]
    await app_client.post(f"{ADMIN}/entries/{entry_id}/approve", headers=auth(admin_token))
    await app_client.post(f"{ADMIN}/entries/{entry_id}/publish", headers=auth(admin_token))

    edited = await app_client.put(
        f"{ADMIN}/entries/{entry_id}", headers=auth(admin_token),
        json=entry_body(claim="Curcumin absorption rises sharply with piperine."),
    )
    assert edited.status_code == 200, edited.text
    new_entry = edited.json()

    assert new_entry["id"] != entry_id, "editing overwrote the published entry"
    assert new_entry["version"] == 2
    assert new_entry["supersedes_id"] == entry_id
    assert new_entry["status"] == ReviewStatus.DRAFT.value, "a new version must be reviewed again"

    old = await app_client.get(f"{ADMIN}/entries/{entry_id}", headers=auth(admin_token))
    assert old.status_code == 200, "the previous version was destroyed"
    assert old.json()["claim"] == original_claim, "the previous version's wording changed"
    assert old.json()["status"] == ReviewStatus.SUPERSEDED.value

    history = await app_client.get(f"{ADMIN}/entries/{entry_id}/versions", headers=auth(admin_token))
    assert [v["version"] for v in history.json()["versions"]] == [1, 2]


@pytest.mark.asyncio
async def test_editing_a_draft_does_not_spawn_a_version(db_clean, app_client, admin_token):
    """A draft is still being written; versioning it would be noise."""
    created = await app_client.post(ADMIN + "/entries", headers=auth(admin_token), json=entry_body())
    entry_id = created.json()["id"]

    edited = await app_client.put(
        f"{ADMIN}/entries/{entry_id}", headers=auth(admin_token),
        json=entry_body(claim="A corrected draft wording."),
    )
    assert edited.json()["id"] == entry_id
    assert edited.json()["version"] == 1
    assert edited.json()["claim"] == "A corrected draft wording."


@pytest.mark.asyncio
async def test_a_new_version_must_earn_its_own_approval(db_clean, app_client, admin_token):
    created = await app_client.post(ADMIN + "/entries", headers=auth(admin_token), json=entry_body())
    entry_id = created.json()["id"]
    await app_client.post(f"{ADMIN}/entries/{entry_id}/approve", headers=auth(admin_token))
    await app_client.post(f"{ADMIN}/entries/{entry_id}/publish", headers=auth(admin_token))

    new_id = (await app_client.put(
        f"{ADMIN}/entries/{entry_id}", headers=auth(admin_token),
        json=entry_body(claim="Revised.", source_url=None),
    )).json()["id"]

    refused = await app_client.post(f"{ADMIN}/entries/{new_id}/approve", headers=auth(admin_token))
    assert refused.status_code == 422, "a new version skipped the source-URL gate"


# ---------------------------------------------------------------------------
# CSV import: drafts only, always
# ---------------------------------------------------------------------------
CSV_HEADER = "subject_type,subject_key,claim,value,unit,source_name,source_url,evidence_tier,notes\n"


@pytest.mark.asyncio
async def test_csv_import_lands_everything_in_draft(db_clean, app_client, admin_token):
    """The acceptance criterion. Import can add work; it can never publish it."""
    csv_body = CSV_HEADER + (
        "ingredient,ashwagandha,Traditionally used as a tonic,,,Charaka Samhita,"
        "https://example.org/charaka,classical_text,\n"
        "ingredient,amla,High in vitamin C,600,mg per 100g,IFCT 2017,"
        "https://example.org/ifct,clinically_studied,\n"
        "cookware,aluminium,Not enough information on leaching,,,FSSAI,"
        "https://example.org/fssai,not_enough_information,\n"
    )
    response = await app_client.post(
        ADMIN + "/import", headers=auth(admin_token),
        files={"file": ("entries.csv", csv_body, "text/csv")},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["created_count"] == 3
    assert body["error_count"] == 0
    assert body["status"] == ReviewStatus.DRAFT.value
    assert all(entry["status"] == ReviewStatus.DRAFT.value for entry in body["created"])

    # Nothing in the database escaped draft, whatever the file asked for.
    factory = get_sessionmaker()
    async with factory() as session:
        statuses = set((await session.execute(select(EvidenceClaim.review_status))).scalars().all())
    assert statuses == {ReviewStatus.DRAFT.value}


@pytest.mark.asyncio
async def test_csv_import_cannot_be_told_to_publish(db_clean, app_client, admin_token):
    """A status column in the file must not be honoured."""
    csv_body = (
        "subject_type,subject_key,claim,source_name,source_url,evidence_tier,review_status,status\n"
        "ingredient,ghee,Culinary fat,Ayurveda text,https://example.org/x,classical_text,published,published\n"
    )
    response = await app_client.post(
        ADMIN + "/import", headers=auth(admin_token),
        files={"file": ("entries.csv", csv_body, "text/csv")},
    )
    assert response.status_code == 201, response.text
    assert response.json()["created"][0]["status"] == ReviewStatus.DRAFT.value


@pytest.mark.asyncio
async def test_csv_import_reports_a_bad_row_and_keeps_the_good_ones(
    db_clean, app_client, admin_token,
):
    csv_body = CSV_HEADER + (
        "ingredient,amla,High in vitamin C,600,mg,IFCT,https://example.org/ifct,clinically_studied,\n"
        "ingredient,,Missing its subject,,,Somebody,https://example.org/y,traditional_use,\n"
        "ingredient,neem,Bad tier,,,Somebody,https://example.org/z,made_up_tier,\n"
    )
    response = await app_client.post(
        ADMIN + "/import", headers=auth(admin_token),
        files={"file": ("entries.csv", csv_body, "text/csv")},
    )
    body = response.json()
    assert body["created_count"] == 1
    assert body["error_count"] == 2
    assert {error["line"] for error in body["errors"]} == {3, 4}


@pytest.mark.asyncio
async def test_csv_import_refuses_a_file_missing_required_columns(
    db_clean, app_client, admin_token,
):
    response = await app_client.post(
        ADMIN + "/import", headers=auth(admin_token),
        files={"file": ("entries.csv", "subject_type,claim\ningredient,hello\n", "text/csv")},
    )
    assert response.status_code == 422
    assert "subject_key" in response.text


@pytest.mark.asyncio
async def test_pasted_csv_also_lands_in_draft(db_clean, app_client, admin_token):
    """The paste route shares the importer, so it shares the draft-only rule."""
    csv_body = CSV_HEADER + (
        "ingredient,ghee,A culinary fat,,,Ayurveda text,https://example.org/x,classical_text,\n"
    )
    response = await app_client.post(
        ADMIN + "/import-text", headers=auth(admin_token), json={"csv": csv_body},
    )
    assert response.status_code == 201, response.text
    assert response.json()["created_count"] == 1
    assert response.json()["status"] == ReviewStatus.DRAFT.value
    assert response.json()["created"][0]["status"] == ReviewStatus.DRAFT.value


# ---------------------------------------------------------------------------
# It is admin-only
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_non_admin_cannot_reach_the_tool(db_clean, app_client, registered_supabase_user):
    token, _ = await registered_supabase_user()
    for method, path in (
        ("get", ADMIN + "/entries"),
        ("post", ADMIN + "/entries"),
        ("get", ADMIN + "/vocabulary"),
    ):
        response = await getattr(app_client, method)(
            path, headers=auth(token), **({"json": entry_body()} if method == "post" else {}),
        )
        assert response.status_code == 403, f"{path} was reachable by a non-admin"
