/**
 * Maintenance timing pieces (VC-06).
 *
 * These render *timing* and nothing else. There is no place to book, no price,
 * and no judgement about how anyone looks — a kind is only ever scheduled once
 * the customer has chosen both a rhythm and a starting date, and the catalogue
 * preset is offered as a suggestion they accept rather than applied for them.
 *
 * Controls live behind a setup panel rather than sitting permanently on screen,
 * so the list stays readable on a phone.
 */
import React, { useEffect, useState } from 'react';
import { StyleSheet, Switch, Text, TextInput, TouchableOpacity, View } from 'react-native';

import { MaintenanceKindStatus } from '../../services/apiV2';
import { COLORS, FONTS, RADIUS, SPACING } from '../../theme/colors';

export interface IntervalBounds {
  min_days: number;
  max_days: number;
}

export function statusLabel(kind: MaintenanceKindStatus): string {
  switch (kind.status) {
    case 'due':
      return 'Due by your rhythm';
    case 'coming_up':
    case 'not_due':
      if (kind.days_until_due === 1) return 'Due tomorrow';
      return `Due in ${kind.days_until_due} days`;
    case 'needs_cadence':
      return 'Add your usual timing';
    case 'needs_anchor':
      return 'Add your last date';
    default:
      return 'Not tracked';
  }
}

/** What the customer still needs to tell us before a schedule exists. */
export function missingFact(kind: MaintenanceKindStatus): 'cadence' | 'last_date' | null {
  if (kind.status === 'needs_cadence') return 'cadence';
  if (kind.status === 'needs_anchor') return 'last_date';
  return null;
}

export function isValidInterval(value: string, bounds: IntervalBounds): boolean {
  if (!/^\d+$/.test(value.trim())) return false;
  const parsed = Number(value.trim());
  return parsed >= bounds.min_days && parsed <= bounds.max_days;
}

