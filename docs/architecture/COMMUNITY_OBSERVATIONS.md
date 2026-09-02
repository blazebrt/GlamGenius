# Shopper observations

Four kinds of claim live in a Product Result and none of them outranks another by accident:

| Layer | What it is | Who establishes it |
| --- | --- | --- |
| Label fact | What the pack says | Step 3 capture, from a photo of the pack |
| Scientific grade | What published thresholds say about the food | The grading engine, over reviewed evidence |
| Official record | What a regulator has published | Step 4, from a FoSCoS export |
| **Shopper observation** | **What separate people report seeing** | **Step 5, this document** |

Community is the lowest-privilege of the four. It can never modify a grade, a decision, a negative, a positive, an `OfficialRecord`, a `LabelSnapshot`, a label version, or a product's confidence. It adds one thing to the response and nothing else.

## What a shopper may say

Ten codes, and no free text anywhere — not a note, not a caption, not an optional "anything else". Zero free text is a constitutional rule, and the table has no column to put one in.

Four are about our data, and are scoped to the barcode: `barcode_result_differs_from_pack`, `ingredients_list_differs_from_app`, `nutrition_panel_differs_from_app`, `pack_size_differs_from_app`. They say shoppers reported a discrepancy. They correct nothing — Step 3 label capture remains the only path by which what the app believes about a product changes.

Six are about the condition of one physical pack, and are always scoped to one lot: `date_marking_unreadable`, `seal_broken`, `pack_leaking`, `pack_swollen`, `visible_foreign_material`, `insect_observed`. A packet made on one line on one day says nothing about the next lot, so three reports across three lots stay three ones — Step 5 deliberately does not infer a product-wide claim from them.

V1 omits smell, colour, "appeared spoiled" and the like: those depend on storage and on the reporter's judgement. It omits "adulterated", "fake", "counterfeit", "unsafe" and "fraud" outright, because they are conclusions, and a conclusion about a named brand is the thing the writing rules exist to prevent.

## Who may report

Scanning stays anonymous. Viewing a Product Result stays anonymous. Submitting does not, because a report can put a brand's name next to a stranger's claim. A submission needs all of:

- an authenticated account;
- a registered device that account has claimed (`device.claimed_by_account_id == account_id`) — a handset minted thirty seconds ago does not get public influence;
- a `ScanEvent` for that barcode from that account on that device, so nobody can file against an enumerated barcode list;
- an `active` `MediaAsset` owned by the account with purpose `community_observation` and an image content type. An inventory photo or an analysis photo is not evidence the person offered about a pack.

Reporter identity is taken from the session and the device token. The request body carries `client_report_id`, `barcode`, `observation_code` and `photo_asset_id`, and is `extra="forbid"`, so an injected `account_id` is refused rather than ignored.

**A photo is not verification.** The system knows one thing: a shopper supplied an image. Nothing looks at it — no Gemini, no vision, no OCR, no perceptual judgement anywhere in acceptance, aggregation or moderation. It is never called verified, proof, confirmed or validated.

## The pack in this person's hand

A pack-condition report needs a lot number, and the shopper is never asked to type one. It comes from **this device's own most recent scan** of that barcode, ordered `created_at DESC, id DESC` — server time, never the client's `scanned_at`, which an offline queue may backdate.

Two rules, and the second is the one that goes wrong quietly:

- The batch comes from **this device's** capture, not the product's newest `LabelSnapshot`. That row may be a stranger's photograph of a stranger's packet, and Step 3 deduplicates identical label content into one snapshot owned by whoever captured it first, so ownership of it was never the question.
- It comes from the **newest scan only**. If the newest event is a plain scan, a different physical packet is in this person's hand now and its lot is unknown until they capture it. Reaching past that scan to an older capture would attach a report about today's packet to last month's lot, and would keep showing this shopper a signal about a pack they put back on the shelf.

`GET /api/v2/community/observations/context/{barcode}` answers this server-side, so the app never guesses and is never handed anybody else's lot. If the person's own capture yields no meaningful lot, a batch-scoped submission is refused with `batch_capture_required` and the app sends them to the existing label capture.

Lot comparison is this domain's own rule, not an import from the FSSAI adapter: NFKC, whitespace collapsed, casefolded, separators preserved. `B-123` equals `b-123` and does not equal `B 123`. Placeholders (`NA`, `nil`, `other`, `loose`, zero-only strings and the rest) identify nothing and are refused; short real codes such as `C`, `1` and `L0` survive.

## Provenance: the scan, not the snapshot

Every report stores `scan_event_id` — the exact scan that established its context, and the authoritative physical-pack provenance. The report's account, device and barcode must all agree with that event at acceptance.

`label_snapshot_id` is set **only** when a snapshot exists for that exact scan event, and is otherwise null. It is never inferred by matching content fingerprints: Step 3 deliberately excludes `batch_number` from its semantic fingerprint, so two packets from lots B1 and B2 with the same printed label share one fingerprint by design. Pointing a B2 report at somebody else's B1 capture because the semantic content matched would be a physical claim the data does not support. Null is the honest answer.

## Viewing: your lot, not the newest one

A batch signal is assembled only for a viewer whose **own device** confirmed that same lot. The product's newest `LabelSnapshot` is not that: it may be a stranger's photograph of a stranger's packet, and showing this shopper a warning about a lot they are not holding is the exact false positive a batch scope exists to prevent. A viewer with no confirmed lot of their own sees no batch signal at all. Product-data signals remain barcode-scoped and reach everyone.

