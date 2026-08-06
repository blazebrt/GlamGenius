# GlamGenius Operations Runbook

## Production deployment
1. Merge PRs into `main`.
2. Push a new Git tag or create a GitHub Release to trigger the `Release` CI workflow.
3. Observe `.github/workflows/release.yml` completing successfully.
4. The deployment updates the production Docker container running on the host.

## Rollback
1. Identify the previous working container image tag or commit hash.
2. In the hosting environment, update the `.env` or compose file to point to the previous tag.
3. Restart the container: `docker compose up -d`.
4. Note: Rollbacks do not revert database migrations. If a database schema change is incompatible with the old code, you must manually run a reverse migration (`alembic downgrade`).

## Database migration
1. Create a migration locally: `alembic revision --autogenerate -m "description"`.
2. Commit and merge to `main`.
3. The release orchestration script (`python backend/app/cli/release.py`) runs `alembic upgrade head` before booting the API.

## Reference-data seeding
- Run `python -m app.cli.seed` inside the production environment to idempotently insert required catalog data (brands, products, routines). This is automatically triggered by the release script.

## API health/readiness
- `/health`: Liveness probe. Validates basic process function.
- `/ready`: Readiness probe. Validates PostgreSQL connectivity, seed completion, Gemini AI availability, and background worker liveness.

## Account-deletion worker
- A Celery worker instance (`celery -A app.worker worker`) processes account deletions asynchronously.
- Failures in the worker are automatically retried using exponential backoff.
- The `account_deletion_jobs` table tracks the deletion lifecycle.

## Queue failure recovery
- Dead-letter queues hold failed jobs.
- The admin dashboard (to be built) or direct DB queries can requeue jobs manually by resetting `status = 'pending'`.

## Outages
- **Supabase**: If Supabase Auth or DB is down, the `/ready` probe will fail. Users cannot log in or fetch data. The app will show "Network error" to users. Wait for Supabase recovery.
- **Gemini**: The app falls back to `GEMINI_FALLBACK_MODELS`. If all fail, the `/ready` probe fails and AI features show temporary unavailability.
- **Sentry**: Telemetry is lost, but the app continues functioning.
- **Storage**: Image uploads fail. Signed URLs fail. The app functions normally for text-only operations.

## Email delivery failure
- Supabase Auth handles transactional emails (confirmations, resets). Check Supabase logs for bounce rates and SMTP issues.

## Credential rotation & Secret revocation
1. Generate new keys in Supabase/Gemini.
2. Update the host `.env` file.
3. Restart the backend container.
4. If a secret was leaked, revoke the old one immediately in the provider dashboard.

## Incident response
1. Acknowledge incident in Sentry or monitoring alerts.
2. Identify scope (API, DB, AI).
3. If data is corrupting, stop the container to prevent further damage.
4. Fix the issue, write regression tests, deploy hotfix.

## Privacy incident response
- If private data (e.g., routines or photos) is exposed:
1. Revoke public URLs immediately if storage misconfigured.
2. If DB ACLs failed, disable the API.
3. Notify affected users within 72 hours per GDPR guidelines.

## Backup and restore
- Supabase provides automated daily backups (Pro plan).
- Point-in-time recovery (PITR) is available on the Supabase dashboard.
- Restore drill: Use Supabase CLI to pull a logical dump and restore it to a staging instance.

## Release checklist
- CI passed?
- DB test passed?
- Container scanner passed?
- Staging validated?

## Post-release monitoring
- Monitor Sentry for new error spikes in the 1 hour following a release.
- Check Supabase DB load (CPU/Memory).

## Cost & Abuse-control monitoring
- Monitor Gemini token usage via Google Cloud console.
- Supabase logs provide API request rates.
- The app implements rate-limiting in Redis/DB for scans, styles, and routines per month.
