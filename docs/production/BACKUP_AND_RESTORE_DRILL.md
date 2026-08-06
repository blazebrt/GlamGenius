# Backup and Restore Drill

This document outlines the standard operating procedure for backing up and restoring the GlamGenius production database on Supabase, and logs the results of the staging simulation.

## Architecture & Coverage

GlamGenius relies on Supabase for data persistence. This includes:
1. **PostgreSQL Database**: Contains all user profiles, recommendations, wardrobes, and application data.
2. **Auth Service**: Contains the identities and credentials.
3. **Storage**: Contains uploaded photos and generated assets.

*Note: Supabase Point-In-Time-Recovery (PITR) automatically backs up the Postgres database. Storage and Auth require distinct strategies if migrated entirely.*

## Routine Drill Procedures

1. **Logical Dump Generation**: Use `supabase db dump -f snapshot.sql` to capture a full backup of the database structure and data.
2. **Local Restoration**: Spin up a fresh Supabase local container (`supabase start`) or a plain Postgres instance and restore the snapshot via `psql`.
3. **Data Verification**: Run integration tests against the restored local instance to verify data integrity and schema validity.
4. **Storage Backup Simulation**: Sync the Supabase storage bucket using AWS CLI or `rclone` to a cold-storage S3 bucket.

## Drill Execution Log

**Date:** YYYY-MM-DD
**Environment:** Staging
**Operator:** CI/CD / Admin

**Results:**
- **Postgres Dump:** SKIPPED (Missing live credentials)
- **Local Restore:** SKIPPED (Missing live credentials)
- **Integrity Validation:** PENDING LIVE EXECUTION

**Conclusion:** 
The simulation script (`scripts/simulate_backup_restore.sh`) successfully runs through the logical steps, but the *actual* backup and restore drill is marked **INCOMPLETE** due to missing `SUPABASE_SERVICE_ROLE_KEY` and administrative credentials. This must be executed successfully with real data before the final production GO.
