# Step 8I petrolatum/dry-skin activation runbook

This is a controlled operator procedure over existing governed admin workflows. It
adds no mutation API and must never be performed during the implementation PR.

## Stage 0 — preflight (read-only)

1. Verify the deployed commit contains the independently reviewed Step 8I pack.
2. Inspect the current active personal-decision release.
3. Check for any existing or conflicting `petrolatum` identity.
4. Check for any existing or conflicting petrolatum personal-applicability claim.
5. Open the live PubChem SID 135345390, AAD dry-skin guidance, and PubMed PMID
   31532576 sources. Confirm their exact metadata and that they still support the
   reviewed identity and narrow applicability.
6. Confirm the AAD page still supports the *selected* proposition at its exact
   locator — that dermatologist guidance lists petrolatum among cream/ointment
   ingredients for dry skin. It does not need to establish the moisture-loss
   mechanism, and must not be treated as if it does; that mechanism belongs to the
   PubMed study and is not the customer-facing citation.

On any conflict or source drift: **STOP**. Never overwrite or choose between records.

### Exact source metadata to record

Enter these values verbatim. The nulls are deliberate: they record that the source
is silent, and the compiler rejects an inferred value in their place.

**AAD** (`professional_consensus`)

```text
publisher           American Academy of Dermatology Association
locator             What skin care products are best for dry skin? / Ointment or cream
publication_date    (leave empty / null)   <- "Last updated: 1/2/26" is not a publication date
version_or_revision Last updated 2026-01-02
jurisdiction        (leave empty / null)   <- the page states no territory
```

**PubMed / PMID 31532576** (`peer_reviewed_research`)

```text
publisher           Wiley Periodicals, Inc.   <- not "PubMed"; the article carries "© 2019 Wiley Periodicals, Inc."
canonical_url       https://pubmed.ncbi.nlm.nih.gov/31532576/
locator             Abstract / Conclusions
publication_date    2019-09-18
version_or_revision PMID 31532576; DOI 10.1111/jocd.13163
jurisdiction        (leave empty / null)
```

**Identity source** (`government_reference`)

```text
publisher           ChemIDplus            <- PubChem hosts the record; ChemIDplus is the depositor
canonical_url       https://pubchem.ncbi.nlm.nih.gov/substance/135345390
title               Petrolatum [USP]
external id         0008009038
```

## Stage 1 — identity draft

Use the existing Step 7A/substance evidence workflow to create exactly:

- key `petrolatum`, entity kind `mixture`;
- preferred official-reference name `Petrolatum` only;
- governmental SID 135345390 identity source, recorded with publisher `ChemIDplus`
  (the depositor PubChem names, not the host), and reviewed factual use note.

Create a draft only. Do not record review verification automatically.

## Stage 2 — identity governance review

Review the live source and exact draft. Record each existing publication-verification
field explicitly only after the named review really happened. In particular, the
founder checkpoint is true only after founder review. Then approve and publish through
the existing evidence workflow.

## Stage 3 — Step 8G applicability evidence

Create the exact Step 8I draft through the specialized Step 8G admin path, using its
two reviewed sources and one controlled value. Let Step 8G derive `equals_any`.
Review it, record every existing evidence publication attestation explicitly, approve,
and publish through Step 8G. Save the exact serialized published entry as JSON.

## Stage 4 — compile the exact release

Run:

```bash
python scripts/build_step8i_petrolatum_release.py published-entry.json \
  --output canonical-manifest.json
```

Retain the printed content hash. Do not hand-edit the output. Create a Step 8H draft
through the existing admin API with that canonical manifest.

## Stage 5 — exact release review

Inspect the exact claim key/version, semantic identity/direction, policy
identity/action/gap flags, explanation identity, generated AAD source key and exact
locator, reason key, and content hash. The reason key must be exactly
`for_you.skin_care.petrolatum.dry_skin.dermatologist_guidance`; the retired
`...moisture_loss` key must appear nowhere, and there is no alias for it. Record Step 8H verification only after the
named reviews really happened. Run the existing `/validate` operation, then approve.

## Stage 6 — activation hold

**DO NOT ACTIVATE UNTIL THE STEP 8I PR HAS PASSED INDEPENDENT REVIEW, HAS BEEN MERGED,
AND THE EXACT STAGED RELEASE HASH HAS BEEN REVIEWED/AUTHORISED FOR ACTIVATION.**

No production activation, evidence publication, verification, or release mutation is
part of the Step 8I implementation PR.
