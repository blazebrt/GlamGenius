/**
 * You → Improve.
 *
 * The routine overview, how consistently it is going, what needs attention,
 * what is running out, what is going unused, and which routine steps have
 * nothing in them.
 *
 * Every module here is conditional on there being something to show. A user
 * who has catalogued nothing sees one honest empty state and a way in, not six
 * cards full of zeroes.
 */
import React, { useCallback, useState } from 'react';
import { RefreshControl, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { useFocusEffect, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';

import {
  ConsistencyCard, EmptyModule, ExpiringSection, LowUseSection, RoutineCard, WarningList,
} from '../src/components/routines/RoutinePieces';
import { CareExperienceFeedbackSheet } from '../src/components/routines/CareExperienceFeedback';
import {
  ImproveOverview, Routine, RoutineStep, completeRoutineStep, generateRoutines, getImproveOverview,
} from '../src/services/apiV2';
import { COLORS, FONTS, RADIUS, SPACING } from '../src/theme/colors';

export default function ImproveScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [overview, setOverview] = useState<ImproveOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(false);
  const [feedbackTarget, setFeedbackTarget] = useState<{ routine: Routine; step: RoutineStep } | null>(null);

  const load = useCallback(async (mode: 'initial' | 'refresh' = 'initial') => {
    if (mode === 'refresh') setRefreshing(true);
    setError(false);
    try {
      setOverview(await getImproveOverview());
    } catch (err) {
      console.warn('improve load failed', err);
      setError(true);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useFocusEffect(useCallback(() => { void load('refresh'); }, [load]));

  const build = async () => {
    setBusy(true);
    try {
      await generateRoutines({});
      await load('refresh');
    } catch (err) {
      console.warn('generate failed', err);
    } finally {
      setBusy(false);
    }
  };

  const onComplete = async (step: RoutineStep) => {
    if (!step.id) return;
    try {
      await completeRoutineStep(step.id, !step.completed_today);
      await load('refresh');
    } catch (err) { console.warn('complete failed', err); }
  };

  if (loading && !overview) {
    return <View style={styles.centre}><Text style={styles.muted}>Opening your routines…</Text></View>;
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
            <Text style={styles.title}>Improve</Text>
          </View>
        </View>

        {!overview.has_shelf ? (
          <EmptyModule
            title="Nothing on your shelf yet"
            body="Add a face wash, a moisturiser or a shampoo you already own, and we can build a routine out of what you have."
            actionLabel="Add a product"
            onPress={() => router.push({ pathname: '/inventory-add', params: { category: 'beauty' } })}
          />
        ) : !overview.has_routines ? (
          <EmptyModule
            title="Ready to build your routine"
            body="We will use only the products you have already recorded, in the order they are usually used."
            actionLabel={busy ? 'Building…' : 'Build my routine'}
            onPress={() => void build()}
          />
        ) : (
          <>
            <Text style={styles.section}>Your routines</Text>
            {overview.routines.map((routine) => (
              <RoutineCard
                key={routine.id ?? routine.kind}
                routine={routine}
                onComplete={(step) => void onComplete(step)}
                onFeedback={(step) => setFeedbackTarget({ routine, step })}
              />
            ))}

            <Text style={styles.section}>How it is going</Text>
            <ConsistencyCard consistency={overview.consistency} />
          </>
        )}

        {!!overview.needs_attention.length && (
          <>
            <Text style={styles.section}>Needs your attention</Text>
            <WarningList warnings={overview.needs_attention} />
          </>
        )}

        {overview.has_shelf && (
          <>
            <ExpiringSection report={overview.expiring} />
            <LowUseSection low={overview.low_use} />
          </>
        )}

        {!!overview.missing_categories.length && (
          <>
            <Text style={styles.section}>Steps with nothing in them</Text>
            {/* Named as a category, never as a product to buy. */}
            <WarningList warnings={overview.missing_categories} />
          </>
        )}

        {!!overview.counts.awaiting_confirmation && (
          <TouchableOpacity
            accessibilityRole="button"
            accessibilityLabel="Confirm ingredients we read at low confidence"
            onPress={() => router.push('/(tabs)/inventory')}
            style={styles.confirm}
          >
            <Ionicons name="help-circle-outline" size={19} color={COLORS.primary} />
            <Text style={styles.confirmText}>
              {overview.counts.awaiting_confirmation} ingredient
              {overview.counts.awaiting_confirmation === 1 ? '' : 's'} to confirm. We do not warn about anything until you do.
            </Text>
          </TouchableOpacity>
        )}

        <Text style={styles.disclaimer}>{overview.disclaimer}</Text>
      </ScrollView>
      {!!feedbackTarget?.step.id && (
        <CareExperienceFeedbackSheet
          open={feedbackTarget !== null}
          subjectType="routine_step"
          subjectId={feedbackTarget.step.id}
          subjectLabel={`${feedbackTarget.routine.label} · ${feedbackTarget.step.label}`}
          onClose={() => setFeedbackTarget(null)}
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.backgroundSecondary },
  centre: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 8, backgroundColor: COLORS.background },
  header: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 6 },
  eyebrow: { fontFamily: FONTS.family.bodySemibold, color: COLORS.primary, fontSize: 10, letterSpacing: 1.4 },
  title: { fontFamily: FONTS.family.heading, color: COLORS.textPrimary, fontSize: 29, marginTop: 2 },
  section: { fontFamily: FONTS.family.headingMedium, color: COLORS.textPrimary, fontSize: 18, marginTop: 22 },
  muted: { fontFamily: FONTS.family.body, color: COLORS.textSecondary, fontSize: 13 },
  link: { fontFamily: FONTS.family.bodySemibold, color: COLORS.primary, fontSize: 13 },
  confirm: { flexDirection: 'row', gap: 9, alignItems: 'flex-start', backgroundColor: COLORS.primaryLight, borderRadius: RADIUS.lg, borderWidth: 1, borderColor: COLORS.primaryMuted, padding: 13, marginTop: 14 },
  confirmText: { flex: 1, fontFamily: FONTS.family.body, color: COLORS.textPrimary, fontSize: 12, lineHeight: 18 },
  disclaimer: { fontFamily: FONTS.family.body, color: COLORS.textMuted, fontSize: 11, textAlign: 'center', marginTop: SPACING.lg },
});
