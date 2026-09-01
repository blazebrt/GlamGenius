/**
 * Turning a grade into the three lines the primary screen shows.
 *
 * All the judgement about *what to say* lives here, away from the layout, so
 * the screen is only ever arranging text it was handed. The strings themselves
 * live in src/strings/verdict.ts; this file decides which ones and with what
 * numbers.
 *
 * The everyday number is the point of the screen. "22.5 g of sugar per 100 g"
 * is a fact almost nobody can picture. "6 spoons of sugar in one packet" is the
 * same fact, and you can see it.
 */
import { S, t } from '../strings/verdict';

export type GradeLetter = 'A' | 'B' | 'C' | 'D' | 'E';
export type ColourBand = 'green' | 'yellow' | 'red';
export type Outcome = 'graded' | 'not_graded' | 'not_enough_information';

export interface VerdictEvidenceSource {
  name: string;
  url: string | null;
  publisher: string | null;
  version: string | null;
}

export interface VerdictFactor {
  key: string;
  /**
   * The customer-facing name of the thing this row is about.
   *
   * A string key the app resolves to words — never the rule id. "Sugar" is
   * what a person holding a biscuit can act on; "grade.step2.sugar" is not.
   */
  label: string;
  status: string;
  band: ColourBand;
  quantity: {
    value: number;
    unit: string;
    basis: 'per_100_g' | 'per_100_ml' | 'of_product';
  } | null;
  explanation: string;
  rule: string | null;
  sources: VerdictEvidenceSource[];
}

export interface VerdictComponent {
  key: 'processing' | 'nutrients' | 'additives' | 'naming';
  label: string;
  plain: string;
  band: ColourBand;
  rule: string;
  source: string;
  sourceUrl?: string | null;
  sources?: VerdictEvidenceSource[];
  /** A technical word and the words that explain it, shown side by side. */
  term?: { word: string; plain: string };
}

export interface VerdictIngredientDetail {
  whatItDoes?: string | null;
  whyFlagged?: string | null;
  rule?: string | null;
  authorityPosition?: string | null;
  interpretation?: string | null;
  evidenceStatus?: string | null;
  source?: VerdictEvidenceSource | null;
}

export interface VerdictIngredient {
  name: string;
  /** What the row prints: the additive's own name and INS where it has one. */
  label?: string;
  tier: 'plain' | 'green' | 'amber' | 'red' | 'black';
  tierLabel: string;
  description: string;
  status?: string;
  whyFlagged?: string | null;
  sources?: VerdictEvidenceSource[];
  /** What the `?` control opens. Absent when there is nothing more to say. */
  detail?: VerdictIngredientDetail | null;
}

export interface Alternative {
  name: string;
  pricePaise: number;
  grade: GradeLetter;
}

export interface VerdictSource {
  resultContractVersion?: 'v1';
  barcode?: string;
  brand?: string | null;
  outcome: Outcome;
  grade: GradeLetter | null;
  productName: string;
  taxonomy?: { domain: string; category: string; subcategory: string };
  decision?: { action: 'buy' | 'wait' | 'skip' | null; reasonKey: string };
  confidence?: { level: string; text: string } | null;
  factsProvenance?: 'confirmed_label_snapshot' | 'open_food_facts' | string | null;
  /** Per 100 g / 100 ml, straight off the panel. */
  totalSugarG?: number | null;
  saltG?: number | null;
  totalFatG?: number | null;
  proteinG?: number | null;
  /** Grams in the pack, so "one packet" means this packet. */
  packSizeG?: number | null;
  /** Which unit the per-100 panel is in, for when there is no pack size. */
  basis?: 'solid' | 'drink';
  /**
   * Set when the product name, ingredients or nutrition came from Open Food
   * Facts. Their licence requires the attribution on every surface that shows
   * the data, so this travels with it rather than being decided per screen.
   */
  attribution?: string | null;
  components: VerdictComponent[];
  negatives?: VerdictFactor[];
  positives?: VerdictFactor[];
  /** Temporary compatibility aliases of the canonical Product Result V1 arrays. */
  lowers?: VerdictFactor[];
  helps?: VerdictFactor[];
  ingredients: VerdictIngredient[];
  alternative?: Alternative | null;
  quantityGuidance?: string | null;
  purityNote?: string | null;
  missing?: string[];
}

export interface VerdictView {
  band: ColourBand;
  letter: GradeLetter | null;
  /** Two or three words. The colour has already answered. */
  verdict: string;
  /** Server-authoritative purchase action; absent for non-decision outcomes. */
  decisionAction: string | null;
  /** Deterministic, customer-facing explanation of the action. */
  primaryReason: string;
  /** Line 1: what to do, under ten words. */
  action: string;
  /** Line 2: one number, in something you can picture. */
  everydayNumber: string;
  /** Line 3: a better one, or nothing. */
  alternativeLine: string | null;
  spoken: string;
}

