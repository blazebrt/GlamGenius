# Media storage — production operations

Fix 9 (Work Package 2) — production runbook for the S3-compatible
media adapter. This document is what an operator reads before
switching a live deployment off the local filesystem adapter.

## 1. Why the local adapter is not for production

`backend/app/domains/media/storage/local.py` writes to the pod's own
disk under `MEDIA_LOCAL_ROOT` (`/data/media` in the shipped compose
file). In production that is wrong in three ways:

1. A redeploy replaces the pod and destroys the volume unless a
   PersistentVolumeClaim is explicitly mounted. Rolling a release
   silently deletes every upload since the previous release.
2. A second pod cannot see the first pod's writes. Once traffic is
   scaled, users see other users' uploads intermittently, depending
   on which pod answered the request.
3. Every backup path in the platform expects the object store, not
   the pod filesystem. Snapshotting an EBS/Ceph volume covers the
   database, not the media directory.

`get_storage()` therefore refuses to boot with
`MEDIA_STORAGE_BACKEND=local` when `APP_ENV=production`, unless
the operator has consciously set
`MEDIA_ALLOW_LOCAL_IN_PRODUCTION=true`. The refusal happens **at
first use of the storage adapter**, not lazily on the first upload,
so a mis-configured deployment fails observably rather than accepting
uploads it will lose.

The escape hatch exists for a single-pod dev-preview where the
operator understands the trade-off. If you set it, expect uploads to
be lost on the next redeploy.

## 2. What the S3-compatible adapter delivers

`backend/app/domains/media/storage/s3.py` speaks the S3 REST API via
`boto3`. It is tested against MinIO in CI (see §5) and shipped
against Cloudflare R2 / AWS S3 / DigitalOcean Spaces / equivalent in
production.

Guarantees the adapter carries:

| Guarantee | Enforced by |
|---|---|
| Private objects by default | The adapter never sends `ACL=public-read`. Bucket policy must also default-deny anonymous access. |
| Owner-scoped keys | `build_key(account_id, asset_id, mime)` in `storage/base.py` shards every object under `media/<account_id>/<asset_id>.<ext>`; nothing outside the service layer can construct a key that escapes that shape. |
| Server-side encryption | Every `put_object` sends `ServerSideEncryption=<S3_SERVER_SIDE_ENCRYPTION>` (default `AES256`). Set the setting to `"aws:kms"` when a KMS key is configured; set it to empty string only if the provider refuses the header and the bucket has default encryption configured out of band. |
| Short-lived signed access | `presigned_get_url(key, ttl_seconds)` returns a boto3-signed URL. The TTL is clamped at `[60, 900]` seconds inside the adapter; a caller cannot request a multi-day link. |
| Backend streaming fallback | The local adapter's `presigned_get_url` returns `None`. The service layer falls back to backend streaming, so a client never receives a cleartext filesystem path. |
| Idempotent deletion | Both adapters treat "delete an object that is already gone" as success. |
| MIME sniffed on upload | `app/shared/validation/media.py` sniffs the leading bytes and refuses any file whose declared type does not match. This is provider-agnostic. |
| Size ceiling | `MEDIA_MAX_BYTES` (default 8 MiB) is enforced before the bytes reach the storage backend. |

## 3. Configuration

Required environment variables when `MEDIA_STORAGE_BACKEND=s3`:

| Name | Purpose | Example |
|---|---|---|
| `S3_ENDPOINT_URL` | S3-compatible endpoint. Leave empty for AWS S3 default. | `https://<accountid>.r2.cloudflarestorage.com` |
| `S3_BUCKET` | Bucket name; must already exist. | `glamgenius-media-prod` |
| `S3_REGION` | Region. `auto` is fine for R2. | `auto` / `ap-south-1` |
| `S3_ACCESS_KEY_ID` | Least-privilege credential (see §6). | — |
| `S3_SECRET_ACCESS_KEY` | Least-privilege credential (see §6). | — |
| `S3_SIGNED_URL_TTL_SECONDS` | Presigned-GET TTL (clamped `[60, 900]`). | `300` |
| `S3_SERVER_SIDE_ENCRYPTION` | SSE header; empty disables. | `AES256` |
| `MEDIA_STORAGE_BACKEND` | Must be `s3` in production. | `s3` |
| `APP_ENV` | Must be `production` for the guard to be active. | `production` |
| `MEDIA_ALLOW_LOCAL_IN_PRODUCTION` | Do **not** set to `true` in production. | (unset) |

## 4. Bucket lifecycle

