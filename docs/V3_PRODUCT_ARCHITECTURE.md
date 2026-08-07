# GlamGenius V3 Product Architecture Contract

## 1. Product Mission
GlamGenius V3 is a **Personal Appearance Decision Engine**. Its purpose is to reduce the research, planning, and decision-making users currently perform across clothing, outfits, shoes, accessories, skin care, hair care, perfumes, products, ingredients, routines, nutrition, supplements, maintenance, and event preparation.

## 2. Product Boundaries
GlamGenius is an intelligent decision engine that synthesizes multiple domains into cohesive recommendations. 
**It is NOT:**
- Merely an outfit planner or skin scanner.
- A generic AI chatbot.
- A wardrobe inventory or habit tracker alone.
- A salon marketplace, booking platform, or product marketplace.
- A medical or diagnostic tool.

## 3. V3 Navigation
The application follows a streamlined, intentional hierarchy:
- **Today**: The primary daily decision surface ("What should I do for my appearance today?").
- **Style**: Outfits, clothing, shoes, accessories, colour suitability, and size/fit.
- **Care**: Skin care, hair care, shelf/inventory, ingredients, and routines.
- **Plan**: Weekly routines and major Event Ready preparation.
- **You**: Profile, preferences, memory, settings, and progress.

## 4. Domain Map
The architecture unifies the following domains:
- **Appearance Profile**: Source of truth for skin tone, skin type, hair characteristics, and fit preferences.
- **Inventory (Shelf)**: Wardrobe, shoes, accessories, skin-care, hair-care, perfumes, supplements.
- **Context Engine**: Unified state of weather, season, AQI, and time.
- **Style / Skin Care / Hair Care**: Domain-specific recommendation logic.
- **Ingredient Intelligence**: Deterministic rules for product safety and usage.
- **Nutrition / Supplements**: Appearance-related wellness context.
- **Maintenance Ideas**: Cadence for haircuts, grooming, etc.
- **Planner (Event Ready & Weekly)**: Forward-looking preparation.
- **Purchase Decisions**: Evaluating products before buying.
- **Memory & Progress**: Feedback loops and utilization tracking without scores.

## 5. Context Engine Contract
The **Context Engine** is the single source of truth for environmental and temporal data. Separate domains must NOT independently invent these factors.
- **Inputs**: Date, local time, timezone, location, observed weather, forecast, AQI, calendar events, laundry state.
- **Outputs**: A unified context snapshot provided to the Today and Planner engines.
- **Ownership**: The Context Engine owns weather data, AQI interpretation, and Indian season determination (based on location + calendar + observed weather, not just month).

## 6. Today Decision Contract
**Today** answers "What should I do for my appearance today?" without overwhelming the user.
- **Ownership**: The Today Decision Engine decides which modules are relevant based on the Context Engine.
- **Hierarchy**: 
  - *Primary Decision*: Most critical action (e.g., outfit for a normal day; preparation timeline for an event day).
  - *Supporting Decisions*: 1-2 contextually relevant things (e.g., hair wash day, skin routine).
  - *Explore*: All other domains on demand.
- **Rule**: Today is dynamically composed. It is NOT a fixed dashboard of mandatory cards.

## 7. Style Decision Contract
Style encompasses more than occasion generation.
- **Colour Suitability**: Uses skin tone/undertone from the Profile. Presents a premium constructive framework (e.g., Signature, Excellent, Complementary, Neutral, Less Harmonious) instead of Good/Bad. Explains *why* colours work.
- **Size/Fit**: Uses known size, fit preferences, and silhouette preferences from the Profile. Focuses on comfort and proportion.
- **Inputs**: Weather, Indian season, AQI practicality, occasions, owned inventory.

## 8. Skin Care Contract
Skin care decisions provide routine and product utility based on deterministic facts.
- **Inputs**: Skin profile, shelf, ingredients, routine compatibility, user feedback, weather, humidity, AQI context.
- **Prohibitions**: No medical diagnoses or condition treatment.

