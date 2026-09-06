"""Loading the active release, and running the governed chain against it.

Two things live here, and they are deliberately different shapes.

``load_active_personal_decision_release`` is the only place that reads the
active release out of the database. One bounded query, then in-memory parsing
of the manifest it found. It does **not** re-run the evidence cross-validation
that approval and activation performed: that pass touches every claim and
every source path a release names, and doing it per customer request would
turn a scan into hundreds of queries to re-derive an answer a human already
signed off. Live evidence eligibility is still guaranteed, by Step 8B, on
every request -- and because every rule names an exact claim *version*,
evidence revised after activation simply stops matching rather than being
silently inherited.

``evaluate_personal_decision_with_release`` is pure. It takes an already
loaded release, hands its rules to Steps 8C, 8D, 8E and 8F explicitly, and
returns what Step 8F decided with the release's identity attached. There is no
hidden global: with no active release the three registries are passed as empty
tuples, in the open, so "production has no reviewed knowledge" is a visible
state rather than a default nobody can see.

The detached ``ActivePersonalDecisionRelease`` matters too. No ORM object
reaches the pure layers -- they would then hold a row bound to a session, and
a lazy load inside a deterministic function is exactly the kind of surprise
those layers exist to rule out.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.personal_applicability import LabelSnapshotPersonalApplicability
from app.domains.personal_decision_aggregation import aggregate_personal_decision_signals
from app.domains.personal_decision_explanation import (
    PersonalDecisionExplanationRule,
    PersonalDecisionPresentation,
    present_personal_decision,
)
from app.domains.personal_decision_policy import (
    PersonalDecisionPolicyRule,
    evaluate_personal_decision_policy,
)
from app.domains.personal_decision_release.enums import (
    PERSONAL_DECISION_RELEASE_KEY,
    PersonalDecisionReleaseStatus,
)
from app.domains.personal_decision_release.manifest import (
    PERSONAL_DECISION_RELEASE_MANIFEST_SCHEMA_VERSION,
    PersonalDecisionReleaseManifestError,
    assert_registries_valid,
    manifest_content_hash,
    parse_release_manifest,
)
from app.domains.personal_decision_release.models import PersonalDecisionRelease
from app.domains.personal_decision_release.validation import (
    PersonalDecisionReleaseInvariantError,
)
from app.domains.personal_decision_semantics import (
    PersonalDecisionSemanticRule,
    project_personal_decision_semantics,
)


@dataclass(frozen=True, slots=True)
class ActivePersonalDecisionRelease:
    """The active release, detached from the database and immutable.

    Tuples rather than lists, and no ORM row: what reaches Steps 8C to 8F is a
    plain immutable value that cannot be mutated by a later caller and cannot
    reach back into a session.
    """

    release_id: uuid.UUID
    release_version: int
    content_hash: str
    semantic_rules: tuple[PersonalDecisionSemanticRule, ...]
    policy_rules: tuple[PersonalDecisionPolicyRule, ...]
    explanation_rules: tuple[PersonalDecisionExplanationRule, ...]


@dataclass(frozen=True, slots=True)
class ReleasedPersonalDecisionResult:
    """Step 8F's result, with the release that produced it named.

    The release identity travels with the presentation so any later surface
    can say which reviewed bundle answered -- and so an answer can be traced
    back to the exact rules and evidence a human approved. All three release
    fields are ``None`` together when no release was active.
    """

    release_id: uuid.UUID | None
    release_version: int | None
    release_content_hash: str | None
    presentation: PersonalDecisionPresentation


def select_active_release(
    rows: Sequence[PersonalDecisionRelease],
) -> PersonalDecisionRelease | None:
    """Exactly one active release, or none. Never a choice between several.

    Separated from the query so the >1 case can be exercised directly: the
    partial unique index should make it impossible to construct, and a rule
    that can only be tested by first defeating the database is a rule nobody
    checks.

    Picking the highest version, the most recently activated, or the first row
    would each be an unreviewed decision about what customers are told, made
    by a sort order.
    """
    if not rows:
        return None
    if len(rows) > 1:
        raise PersonalDecisionReleaseInvariantError(
            f"{len(rows)} active personal decision releases; refusing to choose between them"
        )
    return rows[0]


def materialise_active_release(
    release: PersonalDecisionRelease,
) -> ActivePersonalDecisionRelease:
    """Turn one active row into the immutable runtime object, or fail closed.

    Everything here is a check on the row rather than on the request that
    wrote it. A JSONB column can be edited directly, so the schema version,
    the bounds, the types, the hash and the three registry validators all run
    again on the way out of the database.
    """
    if release.status != PersonalDecisionReleaseStatus.ACTIVE.value:
        raise PersonalDecisionReleaseInvariantError(
            f"release {release.id} is {release.status}, not active"
        )
    if release.manifest_schema_version != PERSONAL_DECISION_RELEASE_MANIFEST_SCHEMA_VERSION:
        raise PersonalDecisionReleaseInvariantError(
            f"release {release.id} declares manifest schema "
            f"{release.manifest_schema_version}, which is not supported"
        )

    try:
        manifest = parse_release_manifest(release.manifest)
        assert_registries_valid(manifest)
    except (PersonalDecisionReleaseManifestError, ValueError) as error:
        raise PersonalDecisionReleaseInvariantError(
            f"release {release.id} holds a manifest that is not usable: {error}"
        ) from error

    if manifest_content_hash(manifest) != release.content_hash:
        raise PersonalDecisionReleaseInvariantError(
            f"release {release.id} no longer matches its recorded content hash"
        )

    return ActivePersonalDecisionRelease(
        release_id=release.id,
        release_version=release.release_version,
        content_hash=release.content_hash,
        semantic_rules=manifest.semantic_rules,
        policy_rules=manifest.policy_rules,
        explanation_rules=manifest.explanation_rules,
    )


async def load_active_personal_decision_release(
    session: AsyncSession,
) -> ActivePersonalDecisionRelease | None:
    """The one active release, or ``None`` when production has no reviewed knowledge.

    One query. There are no per-rule child tables to join, by design: the
    bundle is the unit that was reviewed, and it is stored and read as one.
    """
    rows = (
        (
            await session.execute(
                select(PersonalDecisionRelease).where(
                    PersonalDecisionRelease.release_key == PERSONAL_DECISION_RELEASE_KEY,
                    PersonalDecisionRelease.status == PersonalDecisionReleaseStatus.ACTIVE.value,
                )
            )
        )
        .scalars()
        .all()
    )
    active = select_active_release(list(rows))
    if active is None:
        return None
    return materialise_active_release(active)


def evaluate_personal_decision_with_release(
    personal_applicability: LabelSnapshotPersonalApplicability,
    release: ActivePersonalDecisionRelease | None,
) -> ReleasedPersonalDecisionResult:
    """Run Steps 8C to 8F over one Step 8B result using one active release.

    Pure and synchronous. Every rule set is passed explicitly, including the
    empty ones: with no active release this calls the same four functions with
    ``()`` three times rather than relying on the module-level registries, so
    the absence of reviewed knowledge is something the caller can see in the
    arguments instead of something it has to know about the defaults.

    The absence of a release never suppresses a safety handoff. Step 8A's
    handoff is carried through Step 8C untouched and answered by Step 8E
    before any registry is consulted, so a scan that needs a professional
    still says so when there is no decision knowledge at all.
    """
    semantic_rules: tuple[PersonalDecisionSemanticRule, ...] = (
        release.semantic_rules if release is not None else ()
    )
    policy_rules: tuple[PersonalDecisionPolicyRule, ...] = (
        release.policy_rules if release is not None else ()
    )
    explanation_rules: tuple[PersonalDecisionExplanationRule, ...] = (
        release.explanation_rules if release is not None else ()
    )

    semantics = project_personal_decision_semantics(
        personal_applicability,
        rules=semantic_rules,
    )
    aggregation = aggregate_personal_decision_signals(semantics)
    policy = evaluate_personal_decision_policy(aggregation, rules=policy_rules)
    presentation = present_personal_decision(policy, rules=explanation_rules)

    return ReleasedPersonalDecisionResult(
        release_id=release.release_id if release is not None else None,
        release_version=release.release_version if release is not None else None,
        release_content_hash=release.content_hash if release is not None else None,
        presentation=presentation,
    )
