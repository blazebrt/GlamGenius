import React from 'react';
import { StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { COLORS, FONTS, RADIUS, SPACING } from '../../theme/colors';

export const GOAL_OPTIONS = [
  'Everyday confidence', 'Work', 'Wedding or festival', 'Interview',
  'Date or social event', 'Travel', 'Improve my wardrobe', 'Improve my routines',
];

export const validateGoal = (goal: string): string | null =>
  GOAL_OPTIONS.includes(goal) ? null : 'Choose what you are preparing for.';

export function GoalChooser({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  return (
    <View accessibilityLabel="What are you preparing for options">
      {GOAL_OPTIONS.map((goal) => (
        <TouchableOpacity
          accessibilityRole="radio"
          accessibilityLabel={goal}
          accessibilityState={{ selected: value === goal }}
          key={goal}
          onPress={() => onChange(goal)}
          style={[styles.option, value === goal && styles.optionSelected]}
        >
          <Text style={[styles.optionText, value === goal && styles.optionTextSelected]}>{goal}</Text>
        </TouchableOpacity>
      ))}
    </View>
  );
}

export function OnboardingRecovery({ onRetry, onHome }: { onRetry: () => void; onHome: () => void }) {
  return (
    <View style={styles.recovery} accessibilityRole="alert">
      <Text style={styles.recoveryTitle}>Your progress is safe</Text>
      <Text style={styles.recoveryText}>We could not load it just now. Check your connection and try again.</Text>
      <TouchableOpacity accessibilityRole="button" accessibilityLabel="Retry onboarding" onPress={onRetry} style={styles.retry}><Text style={styles.retryText}>Try again</Text></TouchableOpacity>
      <TouchableOpacity accessibilityRole="button" accessibilityLabel="Return home" onPress={onHome}><Text style={styles.homeText}>Return home</Text></TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  option: { backgroundColor: COLORS.card, borderWidth: 1, borderColor: COLORS.border, borderRadius: RADIUS.lg, padding: SPACING.md, marginBottom: 10 },
  optionSelected: { backgroundColor: COLORS.primaryLight, borderColor: COLORS.primary },
  optionText: { fontFamily: FONTS.family.bodyMedium, color: COLORS.textPrimary, fontSize: 15 },
  optionTextSelected: { color: COLORS.primary, fontFamily: FONTS.family.bodySemibold },
  recovery: { padding: SPACING.lg, alignItems: 'center' },
  recoveryTitle: { fontFamily: FONTS.family.heading, fontSize: 24, color: COLORS.textPrimary },
  recoveryText: { fontFamily: FONTS.family.body, color: COLORS.textSecondary, textAlign: 'center', marginTop: 8, lineHeight: 21 },
  retry: { backgroundColor: COLORS.primary, paddingHorizontal: 24, paddingVertical: 12, borderRadius: RADIUS.full, marginTop: 20 },
  retryText: { color: COLORS.white, fontFamily: FONTS.family.bodySemibold },
  homeText: { color: COLORS.primary, fontFamily: FONTS.family.bodyMedium, marginTop: 16 },
});
