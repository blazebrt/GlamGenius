"""Execute F04 mutants against real focused regressions; exit nonzero on survivor.

Requires a disposable migrated PostgreSQL URL. Normal suite never runs this.
"""
from __future__ import annotations

import re
import subprocess
import sys

CASES = {
    "quota_removed": "test_fresh_ids_hit_device_count_and_replay_at_ceiling_is_free",
    "quota_after_storage": "test_fresh_ids_hit_device_count_and_replay_at_ceiling_is_free",
    "legacy_unknown_zero": "test_legacy_unknown_photo_costs_full_cap_not_zero",
    "replay_charged": "test_fresh_ids_hit_device_count_and_replay_at_ceiling_is_free",
    "serialization_removed": "test_two_independent_postgres_transactions_cannot_over_admit[device]",
    "trust_declared_mime": "test_invalid_photo_never_reaches_storage_or_database",
    "accept_magic_only": "test_shared_validator_rejects_signature_only_files",
    "unbounded_photo_read": "test_photo_reader_stops_at_cap_plus_one_and_never_requests_unbounded_read",
    "commit_exception_guess_delete": "test_c_c_an_outcome_that_cannot_be_checked_deletes_nothing",
    "rejection_still_stores": "test_fresh_ids_hit_device_count_and_replay_at_ceiling_is_free",
    "upload_ack_compensation_removed": "test_storage_put_acknowledgement_failure_compensates_already_written_object[cleanup-available]",
}


def main() -> int:
    failures = []
    for mutant, test in CASES.items():
        module = "test_label_report_evidence_integrity" if mutant == "commit_exception_guess_delete" else "test_audit_lane4_public_resource_safety"
        target = f"tests/{module}.py::{test}"
        result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "tests.audit_lane4_mutations",
                                 "--lane4-mutant", mutant, target, "--tb=short"], capture_output=True, text=True, check=False)
        # Only an assertion failure in the exercised regression is a kill.
        regression = re.search(r"^E\s+(AssertionError:|assert |Failed: DID NOT RAISE)", result.stdout, re.MULTILINE)
        killed = result.returncode == 1 and bool(regression) and "ERROR at setup" not in result.stdout
        sys.stdout.write(f"{mutant}: {'KILLED' if killed else 'NOT PROVEN'}\n")
        sys.stdout.write(result.stdout)
        if not killed:
            sys.stdout.write(result.stderr)
            failures.append(mutant)
    sys.stdout.write(f"Mutation result: {len(CASES) - len(failures)}/{len(CASES)} proven kills\n")
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
