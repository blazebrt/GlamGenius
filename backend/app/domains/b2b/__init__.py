"""Step 17 — the Verified Product Truth API (B2B V1).

An organisation can *request* a GlamGenius product decision through an API.
It can never *influence* one. That is the whole design, and it is enforced by
the shape of the code rather than by a promise:

::

    Evidence / canonical facts
              |
        Product Truth           (app/domains/product/truth.py — shared)
              |
     deterministic decision
              |
          B2B API               (this package + app/api/b2b)

Nothing points the other way. The Product Truth projection
(:mod:`app.domains.b2b.truth`) has no parameter through which a client, a key,
a quota or a usage count could reach it: a ₹1 contract and a ₹100 crore
contract get the same answer for the same confirmed label and the same
published ruleset, and the test suite proves it byte for byte.

What this package owns
----------------------
* :mod:`.models` — three small operational tables: who may call
  (``b2b_api_clients``), with what credential (``b2b_api_keys``, a hash, never
  the secret), and how many calls were made each day
  (``b2b_api_usage_daily``, aggregate counts only — no barcode, no answer).
* :mod:`.credentials` — the key format, generation and constant-time
  verification.
* :mod:`.access` — client and key lifecycle for administrators (issue,
  revoke, suspend, activate) and the per-request authentication.
* :mod:`.quota` — the per-minute burst limit and the concurrency-safe daily
  allowance. Quota changes how many answers a client may have, never what
  any answer says.
* :mod:`.truth` — the Store B-only Product Truth projection and its
  ``b2b-product-truth-v1`` contract.

What it deliberately does not do (V1)
-------------------------------------
No Open Food Facts data, ever (the ODbL wall; ``docs/architecture/ODBL_DATA_WALL.md``).
No comparable alternative and no value comparison (both read Store A). No
physical-pack claim, no lot, no official-record pack match. No personal
context of any kind. No AI call. No write route for clients, no bulk route,
no list, no search, no export, no webhook. No price, commission, contract
value or charging logic. No stored answer.

See ``docs/architecture/B2B_PRODUCT_TRUTH_API.md`` for the full rationale.
"""
