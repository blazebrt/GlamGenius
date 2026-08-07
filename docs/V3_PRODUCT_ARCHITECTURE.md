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
- **Ingredient Intelligence**: Deterministic rules for product compatibility and usage.
- **Nutrition / Supplements**: Appearance-related wellness context.
- **Skin & Hair Maintenance Ideas**: Cadence for haircuts, grooming, etc.
- **Planner (Event Ready & Weekly)**: Forward-looking preparation.
- **Purchase Decisions**: Evaluating products (style and care) before buying.
- **Memory & Progress**: Feedback loops and utilization tracking without scores.

## 5. Context Engine Contract
The **Context Engine** acts as an aggregator and normalizer of environmental and temporal data. It does NOT own events, inventory, or profiles.
- **Owns**: Date/time, timezone, normalized weather snapshot, forecast, humidity, precipitation, normalized AQI snapshot, and season/climate interpretation.
- **Consumes**: Events from the Planning domain, inventory state, and relevant profile attributes.
- **Indian Season Context**: P0 capability based on location + calendar period + observed weather (e.g., hot/summer, monsoon/rainy, cooler/winter, transitional/post-monsoon).
- **AQI Context**: P0 capability. External AQI data → normalized context → reviewed deterministic interpretation → relevant module → optional AI explanation (e.g., outdoor clothing practicality, hair maintenance, evidence-supported skin care). 

## 6. Today Decision Contract (Today Orchestrator)
**Today** answers "What should I do for my appearance today?" deterministically first.
- **Ownership**: The Today Orchestrator / Relevance Engine decides relevance based on context, event state, inventory readiness, routine state, user preferences, urgency, and explicit settings.
- **AI Role**: AI may rank eligible alternatives, explain a decision, or improve presentation language. AI must NOT independently decide that an unsupported health/care module is necessary.
- **Hierarchy**: Primary Decision, Supporting Decisions, Explore.

## 7. Style Decision Contract
Style encompasses more than occasion generation.
- **Colour Suitability**: Uses skin tone/undertone from the Profile. Explains best colours, strong alternatives, neutral choices, less harmonious colours, and ways to make less-optimal colours work (contrast, layering). Never uses terms like "ugly" or "bad skin tone".
- **Size/Fit**: User fit preferences and measurements belong to the Appearance Profile. Item-specific labelled size/fit belongs to the Inventory item. Focuses on comfort and proportion.
- **Inputs**: Weather, Indian season, AQI practicality, occasions, owned inventory.

## 8. Skin Care Contract
Skin care decisions provide routine and personal product utility based on deterministic facts.
- **Personal Fit**: Evaluates "generally suitable" vs "likely a strong fit for your current routine" based on confirmed skin type, sensitivity, current products, confirmed ingredients, routine position, compatibility, frequency, prior user feedback, and environmental context (where evidence supports it).
- **Prohibitions**: No medical diagnoses or condition treatment.
- **Internal Mapping**: Customer-facing categories are "Skin Care" and "Hair Care". Internal database identifiers are `beauty` (Skin Care) and `hair` (Hair Care). Do NOT combine or rename these DB values.

## 9. Hair Care Contract
Hair care decisions govern styling, products, and wash routines.
- **Scope**: Hair characteristics, scalp/hair non-diagnostic context, wash-day state, shampoo, conditioner, mask, pre-wash, leave-in, scalp products, heat protectant, styling products, ingredient/product compatibility, product usage, humidity, rain, season, heat styling, upcoming events, user feedback, and maintenance timing.
- **Prohibitions**: No medical hair-loss diagnosis.

## 10. Shelf + Ingredient Intelligence Contract (REUSE + EXTEND)
The system understands what the user owns, product roles, expiry, and compatibility based on existing routines rules and parsers.
- **Scope**: Compatibility, usage, irritation caution, user-declared avoidance, routine placement, and evidence-backed information.
- **AI Rule**: AI may extract and summarize ingredients, but AI must NOT invent safety rules.
- **Prohibitions**: Medical safety claims remain outside the product.

## 11. Nutrition Contract
Provides basic appearance-related nutrition context (P1).
- **Logic**: Nutrient context → relevant food sources → filtered by user's dietary preferences.
- **Dietary Types**: Supports vegetarian, vegan, Jain, eggetarian, pescatarian, non-vegetarian.
- **Prohibitions**: Cannot diagnose deficiencies or prescribe medical treatments.

## 12. Supplement Boundary
Supplements exist in inventory for safe utility.
- **Scope**: Owned items, user-entered purpose, expiry, duplicate ingredients (where label data permits).
- **Prohibitions**: AI must NOT prescribe dosages, replacement medications, or disease treatments.

