# The FOR YOU mobile loop

*Step 8L. The first milestone where a real customer can complete the governed
personal-decision loop from the phone.*

```
scan a barcode
  → choose "Skin care"
  → photograph the ingredient label
  → check what the camera read
  → confirm the physical pack
  → answer a one-session safety check
  → receive the exact Step 8K governed result
  → open the exact cited source
```

Everything decided in that sequence is decided on the server. This document is
mostly about why the client is allowed to do so little.

---

## Why this is one milestone and not three

Three gaps each individually blocked the pilot, and closing any two of them
still leaves no customer loop:

- The phone used the **generic** label-capture path, so no capture ever became a
  category-bound skin-care `LabelSnapshot`.
- There was no UI for the two controlled skin facts Step 8A matches against, so
  the personal context was always empty.
- Step 8K had no customer surface at all.

Shipped separately, each would be a half-integration nobody could exercise
end to end.

## Why the category is an explicit user choice

Step 8J deliberately built a **separate** skin-care capture surface rather than
widening the food one. Reaching `/scan/skin-care/label/transcribe` *is* the
structured assertion that this is a skin-care product — the route is the
category authority.

That only holds if a person makes the choice. So the app asks, in one small
stage before the camera:

> **What kind of label is this?** · Packaged food · Skin care

It is not inferred from ingredient words, not classified by the model, and not
assumed for every photographed label. A category guessed from a picture would
enter the governed chain wearing the same clothes as one a person established,
and no later layer could tell them apart.

Hair care and cosmetics are absent rather than disabled: there is no capture
flow behind them, and offering a dead choice is worse than offering none.

**Packaged food keeps its existing path**, unchanged, on the generic
transcription and confirmation contracts.

## Why the category is never sent to Step 8K

Step 8K reads `product_category` from the confirmed snapshot. The client does
not send it, cannot send it (`extra="forbid"` server-side, and the request
builder has exactly two possible keys), and has no reason to know it.

A read-time category would let the same captured formula be re-asked under a
category nobody confirmed, and the evidence engine would answer conscientiously
about the wrong question.

## Why no plain scan may happen after confirmation

**This is the single most load-bearing rule in the milestone.**

Step 8K defines the current physical pack as the device's **newest** scan event.
The pre-existing generic flow ends with `confirm → scanBarcode(barcode)`, which
refreshes the food result. Doing that after a skin-care confirmation would make
the plain lookup the newest event, and Step 8K would correctly answer
`pack_not_confirmed` — the person would confirm a pack and immediately be told
they had not.

So the skin-care path never re-scans. It transitions straight to a dedicated
confirmed-label state built from the draft it already reviewed. A test asserts
`scanBarcode` is called exactly once for the whole journey, and reintroducing
the old line fails it.

That confirmed state is deliberately **not** disguised as an Open Food Facts
result. Nothing in it came from them, so rendering their attribution beside it
would be a false statement about where the data originated.

### The plain scan must settle first

Not scanning again is necessary but not sufficient. `scanBarcode` records its
own `/scan/events` write **in the background**, so a lookup can answer at shop
speed — and that write can still be in flight when the person chooses Skin
care. The race runs: lookup answers → plain event still pending → label
photographed → label confirmed → the delayed plain event finally lands, takes a
later server `created_at`, and becomes the newest event. Step 8K then correctly
reports `pack_not_confirmed`, seconds after somebody confirmed a pack.

So before any skin-care model call, `settleScanEvents(barcode)` proves the write
cannot arrive late: it awaits the tracked in-flight persistence for that
barcode, flushes the offline queue, re-reads it, and requires no unresolved
entry **for that barcode**. A different barcode stuck in the queue never blocks
this one.

If it cannot be settled — offline, or the queue will not flush — the capture
does not begin. No photograph, no model call, no confirmation: only a neutral
retryable message. Spending a model call on a pack whose confirmation might be
silently superseded is the worse outcome, and technical failure is never turned
into a verdict.

## Why the client renders and does not decide

The FOR YOU card inspects a presentation status and prints server fields. It
has no signal-to-action logic, because it never receives a signal. Concretely:

| Rule | Why |
| --- | --- |
| Verdict word comes from `verdict_text`, never `action` | Governed copy is versioned server-side; a client that formatted the action would silently fork from it |
| Reason comes from `reason_text`, never `reason_key` | A key-to-prose table on the phone is an unreviewed second copy of the science |
| One neutral card treatment for every action | BUY-green / SKIP-red would make styling an interpretation layer |
| No fallback verdict for an unknown status | "We do not know what this means" is not a reason to guess |
| A presentable response missing its verdict, reason, citation or openable URL shows **nothing at all** | Half a decision is worse than none — see below |

One reason key is read — `for_you.not_enough.personal_context` — and only to
decide whether to show a button. The sentence beside it still comes from the
server.

