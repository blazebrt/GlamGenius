/**
 * Today — the merged Home and Today screen.
 *
 * Opens from the saved plan, so the common case is instant. A background
 * refresh only replaces what is on screen when the server actually sends
 * something different.
 */
import React, { useCallback, useState } from 'react';
import { RefreshControl, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { Redirect, useFocusEffect, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';

import {
  ActionRow, ClarificationCard, EnvironmentDecisionCard, MissingInformation, NeedsInventory, OfflineNotice,
  OptionalModules, OutfitCard, StaleNotice, TodayHeader, TodayLoading, isStale,
} from '../../src/components/today/TodayPieces';
import { TodayCareGuidance, TodayFood, TodayHomeCare, TodayPerfume, TodayRoutineCard } from '../../src/components/routines/TodayRoutine';
import {
  CareGuidance, DailyPlan, HomeCare, LookPiece, NutritionSuggestion, PerfumePick, PlanAction, Routine, RoutineStep,
  answerClarification, completePlanAction, completeRoutineStep, getNutritionSuggestions,
  getPerfumeRecommendation, getRoutinesToday, getToday, getTodayAgenda, regenerateToday, reportItemUnavailable,
  AttentionAgenda,
  sendTodayFeedback,
} from '../../src/services/apiV2';
import { COLORS, FONTS, RADIUS, SPACING } from '../../src/theme/colors';

export default function TodayScreen() {
  return <Redirect href="/scan-product" />;
}

export function RetiredTodayScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [plan, setPlan] = useState<DailyPlan | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [offline, setOffline] = useState(false);
  const [expanded, setExpanded] = useState(false);
  // Phase 6 modules. Each stays null until the server has something to say, and
  // a failure to load one must never blank the outfit.
  const [routines, setRoutines] = useState<Routine[]>([]);
  const [careGuidance, setCareGuidance] = useState<CareGuidance | null>(null);
  const [homeCare, setHomeCare] = useState<HomeCare | null>(null);
  const [perfume, setPerfume] = useState<PerfumePick | null>(null);
  const [food, setFood] = useState<NutritionSuggestion | null>(null);
  const [agenda, setAgenda] = useState<AttentionAgenda | null>(null);

  const loadModules = useCallback(async () => {
    const [routineResult, perfumeResult, foodResult] = await Promise.allSettled([
      getRoutinesToday(), getPerfumeRecommendation(), getNutritionSuggestions(),
    ]);
    if (routineResult.status === 'fulfilled') {
      setRoutines(routineResult.value.routines);
      setCareGuidance(routineResult.value.care_guidance ?? null);
      setHomeCare(routineResult.value.home_care ?? null);
    }
    if (perfumeResult.status === 'fulfilled') setPerfume(perfumeResult.value.recommendations[0] ?? null);
    if (foodResult.status === 'fulfilled') {
      setFood(foodResult.value.enabled ? foodResult.value.suggestions[0] ?? null : null);
    }
  }, []);

  const load = useCallback(async (mode: 'initial' | 'refresh' = 'initial') => {
    if (mode === 'refresh') setRefreshing(true);
    try {
      setPlan(await getToday());
      void getTodayAgenda().then(setAgenda).catch(() => setAgenda(null));
      void loadModules();
      setOffline(false);
    } catch (err) {
      console.warn('today load failed', err);
      // Keep whatever we already have on screen rather than blanking it.
      setOffline(true);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [loadModules]);

  // useFocusEffect fires on mount as well as on every focus, so a separate
  // mount effect would just double the first request.
  useFocusEffect(useCallback(() => { void load('refresh'); }, [load]));

  const apply = (next: DailyPlan) => { setPlan(next); setOffline(false); };

  const onComplete = async (action: PlanAction) => {
    try {
      apply(await completePlanAction(action.id, !action.completed));
      const nextAgenda = await getTodayAgenda();
      setAgenda(nextAgenda);
    }
    catch (err) { console.warn('complete failed', err); }
  };

  const onAnswer = async (value: string) => {
    if (!plan?.clarification) return;
    try { apply(await answerClarification(plan.clarification.key, value)); }
    catch (err) { console.warn('clarify failed', err); }
  };

  const onUnavailable = async (piece: LookPiece) => {
    if (!piece.inventory_item_id) return;
    try { apply(await reportItemUnavailable(piece.inventory_item_id, 'in_wash')); }
    catch (err) { console.warn('unavailable failed', err); }
  };

  const onWore = async () => {
    try { apply(await sendTodayFeedback('wore_it')); }
    catch (err) { console.warn('feedback failed', err); }
  };

  const onSomethingElse = async () => {
    try { apply(await regenerateToday('not_my_style')); }
    catch (err) { console.warn('regenerate failed', err); }
  };

  const onRoutineStep = async (step: RoutineStep) => {
    if (!step.id) return;
    try {
      await completeRoutineStep(step.id, !step.completed_today);
      await loadModules();
    } catch (err) { console.warn('routine step failed', err); }
  };

  if (loading && !plan) return <View style={styles.container}><TodayLoading /></View>;

  if (!plan) {
    return (
      <View style={[styles.container, styles.centre, { paddingTop: insets.top }]}>
        <Text style={styles.body}>We could not load your day.</Text>
        <TouchableOpacity accessibilityRole="button" accessibilityLabel="Try again" onPress={() => void load()}>
          <Text style={styles.link}>Try again</Text>
        </TouchableOpacity>
      </View>
    );
  }

  const agendaActionIds = new Set(
    (agenda?.items ?? []).filter((item) => item.source_kind === 'today_action' && item.source_action_id).map((item) => item.source_action_id as string),
  );

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <ScrollView
        contentContainerStyle={{ padding: SPACING.lg, paddingBottom: insets.bottom + 110 }}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void load('refresh')} tintColor={COLORS.primary} />}
      >
        {offline && <OfflineNotice />}
        {!offline && isStale(plan) && <StaleNotice onRefresh={() => void load('refresh')} />}

        <TodayHeader plan={plan} />

        <NextUp agenda={agenda} plan={plan} onComplete={(action) => void onComplete(action)} onOpen={(destination, params) => {
          if (!['/(tabs)/today', '/(tabs)/style', '/(tabs)/care', '/(tabs)/plan', '/event-ready', '/improve', '/(tabs)/services', '/(tabs)/inventory'].includes(destination)) return;
          if (destination === '/event-ready') router.push({ pathname: destination, params });
          else router.push(destination as never);
        }} />

        {plan.needs_clarification && plan.clarification && (
          <View style={{ marginTop: SPACING.lg }}>
            <ClarificationCard clarification={plan.clarification} onAnswer={(value) => void onAnswer(value)} />
          </View>
        )}

        <View style={{ marginTop: SPACING.lg }}>
          {plan.status === 'needs_inventory' ? (
            <NeedsInventory plan={plan} onAdd={() => router.push('/inventory-add')} />
          ) : (
            <>
              <OutfitCard
                plan={plan}
                onWore={() => void onWore()}
                onNotForMe={() => void onSomethingElse()}
                onUnavailable={(piece) => void onUnavailable(piece)}
              />
              {plan.primary
                .filter((action) => action.action_type !== 'wear_outfit' && !agendaActionIds.has(action.id))
                .map((action) => (action.action_type === 'environment_decision' ? (
                  // A decision, not a task. Nothing here is ticked off.
                  <EnvironmentDecisionCard key={action.id} action={action} />
                ) : (
                  <ActionRow key={action.id} action={action} onComplete={() => void onComplete(action)} />
                )))}
              <OptionalModules
                actions={plan.optional_modules.filter((action) => !agendaActionIds.has(action.id))}
                expanded={expanded}
                onToggle={() => setExpanded((value) => !value)}
                onComplete={(action) => void onComplete(action)}
              />
            </>
          )}
        </View>

        <TodayCareGuidance guidance={careGuidance} />
        {routines.map((routine) => (
          <TodayRoutineCard
            key={routine.id ?? routine.kind}
            routine={routine}
            onComplete={(step) => void onRoutineStep(step)}
            onOpen={() => router.push('/improve')}
          />
        ))}
        <TodayHomeCare homeCare={homeCare} />
        <TodayPerfume pick={perfume} />
        <TodayFood suggestion={food} />

        <MissingInformation plan={plan} />

        <View style={styles.shortcuts}>
          <TouchableOpacity accessibilityRole="button" accessibilityLabel="Open the weekly plan" onPress={() => router.push('/(tabs)/plan')} style={styles.shortcut}>
            <Ionicons name="calendar-outline" size={19} color={COLORS.primary} />
            <Text style={styles.shortcutText}>Plan the week</Text>
          </TouchableOpacity>
          <TouchableOpacity accessibilityRole="button" accessibilityLabel="Open my routines" onPress={() => router.push('/(tabs)/care')} style={styles.shortcut}>
            <Ionicons name="list-outline" size={19} color={COLORS.primary} />
            <Text style={styles.shortcutText}>My routines</Text>
          </TouchableOpacity>
        </View>

        <Text style={styles.disclaimer}>{plan.disclaimer}</Text>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.backgroundSecondary },
  centre: { alignItems: 'center', justifyContent: 'center', gap: 8 },
  body: { fontFamily: FONTS.family.body, fontSize: 13, color: COLORS.textSecondary },
  link: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.primary },
  shortcuts: { flexDirection: 'row', gap: 10, marginTop: SPACING.lg },
  shortcut: { flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, paddingVertical: 14, borderRadius: RADIUS.lg, backgroundColor: COLORS.card, borderWidth: 1, borderColor: COLORS.border },
  shortcutText: { fontFamily: FONTS.family.bodySemibold, fontSize: 12, color: COLORS.textPrimary },
  disclaimer: { fontFamily: FONTS.family.body, fontSize: 11, color: COLORS.textMuted, textAlign: 'center', marginTop: SPACING.lg },
  nextUp: { marginTop: SPACING.lg, backgroundColor: COLORS.card, borderColor: COLORS.border, borderWidth: 1, borderRadius: RADIUS.lg, padding: SPACING.md },
  nextTitle: { fontFamily: FONTS.family.headingMedium, fontSize: 18, color: COLORS.textPrimary },
  nextItem: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 10 },
  nextSecondary: { borderTopWidth: 1, borderTopColor: COLORS.border },
  nextItemTitle: { fontFamily: FONTS.family.bodySemibold, fontSize: 14, color: COLORS.textPrimary },
  nextBody: { fontFamily: FONTS.family.body, fontSize: 12, lineHeight: 17, color: COLORS.textSecondary, marginTop: 2 },
  nextComplete: { borderColor: COLORS.primary, borderWidth: 1, borderRadius: RADIUS.md, paddingHorizontal: 10, paddingVertical: 6 },
  nextCompleteText: { fontFamily: FONTS.family.bodySemibold, fontSize: 12, color: COLORS.primary },
});

