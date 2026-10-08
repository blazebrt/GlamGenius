"""Status guards only; these tests must never be described as a restore drill."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_historical_zero_byte_simulator_is_no_longer_an_executable_success_path():
    assert not (ROOT / "scripts/simulate_backup_restore.sh").exists()


def test_recovery_authority_distinguishes_executed_live_drill_and_development_head():
    qualification = (ROOT / "docs/operations/BACKUP_RESTORE_QUALIFICATION.md").read_text(encoding="utf-8")
    assert "F15 READY FOR INDEPENDENT CLOSURE REVIEW" in qualification or "F15 OPEN — real backup/restore qualification incomplete" in qualification
    assert "Live production is intentionally behind repository main because Render auto-deploy is OFF." in qualification
    assert "d0e1f2g3h4" in qualification and "o3p4q5r6s7" in qualification
    assert "Vault credential recovery is not established" in qualification
    assert "No guaranteed production RPO is currently claimed" in qualification
    evidence = json.loads((ROOT / "docs/operations/evidence/F15-2026-10-08.json").read_text(encoding="utf-8"))
    assert evidence["ordinary_database_parity"] is True
    assert evidence["source_before"]["alembic_heads"] == evidence["restored"]["alembic_heads"] == ["d0e1f2g3h4"]
    assert evidence["repository_head"] == "o3p4q5r6s7"
    assert {artifact["filename"] for artifact in evidence["artifacts"]} == {"roles.sql", "schema.sql", "data.sql"}
    assert all(artifact["bytes"] > 0 for artifact in evidence["artifacts"])
    assert evidence["hosted_auth_authority_proven"] is False
    assert evidence["production_mutation"] is False
    for path in (
        "docs/architecture/ARCHITECTURE_AUTHORITY.md",
        "docs/operations/SCALE_READINESS_AND_SLOS.md",
    ):
        assert "BACKUP_RESTORE_QUALIFICATION.md" in (ROOT / path).read_text(encoding="utf-8")
    historical = (ROOT / "docs/production/BACKUP_AND_RESTORE_DRILL.md").read_text(encoding="utf-8")
    assert "HISTORICAL / UNVERIFIED" in historical
    provenance = json.loads((ROOT / "docs/operations/evidence/F15-provenance.json").read_text(encoding="utf-8"))
    live = provenance["live_operator"]
    assert live["executed_against_production"] is True
    assert live["performed_read_only_dump"] is True and live["performed_isolated_local_restore"] is True
    assert live["completed_final_canonical_constraint_parity_by_itself"] is False
    assert live["raw_constraint_comparison_failed_closed"] is True
    assert live["credential_process_cleanup"] is True and live["private_artifact_cleanup"] is False
    assert live["sha256"] == "a07f2cc68743d4fdeae961310bcaa2f5e381437ddb0d87b3005a3153bec66df2"
    assert provenance["constraint_reparse"]["executed_separately_after_raw_mismatch"] is True
    assert provenance["constraint_reparse"]["original_check_definitions_reparsed"] == 43
    assert provenance["constraint_reparse"]["transaction_rolled_back"] is True
    assert provenance["constraint_reparse"]["restored_catalog_rechecked_unchanged"] is True
    assert provenance["cleanup"]["executed_separately"] is True
    assert provenance["cleanup"]["current_absence_reverified"] is True
    reusable = provenance["reusable_operator"]
    assert reusable["executed_against_production"] is False
    assert reusable["selftest_passed"] is True
    assert reusable["includes_cleanup_finally"] is True and reusable["includes_constraint_reparse"] is True
    assert provenance["production_rerun_performed_for_provenance_correction"] is False
    assert provenance["production_write_performed_for_provenance_correction"] is False
    assert provenance["vault_credential_recovery_proven"] is False
    assert provenance["hosted_auth_authority_proven"] is False
    assert provenance["guaranteed_production_rpo"] is False and provenance["guaranteed_production_rto"] is False
    assert "multi-stage executed recovery proof" in qualification
    assert "No guaranteed production RTO is currently claimed" in qualification
    bindings = [provenance[key] for key in (
        "live_operator", "source_manifest_helper", "dump_image_authority", "public_tls_certificate",
        "constraint_reparse", "cleanup_record", "evidence_export_transformer", "reusable_operator",
    )]
    bindings += [provenance["constraint_reparse"]["evidence"], provenance["constraint_reparse"]["current_reusable_helper"],
                 provenance["cleanup"]["current_absence_evidence"]]
    for binding in bindings:
        raw = (ROOT / binding["path"]).read_bytes()
        assert len(raw) == binding["bytes"]
        assert hashlib.sha256(raw).hexdigest() == binding["sha256"]
    assert provenance["source_manifest_helper"]["historical_bytes_match_committed"] is True
    assert provenance["dump_image_authority"]["historical_bytes_match_committed"] is True
    assert provenance["constraint_reparse"]["historical_bytes_match_current_reusable_helper"] is False
    absence = json.loads((ROOT / provenance["cleanup"]["current_absence_evidence"]["path"]).read_text(encoding="utf-8-sig"))
    assert all(absence[field] is True for field in (
        "historical_workspace_absent", "historical_sql_files_absent", "historical_container_absent",
        "historical_volume_absent", "current_absence_reverified",
    ))
    assert absence["historical_dump_clients_remaining"] == 0
    assert all(process["absent"] is True for process in absence["recorded_credential_processes"])
    assert absence["production_connection_performed"] is False and absence["production_write"] is False