## 13. Skin & Hair Maintenance Ideas Contract
Provides ideas and timing for grooming.
- **Scope**: Haircut timing, trim timing, grooming cadence, preparation before weddings/events, maintenance ideas, reminders based on preferences.
- **Refactoring**: Legacy AI `salon_suggestions` are classified as REFACTOR / MIGRATE SEMANTICS LATER toward maintenance ideas. 
- **Prohibitions**: No salon marketplace, booking system, price comparison, or payments.

## 14. Event Ready Contract
A major P0 planning capability for high-intent moments (weddings, interviews, dates, etc.).
- **Event**: Canonical important future occurrence (id, type, title, start/end, importance, location, source, user confirmation, relevant dress info).
- **Event Plan**: Preparation plan associated with an Event (timeline, modules, completion state, linked look, backup look, maintenance tasks, care-prep tasks, logistical tasks).
- **P0 MVP Scope**: Extensible event type, date/time, context, primary outfit, shoes/accessories, skin/hair prep, maintenance timing, weather/context awareness, simple prep timeline, owned-item awareness, missing-information handling.
- **P1 Scope**: Backup outfit, weather backup, sophisticated packing, travel integration, dry-cleaning workflows, advanced reminders, richer event templates.

## 15. Weekly Planning Contract
Supports normal life logistics.
- **Scope**: Office days, weather backups, laundry sync, wash days, and recurring routines.
- **Boundary**: May reference an Event or Event Plan for a given date, but does NOT own the full Event Ready structure.

## 16. Purchase Decision Contracts
One broad Purchase Decision domain with different strategies.
- **Style Purchase (EXTEND)**: Evaluates wardrobe gaps, outfit combinations, occasion usefulness, fit, and colour suitability (Buy / Wait / Skip concepts).
- **Care Purchase (EXTEND + NEW)**: Reuses purchase concepts, inventory, shelf, and ingredient intelligence to evaluate ingredient utility, routine placement, compatibility, and redundancy.

## 17. Memory and Personalization Contract (Domain-Specific Authority)
Progressive personalization based on explicit evidence and strict data provenance. Truth is based on Domain-Specific Authority rather than a global hierarchy:
- **Ownership** → Inventory
- **User preference** → Profile / explicit user declaration
- **Weather** → Context normalized external source
- **Event** → Planning/Event domain
- **Product ingredients** → Confirmed product record
- **Ingredient compatibility** → Reviewed deterministic rule registry
- **User reaction/experience** → Explicit user feedback
- **Recommendation preference** → Memory
- **AI extraction** → Candidate fact until confidence/confirmation requirements are satisfied. (e.g. Skin Tone: Confirmed value outranks unconfirmed machine observation).

## 18. Progress Contract
Progress measures useful behaviours (wardrobe utilization, routine consistency, products used up, event readiness).
- **Prohibitions**: NO attractiveness score, body quality score, skin quality score, or overall "appearance score".

## 19. Future Gamification Boundaries
- **Allowed**: Preparation progress, consistency, smarter purchases, use-up value recovery. Premium and editorial tone.
- **Avoid**: Childish coin economies, leaderboards, streak resets, shame-based messaging.

## 20. AI vs Deterministic Responsibility Matrix

