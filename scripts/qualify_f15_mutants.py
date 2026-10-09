"""Actually mutate F15 checks, execute the detecting tests, and restore bytes.

Run from an isolated checkout with disposable local test databases. No live
credentials are needed. A nonzero baseline or surviving mutant fails the run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECOVERY = ROOT / "backend/app/operations/recovery.py"
IMPORTER = ROOT / "backend/app/domains/off/importer.py"
MANIFEST = ROOT / "scripts/f15/safe-source-manifest.sql"
TEST = "tests/test_f15_recovery.py"
MUTANTS = (
    ("zero_dump", RECOVERY, "if size == 0:", "if False:", "test_zero_dump_is_rejected_before_restore"),
    ("dump_command_failed", RECOVERY, "if result.returncode != 0:", "if False:", "test_dump_command_failure_cannot_become_success"),
    ("restore_command_failed", RECOVERY, "if result.returncode != 0:", "if False:", "test_restore_command_failure_cannot_become_success"),
    ("verification_skipped", RECOVERY,
     'if verify_restore is None:\n        raise RecoveryFailed("VERIFICATION_REQUIRED")',
     'if verify_restore is None:\n        verify_restore = lambda: source', "test_verification_cannot_be_skipped"),
    ("manifest_mismatch", RECOVERY,
     'for field in ORDINARY_FIELDS:', "for field in ():", "test_any_manifest_mismatch_fails"),
    ("proprietary_field_accepted", IMPORTER, "set(row) != CANONICAL_FIELDS", "not CANONICAL_FIELDS <= set(row)",
     "test_importer_rejects_proprietary_unknown_and_fetch_time_before_session"),
    ("fabricated_fetched_at", IMPORTER, "fetched_at=None", "fetched_at=__import__('datetime').datetime.now(__import__('datetime').UTC)",
     "test_store_a_export_destroy_recreate_import_actual_parity_and_unknown_freshness"),
    ("nonzero_storage_proof_skipped", RECOVERY,
     'if object_count and len(storage_proofs) != object_count:\n        raise RecoveryFailed("PRODUCTION_OBJECT_BYTE_PROOF_REQUIRED")\n    assert_storage_proofs(source, storage_proofs)', "pass",
     "test_required_storage_proof_cannot_be_skipped_for_nonzero_source"),
    ("connected_cluster_separation_skipped", IMPORTER, "connected == store_b_system_identifier", "False",
     "tests/test_f15_review_regressions.py::test_connected_same_cluster_alias_is_rejected_before_any_insert"),
    ("fabricated_object_binding_accepted", RECOVERY, 'expected = source.get("storage_byte_manifest", [])',
     'return\n    expected = source.get("storage_byte_manifest", [])',
     "tests/test_f15_review_regressions.py::test_object_proofs_are_exactly_bound_to_independent_source"),
    ("not_null_authority_omitted", MANIFEST, "'not_null',a.attnotnull", "'not_null',false",
     "tests/test_f15_review_regressions.py::test_complete_column_authority_mutants_detected[ALTER TABLE outside_eight ALTER COLUMN value DROP NOT NULL]"),
    ("default_authority_omitted", MANIFEST, "pg_get_expr(d.adbin,d.adrelid,false)", "NULL::text",
     "tests/test_f15_review_regressions.py::test_complete_column_authority_mutants_detected[ALTER TABLE outside_eight ALTER COLUMN value SET DEFAULT 'changed']"),
    ("identity_authority_omitted", MANIFEST, "'identity',a.attidentity", "'identity',''",
     "tests/test_f15_review_regressions.py::test_complete_column_authority_mutants_detected[ALTER TABLE outside_eight ALTER COLUMN id SET GENERATED ALWAYS]"),
    ("type_authority_omitted", MANIFEST, "'type_name',t.typname", "'type_name','omitted'",
     "tests/test_f15_review_regressions.py::test_complete_column_authority_mutants_detected[ALTER TABLE outside_eight ALTER COLUMN value TYPE varchar(100)]"),
    ("generated_expression_authority_omitted", MANIFEST, "pg_get_expr(d.adbin,d.adrelid,false)", "NULL::text",
     "tests/test_f15_review_regressions.py::test_generated_expression_same_ordinal_is_detected"),
    ("full_row_content_omitted", MANIFEST,
     "CASE WHEN t.typcategory='A' THEN format('jsonb_build_object(''bounds'',array_dims(r.%I),''value'',to_jsonb(r.%I))',a.attname,a.attname)\n      WHEN t.typname='json' THEN format('to_jsonb(r.%I::text)',a.attname)\n      ELSE format('to_jsonb(r.%I)',a.attname) END AS row_value", "'NULL' AS row_value",
     "tests/test_f15_review_regressions.py::test_complete_non_id_row_mutants_detected"),
)


def execute(selector: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-B", "-m", "pytest", "-q", selector, "--tb=short"],
                          cwd=ROOT / "backend", text=True, capture_output=True, timeout=180)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    originals = {path: path.read_bytes() for path in {m[1] for m in MUTANTS}}
    baseline = execute("tests/test_f15_recovery.py")
    if baseline.returncode:
        sys.stderr.write("F15 mutant baseline failed; no mutation executed.\n")
        return 1
    results = []
    review_baseline = execute("tests/test_f15_review_regressions.py")
    if review_baseline.returncode:
        sys.stderr.write("Review mutant baseline failed; no mutation executed.\n")
        return 1
    try:
        for name, path, old, new, selector in MUTANTS:
            text = originals[path].decode("utf-8")
            if text.count(old) != 1:
                raise RuntimeError("Mutation target must occur exactly once")
            mutated = text.replace(old,new)
            if name == "nonzero_storage_proof_skipped":
                mutated = mutated.replace('assert_storage_proofs(after, storage_proofs)', 'pass')
            if name == "type_authority_omitted":
                mutated = mutated.replace("'type_modifier',a.atttypmod", "'type_modifier',0").replace("format_type(a.atttypid,a.atttypmod)", "'omitted'")
            path.write_text(mutated, encoding="utf-8", newline="\n")
            # Python's timestamp/size cache must never substitute original code.
            for cache in path.parent.glob(f"__pycache__/{path.stem}.*.pyc"):
                cache.unlink()
            result = execute(selector if selector.startswith('tests/') else f"{TEST}::{selector}")
            killed = result.returncode == 1 and "failed" in result.stdout and "ERROR collecting" not in result.stdout
            results.append({"mutant": name, "test": selector, "exit_code": result.returncode, "killed": killed})
            sys.stdout.write(json.dumps({"mutant":name,"killed":killed})+'\n')
            sys.stdout.flush()
            path.write_bytes(originals[path])
            for cache in path.parent.glob(f"__pycache__/{path.stem}.*.pyc"):
                cache.unlink()
    finally:
        for path, original in originals.items():
            path.write_bytes(original)
    report = {"baseline_passed": True, "executed": len(results), "killed": sum(r["killed"] for r in results),
              "all_original_bytes_restored": all(path.read_bytes() == original for path, original in originals.items()),
              "original_source_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(data).hexdigest() for path, data in originals.items()},
              "results": results}
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(json.dumps(report) + "\n")
    return 0 if len(results) == len(MUTANTS) and report["killed"] == len(MUTANTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
