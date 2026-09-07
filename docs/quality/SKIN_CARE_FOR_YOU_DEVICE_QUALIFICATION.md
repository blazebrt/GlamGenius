# Skin-care FOR YOU — device qualification

The Step 8L skin-care FOR YOU loop is built and covered by Jest. This document
is what turns that into a **qualified** loop: the checklist a person works
through with a real phone, a real pack and a real barcode in their hands.

It is split into three parts, and the split is the whole point of the document.

| Part | What it is | Who can sign it | State today |
| --- | --- | --- | --- |
| **A. Readiness** | What automated tests prove on every commit | CI | Covered — see below |
| **B. Physical-device qualification** | What only a person with hardware can prove | The tester | **Not performed** |
| **C. Post-Phase-B presentable decision** | The one case that needs an activated release | The tester, after Phase B | **Blocked until Phase B is complete** |

**Nothing in this document is a pass.** Part A is a statement about test
coverage, not about a phone. Parts B and C are unperformed until somebody
writes a date, a device and their name into the record below.

---

## Part A — Qualification readiness (automated)

These hold on every commit and need no hardware. They are the reason a tester
can work through Part B by observing states rather than reading code.

- The category is asked, never inferred, and only **Packaged food** and
  **Skin care** exist.
- An unreadable ingredient list cannot be confirmed; retake stays available.
- There is no text input anywhere in the label review or the safety preflight.
- The safety preflight is five closed flags plus an exclusive
  **None of these**; only true selected flags are sent.
- The client never derives BUY / WAIT / SKIP, never maps a signal to an action,
  never turns a reason key into prose, and gives every action one neutral
  visual treatment.
- A technical failure is never rendered as a verdict.
- A malformed `decision_presentable` shows structural copy only — no verdict,
  no reason, no citation, no profile CTA.
- A hard handoff shows the server's sentence and no verdict.
- The source control opens exactly `citation.canonical_url`.
- The generic scan event for the current barcode must be proven settled before
  a skin-care model call; a confirmation is never followed by another generic
  barcode scan.

Automated readiness lives in:

- `frontend/src/__tests__/step8lDeviceQualificationReadiness.test.tsx` — the
  qualification contract: every state below has a stable hook, and the states
  that must never be confused are provably distinct.
- `frontend/src/__tests__/step8lForYouMobile.test.tsx` — the product rules.
- `frontend/src/__tests__/step8lScanFlow.test.tsx` — the whole loop through the
  real screen, including the settlement barrier.
- `frontend/src/__tests__/productScan.test.ts` — the settlement proof itself.

### The observable states

A tester should be able to name the state on screen without reading code. These
are the stable hooks; they are qualification and accessibility anchors only,
and none of them exposes a release, an evidence id, a safety flag or a payload.

| State on the phone | Hook |
| --- | --- |
| Barcode scanner is live | `scan-camera` |
| Category question | `label-kind-choice` |
| — Packaged food | `label-kind-packaged-food` |
| — Skin care | `label-kind-skin-care` |
| Scan ledger would not settle — capture refused | `scan-settlement-failed` |
| Other error on the category step | `label-kind-error` |
| Label capture step | `label-capture` |
| — Take the photo (busy state on the same node) | `label-capture-take-photo` |
| — Capture/transcription error | `label-capture-error` |
| Live label camera preview | `label-camera` |
| Label review | `skin-care-label-review` |
| — What the camera read as ingredients | `skin-care-ingredients` |
| — Ingredients unreadable | `skin-care-ingredients-unreadable` |
| — Confirm (absent when unreadable) | `skin-care-confirm` |
| — Retake | `skin-care-retake` |
| Confirmed pack | `skin-care-confirmed-card` |
| — Scan another | `skin-care-scan-again` |
| Safety preflight | `safety-preflight` |
| — Pregnancy | `safety-pregnancy` |
| — Breastfeeding | `safety-breastfeeding` |
| — Medication involved | `safety-medication_involved` |
| — Diagnosed condition involved | `safety-diagnosed_condition_involved` |
| — For a child under 12 | `safety-subject_is_child` |
| — None of these | `safety-none` |
| — Check FOR YOU | `safety-submit` |
| FOR YOU card (any state) | `for-you-card` |
| — Checking | `for-you-loading` |
| — Technically unavailable | `for-you-technical-unavailable` |
| — Try again | `for-you-retry` |
| — Hard handoff | `for-you-handoff` |
| — Governed non-decision | `for-you-nondecision` |
| — Add skin details (personal-context gap only) | `for-you-add-skin-details` |
| — Malformed presentable, withheld | `for-you-malformed-presentable` |
| — Verdict | `for-you-verdict` |
| — Reason | `for-you-reason` |
| — Source block | `for-you-source` |
| — Open source | `for-you-open-source` |
| — Link could not be opened | `for-you-source-link-failed` |

