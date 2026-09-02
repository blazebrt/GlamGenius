import React from 'react';
import { ActivityIndicator, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';

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
  selected, onSelect, onAddPhoto, onSubmit, onCancel, onCaptureLabel,
  photoAdded, busy, status, signedIn, batchRequired,
}: {
  selected: string | null;
  onSelect: (code: string) => void;
  onAddPhoto: () => void;
  onSubmit: () => void;
  onCancel: () => void;
  onCaptureLabel: () => void;
  photoAdded: boolean;
  busy: boolean;
  status: string | null;
  signedIn: boolean;
  batchRequired: boolean;
}) {
  const canSubmit = !!selected && photoAdded && signedIn && !batchRequired && !busy;
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
  action: { color: COLORS.primary, fontFamily: FONTS.family.body, fontSize: 14, marginTop: SPACING.sm },
  submit: {
    alignItems: 'center', backgroundColor: COLORS.primary, borderRadius: RADIUS.md,
    marginTop: SPACING.md, padding: SPACING.md,
  },
  submitDisabled: { opacity: 0.5 },
  submitText: { color: COLORS.background, fontFamily: FONTS.family.heading, fontSize: 16 },
  status: { color: COLORS.textSecondary, fontFamily: FONTS.family.body, fontSize: 14, marginTop: SPACING.sm },
});
