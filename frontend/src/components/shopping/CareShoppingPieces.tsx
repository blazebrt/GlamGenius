import React from 'react';
import { StyleSheet, Text, TouchableOpacity, View } from 'react-native';

import {
  CareCandidateInspection, CareCompatibilityFinding, CareEvidenceFinding, CarePurchaseCheck,
  CareValueRecoveryItem,
} from '../../services/apiV2';
import { COLORS, FONTS, RADIUS, SPACING } from '../../theme/colors';

const VERDICT_COPY = {
  buy: { label: 'Buy', colour: COLORS.success, background: COLORS.successLight },
  wait: { label: 'Wait', colour: COLORS.warning, background: COLORS.warningLight },
  skip: { label: 'Skip', colour: COLORS.info, background: COLORS.infoLight },
} as const;

export function CareCandidateReview({
  inspection,
  onConfirm,
  onCorrect,
}: {
  inspection: CareCandidateInspection;
  onConfirm: () => void;
  onCorrect: () => void;
}) {
  const candidate = inspection.candidate;
  const details = candidate.details || {};
  const facts = [
    ['Product type', details.product_type],
    ['Brand', candidate.brand],
    ['Size', details.size],
    ['Ingredients from label', details.ingredients_text],
    ['Price', candidate.price != null ? `${candidate.currency} ${candidate.price}` : null],
  ].filter(([, value]) => Boolean(value)) as [string, string][];
  return (
    <View style={styles.card} accessibilityLabel={`What we read for ${candidate.display_name}`}>
      <Text style={styles.eyebrow}>WHAT WE READ</Text>
      <Text style={styles.title}>{candidate.display_name}</Text>
      {facts.map(([label, value]) => <Text key={label} style={styles.fact}>{label}: {value}</Text>)}
      {!!inspection.missing_information.length && (
        <Text style={styles.warn}>Still missing: {inspection.missing_information.join(', ')}.</Text>
      )}
      {!!candidate.uncertain_fields.length && (
        <Text style={styles.warn}>Please check: {candidate.uncertain_fields.join(', ')}.</Text>
      )}
      <Text style={styles.note}>This is something you are considering. It has not been added to your inventory.</Text>
      <View style={styles.row}>
        <TouchableOpacity accessibilityRole="button" accessibilityLabel="Correct product facts" onPress={onCorrect} style={styles.outline}>
          <Text style={styles.outlineText}>Correct it</Text>
        </TouchableOpacity>
        <TouchableOpacity accessibilityRole="button" accessibilityLabel="Confirm product facts" onPress={onConfirm} style={[styles.primary, { flex: 1, marginTop: 0 }]}>
          <Text style={styles.primaryText}>Looks right</Text>
        </TouchableOpacity>
      </View>
    </View>
  );
}

export function CareVerdictCard({ check }: { check: CarePurchaseCheck }) {
  const meta = VERDICT_COPY[check.verdict.verdict];
  return (
    <View style={[styles.verdict, { backgroundColor: meta.background }]} accessibilityLabel={`Care verdict: ${meta.label}`}>
      <Text style={[styles.verdictLabel, { color: meta.colour }]}>{meta.label}</Text>
      <Text style={styles.title}>{check.verdict.headline}</Text>
      <Text style={styles.body}>{check.verdict.explanation}</Text>
    </View>
  );
}

const roleCopy: Record<string, string> = {
  addresses_required_gap: 'This would fill a role your routine currently needs.',
  required_role_already_covered: 'Your routine already has this required role covered.',
  optional_role_not_required: 'This role is optional for your current routine.',
  role_unresolved: 'We could not identify a clear routine role yet.',
};