| Decision Area | Deterministic Responsibility | AI Allowed | User Confirmation Req | External Data | Prohibited Behavior |
| --- | --- | --- | --- | --- | --- |
| **Inventory** | Item records, ownership, availability. | Extractor of item attributes from images. | Required for AI extraction. | N/A | Inventing owned clothing/products. |
| **Clothing Colour** | Base harmonious colour palettes. | Evaluating contrast, styling imperfect matches. | No, unless explicit profile override. | N/A | Using "ugly", "unattractive", "bad". |
| **Fit/Size** | Size matching from Profile vs Item. | Suggesting styling tricks for fit. | Required for user measurements. | N/A | Inventing user size or diagnosing body type. |
| **Weather** | Source of truth (Context Engine). | Explaining weather impact on style/care. | No. | Forecast APIs | Fabricating weather data. |
| **Season (India)** | Location + calendar + observed weather. | Contextual styling suggestions. | No. | Calendar/Location | Hardcoding fixed national seasons. |
| **AQI** | Source of truth (Context Engine). | Explaining practicality/care context. | No. | AQI APIs | Health claims, disease prediction, medical advice. |
| **Calendar Event** | Event records, dates, types. | Generating prep timelines. | Yes for event details. | Calendar integrations | Hallucinating events. |
| **Event Ready** | Preparation milestones and rules. | Suggesting looks and specific prep tasks. | Yes for final plan selection. | N/A | Treating daily chores as Events. |
| **Skin Profile** | Skin tone, type, sensitivity. | Non-diagnostic observation (candidate). | Yes (Confirmed > observation). | N/A | Medical diagnosis, treating single selfie as truth. |
| **Hair Profile** | Characteristics, scalp type. | Non-medical observation (candidate). | Yes. | N/A | Medical hair-loss diagnosis. |
| **Product Ingredients** | Ontology, parser, routine slots. | Summarizing labels into candidate lists. | Yes for new uncategorized products. | Product DBs | Inventing ingredients not on label. |
| **Ingredient Compat** | Reviewed deterministic rule registry. | Explaining warnings in plain text. | No. | Scientific evidence | Inventing safety rules/contraindications. |
| **Skin/Hair Routine**| Slots, frequency, execution. | Suggesting slot placement, explaining fit. | Yes for routine changes. | N/A | Prescribing medical routines. |
| **Nutrition** | Food sources, dietary preferences. | Matching nutrients to appearance goals. | Yes for diet preferences. | N/A | Diagnosing deficiencies, treating disease. |
| **Supplements** | Expiry, purpose, duplicate tracking. | Explaining general utility. | Yes for inventory. | N/A | Prescribing dosages, replacing medications. |
| **Style Purchase** | Wardrobe gaps, Buy/Wait/Skip rules. | Explaining outfit combinations. | No. | N/A | Unjustified push to buy. |
| **Care Purchase** | Ingredient utility, duplication rules. | Explaining routine placement. | No. | N/A | Ignoring user sensitivities. |
| **Today Relevance** | Orchestrator prioritization rules. | Ranking alternatives, drafting text. | No. | N/A | Deciding unsupported health modules are needed. |
| **Memory** | Storage of explicit feedback. | Identifying patterns in feedback. | No. | N/A | Overwriting confirmed Profile facts. |

## 21. Existing V2 Reuse Map

| Capability | Status | Relevant V2 Domain Path |
| --- | --- | --- |
| FastAPI App Foundation | REUSE | `backend/app` |
| Supabase/PostgreSQL | REUSE | `backend/app/shared/database`, `backend/app/shared/security/supabase_auth.py` |
| Auth & Security | REUSE | `backend/app/shared/security` |
| Consent / Privacy | REUSE | `backend/app/domains/consent` |
| Appearance Profile | REUSE | `backend/app/domains/profile`, `backend/app/domains/appearance` |
| Complete Inventory | REUSE | `backend/app/domains/inventory`, `backend/app/domains/closet` |
| AI Gateway | REUSE | `backend/app/domains/ai_gateway` |
| Structured Failures | REUSE | `backend/app/shared/errors` |
| Memory Foundation | REUSE | `backend/app/domains/memory` |
| Progress Foundation | REUSE | `backend/app/domains/progress` |
| Today / Orchestration | EXTEND | `frontend/src/components/today`, `backend/app/domains/dashboard` (requires verification) |
| Weather / Context | EXTEND | logic already present (requires verification of exact path) |
| Calendar / Events | EXTEND | `backend/app/domains/calendar`, `backend/app/domains/events` |
| Weekly Planner | EXTEND | `backend/app/domains/planning`, `frontend/src/components/planner` |
| Occasion Styling | EXTEND | requires verification |
| Shopping / Purchase Eval | EXTEND | `backend/app/domains/commerce` |
| Shelf | EXTEND | `backend/app/domains/routines/shelf.py` |
| Routines | EXTEND | `backend/app/domains/routines` |
| Ingredient Ontology/Rules| EXTEND | `backend/app/domains/routines` |
| Nutrition | EXTEND | `backend/app/domains/routines/nutrition.py` |
| Perfume | EXTEND | `backend/app/domains/routines/perfume.py` |
| Supplements | EXTEND | requires verification |
| Event Ready Structure | NEW SUBDOMAIN / EXTENSION | TBD |
| Normalized AQI Context | NEW SUBDOMAIN / EXTENSION | TBD |
| Care-specific Purchase | NEW SUBDOMAIN / EXTENSION | TBD |

## 22. V3 API Principles
- **Contracts**: Strictly typed frontend contracts (e.g., via `apiV2.ts`).
- **Validation**: Strict backend schema validation.
- **Failures**: Explicit errors and deterministic failure where appropriate. No fabricated fallback results.
- **Idempotency**: Idempotency for appropriate mutations.
- **Versioning**: API/schema versioning for safe iteration.
- **Integrity**: Provenance and confidence metadata required when AI extraction is involved. User confirmation required for low-confidence facts. Backward-compatible evolution where possible.

