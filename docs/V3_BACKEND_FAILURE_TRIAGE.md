# V3 Backend Failure Triage

This document contains the root cause analysis for the 47 failures and 1 error discovered during the GlamGenius V3-00 baseline test run.

## Failure Clusters & Root Causes

### 1. `ZoneInfoNotFoundError` (Windows Environment Constraint)
- **Symptom**: `zoneinfo._common.ZoneInfoNotFoundError: 'No time zone found with key Asia/Kolkata'`
- **Scope**: Causes the vast majority of the 47 test failures across `test_domain_planning.py`, `test_domain_progress_api.py`, `test_domain_routines_api.py`, `test_domain_privacy_integration.py`, and `test_critical_journey.py`.
- **Root Cause**: The application relies on `ZoneInfo("Asia/Kolkata")` (likely standard in `app/domains/planning/clock.py` or similar). Python's `zoneinfo` module on Windows lacks a native IANA time zone database. While Linux/macOS and CI environments have this built-in, local Windows development environments require the `tzdata` package to be installed.
- **Proposed Fix**: Add `tzdata; sys_platform == "win32"` to `requirements.txt` to enable cross-platform local development without affecting production dependencies, or install it in the local environment during setup.

### 2. Alembic Path Hardcoding in Health Check
- **Symptom**: `AssertionError: 503 == 200` in `test_health_ready.py::test_ready_ok_during_normal_operation`. The health endpoint returns `{"alembic_status": "error: Path doesn't exist: '...\\backend\\alembic'"}`.
- **Scope**: 1 test failure (`tests/test_health_ready.py`).
- **Root Cause**: The repository's migrations are located in the `migrations` folder (correctly configured in `alembic.ini`). However, the `alembic_status` component check in `app/api/v2/config.py` hardcodes the script location to `"alembic"` (`alembic_cfg.set_main_option("script_location", os.path.join(backend_dir, "alembic"))`), leading to a missing directory error on the health check endpoint.
- **Proposed Fix**: Change `os.path.join(backend_dir, "alembic")` to `os.path.join(backend_dir, "migrations")` in `app/api/v2/config.py`.

### 3. Missing `PRIVACY_POLICY_URL` in Production Config Test
- **Symptom**: `RuntimeError: CRITICAL: PRIVACY_POLICY_URL must be a real URL in production.` during `tests/test_production_config.py`.
- **Scope**: 1 test failure (`tests/test_production_config.py::test_valid_production_config`).
- **Root Cause**: The test executes a production simulation (likely setting `IS_PRODUCTION=true` in a subprocess) to assert the server boots securely. The application requires `PRIVACY_POLICY_URL` to be present when running in production, but the test environment setup does not provide a dummy value for it.
- **Proposed Fix**: Inject `PRIVACY_POLICY_URL=https://example.com/privacy` into the subprocess environment inside `test_valid_production_config`.

### 4. Database Deadlocks during Cleanup (TRUNCATE)
- **Symptom**: `sqlalchemy.exc.DBAPIError: DeadlockDetectedError`
- **Scope**: Occasional failures in `test_domain_progress_api.py` and `test_domain_privacy_integration.py`.
- **Root Cause**: Asynchronous tests are truncating tables concurrently during fixture teardown/setup without sufficient isolation or locking. PostgreSQL detects a cyclic dependency in lock acquisition and kills one of the transactions.
- **Proposed Fix**: Ensure the `db_clean` fixture uses cascading or ordered truncates, or lock the database cleanup operation so it doesn't conflict during parallel asynchronous test execution.

### 5. Missing `admin_client` Fixture (The 1 Error)
- **Symptom**: `fixture 'admin_client' not found` at the setup phase of `tests/test_admin_workers.py`.
- **Scope**: 1 test error (`tests/test_admin_workers.py`).
- **Root Cause**: The test file or function requests an `admin_client` fixture, but no such fixture is defined in `conftest.py` or the test file itself.
- **Proposed Fix**: Define the `admin_client` fixture in `conftest.py` (or locally), likely mirroring `app_client` but with admin authorization headers injected.

---

## Proposed Remediation Groups

The failures should be tackled in small, controlled groups to ensure safe progression.

### **V3-00.2A: Environment & Configuration Remediation**
- Fix the `tzdata` requirement for Windows to eliminate `ZoneInfoNotFoundError`.
- Fix the production config test by providing the missing `PRIVACY_POLICY_URL`.
- *Expected Outcome*: Massive reduction in failures; unlocks accurate visibility into actual application bugs.

### **V3-00.2B: Fixtures & Database Concurrency Remediation**
- Define the missing `admin_client` fixture to resolve the 1 setup Error.
- Address the `DeadlockDetectedError` by resolving concurrent `TRUNCATE` operations in test database setup/teardown.
- *Expected Outcome*: Resolves random/flaky test failures and setup errors.

### **V3-00.2C: Application Logic Remediation**
- Fix the hardcoded `"alembic"` path in `app/api/v2/config.py` to point to `"migrations"`.
- *Expected Outcome*: A fully passing backend test suite (0 failures, 0 errors) that solidifies the V3-00 baseline.
