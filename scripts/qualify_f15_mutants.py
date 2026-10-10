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
LOCAL_AUTHORITY = ROOT / "backend/app/domains/off/local_recovery.py"
PROXY = ROOT / "scripts/f15/pinned-docker-proxy.py"
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
    ("connected_cluster_separation_skipped", IMPORTER, 'connected == store_b["system_identifier"]', "False",
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
    ("type_authority_omitted", MANIFEST, "'type_name',t.typname,'type_kind',t.typtype", "'type_name','omitted','type_kind',t.typtype",
     "tests/test_f15_review_regressions.py::test_complete_column_authority_mutants_detected[ALTER TABLE outside_eight ALTER COLUMN value TYPE varchar(100)]"),
    ("generated_expression_authority_omitted", MANIFEST, "pg_get_expr(d.adbin,d.adrelid,false)", "NULL::text",
     "tests/test_f15_review_regressions.py::test_generated_expression_same_ordinal_is_detected"),
    ("physical_dropped_column_slots_compared", MANIFEST,
     "row_number() OVER (PARTITION BY c.oid ORDER BY a.attnum)", "a.attnum",
     "tests/test_f15_review_regressions.py::test_logical_column_order_survives_dropped_slots_and_rejects_reordering"),
    ("full_row_content_omitted", MANIFEST, "format('CASE WHEN r.%I IS NULL THEN jsonb_build_object(''sql_null'',true) ELSE jsonb_build_object(''sql_null'',false,''value'',%s) END',\n      a.attname,\n      CASE WHEN t.typcategory='A' THEN format('jsonb_build_object(''bounds'',array_dims(r.%I),''value'',(SELECT coalesce(jsonb_agg(CASE WHEN f15_element IS NULL THEN jsonb_build_object(''sql_null'',true) ELSE jsonb_build_object(''sql_null'',false,''value'',to_jsonb(f15_element)) END ORDER BY f15_order),''[]''::jsonb) FROM unnest(r.%I) WITH ORDINALITY AS e(f15_element,f15_order)))',a.attname,a.attname)\n        WHEN t.typname='json' THEN format('to_jsonb(r.%I::text)',a.attname)\n        ELSE format('to_jsonb(r.%I)',a.attname) END) AS row_value", "'NULL' AS row_value",
     "tests/test_f15_review_regressions.py::test_complete_non_id_row_mutants_detected"),
    ("sql_null_markers_omitted", MANIFEST,
     "CASE WHEN r.%I IS NULL THEN jsonb_build_object(''sql_null'',true) ELSE jsonb_build_object(''sql_null'',false,''value'',%s) END",
     "to_jsonb(r.%I) /* %s */",
     "tests/test_f15_review_regressions.py::test_sql_null_is_distinct_from_every_persisted_non_null_value"),
    ("store_b_identity_replaced_with_caller_text", LOCAL_AUTHORITY,
     "SELECT system_identifier::text FROM pg_catalog.pg_control_system()", "SELECT '10000000000'::text",
     "tests/test_f15_review_regressions.py::test_connected_same_cluster_alias_is_rejected_before_any_insert"),
    ("sequence_runtime_omitted", MANIFEST,
     "'last_value',((xpath('/row/last_value/text()',r.runtime))[1]::text)::bigint,\n      'is_called',((xpath('/row/is_called/text()',r.runtime))[1]::text)::boolean",
     "'last_value',1,'is_called',true",
     "tests/test_f15_review_regressions.py::test_sequence_definition_and_runtime_mutations_detected"),
    ("substring_only_secret_check_restored", PROXY,
     "if mode is None:\n        raise ValueError('DUMP_COMMAND_REJECTED')",
     "if mode is None:\n        mode = 'schema'\n        script = re.sub(r'^export PGPASSWORD=.*(?:\\n|$)', '', body['Cmd'][2], flags=re.M)\n        if password in script:\n            raise ValueError('DUMP_COMMAND_REJECTED')",
     "tests/test_f15_review_regressions.py::test_shell_reconstructed_secret_commands_fail_closed"),
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
            if name == "substring_only_secret_check_restored":
                mutated = mutated.replace("canonical_script(mode)]", "re.sub(r'^export PGPASSWORD=.*(?:\\n|$)', '', body['Cmd'][2], flags=re.M)]")
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
