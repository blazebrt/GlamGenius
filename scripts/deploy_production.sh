#!/usr/bin/env bash
set -euo pipefail

# Scripts are executed from the project root.
cd "$(dirname "$0")/.."

echo "Deploying GlamGenius Production..."

export APP_ENV=production

echo "Running Alembic migrations..."
cd backend
python -m alembic upgrade head
cd ..

echo "Starting Uvicorn..."
cd backend
python -m uvicorn server:app --host 0.0.0.0 --port 8000
