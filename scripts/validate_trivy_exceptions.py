#!/usr/bin/env python3
import sys
import yaml
import datetime
from pathlib import Path

def validate_exceptions(exceptions_file: Path, ignore_file: Path):
    if not exceptions_file.exists():
        print(f"Exception registry not found at {exceptions_file}. Generating empty .trivyignore.")
        ignore_file.write_text("")
        return

    try:
        with open(exceptions_file) as f:
            data = yaml.safe_load(f)
    except Exception as e:
        print(f"FAIL: Failed to parse exceptions YAML: {e}")
        sys.exit(1)

    if not isinstance(data, dict) or "exceptions" not in data:
        print("FAIL: Exceptions registry must be a dictionary with an 'exceptions' key.")
        sys.exit(1)
        
    exceptions = data["exceptions"]
    if not isinstance(exceptions, list):
        print("FAIL: 'exceptions' must be a list.")
        sys.exit(1)

    required_fields = {
        "cve_or_advisory_id", "affected_package", "installed_version", 
        "reason", "exploitability_assessment", "compensating_control", 
        "owner", "created_date", "review_date", "expiry_date", "upgrade_plan"
    }

    trivy_ignores = set()
    today = datetime.date.today()

    for idx, exc in enumerate(exceptions):
        missing = required_fields - set(exc.keys())
        if missing:
            print(f"FAIL: Exception {idx} is missing required fields: {missing}")
            sys.exit(1)
            
        cve = exc["cve_or_advisory_id"]
        if "*" in str(cve):
            print(f"FAIL: Wildcard CVE ignores are not allowed. Found in exception {idx}.")
            sys.exit(1)

        try:
            expiry = datetime.datetime.strptime(str(exc["expiry_date"]), "%Y-%m-%d").date()
        except ValueError:
            print(f"FAIL: Invalid expiry_date format in exception {idx}. Must be YYYY-MM-DD.")
            sys.exit(1)

        if expiry < today:
            print(f"FAIL: Exception for {cve} expired on {expiry} (today is {today}). Please fix the vulnerability or renew the exception.")
            sys.exit(1)

        trivy_ignores.add(str(cve))

    # Generate .trivyignore
    with open(ignore_file, "w") as f:
        for cve in sorted(trivy_ignores):
            f.write(f"{cve}\n")
            
    print(f"PASS: Validated {len(exceptions)} active exceptions. Generated .trivyignore.")

if __name__ == "__main__":
    exceptions_file = Path(".trivy-exceptions.yaml")
    ignore_file = Path(".trivyignore")
    validate_exceptions(exceptions_file, ignore_file)
