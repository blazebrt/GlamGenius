/**
 * The routine strip on Today.
 *
 * Deliberately small. Today belongs to the outfit; this sits underneath and
 * shows only what is due right now — the server already decided that from the
 * time of day, so the client does not second-guess it.
 *
 * Renders nothing at all when there is nothing due. An empty routine card on
 * Today every morning is how people learn to scroll past a screen.
 */
import React from 'react';
import { StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';

import { CareGuidance, NutritionSuggestion, PerfumePick, Routine, RoutineStep } from '../../services/apiV2';
import { COLORS, FONTS, RADIUS } from '../../theme/colors';

export function TodayRoutineCard({ routine, onComplete, onOpen }: {
  routine: Routine;
  onComplete?: (step: RoutineStep) => void;
  onOpen?: () => void;
}) {
  const actionable = routine.steps.filter((step) => !step.is_gap);
  if (!actionable.length) return null;
  const done = actionable.filter((step) => step.completed_today).length;

  return (
    <View style={styles.card} accessibilityLabel={`${routine.label}, ${done} of ${actionable.length} done`}>
      <TouchableOpacity
        accessibilityRole="button"
        accessibilityLabel={`Open ${routine.label}`}
        onPress={onOpen}
        style={styles.head}
      >
        <View style={{ flex: 1 }}>
          <Text style={styles.title}>{routine.label}</Text>
          <Text style={styles.progress}>{done} of {actionable.length} done</Text>
        </View>
        <Ionicons name="chevron-forward" size={18} color={COLORS.textMuted} />
      </TouchableOpacity>

      {actionable.slice(0, 4).map((step) => (
        <TouchableOpacity
          key={step.id ?? step.slot}
          accessibilityRole="checkbox"
          accessibilityState={{ checked: step.completed_today === true }}
          accessibilityLabel={`${step.completed_today ? 'Undo' : 'Mark done'}: ${step.label}, ${step.product_name ?? ''}`}
          onPress={() => onComplete?.(step)}
          style={styles.step}
        >
          <Ionicons
            name={step.completed_today ? 'checkmark-circle' : 'ellipse-outline'}
            size={19}
            color={step.completed_today ? COLORS.primary : COLORS.textMuted}
          />
          <View style={{ flex: 1 }}>
            <Text style={styles.stepLabel}>{step.label}</Text>
            {!!step.product_name && <Text style={styles.stepProduct}>{step.product_name}</Text>}
          </View>
        </TouchableOpacity>
      ))}
    </View>
  );
}

export function TodayPerfume({ pick }: { pick: PerfumePick | null }) {
  if (!pick) return null;
  const reason = pick.reasons[0]?.note;
  return (
    <View style={styles.slim} accessibilityLabel={`Perfume today: ${pick.display_name}`}>
      <Ionicons name="sparkles-outline" size={17} color={COLORS.primary} />
      <View style={{ flex: 1 }}>
        <Text style={styles.slimTitle}>{pick.display_name}</Text>
        {!!reason && <Text style={styles.slimBody}>{reason}</Text>}
      </View>
    </View>
  );
}

export function TodayFood({ suggestion }: { suggestion: NutritionSuggestion | null }) {
  if (!suggestion || !suggestion.foods.length) return null;
  return (
    <View style={styles.slim} accessibilityLabel={`Food idea: ${suggestion.display_name}`}>
      <Ionicons name="nutrition-outline" size={17} color={COLORS.primary} />
      <View style={{ flex: 1 }}>
        <Text style={styles.slimTitle}>{suggestion.display_name}</Text>
        <Text style={styles.slimBody}>{suggestion.foods.slice(0, 3).join(' · ')}</Text>
      </View>
    </View>
  );
}

export function TodayCareGuidance({ guidance }: { guidance: CareGuidance | null }) {
  const items = guidance?.items?.slice(0, 3) ?? [];
  if (!items.length) return null;
  return (
    <View accessibilityLabel="Care context" style={styles.guidance}>
      <Text style={styles.guidanceLabel}>CARE CONTEXT</Text>
      {items.map((item) => (
        <View key={item.rule_id} style={styles.guidanceItem}>
          <Text style={styles.slimTitle}>{item.title}</Text>
          <Text style={styles.slimBody}>{item.body}</Text>
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  card: { backgroundColor: COLORS.card, borderRadius: RADIUS.lg, borderWidth: 1, borderColor: COLORS.border, padding: 13, marginTop: 10 },
  head: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  title: { fontFamily: FONTS.family.headingMedium, color: COLORS.textPrimary, fontSize: 16 },
  progress: { fontFamily: FONTS.family.body, color: COLORS.textMuted, fontSize: 11, marginTop: 1 },
  step: { flexDirection: 'row', alignItems: 'center', gap: 9, paddingVertical: 8, borderTopWidth: 1, borderTopColor: COLORS.border, marginTop: 8 },
  stepLabel: { fontFamily: FONTS.family.bodyMedium, color: COLORS.textPrimary, fontSize: 12 },
  stepProduct: { fontFamily: FONTS.family.body, color: COLORS.textSecondary, fontSize: 11, marginTop: 1 },
  slim: { flexDirection: 'row', alignItems: 'center', gap: 9, backgroundColor: COLORS.card, borderRadius: RADIUS.lg, borderWidth: 1, borderColor: COLORS.border, padding: 12, marginTop: 8 },
  slimTitle: { fontFamily: FONTS.family.bodySemibold, color: COLORS.textPrimary, fontSize: 12 },
  slimBody: { fontFamily: FONTS.family.body, color: COLORS.textSecondary, fontSize: 11, marginTop: 1 },
  guidance: { backgroundColor: COLORS.card, borderRadius: RADIUS.lg, borderWidth: 1, borderColor: COLORS.border, padding: 12, marginTop: 10 },
  guidanceLabel: { fontFamily: FONTS.family.bodySemibold, color: COLORS.primary, fontSize: 10, letterSpacing: 1.1, marginBottom: 4 },
  guidanceItem: { borderTopWidth: 1, borderTopColor: COLORS.border, paddingTop: 8, marginTop: 8 },
});
