/**
 * You → Progress.
 *
 * A weekly or monthly view of every metric, each with its own formula on tap.
 *
 * Deliberately opens with the "no single number" note. A screen full of
 * percentages invites the eye to average them, and this product does not have
 * an average — so it says so before showing any of them.
 */
import React, { useCallback, useState } from 'react';
import { RefreshControl, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { useFocusEffect, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';

import {
  ComparisonPanel, EmptyState, GoalCard, MetricCard, MilestoneRow, NoOverallScoreNote,
} from '../src/components/progress/ProgressPieces';
import {
  ComparisonResponse, Goal, ProgressOverview, getComparisons, getProgress, patchGoal,
  recordSelfReport,
} from '../src/services/apiV2';
import { COLORS, FONTS, RADIUS, SPACING } from '../src/theme/colors';

const PERIODS: { key: 'week' | 'month'; label: string }[] = [
  { key: 'week', label: 'This week' },
  { key: 'month', label: 'This month' },
];

const RATINGS: (1 | 2 | 3 | 4 | 5)[] = [1, 2, 3, 4, 5];

export default function ProgressScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [period, setPeriod] = useState<'week' | 'month'>('week');
  const [overview, setOverview] = useState<ProgressOverview | null>(null);
  const [comparison, setComparison] = useState<ComparisonResponse | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(false);
  const [reported, setReported] = useState<string | null>(null);

  const load = useCallback(async (mode: 'initial' | 'refresh' = 'initial', which = period) => {
    if (mode === 'refresh') setRefreshing(true);
    setError(false);
    try {
      const [next, photos] = await Promise.allSettled([getProgress(which), getComparisons('face')]);
      if (next.status === 'fulfilled') setOverview(next.value);
      else setError(true);
      if (photos.status === 'fulfilled') setComparison(photos.value);
    } catch (err) {
      console.warn('progress load failed', err);
      setError(true);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [period]);

  useFocusEffect(useCallback(() => { void load('refresh'); }, [load]));

  const choosePeriod = async (next: 'week' | 'month') => {
    setPeriod(next);
    await load('refresh', next);
  };

  const onAchieve = async (goal: Goal) => {
    try {
      await patchGoal(goal.id, { status: 'achieved' });
      await load('refresh');
    } catch (err) { console.warn('goal update failed', err); }
  };

  const onSelfReport = async (rating: 1 | 2 | 3 | 4 | 5) => {
    try {
      const result = await recordSelfReport(rating);
      setReported(result.message);
      await load('refresh');
    } catch (err) { console.warn('self report failed', err); }
  };

  if (loading && !overview) {
    return <View style={styles.centre}><Text style={styles.muted}>Working out your numbers…</Text></View>;
  }

  if (error || !overview) {
    return (
      <View style={[styles.centre, { paddingTop: insets.top }]}>
        <Text style={styles.muted}>We could not load this right now.</Text>
        <TouchableOpacity accessibilityRole="button" accessibilityLabel="Try again" onPress={() => void load()}>
          <Text style={styles.link}>Try again</Text>
        </TouchableOpacity>
      </View>
    );
  }

  const available = overview.metrics.filter((row) => row.status !== 'unavailable');
  const waiting = overview.metrics.filter((row) => row.status === 'unavailable');

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <ScrollView
        contentContainerStyle={{ padding: SPACING.lg, paddingBottom: insets.bottom + 40 }}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void load('refresh')} tintColor={COLORS.primary} />}
      >
        <View style={styles.header}>
          <TouchableOpacity accessibilityRole="button" accessibilityLabel="Go back" onPress={() => router.back()}>
            <Ionicons name="chevron-back" size={24} color={COLORS.textPrimary} />
          </TouchableOpacity>
          <View>
            <Text style={styles.eyebrow}>YOU</Text>
            <Text style={styles.title}>Progress</Text>
          </View>
        </View>

        <NoOverallScoreNote note={overview.no_overall_score_note} />

        <View style={styles.tabs} accessibilityRole="tablist">
          {PERIODS.map((row) => (
            <TouchableOpacity
              key={row.key}
              accessibilityRole="tab"
              accessibilityLabel={row.label}
              accessibilityState={{ selected: period === row.key }}
              onPress={() => void choosePeriod(row.key)}
              style={[styles.tab, period === row.key && styles.tabSelected]}
            >
              <Text style={[styles.tabText, period === row.key && styles.tabTextSelected]}>{row.label}</Text>
            </TouchableOpacity>
          ))}
        </View>
        <Text style={styles.range}>{overview.period_start} to {overview.period_end}</Text>

        {available.length ? (
          <>
            <Text style={styles.section}>What we can measure</Text>
            {available.map((metric) => (
              <MetricCard
                key={metric.key}
                metric={metric}
                expanded={expanded === metric.key}
                onToggle={() => setExpanded(expanded === metric.key ? null : metric.key)}
              />
            ))}
          </>
        ) : (
          <EmptyState
            title="Nothing to measure yet"
            body="Catalogue a few things and log what you wear, and these fill in on their own."
            actionLabel="Open inventory"
            onPress={() => router.push('/(tabs)/inventory')}
          />
        )}

        {!!overview.goals.length && (
          <>
            <Text style={styles.section}>Your goals</Text>
            {overview.goals.map((goal) => (
              <GoalCard key={goal.id} goal={goal} onAchieve={(row) => void onAchieve(row)} />
            ))}
          </>
        )}

        <Text style={styles.section}>How did you feel this week?</Text>
        <View style={styles.card}>
          {/* The only source for that metric. The app never estimates it. */}
          <Text style={styles.body}>
            Your own answer, and nothing else. We never work this out for you.
          </Text>
          <View style={styles.ratingRow}>
            {RATINGS.map((rating) => (
              <TouchableOpacity
                key={rating}
                accessibilityRole="button"
                accessibilityLabel={`Rate how you felt: ${rating} out of 5`}
                onPress={() => void onSelfReport(rating)}
                style={styles.rating}
              >
                <Text style={styles.ratingText}>{rating}</Text>
              </TouchableOpacity>
            ))}
          </View>
          {!!reported && <Text style={styles.muted}>{reported}</Text>}
        </View>

        {!!comparison && (
          <>
            <Text style={styles.section}>Photo comparison</Text>
            <ComparisonPanel result={comparison} />
          </>
        )}

        {!!overview.milestones.length && (
          <>
            <Text style={styles.section}>What you have reached</Text>
            {overview.milestones.map((milestone) => (
              <MilestoneRow key={milestone.id} milestone={milestone} />
            ))}
          </>
        )}

        {!!waiting.length && (
          <>
            <Text style={styles.section}>Not enough information yet</Text>
            {/* Shown as "waiting", never as a zero. A bar at 0% and a bar with
                no data look the same to a person, and one is an accusation. */}
            {waiting.map((metric) => (
              <MetricCard
                key={metric.key}
                metric={metric}
                expanded={expanded === metric.key}
                onToggle={() => setExpanded(expanded === metric.key ? null : metric.key)}
              />
            ))}
          </>
        )}

        <TouchableOpacity
          accessibilityRole="button"
          accessibilityLabel="Open what GlamGenius remembers"
          onPress={() => router.push('/memory')}
          style={styles.action}
        >
          <Ionicons name="bookmark-outline" size={18} color={COLORS.primary} />
          <Text style={styles.actionText}>What we remember about you</Text>
        </TouchableOpacity>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.backgroundSecondary },
  centre: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 8, backgroundColor: COLORS.background },
  header: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 6 },
  eyebrow: { fontFamily: FONTS.family.bodySemibold, color: COLORS.primary, fontSize: 10, letterSpacing: 1.4 },
  title: { fontFamily: FONTS.family.heading, color: COLORS.textPrimary, fontSize: 29, marginTop: 2 },
  tabs: { flexDirection: 'row', gap: 7, marginTop: 14 },
  tab: { flex: 1, paddingVertical: 9, borderRadius: RADIUS.full, borderWidth: 1, borderColor: COLORS.border, alignItems: 'center' },
  tabSelected: { backgroundColor: COLORS.primary, borderColor: COLORS.primary },
  tabText: { fontFamily: FONTS.family.bodyMedium, color: COLORS.textSecondary, fontSize: 12 },
  tabTextSelected: { color: COLORS.white },
  range: { fontFamily: FONTS.family.body, color: COLORS.textMuted, fontSize: 11, marginTop: 7, textAlign: 'center' },
  section: { fontFamily: FONTS.family.headingMedium, color: COLORS.textPrimary, fontSize: 18, marginTop: 22 },
  card: { backgroundColor: COLORS.card, borderRadius: RADIUS.lg, borderWidth: 1, borderColor: COLORS.border, padding: 13, marginTop: 10 },
  body: { fontFamily: FONTS.family.body, color: COLORS.textSecondary, fontSize: 12, lineHeight: 18 },
  muted: { fontFamily: FONTS.family.body, color: COLORS.textMuted, fontSize: 12, marginTop: 8 },
  link: { fontFamily: FONTS.family.bodySemibold, color: COLORS.primary, fontSize: 13 },
  ratingRow: { flexDirection: 'row', gap: 8, marginTop: 11 },
  rating: { flex: 1, paddingVertical: 12, borderRadius: RADIUS.md, borderWidth: 1, borderColor: COLORS.border, alignItems: 'center' },
  ratingText: { fontFamily: FONTS.family.bodySemibold, color: COLORS.textPrimary, fontSize: 15 },
  action: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, paddingVertical: 13, borderRadius: RADIUS.lg, backgroundColor: COLORS.card, borderWidth: 1, borderColor: COLORS.border, marginTop: 22 },
  actionText: { fontFamily: FONTS.family.bodySemibold, color: COLORS.textPrimary, fontSize: 12 },
});
