/**
 * Should-I-buy-this building blocks.
 *
 * Two things this screen must never do, and the components are shaped so it
 * cannot: it never calls a purchase a waste, and it never shows a verdict
 * without the arithmetic behind it. `ROIBreakdown` renders every factor, so a
 * user who disagrees can see exactly which line they disagree with.
 */
import React from 'react';
import { ActivityIndicator, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';

import { AppearanceROI, OwnedReference, PurchaseEvaluation, ShoppingCandidate, Verdict } from '../../services/apiV2';
import { COLORS, FONTS, RADIUS, SPACING } from '../../theme/colors';

export const VERDICT_META: Record<Verdict, { label: string; icon: string; colour: string; background: string; blurb: string }> = {
  buy: {
    label: 'Buy', icon: 'checkmark-circle', colour: COLORS.success, background: COLORS.successLight,
    blurb: 'This earns its place in what you already own.',
  },
  wait: {
    label: 'Wait', icon: 'time-outline', colour: COLORS.warning, background: COLORS.warningLight,
    blurb: 'Worth thinking about — there is something to check first.',
  },
  skip: {
    label: 'Skip', icon: 'close-circle-outline', colour: COLORS.info, background: COLORS.infoLight,
    blurb: 'You already have this covered.',
  },
};

export const DECISION_OPTIONS: { key: 'bought' | 'waiting' | 'skipped'; label: string }[] = [
  { key: 'bought', label: 'I bought it' },
  { key: 'waiting', label: 'I am waiting' },
  { key: 'skipped', label: 'I skipped it' },
];

/** A percentage is easier to act on than 0.62, and it is the same number. */
export const roiPercent = (score: number) => Math.round(score * 100);

export function ShoppingUpload({ onPickScreenshot, onEnterDetails, busy }: {
  onPickScreenshot: () => void; onEnterDetails: () => void; busy?: boolean;
}) {
  return (
    <View style={styles.card} accessibilityLabel="Check something before you buy it">
      <Text style={styles.eyebrow}>SHOULD I BUY THIS?</Text>
      <Text style={styles.title}>Check it against what you already own</Text>
      <Text style={styles.body}>
        Send a screenshot of the product page, or type in what it is. Nothing is bought here and nothing is added to your inventory.
      </Text>
      <TouchableOpacity
        accessibilityRole="button"
        accessibilityLabel="Upload a product screenshot"
        onPress={onPickScreenshot}
        disabled={busy}
        style={[styles.primary, busy && styles.disabled]}
      >
        {busy ? <ActivityIndicator color={COLORS.white} /> : <Ionicons name="image-outline" size={18} color={COLORS.white} />}
        <Text style={styles.primaryText}>Upload screenshot</Text>
      </TouchableOpacity>
      <TouchableOpacity accessibilityRole="button" accessibilityLabel="Enter the details myself" onPress={onEnterDetails}>
        <Text style={styles.link}>Enter the details myself</Text>
      </TouchableOpacity>
    </View>
  );
}

export function ExtractedItemReview({ candidate, onConfirm, onEdit }: {
  candidate: ShoppingCandidate; onConfirm?: () => void; onEdit?: () => void;
}) {
  const facts = [
    ['Brand', candidate.brand], ['Colour', candidate.colour], ['Size', candidate.size],
    ['Fabric', candidate.fabric], ['Price', candidate.price != null ? `${candidate.currency} ${candidate.price}` : null],
  ].filter(([, value]) => !!value) as [string, string][];

  return (
    <View style={styles.card} accessibilityLabel={`Read from your screenshot: ${candidate.display_name}`}>
      <Text style={styles.eyebrow}>WHAT WE READ</Text>
      <Text style={styles.title}>{candidate.display_name}</Text>
      {facts.map(([label, value]) => (
        <Text key={label} style={styles.fact}>{label}: {value}</Text>
      ))}
      {candidate.price == null && <Text style={styles.warn}>No price was visible. Add it for a cost-per-wear estimate.</Text>}
      {candidate.uncertain_fields.length > 0 && (
        <Text style={styles.warn}>Check these before deciding: {candidate.uncertain_fields.join(', ')}.</Text>
      )}
      <Text style={styles.note}>{candidate.note}</Text>
      <View style={styles.row}>
        {onEdit && <TouchableOpacity accessibilityRole="button" accessibilityLabel={`Correct ${candidate.display_name}`} onPress={onEdit} style={styles.outline}><Text style={styles.outlineText}>Correct it</Text></TouchableOpacity>}
        {onConfirm && <TouchableOpacity accessibilityRole="button" accessibilityLabel={`Looks right, check ${candidate.display_name}`} onPress={onConfirm} style={[styles.primary, { flex: 1, marginTop: 0 }]}><Text style={styles.primaryText}>Looks right</Text></TouchableOpacity>}
      </View>
    </View>
  );
}

export function VerdictCard({ evaluation }: { evaluation: PurchaseEvaluation }) {
  const meta = VERDICT_META[evaluation.verdict];
  return (
    <View style={[styles.verdict, { backgroundColor: meta.background }]} accessibilityLabel={`Verdict: ${meta.label}`}>
      <View style={styles.verdictHead}>
        <Ionicons name={meta.icon as any} size={26} color={meta.colour} />
        <Text style={[styles.verdictLabel, { color: meta.colour }]}>{meta.label}</Text>
        <View style={styles.roiPill}>
          <Text style={styles.roiPillText}>ROI {roiPercent(evaluation.appearance_roi.score)}%</Text>
        </View>
      </View>
      <Text style={styles.verdictBlurb}>{meta.blurb}</Text>
      <Text style={styles.body}>{evaluation.summary}</Text>
    </View>
  );
}

export function ROIBreakdown({ roi }: { roi: AppearanceROI }) {
  return (
    <View style={styles.card} accessibilityLabel="Appearance ROI breakdown">
      <Text style={styles.eyebrow}>APPEARANCE ROI · {roi.version}</Text>
      <Text style={styles.title}>How this was worked out</Text>
      <Text style={styles.note}>{roi.formula}</Text>
      {roi.factors.map((factor) => (
        <View key={factor.key} style={styles.factor} accessibilityLabel={`${factor.label}, ${roiPercent(factor.value)} percent`}>
          <View style={styles.factorHead}>
            <Text style={styles.factorLabel}>{factor.label}</Text>
            <Text style={styles.factorValue}>{roiPercent(factor.value)}%</Text>
          </View>
          <View style={styles.bar}>
            <View style={[styles.barFill, { width: `${Math.max(2, roiPercent(factor.value))}%` }]} />
          </View>
          <Text style={styles.factorWhy}>{factor.explanation}</Text>
        </View>
      ))}
      <Text style={styles.note}>
        Buy at {roiPercent(roi.thresholds.buy)}% and above, Wait from {roiPercent(roi.thresholds.wait)}%, Skip below that.
      </Text>
    </View>
  );
}

export function NewCombinations({ count }: { count: number }) {
  return (
    <View style={styles.card} accessibilityLabel={`${count} new outfit combinations`}>
      <Text style={styles.title}>{count} new outfit{count === 1 ? '' : 's'}</Text>
      <Text style={styles.body}>
        {count > 0
          ? 'That is how many workable combinations this would add to what you already own — counting only pairings where the colours and the formality actually match.'
          : 'This would not combine with anything you have recorded, so it would be worn on its own.'}
      </Text>
    </View>
  );
}

export function OwnedComparisons({ similar, alternatives }: {
  similar: OwnedReference[]; alternatives: OwnedReference[];
}) {
  if (!similar.length && !alternatives.length) return null;
  return (
    <View style={styles.card} accessibilityLabel="What you already own">
      {similar.length > 0 && (
        <>
          <Text style={styles.title}>You already own something very like this</Text>
          {similar.map((row) => (
            <Text key={row.inventory_item_id} style={styles.fact}>• {row.display_name}{row.brand ? ` (${row.brand})` : ''}</Text>
          ))}
        </>
      )}
      {alternatives.length > 0 && (
        <>
          <Text style={[styles.title, similar.length ? { marginTop: SPACING.md } : null]}>Things you own that could do the same job</Text>
          {alternatives.map((row) => (
            <Text key={row.inventory_item_id} style={styles.fact}>• {row.display_name}{row.brand ? ` (${row.brand})` : ''}</Text>
          ))}
        </>
      )}
    </View>
  );
}

export function RiskNotes({ evaluation }: { evaluation: PurchaseEvaluation }) {
  const groups: [string, string[]][] = [
    ['Fit', evaluation.fit_risks],
    ['Colour', evaluation.colour_risks],
    ['Climate', evaluation.climate_notes],
    ['Missing information', evaluation.missing_information],
  ];
  const shown = groups.filter(([, rows]) => rows.length > 0);
  if (!shown.length) return null;
  return (
    <View style={styles.card} accessibilityLabel="Things to check">
      <Text style={styles.title}>Things to check</Text>
      {shown.map(([label, rows]) => (
        <View key={label} style={{ marginTop: SPACING.sm }}>
          <Text style={styles.factorLabel}>{label}</Text>
          {rows.map((row, index) => <Text key={index} style={styles.fact}>• {row}</Text>)}
        </View>
      ))}
    </View>
  );
}

export function DecisionActions({ current, onDecide, busy = false }: {
  current?: string | null; onDecide: (decision: 'bought' | 'waiting' | 'skipped') => void; busy?: boolean;
}) {
  return (
    <View style={styles.card} accessibilityLabel="Save your decision">
      <Text style={styles.title}>What did you decide?</Text>
      <Text style={styles.body}>Saving this helps the next check. You can disagree with us — that is useful too.</Text>
      <View style={styles.row}>
        {DECISION_OPTIONS.map((option) => {
          const active = current === option.key;
          return (
            <TouchableOpacity
              key={option.key}
              accessibilityRole="button"
              accessibilityLabel={option.label}
              accessibilityState={{ selected: active, disabled: busy }}
              disabled={busy}
              onPress={() => onDecide(option.key)}
              style={[styles.outline, active && styles.outlineActive, busy && styles.disabled]}
            >
              <Text style={[styles.outlineText, active && styles.outlineTextActive]}>{option.label}</Text>
            </TouchableOpacity>
          );
        })}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: { backgroundColor: COLORS.card, borderRadius: RADIUS.xl, padding: SPACING.lg, borderWidth: 1, borderColor: COLORS.border, marginBottom: SPACING.md },
  eyebrow: { fontFamily: FONTS.family.bodySemibold, color: COLORS.accent, fontSize: 10, letterSpacing: 1.2, textTransform: 'uppercase' },
  title: { fontFamily: FONTS.family.headingMedium, fontSize: 18, color: COLORS.textPrimary, marginTop: 4 },
  body: { fontFamily: FONTS.family.body, fontSize: 13, lineHeight: 19, color: COLORS.textSecondary, marginTop: 6 },
  fact: { fontFamily: FONTS.family.body, fontSize: 12, lineHeight: 18, color: COLORS.textSecondary, marginTop: 3 },
  note: { fontFamily: FONTS.family.body, fontSize: 11, lineHeight: 16, color: COLORS.textMuted, marginTop: 8 },
  warn: { fontFamily: FONTS.family.bodyMedium, fontSize: 12, lineHeight: 18, color: COLORS.warning, marginTop: 6 },
  verdict: { borderRadius: RADIUS.xl, padding: SPACING.lg, marginBottom: SPACING.md },
  verdictHead: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  verdictLabel: { fontFamily: FONTS.family.heading, fontSize: 26, flex: 1 },
  roiPill: { backgroundColor: COLORS.white, borderRadius: RADIUS.full, paddingHorizontal: 10, paddingVertical: 4 },
  roiPillText: { fontFamily: FONTS.family.bodySemibold, fontSize: 11, color: COLORS.textPrimary },
  verdictBlurb: { fontFamily: FONTS.family.bodyMedium, fontSize: 14, color: COLORS.textPrimary, marginTop: 6 },
  factor: { marginTop: SPACING.md },
  factorHead: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  factorLabel: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.textPrimary },
  factorValue: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.primary },
  bar: { height: 6, borderRadius: 3, backgroundColor: COLORS.backgroundTertiary, marginTop: 5, overflow: 'hidden' },
  barFill: { height: 6, borderRadius: 3, backgroundColor: COLORS.primary },
  factorWhy: { fontFamily: FONTS.family.body, fontSize: 11, lineHeight: 16, color: COLORS.textSecondary, marginTop: 4 },
  row: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: SPACING.md },
  primary: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, backgroundColor: COLORS.primary, borderRadius: RADIUS.full, paddingHorizontal: 20, paddingVertical: 13, marginTop: SPACING.md },
  primaryText: { fontFamily: FONTS.family.bodySemibold, fontSize: 14, color: COLORS.white },
  disabled: { opacity: 0.6 },
  outline: { alignItems: 'center', justifyContent: 'center', borderRadius: RADIUS.full, paddingHorizontal: 16, paddingVertical: 11, borderWidth: 1, borderColor: COLORS.border },
  outlineActive: { backgroundColor: COLORS.primary, borderColor: COLORS.primary },
  outlineText: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.textPrimary },
  outlineTextActive: { color: COLORS.white },
  link: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.primary, textAlign: 'center', marginTop: SPACING.md },
});
