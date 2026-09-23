/**
 * Step 13 — one owned supplement's label, as the person recorded it.
 *
 * Presentational only: the screen fetches, this renders. Every word comes from
 * `strings/supplements.ts`; the only other text on screen is the person's own
 * (names, notes, printed text) and published research a reviewer approved,
 * both shown verbatim.
 *
 * Three things are deliberate:
 * - Printed text is never restyled. No capitalisation, no unit clean-up:
 *   "mcg" and "µg" are shown as they were recorded.
 * - Serving text is always framed as printed text, in quotes, never as an
 *   instruction.
 * - A research line appears only when the server marks it published, and it
 *   always carries its source with an open link. Anything else reads
 *   "Not enough information" — on purpose, not as a gap.
 */
import React from 'react';
import { StyleSheet, Text, TouchableOpacity, View } from 'react-native';

import { BoundaryNotice } from '../routines/RoutinePieces';
import { openExternalUrl } from '../../services/externalLinks';
import type { SupplementDetail, SupplementDetailComponent } from '../../services/apiV2';
import { S, fill } from '../../strings/supplements';
import { COLORS, FONTS, RADIUS } from '../../theme/colors';

const missingText = (codes: string[]): string | null => {
  const words = codes.map((code) => S.missing[code]).filter(Boolean);
  return words.length ? `${S.missingLabel}: ${words.join(', ')}` : null;
};

const amountText = (component: SupplementDetailComponent): string => {
  const { amount, unit } = component.printed;
  if (amount === null) return S.amountNotAdded;
  return unit ? `${amount} ${unit}` : amount;
};

function FormLine({ component }: { component: SupplementDetailComponent }) {
  const form = component.form;
  if (form.status === 'awaiting_confirmation') return null;
  const value = form.status === 'exact' && 'name' in form && form.name
    ? form.name
    : form.status === 'not_stated' ? S.form.notStated : S.form.notEnoughInformation;
  return <Text style={styles.line}><Text style={styles.label}>{S.form.label}: </Text>{value}</Text>;
}

function ChemistryLine({ component }: { component: SupplementDetailComponent }) {
  const chemistry = component.package_chemistry;
  if (chemistry.status === 'calculated' && 'percent_by_weight' in chemistry && chemistry.percent_by_weight && chemistry.element) {
    return (
      <View testID="supplement-chemistry" style={styles.block}>
        <Text style={styles.line}>
          <Text style={styles.label}>{S.chemistry.label}: </Text>
          {fill(S.chemistry.calculated, { percent: chemistry.percent_by_weight, element: chemistry.element })}
        </Text>
        <Text style={styles.note}>{S.chemistry.basis}</Text>
      </View>
    );
  }
  if (chemistry.status === 'withheld' && 'withheld_reason' in chemistry && chemistry.withheld_reason) {
    return (
      <Text testID="supplement-chemistry" style={styles.line}>
        <Text style={styles.label}>{S.chemistry.label}: </Text>{S.chemistry.withheld[chemistry.withheld_reason]}
      </Text>
    );
  }
  return null;
}

function KnowledgeLine({ component }: { component: SupplementDetailComponent }) {
  const knowledge = component.published_knowledge;
  if (knowledge.status === 'published' && 'source' in knowledge) {
    return (
      <View testID="supplement-knowledge" style={styles.block}>
        <Text style={styles.label}>{S.knowledge.label}</Text>
        <Text style={styles.line}>{knowledge.summary}</Text>
        <Text style={styles.line}>{knowledge.unit ? `${knowledge.value} (${knowledge.unit})` : knowledge.value}</Text>
        {!!knowledge.disagreement && <Text style={styles.note}>{S.knowledge.sourcesDiffer}: {knowledge.disagreement}</Text>}
        {!!knowledge.evidence_strength && <Text style={styles.note}>{S.knowledge.evidence}: {knowledge.evidence_strength}</Text>}
        <Text style={styles.note}>{knowledge.source.name} · {knowledge.source.publisher}</Text>
        <TouchableOpacity
          accessibilityRole="link"
          accessibilityLabel={S.a11y.openSource(knowledge.source.name)}
          onPress={() => void openExternalUrl(knowledge.source.url)}
        >
          <Text style={styles.link}>{S.knowledge.openSource}</Text>
        </TouchableOpacity>
      </View>
    );
  }
  // Research can only exist for an exact form; elsewhere the form line has already said why.
  if (component.form.status !== 'exact') return null;
  return <Text testID="supplement-knowledge" style={styles.line}><Text style={styles.label}>{S.knowledge.label}: </Text>{S.knowledge.none}</Text>;
}