Four states all look like "no verdict" on a phone and must never be confused
during qualification. Each has its own hook, and a tester recording a result
should name which one they saw:

| Looks like | Actually is | Hook |
| --- | --- | --- |
| No verdict | The request did not complete | `for-you-technical-unavailable` |
| No verdict | The server explained an absence | `for-you-nondecision` |
| No verdict | The safety authority took over | `for-you-handoff` |
| No verdict | A claim arrived without its evidence | `for-you-malformed-presentable` |

### What Part A cannot prove

Jest, TypeScript, the Metro bundle and the web export prove none of the
following, which is exactly why Part B exists: native camera permission
behaviour, real barcode decoding at real focal distances, real OCR quality on a
real pack under real light, `Linking.openURL` reaching an installed browser,
network loss mid-request, and how any of it behaves on a specific Android
version and handset.

---

## Part B — Physical-device qualification (manual, not yet performed)

### Record for each qualification run

Fill this in per run. No credentials, tokens or URLs with secrets in them.

| Field | Value |
| --- | --- |
| Git SHA under test | |
| Build profile | |
| APK / build identifier | |
| Android version | |
| Manufacturer and model | |
| Network state (Wi-Fi / mobile / throttled) | |
| Backend environment (never production unless stated) | |
| Tester | |
| Date and time (IST) | |

Result column for every case below: `PASS` / `FAIL` / `BLOCKED`, with a note.
A case with no recorded date is **not performed**, not passed.

---

### Case 1 — Camera permission

1. Fresh install (or clear app data).
2. Open the app. The camera permission request appears.
3. Grant it. The scanner opens.
4. Repeat with a fresh install and **deny**. The app does not crash.
5. From the denied state, follow the on-screen recovery path
   (*Continue without scanning*, then re-enable in system settings) and confirm
   the scanner works afterwards.

Do not invent a new permission flow. Raise a defect if the existing one is
wrong.

**Result:**

---

### Case 2 — Barcode

Use an actual retail pack with a printed EAN/UPC.

1. Point at the barcode. One physical scan produces one logical lookup.
2. Hold the camera on the barcode for several seconds. The rapid native
   callbacks must not produce multiple visible scans or stacked results.
3. The product result displays.
4. **Scan again** returns to the scanner.

Do not add a manual barcode entry field to make this easier.

**Result:**

---

### Case 3 — Login gate

1. Signed out, scan a product. Scanning itself works with no account.
2. Attempt label capture. The existing auth gate appears.
3. Confirm no new or parallel sign-in path was introduced.

**Result:**

---

### Case 4 — Category

1. From a scanned product, start label capture.
2. Exactly two options are shown: **Packaged food** and **Skin care**.
3. No hair-care, cosmetics, make-up or supplement option is present.
4. Select **Skin care** explicitly. Nothing infers it.

**Result:**

---

### Case 5 — Settlement failure

**Controlled non-production environment only.**

1. Scan a barcode, then interrupt connectivity (or otherwise cause the
   `/scan/events` write for that barcode to fail) before choosing a category.
2. Choose **Skin care**.
3. The skin-care model call does **not** begin: no photograph is requested, no
   transcription happens, nothing is confirmed.
4. The refusal is neutral and retryable —
   `scan-settlement-failed`, wording: *"We could not finish saving this scan.
   Check your connection and try again."*
5. No BUY, WAIT or SKIP appears.
6. Restore connectivity, choose **Skin care** again, and confirm progression
   now succeeds.

An unrelated barcode still stuck in the offline queue must **not** block this
one. Verify by leaving a different barcode queued and confirming this pack
still proceeds.

**Result:**

---

### Case 6 — Label photo

Use a real skin-care pack.

1. Photograph the ingredient label with the device camera.
2. What the camera read is shown back (`skin-care-label-review`).
3. There is no editable ingredient field anywhere.
4. **Retake** is available and produces a new capture.
5. Nothing is saved or confirmed before the explicit confirmation.

**Result:**

---

### Case 7 — Unreadable label

Deliberately capture a blurred, dark or obstructed label.

1. The ingredients are marked unreadable
   (`skin-care-ingredients-unreadable`).
2. The confirm control is **absent**, not merely greyed.
3. **Retake** is available.
4. No product interpretation, verdict or FOR YOU state appears.

**Result:**

---

### Case 8 — Confirmation

With a readable capture:

1. Read the camera output against the physical pack, word for word.
2. Confirm.
3. The confirmed-pack state appears (`skin-care-confirmed-card`).
4. **No second generic barcode scan happens afterwards.** If the app returns to
   the scanner or re-looks-up the barcode at this point, that is a defect: the
   plain lookup would become the device's newest scan event and supersede the
   confirmation.

**Result:**

---

### Case 9 — Safety: none of these

