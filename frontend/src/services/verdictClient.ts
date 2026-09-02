/**
 * Fetching a verdict and turning the wire shape into what the screen reads.
 *
 * The backend sends keys, bands and sources. The English is chosen here, from
 * the string file, so the wire format carries no copy and the copy carries no
 * logic.
 */
import { S, t } from '../strings/verdict';
import {
  readProductVerdict, type ProductVerdictWire,
} from './apiV2';
import type {
  Alternative, VerdictComponent, VerdictIngredient, VerdictSource,
} from './verdictModel';

const TIER_LABELS: Record<string, string> = {
  plain: S.ingredients.tierPlain,
  green: S.ingredients.tierGreen,
  amber: S.ingredients.tierAmber,
  red: S.ingredients.tierRed,
  black: S.ingredients.tierBlack,
};

const NOVA_PLAIN: Record<string, string> = {
  nova1: S.why.processing.nova1,
  nova2: S.why.processing.nova2,
  nova3: S.why.processing.nova3,
  nova4: S.why.processing.nova4,
};

/** Pick the plain sentence for one component from its key and state. */
function plainFor(component: ProductVerdictWire['components'][number]): string {
  switch (component.key) {
    case 'processing':
      return NOVA_PLAIN[component.state] ?? S.why.processing.plain;
    case 'nutrients': {
      const high = component.high ?? [];
      if (high.length === 0) {
        return component.state === 'exempt'
          ? S.why.nutrients.satFatNotCounted
          : S.why.nutrients.nothingHigh;
      }
      const first = high[0];
      if (first.nutrient === 'total sugars') return S.why.nutrients.highSugar;
      if (first.nutrient === 'salt') return S.why.nutrients.highSalt;
      return t(S.why.nutrients.highSaturatedFat, {
        source: first.attribution ?? S.why.nutrients.term,
      });
    }
    case 'additives':
      if (component.state === 'none') return S.why.additives.none;
      if (component.state === 'child_colour') return S.why.additives.childColour;
      return t(
        component.state === 'black' ? S.why.additives.black : S.why.additives.red,
        { name: component.finding ?? '' },
      );
    case 'naming': {
      const ingredient = component.ingredient ?? '';
      if (component.state === 'not_promised') return S.why.naming.notPromised;
      if (component.state === 'not_declared') return t(S.why.naming.notDeclared, { ingredient });
      if (component.state === 'good') return t(S.why.naming.good, { ingredient });
      if (component.state === 'note') return t(S.why.naming.note, { ingredient });
      return t(S.why.naming.low, {
        ingredient, percent: component.declared_percent ?? 0,
      });
    }
    default:
      return '';
  }
}

const LABELS: Record<string, string> = {
  processing: S.why.processing.label,
  nutrients: S.why.nutrients.label,
  additives: S.why.additives.label,
  naming: S.why.naming.label,
};

const TERMS: Record<string, { word: string; plain: string } | undefined> = {
  processing: { word: S.why.processing.term, plain: S.why.processing.termPlain },
  nutrients: { word: S.why.nutrients.term, plain: S.why.nutrients.termPlain },
  additives: { word: S.why.additives.term, plain: S.why.additives.termPlain },
  naming: undefined,
};

