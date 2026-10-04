"""Deterministic tests for the container exception gate's own Python runtime."""

import contextlib
import datetime
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("validate_trivy_exceptions", ROOT / "scripts/validate_trivy_exceptions.py")
assert SPEC is not None and SPEC.loader is not None
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


def entry(advisory_id: str, **changes: object) -> dict[str, str]:
    record = {
        "cve_or_advisory_id": advisory_id,
        "affected_package": "example",
        "installed_version": "1.0",
        "reason": "No fixed package",
        "exploitability_assessment": "Not reachable from requests",
        "compensating_control": "Non-root runtime",
        "owner": "@example",
        "created_date": "2026-08-08",
        "review_date": "2026-09-05",
        "expiry_date": "2026-10-03",
        "upgrade_plan": "Remove when fixed",
    }
    record.update(changes)
    return record


def write_registry(path: Path, entries: list[dict[str, str]]) -> None:
    path.write_text(yaml.safe_dump({"exceptions": entries}), encoding="utf-8")


class TrivyExceptionPolicyTests(unittest.TestCase):
    def test_reports_all_eighteen_expired_entries_without_changing_ignore(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = Path(directory) / "registry.yaml"
            ignore = Path(directory) / ".trivyignore"
            ignore.write_text("original\n", encoding="utf-8")
            write_registry(registry, [entry(f"CVE-2026-{10000 + index}") for index in range(18)])
            output = io.StringIO()

            with contextlib.redirect_stdout(output):
                valid = VALIDATOR.validate_exceptions(registry, ignore, today=datetime.date(2026, 10, 4))

            self.assertFalse(valid)
            self.assertEqual(output.getvalue().count("expired on 2026-10-03"), 18)
            self.assertIn("18 Trivy exception validation error(s)", output.getvalue())
            self.assertEqual(ignore.read_text(encoding="utf-8"), "original\n")

    def test_reports_missing_fields_bad_dates_and_wildcard_in_one_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = Path(directory) / "registry.yaml"
            ignore = Path(directory) / ".trivyignore"
            record = entry("CVE-2026-*")
            del record["reason"]
            record["review_date"] = "2026-02-30"
            record["expiry_date"] = "tomorrow"
            write_registry(registry, [record])
            output = io.StringIO()

            with contextlib.redirect_stdout(output):
                valid = VALIDATOR.validate_exceptions(registry, ignore, today=datetime.date(2026, 10, 4))

            self.assertFalse(valid)
            for expected in (
                "missing required fields: reason",
                "wildcard advisory ID",
                "invalid review_date",
                "invalid expiry_date",
                "4 Trivy exception validation error(s)",
            ):
                self.assertIn(expected, output.getvalue())
            self.assertFalse(ignore.exists())

    def test_retired_ids_removed_only_after_valid_registry(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = Path(directory) / "registry.yaml"
            ignore = Path(directory) / ".trivyignore"
            ignore.write_text("CVE-2026-41992\n", encoding="utf-8")
            write_registry(
                registry,
                [
                    entry("CVE-2026-54369", review_date="2026-10-11", expiry_date="2026-10-18"),
                    entry("CVE-2025-69720", review_date="2026-10-11", expiry_date="2026-10-18"),
                ],
            )

            self.assertTrue(VALIDATOR.validate_exceptions(registry, ignore, today=datetime.date(2026, 10, 4)))
            self.assertEqual(ignore.read_text(encoding="utf-8"), "CVE-2025-69720\nCVE-2026-54369\n")

    def test_tracked_ignore_set_exactly_matches_active_registry(self):
        data = yaml.safe_load((ROOT / ".trivy-exceptions.yaml").read_text(encoding="utf-8"))
        active_ids = {record["cve_or_advisory_id"] for record in data["exceptions"]}
        ignored_ids = set((ROOT / ".trivyignore").read_text(encoding="utf-8").splitlines())

        self.assertEqual(active_ids, ignored_ids)
        self.assertEqual(len(active_ids), len(data["exceptions"]))

    def test_missing_registry_fails_closed_without_erasing_ignore(self):
        with tempfile.TemporaryDirectory() as directory:
            ignore = Path(directory) / ".trivyignore"
            ignore.write_text("CVE-2026-54369\n", encoding="utf-8")

            self.assertFalse(VALIDATOR.validate_exceptions(Path(directory) / "missing.yaml", ignore))
            self.assertEqual(ignore.read_text(encoding="utf-8"), "CVE-2026-54369\n")


if __name__ == "__main__":
    unittest.main()
