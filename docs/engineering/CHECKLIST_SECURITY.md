# Security review checklist

Use this checklist when the PR touches authentication, authorization,
session handling, invite/consent, secrets, or any code path that grants
or revokes access to a resource. Every item is either satisfied, marked
`N/A` with a one-line reason, or the PR does not merge.

## 1. Authorisation

- [ ] No endpoint accepts `account_id`, `user_id`, `owner_id` or an
      equivalent identifier from the request body, query string, or
      route parameter for a write path.
- [ ] Every write path is scoped to the caller's authenticated
      `account_id` (`current.account_id` or equivalent).
- [ ] Reads that expose another account's data would fail; a
      regression test in `test_privacy.py` proves it.
- [ ] Admin-only paths check the admin secret / role explicitly. The
      absence of an admin secret means the endpoint is unavailable, not
      that everyone is an admin.
- [ ] Rate limits or lockouts on the affected route are unchanged, or
      the change is described in the PR.

## 2. Authentication

- [ ] Password hashing uses the existing scheme (`passlib[bcrypt]`).
      No hand-rolled hash. No plaintext comparison.
- [ ] JWT signing uses `JWT_SECRET` from the environment. No default,
      no in-code fallback, no dev-mode bypass that could ship.
- [ ] Token lifetimes are unchanged, or the change is described.
- [ ] Password reset and account recovery still verify identity with
      the existing mechanism.
- [ ] Session invalidation on logout / password change still works.

## 3. Secrets

- [ ] No secret material is committed. Search the diff for
      `sk_`, `pk_`, `ghp_`, `AKIA`, `AIzaSy`, `-----BEGIN`,
      `api_key`, `client_secret`.
- [ ] The Gitleaks CI job (`Secret scan`) is green.
- [ ] Any new environment variable is documented in `env.example`
      with a placeholder value.
- [ ] `.env`, `.env.local`, `.env.production` and equivalents remain
      gitignored.
- [ ] Secrets used in tests are non-production placeholder strings.

## 4. Input validation

- [ ] Every new field on a Pydantic model has an explicit type and,
      where relevant, a length / range / enum constraint.
- [ ] Free-text fields that will be stored have an upper length.
- [ ] Image and file uploads still go through the media validator
      (`backend/app/shared/validation/media.py`).
- [ ] URL and redirect targets are validated against an allow-list
      when applicable (see the `.emergent` cron dispatcher hardening
      in Work Package 1).

## 5. Output encoding

- [ ] Error responses do not leak stack traces, SQL, or full request
      payloads in production mode.
- [ ] Server logs do not include secrets, tokens, or full request
      bodies for authenticated calls.
- [ ] Log lines describing user data pass through the monitoring
      scrubber (`backend/app/domains/monitoring/scrubbers.py` and its
      privacy test).

## 6. Cryptography

- [ ] No new use of `md5`, `sha1`, `random.random`, `random.randint`
      for a security purpose.
- [ ] `secrets.token_urlsafe` or `secrets.token_bytes` is used for
      tokens.
- [ ] Any AES / GCM / KDF use points at a vetted implementation
      (`cryptography` package), not hand-rolled.

## 7. External calls

- [ ] Every outbound HTTP call has a timeout.
- [ ] Every outbound HTTP call runs on a limited-privilege client
      (no `verify=False`, no swallowed TLS errors).
- [ ] Redirects from an outbound call do not forward Authorization
      headers to a host outside a documented allow-list.
- [ ] Retries are bounded and idempotent (see also
      [`CHECKLIST_EXTERNAL_INTEGRATION.md`](CHECKLIST_EXTERNAL_INTEGRATION.md)).

## 8. Dependencies

- [ ] `pip-audit` and `yarn audit` in CI are green (see
      `.github/workflows/ci.yml`). A HIGH or CRITICAL advisory blocks
      merge.
- [ ] The PR does not pin a dependency backwards to escape an
      advisory.

## 9. Evidence

- [ ] The PR description names the specific tests that cover the
      change and pastes the CI run URL or output.
- [ ] If a real-world test was needed (e.g. a captured production
      log), the test is redacted before it is pasted.

## 10. Sign-off

- [ ] Independent reviewer per [`REVIEW_POLICY.md §3`](REVIEW_POLICY.md#3-what-counts-as-an-independent-reviewer).
- [ ] Owner approval per CODEOWNERS if the diff matches an owner rule.
