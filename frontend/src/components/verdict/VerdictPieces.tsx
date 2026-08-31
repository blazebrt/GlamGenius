/**
 * The verdict, on screen.
 *
 * The constraint that shapes everything here: someone who has never seen this
 * app should be able to say "buy or not" from the colour alone, in under three
 * seconds, without reading. So the colour block is the screen — not a chip, not
 * a border, not an accent on a card. Text is what you read *after* you have
 * already decided.
 *
 * Every string comes from src/strings/verdict.ts. Nothing here is written
 * inline.
 */
import React from 'react';
import { Linking, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';

import { S, t } from '../../strings/verdict';
import type {
  ColourBand, VerdictComponent, VerdictIngredient, VerdictView,
  VerdictFactor,
} from '../../services/verdictModel';
import { COLORS, FONTS, RADIUS, SPACING } from '../../theme/colors';

/** The three answers, as colour. Chosen for contrast against the letter, not decoration. */
export const BAND_COLOURS: Record<ColourBand, { fill: string; ink: string; name: string }> = {
  green: { fill: '#0F7A46', ink: '#FFFFFF', name: S.a11y.colourGreen },
  yellow: { fill: '#E8B500', ink: '#1C1917', name: S.a11y.colourYellow },
  red: { fill: '#C0271C', ink: '#FFFFFF', name: S.a11y.colourRed },
};

const TIER_LABELS: Record<VerdictIngredient['tier'], string> = {
  plain: S.ingredients.tierPlain,
  green: S.ingredients.tierGreen,
  amber: S.ingredients.tierAmber,
  red: S.ingredients.tierRed,
  black: S.ingredients.tierBlack,
};

const INGREDIENT_STATUS: Record<string, string> = {
  no_concern_found: S.ingredients.noConcernFound,
  worth_knowing: S.ingredients.worthKnowing,
  worth_caution: S.ingredients.worthCaution,
  flagged: S.ingredients.flagged,
  not_permitted: S.ingredients.notPermitted,
  not_enough_information: S.ingredients.notEnoughInformation,
};

const TIER_DOTS: Record<VerdictIngredient['tier'], string> = {
  plain: COLORS.textMuted,
  green: BAND_COLOURS.green.fill,
  amber: BAND_COLOURS.yellow.fill,
  red: BAND_COLOURS.red.fill,
  black: '#1C1917',
};

/**
 * The letter, at the size the answer deserves.
 *
 * The colour fills the block behind it, so the answer arrives peripherally,
 * before the eye has focused on anything. The letter is the confirmation.
 */
export function GradeBlock({ view }: { view: VerdictView }) {
  const colour = BAND_COLOURS[view.band];
  return (
    <View
      style={[styles.block, { backgroundColor: colour.fill }]}
      accessibilityLabel={
        view.letter
          ? t(S.a11y.gradeBadge, { letter: view.letter, verdict: view.verdict })
          : view.verdict
      }
    >
      {view.letter ? (
        <Text style={[styles.letter, { color: colour.ink }]}>{view.letter}</Text>
      ) : (
        <Ionicons name="help-outline" size={72} color={colour.ink} />
      )}
      <Text style={[styles.verdictWord, { color: colour.ink }]}>{view.verdict}</Text>
    </View>
  );
}

/** The three lines. Never a fourth. */
export function VerdictLines({
  view, onReport,
}: {
  view: VerdictView;
  onReport: (subject: string) => void;
}) {
  return (
    <View style={styles.lines}>
      <Text style={styles.action}>{view.action}</Text>

      {!!view.everydayNumber && (
        <View style={styles.numberRow}>
          <Text style={styles.number}>{view.everydayNumber}</Text>
          {/* One tap, from the number itself. The only labelled target on the
              row, so a screen reader reads the number then the one action. */}
          <TouchableOpacity
            accessibilityRole="button"
            accessibilityLabel={t(S.a11y.report, { subject: view.everydayNumber })}
            onPress={() => onReport(view.everydayNumber)}
            hitSlop={16}
          >
            <Text style={styles.reportInline}>{S.report.triggerShort}</Text>
          </TouchableOpacity>
        </View>
      )}

      {!!view.alternativeLine && (
        <View style={styles.alternative}>
          <Text style={styles.alternativeLead}>{S.primary.alternativeLead}</Text>
          <Text style={styles.alternativeText}>{view.alternativeLine}</Text>
        </View>
      )}
    </View>
  );
}

/** Why, listen, share. Three targets, all thumb-sized. */
export function VerdictActions({
  onWhy, onListen, onShare, speaking, speechAvailable,
}: {
  onWhy: () => void;
  onListen: () => void;
  onShare: () => void;
  speaking: boolean;
  speechAvailable: boolean;
}) {
  return (
    <View style={styles.actions}>
      <TouchableOpacity
        accessibilityRole="button" accessibilityLabel={S.a11y.why}
        onPress={onWhy} style={[styles.actionButton, styles.actionPrimary]}
      >
        <Ionicons name="information-circle-outline" size={22} color={COLORS.white} />
        <Text style={styles.actionPrimaryText}>{S.primary.why}</Text>
      </TouchableOpacity>

      <TouchableOpacity
        accessibilityRole="button"
        accessibilityLabel={speaking ? S.a11y.stop : S.a11y.listen}
        onPress={onListen} disabled={!speechAvailable}
        style={[styles.actionButton, !speechAvailable && styles.actionDisabled]}
      >
        <Ionicons
          name={speaking ? 'stop-circle-outline' : 'volume-high-outline'}
          size={22} color={COLORS.primary}
        />
        <Text style={styles.actionText}>
          {speaking ? S.primary.stopListening : S.primary.listen}
        </Text>
      </TouchableOpacity>

      <TouchableOpacity
        accessibilityRole="button" accessibilityLabel={S.a11y.share}
        onPress={onShare} style={styles.actionButton}
      >
        <Ionicons name="share-social-outline" size={22} color={COLORS.primary} />
        <Text style={styles.actionText}>{S.primary.share}</Text>
      </TouchableOpacity>
    </View>
  );
}

/** One of the four things that decided the letter. Tap for the rule and the source. */
export function ComponentRow({
  component, expanded, onToggle,
}: {
  component: VerdictComponent;
  expanded: boolean;
  onToggle: () => void;
}) {
  const colour = BAND_COLOURS[component.band];
  return (
    <View style={styles.component}>
      <TouchableOpacity
        accessibilityRole="button"
        accessibilityLabel={t(
          expanded ? S.a11y.collapseComponent : S.a11y.expandComponent,
          { label: component.label },
        )}
        onPress={onToggle}
        style={styles.componentHead}
      >
        <View style={[styles.dot, { backgroundColor: colour.fill }]} />
        <View style={{ flex: 1 }}>
          <Text style={styles.componentLabel}>{component.label}</Text>
          <Text style={styles.componentPlain}>{component.plain}</Text>
        </View>
        <Ionicons
          name={expanded ? 'chevron-up' : 'chevron-down'}
          size={18} color={COLORS.textMuted}
        />
      </TouchableOpacity>

      {(component.band === 'red' || component.band === 'yellow') && !!component.source && (
        <TouchableOpacity
          accessibilityRole="link"
          accessibilityLabel={`${S.why.sourceLead}: ${component.source}`}
          disabled={!component.sourceUrl}
          onPress={() => { if (component.sourceUrl) void Linking.openURL(component.sourceUrl); }}
        >
          <Text style={styles.bodySource}>{component.source}</Text>
        </TouchableOpacity>
      )}

      {expanded && (
        <View style={styles.componentBody}>
          {!!component.term && (
            // The one place a technical word may appear, and only with its
            // explanation attached to it.
            <Text style={styles.term}>
              <Text style={styles.termWord}>{component.term.word}</Text>
              {` — ${component.term.plain}`}
            </Text>
          )}
          <Text style={styles.bodyLead}>{S.why.ruleLead}</Text>
          <Text style={styles.bodyText}>{component.rule}</Text>
          <Text style={styles.bodyLead}>{S.why.sourceLead}</Text>
          <Text style={styles.bodySource}>{component.source}</Text>
        </View>
      )}
    </View>
  );
}

/** Default-visible evidence facts. Details are separate from the source link. */
export function FactorSection({
  title, rows, empty, onExplain,
}: {
  title: string;
  rows: VerdictFactor[];
  empty: string;
  onExplain: (row: VerdictFactor) => void;
}) {
  return (
    <View style={styles.factorSection}>
      <Text style={styles.sectionHeading}>{title}</Text>
      {rows.length === 0 && <Text style={styles.empty}>{empty}</Text>}
      {rows.map((row) => (
        <View key={row.key} style={styles.factor}>
          <View style={[styles.dot, { backgroundColor: BAND_COLOURS[row.band].fill }]} />
          <View style={{ flex: 1, gap: 2 }}>
            {/*
              The name of the thing comes first. A row that opens with "High"
              and a number leaves the reader to work out high *what* — which is
              the one question the row exists to answer.
            */}
            <Text style={styles.factorName}>
              {S.factors[`label_${row.label}` as keyof typeof S.factors] ?? row.label}
            </Text>
            <Text style={styles.factorStatus}>{S.factors[row.status as keyof typeof S.factors] ?? row.status}</Text>
            {!!row.quantity && (
              <Text style={styles.factorQuantity}>
                {`${row.quantity.value} ${row.quantity.unit} ${S.factors[row.quantity.basis]}`}
              </Text>
            )}
            <Text style={styles.ingredientDescription}>{S.factors[row.explanation as keyof typeof S.factors] ?? row.explanation}</Text>
            {row.sources.map((source) => (
              <TouchableOpacity key={source.name} accessibilityRole="link" disabled={!source.url}
                onPress={() => { if (source.url) void Linking.openURL(source.url); }}>
                <Text style={styles.bodySource}>{source.name}</Text>
              </TouchableOpacity>
            ))}
          </View>
          <TouchableOpacity accessibilityRole="button" accessibilityLabel={S.factors.details}
            onPress={() => onExplain(row)} hitSlop={12}>
            <Ionicons name="help-circle-outline" size={20} color={COLORS.primary} />
          </TouchableOpacity>
        </View>
      ))}
    </View>
  );
}

/** Every ingredient, free, with what it does in plain words. */
export function IngredientList({
  ingredients, onReport, onExplain,
}: {
  ingredients: VerdictIngredient[];
  onReport: (subject: string) => void;
  onExplain: (ingredient: VerdictIngredient) => void;
}) {
  if (ingredients.length === 0) {
    return <Text style={styles.empty}>{S.ingredients.empty}</Text>;
  }
  return (
    <View>
      <Text style={styles.orderNote}>{S.ingredients.orderNote}</Text>
      {ingredients.map((row, index) => {
        const shown = row.label || row.name;
        const source = row.sources?.find((entry) => !!entry.url) ?? null;
        return (
          <View
            key={`${row.name}-${index}`}
            style={styles.ingredient}
            accessibilityLabel={t(S.a11y.ingredientRow, {
              name: shown, tier: TIER_LABELS[row.tier], description: row.description,
            })}
          >
            <View style={[styles.dot, { backgroundColor: TIER_DOTS[row.tier] }]} />
            <View style={{ flex: 1 }}>
              <Text style={styles.ingredientName}>{shown}</Text>
              <Text style={styles.ingredientTier}>
                {INGREDIENT_STATUS[row.status ?? ''] ?? TIER_LABELS[row.tier]}
              </Text>
              {/*
                The row explains itself. Everything below this line is an extra
                a person may reach for, never the only place the basic
                explanation exists.
              */}
              <Text style={styles.ingredientDescription}>{row.description}</Text>
              {!!row.whyFlagged && (
                <Text style={styles.ingredientDescription}>{row.whyFlagged}</Text>
              )}

              {/*
                Three separate controls, three separate handlers. Opening an
                authority, asking us to explain ourselves, and telling us we
                are wrong are different requests and must not share a target.
              */}
              <View style={styles.ingredientActions}>
                {!!source && (
                  <TouchableOpacity
                    accessibilityRole="link"
                    accessibilityLabel={t(S.a11y.openSource, { name: shown })}
                    onPress={() => { if (source.url) void Linking.openURL(source.url); }}
                    hitSlop={8}
                  >
                    <Text style={styles.actionLink}>{S.why.sourceLink}</Text>
                  </TouchableOpacity>
                )}
                <TouchableOpacity
                  accessibilityRole="button"
                  accessibilityLabel={t(S.a11y.explain, { name: shown })}
                  onPress={() => onExplain(row)}
                  hitSlop={8}
                >
                  <Text style={styles.actionLink}>{S.ingredients.explainAction}</Text>
                </TouchableOpacity>
                <TouchableOpacity
                  accessibilityRole="button"
                  accessibilityLabel={t(S.a11y.report, { subject: shown })}
                  onPress={() => onReport(shown)}
                  hitSlop={8}
                >
                  <Text style={styles.actionLink}>{S.report.triggerShort}</Text>
                </TouchableOpacity>
              </View>
            </View>
          </View>
        );
      })}
    </View>
  );
}

/**
 * What the `?` opens: the full basis for one ingredient row.
 *
 * Seven sections, each present only when there is something true to put in it.
 * A missing section says so rather than being filled with a guess.
 */
export function IngredientDetail({ ingredient }: { ingredient: VerdictIngredient }) {
  const detail = ingredient.detail;
  const shown = ingredient.label || ingredient.name;
  const source = detail?.source ?? ingredient.sources?.find((row) => !!row.url) ?? null;
  const sections: { lead: string; body: string }[] = [
    { lead: S.ingredients.whatItDoes, body: detail?.whatItDoes || ingredient.description },
    { lead: S.ingredients.whyFlagged, body: detail?.whyFlagged || S.ingredients.noNote },
    { lead: S.ingredients.exactRule, body: detail?.rule || S.ingredients.noRule },
    {
      lead: S.ingredients.authorityPosition,
      body: detail?.authorityPosition || source?.name || S.ingredients.noAuthority,
    },
    {
      lead: S.ingredients.ourInterpretation,
      body: detail?.interpretation || S.ingredients.noInterpretation,
    },
    {
      lead: S.ingredients.evidenceStatus,
      body: detail?.evidenceStatus || S.ingredients.unreviewedRule,
    },
  ];
  return (
    <View style={styles.card} accessibilityLabel={S.ingredients.explainTitle}>
      <Text style={styles.cardTitle}>{shown}</Text>
      {sections.map((section) => (
        <View key={section.lead}>
          <Text style={styles.bodyLead}>{section.lead}</Text>
          <Text style={styles.bodyText}>{section.body}</Text>
        </View>
      ))}
      {!!source?.url && (
        <TouchableOpacity
          accessibilityRole="link"
          accessibilityLabel={t(S.a11y.openSource, { name: shown })}
          onPress={() => void Linking.openURL(source.url as string)}
        >
          <Text style={styles.bodySource}>{S.ingredients.openSource}</Text>
        </TouchableOpacity>
      )}
    </View>
  );
}

export function NotGradedCard({
  quantity, purity,
}: {
  quantity?: string | null;
  purity?: string | null;
}) {
  return (
    <View style={styles.card}>
      <Text style={styles.cardTitle}>{S.notGraded.title}</Text>
      <Text style={styles.bodyText}>{S.notGraded.body}</Text>
      {!!quantity && (
        <>
          <Text style={styles.bodyLead}>{S.notGraded.quantityLead}</Text>
          <Text style={styles.bodyText}>{quantity}</Text>
        </>
      )}
      {!!purity && (
        <>
          <Text style={styles.bodyLead}>{S.notGraded.purityLead}</Text>
          <Text style={styles.bodyText}>{purity}</Text>
        </>
      )}
    </View>
  );
}

export function UnknownCard({ missing, onSendPhoto }: { missing: string[]; onSendPhoto: () => void }) {
  return (
    <View style={styles.card}>
      <Text style={styles.cardTitle}>{S.unknown.title}</Text>
      <Text style={styles.bodyText}>{S.unknown.body}</Text>
      {missing.length > 0 && (
        <>
          <Text style={styles.bodyLead}>{S.unknown.missingLead}</Text>
          <Text style={styles.bodyText}>{missing.join(', ')}</Text>
        </>
      )}
      <TouchableOpacity
        accessibilityRole="button" accessibilityLabel={S.unknown.helpUs}
        onPress={onSendPhoto} style={styles.cardButton}
      >
        <Text style={styles.cardButtonText}>{S.unknown.helpUs}</Text>
      </TouchableOpacity>
    </View>
  );
}

/** The report sheet: structured options, a photo, no typing required. */
export const REPORT_OPTIONS = [
  { reason: 'wrong_number' as const, label: S.report.optionWrongNumber },
  { reason: 'wrong_ingredient' as const, label: S.report.optionWrongIngredient },
  { reason: 'wrong_product' as const, label: S.report.optionWrongProduct },
  { reason: 'wrong_grade' as const, label: S.report.optionWrongGrade },
  { reason: 'pack_changed' as const, label: S.report.optionPackChanged },
  { reason: 'something_else' as const, label: S.report.optionSomethingElse },
];

export function ReportSheet({
  subject, onPick, onCancel, onAddPhoto, photoAdded, busy, status,
}: {
  subject: string;
  onPick: (reason: (typeof REPORT_OPTIONS)[number]['reason']) => void;
  onCancel: () => void;
  onAddPhoto: () => void;
  photoAdded: boolean;
  busy: boolean;
  status: string | null;
}) {
  return (
    <ScrollView style={styles.sheet} contentContainerStyle={{ paddingBottom: SPACING.lg }}>
      <Text style={styles.cardTitle}>{S.report.title}</Text>
      <Text style={styles.bodyText}>{subject}</Text>
      <Text style={styles.sheetSubtitle}>{S.report.subtitle}</Text>

      {REPORT_OPTIONS.map((option) => (
        <TouchableOpacity
          key={option.reason}
          accessibilityRole="button" accessibilityLabel={option.label}
          onPress={() => onPick(option.reason)} disabled={busy}
          style={styles.option}
        >
          <Text style={styles.optionText}>{option.label}</Text>
          <Ionicons name="chevron-forward" size={17} color={COLORS.textMuted} />
        </TouchableOpacity>
      ))}

      <TouchableOpacity
        accessibilityRole="button"
        accessibilityLabel={photoAdded ? S.report.retakePhoto : S.report.addPhoto}
        onPress={onAddPhoto} style={styles.photoButton}
      >
        <Ionicons name="camera-outline" size={19} color={COLORS.primary} />
        <Text style={styles.actionText}>
          {photoAdded ? S.report.photoAdded : S.report.addPhoto}
        </Text>
      </TouchableOpacity>

      {!!status && <Text style={styles.status}>{status}</Text>}

      <TouchableOpacity
        accessibilityRole="button" accessibilityLabel={S.report.cancel}
        onPress={onCancel} style={styles.cancel}
      >
        <Text style={styles.cancelText}>{S.report.cancel}</Text>
      </TouchableOpacity>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  factorName: {
    color: COLORS.textPrimary, fontFamily: FONTS.family.bodySemibold, fontSize: 16,
  },
  ingredientActions: {
    flexDirection: 'row', gap: SPACING.md, marginTop: 6, flexWrap: 'wrap',
  },
  actionLink: {
    color: COLORS.primary, fontFamily: FONTS.family.bodySemibold, fontSize: 13,
  },
  block: {
    borderRadius: RADIUS.lg, alignItems: 'center', justifyContent: 'center',
    paddingVertical: SPACING.xl, gap: 4,
  },
  letter: { fontFamily: FONTS.family.heading, fontSize: 132, lineHeight: 140 },
  verdictWord: { fontFamily: FONTS.family.bodySemibold, fontSize: 26, letterSpacing: 0.5 },
  lines: { marginTop: SPACING.lg, gap: SPACING.sm },
  action: { fontFamily: FONTS.family.heading, fontSize: 24, color: COLORS.textPrimary },
  numberRow: { flexDirection: 'row', alignItems: 'center', gap: SPACING.sm },
  number: { flex: 1, fontFamily: FONTS.family.bodyMedium, fontSize: 18, color: COLORS.textSecondary },
  reportInline: { fontFamily: FONTS.family.bodySemibold, fontSize: 12, color: COLORS.primary },
  alternative: {
    backgroundColor: COLORS.primaryLight, borderRadius: RADIUS.md, padding: SPACING.md, gap: 2,
  },
  alternativeLead: {
    fontFamily: FONTS.family.bodySemibold, fontSize: 11, letterSpacing: 1.2, color: COLORS.primary,
  },
  alternativeText: { fontFamily: FONTS.family.bodyMedium, fontSize: 17, color: COLORS.textPrimary },
  actions: { flexDirection: 'row', gap: SPACING.sm, marginTop: SPACING.lg },
  actionButton: {
    flex: 1, minHeight: 56, borderRadius: RADIUS.md, alignItems: 'center',
    justifyContent: 'center', gap: 4, borderWidth: 1, borderColor: COLORS.primary,
    paddingVertical: SPACING.sm,
  },
  actionPrimary: { backgroundColor: COLORS.primary },
  actionPrimaryText: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.white },
  actionText: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.primary },
  actionDisabled: { opacity: 0.4 },
  component: {
    backgroundColor: COLORS.card, borderRadius: RADIUS.lg, borderWidth: 1,
    borderColor: COLORS.border, marginTop: SPACING.sm, overflow: 'hidden',
  },
  factorSection: { marginTop: SPACING.lg, gap: SPACING.sm },
  sectionHeading: { fontFamily: FONTS.family.heading, fontSize: 21, color: COLORS.textPrimary },
  factor: {
    flexDirection: 'row', alignItems: 'flex-start', gap: SPACING.sm,
    backgroundColor: COLORS.card, borderRadius: RADIUS.md, padding: SPACING.md,
    borderWidth: 1, borderColor: COLORS.border,
  },
  factorStatus: { fontFamily: FONTS.family.bodySemibold, fontSize: 14, color: COLORS.textPrimary },
  factorQuantity: { fontFamily: FONTS.family.bodyMedium, fontSize: 13, color: COLORS.textSecondary },
  componentHead: { flexDirection: 'row', alignItems: 'center', gap: SPACING.sm, padding: SPACING.md },
  dot: { width: 14, height: 14, borderRadius: 7 },
  componentLabel: { fontFamily: FONTS.family.bodySemibold, fontSize: 16, color: COLORS.textPrimary },
  componentPlain: { fontFamily: FONTS.family.body, fontSize: 14, color: COLORS.textSecondary, marginTop: 2 },
  componentBody: {
    paddingHorizontal: SPACING.md, paddingBottom: SPACING.md, gap: 4,
    borderTopWidth: 1, borderTopColor: COLORS.borderLight, paddingTop: SPACING.sm,
  },
  term: { fontFamily: FONTS.family.body, fontSize: 13, color: COLORS.textSecondary, marginBottom: 4 },
  termWord: { fontFamily: FONTS.family.bodySemibold, color: COLORS.textPrimary },
  bodyLead: {
    fontFamily: FONTS.family.bodySemibold, fontSize: 11, letterSpacing: 1.1,
    color: COLORS.textMuted, marginTop: 6,
  },
  bodyText: { fontFamily: FONTS.family.body, fontSize: 14, lineHeight: 21, color: COLORS.textSecondary },
  bodySource: { fontFamily: FONTS.family.body, fontSize: 12, lineHeight: 18, color: COLORS.textMuted },
  orderNote: { fontFamily: FONTS.family.body, fontSize: 13, color: COLORS.textMuted, marginBottom: SPACING.sm },
  ingredient: {
    flexDirection: 'row', alignItems: 'flex-start', gap: SPACING.sm,
    paddingVertical: SPACING.sm, borderBottomWidth: 1, borderBottomColor: COLORS.borderLight,
  },
  ingredientName: { fontFamily: FONTS.family.bodySemibold, fontSize: 15, color: COLORS.textPrimary },
  ingredientTier: { fontFamily: FONTS.family.bodyMedium, fontSize: 12, color: COLORS.textSecondary, marginTop: 1 },
  ingredientDescription: { fontFamily: FONTS.family.body, fontSize: 13, color: COLORS.textSecondary, marginTop: 2 },
  empty: { fontFamily: FONTS.family.body, fontSize: 14, color: COLORS.textSecondary },
  card: {
    backgroundColor: COLORS.card, borderRadius: RADIUS.lg, padding: SPACING.lg,
    borderWidth: 1, borderColor: COLORS.border, gap: 4, marginTop: SPACING.md,
  },
  cardTitle: { fontFamily: FONTS.family.heading, fontSize: 22, color: COLORS.textPrimary },
  cardButton: {
    backgroundColor: COLORS.primary, borderRadius: RADIUS.md, paddingVertical: 14,
    alignItems: 'center', marginTop: SPACING.sm,
  },
  cardButtonText: { fontFamily: FONTS.family.bodySemibold, fontSize: 15, color: COLORS.white },
  sheet: { backgroundColor: COLORS.background, padding: SPACING.lg },
  sheetSubtitle: {
    fontFamily: FONTS.family.body, fontSize: 13, color: COLORS.textMuted, marginBottom: SPACING.sm,
  },
  option: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    backgroundColor: COLORS.card, borderRadius: RADIUS.md, borderWidth: 1,
    borderColor: COLORS.border, padding: SPACING.md, marginTop: SPACING.sm, minHeight: 54,
  },
  optionText: { fontFamily: FONTS.family.bodyMedium, fontSize: 15, color: COLORS.textPrimary },
  photoButton: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8,
    borderRadius: RADIUS.md, borderWidth: 1, borderColor: COLORS.primary,
    paddingVertical: 14, marginTop: SPACING.md,
  },
  status: {
    fontFamily: FONTS.family.bodyMedium, fontSize: 13, color: COLORS.primary,
    marginTop: SPACING.md, textAlign: 'center',
  },
  cancel: { paddingVertical: 14, alignItems: 'center', marginTop: SPACING.sm },
  cancelText: { fontFamily: FONTS.family.bodySemibold, fontSize: 14, color: COLORS.textSecondary },
});
