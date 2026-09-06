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

On any conflict or source drift: **STOP**. Never overwrite or choose between records.

## Stage 1 — identity draft

Use the existing Step 7A/substance evidence workflow to create exactly:

- key `petrolatum`, entity kind `mixture`;
- preferred official-reference name `Petrolatum` only;
- governmental PubChem/ChemIDplus SID 135345390 identity source and reviewed factual
  use note.

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
locator, reason key, and content hash. Record Step 8H verification only after the
named reviews really happened. Run the existing `/validate` operation, then approve.

## Stage 6 — activation hold

**DO NOT ACTIVATE UNTIL THE STEP 8I PR HAS PASSED INDEPENDENT REVIEW, HAS BEEN MERGED,
AND THE EXACT STAGED RELEASE HASH HAS BEEN REVIEWED/AUTHORISED FOR ACTIVATION.**

No production activation, evidence publication, verification, or release mutation is
part of the Step 8I implementation PR.
