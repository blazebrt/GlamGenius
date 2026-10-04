#!/usr/bin/env python3
"""Fail-closed validation and generation of the container Trivy ignore file."""

import datetime
import re
import sys
from pathlib import Path

import yaml


REQUIRED_FIELDS = {
    "cve_or_advisory_id",
    "affected_package",
    "installed_version",
    "reason",
    "exploitability_assessment",
    "compensating_control",
    "owner",
    "created_date",
    "review_date",
    "expiry_date",
    "upgrade_plan",
}
DATE_FIELDS = ("created_date", "review_date", "expiry_date")
ID_PATTERN = re.compile(r"(?:CVE-\d{4}-\d{4,}|GHSA-[A-Za-z0-9]{4}(?:-[A-Za-z0-9]{4}){2})\Z")


def _parse_date(value: object, field: str, index: int, errors: list[str]) -> datetime.date | None:
    text = str(value)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        errors.append(f"Exception {index} has invalid {field}: expected YYYY-MM-DD.")
        return None
    try:
        return datetime.date.fromisoformat(text)
    except ValueError:
        errors.append(f"Exception {index} has invalid {field}: expected a real calendar date.")
        return None


def validate_exceptions(
    exceptions_file: Path,
    ignore_file: Path,
    *,
    today: datetime.date | None = None,
) -> bool:
    """Report every entry error; write the ignore file only after all entries pass."""
    if not exceptions_file.exists():
        print(f"FAIL: Exception registry not found at {exceptions_file}.")
        return False

    try:
        data = yaml.safe_load(exceptions_file.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        print(f"FAIL: Failed to parse exceptions YAML: {exc}")
        return False

    if not isinstance(data, dict) or not isinstance(data.get("exceptions"), list):
        print("FAIL: Exceptions registry must be a dictionary with an 'exceptions' list.")
        return False

    errors: list[str] = []
    ignored_ids: set[str] = set()
    current_date = today or datetime.date.today()
    exceptions = data["exceptions"]

    for index, entry in enumerate(exceptions):
        if not isinstance(entry, dict):
            errors.append(f"Exception {index} must be a dictionary.")
            continue

        missing = sorted(
            field for field in REQUIRED_FIELDS if field not in entry or entry[field] is None or not str(entry[field]).strip()
        )
        if missing:
            errors.append(f"Exception {index} is missing required fields: {', '.join(missing)}.")

        advisory_id = str(entry.get("cve_or_advisory_id", "")).strip()
        if advisory_id:
            if any(char in advisory_id for char in "*?[]"):
                errors.append(f"Exception {index} has a wildcard advisory ID: {advisory_id}.")
            elif not ID_PATTERN.fullmatch(advisory_id):
                errors.append(f"Exception {index} has an invalid CVE or advisory ID: {advisory_id}.")
            elif advisory_id in ignored_ids:
                errors.append(f"Exception {index} duplicates advisory ID {advisory_id}.")
            else:
                ignored_ids.add(advisory_id)

        dates = {
            field: _parse_date(entry[field], field, index, errors)
            for field in DATE_FIELDS
            if field in entry and entry[field] is not None and str(entry[field]).strip()
        }
        expiry = dates.get("expiry_date")
        if expiry is not None and expiry < current_date:
            errors.append(
                f"Exception for {advisory_id or f'entry {index}'} expired on {expiry} "
                f"(today is {current_date}). Please fix the vulnerability or renew the exception."
            )

    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        print(f"FAIL: {len(errors)} Trivy exception validation error(s); .trivyignore was not changed.")
        return False

    ignore_file.write_text("".join(f"{advisory_id}\n" for advisory_id in sorted(ignored_ids)), encoding="utf-8")
    print(f"PASS: Validated {len(exceptions)} active exceptions. Generated .trivyignore.")
    return True


if __name__ == "__main__":
    sys.exit(0 if validate_exceptions(Path(".trivy-exceptions.yaml"), Path(".trivyignore")) else 1)
