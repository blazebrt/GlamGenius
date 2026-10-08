"""Reparse original dump CHECK definitions on LOCAL PG17.11, then roll back.

No production connection, credential, private row, or SQL definition is output.
The source is the integrity-checked schema artifact from the real backup.
"""
import argparse
import hashlib
import json
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

verification_started = datetime.now(UTC)
verification_clock = time.monotonic()
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--progress', required=True, type=Path)
parser.add_argument('--query', required=True, type=Path)
parser.add_argument('--container', required=True)
args = parser.parse_args()
if not re.fullmatch(r'supabase_db_glamgenius-f15-[a-z0-9-]+', args.container):
    raise SystemExit('TASK_LOCAL_TARGET_REQUIRED')
context = subprocess.run(['docker','context','show'],text=True,capture_output=True,timeout=10)
if context.returncode or context.stdout.strip() != 'desktop-linux':
    raise SystemExit('LOCAL_DOCKER_CONTEXT_MISMATCH')
evidence = json.loads(args.progress.read_text(encoding='utf-8-sig'))
schema_path = Path(evidence["protected_workspace"]) / "schema.sql"
expected_hash = next(a["sha256"] for a in evidence["artifacts"] if a["filename"] == "schema.sql")
schema_bytes = schema_path.read_bytes()
if hashlib.sha256(schema_bytes).hexdigest() != expected_hash:
    raise SystemExit("SCHEMA_ARTIFACT_INTEGRITY_FAILED")
source = {(c["table"], c["name"]): c for c in evidence["source_before"]["public_constraints"]}
target = {(c["table"], c["name"]): c for c in evidence["restored_manifest"]["public_constraints"]}
if source.keys() != target.keys():
    raise SystemExit("CONSTRAINT_INVENTORY_MISMATCH")
different = {key for key in source if source[key] != target[key]}
for key in different:
    if source[key]["type"] != "c" or target[key]["type"] != "c" or not source[key]["validated"] or not target[key]["validated"]:
        raise SystemExit("NON_CHECK_CONSTRAINT_MISMATCH")
definitions = {}
for table_match in re.finditer(r'^CREATE TABLE IF NOT EXISTS "public"\."([^"]+)" \(\n(.*?)^\);', schema_bytes.decode("utf-8"), re.M | re.S):
    table, body = table_match.groups()
    for line in body.splitlines():
        match = re.fullmatch(r'\s*CONSTRAINT "([^"]+)" (CHECK .+?),?', line)
        if match:
            key = (table, match[1])
            if key in definitions:
                raise SystemExit("DUPLICATE_DUMP_CONSTRAINT")
            definitions[key] = match[2]
if not different <= definitions.keys():
    raise SystemExit("SOURCE_CHECK_DEFINITIONS_NOT_FOUND")

def identifier(value):
    return '"' + value.replace('"', '""') + '"'

def literal(value):
    return "'" + value.replace("'", "''") + "'"

# Every probe is installed without scanning rows and is rolled back. Re-parsing
# on the destination's actual column types checks the restored expression's
# canonical definition, rather than ignoring a differing catalog hash.
statements = ["BEGIN;", "CREATE TEMP TABLE f15_constraint_checks (table_name text, constraint_name text, source_canonical_sha256 text, restored_sha256 text);"]
for table, name in sorted(different):
    probe = "f15_probe_" + hashlib.sha256((table + "." + name).encode()).hexdigest()[:20]
    statements.append(f"ALTER TABLE public.{identifier(table)} ADD CONSTRAINT {identifier(probe)} {definitions[(table, name)]} NOT VALID;")
    statements.append("INSERT INTO f15_constraint_checks SELECT " + literal(table) + ", " + literal(name) + ", "
        "encode(extensions.digest(convert_to(regexp_replace(pg_get_constraintdef(c.oid), ' NOT VALID$', ''), 'UTF8'), 'sha256'), 'hex'), "
        + literal(target[(table, name)]["definition_sha256"]) + " FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid "
        "JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname='public' AND t.relname=" + literal(table) + " AND c.conname=" + literal(probe) + ";")
statements.append("SELECT jsonb_agg(jsonb_build_object('table', table_name, 'name', constraint_name, 'source_canonical_sha256', source_canonical_sha256, 'restored_sha256', restored_sha256, 'equal', source_canonical_sha256=restored_sha256) ORDER BY table_name, constraint_name) FROM f15_constraint_checks;")
statements.append("ROLLBACK;")
result = subprocess.run(["docker", "exec", "-i", args.container, "psql", "-U", "supabase_admin", "-d", "postgres", "-X", "-qAt", "-v", "ON_ERROR_STOP=1", "-v", "VERBOSITY=sqlstate", "-v", "SHOW_CONTEXT=never", "-f", "-"], input="\n".join(statements), text=True, capture_output=True, timeout=120)
if result.returncode:
    raise SystemExit("LOCAL_CONSTRAINT_REPARSE_FAILED")