## When three reports become one sentence

`community-observations-v1`, a **product display policy** — not scientific evidence, not a regulatory finding, and versioned so a future policy can re-read the same retained rows and answer differently.

A row counts toward a public signal only while it is `accepted`, inside the 90-day active window measured on server time, and backed by a live photo asset. Then:

- **`MIN_PUBLIC_REPORTERS = 3`**, counted as distinct `account_id`. Not rows, not uploads, not devices: ten reports from one person, or one person on three claimed phones, is one reporter.
- **`MIN_UNIQUE_PHOTOS = 3`**, counted as the size of the largest **reporter-to-photograph pairing** — a maximum bipartite matching over `MediaAsset.sha256`, not a raw count of distinct hashes.

The matching is what makes the second threshold mean anything. Counting accounts and hashes as two separate sets, one account uploading three distinct photographs while two friends each re-upload the first gives three accounts and three hashes — public, though only one person ever photographed anything. Matched, the two friends compete for the one hash they share and only one can hold it, so the pairing is two. Conversely, when a genuine assignment exists (A has H1 and H2, B has only H1, C has H3), the matching finds `A→H2, B→H1, C→H3` where a greedy pass would strand B and wrongly refuse a signal three people did evidence.

The threshold does not bend because a code sounds serious. That is precisely when a single mistaken or malicious report does the most damage to a brand that has done nothing wrong. One report may be retained internally; it is never public. Two are never public.

Rate limits (10/account/hour, 20/account/day, 10/device/hour) are enforced behind transaction-scoped PostgreSQL advisory locks taken account-then-device, because counting rows and then inserting is not a limit — several requests can all read nine and all pass a limit of ten. The idempotency key is re-read behind the lock, so a concurrent copy of the same retry still resolves as a retry rather than being refused for the quota slot it is itself occupying.

Aggregation reads current rows on every request rather than a cached count, so a withdrawal, a moderation, a deleted photo or a deleted account takes effect on the next response instead of leaving a stale public claim standing. Deleting an account removes its reports by `ON DELETE CASCADE`; if that drops three reporters to two, the signal disappears.

### Withdrawal is terminal

A moderator may move a report between `accepted`, `under_review` and `invalid` in either direction — a finding can be reconsidered. A report the shopper withdrew is out of reach: any moderation attempt is refused with `report_withdrawn`, because setting it back to `accepted` would republish a person's claim about a brand after they retracted it. Their withdrawal outranks ours. A database check keeps `status = 'withdrawn'` and `withdrawn_at IS NOT NULL` in lockstep, so nothing can quietly clear one field and revive the row.

The shopper reaches their own rows through `GET /api/v2/community/observations/mine/{barcode}` — their reports for one barcode, and nothing else. Not a feed, not anybody else's history, not a profile. It exists so a person can manage the content they created after closing and reopening the screen, rather than depending on state that died with the modal.

### One draft, one key

The client sends a `client_report_id` that is stable for one logical draft — this pack, this observation, this photograph. Retrying after a lost response reuses it, so the server recognises the retry instead of storing a second identical report from one person. Deliberately changing the observation or replacing the photograph is a different draft and earns a new key.

## Publication fails closed

Two settings gate display, and both must be right:

- `COMMUNITY_PUBLIC_SIGNALS_ENABLED` — **false by default**.
- `COMMUNITY_BRAND_REPLY_URL` — a valid HTTPS address. No support address is invented here; the operator configures the real channel.

Missing, non-HTTPS or malformed, and public display switches off silently rather than publishing anyway. The client applies the same rule to what it renders: the card requires `public_enabled`, at least one signal, and a valid HTTPS reply address, and renders nothing otherwise — publishing the claim while merely omitting the link is the failure this guards against. The separate **Report what you saw** action stays available throughout, because collection and publication are different things: the Constitution requires a visible right of reply before any user-generated content is shown. Collection, moderation and aggregation all keep working while display is off — publication is the only thing gated. Every rendered block carries a visible **Brand right of reply** control. This is not a brand response system; Step 5 builds no brand accounts.

## The public shape

```
community_observations: {
  policy_version, public_enabled, active_window_days, brand_reply_url,
  signals: [{ observation_code, scope, batch_number, independent_reporters,
              first_reported_at, last_reported_at,
              analysis_score_eligible: false, official_finding: false }]
}
```

A signal is a count. It never carries a report id, an account, a device, a photo asset, a hash, a storage key, a moderation state, a username or an avatar. `result_contract_version` stays `"v1"` — the block is additive.

Signals are ordered product-data first, then the viewer's batch, newest first, then by code. Deliberately not by severity: deciding which observation is more alarming is the step from observation to conclusion this system exists to avoid.

## Silence is not a clean bill of health

An empty `signals` list is never rendered as "no reports", "no issues", "no concerns" or "community verified". It equally means below threshold, outside the window, display switched off, or a batch signal about a lot this shopper is not holding. Absence of a public signal is not evidence of absence, and the block renders nothing at all rather than saying so.

## What Step 5 is not

Not reviews, ratings, comments, replies, likes, followers, profiles, usernames, a feed, a leaderboard, or a map. No geography is collected. No AI touches any part of it. It is structured telemetry with a customer-visible aggregate, and the `community` domain must never be imported into grading, evidence, or official records — a test walks those packages and fails if it is.
