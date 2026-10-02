# Architecture authority — current versus historical

**Status: CURRENT / AUTHORITATIVE for document precedence.** Checked against
`main` `485729ab3ddba92c53257e7eea7fa2876212e3f7` on 2026-10-02. This
index describes the repository, not a live-provider configuration attestation.
When code and prose differ, inspect the exact deployed SHA and escalate the
discrepancy; an old document never authorizes a change to Product Truth.

## Precedence and use

1. [Product Constitution](../../PRODUCT_CONSTITUTION.md) and root `AGENTS.md`
   govern product boundaries. `render.yaml`, application code, migrations,
   current CI and exact-SHA deployment evidence govern implemented behavior.
2. [Scale architecture](SCALE_ARCHITECTURE_100CR.md) is the current topology,
   invariants and **future decision gates**. Its NEXT/LATER options are not
   deployment instructions.
3. [Operations](../OPERATIONS.md), beginning at **Render Pre-PMF Runtime**, is
   the one current deployment/runbook authority. Its clearly labelled earlier
   host-era sections are archival only. [Scale readiness and SLOs](../operations/SCALE_READINESS_AND_SLOS.md)
   governs planning targets and incident/restore maturity, not current SLAs.
4. Domain-specific current authorities below explain one boundary; they do not
   override code, Constitution or operations.
5. Roadmaps, V3 plans and old reports describe intent or history, never current
   production topology. A future agent must verify live code before acting.

| Class | Documents | How to read them |
| --- | --- | --- |
| CURRENT / AUTHORITATIVE | This index; [scale architecture](SCALE_ARCHITECTURE_100CR.md); [current operations](../OPERATIONS.md) from its Render section; [scale readiness](../operations/SCALE_READINESS_AND_SLOS.md); [organization](../company/SCALING_ORGANIZATION_100CR.md); [unit economics](../company/UNIT_ECONOMICS_AND_CAPACITY_MODEL.md) | NOW is implemented/current policy; NEXT requires its measured trigger and founder approval for spend; LATER is a candidate, not a commitment. |
| CURRENT BUT DOMAIN-SPECIFIC | [ODbL data wall](ODBL_DATA_WALL.md), [B2B V1](B2B_PRODUCT_TRUTH_API.md), [Commerce handoff](COMMERCE_HANDOFF.md), [Product Watch](PRODUCT_WATCH_MATERIAL_NOTICES.md), [privacy export](PRIVACY_EXPORT_COMPLETENESS.md), [Supabase auth security](SUPABASE_AUTH_SECURITY.md) | Boundaries for their own domain; compare to current implementation before operation. |
| OPERATIONAL RUNBOOK | [Current operations](../OPERATIONS.md) Render section | Only live deployment procedure. [Old short runbook](../operations/RUNBOOK.md) is a redirect, not a second procedure. |
| HISTORICAL / SUPERSEDED | [V3 appearance architecture](../V3_PRODUCT_ARCHITECTURE.md), [old engineering architecture](../engineering/ARCHITECTURE.md), [2026-01 Supabase target](SUPABASE_TARGET_ARCHITECTURE.md), [backup-drill draft](../production/BACKUP_AND_RESTORE_DRILL.md), `docs/reports/PHASE_*`, `docs/stabilisation/*`, early sections of `docs/OPERATIONS.md` | Preserve for provenance; do not follow old navigation, Mongo/V1, Celery, Redis/S3, release.yml, host systemd/cron or assumed paid-backup instructions. |
| ROADMAP / PLANNING | `docs/V3_*`, past phase plans and proposals; NEXT/LATER sections of the Step 18 authorities | No authority to provision, deploy, activate science, hire or spend. |

## Drift decisions made in Step 18

- `docs/operations/RUNBOOK.md` named a nonexistent Release workflow,
  `backend/app/cli/release.py`, Celery/dead-letter queues, Redis and paid PITR.
  **Updated** to a pointer to the live operations authority; Git history keeps
  the old text.
- `docs/OPERATIONS.md` began with host-era backup, systemd/cron and continuous
  worker instructions, while its later Render section describes the current
  free-tier model. **Marked the earlier material historical** and corrected a
  category-count claim in the live release procedure. The live section is the
  single deployment authority.
- `docs/engineering/ARCHITECTURE.md` says it is today's map but depicts
  Mongo/V1, S3 and Razorpay. `docs/V3_PRODUCT_ARCHITECTURE.md` depicts the
  retired appearance/Style navigation. **Marked both historical**; do not
  mechanically remove their design record.
- `docs/architecture/SUPABASE_TARGET_ARCHITECTURE.md` is a January target with
  one application database in its diagram and predates the physical Store A/B
  split. **Marked historical**. The current ODbL wall and code prevail.
- `docs/production/BACKUP_AND_RESTORE_DRILL.md` is an unexecuted draft that
  assumes paid PITR. **Marked historical / unverified**; current backup/restore
  evidence and future targets are tracked in scale readiness.
- Other occurrences of `Mongo`, `Celery`, `Redis`, old navigation and
  `release.yml` in phase/stabilisation reports are **historical citations**, not
  live instructions. They remain linked here instead of being mass-deleted.

## Change discipline

Update the current authority and its evidence pointer in the same reviewed PR
as an architecture change. Reclassify an historical page only after comparing
it to code and the deployed SHA. Never treat a future option as approval to
buy infrastructure, change science, merge stores or weaken privacy.
