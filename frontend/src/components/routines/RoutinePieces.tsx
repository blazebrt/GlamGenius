/**
 * Phase 6 building blocks: routines, warnings, shelf attention, perfume,
 * supplements and food context.
 *
 * Two rules run through all of it.
 *
 * A **warning is only ever rendered from a rule the server sent**. There is no
 * copy in this file that invents a caution, and `RuleWarning.rule_id` is
 * displayed so a person can ask what it came from.
 *
 * A **module with nothing in it renders nothing at all**. Every component here
 * returns `null` on empty rather than an encouraging placeholder — the brief is
 * explicit that a user who has not populated a module should not be shown it.
 */
import React, { useState } from 'react';
import { StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';

import {
  CareProductControl, ExpiringReport, NutritionSuggestion, PerfumePick, Routine, RoutineStep,
  RuleWarning, ShelfProductRow, SupplementRow,
} from '../../services/apiV2';
import { COLORS, FONTS, RADIUS, SPACING } from '../../theme/colors';

const SEVERITY_COLOUR: Record<string, string> = {
  avoid: COLORS.error ?? '#B4231F',
  caution: COLORS.warning ?? '#B4231F',
  info: COLORS.primary,
};

const SEVERITY_ICON: Record<string, keyof typeof Ionicons.glyphMap> = {
  avoid: 'close-circle-outline',
  caution: 'alert-circle-outline',
  info: 'information-circle-outline',
};

/** How serious this is, in words rather than a colour alone. */
const SEVERITY_LABEL: Record<string, string> = {
  avoid: 'Leave this out',
  caution: 'Worth knowing',
  info: 'For information',
};

export function WarningCard({ warning }: { warning: RuleWarning }) {
  const colour = SEVERITY_COLOUR[warning.severity] ?? COLORS.primary;
  return (
    <View
      style={[styles.card, { borderLeftWidth: 3, borderLeftColor: colour }]}
      accessibilityLabel={`${SEVERITY_LABEL[warning.severity] ?? 'Note'}: ${warning.headline}`}
    >
      <View style={styles.row}>
        <Ionicons name={SEVERITY_ICON[warning.severity] ?? 'information-circle-outline'} size={18} color={colour} />
        <Text style={[styles.severity, { color: colour }]}>{SEVERITY_LABEL[warning.severity] ?? 'Note'}</Text>
      </View>
      <Text style={styles.cardTitle}>{warning.headline}</Text>
      <Text style={styles.body}>{warning.plain_english || warning.detail}</Text>
      {!!warning.evidence_note && <Text style={styles.evidence}>{warning.evidence_note}</Text>}
      {/* Shown, not hidden. If we cannot name the rule, we should not be warning. */}
      <Text style={styles.ruleId}>Rule: {warning.rule_id}</Text>
    </View>
  );
}

export function WarningList({ warnings }: { warnings: RuleWarning[] }) {
  if (!warnings.length) return null;
  return (
    <View accessibilityLabel="Things worth knowing">
      {warnings.map((warning, index) => (
        <WarningCard key={`${warning.rule_id}-${index}`} warning={warning} />
      ))}
    </View>
  );
}

export type CareProductAction = 'pause' | 'resume' | 'prefer' | 'unprefer';

export function StepRow({ step, onComplete, onFeedback, careProduct, careProducts = [], onCareAction, careActionBusy = false }: {
  step: RoutineStep;
  onComplete?: (step: RoutineStep) => void;
  onFeedback?: (step: RoutineStep) => void;
  careProduct?: CareProductControl;
  careProducts?: CareProductControl[];
  onCareAction?: (product: CareProductControl, action: CareProductAction) => void;
  careActionBusy?: boolean;
}) {
  const done = step.completed_today === true;
  const [choicesOpen, setChoicesOpen] = useState(false);
  const selectedProduct = careProduct;
  const sameSlotAlternatives = careProducts.filter((product) => (
    product.inventory_item_id !== selectedProduct?.inventory_item_id
    && product.slot === step.slot
    && product.eligible
    && !product.paused
  ));
  const action = (product: CareProductControl, value: CareProductAction) => {
    if (onCareAction) onCareAction(product, value);
  };
  return (
    <View style={[styles.step, step.is_gap && styles.stepGap]}>
      <View style={styles.row}>
        <Text style={styles.stepOrder}>{step.order / 10}</Text>
        <View style={{ flex: 1 }}>
          <Text style={styles.stepLabel}>{step.label}</Text>
          <Text style={step.is_gap ? styles.gapName : styles.stepProduct}>
            {step.product_name ?? 'Nothing recorded for this step yet'}
          </Text>
        </View>
        {!!onComplete && !step.is_gap && (
          <TouchableOpacity
            accessibilityRole="checkbox"
            accessibilityState={{ checked: done }}
            accessibilityLabel={`${done ? 'Undo' : 'Mark done'}: ${step.label}`}
            onPress={() => onComplete(step)}
            style={[styles.check, done && styles.checkDone]}
            hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
          >
            <Ionicons name={done ? 'checkmark' : 'ellipse-outline'} size={17} color={done ? COLORS.white : COLORS.textMuted} />
          </TouchableOpacity>
        )}
      </View>

      <Text style={styles.why}>{step.plain_english || step.why}</Text>
      <View style={styles.metaRow}>
        <Text style={styles.meta}>{step.frequency}</Text>
        <Text style={styles.meta}>·</Text>
        <Text style={styles.meta}>{step.required ? 'Worth keeping' : 'Optional'}</Text>
      </View>
      {!!onFeedback && !!step.id && !step.is_gap && (
        <TouchableOpacity
          accessibilityRole="button"
          accessibilityLabel={`Record experience for ${step.label}`}
          onPress={() => onFeedback(step)}
          style={styles.feedbackButton}
        >
          <Ionicons name="create-outline" size={15} color={COLORS.primary} />
          <Text style={styles.feedbackText}>Record experience</Text>
        </TouchableOpacity>
      )}
      {!!step.safety_note && <Text style={styles.safety}>{step.safety_note}</Text>}
      {!!step.climate_note && <Text style={styles.climate}>{step.climate_note}</Text>}
      {!!step.alternative && <Text style={styles.alternative}>If not: {step.alternative}</Text>}
      {!!selectedProduct && !step.is_gap && !!onCareAction && (
        <>
          <TouchableOpacity
            accessibilityRole="button"
            accessibilityLabel={`Routine choices for ${selectedProduct.display_name}`}
            accessibilityState={{ expanded: choicesOpen, disabled: careActionBusy }}
            onPress={() => setChoicesOpen((value) => !value)}
            disabled={careActionBusy}
            style={styles.choiceDisclosure}
          >
            <Ionicons name="options-outline" size={15} color={COLORS.primary} />
            <Text style={styles.feedbackText}>Routine choices</Text>
          </TouchableOpacity>
          {choicesOpen && (
            <View style={styles.choicePanel} accessibilityLabel={`Care choices for ${selectedProduct.display_name}`}>
              <Text style={styles.choiceHint}>These choices are reversible and the server keeps your routine safe.</Text>
              <Text style={styles.choiceTitle}>Current · {selectedProduct.display_name}</Text>
              <View style={styles.choiceRow}>
                <TouchableOpacity
                  accessibilityRole="button"
                  accessibilityLabel={`${selectedProduct.paused ? 'Resume' : 'Pause'} ${selectedProduct.display_name}`}
                  onPress={() => action(selectedProduct, selectedProduct.paused ? 'resume' : 'pause')}
                  disabled={careActionBusy}
                  style={styles.choiceButton}
                >
                  <Text style={styles.choiceButtonText}>{selectedProduct.paused ? 'Resume product' : 'Pause product'}</Text>
                </TouchableOpacity>
                {selectedProduct.eligible && <TouchableOpacity
                  accessibilityRole="button"
                  accessibilityLabel={`${selectedProduct.preferred ? 'Unprefer' : 'Prefer'} ${selectedProduct.display_name}`}
                  onPress={() => action(selectedProduct, selectedProduct.preferred ? 'unprefer' : 'prefer')}
                  disabled={careActionBusy}
                  style={styles.choiceButton}
                >
                  <Text style={styles.choiceButtonText}>{selectedProduct.preferred ? 'Unprefer product' : 'Prefer product'}</Text>
                </TouchableOpacity>}
              </View>
              {!!sameSlotAlternatives.length && <>
                <Text style={styles.choiceTitle}>Other products for {step.label.toLowerCase()}</Text>
                {sameSlotAlternatives.map((product) => (
                  <View key={product.inventory_item_id} style={styles.choiceProductRow}>
                    <Text style={styles.choiceProductName}>{product.display_name}</Text>
                    <TouchableOpacity
                      accessibilityRole="button"
                      accessibilityLabel={`${product.preferred ? 'Remove preference from' : 'Prefer'} ${product.display_name}`}
                      onPress={() => action(product, product.preferred ? 'unprefer' : 'prefer')}
                      disabled={careActionBusy}
                      style={styles.choiceButton}
                    >
                      <Text style={styles.choiceButtonText}>{product.preferred ? 'Remove preference' : 'Prefer'}</Text>
                    </TouchableOpacity>
                  </View>
                ))}
              </>}
            </View>
          )}
        </>
      )}
    </View>
  );
}

export function RoutineCard({ routine, onComplete, onFeedback, careProducts = [], onCareAction, careActionBusyId }: {
  routine: Routine;
  onComplete?: (step: RoutineStep) => void;
  onFeedback?: (step: RoutineStep) => void;
  careProducts?: CareProductControl[];
  onCareAction?: (product: CareProductControl, action: CareProductAction) => void;
  careActionBusyId?: string | null;
}) {
  return (
    <View style={styles.routine} accessibilityLabel={routine.label}>
      <Text style={styles.routineTitle}>{routine.label}</Text>
      <Text style={styles.routineFrequency}>{routine.frequency}</Text>
      {!!routine.summary && <Text style={styles.body}>{routine.summary}</Text>}

      {routine.steps.map((step) => (
        <StepRow
          key={step.id ?? step.slot}
          step={step}
          onComplete={onComplete}
          onFeedback={onFeedback}
          careProduct={careProducts.find((product) => product.inventory_item_id === step.inventory_item_id)}
          careProducts={careProducts}
          onCareAction={onCareAction}
          careActionBusy={careActionBusyId !== null && careActionBusyId === step.inventory_item_id}
        />
      ))}

      {!!routine.skipped_for_allergy.length && (
        <Text style={styles.skipped}>
          Left out because of what you told us to avoid: {routine.skipped_for_allergy.join(', ')}
        </Text>
      )}
      <WarningList warnings={routine.warnings} />
      <Text style={styles.disclaimer}>{routine.disclaimer}</Text>
    </View>
  );
}

export function ConsistencyCard({ consistency }: {
  consistency: { days_considered: number; days_with_activity: number; steps_completed: number; note: string };
}) {
  return (
    <View style={styles.card} accessibilityLabel="How the routine is going">
      <Text style={styles.cardTitle}>How it is going</Text>
      <View style={styles.statRow}>
        <View style={styles.stat}>
          <Text style={styles.statValue}>{consistency.days_with_activity}</Text>
          <Text style={styles.statLabel}>days with something done</Text>
        </View>
        <View style={styles.stat}>
          <Text style={styles.statValue}>{consistency.steps_completed}</Text>
          <Text style={styles.statLabel}>steps ticked off</Text>
        </View>
      </View>
      {/* No streak counter. A streak turns a missed Tuesday into a failure. */}
      <Text style={styles.evidence}>{consistency.note}</Text>
    </View>
  );
}

export function ExpiringSection({ report }: { report: ExpiringReport }) {
  const total = report.expired.length + report.expiring_soon.length;
  if (!total && !report.no_date_recorded.length) return null;
  const line = (row: ShelfProductRow, suffix: string) => (
    <Text key={row.inventory_item_id} style={styles.listRow}>• {row.display_name} — {suffix}</Text>
  );
  return (
    <View style={styles.card} accessibilityLabel="Products running out">
      <Text style={styles.cardTitle}>Dates worth watching</Text>
      {report.expired.map((row) => line(row, 'past its date'))}
      {report.expiring_soon.map((row) => line(row, `${row.days_to_expiry} days left`))}
      {!!report.no_date_recorded.length && (
        <Text style={styles.evidence}>
          {report.no_date_recorded.length} with no date recorded. Adding one lets us tell you when it runs out.
        </Text>
      )}
      <Text style={styles.evidence}>{report.note}</Text>
    </View>
  );
}

export function LowUseSection({ low }: {
  low: { products: ShelfProductRow[]; count: number; definition: string; note: string };
}) {
  if (!low.count) return null;
  return (
    <View style={styles.card} accessibilityLabel="Products you are not using">
      <Text style={styles.cardTitle}>Sitting unused · {low.count}</Text>
      {low.products.slice(0, 6).map((row) => (
        <Text key={row.inventory_item_id} style={styles.listRow}>• {row.display_name}</Text>
      ))}
      <Text style={styles.body}>{low.note}</Text>
      <Text style={styles.evidence}>{low.definition}</Text>
    </View>
  );
}

export function PerfumeCard({ pick }: { pick: PerfumePick }) {
  return (
    <View style={styles.card} accessibilityLabel={`Perfume suggestion: ${pick.display_name}`}>
      <Text style={styles.cardTitle}>{pick.display_name}</Text>
      {!!pick.fragrance_family && <Text style={styles.meta}>{pick.fragrance_family}</Text>}
      {pick.reasons.map((reason) => (
        <Text key={reason.rule_id} style={styles.body}>• {reason.note}</Text>
      ))}
      {pick.missing_information.map((note, index) => (
        <Text key={index} style={styles.evidence}>{note}</Text>
      ))}
    </View>
  );
}

export function SupplementCard({ row }: { row: SupplementRow }) {
  return (
    <View style={styles.card} accessibilityLabel={`Supplement: ${row.display_name}`}>
      <Text style={styles.cardTitle}>{row.display_name}</Text>
      {!!row.brand && <Text style={styles.meta}>{row.brand}</Text>}
      {/* Recorded verbatim. We never interpret what somebody said it is for. */}
      {!!row.user_entered_purpose && <Text style={styles.body}>You noted: {row.user_entered_purpose}</Text>}
      {!!row.use_frequency && <Text style={styles.meta}>{row.use_frequency}</Text>}
      <Text style={styles.meta}>
        {row.expiry_date ? `Dated ${row.expiry_date}` : 'No expiry date recorded'}
      </Text>
      {row.flags.map((flag) => (
        <Text key={flag.flag} style={styles.safety}>{flag.message}</Text>
      ))}
    </View>
  );
}

export function BoundaryNotice({ message }: { message: string }) {
  return (
    <View style={styles.boundary} accessibilityLabel="This one needs a professional">
      <Ionicons name="medical-outline" size={18} color={COLORS.primary} />
      <Text style={styles.boundaryText}>{message}</Text>
    </View>
  );
}

export function NutritionCard({ suggestion }: { suggestion: NutritionSuggestion }) {
  return (
    <View style={styles.card} accessibilityLabel={suggestion.title}>
      <Text style={styles.cardTitle}>{suggestion.title}</Text>
      <Text style={styles.body}>{suggestion.body}</Text>
    </View>
  );
}

export function PausedCareProducts({ products, onCareAction, careActionBusyId }: {
  products: CareProductControl[];
  onCareAction: (product: CareProductControl, action: CareProductAction) => void;
  careActionBusyId?: string | null;
}) {
  const paused = products.filter((product) => product.paused);
  if (!paused.length) return null;
  return (
    <View style={styles.card} accessibilityLabel="Paused Care products">
      <Text style={styles.cardTitle}>Products you paused</Text>
      <Text style={styles.body}>They stay on your shelf and can return whenever you choose.</Text>
      {paused.map((product) => (
        <View key={product.inventory_item_id} style={styles.pausedRow}>
          <Text style={styles.listRow}>{product.display_name}</Text>
          <TouchableOpacity
            accessibilityRole="button"
            accessibilityLabel={`Resume ${product.display_name}`}
            onPress={() => onCareAction(product, 'resume')}
            disabled={careActionBusyId === product.inventory_item_id}
            style={styles.choiceButton}
          >
            <Text style={styles.choiceButtonText}>Resume</Text>
          </TouchableOpacity>
        </View>
      ))}
    </View>
  );
}

export function EmptyModule({ title, body, actionLabel, onPress }: {
  title: string;
  body: string;
  actionLabel?: string;
  onPress?: () => void;
}) {
  return (
    <View style={styles.empty} accessibilityLabel={title}>
      <Text style={styles.cardTitle}>{title}</Text>
      <Text style={styles.body}>{body}</Text>
      {!!actionLabel && !!onPress && (
        <TouchableOpacity accessibilityRole="button" accessibilityLabel={actionLabel} onPress={onPress}>
          <Text style={styles.link}>{actionLabel}</Text>
        </TouchableOpacity>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  card: { backgroundColor: COLORS.card, borderRadius: RADIUS.lg, borderWidth: 1, borderColor: COLORS.border, padding: 13, marginTop: 10 },
  routine: { backgroundColor: COLORS.card, borderRadius: RADIUS.lg, borderWidth: 1, borderColor: COLORS.border, padding: 14, marginTop: 12 },
  routineTitle: { fontFamily: FONTS.family.headingMedium, color: COLORS.textPrimary, fontSize: 19 },
  routineFrequency: { fontFamily: FONTS.family.bodyMedium, color: COLORS.primary, fontSize: 11, marginTop: 2, marginBottom: 6 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 7 },
  severity: { fontFamily: FONTS.family.bodySemibold, fontSize: 10, letterSpacing: 0.7, textTransform: 'uppercase' },
  cardTitle: { fontFamily: FONTS.family.headingMedium, color: COLORS.textPrimary, fontSize: 15, marginTop: 4 },
  body: { fontFamily: FONTS.family.body, color: COLORS.textSecondary, fontSize: 13, lineHeight: 19, marginTop: 4 },
  evidence: { fontFamily: FONTS.family.body, color: COLORS.textMuted, fontSize: 11, lineHeight: 16, marginTop: 5 },
  ruleId: { fontFamily: FONTS.family.body, color: COLORS.textMuted, fontSize: 10, marginTop: 5 },
  step: { borderTopWidth: 1, borderTopColor: COLORS.border, paddingVertical: 11 },
  stepGap: { opacity: 0.85 },
  stepOrder: { fontFamily: FONTS.family.headingMedium, color: COLORS.primary, fontSize: 13, width: 22 },
  stepLabel: { fontFamily: FONTS.family.bodySemibold, color: COLORS.textPrimary, fontSize: 13 },
  stepProduct: { fontFamily: FONTS.family.body, color: COLORS.textSecondary, fontSize: 12, marginTop: 1 },
  gapName: { fontFamily: FONTS.family.body, color: COLORS.textMuted, fontSize: 12, marginTop: 1, fontStyle: 'italic' },
  why: { fontFamily: FONTS.family.body, color: COLORS.textSecondary, fontSize: 12, lineHeight: 18, marginTop: 5 },
  metaRow: { flexDirection: 'row', gap: 6, marginTop: 4 },
  meta: { fontFamily: FONTS.family.bodyMedium, color: COLORS.textMuted, fontSize: 11 },
  feedbackButton: { flexDirection: 'row', alignItems: 'center', alignSelf: 'flex-start', gap: 5, borderWidth: 1, borderColor: COLORS.primaryMuted, borderRadius: RADIUS.full, paddingHorizontal: 11, paddingVertical: 7, marginTop: 9 },
  feedbackText: { fontFamily: FONTS.family.bodySemibold, color: COLORS.primary, fontSize: 11 },
  choiceDisclosure: { flexDirection: 'row', alignItems: 'center', gap: 5, marginTop: 10 },
  choicePanel: { backgroundColor: COLORS.backgroundSecondary, borderRadius: RADIUS.md, padding: 10, marginTop: 8 },
  choiceHint: { fontFamily: FONTS.family.body, fontSize: 11, lineHeight: 16, color: COLORS.textMuted },
  choiceTitle: { fontFamily: FONTS.family.bodySemibold, fontSize: 11, color: COLORS.textPrimary, marginTop: 9 },
  choiceRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 7, marginTop: 8 },
  choiceProductRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginTop: 8 },
  choiceProductName: { flex: 1, fontFamily: FONTS.family.body, color: COLORS.textSecondary, fontSize: 12 },
  choiceButton: { borderWidth: 1, borderColor: COLORS.border, borderRadius: RADIUS.full, paddingHorizontal: 11, paddingVertical: 8 },
  choiceButtonText: { fontFamily: FONTS.family.bodySemibold, fontSize: 11, color: COLORS.primary },
  pausedRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginTop: 10 },
  safety: { fontFamily: FONTS.family.bodyMedium, color: COLORS.primary, fontSize: 11, lineHeight: 16, marginTop: 5 },
  climate: { fontFamily: FONTS.family.body, color: COLORS.textSecondary, fontSize: 11, lineHeight: 16, marginTop: 4 },
  alternative: { fontFamily: FONTS.family.body, color: COLORS.textMuted, fontSize: 11, lineHeight: 16, marginTop: 4 },
  skipped: { fontFamily: FONTS.family.bodyMedium, color: COLORS.textSecondary, fontSize: 11, lineHeight: 16, marginTop: 8 },
  disclaimer: { fontFamily: FONTS.family.body, color: COLORS.textMuted, fontSize: 10, marginTop: 10 },
  check: { width: 32, height: 32, borderRadius: 16, alignItems: 'center', justifyContent: 'center', borderWidth: 1, borderColor: COLORS.border },
  checkDone: { backgroundColor: COLORS.primary, borderColor: COLORS.primary },
  statRow: { flexDirection: 'row', gap: 10, marginTop: 8 },
  stat: { flex: 1 },
  statValue: { fontFamily: FONTS.family.headingMedium, color: COLORS.primary, fontSize: 23 },
  statLabel: { fontFamily: FONTS.family.body, color: COLORS.textSecondary, fontSize: 11, lineHeight: 15, marginTop: 2 },
  listRow: { fontFamily: FONTS.family.body, color: COLORS.textSecondary, fontSize: 12, lineHeight: 19, marginTop: 2 },
  foods: { fontFamily: FONTS.family.bodyMedium, color: COLORS.textPrimary, fontSize: 12, lineHeight: 19, marginTop: 6 },
  boundary: { flexDirection: 'row', gap: 9, alignItems: 'flex-start', backgroundColor: COLORS.primaryLight, borderRadius: RADIUS.lg, borderWidth: 1, borderColor: COLORS.primaryMuted, padding: 13, marginTop: 10 },
  boundaryText: { flex: 1, fontFamily: FONTS.family.body, color: COLORS.textPrimary, fontSize: 12, lineHeight: 18 },
  empty: { backgroundColor: COLORS.card, borderRadius: RADIUS.lg, borderWidth: 1, borderColor: COLORS.border, padding: SPACING.lg, marginTop: 10 },
  link: { fontFamily: FONTS.family.bodySemibold, color: COLORS.primary, fontSize: 13, marginTop: 9 },
});
