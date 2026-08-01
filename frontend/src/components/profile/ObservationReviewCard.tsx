import React, { useState } from 'react';
import { StyleSheet, Text, TextInput, TouchableOpacity, View } from 'react-native';
import { ProfileObservation } from '../../services/apiV2';
import { COLORS, FONTS, RADIUS, SPACING } from '../../theme/colors';

export function confidenceLabel(value: number): string {
  if (value >= 0.8) return 'High confidence';
  if (value >= 0.55) return 'Medium confidence';
  return 'Low confidence';
}

export function ObservationReviewCard({
  observation,
  busy = false,
  onConfirm,
  onReject,
  onEdit,
  onNotSure,
}: {
  observation: ProfileObservation;
  busy?: boolean;
  onConfirm: () => void;
  onReject: () => void;
  onEdit: (value: string) => void;
  onNotSure: () => void;
}) {
  const initial = Array.isArray(observation.value) ? observation.value.join(', ') : String(observation.value);
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(initial);
  const reviewed = ['confirmed', 'rejected'].includes(observation.verification_state);

  return (
    <View style={styles.card} accessibilityLabel={`${observation.label} observation`}>
      <View style={styles.header}>
        <Text style={styles.label}>{observation.label}</Text>
        <Text style={styles.confidence}>{confidenceLabel(observation.confidence)}</Text>
      </View>
      {editing ? (
        <TextInput
          accessibilityLabel={`Edit ${observation.label}`}
          style={styles.input}
          value={value}
          onChangeText={setValue}
          autoFocus
        />
      ) : <Text style={styles.value}>{initial}</Text>}
      <Text style={styles.why}>Why: {observation.why}</Text>
      {!reviewed && (
        <View style={styles.actions}>
          {editing ? (
            <TouchableOpacity accessibilityRole="button" accessibilityLabel={`Save ${observation.label}`} disabled={busy || !value.trim()} onPress={() => { onEdit(value.trim()); setEditing(false); }} style={styles.primaryButton}>
              <Text style={styles.primaryText}>Save edit</Text>
            </TouchableOpacity>
          ) : (
            <TouchableOpacity accessibilityRole="button" accessibilityLabel={`Confirm ${observation.label}`} disabled={busy} onPress={onConfirm} style={styles.primaryButton}>
              <Text style={styles.primaryText}>Confirm</Text>
            </TouchableOpacity>
          )}
          <TouchableOpacity accessibilityRole="button" accessibilityLabel={`Edit ${observation.label}`} disabled={busy} onPress={() => setEditing(!editing)} style={styles.secondaryButton}>
            <Text style={styles.secondaryText}>{editing ? 'Cancel' : 'Edit'}</Text>
          </TouchableOpacity>
          <TouchableOpacity accessibilityRole="button" accessibilityLabel={`Not sure about ${observation.label}`} disabled={busy} onPress={onNotSure} style={styles.secondaryButton}>
            <Text style={styles.secondaryText}>Not sure</Text>
          </TouchableOpacity>
          <TouchableOpacity accessibilityRole="button" accessibilityLabel={`Reject ${observation.label}`} disabled={busy} onPress={onReject} style={styles.rejectButton}>
            <Text style={styles.rejectText}>Reject</Text>
          </TouchableOpacity>
        </View>
      )}
      {reviewed && <Text style={styles.state}>{observation.verification_state === 'confirmed' ? 'Confirmed by you' : 'Rejected and kept in history'}</Text>}
    </View>
  );
}

const styles = StyleSheet.create({
  card: { backgroundColor: COLORS.card, borderRadius: RADIUS.lg, borderWidth: 1, borderColor: COLORS.border, padding: SPACING.md, marginBottom: 12 },
  header: { flexDirection: 'row', justifyContent: 'space-between', gap: 8 },
  label: { fontFamily: FONTS.family.bodySemibold, color: COLORS.textPrimary, fontSize: 14 },
  confidence: { fontFamily: FONTS.family.bodyMedium, color: COLORS.primary, fontSize: 11 },
  value: { fontFamily: FONTS.family.headingMedium, color: COLORS.textPrimary, fontSize: 20, marginTop: 8, textTransform: 'capitalize' },
  why: { fontFamily: FONTS.family.body, color: COLORS.textSecondary, fontSize: 12, lineHeight: 18, marginTop: 8 },
  input: { borderWidth: 1, borderColor: COLORS.borderFocus, borderRadius: RADIUS.md, padding: 10, marginTop: 8, color: COLORS.textPrimary },
  actions: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: 14 },
  primaryButton: { backgroundColor: COLORS.primary, borderRadius: RADIUS.full, paddingHorizontal: 14, paddingVertical: 8 },
  primaryText: { color: COLORS.white, fontFamily: FONTS.family.bodySemibold, fontSize: 12 },
  secondaryButton: { borderWidth: 1, borderColor: COLORS.border, borderRadius: RADIUS.full, paddingHorizontal: 12, paddingVertical: 8 },
  secondaryText: { color: COLORS.textSecondary, fontFamily: FONTS.family.bodyMedium, fontSize: 12 },
  rejectButton: { paddingHorizontal: 10, paddingVertical: 8 },
  rejectText: { color: COLORS.error, fontFamily: FONTS.family.bodyMedium, fontSize: 12 },
  state: { color: COLORS.textMuted, fontFamily: FONTS.family.bodyMedium, fontSize: 12, marginTop: 12 },
});