1. At the safety preflight, select **None of these**.
2. It is exclusive: no other choice can be checked at the same time.
3. Submit. Exactly one FOR YOU request is made.
4. No medical information is stored anywhere; the selection does not survive
   leaving the product.

**Result:**

---

### Case 10 — Each hard handoff

Test these five **independently**, one product session each:

| Flag | Result |
| --- | --- |
| Pregnancy | |
| Breastfeeding | |
| Medication involved | |
| Diagnosed condition involved | |
| For a child under 12 | |

For each one, verify:

- only that one flag is selected;
- the server returns a hard handoff (`for-you-handoff`);
- **no** BUY, **no** WAIT, **no** SKIP;
- no product citation or verdict appears alongside the handoff;
- the sentence shown is the server's, unmodified.

Never type a medicine name, a diagnosis or a symptom. There is no field for
one, and there must not be.

**Result:**

---

### Case 11 — Profile missing

With the required skin profile fact absent:

1. Complete the loop to FOR YOU.
2. A governed non-decision appears (`for-you-nondecision`).
3. **Add skin details** is offered (`for-you-add-skin-details`).
4. It opens the controlled profile screen — closed choices only, no
   unrestricted free text.
5. Set the fact, then return to FOR YOU.
6. Exactly **one** genuine refetch happens on focus return. No rescanning, no
   re-photographing, no re-confirmation.

**Result:**

---

### Case 12 — Presentable decision

> **BLOCKED.** This case cannot be marked PASS until Phase B — Controlled
> Production Knowledge Activation V1 — has:
>
> 1. passed independent PR review;
> 2. merged;
> 3. been deliberately operated against the target environment;
> 4. had the exact Step 8I release activated through the governed process.
>
> Until all four are true, record this case as **BLOCKED**, not FAIL and
> certainly not PASS. This document does not activate anything.

Once that has genuinely happened:

**Setup**

- a confirmed physical skin-care pack whose printed ingredient list contains
  petrolatum;
- the controlled profile fact `care_skin_usual_feel=often_dry_or_tight`;
- safety answered **None of these**.

**Verify the device displays**

- the server's presentable status;
- the server's BUY verdict, in the server's words (`for-you-verdict`);
- the reviewed server reason (`for-you-reason`);
- the exact server-selected AAD citation (`for-you-source`);
- the exact canonical source URL behind **Open source**.

The expected reviewed result is documented here so a tester knows what they are
looking at. **The application must receive it from the backend.** No client
qualification helper, fixture or build flag may contain that decision, and none
does — the client has no petrolatum, no thresholds and no verdict logic in it.

**Result:**

---

### Case 13 — Source

From a valid presentable decision:

1. Tap **Open source**.
2. The exact canonical URL supplied by the server opens.
3. It is not a reconstructed search URL.
4. It is not a publisher homepage fallback.
5. If opening fails (no browser, malformed URL), a neutral link-failure message
   appears (`for-you-source-link-failed`) and nothing else changes.

**Result:**

---

### Case 14 — Network failure at the FOR YOU request

1. Reach the safety preflight, then disable connectivity.
2. Submit.
3. The technical-unavailable state appears
   (`for-you-technical-unavailable`).
4. **No** BUY, **no** WAIT, **no** SKIP. A technical failure must never become
   a WAIT — WAIT is a reviewed action, not a way of saying "we could not ask".
5. **Try again** is offered.
6. Reconnect and retry. The decision arrives.

**Result:**

---

### Case 15 — Rescan hygiene

1. Complete one whole skin-care flow through to a FOR YOU result.
2. Tap **Scan another** and scan a different product.
3. Verify that none of the following survives into the new session:
   - the confirmed label;
   - the safety selection;
   - the FOR YOU result;
   - any technical error state.

Safety information must not leak from one product to the next.

**Result:**

---

## Part C — Sign-off

A qualification run is complete only when every case in Part B has a recorded
result and a date. Case 12 stays **BLOCKED** until Phase B is genuinely
complete; a run with Case 12 blocked is a valid partial qualification and must
be recorded as such, never as a full pass.

| | |
| --- | --- |
| Cases performed | |
| Cases passed | |
| Cases blocked | |
| Defects raised | |
| Tester signature | |
| Date | |

---

## What this document deliberately does not do

- It does not activate a release, publish evidence or compile a knowledge pack.
  That is Phase B's work, through Phase B's governed process.
- It does not add a debug menu, a decision toggle, a barcode text field, an
  ingredient editor, a fake safety context or an "E2E mode". Every hook above
  names something already on screen. Product authority is unchanged.
- It does not introduce an E2E framework. The critical path is native camera,
  native permissions, real OCR, external link opening and real network loss —
  all of which need hardware regardless. What to automate is a decision worth
  making after a first real qualification run, not before it.
