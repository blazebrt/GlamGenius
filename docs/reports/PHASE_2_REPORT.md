# Phase 2 Report — Appearance Digital Twin and Progressive Onboarding

**Baseline:** `401a4f6` (`main`, merged Phase 1)
**Branch:** `v2/phase-2-digital-twin`
**Date:** 2026-08-01

## Outcome

Phase 2 adds a structured, versioned appearance profile without replacing the legacy
MongoDB user record. User-declared facts, photo observations, later behavioural or
integration inferences, and stylist-verified values have distinct provenance. An AI result
creates a reviewable observation only; it never writes a confirmed attribute.

The first onboarding question is “What are you preparing for?” A user may finish with that
single goal and receive a useful first direction. Style, fit, lifestyle and photo steps are
optional, saved after each step, and resumable. Weight is not requested or accepted by the
new profile API.

## Architecture delivered

The new `app/domains/profile` module contains:

- a controlled attribute registry and value validation
- profile creation, relational projections, readiness, versioning and change history
- confidence thresholds and an observation inbox
- confirm, edit, reject and not-sure verification transitions
- a resumable onboarding state machine
- an optional baseline analysis schema and prompt routed through the Phase 1 AI gateway

The profile is protected by the `v2_profile` feature flag and the existing authenticated
V2 account dependency. Ownership is derived from the bearer token; no profile or
observation endpoint accepts a user id.

## Database migration

Migration `0002_appearance_digital_twin` adds ten PostgreSQL tables:

1. `appearance_profiles`
2. `profile_attributes`
3. `style_preferences`
4. `fit_preferences`
5. `lifestyle_context`
6. `appearance_goals`
7. `user_constraints`
8. `attribute_observations`
9. `onboarding_sessions`
10. `profile_change_events`

Every attribute has a controlled key, value, source, confidence, verification state,
timestamps, optional review/expiry dates, and optional AI-run foreign key. JSONB is limited
to typed values, list columns, onboarding step state and event snapshots; it is not an
uncontrolled profile document.

## API delivered

- `GET/PATCH /api/v2/profile`
- `POST /api/v2/profile/baseline-analysis`
- `GET /api/v2/profile/attributes`
- `GET /api/v2/profile/observations`
- `POST /api/v2/profile/observations/{id}/confirm`
- `POST /api/v2/profile/observations/{id}/reject`
- `PATCH /api/v2/profile/observations/{id}`
- `GET /api/v2/onboarding/status`
- `POST /api/v2/onboarding/step`
- `POST /api/v2/onboarding/complete`

The Phase 1 privacy export now includes attributes, all observation states, profile change
history and onboarding state.

## User experience delivered

- A premium, one-decision-at-a-time onboarding flow.
- Concrete goal options for work, events, travel, wardrobe and routines.
- Optional style, size/fit, city/lifestyle and photo steps.
- Explicit consent before the optional photo leaves the device.
- Honest low-quality-photo response with a skip path and no guessed result.
- A colour starting result when the image is usable.
- Observation cards showing value, confidence and visible reasoning.
- Accessible Confirm, Edit, Reject and Not sure controls.
- “My Appearance” with verified sections, unverified observations and decision readiness.
- No generic completion percentage and no mandatory or central weight field.
- Registration enters onboarding; returning users continue to the existing home screen.

## Trust and precedence rules

- `user_declared` updates are confirmed with confidence `1.0`.
- Photo output below confidence `0.35` is not presented as an observation.
- No confidence threshold auto-confirms a value.
- A baseline run never changes a current profile attribute.
- Confirmation is an explicit user action and writes a new profile version.
- Rejection is persisted and cannot later be confirmed or edited.
- The same AI run/key cannot duplicate a rejected observation; a later run is new evidence.
- Optional face images are transient provider input and are never stored in media,
  onboarding or profile tables.

## Verification evidence

### Backend

```text
alembic upgrade head
alembic check
104 passed, 189 warnings in 24.63s
```

The 104 tests include 11 Phase 2 tests covering authentication, ownership, provenance,
confirmed-value precedence, confidence filtering, change history, rejection persistence,
invalid measurements, no weight attribute, low-quality images, partial/resumed onboarding,
optional steps and the first result. Existing warnings are Pydantic/FastAPI deprecations
and the intentionally short test JWT key.

### Frontend

```text
TypeScript: clean
Lint: 0 errors, 3 pre-existing hook warnings
Test Suites: 5 passed, 5 total
Tests: 49 passed, 49 total
```

Seven new frontend tests cover goal validation, concrete options, radio accessibility,
navigation recovery, confidence wording and accessible observation actions.

### Runtime smoke

- Fresh PostgreSQL applied `0001` then `0002` and reported 21 public tables.
- `/api/v2/health` returned healthy/PostgreSQL up.
- Startup logged `v2_profile` enabled.
- Worker started after the migrated backend became healthy, with no outbox error.
- Unauthenticated profile and onboarding requests both returned 401.
- Expo production export succeeded for all 23 static routes, including `/onboarding` and
  `/my-appearance`.

## Live Gemini verification

A real Gemini credential was configured in the ignored local `.env`; it was not committed
or written to this report. Both health endpoints reported the provider ready, and the first
live face-analysis request reached `gemini-2.5-flash` and returned HTTP 200. Subsequent
requests received HTTP 429 from Google for both configured models, so the external suite
could not complete successfully and is not represented as passing.

The live runner completed 9 of 18 checks. Its first assertion also still expects the legacy
`wellness_scores` object, while the current safety contract intentionally excludes
unversioned appearance scores. The deterministic container suite remains the authoritative
Phase 2 contract check for schema, failure, low-quality, provenance and no-overwrite
behaviour until the live runner is updated and sufficient provider quota is available.

## Rollback

Disable only the Phase 2 routes by removing `v2_profile` from `V2_FEATURES`. To remove the
Phase 2 schema while retaining Phase 1:

```bash
docker compose exec backend alembic downgrade 0001_v2_foundation
```

MongoDB and all V1 routes remain untouched.

## Acceptance criteria

| Criterion | Status | Evidence |
|---|---|---|
| Minimum onboarding under five minutes | ✅ | Only the concrete goal is required; all later steps expose Skip |
| Weight is not mandatory | ✅ | Absent from registry/onboarding; API rejects `weight_kg` |
| Every inferred value is actionable | ✅ | Confirm, edit, reject and not-sure API/UI controls |
| Confirmed values cannot be silently overwritten | ✅ | Baseline creates observations only; precedence test |
| Rejected values persist | ✅ | Terminal rejected state, history list and same-evidence deduplication |
| Partial onboarding resumes | ✅ | Per-step session state and resume test |
| Useful first result | ✅ | Deterministic preview returned by completion endpoint |
| Source and confidence | ✅ | Required relational columns and API response fields |
| Relevant provider-independent tests | ✅ | 104 backend and 49 frontend tests; production export and cold start pass |
| Live Gemini end-to-end acceptance | ⚠️ | Provider configured and one call succeeded; full runner blocked by Google 429 quota and a stale legacy score assertion |
| Report exists | ✅ | This file |
| Phase committed | ✅ | Implementation commit recorded below |

## Commit

```text
d46af80  feat(v2): add appearance digital twin and progressive onboarding
```

Phase 3 was not started.
