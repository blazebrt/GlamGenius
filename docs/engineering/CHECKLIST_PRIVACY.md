# Privacy review checklist

Use this checklist when the PR touches images, personal data, consent,
telemetry, logs, monitoring events, exports, or deletion. The bar for
privacy in GlamGenius is intentionally higher than the bar for a
generic app because photos of a user's face and body are the most
sensitive data this product touches.

## 1. Storage boundaries

- [ ] No new field stores raw image bytes on any record other than the
      media service's own records.
- [ ] No new field stores a base64 fragment, "preview thumbnail",
      "small copy" or "first N characters" of an image on any record.
      (This is the V1 defect Fix 12 will remove; do not re-introduce
      it.)
- [ ] Media references are opaque identifiers (`media_asset_id`,
      `content_hash`, `ai_run_id`) — never a slice of the media itself.
- [ ] The safety filter (`safety.narrative_is_safe`) sweeps any new
      AI-written string that gets persisted.

## 2. Data minimisation

- [ ] The PR does not persist a field it does not consume elsewhere.
      "We might want it later" is not a reason.
- [ ] The PR does not add a request/response field that leaks a
      derived fact not required for the current caller (e.g. exposing
      internal moderation confidence to the app).
- [ ] Feature flags that read personal data respect the current
      consent state (`REQUIRE_ANALYSIS_CONSENT` and equivalents).

## 3. Logs and monitoring

- [ ] No log line prints image bytes, image URLs signed with a bearer
      token, or any personal name / phone number / email in cleartext.
- [ ] The monitoring privacy scrubber
      (`backend/app/domains/monitoring/scrubbers.py`) still redacts the
      structures the PR introduces. Add a scrubber test if a new
      structure needs redacting.
- [ ] Sentry (or equivalent) event payloads carry no unredacted image
      URLs, no OAuth tokens, no request bodies for authenticated
      calls. The frontend event path scrubs before send.

## 4. Consent

- [ ] Any new use of a photo for AI analysis is gated on the
      analysis-consent flag.
- [ ] A user's revoked consent still stops the new code path from
      running.
- [ ] Any change to a consent copy-string ships with the same visual
      review as any other user-facing wording.

## 5. Retention and deletion

- [ ] Account deletion still removes the data introduced here. The
      deletion path is exercised by `test_privacy.py`.
- [ ] Media deletion is idempotent and cleans orphaned objects.
- [ ] Any new table stores a `deleted_at` (or joins to a parent that
      does) so a soft-delete request removes the record from user
      queries.

## 6. Third parties

- [ ] No new outbound call sends personal data to a service that is
      not already documented in
      [`docs/stabilisation/INTEGRATIONS.md`](../stabilisation/INTEGRATIONS.md)
      (once that file exists in Work Package 5) with the least-privilege
      scope, the retention terms, and the access date.
- [ ] If the PR predates that file, the reviewer records the third
      party in the PR description and opens a follow-up task to add it
      to the integrations register.

## 7. Cross-account isolation

- [ ] A regression test asserts that the new endpoint (or the modified
      one) refuses to expose account A's data to account B.
- [ ] Media reads still fail closed for a caller whose account does
      not own the referenced asset.

## 8. Payment surface

- [ ] The diff does not touch payment tables or the Razorpay call
      surface, per the non-payment stabilisation phase rules. See
      [`CHECKLIST_EVIDENCE.md`](CHECKLIST_EVIDENCE.md#payment-mechanics-untouched)
      for the reviewer's confirmation command.

## 9. Evidence

- [ ] `pytest -q tests/test_privacy.py tests/test_media.py
      tests/test_v1_regression.py` on the head commit is green.
- [ ] For any change to log fields, the PR includes a redacted before /
      after log line.

## 10. Sign-off

- [ ] Independent reviewer per [`REVIEW_POLICY.md`](REVIEW_POLICY.md).
- [ ] Owner approval per CODEOWNERS if the diff matches privacy /
      media / consent paths.