## 9. Hair Care Contract
Hair care decisions govern styling and wash routines.
- **Inputs**: Hair profile, wash-day logic, styling plans, heat styling context, shelf, weather, Indian seasonal context, upcoming events.
- **Prohibitions**: No medical diagnoses.

## 10. Shelf + Ingredient Intelligence Contract
The system understands what the user owns, product roles, expiry, and compatibility.
- **Ownership**: Ingredient Intelligence owns compatibility warnings.
- **AI Rule**: AI may extract and summarize ingredients, but AI must NOT invent safety rules. All compatibility warnings trace back to reviewed deterministic evidence/rules.
- **Terminology**: The internal DB identifier `beauty` is preserved for skin/hair care. The UI must use "Skin Care", "Hair Care", or "Shelf".

## 11. Product Check Contract
A high-value decision engine to evaluate a product before purchase ("Does this product make sense for me?").
- **Evaluates**: Profile fit, routine placement, ingredient utility, duplication of owned items, expiration of current products, and compatibility.
- **Output**: Explains reasoning (Buy / Wait / Skip) based on deterministic facts.

## 12. Nutrition Contract
Provides general appearance-related nutrition context.
- **Preferences**: Respects dietary choices (vegetarian, vegan, Jain, non-vegetarian). 
- **Prohibitions**: Cannot diagnose deficiencies or prescribe medical treatments.

## 13. Supplement Boundary
Supplements exist in inventory for safe utility.
- **Scope**: Owned items, user-entered purpose, expiry, duplicate ingredients (where label data permits).
- **Prohibitions**: AI must NOT prescribe dosages, replacement medications, or disease treatments.

## 14. Skin & Hair Maintenance Ideas Contract
Provides ideas and timing for grooming.
- **Scope**: Haircut timing, trim timing, grooming cadence, event preparation.
- **Prohibitions**: Strictly NO salon marketplace, booking system, price comparison, or payments.

## 15. Event Ready Contract
A major planning capability for high-intent moments (weddings, interviews, dates, etc.).
- **Scope**: Modular preparation spanning primary outfit, backup outfit, footwear, accessories, skin/hair prep, grooming ideas, packing, laundry, and preparation timeline.
- **Logic**: Modules are derived dynamically from event type + context + time remaining + inventory + preferences. An interview does not behave like a wedding.

## 16. Weekly Planning Contract
Supports normal life logistics.
- **Scope**: Office days, weather backups, laundry sync, wash days, and recurring routines.

## 17. Purchase Decision Contracts
- **Style Purchase**: Evaluates wardrobe gaps, outfit combinations, occasion usefulness, fit, and colour suitability.
- **Care Purchase**: Evaluates ingredient utility, routine placement, compatibility, and redundancy against the current shelf.

## 18. Memory and Personalization Contract
Progressive personalization based on explicit evidence (wears, accepted/rejected recommendations, liked colours, product irritation).
- **Hierarchy of Truth**: 1) User-declared fact, 2) Observed non-medical attribute, 3) Behavioural inference, 4) External context, 5) Reviewed scientific rule.
- **Handling Unknowns**: If information is unknown or low-confidence, the system must ask the user, omit the module, or communicate uncertainty. Never fabricate.

## 19. Progress Contract
Progress measures useful behaviours (wardrobe utilization, routine consistency, products used up, event readiness).
- **Prohibitions**: NO attractiveness score, body quality score, skin quality score, or overall "appearance score". 

## 20. Future Gamification Boundaries
- **Allowed**: Preparation progress, consistency, smarter purchases, use-up value recovery. Premium and editorial tone.
- **Avoid**: Childish coin economies, leaderboards, streak resets, shame-based messaging.

## 21. AI vs Deterministic Responsibility Matrix

