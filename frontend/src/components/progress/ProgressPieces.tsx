/**
 * Progress and memory building blocks.
 *
 * Three rules run through all of it, and they are the phase's promises made
 * visible rather than merely implemented on the server.
 *
 * **A number is never shown without its formula.** `MetricCard` renders a
 * "how is this worked out?" disclosure carrying the exact formula string and
 * its version. There is no code path here that displays a value alone.
 *
 * **An unavailable metric is never drawn as zero.** A bar at 0% and a bar with
 * no data look identical to a person, and one of them is an accusation. An
 * unavailable metric shows a dash and says what it is waiting for.
 *
 * **There is no headline number.** No component here aggregates metrics, and
 * the screen shows the server's own "no single number" note at the top.
 *
 * Charts are accessible: every bar carries an `accessibilityLabel` with the
 * value in words, because a coloured bar alone tells a screen-reader user
 * nothing.
 */
import React from 'react';
import { StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';

import {
  ComparisonResponse, EarnedMilestone, Goal, MemoryCategorySummary, MemoryFact, Metric,
} from '../../services/apiV2';
import { COLORS, FONTS, RADIUS, SPACING } from '../../theme/colors';

/** A metric's value in words. Used on screen and as the accessibility label. */
export function formatMetric(metric: Metric): string {
  if (metric.value === null) return 'Not enough information yet';
  switch (metric.unit) {
    case 'ratio':
      return `${Math.round(metric.value * 100)}%`;
    case 'currency':
      return `₹${metric.value.toLocaleString('en-IN')}`;
    case 'days':
      return `${metric.value} day${metric.value === 1 ? '' : 's'}`;
    case 'scale_1_5':
      return `${metric.value} out of 5`;
    default:
      return `${metric.value}`;
  }
}

/** How wide the bar should be. Null values get no bar at all, not a zero-width one. */
export function barWidth(metric: Metric): number | null {
  if (metric.value === null) return null;
  if (metric.unit === 'ratio') return Math.max(2, Math.min(100, metric.value * 100));
  if (metric.unit === 'scale_1_5') return Math.max(2, (metric.value / 5) * 100);
  return null;
}

export function MetricCard({ metric, expanded, onToggle }: {
  metric: Metric;
  expanded: boolean;
  onToggle: () => void;
}) {
  const width = barWidth(metric);
  const unavailable = metric.status === 'unavailable';

  return (
    <View
      style={styles.card}
      accessibilityLabel={`${metric.label}: ${formatMetric(metric)}`}
    >
      <View style={styles.row}>
        <Text style={styles.cardTitle}>{metric.label}</Text>
        <Text style={[styles.value, unavailable && styles.valueMuted]}>
          {unavailable ? '—' : formatMetric(metric)}
        </Text>
      </View>

      {width !== null && (
        <View style={styles.track} accessibilityElementsHidden importantForAccessibility="no-hide-descendants">
          <View style={[styles.fill, { width: `${width}%` }]} />
        </View>
      )}

      {unavailable ? (
        <Text style={styles.waiting}>{metric.note}</Text>
      ) : (
        <>
          {metric.status === 'partial' && !!metric.note && (
            <Text style={styles.partial}>{metric.note}</Text>
          )}
          <Text style={styles.body}>{metric.explanation}</Text>
        </>
      )}

      <TouchableOpacity
        accessibilityRole="button"
        accessibilityLabel={
          expanded
            ? `Hide how ${metric.label} is worked out`
            : `Show how ${metric.label} is worked out`
        }
        accessibilityState={{ expanded }}
        onPress={onToggle}
        style={styles.disclosure}
      >
        <Text style={styles.disclosureText}>How is this worked out?</Text>
        <Ionicons name={expanded ? 'chevron-up' : 'chevron-down'} size={16} color={COLORS.textSecondary} />
      </TouchableOpacity>

      {expanded && (
        <View style={styles.formula} accessibilityLabel={`How ${metric.label} is worked out`}>
          <Text style={styles.formulaText}>{metric.formula}</Text>
          <Text style={styles.notMeasure}>Not a measure of: {metric.not_a_measure_of}</Text>
          <Text style={styles.meta}>
            Formula {metric.formula_version} · updates {metric.update_frequency.replace('_', ' ')}
          </Text>
          {!!metric.missing_inputs.length && (
            <Text style={styles.meta}>Waiting on: {metric.missing_inputs.join(', ')}</Text>
          )}
        </View>
      )}
    </View>
  );
}

export function NoOverallScoreNote({ note }: { note: string }) {
  return (
    <View style={styles.banner} accessibilityLabel="About these numbers">
      <Ionicons name="information-circle-outline" size={18} color={COLORS.primary} />
      <Text style={styles.bannerText}>{note}</Text>
    </View>
  );
}

export function GoalCard({ goal, onAchieve }: { goal: Goal; onAchieve?: (goal: Goal) => void }) {
  const pct = goal.progress === null ? null : Math.round(goal.progress * 100);
  return (
    <View
      style={styles.card}
      accessibilityLabel={
        pct === null
          ? `${goal.title}. Progress not available yet.`
          : `${goal.title}. ${pct}% of the way there.`
      }
    >
      <View style={styles.row}>
        <Text style={styles.cardTitle}>{goal.title}</Text>
        <Text style={[styles.value, pct === null && styles.valueMuted]}>
          {pct === null ? '—' : `${pct}%`}
        </Text>
      </View>

      {pct !== null && (
        <View style={styles.track} accessibilityElementsHidden importantForAccessibility="no-hide-descendants">
          <View style={[styles.fill, { width: `${Math.max(2, pct)}%` }]} />
        </View>
      )}

      {!!goal.progress_note && <Text style={styles.waiting}>{goal.progress_note}</Text>}
      {goal.metric_key && goal.starting_value !== null && (
        <Text style={styles.meta}>
          Started at {goal.starting_value}
          {goal.target_value !== null ? `, aiming for ${goal.target_value}` : ''}
        </Text>
      )}
      {!!goal.updates.length && !!goal.updates[0].note && (
        <Text style={styles.body}>{goal.updates[0].note}</Text>
      )}

      {goal.status === 'active' && !!onAchieve && (
        <TouchableOpacity
          accessibilityRole="button"
          accessibilityLabel={`Mark "${goal.title}" as reached`}
          onPress={() => onAchieve(goal)}
        >
          <Text style={styles.link}>Mark as reached</Text>
        </TouchableOpacity>
      )}
    </View>
  );
}

export function MilestoneRow({ milestone }: { milestone: EarnedMilestone }) {
  return (
    <View
      style={styles.milestone}
      accessibilityLabel={`${milestone.label}. ${milestone.description}`}
    >
      {/* A tick, not a trophy. This marks a fact, it does not cheer. */}
      <Ionicons name="checkmark-circle-outline" size={19} color={COLORS.primary} />
      <View style={{ flex: 1 }}>
        <Text style={styles.milestoneTitle}>{milestone.label}</Text>
        <Text style={styles.body}>{milestone.description}</Text>
        <Text style={styles.meta}>{milestone.earned_on}</Text>
      </View>
    </View>
  );
}

export function ComparisonPanel({ result }: { result: ComparisonResponse }) {
  if (!result.comparable) {
    return (
      <View style={styles.card} accessibilityLabel="Why we are not showing a comparison">
        <Text style={styles.cardTitle}>No comparison right now</Text>
        <Text style={styles.body}>{result.message}</Text>
        {result.rejected?.map((row) => (
          <View key={row.photo_id} style={{ marginTop: 6 }}>
            <Text style={styles.meta}>Photo from {row.taken_on}</Text>
            {row.reasons.map((reason, index) => (
              <Text key={index} style={styles.waiting}>• {reason}</Text>
            ))}
          </View>
        ))}
        {!!result.guidance?.length && (
          <View style={{ marginTop: 8 }}>
            <Text style={styles.meta}>For a comparison that works next time:</Text>
            {result.guidance.map((tip, index) => (
              <Text key={index} style={styles.body}>• {tip}</Text>
            ))}
          </View>
        )}
      </View>
    );
  }

  const comparison = result.comparison!;
  return (
    <View style={styles.card} accessibilityLabel="Photo comparison">
      <Text style={styles.cardTitle}>
        {comparison.baseline.taken_on} and {comparison.current.taken_on}
      </Text>
      <Text style={styles.body}>{result.message}</Text>
      <Text style={styles.meta}>{comparison.days_apart} days apart</Text>
      {comparison.checks.filter((row) => !row.passed).map((row) => (
        <Text key={row.key} style={styles.waiting}>{row.detail}</Text>
      ))}
      <Text style={styles.meta}>{comparison.disclaimer}</Text>
    </View>
  );
}

// --- Memory ---------------------------------------------------------------

export function MemoryFactCard({ fact, onConfirm, onCorrect, onDelete }: {
  fact: MemoryFact;
  onConfirm?: (fact: MemoryFact) => void;
  onCorrect?: (fact: MemoryFact) => void;
  onDelete?: (fact: MemoryFact) => void;
}) {
  const deleted = fact.deletion_state === 'deleted';
  return (
    <View style={styles.card} accessibilityLabel={deleted ? 'A deleted memory' : fact.fact}>
      <Text style={styles.cardTitle}>{deleted ? 'Deleted' : fact.fact}</Text>
      <Text style={styles.meta}>{fact.category_label} · {fact.source_label}</Text>
      <Text style={styles.body}>{fact.why_we_remember}</Text>

      {/* Says outright whether this is currently shaping suggestions. */}
      <Text style={fact.influences_recommendations ? styles.influencing : styles.notInfluencing}>
        {fact.influences_recommendations
          ? 'Currently used in what we suggest'
          : 'Not used in what we suggest'}
      </Text>

      {fact.reinforcement_count > 1 && (
        <Text style={styles.meta}>Seen {fact.reinforcement_count} times</Text>
      )}
      {!!fact.linked_evidence.length && (
        <Text style={styles.meta}>
          Learned from: {fact.linked_evidence.map((row) => row.source_label).join(', ')}
        </Text>
      )}

      {!deleted && (
        <View style={styles.actions}>
          {fact.verification_state !== 'confirmed' && !!onConfirm && (
            <TouchableOpacity
              accessibilityRole="button"
              accessibilityLabel={`Confirm: ${fact.fact}`}
              onPress={() => onConfirm(fact)}
            >
              <Text style={styles.link}>Yes, that is right</Text>
            </TouchableOpacity>
          )}
          {!!onCorrect && (
            <TouchableOpacity
              accessibilityRole="button"
              accessibilityLabel={`Correct: ${fact.fact}`}
              onPress={() => onCorrect(fact)}
            >
              <Text style={styles.link}>Correct it</Text>
            </TouchableOpacity>
          )}
          {!!onDelete && (
            <TouchableOpacity
              accessibilityRole="button"
              accessibilityLabel={`Forget: ${fact.fact}`}
              onPress={() => onDelete(fact)}
            >
              <Text style={styles.destructive}>Forget this</Text>
            </TouchableOpacity>
          )}
        </View>
      )}
    </View>
  );
}

export function MemoryCategoryRow({ category, onToggle }: {
  category: MemoryCategorySummary;
  onToggle: (category: MemoryCategorySummary) => void;
}) {
  return (
    <TouchableOpacity
      accessibilityRole="switch"
      accessibilityState={{ checked: category.enabled }}
      accessibilityLabel={`${category.label}, ${category.count} remembered`}
      onPress={() => onToggle(category)}
      style={styles.categoryRow}
    >
      <View style={{ flex: 1 }}>
        <Text style={styles.categoryLabel}>{category.label}</Text>
        <Text style={styles.meta}>
          {category.count} remembered
          {category.enabled ? '' : ' · switched off, nothing deleted'}
        </Text>
      </View>
      <Ionicons
        name={category.enabled ? 'toggle' : 'toggle-outline'}
        size={30}
        color={category.enabled ? COLORS.primary : COLORS.textMuted}
      />
    </TouchableOpacity>
  );
}

export function EmptyState({ title, body, actionLabel, onPress }: {
  title: string; body: string; actionLabel?: string; onPress?: () => void;
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
  row: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8 },
  cardTitle: { flex: 1, fontFamily: FONTS.family.headingMedium, color: COLORS.textPrimary, fontSize: 15 },
  value: { fontFamily: FONTS.family.headingMedium, color: COLORS.primary, fontSize: 20 },
  valueMuted: { color: COLORS.textMuted },
  track: { height: 6, borderRadius: 3, backgroundColor: COLORS.border, marginTop: 9, overflow: 'hidden' },
  fill: { height: 6, borderRadius: 3, backgroundColor: COLORS.primary },
  body: { fontFamily: FONTS.family.body, color: COLORS.textSecondary, fontSize: 12, lineHeight: 18, marginTop: 5 },
  waiting: { fontFamily: FONTS.family.body, color: COLORS.textMuted, fontSize: 12, lineHeight: 18, marginTop: 5 },
  partial: { fontFamily: FONTS.family.bodyMedium, color: COLORS.textSecondary, fontSize: 11, lineHeight: 16, marginTop: 5 },
  meta: { fontFamily: FONTS.family.body, color: COLORS.textMuted, fontSize: 11, marginTop: 4 },
  disclosure: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: 9, paddingTop: 8, borderTopWidth: 1, borderTopColor: COLORS.border },
  disclosureText: { fontFamily: FONTS.family.bodySemibold, color: COLORS.textSecondary, fontSize: 12 },
  formula: { marginTop: 8, padding: 10, borderRadius: RADIUS.md, backgroundColor: COLORS.backgroundSecondary },
  formulaText: { fontFamily: FONTS.family.body, color: COLORS.textPrimary, fontSize: 12, lineHeight: 18 },
  notMeasure: { fontFamily: FONTS.family.bodyMedium, color: COLORS.textSecondary, fontSize: 11, lineHeight: 16, marginTop: 7 },
  banner: { flexDirection: 'row', gap: 9, alignItems: 'flex-start', backgroundColor: COLORS.primaryLight, borderRadius: RADIUS.lg, borderWidth: 1, borderColor: COLORS.primaryMuted, padding: 13, marginTop: 10 },
  bannerText: { flex: 1, fontFamily: FONTS.family.body, color: COLORS.textPrimary, fontSize: 12, lineHeight: 18 },
  milestone: { flexDirection: 'row', gap: 10, alignItems: 'flex-start', backgroundColor: COLORS.card, borderRadius: RADIUS.lg, borderWidth: 1, borderColor: COLORS.border, padding: 13, marginTop: 8 },
  milestoneTitle: { fontFamily: FONTS.family.bodySemibold, color: COLORS.textPrimary, fontSize: 13 },
  influencing: { fontFamily: FONTS.family.bodyMedium, color: COLORS.primary, fontSize: 11, marginTop: 6 },
  notInfluencing: { fontFamily: FONTS.family.bodyMedium, color: COLORS.textMuted, fontSize: 11, marginTop: 6 },
  actions: { flexDirection: 'row', gap: 16, marginTop: 10, flexWrap: 'wrap' },
  link: { fontFamily: FONTS.family.bodySemibold, color: COLORS.primary, fontSize: 12, marginTop: 8 },
  destructive: { fontFamily: FONTS.family.bodySemibold, color: COLORS.error ?? '#B4231F', fontSize: 12, marginTop: 8 },
  categoryRow: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 11, borderBottomWidth: 1, borderBottomColor: COLORS.border },
  categoryLabel: { fontFamily: FONTS.family.bodyMedium, color: COLORS.textPrimary, fontSize: 13 },
  empty: { backgroundColor: COLORS.card, borderRadius: RADIUS.lg, borderWidth: 1, borderColor: COLORS.border, padding: SPACING.lg, marginTop: 10 },
});
