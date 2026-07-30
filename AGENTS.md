# AGENTS.md

## Cursor Cloud specific instructions

### Product

**GlamGenius** is an Expo (React Native) salon advisor app with a **FastAPI** backend (`backend/server.py`) and **MongoDB** persistence.

### Services (local dev)

| Service | Command | URL |
|--------|---------|-----|
| MongoDB | `sudo -u mongodb mongod --dbpath /var/lib/mongodb --bind_ip 127.0.0.1 --port 27017 --logpath /tmp/mongod.log --fork` | `mongodb://127.0.0.1:27017` |
| Backend | `cd backend && .venv/bin/uvicorn server:app --host 0.0.0.0 --port 8000` | http://localhost:8000 |
| Frontend (web) | `cd frontend && EXPO_PUBLIC_BACKEND_URL=http://localhost:8000 npx expo start --web --port 8081` | http://localhost:8081 |

Use **tmux** for long-running processes (this environment has no systemd for `mongod`).

### Environment files

Create if missing (not committed):

- `backend/.env`: `MONGO_URL=mongodb://127.0.0.1:27017`, `DB_NAME=glamgenius`, optional `EMERGENT_LLM_KEY` for real Gemini calls via `emergentintegrations`.
- `frontend/.env`: `EXPO_PUBLIC_BACKEND_URL=http://localhost:8000`

Without `EMERGENT_LLM_KEY`, scan/advice endpoints still return **200** with fallback mock analysis in `server.py`.

### `emergentintegrations` (required for backend import)

`emergentintegrations==0.1.0` is listed in `backend/requirements.txt` but is **not on public PyPI**. For local API development, install the dev stub once per VM (see update script in Cursor Cloud environment, or run):

```bash
bash scripts/install-emergent-stub.sh
cd backend && .venv/bin/pip install -q ../.dev/emergentintegrations-stub
grep -v '^emergentintegrations' requirements.txt | .venv/bin/pip install -q -r /dev/stdin
```

### Lint / tests

- Frontend: `cd frontend && npm run lint` (existing ESLint issues in repo).
- Backend style: `cd backend && .venv/bin/flake8 server.py` / `.venv/bin/black --check server.py`.
- `backend_test.py` at repo root targets a **remote** preview URL by default; for local smoke tests use `curl http://localhost:8000/api/services` or POST `http://localhost:8000/api/users`.

### System packages (one-time on fresh VMs)

MongoDB 7.x and `python3.12-venv` may need to be installed via apt if not present on the image.
