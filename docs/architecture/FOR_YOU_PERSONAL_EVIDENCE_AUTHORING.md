# FOR YOU Personal Evidence Authoring

Step 8G adds the governed administrator workflow that creates the exact
`substance_personal_applicability` evidence already consumed by Step 8B. It
does not create a second evidence store and does not decide, rank, score, or
explain a purchase outcome.

## Why the generic workflow was insufficient

The general evidence authoring workflow creates `usage_context` claims. Step
8B deliberately reads only `substance_personal_applicability` claims with a
stricter shape. Allowing the generic workflow to mutate those rows would let a
weaker contract bypass the Step 8B boundary. Generic reads remain available,
but every mutation of a specialized row is directed to the specialized admin
route with `SPECIALIZED_AUTHORING_REQUIRED`.

## Exact Step 8B contract

The specialized service governs these values rather than accepting them from
the client:

- subject type `substance`;
- claim type `substance_personal_applicability`;
- category-derived evidence domain;
- `ai_generated = false`;
- evidence tier `clinically_studied`;
- initial review state `draft` and version `1`.

V1 supports Skin Care, Hair Care, and Cosmetics. Packaged Food fails closed as
`CATEGORY_HAS_NO_PERSONAL_BODY_FACTS` because its Step 8A body-fact allowlist is
empty.

## Structured conditions

The administrator selects only a controlled `fact_key` and controlled values.
Vocabulary comes from `BODY_FACT_KEYS_BY_CATEGORY` and `ATTRIBUTE_REGISTRY`.
The service derives `equals_any` for scalar facts and `contains_any` for list
facts, builds the canonical schema-version-1 payload, then validates that
payload through `parse_personal_applicability_payload`. There are one to four
conditions; free text, thresholds, custom operators, unknown values,
`not_sure`, and duplicates are rejected.

## Sources and provenance

Each claim has one to five supporting paths. A path may select an existing
source or create a new opaque source identity. Eligible types are exactly:

- `official_guideline`;
- `government_reference`;
- `systematic_review`;
- `peer_reviewed_research`;
- `professional_consensus`.

Every source must be active, named, published by a named publisher, have an
openable HTTP(S) URL, and carry an explicit licence/use note. A duplicate
canonical URL is rejected with the existing source key. Locators belong to
the claim-to-source link. Every link is `supports`, and duplicate source paths
are rejected.

## Review, verification, and publication

Approval revalidates the persisted claim and every source rather than trusting
the create request. It reuses the evidence lifecycle and explicitly reviews
all supporting links. Publication verification reuses the established seven
explicit checkpoints; none is inferred from metadata or a URL. Publication
revalidates again, requires complete verification with no unresolved doubt,
and finally proves the claim and an eligible source path satisfy the public
knowledge predicates used by Step 8B. A failed final assertion rolls back with
the transaction.

## Editing and history

Draft and rejected rows edit in place and return to draft. Approved or
published rows produce a new version with the same claim key and a
`supersedes_claim_id`; the prior row is retained and marked superseded. New or
changed drafts clear verification, reviewer, publisher, claim status, and
rejection metadata, and their source links begin unreviewed. No automatic
“latest wins” runtime policy is introduced.

## Runtime boundary

The Step 8B query and projection semantics are unchanged. A legitimately
published row can be discovered by that existing runtime, but Step 8G does not
populate the Step 8C semantic, Step 8E policy, or Step 8F explanation
registries. All three production registries remain empty, so no BUY, WAIT, or
SKIP can be produced by this milestone. No real evidence is seeded.

Step 8H is separate work: it will govern immutable, cross-validated releases
of reviewed semantic, policy, and explanation rules. Step 8G does not
implement or activate that release mechanism.
