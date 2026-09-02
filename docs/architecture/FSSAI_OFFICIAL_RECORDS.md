# FSSAI Official Records V1

The authoritative public FoSCoS Food Recall surface is
`https://foscos.fssai.gov.in/food-recall`, whose published columns include
Recall ID, FBO, brand, batch/lot, product, reason, dates, status, licence and
nature of recall. It is browser-rendered. V1 does **not** scrape it or claim
automatic live ingestion: an operator imports a reviewed official FoSCoS JSON
export with `python -m app.commands.ingest_fssai_recalls <official-export-file>`.
No captcha, protected endpoint, mirror, cookie/login, or scraping bypass is
used. The command accepts only the documented `{"rows": [...]}` representation,
commits atomically, and records success or failure without deleting old data.

The `fssai-foscos-food-recall.v1` adapter is deterministic and preserves the
raw payload hash. `official_records` is a dedicated Store-B domain with
`official_source_fetches`, `official_records`, and immutable
`official_record_revisions`. Repeated content is idempotent; changed content
creates a revision; a record is never deleted merely because a later fetch does
not contain it.

Exact pack matching requires both an exact normalized FSSAI licence and exact
normalized batch/lot. Licence normalization applies Unicode compatibility
normalization and keeps digits; batch normalization applies Unicode
compatibility normalization, trimming and case folding while preserving
separators (so `B-123` is not `B 123`). Brand and product are corroboration
checks. Missing or conflicting identity is not a match.

The Product Result `official_records` envelope is additive and separate from
the scientific grade, Open Food Facts Store A, and the complaint handoff. It is
shown only for an exact match and links back to FoSCoS. No health, safety, or
legal conclusion is inferred from the record; the portal remains authoritative.
