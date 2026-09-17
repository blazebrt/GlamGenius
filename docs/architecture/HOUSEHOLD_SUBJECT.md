# The household subject

*Step 11A. Written against `18806c5d` (Step 10B merged).*

## The question this answers

Everything in GlamGenius up to Step 10B was built when one account meant one
person. `account_id` quietly answered three different questions at once:

1. **Who is allowed to make this request?** — the security principal.
2. **Whose rows are being read or written?** — the data owner.
3. **Whose body is this answer about?** — the subject.

A household breaks the third away from the first two. One person signs in, and
asks about a bottle for their mother, their teenager or their eight-year-old.
The first two questions still have the same answer. The third does not.

This document describes the separation, what currently depends on it, and what
deliberately does not yet.

## The six concepts, kept apart

| Concept | What it is | Where it lives |
| --- | --- | --- |
| **Security principal** | The authenticated Supabase account making the request | `CurrentAccount` / `get_current_account` |
| **Household owner** | The account a circle belongs to. One circle per account | `family_circles.account_id` (unique) |
| **Subject** | The human a personalised interpretation is *about* | `family_profiles` row, or the synthesised account holder |
| **Global Product Truth** | What is in the pack. Identical for everyone | `product_records`, `label_snapshots` |
| **Physical ownership** | Who has the thing on their shelf | Step 10A's explicit Add-to-Shelf. Untouched here |
| **Personalised decision** | Truth read through one subject | `personal_lens` → `personal_applicability` → release |

None of these is a synonym for another. In particular, a subject is **not** an
account, and resolving one never widens what an account may reach.

## What already existed, and what was wrong with it

A `family` domain was already in the repository before Step 11:

* `FamilyCircle` — one row per account, `account_id` unique, `ondelete=CASCADE`.
* `FamilyProfile` — up to eight per circle, positions 1–8, `relation` in
  `self | adult | child | other`, `ondelete=CASCADE` to the circle.
* Three routes under `/api/v2/family-circle`.

Three things were true about it that mattered:

1. **It held no age.** The constitution's hardest rule is drawn at 12, and
   nothing in the model could express it.
2. **It was connected to nothing.** No decision path, no scan, no shelf, no
   routine referred to a profile. It was a list that existed and did nothing.
3. **It was promised in the privacy registry and delivered by nothing.** Both
   tables are classified `INCLUDED` in `app/domains/privacy/__init__.py`, which
   is a commitment that the account can read them back — and no export handler
   read either one. An account could create a household and be handed an export
   that did not mention it.

Step 11A does not build a second family model beside this one. It adds the
missing age, connects the model to exactly one decision path, and closes the
export gap.

## The subject authority

`app/domains/family/subject.py` is the only place a subject is resolved.

```
resolve_subject(session, *, account_id, subject_id) -> ResolvedSubject
```

* `subject_id is None` → the account's own stored `self` row, when there is one.
  Omitting the field is the request shape every client used before households
  existed, and it means "me". Once a household exists, what the server knows
  about "me" lives in that row — including an age band the household
  deliberately recorded. Synthesising `not_stated` here instead would make the
  *optional* Step 11 field the thing that decides whether stored authority
  applies: an account could record its holder as under twelve, watch the
  hand-over fire when the row was named explicitly, and get an ordinary answer
  back by simply not sending the field. **An authority a client can skip by
  omission is not an authority.**
  With no circle at all, the subject is synthesised, `not_stated` is correct,
  and nothing is written — asking a question is not opening a household.
* Otherwise the profile is reached **through a join to `family_circles`
  filtered by `account_id`**, and only while `active`. That join *is* the
  authorisation; there is no separate permission check to forget.
* Anything else raises `SubjectNotFound`, which the API answers as a 404 that
  does not echo the identifier.

### One human, one subject

Naming the canonical `self` row and naming nobody resolve to the *identical*
`ResolvedSubject` — same `kind`, same `subject_id`, same `relation`, same
`age_band`. `kind` is derived from the relation in one place (`_subject_from`),
so the two spellings cannot drift apart. That matters beyond tidiness: a later
slice will hang Decision Memory, shelf ownership and Manager state off this
subject, and two spellings that produced two subjects would be a seam for that
state to split along.

