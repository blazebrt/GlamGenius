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

/** One teaspoon of sugar, in grams. The conversion everyone already knows. */
export const SUGAR_G_PER_SPOON = 5;
/** A pinch of salt, in grams. Used because "grams of salt" means nothing. */
export const SALT_G_PER_PINCH = 0.4;
/** One tablespoon of oil, in grams. */
export const OIL_G_PER_SPOON = 14;
/** Protein in one katori of cooked dal, in grams. */
export const PROTEIN_G_PER_BOWL = 6;

export interface VerdictComponent {
  key: 'processing' | 'nutrients' | 'additives' | 'naming';
  label: string;
  plain: string;
  band: ColourBand;
  rule: string;
  source: string;
  /** A technical word and the words that explain it, shown side by side. */
  term?: { word: string; plain: string };
}

export interface VerdictIngredient {
  name: string;
  tier: 'plain' | 'green' | 'amber' | 'red' | 'black';
  tierLabel: string;
  description: string;
}

export interface Alternative {
  name: string;
  pricePaise: number;
  grade: GradeLetter;
}

export interface VerdictSource {
  outcome: Outcome;
  grade: GradeLetter | null;
  productName: string;
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
  // Only a stated net quantity makes "one packet" a real quantity. Without
  // one the number stays what the panel actually says — per 100 g or 100 ml —
  // rather than being presented as a pack it was never measured against.
  const known = !!source.packSizeG && source.packSizeG > 0;
  const pack = known ? (source.packSizeG as number) : 100;
  const per = (per100: number) => (per100 * pack) / 100;
  const drink = source.basis === 'drink';

  const wording = {
    sugar: known ? S.primary.sugarSpoons
      : drink ? S.primary.sugarSpoonsPer100Ml : S.primary.sugarSpoonsPer100,
    sugarOne: known ? S.primary.sugarSpoonsOne
      : drink ? S.primary.sugarSpoonsOnePer100Ml : S.primary.sugarSpoonsOnePer100,
    salt: known ? S.primary.saltPinches
      : drink ? S.primary.saltPinchesPer100Ml : S.primary.saltPinchesPer100,
    saltOne: known ? S.primary.saltPinchesOne
      : drink ? S.primary.saltPinchesOnePer100Ml : S.primary.saltPinchesOnePer100,
    oil: known ? S.primary.oilSpoons
      : drink ? S.primary.oilSpoonsPer100Ml : S.primary.oilSpoonsPer100,
    oilOne: known ? S.primary.oilSpoonsOne
      : drink ? S.primary.oilSpoonsOnePer100Ml : S.primary.oilSpoonsOnePer100,
  };

  const sugar = source.totalSugarG ?? 0;
  const salt = source.saltG ?? 0;
  const fat = source.totalFatG ?? 0;
  const protein = source.proteinG ?? 0;

  const spoonsOfSugar = Math.round(per(sugar) / SUGAR_G_PER_SPOON);
  const pinchesOfSalt = Math.round(per(salt) / SALT_G_PER_PINCH);
  const spoonsOfOil = Math.round(per(fat) / OIL_G_PER_SPOON);
  const bowlsOfDal = Math.round(per(protein) / PROTEIN_G_PER_BOWL);

  // Ordered by what a person would want to know first about a poor product,
  // and only shown when the number is big enough to be worth a sentence.
  if (spoonsOfSugar >= 2) {
    return t(spoonsOfSugar === 1 ? wording.sugarOne : wording.sugar,
      { spoons: spoonsOfSugar });
  }
  if (pinchesOfSalt >= 4) {
    return t(pinchesOfSalt === 1 ? wording.saltOne : wording.salt,
      { pinches: pinchesOfSalt });
  }
  if (spoonsOfOil >= 2) {
    return t(spoonsOfOil === 1 ? wording.oilOne : wording.oil,
      { spoons: spoonsOfOil });
  }
  // For a good product the number worth showing is the good one.
  if (bowlsOfDal >= 2) {
    return t(S.primary.proteinBowls, { bowls: bowlsOfDal });
  }
  return S.primary.noEverydayNumber;
}

const ACTION_BY_GRADE: Record<GradeLetter, string> = {
  A: S.primary.actionA,
  B: S.primary.actionB,
  C: S.primary.actionC,
  D: S.primary.actionD,
  E: S.primary.actionE,
};

export function buildVerdict(source: VerdictSource): VerdictView {
  if (source.outcome === 'not_graded') {
    return {
      band: 'yellow',
      letter: null,
      verdict: S.notGraded.title,
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
      action: S.primary.actionUnknown,
      everydayNumber: '',
      alternativeLine: null,
      spoken: S.voice.unknown,
    };
  }

  const letter = source.grade;
  const meta = S.grade[letter];
  const action = ACTION_BY_GRADE[letter];
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
        verdict: meta.verdict, action, number,
        alternative: source.alternative.name,
        price: rupees(source.alternative.pricePaise),
        grade: source.alternative.grade,
      })
    : t(S.voice.graded, { verdict: meta.verdict, action, number });

  return {
    band: meta.band as ColourBand,
    letter,
    verdict: meta.verdict,
    action,
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
