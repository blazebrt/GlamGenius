# V3 BACKEND BASELINE

## Execution Environment

* **Python version**: 3.11.15
* **executable**: `C:\Users\saura\.gemini\antigravity\scratch\GlamGenius\backend\.venv\Scripts\python.exe`
* **venv**: `C:\Users\saura\.gemini\antigravity\scratch\GlamGenius\backend\.venv`
* **dependency installation**: Installed via `uv pip install -r requirements.txt`. (Note: `uv` was used purely as a fast installer; `uv.lock` or other `uv` project files were NOT added to the repository)
* **application import**: PASS (Confirmed by running `python -c "import server"`)

## Validation Results

* **Ruff result**: All checks passed
* **pytest result**: 497 collected; 449 passed, 47 failed, 0 skipped, 23 warnings, 1 error. (Execution time: 1353.43s)
* **database strategy**: PostgreSQL (local database `glamgenius_v2_test` running with credentials safely stored in ignored environment variable `$env:POSTGRES_URL`)
* **migration result**: Successfully ran `alembic upgrade head`. No Alembic migration source files were modified and no migration history was rewritten.
* **external-service isolation**: Safely configured; no production Supabase databases were touched and no real customer data was used.

## Verified Domain Paths

* **Today + Weekly Planner**: `backend/app/domains/planning`, `backend/app/api/v2/today.py`
* **Weather / Context**: `backend/app/domains/recommendation/context.py`
* **Occasion Styling**: `backend/app/domains/recommendation/occasions.py`, `backend/app/api/v2/style.py`
* **Shopping / Purchase**: `backend/app/domains/recommendation`
* **Appearance Profile**: `backend/app/domains/profile`
* **Shelf / Routines / Ingredients / Nutrition / Perfume / Supplements**: `backend/app/domains/routines`, `backend/app/domains/inventory`
* **Memory / Progress**: `backend/app/domains/progress`
* **Calendar / Events**: `backend/app/domains/planning`

## Defects / Blockers

* **47 pytest failures**: The majority of failures involve `test_domain_planning.py`, `test_domain_privacy_integration.py`, and `test_domain_progress_api.py`. Many failures stem from timezone (`zoneinfo`) issues specific to the environment, and incomplete migration of v2 services in integration tests.
* **1 error**: In `test_admin_workers.py`

## Conclusion

BLOCKED — Do not begin V3 backend work