| Decision Area | Deterministic | AI Allowed | Prohibited Behavior |
| --- | --- | --- | --- |
| **Inventory** | Source of truth for what is owned. | Extracting item attributes from photos. | Inventing owned clothing/products. |
| **Context/Weather** | Source of truth (Context Engine). | Explaining weather impact on style. | Fabricating weather or AQI data. |
| **Ingredient Safety** | Source of truth (Scientific rules). | Reading labels, summarizing evidence. | Inventing safety rules/contraindications. |
| **Profile (Size/Skin)**| Source of truth (User inputs). | Ranking recommendations based on profile. | Diagnosing medical conditions or inventing size. |
| **Routines** | Execution and slots. | Suggesting slot placement. | Prescribing medical routines. |

## 22. Existing V2 Reuse Map

| Capability | Status | Relevant V2 Domain |
| --- | --- | --- |
| FastAPI Backend | REUSE | `backend/server.py`, `backend/routers` |
| Auth & DB (Supabase) | REUSE | `backend/core/security.py`, DB schemas |
| Wardrobe Inventory | REUSE | `backend/domains/inventory` |
| Today Screen | EXTEND | `frontend/src/components/today` |
| AI Gateway | REUSE | `backend/domains/ai_gateway` |
| Profile / Consent | REUSE | `backend/domains/profile`, `backend/domains/consent` |
| Planner (Weekly) | EXTEND | `backend/domains/planning`, `frontend/src/components/planner` |
| Event Ready | NEW | TBD (extends Planning) |
| Purchase Engine | NEW | TBD |
| Progress | EXTEND | `backend/domains/progress` |
| Memory | EXTEND | `backend/domains/memory` |
| Salon Marketplace | DEPRECATE LATER | Old salon models/endpoints |

## 23. V3 Data Ownership Principles
- **Profile**: Single source of truth for skin tone, skin type, hair characteristics, and clothing fit/size.
- **Context Engine**: Single source of truth for weather, AQI, season, and calendar/event timing.
- **Ingredient Intelligence**: Single source of truth for product compatibility and scientific rules.

## 24. V3 API Principles
- **Contracts**: Strictly typed interfaces (e.g., via `apiV2.ts`).
- **Failure**: Graceful handling of low-confidence or missing data. 
- **Integrity**: Endpoints must return uncertainty rather than fabricated AI hallucinations.

## 25. Invite Beta Learning Plan
Target: 50–100 invite-only users. No billing/paywalls in Phase 3.
- **Learning Goals**: Which decisions users request repeatedly, recommendation acceptance rates, Today screen habituation, Event Ready utilization, product check utility, and routine adherence.

## 26. Feature Priority Classification
- **P0 (Required before beta)**: Context Engine (weather/time), unified Today decision engine, Care shelf/ingredients logic, Memory updates for accepted/rejected recommendations.
- **P1 (Required during beta)**: Event Ready planner, Style colour/fit intelligence, basic Product Check.
- **P2 (Valuable after validation)**: Advanced nutrition, Purchase Decision engines, comprehensive Indian seasonal algorithms.
- **LATER (Intentionally deferred)**: Gamification, billing/paywalls, advanced evidence registry UI, AQI integrations.

## 27. V3 Implementation Sequence
1. **V3-01: Context Engine Foundation**. (Dependencies: None. Reuses: Weather APIs. New: Centralized context state).
2. **V3-02: Shelf & Ingredient Core**. (Dependencies: V3-01. Reuses: Inventory. New: Ingredient mapping).
3. **V3-03: Style Intelligence Engine**. (Dependencies: V3-01. Reuses: Wardrobe. New: Colour/Fit logic).
4. **V3-04: Today Decision Engine**. (Dependencies: V3-01, V3-02, V3-03. Reuses: Today UI. New: Relevance router).
5. **V3-05: Event Ready Planner**. (Dependencies: V3-04. Reuses: Planner. New: Event taxonomy and modular prep).
6. **V3-06: Product Check**. (Dependencies: V3-02. New: Pre-purchase evaluation logic).
