import React from 'react';
import { ActivityIndicator, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';

import type { CommunityOwnReport } from '../../services/apiV2';
import { S } from '../../strings/verdict';
import { COLORS, FONTS, RADIUS, SPACING } from '../../theme/colors';

/**
 * The report flow: a closed list of choices and a required photo.
 *
 * There is no text field anywhere in here, and there is not meant to be — not
 * a comment box, not a caption, not an optional "anything else". Zero free text
 * is a constitutional rule, and a test asserts this file renders no TextInput.
 *
 * The batch is never typed either. When an observation needs a lot number, the
 * shopper is sent to the existing label capture rather than asked to read one
 * out.
 */

/** Codes whose signal is about one lot, and therefore need a captured batch. */
export const BATCH_SCOPED_CODES = [
  'date_marking_unreadable',
  'seal_broken',
  'pack_leaking',
  'pack_swollen',
  'visible_foreign_material',
  'insect_observed',
] as const;

export const PRODUCT_DATA_CODES = [
  'barcode_result_differs_from_pack',
  'ingredients_list_differs_from_app',
  'nutrition_panel_differs_from_app',
  'pack_size_differs_from_app',
] as const;

export const OBSERVATION_CODES = [...PRODUCT_DATA_CODES, ...BATCH_SCOPED_CODES];

export function CommunityReportSheet({
  selected, onSelect, onAddPhoto, onSubmit, onCancel, onCaptureLabel, onWithdraw,
  ownReports, photoAdded, busy, status, signedIn, batchRequired,
}: {
  selected: string | null;
  onSelect: (code: string) => void;
  onAddPhoto: () => void;
  onSubmit: () => void;
  onCancel: () => void;
  onCaptureLabel: () => void;
  /** Retract one named row. Only ever this account's own. */
  onWithdraw: (reportId: string) => void;
  /** This account's own reports for this pack. Not a feed, not a history. */
  ownReports: CommunityOwnReport[];
  photoAdded: boolean;
  busy: boolean;
  status: string | null;
  signedIn: boolean;
  batchRequired: boolean;
}) {
  const canSubmit = !!selected && photoAdded && signedIn && !batchRequired && !busy;
  // A retraction is final, so a withdrawn row is offered no action at all.
  const retractable = ownReports.filter((report) => report.status !== 'withdrawn');
  return (
    <ScrollView style={styles.sheet} contentContainerStyle={{ paddingBottom: SPACING.lg }}>
      <Text style={styles.title} accessibilityRole="header">{S.communityObservations.heading}</Text>
      <Text style={styles.subtitle}>{S.communityObservations.chooseObservation}</Text>

      {OBSERVATION_CODES.map((code) => {
        const label = S.communityObservations.observation[code] ?? code;
        const isSelected = selected === code;
        return (
          <TouchableOpacity
            key={code}
            accessibilityRole="button"
            accessibilityLabel={label}
            accessibilityState={{ selected: isSelected, disabled: busy }}
            onPress={() => onSelect(code)}
            disabled={busy}
            style={[styles.option, isSelected && styles.optionSelected]}
          >
            <Text style={styles.optionText}>{label}</Text>
            {/* The tick is not the only signal: selection is announced. */}
            {isSelected && <Ionicons name="checkmark" size={18} color={COLORS.primary} />}
          </TouchableOpacity>
        );
      })}

      {!signedIn && <Text style={styles.notice}>{S.communityObservations.signInToReport}</Text>}

      {batchRequired && (
        <View>
          <Text style={styles.notice}>{S.communityObservations.batchCaptureRequired}</Text>
          <TouchableOpacity
            accessibilityRole="button"
            accessibilityLabel={S.communityObservations.captureLabelAction}
            onPress={onCaptureLabel}
          >
            <Text style={styles.action}>{S.communityObservations.captureLabelAction}</Text>
          </TouchableOpacity>
        </View>
      )}

      <TouchableOpacity
        accessibilityRole="button"
        accessibilityLabel={S.communityObservations.photoAction}
        accessibilityState={{ checked: photoAdded }}
        onPress={onAddPhoto}
        style={styles.photoButton}
      >
        <Ionicons name="camera-outline" size={19} color={COLORS.primary} />
        <Text style={styles.action}>{S.communityObservations.photoAction}</Text>
      </TouchableOpacity>
      {!photoAdded && <Text style={styles.notice}>{S.communityObservations.photoRequired}</Text>}

      <TouchableOpacity
        accessibilityRole="button"
        accessibilityLabel={S.communityObservations.submit}
        accessibilityState={{ disabled: !canSubmit, busy }}
        onPress={onSubmit}
        disabled={!canSubmit}
        style={[styles.submit, !canSubmit && styles.submitDisabled]}
      >
        {busy ? <ActivityIndicator color={COLORS.textPrimary} /> : (
          <Text style={styles.submitText}>{S.communityObservations.submit}</Text>
        )}
      </TouchableOpacity>

      {!!status && <Text accessibilityLiveRegion="polite" style={styles.status}>{status}</Text>}

      {/* The shopper's own content, so anything they sent stays retractable
          after this sheet is closed and reopened. Their rows only: no other
          account appears here, and there is no profile, history or feed. */}
      {retractable.length > 0 && (
        <View style={styles.ownSection}>
          <Text style={styles.ownHeading} accessibilityRole="header">
            {S.communityObservations.yourObservations}
          </Text>
          {retractable.map((report) => (
            <View key={report.id} style={styles.ownRow}>
              <Text style={styles.ownLabel}>
                {S.communityObservations.observation[report.observation_code] ?? report.observation_code}
              </Text>
              <TouchableOpacity
                accessibilityRole="button"
                accessibilityLabel={`${S.communityObservations.withdraw}: ${
                  S.communityObservations.observation[report.observation_code] ?? report.observation_code
                }`}
                onPress={() => onWithdraw(report.id)}
                disabled={busy}
              >
                <Text style={styles.action}>{S.communityObservations.withdraw}</Text>
              </TouchableOpacity>
            </View>
          ))}
        </View>
      )}

      <TouchableOpacity
        accessibilityRole="button" accessibilityLabel={S.communityObservations.cancel}
        onPress={onCancel}
      >
        <Text style={styles.action}>{S.communityObservations.cancel}</Text>
      </TouchableOpacity>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  sheet: { backgroundColor: COLORS.background, flex: 1, padding: SPACING.md },
  title: { color: COLORS.textPrimary, fontFamily: FONTS.family.heading, fontSize: 20 },
  subtitle: {
    color: COLORS.textSecondary, fontFamily: FONTS.family.body, fontSize: 14,
    marginBottom: SPACING.sm, marginTop: SPACING.xs,
  },
  option: {
    alignItems: 'center', borderColor: COLORS.border, borderRadius: RADIUS.md, borderWidth: 1,
    flexDirection: 'row', justifyContent: 'space-between', marginTop: SPACING.xs,
    padding: SPACING.md,
  },
  optionSelected: { borderColor: COLORS.primary },
  optionText: { color: COLORS.textPrimary, flex: 1, fontFamily: FONTS.family.body, fontSize: 14 },
  photoButton: { alignItems: 'center', flexDirection: 'row', gap: SPACING.xs, marginTop: SPACING.md },
  notice: { color: COLORS.textMuted, fontFamily: FONTS.family.body, fontSize: 13, marginTop: SPACING.xs },
  ownSection: { borderTopColor: COLORS.border, borderTopWidth: 1, marginTop: SPACING.lg, paddingTop: SPACING.md },
  ownHeading: { color: COLORS.textPrimary, fontFamily: FONTS.family.heading, fontSize: 15, marginBottom: SPACING.xs },
  ownRow: { alignItems: 'center', flexDirection: 'row', justifyContent: 'space-between', paddingVertical: SPACING.xs },
  ownLabel: { color: COLORS.textSecondary, flexShrink: 1, fontFamily: FONTS.family.body, fontSize: 14 },
  action: { color: COLORS.primary, fontFamily: FONTS.family.body, fontSize: 14, marginTop: SPACING.sm },
  submit: {
    alignItems: 'center', backgroundColor: COLORS.primary, borderRadius: RADIUS.md,
    marginTop: SPACING.md, padding: SPACING.md,
  },
  submitDisabled: { opacity: 0.5 },
  submitText: { color: COLORS.background, fontFamily: FONTS.family.heading, fontSize: 16 },
  status: { color: COLORS.textSecondary, fontFamily: FONTS.family.body, fontSize: 14, marginTop: SPACING.sm },
});
