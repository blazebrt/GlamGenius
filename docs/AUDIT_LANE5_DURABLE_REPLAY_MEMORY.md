# Audit Lane 5 — durable replay and historical authority

Scope: F10/F11/F12 only. Starting main: `fccba05541b8f3a4e97ca83248b5845ebeee3b54`, tree `021981efb389961f936d204062e672237d674151`.

## Defects reproduced on the starting implementation

- F10: a flush of A discarded B enqueued during its network flight, both when A succeeded and when it failed. Two concurrent failed submissions also lost one report. Storage failures were swallowed, incorrectly claiming an offline save. The new queue suite failed nine assertions before the correction.
- F11: food confirmation accepted an existing device/client key with changed barcode, AI run, canonical facts, account or outcome. The five mismatch regressions returned 201 instead of 409 before the correction. A known Store-B record for the second barcode demonstrates that unrelated existing product data is not replay authority.

## F10 correction

All report read/modify/write operations use one serialized mutation lane. Network requests stay outside that lane. A flush snapshots its work, collects acknowledged client report IDs, then re-reads the latest durable queue and removes only those IDs. Reports added during the flight survive. Overlapping flush callers share one flight. Identity is `client_report_id`, never timestamps.

Mutation reads propagate storage, JSON and row-validation failures instead of replacing unknown data with an empty queue. Writes propagate failure. `submitReport` returning false now means durable enqueue was proven; rejection means neither delivery nor storage could be proven. The existing report sheet handles rejection without claiming an offline save, releases its busy state and leaves the draft/photo available for retry. The display-only pending count cannot authorize a write.

Legacy reports without an owner remain anonymous. Reports made under an account wait for that same account. Neither the mutation lane nor queue validation assigns today's account to saved reports.

## F11 correction

`assert_label_confirmation_replay_matches` is food-confirmation-specific policy. The generic `record_scan` implementation is unchanged. The helper compares the stored event against the validated request's device, account, barcode, label-captured outcome, AI run and complete canonical label facts. Dictionary equality is independent of JSON object key ordering. A mismatch returns a non-retryable 409 before any ProductRecord, LabelSnapshot or AI-output change.

An identical replay leaves event/snapshot counts and confidence/count/output state unchanged. Independent PostgreSQL requests are coordinated with barriers, not sleeps. Same-barcode contenders rendezvous before the advisory lock. Different-barcode contenders both perform real missing-key reads before insert; PostgreSQL returns the unique-index winner to the losing transaction, and the same helper rejects its mismatched evidence. Tests verify distinct database sessions/backend PIDs and the actual recovery lookup. Existing savepoint recovery and barcode-scoped snapshot serialization remain intact.

## F12 investigation — existing protection, not new production work

**F12 current architecture already satisfies the original finding.**

The production historical reader of `RoutineRecommendationRun` is the routines domain in `GET /api/v2/privacy/export`, through `privacy.export._routines`. It selects account-owned runs and exports their stored columns/JSON inputs, grouped by household subject. It does not rebuild a historical recommendation from mutable Routine/RoutineStep rows. No other production API reconstructs older RoutineRecommendationRun answers. Generation and routines-today compute current recommendations; they are not historical run replay.

Already present before this lane:

- immutable recommendation snapshot material in `RoutineRecommendationRun.inputs['care_snapshot']`;
- versioned context, decision, routine-plan, engine and ontology authority;
- original rendered routines, selected product identities, fingerprints and guidance/Home Care audit material;
- exact rule IDs/versions, original evidence claim IDs and applicability versions;
- current registry/evidence lookups restricted to current generation/authoring, not historical export.

Guidance/Home Care audit material intentionally omits customer title/body. Their saved fingerprints include original rendered material, but the export does not try to recover customer text from today's registry. No supported historical customer-copy reconstruction was found, so no new text storage or history table is justified. Legacy runs without snapshots stay without snapshots; current authority is never guessed into them.

New regression coverage uses real generated runs and the real privacy-export API. It changes today's registries, stored Routine/RoutineStep rendering, retires previously published evidence, and publishes a new claim version only after generation. Original history remains byte-equivalent JSON material with its original fingerprint. Current guidance genuinely changes in the evidence tests, proving the setup is not vacuous. Reconstruction/evidence/builder seams throw if called during export. Another account's history does not appear. Missing legacy snapshots are not backfilled.

Care production code and schema are unchanged. No migration is necessary.

## Executed mutation battery

Each mutant was applied to the actual runtime path, executed against the regression tests, and reverted before validation/commit. All eight were killed by behavioral assertion failures, not syntax/import errors.

| Mutant | Catching assertion |
| --- | --- |
| F10 final flush uses its stale snapshot | B survives A success/failure |
| F10 failed enqueue bypasses the mutation lane | Both independent failed submissions survive |
| F10 storage read failure becomes empty | Submission rejects and unknown queue is not written |
| F11 key-only replay | Five evidence/ownership/outcome mismatches return 409 |
| F11 barcode comparison removed | Different known barcode cannot replay |
| F11 AI-run comparison removed | Unrelated transcription cannot replay |
| F11 facts comparison removed | Changed canonical facts cannot replay |
| F11 concurrent different-barcode winner bypasses checking | Real race yields 201 + 409, never two successes |

No F12 mutant is claimed: investigation found no affected historical reconstruction path to correct.

## Local validation and platform limitation

- Focused frontend: 6 suites / 70 tests passed. Full frontend from the exact staged Git tree with canonical LF line endings: 84 suites / 1,385 tests passed. TypeScript, zero-warning ESLint, Android export and web export passed. No test was weakened to accommodate Windows CRLF checkout differences.
- The focused PostgreSQL run passed 365 tests; eight media fixtures were initially blocked by the shared Windows pytest temporary directory. Re-running the two affected suites with a task-owned temporary directory passed all 115 tests, including those eight cases.
- Full local backend validation was attempted but stopped at an existing Windows-only local-storage adapter failure: `test_the_real_deletion_lifecycle_removes_a_reporter_from_the_public_count` (418 passed, 1 failed). `list_prefix` returns backslash-separated relative paths on Windows, which the adapter's deletion guard rejects. The exact test also fails on an untouched archive of the starting main, with `StorageMisconfigured`. This unrelated adapter is not changed by Lane 5. Canonical Linux GitHub CI must supply the full-backend result; no local full-suite success is claimed.
- Local test infrastructure uses a disposable PostgreSQL 16 database, UTC like CI, and task-owned media/temp paths. Production is untouched. Ruff, compileall, `git diff --check`, empty-database Alembic upgrade and schema drift check passed; one head remains `o3p4q5r6s7`.

## Boundaries

No changes to F14/F15, resource safety, nutrition, Product Truth grading, commerce, B2B, notifications, dependencies, Node audit/Trivy governance, Render or production Supabase. No migration, deployment, production mutation, paid resource or Phase B. Store A/Open Food Facts separation and ₹0/free-tier behavior remain unchanged. Independent review is required; do not merge this lane automatically.
