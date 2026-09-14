# GlamGenius

GlamGenius is an India-first product decision engine for things that enter or
touch the human body. The customer loop is:

**SCAN → UNDERSTAND → DECIDE → REMEMBER → MANAGE**

Before buying or using a product, scan it. GlamGenius identifies the current
pack, reports evidence-backed facts and sources, determines a governed
**Buy / Wait / Skip** result where the available information permits, and
remembers relevant product context.

## Product authority

Read [PRODUCT_CONSTITUTION.md](PRODUCT_CONSTITUTION.md) before working on a
customer feature. Existing code, migrations, and Git history are not product
authority. The product does not judge appearance and does not include wardrobe
cataloguing, Style Me, style quiz, colour analysis, virtual try-on, general
outfit generation, salon directory/booking, unrestricted AI chat, or public
social features.

Customer-visible language follows [LEGAL_RULES.md](LEGAL_RULES.md). Open Food
Facts data is isolated under the ODbL boundary described in
`docs/architecture/ODBL_DATA_WALL.md`.

## Development

- Backend: FastAPI, PostgreSQL, SQLAlchemy async, and Alembic in `backend/`.
- Frontend: Expo / React Native in `frontend/`.
- Current shelf categories: Skin Care, Hair Care, Perfumes, and Supplements.

Run the repository's backend and frontend validation commands from their
respective directories before opening a PR. Never treat a skipped or weakened
test as validation.
