# VC-08 — Final Information Architecture

## Final primary navigation

The customer-facing primary navigation is exactly: **Today, Style, Care, Plan, You**.
All five destinations use the same standard tab treatment. There is no inventory tab,
centre action tab, or primary Services, Scan, or History tab.

## Domain ownership

- **Style:** Wardrobe, Shoes, Accessories, style decisions, and the existing owned-first shopping check.
- **Care:** Skin Care (`beauty` remains the internal category key), Hair Care, Perfumes, Supplements, routines, shelf, and upkeep timing.
- **Plan:** weekly plan, customer events, Event Ready entry points, and Google Calendar controls.
- **You:** profile, My Appearance, Progress, Memory, privacy, and account controls.
- **Today:** the existing current-day decision surface.

Inventory remains the one authoritative domain and supports all seven categories. VC-08 changes
customer-facing placement only; it does not create another item store or change authorization.

## Legacy routes

`home`, `inventory`, `style-me-tab`, `planner`, `profile`, `services`, `scan-tab`, and `history`
remain routable compatibility routes but are hidden from the primary tab bar. The legacy Inventory
route remains available for compatibility with its full seven-category collection; Style and Care
enter it with a constrained domain context so their normal collections cannot mix categories.

## Out of scope

VC-08 does not add cross-domain orchestration, notifications, backend decision changes, migrations,
or billing. Those concerns remain outside this information-architecture phase.
