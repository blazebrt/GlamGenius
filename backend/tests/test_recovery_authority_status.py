"""Status guards only; these tests must never be described as a restore drill."""
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