export function CareWhy({ check }: { check: CarePurchaseCheck }) {
  const dimensions = check.assessment.dimensions || {};
  const role = dimensions.role_utility || {};
  const redundancy = dimensions.redundancy || {};
  const compatibility = dimensions.compatibility || {};
  const findings = Array.isArray(compatibility.findings) ? compatibility.findings : [];
  const evidenceSupport = check.evidence.evidence_support || {};
  const evidenceFindings = Array.isArray(evidenceSupport.findings) ? evidenceSupport.findings : [];
  const recovery = check.value.value_context.owned_value_recovery || { items: [] };
  const owned = Array.isArray(redundancy.eligible_owned_same_slot) ? redundancy.eligible_owned_same_slot : [];
  const missing = new Set<string>(Array.isArray(dimensions.identity_confidence?.missing_information)
    ? dimensions.identity_confidence.missing_information : []);
  const decisionContext = check.verdict.decision_context;
  if (check.verdict.primary_reason_code === 'candidate_ingredient_information_incomplete') missing.add('ingredient information');
  if (decisionContext.candidate_spend_status === 'missing') missing.add('candidate price');
  if (decisionContext.owned_value_recovery_status === 'financial_context_partial'
    || check.value.value_context.status === 'financial_context_partial') missing.add('incomplete financial context');
  if (decisionContext.currency_context_status === 'mixed_currency_no_conversion') missing.add('mixed currency context (no conversion)');
  const missingItems = [...missing];
  return (
    <>
      <View style={styles.card} accessibilityLabel="Care routine context">
        <Text style={styles.eyebrow}>WHY</Text>
        <Text style={styles.title}>Its place in your routine</Text>
        <Text style={styles.body}>{(role.status && roleCopy[role.status]) || 'The role was assessed from your current Care context.'}</Text>
        {!!role.care_slot && <Text style={styles.fact}>Routine role: {role.care_slot}</Text>}
      </View>

      {!!owned.length && (
        <View style={styles.card} accessibilityLabel="Eligible products you already own">
          <Text style={styles.title}>What you already own</Text>
          <Text style={styles.body}>These eligible products can cover the same routine role.</Text>
          {owned.map((item) => <Text key={item.owned_item_id} style={styles.fact}>• {item.display_name}</Text>)}
        </View>
      )}

      {!!findings.length && (
        <View style={styles.card} accessibilityLabel="Care compatibility context">
          <Text style={styles.title}>Compatibility context</Text>
          {findings.map((finding: CareCompatibilityFinding, index: number) => (
            <View key={`${finding.rule_id || 'finding'}-${index}`} style={styles.subsection}>
              {!!finding.severity && <Text style={styles.note}>{finding.severity === 'caution' ? 'Caution' : 'Information'}</Text>}
              <Text style={styles.fact}>{finding.headline}</Text>
              <Text style={styles.body}>{finding.guidance}</Text>
              {!!finding.owned_item_display_name && <Text style={styles.note}>Compared with {finding.owned_item_display_name}.</Text>}
            </View>
          ))}
        </View>
      )}

      {!!evidenceFindings.length && (
        <View style={styles.card} accessibilityLabel="Reviewed Care evidence">
          <Text style={styles.title}>Reviewed evidence</Text>
          {evidenceFindings.map((finding: CareEvidenceFinding, index: number) => (
            <View key={`${finding.claim_key || 'evidence'}-${index}`} style={styles.subsection}>
              {!!finding.claim_summary && <Text style={styles.fact}>{finding.claim_summary}</Text>}
              {!!finding.evidence_strength && <Text style={styles.note}>Evidence strength: {finding.evidence_strength}</Text>}
              {!!finding.claim_status && <Text style={styles.note}>Status: {finding.claim_status}</Text>}
              {(finding.sources || []).map((source) => <Text key={source.source_id || source.title || 'source'} style={styles.note}>{source.title || 'Reviewed source'} · {source.publisher || 'Publisher not listed'}</Text>)}
            </View>
          ))}
        </View>
      )}

      {!!recovery.items?.length && (
        <View style={styles.card} accessibilityLabel="Value you can use first">
          <Text style={styles.title}>Value you can use first</Text>
          {recovery.items.map((item: CareValueRecoveryItem) => (
            <View key={item.owned_item_id || item.display_name} style={styles.subsection}>
              <Text style={styles.fact}>{item.display_name}</Text>
              {!!item.estimated_value && <Text style={styles.note}>{item.estimated_value} {item.currency || ''}</Text>}
              {!!item.explanation && <Text style={styles.body}>{item.explanation}</Text>}
            </View>
          ))}
        </View>
      )}

      {!!missingItems.length && (
        <View style={styles.card} accessibilityLabel="Missing information">
          <Text style={styles.title}>What would make this clearer</Text>
          {missingItems.map((item) => <Text key={item} style={styles.fact}>• {item}</Text>)}
        </View>
      )}
    </>
  );
}

export function CarePurchaseResult({ check, onReset }: { check: CarePurchaseCheck; onReset: () => void }) {
  return (
    <>
      <CareVerdictCard check={check} />
      <CareWhy check={check} />
      <Text style={styles.noteCenter}>This candidate remains separate from your inventory.</Text>
      <TouchableOpacity accessibilityRole="button" accessibilityLabel="Check something else" onPress={onReset}>
        <Text style={styles.link}>Check something else</Text>
      </TouchableOpacity>
    </>
  );
}

const styles = StyleSheet.create({
  card: { backgroundColor: COLORS.card, borderRadius: RADIUS.xl, padding: SPACING.lg, borderWidth: 1, borderColor: COLORS.border, marginBottom: SPACING.md },
  eyebrow: { fontFamily: FONTS.family.bodySemibold, color: COLORS.accent, fontSize: 10, letterSpacing: 1.2, textTransform: 'uppercase' },
  title: { fontFamily: FONTS.family.headingMedium, fontSize: 18, color: COLORS.textPrimary, marginTop: 4 },
  body: { fontFamily: FONTS.family.body, fontSize: 13, lineHeight: 19, color: COLORS.textSecondary, marginTop: 6 },
  fact: { fontFamily: FONTS.family.body, fontSize: 12, lineHeight: 18, color: COLORS.textSecondary, marginTop: 4 },
  note: { fontFamily: FONTS.family.body, fontSize: 11, lineHeight: 16, color: COLORS.textMuted, marginTop: 8 },
  warn: { fontFamily: FONTS.family.bodyMedium, fontSize: 12, lineHeight: 18, color: COLORS.warning, marginTop: 7 },
  verdict: { borderRadius: RADIUS.xl, padding: SPACING.lg, marginBottom: SPACING.md },
  verdictLabel: { fontFamily: FONTS.family.heading, fontSize: 28 },
  row: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: SPACING.md },
  primary: { alignItems: 'center', justifyContent: 'center', backgroundColor: COLORS.primary, borderRadius: RADIUS.full, paddingHorizontal: 16, paddingVertical: 12 },
  primaryText: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.white },
  outline: { alignItems: 'center', justifyContent: 'center', borderRadius: RADIUS.full, paddingHorizontal: 14, paddingVertical: 11, borderWidth: 1, borderColor: COLORS.border },
  outlineText: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.textPrimary },
  subsection: { marginTop: SPACING.sm },
  noteCenter: { fontFamily: FONTS.family.body, fontSize: 11, lineHeight: 16, color: COLORS.textMuted, textAlign: 'center', marginTop: 4 },
  link: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.primary, textAlign: 'center', marginTop: SPACING.md },
});