function NextUp({ agenda, plan, onComplete, onOpen }: { agenda: AttentionAgenda | null; plan: DailyPlan; onComplete: (action: PlanAction) => void; onOpen: (destination: string, params: Record<string, string>) => void }) {
  if (!agenda || agenda.items.length === 0) return null;
  const planActions = [...plan.primary, ...plan.optional_modules];
  return <View style={styles.nextUp} accessibilityLabel="Next up">
    <Text style={styles.nextTitle}>Next up</Text>
    {agenda.items.slice(0, 3).map((item, index) => {
      const sourceAction = item.source_kind === 'today_action' && item.source_action_id
        ? planActions.find((action) => action.id === item.source_action_id)
        : undefined;
      return <View key={item.key} style={[styles.nextItem, index > 0 && styles.nextSecondary]}>
        <TouchableOpacity accessibilityRole="button" accessibilityLabel={`Open ${item.title}`} onPress={() => onOpen(item.destination, item.destination_params)} style={{ flex: 1 }}>
          <Text style={styles.nextItemTitle}>{item.title}</Text><Text style={styles.nextBody}>{item.body}</Text>
        </TouchableOpacity>
        {sourceAction && <TouchableOpacity accessibilityRole="button" accessibilityLabel={`Complete ${item.title}`} onPress={() => onComplete(sourceAction)} style={styles.nextComplete}>
          <Text style={styles.nextCompleteText}>Done</Text>
        </TouchableOpacity>}
        {!sourceAction && <Ionicons name="chevron-forward" size={17} color={COLORS.textMuted} />}
      </View>;
    })}
  </View>;
}