export const bandFor = (grade: GradeLetter | null): ColourBand => {
  if (grade === 'A' || grade === 'B') return 'green';
  if (grade === 'C') return 'yellow';
  return 'red';
};

export const rupees = (paise: number): string => {
  const whole = Math.round(paise / 100);
  return whole.toLocaleString('en-IN');
};

/**
 * The one number worth converting, chosen by what actually stands out.
 *
 * Only one is shown. A row of four converted numbers is the dashboard this
 * screen exists to replace.
 */
export function everydayNumber(source: VerdictSource): string {
  // The declared label fact, stated with the basis it was measured on.
  //
  // The familiar-unit wordings ("6 spoons of sugar in one packet") are not
  // here and are not importable from here: they live in
  // src/strings/quarantine/familiarUnits.ts with the conditions they would
  // have to meet first. Every one of them claims something about a packet,
  // and this screen is reading a per-100 g panel.
  if (typeof source.totalSugarG === 'number') return `${source.totalSugarG} g total sugar per 100 g`;
  if (typeof source.saltG === 'number') return `${source.saltG} g salt per 100 g`;
  if (typeof source.totalFatG === 'number') return `${source.totalFatG} g total fat per 100 g`;
  if (typeof source.proteinG === 'number') return `${source.proteinG} g protein per 100 g`;
  return S.primary.noEverydayNumber;
}

const ACTION_BY_GRADE: Record<GradeLetter, string> = {
  A: S.primary.actionA,
  B: S.primary.actionB,
  C: S.primary.actionC,
  D: S.primary.actionD,
  E: S.primary.actionE,
};

const ACTION_BY_DECISION: Record<Exclude<NonNullable<VerdictSource['decision']>['action'], null>, string> = {
  buy: S.primary.decisionBuy,
  wait: S.primary.decisionWait,
  skip: S.primary.decisionSkip,
};

function primaryReasonFor(source: VerdictSource): string {
  const key = source.decision?.reasonKey;
  if (key === 'sugar') return S.primary.reasonSugar;
  if (key === 'salt' || key === 'sodium') return S.primary.reasonSalt;
  if (key === 'processing') return S.primary.reasonProcessing;
  if (key?.startsWith('additive:')) return S.primary.reasonAdditive;
  if (key === 'naming') return S.primary.reasonNaming;
  return S.primary.reasonLabelFacts;
}

export function buildVerdict(source: VerdictSource): VerdictView {
  if (source.outcome === 'not_graded') {
    return {
      band: 'yellow',
      letter: null,
      verdict: S.notGraded.title,
      decisionAction: null,
      primaryReason: S.primary.actionNotGraded,
      action: S.primary.actionNotGraded,
      everydayNumber: source.quantityGuidance || '',
      alternativeLine: null,
      spoken: t(S.voice.notGraded, { body: S.notGraded.body }),
    };
  }
  if (source.outcome === 'not_enough_information' || !source.grade) {
    return {
      band: 'yellow',
      letter: null,
      verdict: S.unknown.title,
      decisionAction: null,
      primaryReason: S.primary.actionUnknown,
      action: S.primary.actionUnknown,
      everydayNumber: '',
      alternativeLine: null,
      spoken: S.voice.unknown,
    };
  }

  const letter = source.grade;
  const meta = S.grade[letter];
  // A deterministic backend decision is authoritative. Grade mapping only
  // supports legacy sources that predate Product Result Contract V1.
  const decisionAction = source.decision?.action
    ? ACTION_BY_DECISION[source.decision.action]
    : ACTION_BY_GRADE[letter];
  const primaryReason = primaryReasonFor(source);
  const number = everydayNumber(source);
  const alternative = source.alternative
    ? t(S.primary.alternative, {
        name: source.alternative.name,
        price: rupees(source.alternative.pricePaise),
        grade: source.alternative.grade,
      })
    : null;

  const spoken = source.alternative
    ? t(S.voice.withAlternative, {
        verdict: meta.verdict, action: primaryReason, number,
        alternative: source.alternative.name,
        price: rupees(source.alternative.pricePaise),
        grade: source.alternative.grade,
      })
    : t(S.voice.graded, { verdict: meta.verdict, action: primaryReason, number });

  return {
    band: meta.band as ColourBand,
    letter,
    verdict: meta.verdict,
    decisionAction,
    primaryReason,
    action: primaryReason,
    everydayNumber: number,
    alternativeLine: alternative,
    spoken,
  };
}

/** Words that must never reach the primary screen. */
export const TECHNICAL_TERMS = [
  'nova', 'saturated', 'additive', 'ins ', 'emulsifier', 'maltodextrin',
  'per 100', 'sodium', 'kcal', 'nutri-score', 'nutriscore', 'index',
  'ultra-processed', 'ultraprocessed', 'humectant', 'stabiliser', 'stabilizer',
] as const;
