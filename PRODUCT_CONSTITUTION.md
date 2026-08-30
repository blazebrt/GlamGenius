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
suits this occasion". It is never surfaced as a standalone style feature.

## The manager rule
The app decides. It does not offer menus. Every result is a decision
with a one-line reason and a one-tap override that is never punished.
The manager must also GIVE THINGS BACK, not only restrict. When a
constraint lifts, say so.

## Hard handoffs — the app must NOT decide
Pregnancy, breastfeeding, any named medication, any diagnosed condition,
children under 12. State the fact, hand off to a doctor, never advise.
needs_professional in routines/safety.py is the canonical
implementation. Reuse it everywhere.

## The evidence rule
The app never makes a claim in its own voice. It reports what a named,
openable source says. No source, no claim. Missing data is stated as
"Not enough information", never guessed.

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
