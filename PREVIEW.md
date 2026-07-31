# How to open the GlamGenius preview

The app runs inside the Cloud Agent VM. `localhost` on your laptop is **not** the same machine, so you need one of these:

## Option A — Cursor Agent Ports / Preview
1. Open the agent: https://cursor.com/agents/bc-c8c3b753-e0fa-4910-9fc2-d2754c83c801
2. Open **Ports / Preview**
3. Open port **8081** (app) and **8000** (API)

## Option B — Public tunnels (temporary)
If tunnels are running in this session:

- **App:** https://fresh-cooks-itch.loca.lt  
- **API:** https://sharp-pillows-behave.loca.lt  

LocalTunnel may show a **“Click to continue”** interstitial first — click it once.

Set frontend API base to the API tunnel URL:

```bash
# frontend/.env
EXPO_PUBLIC_BACKEND_URL=https://sharp-pillows-behave.loca.lt
```

## Option C — Run on your machine (most reliable)
```bash
git checkout cursor/personal-stylist-wellness-coach-c801
cd backend && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# put GEMINI_API_KEY in backend/.env (never commit it)
uvicorn server:app --reload --port 8000

cd ../frontend
echo "EXPO_PUBLIC_BACKEND_URL=http://localhost:8000" > .env
npx expo start --web
```
Then open http://localhost:8081

## What to try in the app
1. **Profile** → add height (cm), weight (kg), body frame, style vibe  
2. **Skin check** → upload photo → fashion colours + fits + care  
3. **Outfit plan** → occasion stylist using tone + body + trends  
4. **Stylist quiz** → includes body frame question  
