/**
 * The three small stages that make the skin-care loop explicit.
 *
 * Each exists because something must be *stated* rather than inferred: which
 * kind of label this is, what the camera read, and whether anything about the
 * person's situation should send them to a clinician instead.
 */
import React from 'react';
import { StyleSheet, Text, TouchableOpacity, View } from 'react-native';

import { COLORS, FONTS, RADIUS, SPACING } from '../../theme/colors';
import type {
  ConfirmedSkinCareLabel,
  ForYouSafetyContext,
  SkinCareLabelFactsView,
} from '../../services/productScan';

export type LabelKind = 'packaged_food' | 'skin_care';

export const LABEL_TYPE_COPY = {
  heading: 'What kind of label is this?',
  body: 'This tells us how to read the pack. We do not guess it from the ingredients.',
  packagedFood: 'Packaged food',
  skinCare: 'Skin care',
  cancel: 'Not now',
} as const;

/**
 * The category, asked plainly.
 *
 * The answer decides which backend contract the capture travels down, and
 * reaching the skin-care route is itself the structured assertion that this is
 * a skin-care product. That is why it is a question and not a classifier: an
 * inferred category would enter the governed chain wearing the same clothes as
 * one a person established.
 */
export function LabelTypeChoice({
  onChoose,
  onCancel,
}: {
  onChoose: (kind: LabelKind) => void;
  onCancel: () => void;
}) {
  return (
    <View style={styles.card}>
      <Text style={styles.title}>{LABEL_TYPE_COPY.heading}</Text>
      <Text style={styles.body}>{LABEL_TYPE_COPY.body}</Text>
      <TouchableOpacity
        accessibilityRole="button"
        accessibilityLabel={LABEL_TYPE_COPY.packagedFood}
        testID="label-kind-packaged-food"
        onPress={() => onChoose('packaged_food')}
        style={styles.choice}
      >
        <Text style={styles.choiceText}>{LABEL_TYPE_COPY.packagedFood}</Text>
      </TouchableOpacity>
      <TouchableOpacity
        accessibilityRole="button"
        accessibilityLabel={LABEL_TYPE_COPY.skinCare}
        testID="label-kind-skin-care"
        onPress={() => onChoose('skin_care')}
        style={styles.choice}
      >
        <Text style={styles.choiceText}>{LABEL_TYPE_COPY.skinCare}</Text>
      </TouchableOpacity>
      <TouchableOpacity
        accessibilityRole="button"
        accessibilityLabel={LABEL_TYPE_COPY.cancel}
        onPress={onCancel}
        style={styles.linkButton}
      >
        <Text style={styles.linkText}>{LABEL_TYPE_COPY.cancel}</Text>
      </TouchableOpacity>
    </View>
  );
}

export const SKIN_CARE_REVIEW_COPY = {
  heading: 'This is what the label says',
  body: 'Read by the camera, not checked by anyone yet. Check it against the pack in your hand — nothing is saved until you confirm.',
  confirm: 'Confirm this label',
  confirming: 'Confirming…',
  retake: 'Retake the photo',
} as const;

const SKIN_FACT_LABELS: [keyof SkinCareLabelFactsView, string][] = [
  ['product_name', 'Name'],
  ['brand', 'Brand'],
  ['product_type', 'Product type'],
  ['ingredients_text', 'Ingredients, as printed'],
];

/**
 * The draft, shown back for checking.
 *
 * No text input anywhere. A person who thinks it is wrong retakes the
 * photograph; they do not hand us a different answer and call it a
 * confirmation. And with no readable ingredient list there is nothing to
 * confirm at all, so the action is withheld rather than disabled-looking.
 */
