import React from 'react';
import { StyleSheet, Text, TouchableOpacity, View } from 'react-native';

import { DecisionActions } from './ShoppingPieces';
import { FragrancePurchaseCheck } from '../../services/apiV2';
import { COLORS, FONTS, RADIUS, SPACING } from '../../theme/colors';

const verdictColor = (value: string) => value === 'buy' ? COLORS.success : value === 'skip' ? COLORS.warning : COLORS.accent;

export function FragranceCandidateReview({
  inspection, onConfirm, onCorrect,
}: { inspection: FragrancePurchaseCheck['candidate_truth']; onConfirm: () => void; onCorrect: () => void }) {
  const candidate = inspection.candidate;
  return (
    <View style={styles.card} accessibilityLabel="Review Fragrance product facts">
      <Text style={styles.eyebrow}>REVIEW FRAGRANCE FACTS</Text>
      <Text style={styles.title}>{candidate.display_name}</Text>
      {!!candidate.brand && <Text style={styles.body}>{candidate.brand}</Text>}
      {!!candidate.details.fragrance_family && <Text style={styles.fact}>Family: {candidate.details.fragrance_family}</Text>}
      {!!candidate.details.concentration && <Text style={styles.fact}>Concentration: {candidate.details.concentration}</Text>}
      <Text style={styles.note}>Check the visible facts before continuing. This candidate is not in your inventory.</Text>
      {!inspection.facts_trusted && <View style={styles.row}><TouchableOpacity accessibilityRole="button" accessibilityLabel="Confirm Fragrance facts" onPress={onConfirm} style={styles.primary}><Text style={styles.primaryText}>Confirm facts</Text></TouchableOpacity><TouchableOpacity accessibilityRole="button" accessibilityLabel="Correct Fragrance facts" onPress={onCorrect} style={styles.outline}><Text style={styles.outlineText}>Correct</Text></TouchableOpacity></View>}
      {inspection.facts_trusted && <TouchableOpacity accessibilityRole="button" accessibilityLabel="Check Fragrance purchase" onPress={onConfirm} style={styles.primary}><Text style={styles.primaryText}>Check it</Text></TouchableOpacity>}
    </View>
  );
}

export function FragranceShoppingResult({
  check, onReset, onDecide, busy = false,
}: { check: FragrancePurchaseCheck; onReset: () => void; onDecide: (decision: 'bought' | 'waiting' | 'skipped') => void; busy?: boolean }) {
  const verdict = check.verdict;
  const context = check.collection_context;
  return (
    <>
      <View style={[styles.verdict, { backgroundColor: verdictColor(verdict.verdict) + '20' }]} accessibilityLabel={`Fragrance verdict: ${verdict.verdict[0].toUpperCase()}${verdict.verdict.slice(1)}`}>
        <Text style={styles.eyebrow}>FRAGRANCE PURCHASE</Text><Text style={[styles.verdictLabel, { color: verdictColor(verdict.verdict) }]}>{verdict.verdict.toUpperCase()}</Text><Text style={styles.verdictBlurb}>{verdict.headline}</Text><Text style={styles.body}>{verdict.explanation}</Text>
      </View>
      <View style={styles.card} accessibilityLabel="Fragrance intended use">
        <Text style={styles.title}>Where you said you would use it</Text>
        <Text style={styles.fact}>Occasions: {context.intended_use.occasion.join(', ') || 'Not specified'}</Text>
        <Text style={styles.fact}>Seasons: {context.intended_use.season.join(', ') || 'Not specified'}</Text>
        {!!context.coverage.covered.length && <Text style={styles.fact}>Already covered: {context.coverage.covered.join(', ')}</Text>}
        {!!context.coverage.unknown.length && <Text style={styles.note}>Still unclear from owned metadata: {context.coverage.unknown.join(', ')}</Text>}
        {!!context.coverage.uncovered.length && <Text style={styles.fact}>Not yet recorded: {context.coverage.uncovered.join(', ')}</Text>}
      </View>
      {!!context.owned_options_to_use_first.length && <View style={styles.card} accessibilityLabel="Owned fragrance alternatives"><Text style={styles.title}>What you already own</Text>{context.owned_options_to_use_first.map((item) => <Text key={item.owned_item_id} style={styles.fact}>{item.display_name}{item.brand ? ` · ${item.brand}` : ''}{item.remaining_percent != null ? ` · ${item.remaining_percent}% left` : ''}</Text>)}</View>}
      {!!context.same_family_owned.length && <View style={styles.card} accessibilityLabel="Same family supporting information"><Text style={styles.title}>Same-family context</Text><Text style={styles.note}>This is supporting context only; family overlap does not decide the result.</Text>{context.same_family_owned.map((item) => <Text key={item.owned_item_id} style={styles.fact}>{item.display_name}</Text>)}</View>}
      <Text style={styles.noteCenter}>This candidate remains separate from your inventory. Buying it does not add an inventory item.</Text>
      <DecisionActions current={check.decision?.decision} onDecide={onDecide} busy={busy} />
      <TouchableOpacity accessibilityRole="button" accessibilityLabel="Check something else" onPress={onReset}><Text style={styles.link}>Check something else</Text></TouchableOpacity>
    </>
  );
}

const styles = StyleSheet.create({
  card: { backgroundColor: COLORS.card, borderRadius: RADIUS.xl, padding: SPACING.lg, borderWidth: 1, borderColor: COLORS.border, marginBottom: SPACING.md },
  eyebrow: { fontFamily: FONTS.family.bodySemibold, color: COLORS.accent, fontSize: 10, letterSpacing: 1.2, textTransform: 'uppercase' },
  title: { fontFamily: FONTS.family.headingMedium, fontSize: 18, color: COLORS.textPrimary, marginTop: 4 },
  body: { fontFamily: FONTS.family.body, fontSize: 13, lineHeight: 19, color: COLORS.textSecondary, marginTop: 6 },
  fact: { fontFamily: FONTS.family.body, fontSize: 12, lineHeight: 18, color: COLORS.textSecondary, marginTop: 4 },
  note: { fontFamily: FONTS.family.body, fontSize: 11, lineHeight: 16, color: COLORS.textMuted, marginTop: 8 },
  noteCenter: { fontFamily: FONTS.family.body, fontSize: 11, lineHeight: 16, color: COLORS.textMuted, textAlign: 'center', marginVertical: SPACING.sm },
  verdict: { borderRadius: RADIUS.xl, padding: SPACING.lg, marginBottom: SPACING.md },
  verdictLabel: { fontFamily: FONTS.family.heading, fontSize: 28 },
  row: { flexDirection: 'row', gap: 8, marginTop: SPACING.md },
  primary: { alignItems: 'center', justifyContent: 'center', backgroundColor: COLORS.primary, borderRadius: RADIUS.full, paddingHorizontal: 16, paddingVertical: 12, flex: 1 },
  primaryText: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.white },
  outline: { alignItems: 'center', justifyContent: 'center', borderRadius: RADIUS.full, paddingHorizontal: 14, paddingVertical: 11, borderWidth: 1, borderColor: COLORS.border },
  outlineText: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.textPrimary },
  link: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.primary, textAlign: 'center', marginTop: SPACING.md },
});
