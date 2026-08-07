# Phase 1: Repository Audit and Cleanup Classification

## 1. Executive Summary
The GlamGenius repository is currently bridging V1 architecture with V2 API and V2 domains. 
The backend has successfully migrated to a Domain-Driven Design under `backend/app/domains` with a strictly typed V2 API under `/api/v2`. The frontend is a React Native app utilizing Expo Router. However, the frontend retains some technical debt:
- Direct usage of `api.get` instead of the typed `apiV2.ts` client.
- Remnants of legacy V1 screens (`home.tsx`, `services.tsx`).
- "Beauty" terminology representing skincare, which conflicts with the new "Personal Appearance Decision Engine" philosophy.
- Missing frontend test coverage.

## 2. Architecture Map
- **Frontend**: React Native with Expo Router (`frontend/app/`). 
  - `src/services/apiV2.ts`: Source of truth for typed V2 API communication.
  - `src/services/api.ts`: Base HTTP client and Auth interceptor.
  - `src/store/`: Zustand singletons.
- **Backend**: FastAPI with PostgreSQL/Supabase.
  - `app/api/v2/`: V2 API routers. V1 `/api/` prefix is retired.
  - `app/domains/`: Domain-driven logic (e.g., `inventory`, `routines`, `planning`, `scan`, `ai_gateway`).
  - `app/shared/`: Shared utilities (security, database).

## 3. KEEP - Core Domains
- `backend/app/api/v2/*`: All V2 routers.
- `backend/app/domains/*`: All current domains (`inventory`, `routines`, `planning`, `scan`, `ai_gateway`, etc.).
- `frontend/src/services/apiV2.ts`: Core typed client.
- `frontend/src/services/api.ts`: Core auth/interceptors.
- `frontend/app/(tabs)/today.tsx`, `inventory.tsx`, `style-me-tab.tsx`, `planner.tsx`, `profile.tsx`.

## 4. REFACTOR - Modules needing V2 alignment or typing
- `frontend/src/store/userStore.ts`: Currently calls `api.get('/api/v2/me')` directly. Needs to use `apiV2.ts` typed methods.
- `frontend/app/get-advice.tsx`: Direct call to `api.post('/api/v2/style/occasion')`.
- `frontend/app/scan.tsx`: Direct call to `api.post('/api/v2/scan/analyse')`.
- `frontend/app/style-quiz.tsx`: Direct call to `api.get/post` for quiz endpoints.
- `frontend/app/(tabs)/history.tsx`: Direct call to `/api/v2/scan/history`.

## 5. REPLACE - Candidate for complete V3 replacement
- V2 Data models relying on "beauty" category string (e.g. `BeautyProductDetail` in backend). Needs gradual migration in V3 to "skincare"/"haircare" nomenclature without breaking existing DB.

## 6. DELETE-CANDIDATE - Obsolete/Dead code
- `frontend/app/(tabs)/services.tsx`: "Salon ideas tab — temporarily unavailable".
- `frontend/app/service-details.tsx`: "Salon idea detail — placeholder".
- `frontend/app/(tabs)/home.tsx`: Largely replaced by `today.tsx`, currently just a hidden routable screen.

## 7. UNKNOWN - Needs product decision
- `backend/app/domains/ai_gateway/schemas.py`: `salon_suggestions`. Should the AI still generate these if the UI is dead, or will it be revived in V3?

## 8. "Beauty" vs "Salon" Usage Report
### "Beauty"
- **Internal Database/Backend:** `backend/app/domains/inventory/models.py` uses `BeautyProductDetail`. `backend/app/domains/routines/ontology.py` uses "beauty" in `StepSlot`. This represents skin-care products. **DO NOT RENAME IN PHASE 1.**
- **Frontend/User-facing:** Used in `inventory-item.tsx`, `shelf.tsx`, etc. The term "beauty" will eventually disappear from customer-facing UI in favour of specific categories (Skincare, Haircare).

### "Salon"
- **Frontend:** Used in dead/placeholder components (`services.tsx`, `service-details.tsx`).
- **Backend:** AI Gateway schemas return `salon_suggestions`.

## 9. Missing typing / API V1 vs V2 alignment
- Several frontend components and stores are bypassing `apiV2.ts` and manually writing paths like `/api/v2/me` in `api.get()` calls. This defeats the purpose of the typed client and should be refactored to use strongly typed SDK methods.
- No remaining V1 API endpoints in the backend (all are retired).

## 10. Database Risk Assessment
- The backend relies on `BeautyProductDetail` and the category string `'beauty'`. Any renaming to `'skincare'` would require a careful data migration strategy to avoid breaking existing users' inventory and routines.
- Changing `StepSlot` ontology would break existing saved user routines. 

## 11. Test Coverage Baseline
- **Backend:** High coverage. Tests exist for almost all domains (`ai_gateway`, `consent`, `inventory`, `routines`, `scan`, `planning`, etc.) inside `backend/tests/`.
- **Frontend:** Frontend tests already existed under: `frontend/src/__tests__/`. Phase 2 expanded that coverage.

## 12. Phase 2 Recommendations
- **Frontend Typing:** Standardize all API calls in `frontend` to use `apiV2.ts`.
- **UI Nomenclature:** Update frontend UI strings to replace "Beauty" with "Skincare" / "Haircare", while keeping the API/DB payloads sending the `"beauty"` string.
- **Dead Code:** Remove `home.tsx`, `services.tsx`, and `service-details.tsx` once confirmed by product.
- **Frontend Testing:** Establish a testing baseline (Jest/RTL) for the React Native frontend before making sweeping V3 changes.
