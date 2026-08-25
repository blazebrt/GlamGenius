/**
 * Maintenance timing pieces (VC-06).
 *
 * These render *timing* and nothing else. There is no place to book, no price,
 * and no judgement about how anyone looks — a kind is only ever shown once the
 * customer has chosen to track it, on an interval they control.
 */
import React from 'react';
import { StyleSheet, Text, TouchableOpacity, View } from 'react-native';

import { MaintenanceKindStatus } from '../../services/apiV2';
import { COLORS, FONTS, RADIUS, SPACING } from '../../theme/colors';

export function statusLabel(kind: MaintenanceKindStatus): string {
  switch (kind.status) {
    case 'due':
      return 'Due now';
    case 'coming_up':
      return kind.days_until_due === 1 ? 'Due tomorrow' : `Due in ${kind.days_until_due} days`;
    case 'not_due':
      return kind.days_until_due === 1 ? 'Due tomorrow' : `Due in ${kind.days_until_due} days`;
    case 'needs_anchor':
      return 'Add your last date';
    default:
      return 'Not tracked';
  }
}

export function MaintenanceRow({
  kind, busy, onTrack, onUntrack, onRecordToday,
}: {
  kind: MaintenanceKindStatus;
  busy?: boolean;
  onTrack: () => void;
  onUntrack: () => void;
  onRecordToday: () => void;
}) {
  const tracked = kind.tracked;
  return (
    <View style={styles.row} accessibilityLabel={kind.label}>
      <View style={styles.rowText}>
        <Text style={styles.rowTitle}>{kind.label}</Text>
        <Text style={styles.rowBody}>{kind.description}</Text>
        {tracked && (
          <Text style={kind.status === 'due' ? styles.rowDue : styles.rowMeta}>
            {statusLabel(kind)}
            {kind.interval_is_custom ? ` · every ${kind.interval_days} days` : ''}
          </Text>
        )}
      </View>
      <View style={styles.rowActions}>
        {tracked ? (
          <>
            <TouchableOpacity
              accessibilityRole="button"
              accessibilityLabel={`Record ${kind.label} today`}
              onPress={onRecordToday}
              disabled={busy}
            >
              <Text style={styles.primaryLink}>Done today</Text>
            </TouchableOpacity>
            <TouchableOpacity
              accessibilityRole="button"
              accessibilityLabel={`Stop tracking ${kind.label}`}
              onPress={onUntrack}
              disabled={busy}
            >
              <Text style={styles.quietLink}>Stop tracking</Text>
            </TouchableOpacity>
          </>
        ) : (
          <TouchableOpacity
            accessibilityRole="button"
            accessibilityLabel={`Track ${kind.label}`}
            onPress={onTrack}
            disabled={busy}
            style={styles.trackButton}
          >
            <Text style={styles.trackText}>Track</Text>
          </TouchableOpacity>
        )}
      </View>
    </View>
  );
}

export function MaintenanceEmpty() {
  return (
    <View style={styles.empty}>
      <Text style={styles.emptyTitle}>Nothing tracked yet</Text>
      <Text style={styles.emptyBody}>
        Pick the upkeep you already do and GlamGenius will keep track of the timing,
        on whatever rhythm you choose.
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: SPACING.md,
    paddingVertical: SPACING.md,
    borderBottomWidth: 1,
    borderBottomColor: COLORS.border,
  },
  rowText: { flex: 1 },
  rowTitle: { fontFamily: FONTS.family.bodySemibold, fontSize: 16, color: COLORS.textPrimary },
  rowBody: { fontFamily: FONTS.family.body, fontSize: 13, color: COLORS.textSecondary, marginTop: 2, lineHeight: 19 },
  rowMeta: { fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.textSecondary, marginTop: 6 },
  rowDue: { fontFamily: FONTS.family.bodySemibold, fontSize: 12, color: COLORS.primary, marginTop: 6 },
  rowActions: { alignItems: 'flex-end', gap: 8 },
  primaryLink: { fontFamily: FONTS.family.bodySemibold, fontSize: 12, color: COLORS.primary },
  quietLink: { fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.textSecondary },
  trackButton: {
    borderRadius: RADIUS.full,
    paddingHorizontal: 16,
    paddingVertical: 8,
    backgroundColor: COLORS.primary,
  },
  trackText: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.white },
  empty: { paddingVertical: SPACING.lg },
  emptyTitle: { fontFamily: FONTS.family.headingMedium, fontSize: 18, color: COLORS.textPrimary },
  emptyBody: {
    fontFamily: FONTS.family.body,
    fontSize: 14,
    color: COLORS.textSecondary,
    marginTop: 6,
    lineHeight: 21,
  },
});
