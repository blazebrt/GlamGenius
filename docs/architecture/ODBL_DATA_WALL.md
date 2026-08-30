# The ODbL wall

## The problem, in one paragraph

Open Food Facts publishes its database under the **Open Database License**.
ODbL carries a **share-alike** clause: if you take their database, combine it
with your own into a new one, and then use that new database publicly, you are
obliged to publish *the whole combined database* under ODbL as well.

For us that would mean publishing the absorption knowledge base, the
thresholds, the scores, the verdicts and the decision memory — everything that
makes the product worth anything. Not a fine. Not a warning. An obligation to
give the product away.

## The rule

**The two databases never become one.**

We keep two stores. Open Food Facts data goes in one. Everything of ours goes
in the other. They are read separately and paired in memory, for the length of
a single request, on the barcode. That pairing is thrown away when the response
is sent, so no combined database is ever created and the share-alike clause is
never triggered.

## What lives where

| Store A — Open Food Facts | Store B — ours |
| --- | --- |
| Barcode | Absorption values |
| Product name | Nutrient thresholds |
| Brand | Scores and grades |
| Ingredient list | Purchase verdicts |
| Nutrition values, as published | Evidence links and claims |
| Categories | Customer accounts and profiles |
| Product images | Decision memory |

Two sentences do the work:

- **No value of ours is ever written into a Store A record.**
- **No Open Food Facts field is ever copied into a Store B table.**

## How it is stopped, not just discouraged

Four things, deliberately overlapping.

**1. Separate metadata.** Store A's tables are declared on their own
SQLAlchemy `MetaData`, in `app/domains/off/models.py`. Nothing in the
application can declare a foreign key into them, because SQLAlchemy cannot
resolve a target in another metadata. The main Alembic chain does not manage
them either, so a migration written for the product cannot reach in and add a
column.

**2. Separate connection.** `OFF_DATABASE_URL` points Store A at its own
database. Set it to a different server and the separation is physical. Left
empty it falls back to the application database — acceptable in development,
and the app logs a warning so it is never a silent choice.

**3. An allowlist.** `OFF_FIELDS` in `app/domains/off/wall.py` names every
column Store A may hold. `assert_no_proprietary_fields()` fails if a table
grows anything else. Adding a name to that list is a visible, reviewable act,
which is exactly what it should be.

**4. A write guard.** `guard_off_session()` inspects every object on its way
to Store A and refuses anything carrying a value outside the allowlist. This is
the one that catches a value set dynamically — a dictionary unpacked from one
of our own records, say — which no static check could see.

The export job adds a fifth check at the point of publication: every row is
validated on its way to the file, independent of whatever produced it.

## What would break it

Any of these creates a derived database and triggers the obligation:

- A table holding an Open Food Facts field next to one of ours.
- A foreign key in either direction between the stores.
- A materialised view, or any cached table, spanning both.
- A scheduled job that writes the joined result anywhere.
- Copying `nutriments` into a scoring table "to make the query faster".

That last one is the realistic danger. It looks like a performance improvement
and it is a licence breach.

### The phone's offline cache is not one of these

The scanner keeps recent lookup responses on the phone
(`src/services/productScan.ts`) so it still answers with no signal. That cache
holds the joined response, our half beside theirs, and that is fine: it is one
person's own device holding answers to their own queries, it is never published,
and the attribution renders with it. It stops being fine the moment the same
join is written on a server — a shared cache, a warm-up job, an analytics
table. Keep the pairing on the phone and in the response, and nowhere else.

## What we give back

`python -m app.domains.off.export` publishes our copy of the Open Food Facts
data as an ODbL-licensed JSON Lines file, with the licence notice and a
manifest carrying a checksum. It reads Store A and nothing else — it has no
session for Store B — so it is also the clearest possible demonstration that
nothing of ours has leaked in.

## Attribution

Every surface showing Open Food Facts data must render, verbatim:

> Contains information from Open Food Facts, made available under the Open
> Database License (ODbL)

The wording is a licence condition, not copy. It must not be shortened or
paraphrased. Backend: `app/domains/off/attribution.py`. App:
`src/components/common/OpenFoodFactsAttribution.tsx`.

## Their API

Open Food Facts asks every caller to identify itself with a descriptive
User-Agent and rate-limits or blocks anonymous traffic. The header is built in
`app/domains/off/client.py`, in the one place all outbound calls go through.
Set `OFF_CONTACT_EMAIL` to a real address.

## The tests that hold this up

`backend/tests/test_odbl_data_wall.py`. If one of them fails, do not adjust the
test. The failure means a change is about to put the product under ODbL.