`is_account_holder` reads `kind` alone. It used to also accept
`relation == "self"`, which meant a resolver that classified the `self` row
wrongly would still read correctly and no test could see the mistake.

### When the household is structurally broken

A circle with no active `self` row, or with more than one, is unreachable
through any route: the circle and its `self` row are created in one
transaction, no route deletes a profile, the account holder cannot be
deactivated, and no route creates a second one. If it happens anyway, resolution
raises `HouseholdInvariantError` and the route answers the same governed 503 a
broken label snapshot gets. Falling back to the synthesised subject would answer
with *weaker* authority than the household recorded, and picking one of two
rows would decide whose body a decision is about by insertion order.

### Why a foreign subject is 404 and not 403

A real member of another household and an identifier nobody ever issued produce
**the same response**. Distinguishing them would turn the endpoint into a
membership oracle: sweep identifiers, and every 403 names a real person in a
home you cannot see. `test_a_forged_identifier_is_refused_identically` compares
the two responses field by field.

### Age: a band, not a birth date

`family_profiles.age_band` is one of `under_12 | teen_12_17 | adult_18_plus |
not_stated`, with a CHECK constraint and `not_stated` as both the default and
the meaning of every row written before the column existed.

A date of birth would be a more precise fact about a named human being than any
question here requires, and precision we do not need is a liability we would
then have to protect.

`safety_for()` combines the stored band with what the request volunteered, and
the rule is one-directional:

* the band reports the **top** of its range (`under_12` → 11, `teen_12_17` → 17),
  which is the cautious direction — a ceiling can only ever fail to fire a
  hand-over that a floor would have fired wrongly;
* a request may **add** — stating a younger age, or a pregnancy the server does
  not know about;
* a request may never **subtract**. A stored `under_12` stays `under_12`
  however the request describes that person, and a request cannot reach the
  gate by simply omitting the safety block.

This is the difference between a disclosure and an authority. Before Step 11A
the only way the hard-handoff gate could learn somebody was a child was a flag
the client volunteered on each request — fine as a disclosure, useless as an
authority, because the same client can simply not send it.

The gate itself is unchanged: `routines/hard_handoff.evaluate()`, called with
structured `stated_age` and `subject_is_child`. Nothing here substitutes a
generic safety-copy function for it.

### Correcting the band

An authority nobody can correct is not an authority, it is a trap. Because
`safety_for()` is deliberately built so that no request can argue a stored band
downwards, a band that can only be written once becomes permanent — and so does
the hand-over it causes. A child turns twelve and the product still refuses to
answer about them. A parent taps the wrong band on the way in and there is no
way back.

So `PATCH /api/v2/family-circle/profiles/{id}` accepts `age_band` as well as
`active`, as a partial update:

