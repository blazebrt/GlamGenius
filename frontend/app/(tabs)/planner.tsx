/**
 * The weekly planner — Monday to Sunday.
 *
 * Moving a day is two taps rather than a drag: pick the day, pick where it
 * goes. It reaches the same outcome as drag-and-drop, works with one thumb on
 * a phone, and is usable with a screen reader, which a drag is not.
 */
import React, { useCallback, useRef, useState } from 'react';
import { ActivityIndicator, RefreshControl, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { useFocusEffect, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';

import {
  DayCard, LaundryPanel, RepetitionPanel, SwapBar, UpcomingEvents, WeekEmpty, repeatedOn,
} from '../../src/components/planner/PlannerPieces';
import {
  CalendarEvent, PlannerDay, WeeklyPlan, generateWeek, getUpcomingEvents, getWeek, lockPlannerDay, patchPlannerDay,
} from '../../src/services/apiV2';
import { COLORS, FONTS, RADIUS, SPACING } from '../../src/theme/colors';

export default function PlannerScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [plan, setPlan] = useState<WeeklyPlan | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [moving, setMoving] = useState<PlannerDay | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [upcoming, setUpcoming] = useState<CalendarEvent[]>([]);

  const load = useCallback(async (mode: 'initial' | 'refresh' = 'initial') => {
    if (mode === 'refresh') setRefreshing(true);
    try {
      setPlan(await getWeek());
      setError(null);
    } catch (err) {
      console.warn('week load failed', err);
      setError('We could not load your week.');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  const loadUpcoming = useCallback(async () => {
    try {
      setUpcoming((await getUpcomingEvents()).events);
    } catch (err) {
      // The weekly planner remains useful when this optional read is offline.
      console.warn('upcoming events load failed', err);
    }
  }, []);

  // `plan` must NOT be a dependency here. Every successful load sets it to a
  // new object, which would change this callback, re-run the effect, and
  // refetch forever while the tab is focused. A ref carries the "have we
  // loaded once" bit without changing the callback's identity.
  const hasLoaded = useRef(false);
  useFocusEffect(useCallback(() => {
    void load(hasLoaded.current ? 'refresh' : 'initial');
    void loadUpcoming();
    hasLoaded.current = true;
  }, [load, loadUpcoming]));

  const run = async (action: () => Promise<WeeklyPlan>) => {
    setBusy(true);
    setError(null);
    try {
      setPlan(await action());
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setError(typeof detail?.message === 'string' ? detail.message : 'That did not work. Please try again.');
    } finally {
      setBusy(false);
    }
  };

  const onMove = async (target: string) => {
    if (!moving) return;
    const from = moving.plan_date;
    setMoving(null);
    await run(() => patchPlannerDay(from, { swap_with_date: target }));
  };

  if (loading && !plan) {
    return <View style={[styles.container, styles.centre]}><ActivityIndicator color={COLORS.primary} /></View>;
  }

  const notGenerated = !plan || plan.status === 'not_generated';
  const repeats = plan ? repeatedOn(plan) : {};

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <ScrollView
        contentContainerStyle={{ padding: SPACING.lg, paddingBottom: insets.bottom + 110 }}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void load('refresh')} tintColor={COLORS.primary} />}
      >
        <Text style={styles.eyebrow}>PLAN / PLANNER</Text>

        <UpcomingEvents
          events={upcoming}
          onEventPress={(event) => router.push({ pathname: '/event-ready', params: { eventId: event.id } })}
          onAdd={() => router.push('/event-add')}
        />

        <Text style={styles.eyebrow}>MONDAY TO SUNDAY</Text>
        <Text style={styles.title}>Your week</Text>

        {!!error && (
          <View style={styles.error} accessibilityLabel="Something went wrong">
            <Ionicons name="alert-circle-outline" size={18} color={COLORS.error} />
            <Text style={styles.errorText}>{error}</Text>
          </View>
        )}

        {notGenerated ? (
          <WeekEmpty busy={busy} onGenerate={() => void run(() => generateWeek())} />
        ) : (
          <>
            {moving && plan && (
              <SwapBar
                from={moving}
                days={plan.days}
                onPick={(target) => void onMove(target)}
                onCancel={() => setMoving(null)}
              />
            )}

            {plan!.days.map((day) => (
              <DayCard
                key={day.plan_date}
                day={day}
                repeats={repeats[day.plan_date]}
                selected={moving?.plan_date === day.plan_date}
                onPress={() => setMoving(moving?.plan_date === day.plan_date ? null : day)}
                onLock={() => void run(() => lockPlannerDay(day.plan_date, !day.locked))}
                onRegenerate={() => void run(() => patchPlannerDay(day.plan_date, { regenerate: true }))}
              />
            ))}

            <RepetitionPanel plan={plan!} />
            <LaundryPanel plan={plan!} />

            <TouchableOpacity
              accessibilityRole="button"
              accessibilityLabel="Rebuild the whole week"
              disabled={busy}
              onPress={() => void run(() => generateWeek())}
              style={[styles.outline, busy && styles.disabled]}
            >
              <Text style={styles.outlineText}>{busy ? 'Working…' : 'Rebuild the week'}</Text>
            </TouchableOpacity>
            <Text style={styles.note}>Locked days are never changed when you rebuild.</Text>
          </>
        )}

        <TouchableOpacity accessibilityRole="button" accessibilityLabel="Back to today" onPress={() => router.push('/(tabs)/today')}>
          <Text style={styles.link}>Back to today</Text>
        </TouchableOpacity>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.backgroundSecondary },
  centre: { alignItems: 'center', justifyContent: 'center' },
  eyebrow: { fontFamily: FONTS.family.bodySemibold, color: COLORS.primary, fontSize: 10, letterSpacing: 1.3 },
  title: { fontFamily: FONTS.family.heading, color: COLORS.textPrimary, fontSize: 28, marginTop: 4, marginBottom: SPACING.lg },
  error: { flexDirection: 'row', alignItems: 'center', gap: 8, padding: SPACING.sm, borderRadius: RADIUS.md, backgroundColor: COLORS.errorLight, marginBottom: SPACING.md },
  errorText: { flex: 1, fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.textPrimary },
  outline: { alignItems: 'center', justifyContent: 'center', borderRadius: RADIUS.full, paddingVertical: 13, borderWidth: 1, borderColor: COLORS.border, marginTop: SPACING.lg },
  outlineText: { fontFamily: FONTS.family.bodySemibold, fontSize: 14, color: COLORS.textPrimary },
  disabled: { opacity: 0.6 },
  note: { fontFamily: FONTS.family.body, fontSize: 11, color: COLORS.textMuted, textAlign: 'center', marginTop: 8 },
  link: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.primary, textAlign: 'center', marginTop: SPACING.lg },
});
