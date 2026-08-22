import React from 'react';
import { StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';

import { EventReadyAction, EventReadyCare } from '../../services/apiV2';
import { COLORS, FONTS, RADIUS, SPACING } from '../../theme/colors';

export function EventReadyActionRow({ action, busy, onToggle }: {
  action: EventReadyAction; busy?: boolean; onToggle: () => void;
}) {
  return (
    <TouchableOpacity
      accessibilityRole="button"
      accessibilityLabel={`${action.completed ? 'Undo' : 'Complete'} ${action.title}`}
      disabled={busy}
      onPress={onToggle}
      style={[styles.action, busy && styles.disabled]}
    >
      <Ionicons name={action.completed ? 'checkmark-circle' : 'ellipse-outline'} size={22} color={action.completed ? COLORS.success : COLORS.primary} />
      <View style={styles.actionCopy}>
        <Text style={styles.actionTitle}>{action.title}</Text>
        <Text style={styles.actionBody}>{action.body}</Text>
      </View>
    </TouchableOpacity>
  );
}

export function CareSummary({ care, onOpen }: { care: EventReadyCare; onOpen?: () => void }) {
  const cadence = care.hair_wash.status;
  const message = cadence === 'due'
    ? 'Hair Care is due before this event.'
    : cadence === 'needs_anchor' || cadence === 'unscheduled'
      ? 'Hair wash timing needs one more detail.'
      : 'No extra Hair wash is due.';
  return (
    <View style={styles.panel} accessibilityLabel="Context and Care">
      <Text style={styles.panelEyebrow}>CONTEXT / CARE</Text>
      <Text style={styles.panelTitle}>{message}</Text>
      <Text style={styles.panelBody}>Your usual Care plan stays in place.</Text>
      {!!onOpen && <TouchableOpacity accessibilityRole="button" accessibilityLabel="Open Care" onPress={onOpen}><Text style={styles.link}>Open Care</Text></TouchableOpacity>}
    </View>
  );
}

export function MissingInformation({ keys }: { keys: string[] }) {
  const labels: Record<string, string> = {
    event_confirmation: 'Confirm what the event is.',
    event_day_weather: 'Event-day weather is not available yet.',
    hair_wash_cadence: 'Hair wash timing needs one more detail.',
  };
  if (!keys.length) return null;
  return (
    <View style={styles.missing} accessibilityLabel="Missing information">
      {keys.map((key) => <Text key={key} style={styles.missingText}>• {labels[key] || 'One event detail is still missing.'}</Text>)}
    </View>
  );
}

const styles = StyleSheet.create({
  action: { flexDirection: 'row', alignItems: 'flex-start', backgroundColor: COLORS.card, borderWidth: 1, borderColor: COLORS.border, borderRadius: RADIUS.lg, padding: SPACING.md, marginBottom: 8 },
  actionCopy: { flex: 1, marginLeft: 10 },
  actionTitle: { fontFamily: FONTS.family.bodySemibold, fontSize: 14, color: COLORS.textPrimary },
  actionBody: { fontFamily: FONTS.family.body, fontSize: 12, lineHeight: 18, color: COLORS.textSecondary, marginTop: 4 },
  disabled: { opacity: 0.55 },
  panel: { backgroundColor: COLORS.card, borderWidth: 1, borderColor: COLORS.border, borderRadius: RADIUS.lg, padding: SPACING.md, marginTop: SPACING.md },
  panelEyebrow: { fontFamily: FONTS.family.bodySemibold, fontSize: 10, letterSpacing: 1.1, color: COLORS.primary },
  panelTitle: { fontFamily: FONTS.family.headingMedium, fontSize: 17, color: COLORS.textPrimary, marginTop: 5 },
  panelBody: { fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.textSecondary, marginTop: 4 },
  link: { fontFamily: FONTS.family.bodySemibold, fontSize: 12, color: COLORS.primary, marginTop: 10 },
  missing: { backgroundColor: COLORS.backgroundSecondary, borderRadius: RADIUS.md, padding: SPACING.sm, marginTop: SPACING.md },
  missingText: { fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.textMuted, lineHeight: 18 },
});
