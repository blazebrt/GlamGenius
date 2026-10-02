# Operations runbook — current authority redirect

**Status: REDIRECT, not a second production procedure.** The former contents
of this file described a nonexistent `.github/workflows/release.yml`, host
Docker Compose, Celery/dead-letter queues, Redis, an old release module and
assumed paid Supabase PITR. Those instructions are unsafe for the current
repository and remain available only in Git history.

Use [Running GlamGenius — Render Pre-PMF Runtime](../OPERATIONS.md#render-pre-pmf-runtime-zero-cost)
for the current exact-SHA release gate, scheduler, readiness, rollback and
incident procedures. Use [Scale readiness and SLOs](SCALE_READINESS_AND_SLOS.md)
for planning targets, backup/restore evidence gaps and incident maturity.
Use [Architecture authority](../architecture/ARCHITECTURE_AUTHORITY.md) to
distinguish implemented NOW behavior from NEXT/LATER proposals.

Before any production operation, verify the deployed SHA, live provider
settings and approved credentials using a secure channel. This redirect
authorizes no deployment, paid service, data restore or scientific activation.