function ComponentCard({ component, onConfirm }: { component: SupplementDetailComponent; onConfirm?: (id: string) => void }) {
  const serving = component.printed.serving_text;
  const missing = missingText(component.missing_information);
  return (
    // Not grouped into one accessible element: that would hide the confirm
    // button and the source link inside it from a screen reader. Each line
    // below carries its own label ("Printed amount: …"), so each reads alone.
    <View testID={`supplement-component-${component.id}`} style={styles.component}>
      <Text
        style={styles.name}
        accessibilityLabel={`${S.a11y.component(component.printed.name)}. ${S.provenance[component.provenance]}`}
      >
        {component.printed.name}
      </Text>
      <Text style={[styles.provenance, !component.confirmed && styles.unconfirmed]}>{S.provenance[component.provenance]}</Text>
      {!component.counts_for_overlap && <Text style={styles.note}>{S.awaitingConfirmation}</Text>}
      {!component.confirmed && !!onConfirm && (
        <TouchableOpacity
          accessibilityRole="button"
          accessibilityLabel={S.a11y.confirm(component.printed.name)}
          onPress={() => onConfirm(component.id)}
        >
          <Text style={styles.link}>{S.confirm}</Text>
        </TouchableOpacity>
      )}
      <Text style={styles.line}><Text style={styles.label}>{S.printedAmount}: </Text>{amountText(component)}</Text>
      <Text style={styles.line}>
        <Text style={styles.label}>{S.printedServingText}: </Text>
        {serving ? `“${serving}”` : S.servingNotAdded}
      </Text>
      <FormLine component={component} />
      <ChemistryLine component={component} />
      <KnowledgeLine component={component} />
      {!!missing && <Text style={styles.note}>{missing}</Text>}
    </View>
  );
}

export function SupplementDetailSection({ detail, onConfirm }: {
  detail: SupplementDetail;
  /** Offered only on components the person has not confirmed yet. */
  onConfirm?: (componentId: string) => void;
}) {
  const expiry = detail.expiry.state === 'current' && detail.expiry.date
    ? fill(S.expiry.current, { date: detail.expiry.date })
    : S.expiry[detail.expiry.state];
  const missing = missingText(detail.missing_information);
  return (
    <View testID="supplement-detail">
      {detail.professional_boundary.boundary && !!detail.professional_boundary.message && (
        <BoundaryNotice message={detail.professional_boundary.message} />
      )}
      <Text testID="supplement-expiry" style={styles.expiry}>{expiry}</Text>

      <Text style={styles.section}>{S.heading}</Text>
      <Text style={styles.note}>{S.intro}</Text>
      {detail.components.length === 0 && <Text style={styles.line}>{S.empty}</Text>}
      {detail.components.map((component) => <ComponentCard key={component.id} component={component} onConfirm={onConfirm} />)}

      {detail.overlaps.length > 0 && (
        <View testID="supplement-overlaps" style={styles.overlaps} accessibilityLabel={S.overlap.heading}>
          <Text style={styles.section}>{S.overlap.heading}</Text>
          {detail.overlaps.map((group) => (
            <View key={group.component_key} style={styles.overlapRow}>
              <Text style={styles.name}>{group.nutrient_display_name ?? group.printed_names_here[0]}</Text>
              <Text style={styles.line}>{fill(S.overlap.line, { count: group.product_count })}</Text>
              <Text style={styles.note}>{fill(S.overlap.here, { names: group.printed_names_here.join(', ') })}</Text>
              {group.other_products.map((product) => (
                <Text key={product.inventory_item_id} style={styles.note}>
                  {fill(S.overlap.other, { product: product.product_name, names: product.printed_names.join(', ') })}
                </Text>
              ))}
            </View>
          ))}
          <Text style={styles.note}>{S.overlap.note}</Text>
        </View>
      )}

      {!!missing && <Text testID="supplement-missing" style={styles.line}>{missing}</Text>}

      <View style={styles.weDoNot} accessibilityLabel={S.weDoNot.heading}>
        <Text style={styles.label}>{S.weDoNot.heading}</Text>
        {S.weDoNot.lines.map((line) => <Text key={line} style={styles.note}>• {line}</Text>)}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  section: { fontFamily: FONTS.family.headingMedium, color: COLORS.textPrimary, fontSize: 17, marginTop: 18, marginBottom: 6 },
  expiry: { fontFamily: FONTS.family.bodyMedium, color: COLORS.textSecondary, fontSize: 12, marginTop: 10 },
  component: { backgroundColor: COLORS.card, borderRadius: RADIUS.lg, borderWidth: 1, borderColor: COLORS.border, padding: 12, marginTop: 8 },
  name: { fontFamily: FONTS.family.bodySemibold, color: COLORS.textPrimary, fontSize: 14 },
  provenance: { fontFamily: FONTS.family.bodyMedium, color: COLORS.textSecondary, fontSize: 11, marginTop: 2 },
  unconfirmed: { color: COLORS.warning },
  label: { fontFamily: FONTS.family.bodySemibold, color: COLORS.textSecondary, fontSize: 11 },
  line: { fontFamily: FONTS.family.body, color: COLORS.textPrimary, fontSize: 12, lineHeight: 18, marginTop: 4 },
  note: { fontFamily: FONTS.family.body, color: COLORS.textMuted, fontSize: 11, lineHeight: 16, marginTop: 3 },
  block: { marginTop: 4 },
  link: { fontFamily: FONTS.family.bodySemibold, color: COLORS.primary, fontSize: 12, marginTop: 4 },
  overlaps: { marginTop: 6 },
  overlapRow: { paddingVertical: 8, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: COLORS.border },
  weDoNot: { backgroundColor: COLORS.infoLight, borderRadius: RADIUS.md, padding: 12, marginTop: 16 },
});

export default SupplementDetailSection;