export function SkinCareLabelReview({
  facts,
  ingredientsReadable,
  message,
  busy,
  onConfirm,
  onRetake,
}: {
  facts: SkinCareLabelFactsView;
  ingredientsReadable: boolean;
  message: string | null;
  busy?: boolean;
  onConfirm: () => void;
  onRetake: () => void;
}) {
  const rows = SKIN_FACT_LABELS
    .filter(([key]) => typeof facts[key] === 'string' && (facts[key] as string).trim())
    .map(([key, label]) => ({ key, label, value: facts[key] as string }));

  return (
    <View style={styles.card}>
      <Text style={styles.title}>{SKIN_CARE_REVIEW_COPY.heading}</Text>
      <Text style={styles.body}>{SKIN_CARE_REVIEW_COPY.body}</Text>

      {rows.map((row) => (
        <View key={String(row.key)} style={styles.factRow}>
          <Text style={styles.factLabel}>{row.label}</Text>
          <Text style={styles.factValue}>{row.value}</Text>
        </View>
      ))}

      {!ingredientsReadable && !!message && (
        <Text style={styles.warning} accessibilityLabel="Ingredients could not be read">
          {message}
        </Text>
      )}

      {ingredientsReadable && (
        <TouchableOpacity
          accessibilityRole="button"
          accessibilityLabel={SKIN_CARE_REVIEW_COPY.confirm}
          testID="skin-care-confirm"
          onPress={onConfirm}
          disabled={busy}
          style={styles.primaryButton}
        >
          <Text style={styles.primaryText}>
            {busy ? SKIN_CARE_REVIEW_COPY.confirming : SKIN_CARE_REVIEW_COPY.confirm}
          </Text>
        </TouchableOpacity>
      )}

      <TouchableOpacity
        accessibilityRole="button"
        accessibilityLabel={SKIN_CARE_REVIEW_COPY.retake}
        testID="skin-care-retake"
        onPress={onRetake}
        style={styles.linkButton}
      >
        <Text style={styles.linkText}>{SKIN_CARE_REVIEW_COPY.retake}</Text>
      </TouchableOpacity>
    </View>
  );
}

export const SAFETY_COPY = {
  heading: 'Before FOR YOU',
  body: 'Select anything that applies right now. This check is not saved.',
  pregnancy: 'Pregnancy',
  breastfeeding: 'Breastfeeding',
  medication: 'Medication involved',
  condition: 'Diagnosed condition involved',
  child: 'For a child under 12',
  none: 'None of these',
  submit: 'Check FOR YOU',
} as const;

export type SafetyChoice =
  | 'pregnancy'
  | 'breastfeeding'
  | 'medication_involved'
  | 'diagnosed_condition_involved'
  | 'subject_is_child';

const SAFETY_OPTIONS: [SafetyChoice, string][] = [
  ['pregnancy', SAFETY_COPY.pregnancy],
  ['breastfeeding', SAFETY_COPY.breastfeeding],
  ['medication_involved', SAFETY_COPY.medication],
  ['diagnosed_condition_involved', SAFETY_COPY.condition],
  ['subject_is_child', SAFETY_COPY.child],
];

/** Only the flags a person actually selected. Never an invented negative. */
export function toSafetyContext(selected: Set<SafetyChoice>): ForYouSafetyContext {
  const context: ForYouSafetyContext = {};
  SAFETY_OPTIONS.forEach(([key]) => {
    if (selected.has(key)) context[key] = true;
  });
  return context;
}

/**
 * The one-session safety check.
 *
 * Closed choices only. There is no field for a medicine name, a diagnosis, a
 * symptom or a note, because the product does not need to know *which* one in
 * order to hand over to a clinician — and collecting it would create a health
 * record this milestone has no business holding.
 *
 * Nothing here is stored, logged or sent to analytics. It exists for the
 * length of one request.
 */
