# CLAUDE.md

GlamGenius is a personal stylist + skin/hair wellness coach for India. It is **not** a
diagnosis app. Backend is FastAPI + MongoDB (`backend/server.py`); the app is Expo /
React Native (`frontend/`).

These rules are not suggestions. They hold for every task in this repo unless the user
changes them here.

## Privacy

- Never store user face images. `scan/analyze` deliberately truncates `image_base64` to
  80 characters before saving. Never change this.

  The truncation lives in `analyze_scan` in `backend/server.py` — the
  `image_base64=(request.image_base64[:80] + "...")` line. Stored values are 83
  characters (80 + `...`). If a change makes that number go up, the change is wrong.

## Language

- Never add medical or diagnostic language anywhere — prompts, screens or docs.
  Observations only ("looks dry"), never conditions ("you have eczema"). The disclaimer
  in `analysis.meta` stays.

## Dependencies and scope

- Never add a new dependency unless the task names it. Ask first.
- Never change, rename or reformat files the current task doesn't name.

## Tests

- Never delete or weaken an existing test to make it pass.

## Secrets

- Secrets come from environment variables only. Never hardcoded, never logged, never
  returned in a response.

## Outside services

- For any outside service (Razorpay, Gemini), read the current official docs before
  writing code. Never write integration code from memory.

## Definition of done

- A task is finished only when the verification commands run clean and `backend_test.py`
  passes. "The code looks right" is not finished.

## Communication

- The user is not a coder. Explain everything in plain English. If a decision needs their
  input, ask one clear question with the options spelled out.
