# Supabase Auth — Security Contract

_Last updated: 2026-01._

This document is the reference contract for how FastAPI validates Supabase
Auth tokens. It exists so anyone changing auth code has to reconcile it
against a written policy first.

## 1. Where the JWT comes from

The Expo app authenticates against Supabase Auth using the Supabase JS
SDK. Supabase returns an access token (JWT) and a refresh token. The
access token is short-lived (Supabase default: **1 hour**). The refresh
token rotates on use.

**FastAPI never sees passwords or refresh tokens.** The Expo client
attaches the access token to every business call:

```
Authorization: Bearer <supabase access token>
```

## 2. What FastAPI validates on every request

Implemented in `backend/app/shared/security/supabase_auth.py`.

1. **Header presence and shape.** The `Authorization` header must exist
   and start with `Bearer `. Missing or malformed → `401 UNAUTHORIZED`
   with `code: UNAUTHENTICATED`.
2. **Token decode.** The JWT header must parse. Unknown `alg` (anything
   outside `{RS256, ES256}`) → `401`.
3. **Signature.**
   - **RS256 / ES256 (preferred)**: fetched from
     `${SUPABASE_URL}/auth/v1/.well-known/jwks.json`. JWKS is cached in
     memory with a hard TTL (default 10 minutes) and a soft refresh on
     `kid` miss. Refresh has a 5-second network timeout and fails closed.
4. **Issuer.** Must equal `SUPABASE_JWT_ISSUER`, which defaults to
   `${SUPABASE_URL}/auth/v1`.
5. **Expiry (`exp`).** Rejected with a **0-second** clock skew tolerance.
   No leeway.
6. **`sub` claim.** Must be present and must parse as a UUID. This is the
   canonical `account_id`. Anything supplied in the request body or URL
   is ignored.
7. **`aud` claim.** If present, must equal `authenticated`. If Supabase
   omits it (some newer templates do), that is accepted.
8. **Failure mode.** Any validation error returns the structured error:
   ```json
   {"detail": {"code": "UNAUTHENTICATED",
               "message": "Authentication required.",
               "retryable": false,
               "request_id": "..."}}
   ```
   with HTTP `401`. The specific reason (bad signature vs expired vs
   missing kid) is logged server-side with the request id but is not
   leaked in the response body.

## 3. Cache and refresh

JWKS is fetched lazily on first request and cached in-process with:

- Hard TTL: **10 minutes**.
- Soft refresh: if a JWT arrives with a `kid` that is not currently
  cached, one refresh is attempted with a **5-second** deadline. If that
  refresh fails, the token is rejected — we never trust a `kid` we cannot
  verify.
- Concurrency: refresh is protected by an `asyncio.Lock` so a burst of
  requests only triggers one JWKS fetch.
- No unbounded retry loop. A failed refresh returns `401` for that
  request and the cache TTL is unchanged.

## 4. What we deliberately do not do

- We do not run our own login endpoint. Passwords are Supabase's problem.
- We do not issue our own tokens. There is no `/api/auth/*` on V2.
- We do not accept a client-declared account UUID under any circumstance,
  including admin flows. Admins are identified by the presence of their
  Supabase UUID in `SUPABASE_ADMIN_USER_IDS`.
- We do not implement a session cookie. Every business request carries
  its own bearer token.
- We do not fall back to an unauthenticated user for expired tokens. An
  expired token is `401`, not a downgrade to anonymous.

## 5. Canonical account identity

The Supabase UUID from the verified `sub` claim is the account id. In
code, `CurrentAccount` exposes `account_id: uuid.UUID`. It is the value
used in:

- FK relations (`account_id` column on every product table).
- Storage path scoping (`account/{account_id}/…`).
- Beta usage counters (`(account_id, period)` unique keys).
- Audit rows.

**There is no `account_links` table and no `v1_user_id`.** Any migration,
model, service or route that reintroduces either fails the schema
regression test (`backend/tests/test_schema_regression.py`).

## 6. Admin authorisation

Admin routes live under `/api/v2/access/admin/*`. The dependency is:

```
admin_required = Depends(require_admin(get_current_supabase_user))
```

`require_admin` checks that `current_account.account_id` is in
`SUPABASE_ADMIN_USER_IDS`, which is a **server-side, comma-separated
list of UUIDs**. There is no plaintext admin secret in env, in code, or
in tests. There is no admin header override.

Admin actions (invite create, deactivate, view) are written to
`audit_events` with the acting UUID and the target resource.

## 7. Threats considered

| Threat                                           | Mitigation                                                                                                     |
|--------------------------------------------------|----------------------------------------------------------------------------------------------------------------|
| Forged JWT with a client-controlled `sub`        | Signature verification against JWKS. Bad signature → 401.                                                      |
| Replay of an expired token                       | `exp` checked with 0 clock skew.                                                                               |
| Token from a different Supabase project          | Issuer check against `SUPABASE_JWT_ISSUER`.                                                                    |
| Malicious `kid` pointing at attacker-controlled key | Only JWKS URL derived from the trusted `SUPABASE_JWKS_URL` env is consulted. No `jku`/`x5u` header trusted. |
| JWKS endpoint outage                             | Cached keys keep working until TTL. Cache-miss during outage fails closed (401), not open.                    |
| Cross-account access via body-supplied UUID      | `account_id` is derived only from the token. Body/URL UUIDs are dropped.                                       |
| Privilege escalation via service-role key        | Service-role key is never sent to the client. `SUPABASE_ANON_KEY` is the only key in the Expo bundle.          |
| CORS misuse                                      | `ALLOWED_ORIGINS` is an explicit allowlist. The default excludes `*`.                                          |

## 8. Test coverage

`backend/tests/test_jwks_asymmetric.py` and `backend/tests/test_supabase_auth.py` cover:

- Missing `Authorization` header.
- Malformed header (`Bearer` with no token, wrong prefix).
- Invalid signature and explicit rejection of HS256 / unsigned (none) tokens.
- Wrong issuer.
- Expired token.
- Missing `sub`.
- Non-UUID `sub`.
- Successful RS256 verification (mocked JWKS).
- Cross-account request rejected when the body attempts to override
  `account_id`.

## 9. Rotation and incident response

- **Rotating the service-role key**: rotate in Supabase dashboard →
  update `SUPABASE_SERVICE_ROLE_KEY` in the deployment environment →
  restart FastAPI. Clients are unaffected.
- **Rotating the anon key**: rotate → update `SUPABASE_ANON_KEY` in Expo
  build config → publish a new build.
- **Invalidating all sessions**: Supabase dashboard → Auth → "Sign out
  everyone". FastAPI needs no change.
- **Suspected leak of an admin UUID**: remove the UUID from
  `SUPABASE_ADMIN_USER_IDS` and redeploy. The user retains their normal
  account access but can no longer perform admin actions.