export function SafetyPreflight({
  onSubmit,
  busy,
}: {
  onSubmit: (safety: ForYouSafetyContext) => void;
  busy?: boolean;
}) {
  const [selected, setSelected] = React.useState<Set<SafetyChoice>>(new Set());
  const [none, setNone] = React.useState(false);

  const toggle = (key: SafetyChoice) => {
    setNone(false);
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const chooseNone = () => {
    // Exclusive: "none of these" and "this applies" cannot both be true.
    setSelected(new Set());
    setNone(true);
  };

  const ready = none || selected.size > 0;

  return (
    <View style={styles.card}>
      <Text style={styles.title}>{SAFETY_COPY.heading}</Text>
      <Text style={styles.body}>{SAFETY_COPY.body}</Text>

      {SAFETY_OPTIONS.map(([key, label]) => {
        const on = selected.has(key);
        return (
          <TouchableOpacity
            key={key}
            accessibilityRole="checkbox"
            accessibilityState={{ checked: on }}
            accessibilityLabel={label}
            testID={`safety-${key}`}
            onPress={() => toggle(key)}
            style={[styles.choice, on && styles.choiceOn]}
          >
            <Text style={[styles.choiceText, on && styles.choiceTextOn]}>{label}</Text>
          </TouchableOpacity>
        );
      })}

      <TouchableOpacity
        accessibilityRole="checkbox"
        accessibilityState={{ checked: none }}
        accessibilityLabel={SAFETY_COPY.none}
        testID="safety-none"
        onPress={chooseNone}
        style={[styles.choice, none && styles.choiceOn]}
      >
        <Text style={[styles.choiceText, none && styles.choiceTextOn]}>{SAFETY_COPY.none}</Text>
      </TouchableOpacity>

      <TouchableOpacity
        accessibilityRole="button"
        accessibilityLabel={SAFETY_COPY.submit}
        accessibilityState={{ disabled: !ready || !!busy }}
        testID="safety-submit"
        onPress={() => onSubmit(toSafetyContext(selected))}
        disabled={!ready || busy}
        style={[styles.primaryButton, (!ready || busy) && styles.primaryDisabled]}
      >
        <Text style={styles.primaryText}>{SAFETY_COPY.submit}</Text>
      </TouchableOpacity>
    </View>
  );
}

export const CONFIRMED_COPY = {
  heading: 'Skin care label confirmed',
  scanAnother: 'Scan another',
} as const;

/**
 * What was confirmed, presented as the label it is.
 *
 * Deliberately not disguised as an Open Food Facts result: nothing here came
 * from them, so their attribution would be a false statement about where the
 * data originated.
 */
export function ConfirmedSkinCareLabelCard({
  barcode,
  facts,
  confirmed,
  onScanAgain,
}: {
  barcode: string;
  facts: SkinCareLabelFactsView;
  confirmed: ConfirmedSkinCareLabel;
  onScanAgain: () => void;
}) {
  return (
    <View style={styles.card}>
      <Text style={styles.barcode}>{barcode}</Text>
      <Text style={styles.title}>{CONFIRMED_COPY.heading}</Text>
      {!!facts.product_name && <Text style={styles.name}>{facts.product_name}</Text>}
      {!!facts.brand && <Text style={styles.body}>{facts.brand}</Text>}
      {!!facts.product_type && <Text style={styles.body}>{facts.product_type}</Text>}
      {!!facts.ingredients_text && (
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Ingredients, as printed</Text>
          <Text style={styles.body}>{facts.ingredients_text}</Text>
        </View>
      )}
      <Text style={styles.confidence}>{confirmed.confidence.text}</Text>
      <TouchableOpacity
        accessibilityRole="button"
        accessibilityLabel={CONFIRMED_COPY.scanAnother}
        onPress={onScanAgain}
        style={styles.linkButton}
      >
        <Text style={styles.linkText}>{CONFIRMED_COPY.scanAnother}</Text>
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: COLORS.card, borderRadius: RADIUS.lg, padding: SPACING.lg,
    borderWidth: 1, borderColor: COLORS.border, gap: SPACING.sm,
  },
  title: { fontFamily: FONTS.family.heading, fontSize: 22, color: COLORS.textPrimary },
  name: { fontFamily: FONTS.family.bodySemibold, fontSize: 16, color: COLORS.textPrimary },
  barcode: { fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.textMuted },
  body: { fontFamily: FONTS.family.body, fontSize: 14, lineHeight: 21, color: COLORS.textSecondary },
  warning: { fontFamily: FONTS.family.bodySemibold, fontSize: 14, lineHeight: 21, color: COLORS.warning },
  section: { marginTop: SPACING.xs, gap: 4 },
  sectionTitle: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.textPrimary },
  confidence: { fontFamily: FONTS.family.body, fontSize: 13, color: COLORS.textSecondary },
  factRow: { flexDirection: 'row', gap: SPACING.sm, alignItems: 'flex-start' },
  factLabel: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.textSecondary, width: 110 },
  factValue: { fontFamily: FONTS.family.body, fontSize: 14, color: COLORS.textPrimary, flex: 1 },
  choice: {
    borderWidth: 1, borderColor: COLORS.border, borderRadius: RADIUS.md,
    paddingVertical: 14, paddingHorizontal: SPACING.md, backgroundColor: COLORS.background,
  },
  choiceOn: { borderColor: COLORS.primary, backgroundColor: COLORS.card },
  choiceText: { fontFamily: FONTS.family.bodySemibold, fontSize: 15, color: COLORS.textPrimary },
  choiceTextOn: { color: COLORS.primary },
  primaryButton: {
    backgroundColor: COLORS.primary, borderRadius: RADIUS.md, paddingVertical: 14,
    alignItems: 'center', marginTop: SPACING.sm,
  },
  primaryDisabled: { opacity: 0.5 },
  primaryText: { fontFamily: FONTS.family.bodySemibold, fontSize: 15, color: COLORS.white },
  linkButton: { paddingVertical: 12, alignItems: 'center' },
  linkText: { fontFamily: FONTS.family.bodySemibold, fontSize: 14, color: COLORS.primary },
});
