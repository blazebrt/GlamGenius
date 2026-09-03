/**
 * What two confirmed pack labels stated as MRP, per 100 g or per 100 ml.
 *
 * The narrowest claim on the screen, and the one most easily overstated. Four
 * things this surface must never do:
 *
 *   - **Call it a price.** MRP is the maximum a pack may legally be sold for,
 *     printed on the pack. We have no evidence at all about what a shop charges
 *     today, so "price", "deal", "what you will pay" are claims we cannot make.
 *   - **Characterise the numbers.** No cheaper, no saving, no better value, no
 *     "worth it". V1 reports arithmetic and stops, because the moment a number
 *     becomes a judgement it needs evidence we do not have.
 *   - **Outrank the science.** It sits inside the quiet Better option card,
 *     below the verdict, the regulator's record and the negatives. No green
 *     winner, no celebratory badge.
 *   - **Hide the pack sizes.** ₹100 looks cheaper than ₹120 right up until the
 *     packs are normalised. Showing only the per-100 figure would let somebody
 *     conclude the opposite of what the arithmetic says, so both are printed.
 *
 * No arithmetic happens here. Every figure arrives from the backend as an exact
 * decimal string; this file formats and nothing more.
 */
import React from 'react';
import { StyleSheet, Text, View } from 'react-native';

import type { MrpComparison as MrpComparisonModel, PackMrpObservation } from '../../services/verdictModel';
import { S, t } from '../../strings/verdict';
import { COLORS, FONTS, SPACING } from '../../theme/colors';

/**
 * "120.00" -> ₹120, "120.50" -> ₹120.50, "1299.00" -> ₹1,299.
 *
 * Deliberately not `toLocaleString` on a parsed number: the string is already
 * exact, and reinterpreting it through a device locale is how a decimal
 * separator becomes a thousands separator on somebody's phone. Only INR exists
 * in V1 and no conversion happens anywhere.
 */
export function formatInr(amount: string): string {
  const negative = amount.trim().startsWith('-');
  const [whole = '0', fraction = ''] = amount.trim().replace(/^-/, '').split('.');
  // Indian digit grouping: the last three digits, then pairs above them.
  const last3 = whole.slice(-3);
  const rest = whole.slice(0, -3);
  const grouped = rest
    ? `${rest.replace(/\B(?=(\d{2})+(?!\d))/g, ',')},${last3}`
    : last3;
  const paise = /^0*$/.test(fraction) ? '' : `.${fraction}`;
  return `${negative ? '-' : ''}₹${grouped}${paise}`;
}

/** "500" + "g" -> "500 g". The unit is a fact, never abbreviated away. */
export function formatQuantity(quantity: PackMrpObservation['quantity']): string {
  return `${quantity.amount} ${quantity.unit}`;
}

/** A confirmed pack is dated. "2 Sep 2026", never "today" and never "current". */
export function formatObservedOn(observedAt: string): string {
  const when = new Date(observedAt);
  if (Number.isNaN(when.getTime())) return observedAt;
  const month = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][when.getUTCMonth()];
  return `${when.getUTCDate()} ${month} ${when.getUTCFullYear()}`;
}

function Side({
  role,
  observation,
  basisLabel,
}: {
  role: string;
  observation: PackMrpObservation;
  basisLabel: string;
}) {
  const mrp = formatInr(observation.mrpInr);
  const quantity = formatQuantity(observation.quantity);
  const perHundred = formatInr(observation.mrpPer100Inr);
  const date = formatObservedOn(observation.observedAt);
  return (
    <View
      style={styles.side}
      accessible
      accessibilityLabel={t(S.mrpComparison.a11y.side, {
        role, mrp, quantity, basis: basisLabel, perHundred, date,
      })}
    >
      <Text style={styles.role}>{role}</Text>
      {/* The pack as it was: absolute MRP and the size it applies to. */}
      <Text style={styles.pack}>{t(S.mrpComparison.packLine, { mrp, quantity })}</Text>
      {/* Then the normalised figure, labelled with the basis it is on. */}
      <Text style={styles.perHundred}>{`${basisLabel} ${perHundred}`}</Text>
      <Text style={styles.observed}>{t(S.mrpComparison.observed, { date })}</Text>
    </View>
  );
}

export function MrpComparison({ value }: { value: MrpComparisonModel | null | undefined }) {
  // A response predating this milestone carries no envelope. Nothing to explain.
  if (!value) return null;

  const comparison = value.status === 'available' ? value.comparison : null;
  if (!comparison) {
    return (
      <View style={styles.container}>
        <Text style={styles.heading} accessibilityRole="header">
          {S.mrpComparison.heading}
        </Text>
        {/* States what we do not have. Never that a price does not exist, and
            never that anywhere was searched — we read two pack labels. */}
        <Text style={styles.missing} accessibilityLabel={S.mrpComparison.a11y.missing}>
          {S.mrpComparison.notEnoughInformation}
        </Text>
      </View>
    );
  }

  const basisLabel = comparison.basis === 'per_100ml'
    ? S.mrpComparison.perBasisVolume
    : S.mrpComparison.perBasisMass;

  return (
    <View style={styles.container}>
      <Text style={styles.heading} accessibilityRole="header">
        {S.mrpComparison.heading}
      </Text>
      <Side
        role={S.mrpComparison.current}
        observation={comparison.current}
        basisLabel={basisLabel}
      />
      <Side
        role={S.mrpComparison.alternative}
        observation={comparison.candidate}
        basisLabel={basisLabel}
      />
      {/* What an MRP is, said plainly, so the numbers above cannot be read as
          a shop price. */}
      <Text style={styles.disclosure}>{S.mrpComparison.disclosure}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  // No card of its own and no colour of its own. Money is the quietest thing
  // on this screen, sitting inside the already-quiet Better option surface.
  container: {
    borderTopColor: COLORS.border, borderTopWidth: 1,
    marginTop: SPACING.md, paddingTop: SPACING.md,
  },
  heading: { color: COLORS.textPrimary, fontFamily: FONTS.family.bodySemibold, fontSize: 14 },
  side: { marginTop: SPACING.sm },
  role: { color: COLORS.textSecondary, fontFamily: FONTS.family.bodyMedium, fontSize: 12 },
  pack: { color: COLORS.textPrimary, fontFamily: FONTS.family.body, fontSize: 14, marginTop: 2 },
  perHundred: {
    color: COLORS.textPrimary, fontFamily: FONTS.family.bodyMedium, fontSize: 14, marginTop: 2,
  },
  observed: { color: COLORS.textMuted, fontFamily: FONTS.family.body, fontSize: 11, marginTop: 2 },
  disclosure: {
    color: COLORS.textMuted, fontFamily: FONTS.family.body, fontSize: 11,
    lineHeight: 16, marginTop: SPACING.sm,
  },
  missing: {
    color: COLORS.textSecondary, fontFamily: FONTS.family.body, fontSize: 13,
    lineHeight: 18, marginTop: SPACING.xs,
  },
});
