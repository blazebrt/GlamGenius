import React from 'react';
import { StyleSheet, Text, View } from 'react-native';


import { PURCHASE_MEMORY } from '../../strings/purchaseMemory';
import { COLORS, FONTS, RADIUS, SPACING } from '../../theme/colors';

export type PurchaseGuardState = 'exact_prior_bought' | 'exact_prior_waiting' | 'exact_prior_skipped' | 'exact_prior_consideration' | 'historical_context_incomplete' | 'identity_insufficient' | 'no_step9a_prior_event';

const messageFor = (state: PurchaseGuardState): string | null => {
  switch (state) {
    case 'exact_prior_bought': return PURCHASE_MEMORY.bought;
    case 'exact_prior_waiting': return PURCHASE_MEMORY.waiting;
    case 'exact_prior_skipped': return PURCHASE_MEMORY.skipped;
    case 'exact_prior_consideration': return PURCHASE_MEMORY.considered;
    case 'historical_context_incomplete': return PURCHASE_MEMORY.incomplete;
    case 'identity_insufficient': return PURCHASE_MEMORY.insufficient;
    default: return null;
  }
};

const displayDate = (value: string | null): string | null => {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toLocaleDateString('en-IN', {
    day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC',
  });
};

export interface PurchaseMemoryProps {
  guardState: 'exact_prior_bought' | 'exact_prior_waiting' | 'exact_prior_skipped' | 'exact_prior_consideration' | 'historical_context_incomplete' | 'identity_insufficient' | 'no_step9a_prior_event' | null;
  occurredAt: string | null;
  considerationCount: number;
}

export function PurchaseMemoryCard({ guardState, occurredAt, considerationCount }: PurchaseMemoryProps) {
  if (!guardState || guardState === "no_step9a_prior_event") return null;
  const message = messageFor(guardState);
  if (!message) return null;
  const date = displayDate(occurredAt);
  return (
    <View style={styles.card} accessibilityLabel={`Purchase memory. ${message}`}>
      <Text style={styles.title}>{PURCHASE_MEMORY.title}</Text>
      <Text style={styles.body}>{message}</Text>
      {considerationCount > 1 && <Text style={styles.note}>{PURCHASE_MEMORY.count(considerationCount)}</Text>}
      {!!date && <Text style={styles.note}>{PURCHASE_MEMORY.lastDecision(date)}</Text>}
    </View>
  );
}

const styles = StyleSheet.create({
  card: { backgroundColor: COLORS.card, borderRadius: RADIUS.xl, padding: SPACING.lg, borderWidth: 1, borderColor: COLORS.border, marginBottom: SPACING.md },
  title: { fontFamily: FONTS.family.headingMedium, color: COLORS.textPrimary, fontSize: 16 },
  body: { fontFamily: FONTS.family.body, color: COLORS.textSecondary, fontSize: 13, lineHeight: 19, marginTop: 5 },
  note: { fontFamily: FONTS.family.body, color: COLORS.textMuted, fontSize: 11, lineHeight: 16, marginTop: 5 },
});