Configure the following bucket policies at provider setup — the
application does not attempt to reconfigure the bucket at runtime:

1. **Default deny.** Anonymous `s3:GetObject` is refused. All reads
   go through presigned URLs or backend streaming.
2. **Default encryption.** Turn on the provider's bucket-default
   encryption in addition to the per-object header. Belt-and-braces.
3. **Versioning.** Enabled with a lifecycle rule that expires
   non-current versions after 30 days. Recovers accidental deletes;
   caps cost.
4. **Lifecycle expiry on the `media/` prefix.** No automatic
   deletion — retention is user-driven via the app's account-delete
   path, which erases every asset the user owned. If a regulatory
   policy requires shorter retention, add it here and record the
   policy in `docs/stabilisation/INTEGRATIONS.md`.
5. **Abort incomplete multipart uploads.** 7-day expiry on aborted
   uploads keeps the bill honest.

## 5. CI integration (MinIO)

The docker-compose test stack starts a MinIO container alongside
Postgres and Mongo. The `backend-tests` service runs with the
following environment:

```
S3_INTEGRATION_ENABLED=true
S3_ENDPOINT_URL=http://minio:9000
S3_BUCKET=glamgenius-test
S3_ACCESS_KEY_ID=minioadmin
S3_SECRET_ACCESS_KEY=minioadmin
```

`backend/tests/test_media_s3.py` is skipped when
`S3_INTEGRATION_ENABLED` is unset — this keeps the
provider-independent pytest run runnable on a laptop without any
S3 service.

The integration test exercises:

- put / get / exists / delete round-trip
- idempotent delete on a missing key
- `StorageError` on read of a missing key
- presigned URL TTL clamping (60 – 900 seconds)
- server-side-encryption header round-trip
- account-shard listing isolation (account A's prefix cannot see
  account B's objects)

`test_media_production_guard.py` exercises the boot-time refusal
(unit-level; no infrastructure needed).

## 6. Credential scope

The IAM policy / MinIO user attached to
`S3_ACCESS_KEY_ID / S3_SECRET_ACCESS_KEY` must be limited to:

- `s3:PutObject`
- `s3:GetObject`
- `s3:DeleteObject`
- `s3:HeadObject`
- `s3:AbortMultipartUpload`

…on the resource `arn:aws:s3:::<bucket>/media/*`. **Do not** grant
`s3:ListBucket` on the top-level bucket, `s3:GetBucketAcl`,
`s3:PutBucketPolicy`, or any operation whose scope includes another
service's prefix. The app never lists the whole bucket.

Rotate the credentials at least quarterly. The rotation is a config
change (two environment variables) followed by a rolling restart;
the app holds no in-memory reference beyond a single request.

## 7. Local-development story

Local development still uses `MEDIA_STORAGE_BACKEND=local`. Nothing
in Fix 9 forces boto3 traffic on developers.

`APP_ENV=development` is the default; the guard is not active. If a
developer accidentally sets `APP_ENV=production` in their local `.env`
and boots, the guard raises `StorageError` before the first upload —
the developer sees the message, sets `APP_ENV=development` back, and
carries on.

## 8. Payment mechanics

Fix 9 does not touch payment mechanics. No file under
`backend/app/domains/billing/`, `backend/routes/billing.py`, or the
Razorpay call surface is modified.
`SUBSCRIPTIONS_AVAILABLE=false` is unchanged. Media storage is
independent of billing.

## 9. Cross-references

- [`backend/app/domains/media/storage/base.py`](../../backend/app/domains/media/storage/base.py) — protocol.
- [`backend/app/domains/media/storage/local.py`](../../backend/app/domains/media/storage/local.py) — dev adapter.
- [`backend/app/domains/media/storage/s3.py`](../../backend/app/domains/media/storage/s3.py) — production adapter.
- [`backend/app/domains/media/storage/factory.py`](../../backend/app/domains/media/storage/factory.py) — the production-refusal guard lives here.
- [`backend/app/shared/validation/media.py`](../../backend/app/shared/validation/media.py) — MIME sniffing and size ceiling.
- [`backend/tests/test_media_s3.py`](../../backend/tests/test_media_s3.py) — MinIO integration tests.
- [`backend/tests/test_media_production_guard.py`](../../backend/tests/test_media_production_guard.py) — production-refusal unit tests.
- [`docs/engineering/CHECKLIST_PRIVACY.md`](../engineering/CHECKLIST_PRIVACY.md) — the privacy checklist reviewers walk on every media-touching PR.
