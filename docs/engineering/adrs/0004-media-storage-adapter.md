# ADR 0004 — Media storage adapter with production-refusal guard

**Status:** Accepted. **Date:** 2026-08-03. **Deciders:** @blazebrt.

## Context

Media uploads (photos) are the most sensitive data the product
touches. The V2 rewrite abstracts storage behind a `MediaStorage`
protocol so development can run on the local filesystem while
production runs on an S3-compatible object store.

## Decision

- `MediaStorage` protocol in
  `backend/app/domains/media/storage/base.py`.
- `LocalFilesystemStorage` for development, honouring the
  `MEDIA_LOCAL_ROOT` env variable.
- `S3CompatibleStorage` for production, backed by boto3 (Fix 9).
  Server-side encryption is sent on every PUT; presigned GET
  URLs are clamped to `[60, 900]` seconds.
- `factory.get_storage()` **refuses to boot** with
  `MEDIA_STORAGE_BACKEND=local` when `APP_ENV=production` unless
  the operator sets `MEDIA_ALLOW_LOCAL_IN_PRODUCTION=true`.

## Consequences

- Production cannot silently lose uploads to a pod's local disk.
- Development retains a fast, zero-credential loop.
- Adding a new provider is a new adapter class, not a rewrite.

## Alternatives considered

- **Single provider (S3 only)**: rejected. Developers would need
  cloud credentials to run the app locally.
- **No production guard**: rejected. A misconfigured deployment
  should fail visibly, not silently.

## Related

- `docs/stabilisation/MEDIA_STORAGE_OPERATIONS.md`
- Fix 9 (WP2)
