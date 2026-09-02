/**
 * What a scan looks like on screen.
 *
 * Kept apart from the camera so the parts a person actually reads can be
 * rendered and tested without a device. Two rules run through all of it:
 *
 * - Nothing is shown without its confidence level.
 * - Anything from Open Food Facts is shown with their attribution beside it,
 *   because the licence requires it (docs/architecture/ODBL_DATA_WALL.md).
 */
import React from 'react';
import { Linking, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';

import { COLORS, FONTS, RADIUS, SPACING } from '../../theme/colors';
import type { Confidence, ScanResult } from '../../services/productScan';
import { OpenFoodFactsAttribution } from '../common/OpenFoodFactsAttribution';
import { S } from '../../strings/verdict';

const BADGE_COLOUR: Record<string, string> = {
  verified: COLORS.success,
  community: COLORS.info,
  unverified: COLORS.warning,
  not_enough_information: COLORS.textSecondary,
};

const BADGE_LABEL: Record<string, string> = {
  verified: 'Verified',
  community: 'Community checked',
  unverified: 'Unverified',
  not_enough_information: 'Not enough information',
};

export function ConfidenceBadge({ confidence }: { confidence: Confidence }) {
  const colour = BADGE_COLOUR[confidence.level] ?? COLORS.textSecondary;
  return (
    <View style={styles.badgeRow} accessibilityLabel={`Confidence: ${BADGE_LABEL[confidence.level] ?? confidence.level}`}>
      <View style={[styles.badgeDot, { backgroundColor: colour }]} />
      <View style={{ flex: 1 }}>
        <Text style={[styles.badgeLabel, { color: colour }]}>{BADGE_LABEL[confidence.level] ?? confidence.level}</Text>
        <Text style={styles.badgeText}>{confidence.text}</Text>
      </View>
    </View>
  );
}

export function OfflineNote({ queued }: { queued: number }) {
  return (
    <View style={styles.offline} accessibilityLabel="Offline notice">
      <Ionicons name="cloud-offline-outline" size={16} color={COLORS.textSecondary} />
      <Text style={styles.offlineText}>
        {queued > 0
          ? `You are offline. ${queued} scan${queued === 1 ? '' : 's'} saved — they will sync when you are back.`
          : 'You are offline. Scanning still works from what this phone already has.'}
      </Text>
    </View>
  );
}

/** The FSSAI licence, stated as a fact about the pack and nothing more. */
export function FssaiLine({ licence }: { licence: string }) {
  return (
    <View style={styles.fssai}>
      <Text style={styles.fssaiLabel}>FSSAI licence</Text>
      <Text style={styles.fssaiValue}>{licence}</Text>
    </View>
  );
}

export function ProductResult({
  result,
  onCaptureLabel,
  onScanAgain,
}: {
  result: ScanResult;
  onCaptureLabel: () => void;
  onScanAgain: () => void;
}) {
  const off = result.open_food_facts;
  const licence = result.glamgenius?.fssai_licence;
  const name = off?.product_name?.trim();
  const brand = off?.brands?.trim();
  const officialRecords = result.official_records?.records ?? [];

  return (
    <View style={styles.card}>
      <Text style={styles.barcode}>{result.barcode}</Text>
      <Text style={styles.name}>{name || 'Product on file'}</Text>
      {!!brand && <Text style={styles.brand}>{brand}</Text>}

      <ConfidenceBadge confidence={result.confidence} />

      {!!off?.quantity && <Text style={styles.detail}>Pack size: {off.quantity}</Text>}
      {!!off?.ingredients_text && (
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Ingredients, as printed</Text>
          <Text style={styles.body}>{off.ingredients_text}</Text>
        </View>
      )}
      {!!licence && <FssaiLine licence={licence} />}
      {officialRecords.map((record) => (
        <View key={record.id} style={styles.section} accessible accessibilityLabel={S.officialRecords.title}>
          <Text style={styles.sectionTitle}>{S.officialRecords.title}</Text>
          <Text style={styles.body}>{S.officialRecords.recallFound}</Text>
          <Text style={styles.detail}>{S.officialRecords.recallId}: {record.recall_id}</Text>
          {!!record.recall_status && <Text style={styles.detail}>{S.officialRecords.status}: {record.recall_status}</Text>}
          {!!record.reason && <Text style={styles.detail}>{S.officialRecords.reason}: {record.reason}</Text>}
          <TouchableOpacity
            accessibilityRole="link"
            accessibilityLabel={S.officialRecords.openSource}
            onPress={() => void Linking.openURL(record.source_url)}
            style={styles.secondaryButton}
          >
            <Text style={styles.secondaryText}>{S.officialRecords.openSource}</Text>
          </TouchableOpacity>
        </View>
      ))}
      {!!off && <OpenFoodFactsAttribution />}

      {result.can_capture_label && (
        <TouchableOpacity
          accessibilityRole="button"
          accessibilityLabel="Photograph the label"
          onPress={onCaptureLabel}
          style={styles.secondaryButton}
        >
          <Ionicons name="camera-outline" size={18} color={COLORS.primary} />
          <Text style={styles.secondaryText}>Some of this is missing — photograph the label</Text>
        </TouchableOpacity>
      )}
      <TouchableOpacity
        accessibilityRole="button"
        accessibilityLabel="Scan another product"
        onPress={onScanAgain}
        style={styles.primaryButton}
      >
        <Text style={styles.primaryText}>Scan another</Text>
      </TouchableOpacity>
    </View>
  );
}

export function NotFoundResult({
  result,
  onCaptureLabel,
  onScanAgain,
}: {
  result: ScanResult;
  onCaptureLabel: () => void;
  onScanAgain: () => void;
}) {
  return (
    <View style={styles.card}>
      <Text style={styles.barcode}>{result.barcode}</Text>
      <Text style={styles.name}>We do not know this one yet</Text>
      <ConfidenceBadge confidence={result.confidence} />
      <Text style={styles.body}>
        {result.message || 'Take a photo of the label and we will read what is printed on it.'}
      </Text>
      <TouchableOpacity
        accessibilityRole="button"
        accessibilityLabel="Photograph the label"
        onPress={onCaptureLabel}
        style={styles.primaryButton}
      >
        <Text style={styles.primaryText}>Photograph the label</Text>
      </TouchableOpacity>
      <TouchableOpacity
        accessibilityRole="button"
        accessibilityLabel="Scan another product"
        onPress={onScanAgain}
        style={styles.secondaryButton}
      >
        <Text style={styles.secondaryText}>Scan another</Text>
      </TouchableOpacity>
    </View>
  );
}

const FACT_LABELS: Record<string, string> = {
  product_name: 'Name',
  brand: 'Brand',
  ingredients_text: 'Ingredients',
  serving_size: 'Serving size',
  net_quantity: 'Net quantity',
  allergen_text: 'Allergens',
  veg_mark: 'Veg mark',
  fssai_licence: 'FSSAI licence',
};

/**
 * The VC-07 confirm pattern: read back what was transcribed, and nothing counts
 * until a person says it is right.
 */
export function LabelReview({
  facts,
  onConfirm,
  onRetake,
  busy,
}: {
  facts: Record<string, unknown>;
  onConfirm: () => void;
  onRetake: () => void;
  busy?: boolean;
}) {
  const uncertain = (facts.uncertain_fields as string[] | undefined) ?? [];
  const rows = Object.entries(FACT_LABELS)
    .filter(([key]) => typeof facts[key] === 'string' && (facts[key] as string).trim())
    .map(([key, label]) => ({ key, label, value: facts[key] as string }));
  const nutrition = (facts.nutrition_per_100g as Record<string, string> | undefined) ?? {};
  const nutritionBasis = facts.nutrition_basis;
  const basisLabel = nutritionBasis === 'per_100g'
    ? S.labelReview.basisPer100g
    : nutritionBasis === 'per_100ml'
      ? S.labelReview.basisPer100ml
      : S.labelReview.basisMissing;

  return (
    <View style={styles.card}>
      <Text style={styles.name}>This is what the label says</Text>
      <Text style={styles.body}>
        Read by the camera, not checked by anyone yet. Nothing is saved until you confirm it.
      </Text>

      {rows.map((row) => (
        <View key={row.key} style={styles.factRow}>
          <Text style={styles.factLabel}>{row.label}</Text>
          <Text style={styles.factValue}>{row.value}</Text>
        </View>
      ))}

      {(Object.keys(nutrition).length > 0 || nutritionBasis !== undefined) && (
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Nutrition, exactly as printed</Text>
          <View style={styles.factRow}>
            <Text style={styles.factLabel}>{S.labelReview.basis}</Text>
            <Text style={styles.factValue}>{basisLabel}</Text>
          </View>
          {Object.entries(nutrition).map(([key, value]) => (
            <View key={key} style={styles.factRow}>
              <Text style={styles.factLabel}>{key.replace(/_/g, ' ')}</Text>
              <Text style={styles.factValue}>{value}</Text>
            </View>
          ))}
        </View>
      )}

      {uncertain.length > 0 && (
        <Text style={styles.uncertain}>Could not read clearly: {uncertain.join(', ')}. Retake if these matter.</Text>
      )}

      <TouchableOpacity
        accessibilityRole="button"
        accessibilityLabel="Confirm this label"
        onPress={onConfirm}
        disabled={busy}
        style={styles.primaryButton}
      >
        <Text style={styles.primaryText}>{busy ? 'Saving…' : 'Yes, that is right'}</Text>
      </TouchableOpacity>
      <TouchableOpacity
        accessibilityRole="button"
        accessibilityLabel="Retake the label photo"
        onPress={onRetake}
        style={styles.secondaryButton}
      >
        <Text style={styles.secondaryText}>Retake the photo</Text>
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: COLORS.card, borderRadius: RADIUS.lg, padding: SPACING.lg,
    borderWidth: 1, borderColor: COLORS.border, gap: SPACING.sm,
  },
  barcode: { fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.textMuted, letterSpacing: 1 },
  name: { fontFamily: FONTS.family.heading, fontSize: 22, color: COLORS.textPrimary },
  brand: { fontFamily: FONTS.family.bodyMedium, fontSize: 14, color: COLORS.textSecondary },
  detail: { fontFamily: FONTS.family.body, fontSize: 13, color: COLORS.textSecondary },
  section: { marginTop: SPACING.sm, gap: 4 },
  sectionTitle: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.textPrimary },
  body: { fontFamily: FONTS.family.body, fontSize: 14, lineHeight: 21, color: COLORS.textSecondary },
  badgeRow: {
    flexDirection: 'row', alignItems: 'flex-start', gap: SPACING.sm, paddingVertical: SPACING.sm,
    borderTopWidth: 1, borderBottomWidth: 1, borderColor: COLORS.borderLight,
  },
  badgeDot: { width: 10, height: 10, borderRadius: 5, marginTop: 4 },
  badgeLabel: { fontFamily: FONTS.family.bodySemibold, fontSize: 13 },
  badgeText: { fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.textSecondary, marginTop: 2 },
  offline: {
    flexDirection: 'row', alignItems: 'center', gap: SPACING.sm,
    backgroundColor: COLORS.backgroundSecondary, borderRadius: RADIUS.md, padding: SPACING.sm,
  },
  offlineText: { flex: 1, fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.textSecondary },
  fssai: { flexDirection: 'row', justifyContent: 'space-between', marginTop: SPACING.sm },
  fssaiLabel: { fontFamily: FONTS.family.bodyMedium, fontSize: 13, color: COLORS.textSecondary },
  fssaiValue: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.textPrimary },
  factRow: { flexDirection: 'row', justifyContent: 'space-between', gap: SPACING.md },
  factLabel: { fontFamily: FONTS.family.bodyMedium, fontSize: 13, color: COLORS.textSecondary, flexShrink: 0 },
  factValue: { fontFamily: FONTS.family.body, fontSize: 13, color: COLORS.textPrimary, flex: 1, textAlign: 'right' },
  uncertain: { fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.warning, marginTop: SPACING.sm },
  primaryButton: {
    backgroundColor: COLORS.primary, borderRadius: RADIUS.md, paddingVertical: 14,
    alignItems: 'center', marginTop: SPACING.sm,
  },
  primaryText: { fontFamily: FONTS.family.bodySemibold, fontSize: 15, color: COLORS.white },
  secondaryButton: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8,
    borderRadius: RADIUS.md, paddingVertical: 13, borderWidth: 1, borderColor: COLORS.primary,
  },
  secondaryText: { fontFamily: FONTS.family.bodySemibold, fontSize: 14, color: COLORS.primary },
});