## 23. Feature Priority Classification

### P0 (Required before invite beta)
- **Backend Validation Gate**: V3-00 environment checks.
- **Context Engine Foundation**: Weather, humidity/precipitation (where available), basic Indian season context, basic AQI context.
- **Style Baseline**: Using owned inventory, skin-tone/colour intelligence baseline, fit/size preference baseline.
- **Care Baseline**: Skin Care decision baseline, Hair Care decision baseline.
- **Orchestration**: Unified Today relevance/orchestration.
- **Planning**: Event Ready MVP, existing weekly planner preserved.
- **Personalization**: Feedback/memory signals required to learn from beta.

### P1 (During invite beta / immediately after initial cohort starts)
- **Purchase Evaluators**: Care Product Check, deeper Style Purchase decisions.
- **Wellness**: Basic appearance nutrition across supported diet preferences.
- **Maintenance**: Skin & Hair Maintenance Ideas.
- **Planning**: Stronger event backups/reminders.
- **Personalization**: Deeper care personalization, richer purchase feedback.

### P2 (After validation)
- Advanced regional climate modelling, advanced AQI personalization, evidence registry management UI, richer nutrition personalization, advanced event packing/travel, advanced value/recovery intelligence.

### LATER (Intentionally deferred)
- Billing/paywalls, luxury gamification system, human concierge tier, marketplace, public social network, virtual try-on (unless separately validated).

## 24. V3 Implementation Sequence

### V3-00: Development Environment / Backend Validation Gate
- **Purpose**: Ensure backend safety and testability before any V3 implementation.
- **Dependencies**: None.
- **Scope**: Python runtime accessible, backend dependencies installable, backend unit tests run, lint/static checks run, baseline recorded.
- **Non-scope**: Any feature implementation.
- **Tests Required**: Full execution of existing backend test suite.
- **Exit Criteria**: Developer can run `pytest` (or equivalent) locally with documented results.

### V3-01: Context Engine Foundation
- **Purpose**: Centralize environmental variables into a deterministic snapshot.
- **Dependencies**: V3-00.
- **Reused Systems**: Existing weather APIs/logic.
- **New Contracts**: Aggregator for date, weather, Indian season, AQI.
- **Scope**: Basic Indian season resolution, basic AQI normalization.
- **Exit Criteria**: Context snapshot available to other domains.

### V3-02: Shelf & Ingredient Core
- **Purpose**: Solidify product intelligence and deterministic rules.
- **Dependencies**: V3-00.
- **Reused Systems**: Inventory, `domains/routines` ontology/rules.
- **Scope**: Skin/hair categorization, rule engine extensions for compatibility.
- **Exit Criteria**: Robust evaluation of product ingredients against user profile.

### V3-03: Style Intelligence Engine
- **Purpose**: Baseline styling recommendations with contextual awareness.
- **Dependencies**: V3-01.
- **Reused Systems**: Wardrobe inventory, occasion logic.
- **Scope**: Colour suitability (constructive feedback), size/fit preference logic.
- **Exit Criteria**: Style engine can output looks matching V3-01 context.

### V3-04: Unified Today Orchestrator
- **Purpose**: Deterministically route relevance for daily decisions.
- **Dependencies**: V3-01, V3-02, V3-03.
- **Reused Systems**: Today UI, Dashboard backend.
- **Scope**: Rules to rank primary/supporting decisions based on urgency and context.
- **Exit Criteria**: Today dynamically displays relevant modules without hallucinating needs.

### V3-05: Event Ready Planner MVP
- **Purpose**: High-intent event preparation.
- **Dependencies**: V3-04.
- **Reused Systems**: Planner foundation.
- **Scope**: Extensible event types, date/time, context, outfit/shoes prep, skin/hair prep, simple timeline.
- **Non-scope**: Complex packing, travel logic, secondary outfits.
- **Exit Criteria**: User can create an event and receive a tailored prep timeline.

### V3-06: Purchase Decision Engine (Style & Care)
- **Purpose**: Pre-purchase evaluation.
- **Dependencies**: V3-02, V3-03.
- **Reused Systems**: `domains/commerce`.
- **Scope**: Style gap analysis, Care ingredient utility/duplication logic (Buy/Wait/Skip).
- **Exit Criteria**: Products evaluated with clear deterministic reasoning.
