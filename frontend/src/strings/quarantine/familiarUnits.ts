/**
 * QUARANTINE — not production copy, and not safe to reconnect as-is.
 *
 * These are the familiar-unit conversions the verdict screen used to show:
 * "6 spoons of sugar in one packet", pinches of salt, spoons of oil, bowls of
 * dal. They were removed because every one of them makes a claim about a
 * *packet*, and the screen was computing them from a per-100 g panel. For a
 * 20 g sachet the sentence overstated the amount several times over; for a
 * 1 kg pack it understated it by an order of magnitude.
 *
 * They live here, outside `src/strings/`, so that no production string table
 * carries them and no refactor picks them up by autocomplete. Nothing imports
 * this file, and nothing should until all of the following are true:
 *
 *   1. The verdict payload carries a real pack quantity read off the label
 *      (`pack_size_g` in the grading module supplies one where the pack states
 *      a net quantity — it is absent when the pack does not).
 *   2. The wording is chosen per product basis: a drink is per 100 ml, and
 *      "one packet" is wrong for it in a second way.
 *   3. The conversion factors below have been through the evidence lifecycle
 *      rather than remaining the round numbers somebody typed. A teaspoon is
 *      not exactly 5 g of every sugar, and a katori is not a defined unit.
 *
 * Until then the screen shows the declared label fact, which is true without
 * qualification.
 */

/** One teaspoon of sugar, in grams. Unreviewed. */
export const QUARANTINED_SUGAR_G_PER_SPOON = 5;
/** A pinch of salt, in grams. Unreviewed, and the least defensible of these. */
export const QUARANTINED_SALT_G_PER_PINCH = 0.4;
/** One tablespoon of oil, in grams. Unreviewed. */
export const QUARANTINED_OIL_G_PER_SPOON = 14;
/** Protein in one katori of cooked dal, in grams. Unreviewed. */
export const QUARANTINED_PROTEIN_G_PER_BOWL = 6;

/** The wordings, kept only so the rework does not start from a blank page. */
export const QUARANTINED_STRINGS = {
  sugarSpoons: '{spoons} spoons of sugar in one packet',
  sugarSpoonsOne: '1 spoon of sugar in one packet',
  saltPinches: '{pinches} pinches of salt in one packet',
  saltPinchesOne: '1 pinch of salt in one packet',
  oilSpoons: '{spoons} spoons of oil in one packet',
  oilSpoonsOne: '1 spoon of oil in one packet',
  proteinBowls: 'Protein of about {bowls} bowls of dal',
  sugarSpoonsPer100: '{spoons} spoons of sugar in every 100 g',
  sugarSpoonsOnePer100: '1 spoon of sugar in every 100 g',
  saltPinchesPer100: '{pinches} pinches of salt in every 100 g',
  saltPinchesOnePer100: '1 pinch of salt in every 100 g',
  oilSpoonsPer100: '{spoons} spoons of oil in every 100 g',
  oilSpoonsOnePer100: '1 spoon of oil in every 100 g',
  sugarSpoonsPer100Ml: '{spoons} spoons of sugar in every 100 ml',
  sugarSpoonsOnePer100Ml: '1 spoon of sugar in every 100 ml',
  saltPinchesPer100Ml: '{pinches} pinches of salt in every 100 ml',
  saltPinchesOnePer100Ml: '1 pinch of salt in every 100 ml',
  oilSpoonsPer100Ml: '{spoons} spoons of oil in every 100 ml',
  oilSpoonsOnePer100Ml: '1 spoon of oil in every 100 ml',
} as const;
