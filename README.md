# GlamGenius

Personal stylist + skin & hair wellness coach for India.

Not a diagnosis app. No salon cart or checkout — salon visits are suggestions only.

## Features

- AI skin / hair check (visible observations only)
- Skin tone → clothing colour recommendations (Indian wardrobe)
- Label ingredients to look for (e.g. salicylic acid for oily / pimple-prone look)
- Nutrition: ingredient → common Indian foods
- Salon ideas without prices or booking
- Freemium: 2 free checks / month, Plus subscription unlocks unlimited

## Quick start (Docker)

```bash
cp env.example .env
# set EMERGENT_LLM_KEY in .env
docker compose up --build
```

API: http://localhost:8000/api/health

## Backend (local)

```bash
# start MongoDB locally, then:
cd backend
cp ../env.example .env
# edit MONGO_URL=mongodb://localhost:27017
pip install -r requirements.txt
uvicorn server:app --reload --host 0.0.0.0 --port 8000
```

## Frontend (Expo)

```bash
cd frontend
echo "EXPO_PUBLIC_BACKEND_URL=http://localhost:8000" > .env
npm install
npx expo start --web
```

For a physical phone, set `EXPO_PUBLIC_BACKEND_URL` to your machine LAN IP.

## API overview

| Method | Path | Purpose |
|--------|------|---------|
| GET | /api/health | Health |
| GET | /api/config/public | Prices & free limit |
| POST | /api/users | Create guest/account |
| POST | /api/auth/register | Register |
| POST | /api/auth/login | Login |
| POST | /api/scan/analyze | Coach analysis (quota enforced) |
| GET | /api/scan/history/{user_id} | History |
| GET | /api/scan/trends/{user_id} | Score trends |
| POST | /api/plans/style | Occasion style plan |
| GET | /api/salon-ideas | Salon suggestions (no pay) |
| POST | /api/subscription/create-order | Plus order (mock) |
| POST | /api/subscription/confirm | Activate Plus (mock) |

## Disclaimer

Guidance is for general wellness and personal style from photos — not medical advice.
