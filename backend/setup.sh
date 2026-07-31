#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# Install private LLM SDK separately if you have it:
# pip install emergentintegrations
echo "Backend venv ready. Run: source .venv/bin/activate && uvicorn server:app --host 0.0.0.0 --port 8000"
