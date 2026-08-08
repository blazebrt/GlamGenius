import os
from pathlib import Path


def get_root_dir() -> Path:
    """Return the repository root.

    In CI the full checkout is flat, so ascending three levels from this file
    (backend/tests/test_ci_integrity.py) reaches the repo root.

    In Docker only the backend/ directory is mounted at /app, so ascending
    three levels gives / (the container root).  Allow the caller to override
    via the REPO_ROOT environment variable so the compose file (or a wrapper
    script) can pin the correct path without changing this file.
    """
    if env := os.environ.get("REPO_ROOT"):
        return Path(env)
    # Walk upward until we find the .github sentinel that marks the repo root.
    candidate = Path(__file__).resolve()
    for parent in candidate.parents:
        if (parent / ".github").exists():
            return parent
    # Fallback: old three-level heuristic (works in CI flat checkout).
    return Path(__file__).parent.parent.parent

def test_trivy_exceptions_exists():
    root = get_root_dir()
    exc_file = root / ".trivy-exceptions.yaml"
    assert exc_file.exists(), "Trivy exceptions registry must exist"

def test_branch_protection_script():
    root = get_root_dir()
    script = root / "scripts" / "protect_main_branch.sh"
    assert script.exists(), "Branch protection script missing"
    content = script.read_text(encoding="utf-8")
    assert "--validate" in content, "Must implement --validate flag"
    assert "--dry-run" in content, "Must implement --dry-run flag"
    assert "required_status_checks" in content, "Must configure status checks"
    assert "Legacy and payment absence" in content, "Must require legacy absence check"

def test_ci_workflow_configured():
    root = get_root_dir()
    ci = root / ".github" / "workflows" / "ci.yml"
    assert ci.exists(), "CI workflow missing"
    content = ci.read_text(encoding="utf-8")
    assert "aquasecurity/trivy-action" in content, "Must use trivy"
    assert "validate_trivy_exceptions.py" in content, "Must validate trivy exceptions"
    assert "anchore/sbom-action" in content, "Must use sbom action"