* only the fields actually named are written (`model_dump(exclude_unset=True)`,
  the same convention the rest of the application's patch routes use), so a
  client that only knows how to send `active` still works and does not overwrite
  a band it never mentioned;
* a body that names nothing is refused — an empty body was refused before this
  route had a second field, and a 200 for a request that did nothing reads as
  confirmation that something was recorded;
* neither field accepts `null`. Elsewhere a null in a patch means "leave it
  alone"; for a field that decides whether the product hands somebody to a
  clinician, quietly ignoring what the caller wrote is the wrong way to fail.
  Clearing a stored age is spelled `not_stated`, which is a value in the
  vocabulary rather than an absence;
* unknown fields are refused, because `{"age_bands": "adult_18_plus"}` is a typo
  that must not look like a successful correction.

The vocabulary stays closed and the precision stays coarse. No date of birth, no
exact age, no free text.

**Identity is stable.** The correction updates the row in place. Deleting the
member and creating them again would work today and would be a disaster later,
when Decision Memory, shelf ownership and Manager state hang off that id.

**Two corrections at once** settle as last-write-wins, which is what every other
partial update here does. There is no unique constraint on a band and no
read-modify-write to lose: the ORM issues an `UPDATE` for the named columns
only, so a request changing `active` and one changing `age_band` at the same
moment do not overwrite each other. A version column would be machinery this
does not need.

### The self row

The circle is created with a `relation="self"` row, and the original rule
forbade changing it at all. That rule was protecting something real — the
household's owner must not be able to remove themselves from it — but it was
drawn too wide, which made the account holder's own age band permanently
`not_stated`.

The two are now separated:

* the **shape** of the household is fixed: the `self` row cannot be deactivated,
  and `relation` is not a field this route accepts on any row;
* the **facts** about that person are not: `age_band` can be corrected like
  anybody else's, and reaches the hard-handoff gate the same way.

A patch that mixes an allowed change with a forbidden one is refused whole;
applying half of it would leave the caller with an error and a changed row.

A refusal here returns the same 404 this route has always returned, for the self
case and the foreign case alike — a refusal does not confirm whether the
identifier names anybody.

## What a non-self subject is told today

`PersonalLensSubjectScope.OTHER_HOUSEHOLD_MEMBER` stops the lens **before** it
reads the stored appearance profile, and returns
`NOT_ENOUGH_PERSONAL_CONTEXT`.

The stored profile belongs to the account holder. Reading it and presenting it
as somebody else's would be the one failure a household must never have: two
people quietly sharing one body, invisibly, because the answer would look
perfectly reasonable. Per-subject facts are a later slice; until they exist,
"we do not know enough about this person" is the honest answer.

The hard handoff is evaluated **before** this, so a stored under-12 member
still hands over rather than being told we do not know enough about them.

## Product Truth does not fork

A second person asking about the same pack reads the same `ProductRecord`, the
same `LabelSnapshot` and the same reviewed release. Only the lens differs. The
response's `pack` provenance block is asserted byte-identical across subjects.

There is no `subject_id` column anywhere outside `family_profiles`, and none of
Decision Memory, Shelf ownership, the Manager queue or scan history has changed
semantic ownership. Historical rows still mean exactly what they meant before
this change.

## One word, two meanings

`subject_id` already exists in this codebase and does **not** mean this. On
`/api/v2/routines/experience-feedback` it names the thing being reviewed — a
product or a routine step — alongside a `subject_type`. Here it names a person.
Both are UUIDs, so the type system cannot catch a mix-up. Anything needing both
concepts in one place must name them apart.

## Privacy

* **Export.** A `household` domain reads the circle by `account_id` and the
  profiles through their circle — the same parent-join rule the rest of the
  exporter uses. This closes a gap that predates Step 11.
* **Erasure.** `family_circles.account_id` cascades from `accounts`, and
  `family_profiles.circle_id` cascades from the circle. Proven behaviourally by
  running the real deletion job and then counting rows, not by reading the
  model.
* **Nothing about the question is stored.** Asking FOR YOU about a member
  writes no rows, and the response echoes neither the member id, nor the
  relation, nor the band. A household phone is a shared screen.

## ODbL

Unchanged. Store A and Store B still meet only in memory on barcode. No Open
Food Facts field is copied into a household or member row, and nothing here
reads Store A at all.

## Concurrency

Two races in `add_profile`, both proven against real PostgreSQL with an
`asyncio.Barrier` seam rather than sleeps or sequential mocks:

1. **Two members added at once** both read the same set of taken positions and
   both pick the same number, violating `uq_family_profile_position`. The circle
   row is locked (`SELECT … FOR UPDATE`) before the position is chosen, making
   choosing and taking it one serialised step.
2. **Two first adds at once** both find no circle and both insert one.
   `account_id` is unique, so the loser fails; it recovers inside a savepoint by
   reading the circle the winner created, rather than poisoning the transaction.

Both were pre-existing 500s on a double tap.

## What Step 11A deliberately does not do

* No per-subject profile facts, observations or baseline.
* No subject on Decision Memory, Shelf, the Manager queue, Care, Routines,
  Nutrition, Supplements or scan history.
* No child experience beyond the hand-off — a child under 12 is handed off and
  not advised, and the adult FOR YOU experience is not cloned into a child
  profile.
* No frontend. There is no subject switcher, so there is no subject-bound
  client state to protect yet. When one is built it needs stale-response
  protection equivalent to the exact-identity protection established in
  Step 10A.
* No invitations, no second account, no sharing between households.
* No audit trail of who corrected a band and when. The row holds the current
  fact; the history of corrections is not kept, because nothing in this slice
  reads it.
