/**
 * Today — the merged Home and Today screen.
 *
 * Opens from the saved plan, so the common case is instant. A background
 * refresh only replaces what is on screen when the server actually sends
 * something different.
 */
import React, { useCallback, useState } from 'react';
import { RefreshControl, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { useFocusEffect, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';

import {
  ActionRow, ClarificationCard, MissingInformation, NeedsInventory, OfflineNotice,
  OptionalModules, OutfitCard, StaleNotice, TodayHeader, TodayLoading, isStale,
} from '../../src/components/today/TodayPieces';
import {
  DailyPlan, LookPiece, PlanAction, answerClarification, completePlanAction,
  getToday, regenerateToday, reportItemUnavailable, sendTodayFeedback,
} from '../../src/services/apiV2';
import { COLORS, FONTS, RADIUS, SPACING } from '../../src/theme/colors';

export default function TodayScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [plan, setPlan] = useState<DailyPlan | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [offline, setOffline] = useState(false);
  const [expanded, setExpanded] = useState(false);

  const load = useCallback(async (mode: 'initial' | 'refresh' = 'initial') => {
    if (mode === 'refresh') setRefreshing(true);
    try {
      setPlan(await getToday());
      setOffline(false);
    } catch (err) {
      console.warn('today load failed', err);
      // Keep whatever we already have on screen rather than blanking it.
      setOffline(true);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  // useFocusEffect fires on mount as well as on every focus, so a separate
  // mount effect would just double the first request.
  useFocusEffect(useCallback(() => { void load('refresh'); }, [load]));

  const apply = (next: DailyPlan) => { setPlan(next); setOffline(false); };

  const onComplete = async (action: PlanAction) => {
    try { apply(await completePlanAction(action.id, !action.completed)); }
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

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <ScrollView
        contentContainerStyle={{ padding: SPACING.lg, paddingBottom: insets.bottom + 110 }}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void load('refresh')} tintColor={COLORS.primary} />}
      >
        {offline && <OfflineNotice />}
        {!offline && isStale(plan) && <StaleNotice onRefresh={() => void load('refresh')} />}

        <TodayHeader plan={plan} />

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
                .filter((action) => action.action_type !== 'wear_outfit')
                .map((action) => (
                  <ActionRow key={action.id} action={action} onComplete={() => void onComplete(action)} />
                ))}
              <OptionalModules
                actions={plan.optional_modules}
                expanded={expanded}
                onToggle={() => setExpanded((value) => !value)}
                onComplete={(action) => void onComplete(action)}
              />
            </>
          )}
        </View>

        <MissingInformation plan={plan} />

        <View style={styles.shortcuts}>
          <TouchableOpacity accessibilityRole="button" accessibilityLabel="Open the weekly planner" onPress={() => router.push('/(tabs)/planner')} style={styles.shortcut}>
            <Ionicons name="calendar-outline" size={19} color={COLORS.primary} />
            <Text style={styles.shortcutText}>Plan the week</Text>
          </TouchableOpacity>
          <TouchableOpacity accessibilityRole="button" accessibilityLabel="Open the skin and hair check" onPress={() => router.push('/(tabs)/scan-tab')} style={styles.shortcut}>
            <Ionicons name="scan-outline" size={19} color={COLORS.primary} />
            <Text style={styles.shortcutText}>Skin & hair check</Text>
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
});
