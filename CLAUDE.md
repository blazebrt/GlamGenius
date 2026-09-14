# GlamGenius agent handoff

## PRODUCT AUTHORITY RULE

Existing code is **not** evidence that a feature belongs in the product.
`PRODUCT_CONSTITUTION.md` defines product scope. If source code contradicts
the Constitution, treat that code as legacy debt, not roadmap authority.
Never expand a legacy subsystem merely because it exists.

Before implementing any customer feature, prove which current Constitution loop
it serves: **SCAN → UNDERSTAND → DECIDE → REMEMBER → MANAGE**. If that cannot
be shown, STOP rather than build it.

GlamGenius is an India-first decision engine for products that enter or touch
the human body. The primary habit is: *before I buy or use a product, I check
GlamGenius.* It determines a governed Buy / Wait / Skip result, explains the
published evidence, and remembers relevant product context. It does not judge
appearance.

Read `PRODUCT_CONSTITUTION.md` before product work. Read `LEGAL_RULES.md`
before adding or changing customer-visible text. Every such string belongs in
a keyed string file; state facts, cite sources, do not diagnose or promise a
benefit.

## Never build or revive

Wardrobe cataloguing, Style Me, style quiz, colour analysis, virtual try-on,
general outfit generation, salon directory or booking, unrestricted AI chat,
and public social/feed/group chat are permanently rejected. Do not soften or
rename these concepts into a “manager”, “collection”, or “what you own”.

`INTERNAL ONLY — NOT PRODUCT SURFACE — DO NOT EXPAND`: a small legacy-derived
candidate-selection primitive remains solely for the Constitution's retained
Event Ready exception. It is not permission for a Style API, look-management
surface, clothing shelf, profile facts, or consumer styling flow.

## Technical map

- Backend: FastAPI, PostgreSQL 16, SQLAlchemy async and Alembic under
  `backend/`. Every current route is under `/api/v2`.
- Frontend: Expo/React Native and expo-router under `frontend/`. The only
  primary tabs are **Scan** and **You**.
- Current product domains include product/barcode truth, evidence,
  substances/formulas, personal applicability, purchase decisions/memory,
  body-product shelf and routines, supplements, food intelligence, privacy,
  security, notifications, and governed Event Ready.
- The body-product shelf accepts only `beauty`, `hair`, `perfumes`, and
  `supplements`. Legacy clothing/shoe/accessory database records are
  `LEGACY_STORAGE_ONLY — NOT PRODUCT AUTHORITY` until a separately reviewed,
  data-safe migration can remove them.
- Open Food Facts data is isolated in Store A. Read
  `docs/architecture/ODBL_DATA_WALL.md` before touching it. Never join or
  persist Store A and proprietary data together.

## Safety and deployment boundaries

The hard-handoff gate is `routines/hard_handoff.py`; features in its territory
must call it rather than rely on a nearby text check. Missing evidence remains
“Not enough information”. Do not add diagnosis, dosage advice, or medical
claims.

Do not deploy, alter production secrets, touch production Supabase or Store A,
or run a provider/release phase unless the task explicitly authorizes it.

## Validation

For backend changes run the relevant `pytest` suite, `ruff check .`, Alembic
checks, and `git diff --check`. For frontend changes run TypeScript, zero-warning
lint, focused tests plus Jest, and the CI-required Expo export gates. Do not
weaken product, privacy, evidence, security, or drift tests to make a change
pass.
