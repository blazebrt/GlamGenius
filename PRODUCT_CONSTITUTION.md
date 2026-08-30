# GlamGenius — Product Constitution

## What this product is
A decision engine for everything that enters or touches the human body.
The user scans a product; the app decides Buy / Wait / Skip and says why.
Food, cosmetics, supplements, cookware, salon upkeep. India only.

## The three sentences
1. The number on the label is not the number in your body.
2. Traditional Indian practices were mostly bioavailability rules.
3. Where we do not know, we say we do not know.

## The master rule
The engine judges effect on the body. It never judges appearance.
INTERNAL EXCEPTION: the recommendation/Look engine is retained as
infrastructure for Event Ready, answering "what do you already own that
suits this occasion". This exception covers backend modules only. No
Style, quiz, or colour-analysis SCREEN may exist.

## The manager rule
The app decides. It does not offer menus. Every result is a decision
with a one-line reason and a one-tap override that is never punished.
The manager must also GIVE THINGS BACK, not only restrict. When a
constraint lifts, say so.

## Hard handoffs — the app must NOT decide
Pregnancy, breastfeeding, any named medication, any diagnosed condition,
children under 12. State the fact, hand off to a doctor, never advise.
The gate is routines/hard_handoff.py: evaluate() and requires_handoff().
It covers age under 12, a child subject, pregnancy, breastfeeding, any
named medication, and any condition a clinician is already handling. It
recognises medications by how drug names are built, not from a list, and
it fails closed — medical text with unclear specifics hands off anyway.

needs_professional() in routines/safety.py is a partial text check for a
different, narrower job and MUST NOT be treated as satisfying this
requirement.

A feature satisfies this rule only when it CALLS the gate. The gate
existing is not the same as a feature using it, and every feature that
touches this territory must pass its text and whatever age it holds.

## The evidence rule
The app never makes a claim in its own voice. It reports what a named,
openable source says. No source, no claim. Missing data is stated as
"Not enough information", never guessed.

## Data licensing
Open Food Facts data is ODbL licensed with a share-alike clause.
OFF-derived data and proprietary data live in two physically separate
stores, joined only at query time on barcode. No proprietary value is
ever written into an OFF-derived record. Breaking this obliges us to
publish our entire knowledge base.

## User-generated content
Structured dropdowns only. Zero free text anywhere, ever. Observations
never conclusions. A minimum report threshold before display. A visible
right of reply for brands.

## Knowledge verification — all five required before publishing any entry
1. An openable source URL, or the entry is NOT_ENOUGH_INFORMATION
2. The founder opens the source and confirms the number
3. Claude and Codex asked blind and separately; disagreement does not
   ship
4. An adversarial pass
5. Doubt ships as "not enough information"

## ASLI score gates
Culinary ingredients (ghee, oils, salt, sugar, jaggery) return
NOT_GRADED, never a letter. NOVA 4 has a hard ceiling of C. Saturated
fat is penalised only at NOVA 3-4. A missing nutrition panel or
ingredient list means no grade is shown.

## Free vs paid
Free forever: all scanning, all scores, full ingredient breakdown with
risk tiers, positives and negatives, plain-language explanations,
named-ingredient percentages, one alternative, delivered-dose
comparison, adulteration flags, complaint filing, share cards, voice.

Paid: the FOR YOU personal score, health modes, family profiles, shelf
tracking, history, full personalised alternatives, the skin and hair
manager, proactive alerts.

RULE: never remove something that was free. Only add.

## Permanently rejected — do not build, do not suggest
Consumer-facing outfit generation, Style Me and style quiz screens,
colour analysis, virtual try-on, wardrobe cataloguing as a user concept,
salon directory or booking, open AI chat, user-to-user group chat or
public feed, advertising, brand-paid certification, European Nutri-Score
used unmodified, "minutes of walking to burn this off", any single
composite score averaging incompatible things, environmental readings
shown as a dashboard instead of a decision.

## Kept and repurposed — do not rebuild
Evidence engine, purchase verdict chain, decision memory, safety sweep,
ingredient conflict rules, context engine, care routines and adherence,
skin and hair profile, VC-06 maintenance timing, VC-07 supplement label
components, the notification worker, Open-Meteo and Google Calendar
providers, Event Ready.
