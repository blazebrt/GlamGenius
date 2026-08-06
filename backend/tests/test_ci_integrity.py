from pathlib import Path
import json

def get_root_dir() -> Path:
    # backend/tests/test_ci_integrity.py -> backend/tests -> backend -> root
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