### A malformed presentable decision is not a non-decision

These two look similar and must be treated differently.

A **governed non-decision** (`not_enough_information`, `not_enough_explanation`
and the rest) carries a sentence that explains an *absence*. It asserts nothing
about the product, so it is shown, and `for_you.not_enough.personal_context` may
still offer the profile editor.

A **malformed presentable** — `decision_presentable` arriving without its
`verdict_text`, `reason_text`, citation, or an openable `canonical_url` — is a
different thing entirely. Its `reason_text` is a *product or personal claim*,
and that is precisely what the evidence chain exists to license. Printing it
while the citation is missing would put an unsourced claim on screen.

No source, no claim. Everything is withheld — verdict, reason, citation, and the
profile-gap affordance, which would otherwise leak that a real evaluation
occurred — and only neutral structural copy is shown:

> This result is not available right now.

### Exactly one evaluation per answer

The focused effect is the **single owner** of every FOR YOU request. Submitting
the safety answer records it and nothing more.

An earlier version fetched directly in the submit handler *and* re-fetched from
the focus effect when `safety` changed, producing two initial evaluations for
one deliberate answer — two governed evaluations, and a last-response-wins race
between them. One answer now produces exactly one request, and a genuine
navigation away and back produces exactly one more.

The cited source is opened at exactly `citation.canonical_url`. No publisher
homepage, no search, no reconstruction, no ranking.

## Why the hard handoff renders alone

When Step 8K returns `handoff_required`, the card shows the safety authority's
own message, byte for byte, and nothing else: no verdict, no citation, no
alternative, and no medical wording written by the client.

## Why safety context is session-only

The Step 8A hard-handoff authority accepts ephemeral context, and this milestone
gives it the current situation through six closed choices:

> Pregnancy · Breastfeeding · Medication involved · Diagnosed condition involved ·
> For a child under 12 · None of these

There is no field for a medicine name, a diagnosis, a symptom or a note. The
product does not need to know *which* medicine in order to hand over to a
clinician, and collecting it would create a health record this milestone has no
business holding. `stated_age` is deliberately not collected in V1.

Only `true` flags are sent; `None of these` sends an empty object. Nothing is
written to storage, the profile, analytics, breadcrumbs or logs. It exists for
the length of one request.

## Why the two skin facts are persistent instead

These are different in kind, and the difference is the customer's intent. The
safety check answers "right now, for this one question". The two skin facts are
answers a person deliberately saves to their profile, through the existing
`PATCH /api/v2/profile`.

Exactly two keys, exactly the backend vocabulary, every answer a button:

- `care_skin_usual_feel` — `comfortable` · `often_dry_or_tight` · `often_oily` · `mixed` · `not_sure`
- `care_skin_sensitivity` — `rarely_reactive` · `sometimes_reactive` · `often_reactive` · `not_sure`

The PATCH carries `key` and `value` and nothing else. The backend already
records a direct profile edit as `user_declared`, confidence `1.0`, `confirmed`;
the phone does not restate that, because a client asserting how far its own
input should be trusted is a client asserting its own authority.

## Why FOR YOU is never cached

The answer depends on the current physical pack, live profile facts, live
evidence eligibility and the active release. Any of those can change between
two identical requests, so a cached decision is a decision that may already be
wrong.

It lives in component state for the active screen only — not AsyncStorage, not
the product cache, not the offline outbox, not Zustand. When the screen regains
focus it asks again, which is what lets editing a skin fact change or withdraw
a verdict **without rescanning anything**: the product evidence is bound to the
snapshot; the personal context is live.

A network failure or a `503` shows a neutral unavailable state with a retry. It
is never turned into WAIT, and it never exposes an exception, a rule id, a
manifest or a hash.

## Why Product Truth stays independent

The free Product Truth experience is unchanged and still reachable on its
existing path. FOR YOU is an additional layer bound to a freshly confirmed
physical pack, not a replacement for it, and `verdict.tsx` is deliberately
untouched: personalising a reference view of a product somebody is not holding
is a different question, for a later reviewed milestone.

## Why there is no payment code here

FOR YOU is a paid-class capability in the product strategy, and the repository
deliberately contains no billing implementation. Step 8L is not the billing
milestone, and exposing the loop to a beta does not redefine the long-term
free/paid boundary. No Stripe, no Razorpay, no subscriptions, no entitlement
state.

## Why nothing is activated

This milestone adds no evidence, no release and no knowledge. The static
registries remain empty, an ordinary seed still produces zero Step 8I claims and
zero releases, and no frontend production file imports a knowledge pack — the
client does not contain the word `petrolatum` at all, and a static test keeps it
that way. Until somebody deliberately activates a release, the loop reaches a
governed non-decision, which is the correct answer.
