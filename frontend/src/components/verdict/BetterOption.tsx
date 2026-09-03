/**
 * The comparable alternative, below every evidence layer and quieter than all
 * of them.
 *
 * What this card is allowed to claim is narrow, and the design follows from it:
 * the source lists another product in the same category, and under the same
 * rules it grades higher. That is two facts and a citation, so the card is two
 * lines and a citation. It does not compete with the verdict above it, with the
 * regulator's record, or with the negatives — none of which it may contradict
 * or soften.
 *
 * What is deliberately absent, and must stay absent:
 *
 *   - price, MRP, a discount, a cart, a retailer, an affiliate link. Money is a
 *     later milestone with its own provenance, and none of it exists here.
 *   - stars, review counts, shopper observations. A count of people is not a
 *     property of the product, and the selection never read one.
 *   - "recommended for you", or anything else implying this was chosen for the
 *     person holding the phone. It was not: the same pack yields the same
 *     alternative for everybody.
 *   - "healthier", "safer", "cleaner", "best". The comparison is between two
 *     grades and the card says exactly that.
 *
 * No string is written here. Every word comes from src/strings/verdict.ts, so
 * the copy can be reviewed against LEGAL_RULES.md without reading React.
 */
import React from 'react';
import { StyleSheet, Text, TouchableOpacity, View } from 'react-native';

import type { ComparableAlternative } from '../../services/verdictModel';
import { S, t } from '../../strings/verdict';
import { COLORS, FONTS, RADIUS, SPACING } from '../../theme/colors';
import { OpenFoodFactsAttribution } from '../common/OpenFoodFactsAttribution';

/**
 * The route parameter that marks a verdict screen as a reference view.
 *
 * Lives beside the card that produces it, because the card is the only thing
 * that navigates this way. Reading about a product is not holding it: the
 * screen this opens asks the server to withhold every physical-pack layer.
 */
export const REFERENCE_ALTERNATIVE = 'alternative';

export function BetterOption({
  alternative,
  onView,
}: {
  alternative: ComparableAlternative | null | undefined;
  onView?: (barcode: string) => void;
}) {
  // A response that predates this milestone carries no envelope at all. That is
  // not a missing state to explain to anybody — it is simply nothing to render.
  if (!alternative) return null;

  const candidate = alternative.candidate;
  // A recommendation has to name something. The server already refuses a
  // nameless candidate; this is the second lock, because the failure it guards
  // against is publishing "Better option: 8901000000002" to a shopper, and a
  // barcode is an identifier rather than a name.
  const named = candidate?.productName?.trim() || '';
  if (alternative.status !== 'available' || !candidate || !named) {
    // Rule 5: state the absence, never fill it. This says what we do not know.
    // It does not say nothing better exists — our cached data is not the market.
    return (
      <View style={styles.container}>
        <Text style={styles.heading} accessibilityRole="header">
          {S.betterOption.heading}
        </Text>
        <Text style={styles.missing} accessibilityLabel={S.betterOption.a11y.missing}>
          {S.betterOption.notEnoughInformation}
        </Text>
      </View>
    );
  }

  const name = named;
  const comparison = t(S.betterOption.comparison, {
    candidate: candidate.comparison.candidateGrade,
    current: candidate.comparison.currentGrade,
  });

  return (
    <View style={styles.container}>
      <Text style={styles.heading} accessibilityRole="header">
        {S.betterOption.heading}
      </Text>
      <View
        accessible
        accessibilityLabel={t(S.betterOption.a11y.card, {
          name,
          candidate: candidate.comparison.candidateGrade,
          current: candidate.comparison.currentGrade,
        })}
      >
        <Text style={styles.name}>{name}</Text>
        {/* Absent rather than invented when the source carries no brand. */}
        {!!candidate.brand?.trim() && <Text style={styles.brand}>{candidate.brand.trim()}</Text>}
        {/*
          Both letters in the text, not only in the colour. A grade communicated
          by a coloured chip alone is unreadable to anybody who cannot see it.
        */}
        <Text style={styles.comparison}>{comparison}</Text>
        <Text style={styles.context}>{S.betterOption.sameCategory}</Text>
        {/* What the source says, which is not a claim about any shop today. */}
        <Text style={styles.context}>{S.betterOption.availability}</Text>
      </View>
      {!!onView && (
        <TouchableOpacity
          accessibilityRole="button"
          accessibilityLabel={t(S.betterOption.a11y.view, { name })}
          onPress={() => onView(candidate.barcode)}
        >
          <Text style={styles.action}>{S.betterOption.viewAction}</Text>
        </TouchableOpacity>
      )}
      {/*
        A licence condition, not a footer. The candidate's name, brand and
        category are Open Food Facts data, and the notice renders with them
        wherever they appear — including inside a card of ours.
      */}
      <OpenFoodFactsAttribution />
    </View>
  );
}

const styles = StyleSheet.create({
  // The same quiet card as the rest of the lower screen. Nothing here earns
  // the emphasis the verdict block or an official record has.
  container: {
    backgroundColor: COLORS.card, borderColor: COLORS.border, borderRadius: RADIUS.md,
    borderWidth: 1, marginTop: SPACING.md, padding: SPACING.md,
  },
  heading: { color: COLORS.textPrimary, fontFamily: FONTS.family.heading, fontSize: 17 },
  name: {
    color: COLORS.textPrimary, fontFamily: FONTS.family.bodyMedium, fontSize: 15,
    marginTop: SPACING.xs,
  },
  brand: { color: COLORS.textSecondary, fontFamily: FONTS.family.body, fontSize: 13, marginTop: 2 },
  comparison: {
    color: COLORS.textPrimary, fontFamily: FONTS.family.body, fontSize: 14,
    lineHeight: 20, marginTop: SPACING.xs,
  },
  context: { color: COLORS.textMuted, fontFamily: FONTS.family.body, fontSize: 12, marginTop: 2 },
  missing: {
    color: COLORS.textSecondary, fontFamily: FONTS.family.body, fontSize: 13,
    lineHeight: 18, marginTop: SPACING.xs,
  },
  action: {
    color: COLORS.primary, fontFamily: FONTS.family.bodySemibold, fontSize: 14,
    marginTop: SPACING.sm,
  },
});
