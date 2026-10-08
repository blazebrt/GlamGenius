"""Status guards only; these tests must never be described as a restore drill."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_historical_zero_byte_simulator_is_no_longer_an_executable_success_path():
    assert not (ROOT / "scripts/simulate_backup_restore.sh").exists()


def test_recovery_authority_reports_the_blocker_not_a_successful_drill():
    qualification = (ROOT / "docs/operations/BACKUP_RESTORE_QUALIFICATION.md").read_text(encoding="utf-8")
    assert "F15 OPEN — real backup/restore qualification incomplete" in qualification
    assert "NOT RUN; no files, sizes or SHA-256 values" in qualification
    assert "No guaranteed production RPO is currently claimed" in qualification
    for path in (
        "docs/architecture/ARCHITECTURE_AUTHORITY.md",
        "docs/operations/SCALE_READINESS_AND_SLOS.md",
    ):
        assert "BACKUP_RESTORE_QUALIFICATION.md" in (ROOT / path).read_text(encoding="utf-8")
    historical = (ROOT / "docs/production/BACKUP_AND_RESTORE_DRILL.md").read_text(encoding="utf-8")
    assert "HISTORICAL / UNVERIFIED" in historical