export function toVerdictSource(
  wire: ProductVerdictWire,
  alternative: Alternative | null = null,
  packSizeG: number | null = null,
): VerdictSource {
  const components: VerdictComponent[] = wire.components.map((row) => ({
    key: row.key,
    label: LABELS[row.key],
    plain: plainFor(row),
    band: row.band,
    rule: row.rule ?? row.finding ?? '',
    source: row.source ?? '',
    sourceUrl: row.source_url ?? null,
    sources: row.sources ?? [],
    term: TERMS[row.key],
  }));

  const ingredients: VerdictIngredient[] = wire.ingredients.map((row) => ({
    name: row.name,
    label: row.label ?? row.name,
    tier: (row.tier as VerdictIngredient['tier']) ?? 'plain',
    tierLabel: TIER_LABELS[row.tier] ?? S.ingredients.tierPlain,
    description: row.description ?? S.ingredients.unknownIngredient,
    status: row.status,
    whyFlagged: row.why_flagged,
    sources: row.sources ?? [],
    detail: row.detail
      ? {
        whatItDoes: row.detail.what_it_does,
        whyFlagged: row.detail.why_flagged,
        rule: row.detail.rule,
        authorityPosition: row.detail.authority_position,
        interpretation: row.detail.interpretation,
        evidenceStatus: row.detail.evidence_status,
        source: row.detail.source,
      }
      : null,
  }));

  const missing = wire.missing.map((row) =>
    row === 'ingredient list' ? S.unknown.missingIngredients
      : row === 'nutrition panel' ? S.unknown.missingPanel : row);

  return {
    outcome: wire.outcome,
    grade: wire.grade,
    productName: wire.product_name,
    barcode: wire.barcode,
    brand: wire.brand ?? null,
    taxonomy: wire.taxonomy,
    decision: { action: wire.decision.action, reasonKey: wire.decision.reason_key },
    confidence: wire.confidence ?? null,
    factsProvenance: wire.facts_provenance ?? null,
    labelVersion: wire.label_version ? {
      id: wire.label_version.id, versionNumber: wire.label_version.version_number,
      observedAt: wire.label_version.observed_at, changedFields: wire.label_version.changed_fields,
      completeness: wire.label_version.completeness,
    } : null,
    totalSugarG: wire.nutrition.total_sugar_g,
    saltG: wire.nutrition.salt_g,
    totalFatG: wire.nutrition.total_fat_g,
    proteinG: wire.nutrition.protein_g,
    // The server knows the pack size; the parameter stays as an override for
    // a caller that knows better (a scanned multipack, say).
    packSizeG: packSizeG ?? wire.pack_size_g ?? null,
    basis: wire.basis === 'drink' ? 'drink' : 'solid',
    // ODbL is a condition, not a nicety: wherever their data is shown, the
    // attribution goes with it. Dropping it here left the main graded screen
    // rendering their fields with no notice at all.
    attribution: wire.attribution?.text ?? null,
    components,
    // The label is a key the string file resolves; it is never the rule id.
    resultContractVersion: wire.result_contract_version,
    negatives: (wire.negatives ?? wire.lowers ?? []).map((row) => ({ ...row, label: row.label ?? row.key })),
    positives: (wire.positives ?? wire.helps ?? []).map((row) => ({ ...row, label: row.label ?? row.key })),
    // Existing view consumers can migrate gradually; both aliases derive from
    // the canonical V1 arrays above and cannot diverge.
    lowers: (wire.negatives ?? wire.lowers ?? []).map((row) => ({ ...row, label: row.label ?? row.key })),
    helps: (wire.positives ?? wire.helps ?? []).map((row) => ({ ...row, label: row.label ?? row.key })),
    ingredients,
    alternative: alternative ?? (wire.better_next_action
      ? {
        name: wire.better_next_action.name,
        pricePaise: wire.better_next_action.price_paise,
        grade: wire.better_next_action.grade,
      }
      : null),
    // Step 6A. Passed through as the server decided it: the app chooses the
    // words for these keys and never re-derives the comparison itself.
    comparableAlternative: wire.alternative
      ? {
        policyVersion: wire.alternative.policy_version,
        status: wire.alternative.status,
        reasonKey: wire.alternative.reason_key,
        candidate: wire.alternative.candidate
          ? {
            barcode: wire.alternative.candidate.barcode,
            productName: wire.alternative.candidate.product_name,
            brand: wire.alternative.candidate.brand,
            grade: wire.alternative.candidate.grade,
            band: wire.alternative.candidate.band,
            decision: wire.alternative.candidate.decision,
            comparison: {
              categoryMatch: wire.alternative.candidate.comparison.category_match,
              categorySource: wire.alternative.candidate.comparison.category_source,
              currentGrade: wire.alternative.candidate.comparison.current_grade,
              candidateGrade: wire.alternative.candidate.comparison.candidate_grade,
              basis: wire.alternative.candidate.comparison.basis,
            },
            attributionText: wire.alternative.candidate.attribution?.text ?? null,
          }
          : null,
      }
      : null,
    quantityGuidance: wire.quantity_guidance,
    purityNote: wire.purity_note,
    missing,
    officialRecords: wire.official_records ?? null,
    communityObservations: wire.community_observations ?? null,
  };
}

export const getProductVerdict = async (barcode: string): Promise<VerdictSource> =>
  toVerdictSource(await readProductVerdict(barcode));
