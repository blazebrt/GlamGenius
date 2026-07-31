# GlamGenius

Personal stylist + skin & hair wellness coach for India.

Not a diagnosis app. No salon cart or checkout — salon visits are suggestions only.

## Features

- AI skin / hair check (visible observations only)
- Skin tone → clothing colour recommendations (Indian wardrobe)
- Label ingredients to look for (e.g. salicylic acid for oily / pimple-prone look)
- Nutrition: ingredient → common Indian foods
- Salon ideas without prices or booking
- Free preview without an account: skin tone + top clothing colours
- Freemium: 1 free check / month once signed up, Plus subscription unlocks unlimited

## Quick start (Docker)

```bash
cp env.example .env
# set GEMINI_API_KEY in .env
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

Routes marked 🔒 require an `Authorization: Bearer <token>` header. The token
comes from register or login and is valid 30 days. The caller's identity always
comes from the token, never from the URL or request body.

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | /api/health | — | Health |
| GET | /api/config/public | — | Prices & free limit |
| GET | /api/services | — | Salon ideas |
| GET | /api/salon-ideas | — | Salon suggestions (no pay) |
| GET | /api/quiz/questions | — | Stylist quiz questions |
| POST | /api/scan/preview | — | Free teaser: tone + top colours, saves nothing |
| POST | /api/users | — | Create account (email + password required) |
| POST | /api/auth/register | — | Register, returns token |
| POST | /api/auth/login | — | Login, returns token (rate limited) |
| GET | /api/users/me | 🔒 | Own profile |
| PUT | /api/users/me | 🔒 | Update own profile |
| POST | /api/scan/analyze | 🔒 | Full coach analysis (quota enforced) |
| GET | /api/scan/history | 🔒 | Own scan history |
| GET | /api/scan/trends | 🔒 | Own score trends |
| POST | /api/quiz/submit | 🔒 | Submit stylist quiz |
| POST | /api/plans/style | 🔒 | Occasion style plan |
| GET | /api/recommendations/history | 🔒 | Own past plans |
| POST | /api/subscription/create-order | 🔒 | Plus order (mock) |
| POST | /api/subscription/confirm | 🔒 | Activate Plus (mock) |
| GET | /api/subscription/status | 🔒 | Own plan status |

## Disclaimer

Guidance is for general wellness and personal style from photos — not medical advice.
