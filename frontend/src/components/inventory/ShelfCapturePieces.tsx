/**
 * Reviewing what one shelf photo found.
 *
 * The whole design constraint is speed: fifteen things in under three minutes
 * means about ten seconds each, and most of that should be reading, not
 * tapping. So each candidate is one row with two large targets — keep or drop
 * — and the row leaves the list the moment it is decided, putting the next one
 * under the same thumb.
 *
 * Nothing here is on the shelf. The list says so, because a review screen that
 * looks like an inventory invites people to skip it.
 */
import React from 'react';
import { StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';

import { ImportCandidate, InventoryCategory } from '../../services/apiV2';
import { COLORS, FONTS, RADIUS, SPACING } from '../../theme/colors';
import { CATEGORY_META } from './InventoryPieces';

export function CaptureProgress({
  decided,
  total,
  unreadable,
}: {
  decided: number;
  total: number;
  unreadable: number | null;
}) {
  return (
    <View style={styles.progress} accessibilityLabel={`${decided} of ${total} reviewed`}>
      <Text style={styles.eyebrow}>NOTHING HERE IS ON YOUR SHELF YET</Text>
      <Text style={styles.progressTitle}>
        {decided} of {total} reviewed
      </Text>
      <View style={styles.track}>
        <View style={[styles.fill, { width: `${total ? (decided / total) * 100 : 0}%` }]} />
      </View>
      {!!unreadable && unreadable > 0 && (
        <Text style={styles.note}>
          {unreadable} more {unreadable === 1 ? 'thing was' : 'things were'} visible but not readable. Photograph
          those separately.
        </Text>
      )}
    </View>
  );
}

/** What the photo suggested about one product, and the two taps that settle it. */
export function CandidateRow({
  candidate,
  onKeep,
  onDrop,
  busy,
}: {
  candidate: ImportCandidate;
  onKeep: () => void;
  onDrop: () => void;
  busy?: boolean;
}) {
  const meta = CATEGORY_META[candidate.category as InventoryCategory];
  const detail = [candidate.brand, meta?.label, candidate.details?.size || candidate.details?.product_type]
    .filter(Boolean)
    .join(' · ');

  return (
    <View style={styles.row}>
      <View style={styles.rowIcon}>
        <Ionicons name={(meta?.icon || 'cube-outline') as any} size={20} color={COLORS.primary} />
      </View>
      <View style={{ flex: 1 }}>
        <Text style={styles.rowName} numberOfLines={2}>{candidate.display_name}</Text>
        {!!detail && <Text style={styles.rowMeta} numberOfLines={1}>{detail}</Text>}
        {candidate.uncertain_fields.length > 0 && (
          <Text style={styles.uncertain} numberOfLines={1}>
            Could not read: {candidate.uncertain_fields.join(', ')}
          </Text>
        )}
      </View>
      <TouchableOpacity
        accessibilityRole="button"
        accessibilityLabel={`Drop ${candidate.display_name}`}
        onPress={onDrop}
        disabled={busy}
        style={[styles.tap, styles.drop]}
      >
        <Ionicons name="close" size={22} color={COLORS.error} />
      </TouchableOpacity>
      <TouchableOpacity
        accessibilityRole="button"
        accessibilityLabel={`Keep ${candidate.display_name}`}
        onPress={onKeep}
        disabled={busy}
        style={[styles.tap, styles.keep]}
      >
        <Ionicons name="checkmark" size={22} color={COLORS.white} />
      </TouchableOpacity>
    </View>
  );
}

export function CaptureDone({
  kept,
  dropped,
  onScanAnother,
  onOpenInventory,
}: {
  kept: number;
  dropped: number;
  onScanAnother: () => void;
  onOpenInventory: () => void;
}) {
  return (
    <View style={styles.done} accessibilityLabel="Capture finished">
      <Ionicons name="checkmark-circle-outline" size={28} color={COLORS.success} />
      <Text style={styles.doneTitle}>
        {kept} {kept === 1 ? 'item is' : 'items are'} on your shelf
      </Text>
      <Text style={styles.body}>
        {dropped > 0
          ? `${dropped} ${dropped === 1 ? 'suggestion was' : 'suggestions were'} dropped and saved nothing.`
          : 'Everything the photo found was kept.'}
      </Text>
      <TouchableOpacity
        accessibilityRole="button"
        accessibilityLabel="Photograph another shelf"
        onPress={onScanAnother}
        style={styles.primary}
      >
        <Text style={styles.primaryText}>Photograph another shelf</Text>
      </TouchableOpacity>
      <TouchableOpacity
        accessibilityRole="button"
        accessibilityLabel="Open the inventory"
        onPress={onOpenInventory}
        style={styles.linkButton}
      >
        <Text style={styles.linkText}>See what you own</Text>
      </TouchableOpacity>
    </View>
  );
}

export function EmptyCapture({ onRetake }: { onRetake: () => void }) {
  return (
    <View style={styles.done} accessibilityLabel="Nothing found">
      <Text style={styles.doneTitle}>We could not read anything on that shelf</Text>
      <Text style={styles.body}>
        Try again with more light, the labels facing the camera, and one shelf at a time.
      </Text>
      <TouchableOpacity
        accessibilityRole="button"
        accessibilityLabel="Take another photo"
        onPress={onRetake}
        style={styles.primary}
      >
        <Text style={styles.primaryText}>Take another photo</Text>
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  progress: { gap: 6, paddingBottom: SPACING.sm },
  eyebrow: { fontFamily: FONTS.family.bodySemibold, fontSize: 11, letterSpacing: 1.4, color: COLORS.textMuted },
  progressTitle: { fontFamily: FONTS.family.heading, fontSize: 22, color: COLORS.textPrimary },
  track: { height: 6, borderRadius: 3, backgroundColor: COLORS.backgroundSecondary, overflow: 'hidden' },
  fill: { height: 6, borderRadius: 3, backgroundColor: COLORS.primary },
  note: { fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.textSecondary, marginTop: 4 },
  row: {
    flexDirection: 'row', alignItems: 'center', gap: SPACING.sm,
    backgroundColor: COLORS.card, borderRadius: RADIUS.lg, padding: SPACING.md,
    borderWidth: 1, borderColor: COLORS.border,
  },
  rowIcon: {
    width: 38, height: 38, borderRadius: 12, backgroundColor: COLORS.primaryLight,
    alignItems: 'center', justifyContent: 'center',
  },
  rowName: { fontFamily: FONTS.family.bodySemibold, fontSize: 15, color: COLORS.textPrimary },
  rowMeta: { fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.textSecondary, marginTop: 2 },
  uncertain: { fontFamily: FONTS.family.body, fontSize: 11, color: COLORS.warning, marginTop: 2 },
  // Deliberately large: a thumb, moving fast, fifteen times.
  tap: { width: 48, height: 48, borderRadius: 24, alignItems: 'center', justifyContent: 'center' },
  drop: { backgroundColor: COLORS.errorLight },
  keep: { backgroundColor: COLORS.primary },
  done: { gap: SPACING.sm, alignItems: 'flex-start', paddingVertical: SPACING.lg },
  doneTitle: { fontFamily: FONTS.family.heading, fontSize: 24, color: COLORS.textPrimary },
  body: { fontFamily: FONTS.family.body, fontSize: 14, lineHeight: 21, color: COLORS.textSecondary },
  primary: {
    backgroundColor: COLORS.primary, borderRadius: RADIUS.md, paddingVertical: 14, paddingHorizontal: 20,
    alignSelf: 'stretch', alignItems: 'center', marginTop: SPACING.sm,
  },
  primaryText: { fontFamily: FONTS.family.bodySemibold, fontSize: 15, color: COLORS.white },
  linkButton: { paddingVertical: 10, alignSelf: 'stretch', alignItems: 'center' },
  linkText: { fontFamily: FONTS.family.bodySemibold, fontSize: 14, color: COLORS.primary },
});
