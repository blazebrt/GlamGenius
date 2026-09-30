"""Step 16 — Trust-Preserving Commerce V1: a disclosed outbound handoff.

Commerce comes after the decision and never inside it. The dependency runs one
way only::

    Product Truth / Purchase OS  ->  Commerce

This package reads a finished Purchase Operating System answer and, when that
answer honestly supports it, returns one outbound merchant search address for
one exact barcode. Nothing in grading, evidence, official records, the
comparable alternative, the Purchase OS, Decision Memory, the shelf, Product
Watch, Care or Fragrance imports it (``tests/test_step16_commerce_handoff.py``
holds that structurally).

* :mod:`.partners` — the closed partner registry and the address it builds.
* :mod:`.handoff` — ``commerce-handoff-v1``: who, if anyone, may be the target.
* :mod:`.analytics` — the one account-owned outbound-open event.
* :mod:`.metrics` — aggregate counts of that event, for admins.

No table, no column, no migration: a code registry and the existing
``app_events`` rows are the whole of it.
"""