/** Accepts YYYY-MM-DD that is a real date and not in the future. */
export function isValidPastDate(value: string, today: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value.trim())) return false;
  const trimmed = value.trim();
  const parsed = new Date(`${trimmed}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime())) return false;
  if (parsed.toISOString().slice(0, 10) !== trimmed) return false;
  return trimmed <= today;
}

export function MaintenanceSetup({
  kind, bounds, today, busy, onSaveCadence, onClearCadence, onSaveLastDate, onForgetLastDate,
  onToggleReminders, onClose,
}: {
  kind: MaintenanceKindStatus;
  bounds: IntervalBounds;
  today: string;
  busy?: boolean;
  onSaveCadence: (days: number) => void;
  onClearCadence: () => void;
  onSaveLastDate: (isoDate: string) => void;
  onForgetLastDate: (isoDate: string) => void;
  onToggleReminders: (enabled: boolean) => void;
  onClose: () => void;
}) {
  const [interval, setInterval] = useState(
    kind.interval_days != null ? String(kind.interval_days) : '',
  );
  const [lastDate, setLastDate] = useState(kind.last_done_on ?? '');

  useEffect(() => {
    setInterval(kind.interval_days != null ? String(kind.interval_days) : '');
    setLastDate(kind.last_done_on ?? '');
  }, [kind.interval_days, kind.last_done_on]);

  const intervalOk = isValidInterval(interval, bounds);
  const dateOk = isValidPastDate(lastDate, today);

  return (
    <View style={styles.setup} accessibilityLabel={`${kind.label} settings`}>
      <Text style={styles.setupLabel}>How often do you usually do this?</Text>
      <View style={styles.inline}>
        <TextInput
          style={styles.input}
          value={interval}
          onChangeText={setInterval}
          keyboardType="number-pad"
          placeholder={`e.g. ${kind.suggested_interval_days}`}
          placeholderTextColor={COLORS.textSecondary}
          accessibilityLabel={`Days between each ${kind.label}`}
          maxLength={3}
        />
        <Text style={styles.unit}>days</Text>
        <TouchableOpacity
          accessibilityRole="button"
          accessibilityLabel={`Save timing for ${kind.label}`}
          onPress={() => onSaveCadence(Number(interval.trim()))}
          disabled={busy || !intervalOk}
          style={[styles.smallButton, (busy || !intervalOk) && styles.disabled]}
        >
          <Text style={styles.smallButtonText}>Save</Text>
        </TouchableOpacity>
      </View>
      <View style={styles.inline}>
        <Text style={styles.hint}>
          Most people use about {kind.suggested_interval_days} days. It is only a suggestion.
        </Text>
        <TouchableOpacity
          accessibilityRole="button"
          accessibilityLabel={`Use the suggested timing for ${kind.label}`}
          onPress={() => onSaveCadence(kind.suggested_interval_days)}
          disabled={busy}
        >
          <Text style={styles.link}>Use it</Text>
        </TouchableOpacity>
      </View>
      {kind.interval_days != null && (
        <TouchableOpacity
          accessibilityRole="button"
          accessibilityLabel={`Clear your timing for ${kind.label}`}
          onPress={onClearCadence}
          disabled={busy}
        >
          <Text style={styles.quietLink}>Clear my timing</Text>
        </TouchableOpacity>
      )}

      <Text style={styles.setupLabel}>When did you last do this?</Text>
      <View style={styles.inline}>
        <TextInput
          style={styles.input}
          value={lastDate}
          onChangeText={setLastDate}
          placeholder="YYYY-MM-DD"
          placeholderTextColor={COLORS.textSecondary}
          autoCapitalize="none"
          accessibilityLabel={`Date you last did ${kind.label}`}
          maxLength={10}
        />
        <TouchableOpacity
          accessibilityRole="button"
          accessibilityLabel={`Save last date for ${kind.label}`}
          onPress={() => onSaveLastDate(lastDate.trim())}
          disabled={busy || !dateOk}
          style={[styles.smallButton, (busy || !dateOk) && styles.disabled]}
        >
          <Text style={styles.smallButtonText}>Save</Text>
        </TouchableOpacity>
      </View>
      {!!lastDate && !dateOk && (
        <Text style={styles.hint}>Use YYYY-MM-DD, and a date that has already happened.</Text>
      )}
      {!!kind.last_done_on && (
        <TouchableOpacity
          accessibilityRole="button"
          accessibilityLabel={`Remove the recorded date for ${kind.label}`}
          onPress={() => onForgetLastDate(kind.last_done_on as string)}
          disabled={busy}
        >
          <Text style={styles.quietLink}>Remove {kind.last_done_on}</Text>
        </TouchableOpacity>
      )}

      <View style={styles.switchRow}>
        <View style={{ flex: 1 }}>
          <Text style={styles.setupLabel}>Remind me when it is due</Text>
          <Text style={styles.hint}>Off unless you turn it on.</Text>
        </View>
        <Switch
          value={kind.reminders_enabled}
          onValueChange={onToggleReminders}
          disabled={busy}
          accessibilityLabel={`Reminders for ${kind.label}`}
        />
      </View>

      <TouchableOpacity accessibilityRole="button" accessibilityLabel={`Close ${kind.label} settings`} onPress={onClose}>
        <Text style={styles.quietLink}>Done</Text>
      </TouchableOpacity>
    </View>
  );
}

export function MaintenanceRow({
  kind, bounds, today, busy, expanded, onToggleExpanded,
  onTrack, onUntrack, onRecordToday, onSaveCadence, onClearCadence, onSaveLastDate,
  onForgetLastDate, onToggleReminders,
}: {
  kind: MaintenanceKindStatus;
  bounds: IntervalBounds;
  today: string;
  busy?: boolean;
  expanded?: boolean;
  onToggleExpanded: () => void;
  onTrack: () => void;
  onUntrack: () => void;
  onRecordToday: () => void;
  onSaveCadence: (days: number) => void;
  onClearCadence: () => void;
  onSaveLastDate: (isoDate: string) => void;
  onForgetLastDate: (isoDate: string) => void;
  onToggleReminders: (enabled: boolean) => void;
}) {
  const tracked = kind.tracked;
  const missing = missingFact(kind);
  return (
    <View style={styles.rowWrap}>
      <View style={styles.row} accessibilityLabel={kind.label}>
        <View style={styles.rowText}>
          <Text style={styles.rowTitle}>{kind.label}</Text>
          <Text style={styles.rowBody}>{kind.description}</Text>
          {tracked && (
            <Text style={kind.status === 'due' ? styles.rowDue : styles.rowMeta}>
              {statusLabel(kind)}
              {kind.interval_days != null ? ` · every ${kind.interval_days} days` : ''}
            </Text>
          )}
        </View>
        <View style={styles.rowActions}>
          {tracked ? (
            <>
              {!missing && (
                <TouchableOpacity
                  accessibilityRole="button"
                  accessibilityLabel={`Record ${kind.label} today`}
                  onPress={onRecordToday}
                  disabled={busy}
                >
                  <Text style={styles.primaryLink}>Done today</Text>
                </TouchableOpacity>
              )}
              <TouchableOpacity
                accessibilityRole="button"
                accessibilityLabel={expanded ? `Hide ${kind.label} settings` : `Set up ${kind.label}`}
                onPress={onToggleExpanded}
                disabled={busy}
              >
                <Text style={styles.primaryLink}>{expanded ? 'Hide' : missing ? 'Set up' : 'Edit'}</Text>
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
      {tracked && expanded && (
        <MaintenanceSetup
          kind={kind}
          bounds={bounds}
          today={today}
          busy={busy}
          onSaveCadence={onSaveCadence}
          onClearCadence={onClearCadence}
          onSaveLastDate={onSaveLastDate}
          onForgetLastDate={onForgetLastDate}
          onToggleReminders={onToggleReminders}
          onClose={onToggleExpanded}
        />
      )}
    </View>
  );
}

export function MaintenanceEmpty() {
  return (
    <View style={styles.empty}>
      <Text style={styles.emptyTitle}>Nothing tracked yet</Text>
      <Text style={styles.emptyBody}>
        Pick the upkeep you already do, tell us your usual timing, and GlamGenius will
        keep track of when it comes round again.
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  rowWrap: { borderBottomWidth: 1, borderBottomColor: COLORS.border },
  row: { flexDirection: 'row', alignItems: 'flex-start', gap: SPACING.md, paddingVertical: SPACING.md },
  rowText: { flex: 1 },
  rowTitle: { fontFamily: FONTS.family.bodySemibold, fontSize: 16, color: COLORS.textPrimary },
  rowBody: { fontFamily: FONTS.family.body, fontSize: 13, color: COLORS.textSecondary, marginTop: 2, lineHeight: 19 },
  rowMeta: { fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.textSecondary, marginTop: 6 },
  rowDue: { fontFamily: FONTS.family.bodySemibold, fontSize: 12, color: COLORS.primary, marginTop: 6 },
  rowActions: { alignItems: 'flex-end', gap: 10 },
  primaryLink: { fontFamily: FONTS.family.bodySemibold, fontSize: 12, color: COLORS.primary },
  quietLink: { fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.textSecondary, marginTop: 4 },
  link: { fontFamily: FONTS.family.bodySemibold, fontSize: 12, color: COLORS.primary },
  trackButton: { borderRadius: RADIUS.full, paddingHorizontal: 16, paddingVertical: 8, backgroundColor: COLORS.primary },
  trackText: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.white },
  setup: { paddingBottom: SPACING.md, gap: 8 },
  setupLabel: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.textPrimary, marginTop: 6 },
  inline: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  input: {
    minWidth: 96,
    minHeight: 44,
    borderWidth: 1,
    borderColor: COLORS.border,
    borderRadius: RADIUS.md,
    paddingHorizontal: 12,
    fontFamily: FONTS.family.body,
    fontSize: 14,
    color: COLORS.textPrimary,
  },
  unit: { fontFamily: FONTS.family.body, fontSize: 13, color: COLORS.textSecondary },
  smallButton: {
    minHeight: 44,
    justifyContent: 'center',
    borderRadius: RADIUS.full,
    paddingHorizontal: 16,
    backgroundColor: COLORS.primary,
  },
  smallButtonText: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.white },
  disabled: { opacity: 0.5 },
  hint: { flex: 1, fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.textSecondary, lineHeight: 18 },
  switchRow: { flexDirection: 'row', alignItems: 'center', gap: SPACING.md, marginTop: 6 },
  empty: { paddingVertical: SPACING.lg },
  emptyTitle: { fontFamily: FONTS.family.headingMedium, fontSize: 18, color: COLORS.textPrimary },
  emptyBody: { fontFamily: FONTS.family.body, fontSize: 14, color: COLORS.textSecondary, marginTop: 6, lineHeight: 21 },
});