checks = json.loads(result.stdout) or []
manifest_result = subprocess.run(["docker", "exec", "-i", args.container, "psql", "-U", "supabase_admin", "-d", "postgres", "-X", "-qAt", "-v", "ON_ERROR_STOP=1", "-v", "VERBOSITY=sqlstate", "-v", "SHOW_CONTEXT=never", "-f", "-"], input=args.query.read_text(encoding="utf-8-sig"), text=True, capture_output=True, timeout=120)
if manifest_result.returncode:
    raise SystemExit("LOCAL_MANIFEST_REVERIFICATION_FAILED")
restored = json.loads(manifest_result.stdout)
fields = ("database", "schema_context", "alembic_heads", "public_tables", "public_table_counts", "auth_users", "storage_buckets", "storage_objects", "external_integrations", "off_schema_present", "later_label_report_resources_present", "extensions", "public_indexes", "sorted_primary_id_sha256")
for field in fields:
    if evidence["source_before"][field] != restored[field]:
        raise SystemExit("ORDINARY_MANIFEST_PARITY_FAILED: " + field)
    if evidence["source_before"][field] != evidence["source_after"][field]:
        raise SystemExit("SOURCE_CHANGED_DURING_BACKUP: " + field)
if restored["public_constraints"] != evidence["restored_manifest"]["public_constraints"]:
    raise SystemExit("CONSTRAINT_PROBE_DID_NOT_ROLL_BACK")
if evidence["source_before"]["public_constraints"] != evidence["source_after"]["public_constraints"]:
    raise SystemExit("SOURCE_CONSTRAINTS_CHANGED_DURING_BACKUP")
for artifact in evidence["artifacts"]:
    artifact_bytes = (Path(evidence["protected_workspace"]) / artifact["filename"]).read_bytes()
    if len(artifact_bytes) != artifact["bytes"] or hashlib.sha256(artifact_bytes).hexdigest() != artifact["sha256"]:
        raise SystemExit("BACKUP_ARTIFACT_INTEGRITY_FAILED")
safe_result = {"schema_artifact_sha256_verified": expected_hash, "source_constraint_count": len(source), "restored_constraint_count": len(target), "unchanged_definition_count": len(source)-len(different), "original_check_definitions_reparsed": len(checks), "all_canonical_definitions_equal": len(checks)==len(different) and all(c["equal"] for c in checks), "transaction_rolled_back": True, "source_database_write": False, "checks": checks}
(args.progress.parent / 'F15-Constraint-Reparse-Verification.json').write_text(json.dumps(safe_result, indent=2)+"\n", encoding="utf-8")
print(json.dumps({k:v for k,v in safe_result.items() if k != "checks"}, indent=2))
if not safe_result["all_canonical_definitions_equal"]:
    raise SystemExit("CANONICAL_CONSTRAINT_PARITY_FAILED")
verification_finished = datetime.now(UTC)
evidence.update(restored_manifest=restored, ordinary_database_parity=True,
    status="REAL_STORE_B_PARITY_PASSED_REMAINING_F15_QUALIFICATION_REQUIRED",
    phase="real_store_b_parity_passed", constraint_semantic_verification=safe_result,
    verification_start_utc=verification_started.isoformat(), verification_finish_utc=verification_finished.isoformat(),
    verification_seconds=time.monotonic()-verification_clock,
    total_backup_restore_verification_seconds=(verification_finished-datetime.fromisoformat(evidence["dump_start_utc"].replace("Z", "+00:00"))).total_seconds(),
    total_duration_includes_constraint_diagnostic_and_reverification=True,
    vault_source_rows=evidence["source_before"]["vault_rows"], vault_restored_rows=restored["vault_rows"],
    updated_at_utc=verification_finished.isoformat())
for failure_key in ("error_code", "error_type", "script_line"):
    evidence.pop(failure_key, None)
args.progress.write_text(json.dumps(evidence, indent=2)+"\n", encoding="utf-8")
print(json.dumps({"ordinary_parity": True, "verification_seconds": evidence["verification_seconds"], "total_seconds_including_diagnostic": evidence["total_backup_restore_verification_seconds"]}))
