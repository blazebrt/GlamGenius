import os
import re
from pathlib import Path

def test_legacy_absence():
    """
    Asserts that no legacy concepts (MongoDB, Razorpay, billing, etc.)
    remain in the active codebase. Excludes docs/, memory/, and .git/.
    """
    # If we're inside the backend-tests docker container, the root might be /app.
    # Otherwise, it's the repo root (three parents up).
    current_file = Path(__file__).resolve()
    repo_root = current_file.parent.parent.parent
    if str(repo_root) == "/":
        repo_root = current_file.parent.parent
    
    prohibited_patterns = [
        r"mongo",
        r"pymongo",
        r"motor",
        r"billing",
        r"razorpay",
        r"subscription"
    ]
    
    # Exception for v1 in Supabase auth URLs
    v1_exception = r"auth/v1"
    
    prohibited_regex = re.compile("|".join(prohibited_patterns), re.IGNORECASE)
    v1_regex = re.compile(r"\bv1\b", re.IGNORECASE)
    v1_exception_regex = re.compile(v1_exception, re.IGNORECASE)
    
    exclude_dirs = {".git", "docs", "memory", "__pycache__", "frontend_node_modules", ".expo"}
    
    violations = []
    
    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in exclude_dirs]
        
        for file in files:
            if file.endswith((".png", ".jpg", ".jpeg", ".webp", ".pyc")):
                continue
            if file == "test_legacy_absence.py":
                continue
                
            filepath = Path(root) / file
            
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    for line_num, line in enumerate(f, 1):
                        # Check prohibited patterns
                        if prohibited_regex.search(line):
                            violations.append(f"{filepath.relative_to(repo_root)}:{line_num} -> {line.strip()}")
                        
                        # Check v1 pattern (with exceptions)
                        if v1_regex.search(line) and not v1_exception_regex.search(line):
                            violations.append(f"{filepath.relative_to(repo_root)}:{line_num} -> {line.strip()}")
                            
            except (UnicodeDecodeError, PermissionError, OSError):
                pass

    if violations:
        print("Found legacy violations:")
        for v in violations:
            print(v)
            
    assert not violations, f"Found {len(violations)} legacy references in the codebase."
