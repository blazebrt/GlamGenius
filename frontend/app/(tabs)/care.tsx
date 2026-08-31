import React, { useCallback, useState } from 'react';
import { ActivityIndicator, RefreshControl, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { useFocusEffect, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';

import { CATEGORY_META } from '../../src/components/inventory/InventoryPieces';
import { CARE_CATEGORIES, countForDomain } from '../../src/navigation/finalIA';
import {
  AttentionAgenda, CareProductControl, InventoryCategory, InventoryItem, InventorySummary, MaintenanceOverview, Routine,
  completeRoutineStep, getImproveOverview, getInventoryItems, getInventorySummary, getMaintenance,
  getRoutinesToday, getTodayAgenda, logInventoryUsage, preferCareProduct, recordMaintenanceDone, setEventReadyActionComplete,
} from '../../src/services/apiV2';
import { COLORS, FONTS, RADIUS, SPACING } from '../../src/theme/colors';

type DailyCareData = { routines: Routine[]; controls: CareProductControl[]; inventory: InventoryItem[]; maintenance: MaintenanceOverview | null; agenda: AttentionAgenda | null };

const remaining = (item: InventoryItem): number | null => typeof item.details?.remaining_percent === 'number' ? item.details.remaining_percent : null;
const controlCategory = (category: InventoryCategory): 'skin_care' | 'hair_care' | null => category === 'beauty' ? 'skin_care' : category === 'hair' ? 'hair_care' : null;

export default function CareScreen() {
  const router = useRouter(); const insets = useSafeAreaInsets();
  const [summary, setSummary] = useState<InventorySummary | null>(null); const [daily, setDaily] = useState<DailyCareData | null>(null);
  const [loading, setLoading] = useState(true); const [busy, setBusy] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      const [routine, overview, maintenance, agenda, skin, hair, shelf] = await Promise.all([
        getRoutinesToday(), getImproveOverview(), getMaintenance(), getTodayAgenda(), getInventoryItems({ page_size: 100, category: 'beauty' }), getInventoryItems({ page_size: 100, category: 'hair' }), getInventorySummary(),
      ]);
      setSummary(shelf); setDaily({ routines: routine.routines, controls: overview.care_product_controls, inventory: [...skin.items, ...hair.items], maintenance, agenda });
    } catch (error) { console.warn('care manager load failed', error); } finally { setLoading(false); }
  }, []);
  useFocusEffect(useCallback(() => { void load(); }, [load]));
  const act = async (key: string, work: () => Promise<unknown>) => { if (busy) return; setBusy(key); try { await work(); await load(); } catch (error) { console.warn('care manager action failed', error); } finally { setBusy(null); } };
  const openCollection = (category?: InventoryCategory) => router.push({ pathname: '/(tabs)/inventory', params: { domain: 'care', ...(category ? { category } : {}) } });
  const low = daily?.inventory.filter((item) => remaining(item) !== null && remaining(item)! <= 15 && item.usage_count > 0) ?? [];
  const events = daily?.agenda?.items.filter((item) => item.source_kind === 'event_ready_action' && item.domain === 'care' && !item.completed) ?? [];
  const dueMaintenance = daily?.maintenance?.kinds.filter((item) => item.status === 'due') ?? [];
  return <View style={[styles.container, { paddingTop: insets.top }]}><ScrollView refreshControl={<RefreshControl refreshing={loading} onRefresh={() => void load()} />} contentContainerStyle={{ padding: SPACING.lg, paddingBottom: insets.bottom + 110 }}>
    <Text style={styles.eyebrow}>CARE TODAY</Text><Text style={styles.title}>What to do, not what to choose.</Text><Text style={styles.body}>Your routine is adjusted from the facts GlamGenius has today. Every action is reversible where it is safe to be.</Text>
    {loading && !daily ? <ActivityIndicator color={COLORS.primary} style={{ marginTop: SPACING.xl }} /> : <>
      <Text style={styles.section}>Today’s routine</Text>
      {daily?.routines.flatMap((routine) => routine.steps.filter((step) => !step.is_gap).map((step) => <DecisionRow key={`${routine.id}:${step.id ?? step.slot}`} title={`${step.completed_today ? 'Done' : 'Do'} · ${step.label}`} reason={step.climate_note || step.plain_english || step.why} action={step.completed_today ? 'Undo' : 'Done'} busy={busy === `step:${step.id}`} onPress={() => step.id && void act(`step:${step.id}`, () => completeRoutineStep(step.id!, !step.completed_today))} />))}
      {!daily?.routines.length && <Empty text="Nothing is due in your routine right now." />}
      {!!low.length && <><Text style={styles.section}>Use what you already have</Text>{low.map((item) => {
        const current = daily?.controls.find((control) => control.inventory_item_id === item.id); const category = controlCategory(item.category);
        const alternative = category && current?.slot ? daily?.controls.find((control) => control.category === category && control.slot === current.slot && control.inventory_item_id !== item.id && control.eligible && !control.paused) : undefined;
        const key = `low:${item.id}`;
        return <DecisionRow key={key} title={`${item.display_name} is running low`} reason={`${remaining(item)}% remains after ${item.usage_count} recorded uses.${alternative ? ` ${alternative.display_name} is already suitable for this routine step.` : ''}`} action={alternative ? `Use ${alternative.display_name}` : 'Log use'} busy={busy === key} onPress={() => void act(key, () => alternative ? preferCareProduct(alternative.inventory_item_id) : logInventoryUsage(item.id, new Date().toISOString().slice(0, 10)))} />;
      })}</>}
      {!!dueMaintenance.length && <><Text style={styles.section}>Upkeep due</Text>{dueMaintenance.map((item) => <DecisionRow key={item.kind} title={item.label} reason={item.reason} action="Done" busy={busy === `maintenance:${item.kind}`} onPress={() => void act(`maintenance:${item.kind}`, () => recordMaintenanceDone(item.kind))} />)}</>}
      {!!events.length && <><Text style={styles.section}>For an upcoming event</Text>{events.map((item) => <DecisionRow key={item.key} title={item.title} reason={item.body} action="Done" busy={busy === `event:${item.key}`} onPress={() => item.event_id && item.source_action_id && void act(`event:${item.key}`, () => setEventReadyActionComplete(item.event_id!, item.source_action_id!, true))} />)}</>}
      <View style={styles.shelfHeader}><Text style={styles.section}>Your shelf</Text><TouchableOpacity accessibilityRole="button" accessibilityLabel="Manage your care items" onPress={() => openCollection()}><Text style={styles.link}>Manage</Text></TouchableOpacity></View>
      <View style={styles.grid}>{CARE_CATEGORIES.map((category) => <CategoryEntry key={category} category={category} count={summary?.categories[category] || 0} onPress={() => openCollection(category)} />)}</View>
      {summary !== null && countForDomain(summary.categories, 'care') === 0 && <Empty text="Start with one product you already own. Care will not prompt you to buy something." />}
    </>}
  </ScrollView></View>;
}

function DecisionRow({ title, reason, action, busy, onPress }: { title: string; reason: string; action: string; busy: boolean; onPress: () => void }) { return <View style={styles.decision} accessibilityLabel={`${title}. Why: ${reason}`}><View style={{ flex: 1 }}><Text style={styles.decisionTitle}>{title}</Text><Text style={styles.reason}>{reason}</Text></View><TouchableOpacity accessibilityRole="button" accessibilityLabel={`${action}: ${title}`} onPress={onPress} disabled={busy} style={styles.action}>{busy ? <ActivityIndicator size="small" color={COLORS.white} /> : <Text style={styles.actionText}>{action}</Text>}</TouchableOpacity></View>; }
function Empty({ text }: { text: string }) { return <View style={styles.empty}><Text style={styles.body}>{text}</Text></View>; }
function CategoryEntry({ category, count, onPress }: { category: InventoryCategory; count: number; onPress: () => void }) { const meta = CATEGORY_META[category]; return <TouchableOpacity accessibilityRole="button" accessibilityLabel={`Open ${meta.label}`} onPress={onPress} style={styles.category}><Ionicons name={meta.icon as never} size={20} color={COLORS.primary} /><View style={{ flex: 1 }}><Text style={styles.categoryName}>{meta.label}</Text><Text style={styles.count}>{count} owned</Text></View><Ionicons name="chevron-forward" size={16} color={COLORS.textMuted} /></TouchableOpacity>; }

const styles = StyleSheet.create({ container: { flex: 1, backgroundColor: COLORS.backgroundSecondary }, eyebrow: { color: COLORS.primary, fontFamily: FONTS.family.bodySemibold, fontSize: 10, letterSpacing: 1.4 }, title: { color: COLORS.textPrimary, fontFamily: FONTS.family.heading, fontSize: 29, marginTop: 5 }, body: { color: COLORS.textSecondary, fontFamily: FONTS.family.body, fontSize: 13, lineHeight: 19, marginTop: 3 }, section: { color: COLORS.textPrimary, fontFamily: FONTS.family.headingMedium, fontSize: 19, marginTop: SPACING.xl }, decision: { alignItems: 'center', backgroundColor: COLORS.card, borderColor: COLORS.border, borderRadius: RADIUS.lg, borderWidth: 1, flexDirection: 'row', gap: 10, marginTop: SPACING.sm, padding: SPACING.md }, decisionTitle: { color: COLORS.textPrimary, fontFamily: FONTS.family.bodySemibold, fontSize: 14 }, reason: { color: COLORS.textSecondary, fontFamily: FONTS.family.body, fontSize: 12, lineHeight: 17, marginTop: 3 }, action: { alignItems: 'center', backgroundColor: COLORS.primary, borderRadius: RADIUS.md, justifyContent: 'center', minHeight: 36, paddingHorizontal: 11 }, actionText: { color: COLORS.white, fontFamily: FONTS.family.bodySemibold, fontSize: 12 }, shelfHeader: { alignItems: 'center', flexDirection: 'row', justifyContent: 'space-between' }, link: { color: COLORS.primary, fontFamily: FONTS.family.bodySemibold, fontSize: 13, marginTop: SPACING.xl }, grid: { gap: 9, marginTop: SPACING.sm }, category: { alignItems: 'center', backgroundColor: COLORS.card, borderColor: COLORS.border, borderRadius: RADIUS.lg, borderWidth: 1, flexDirection: 'row', gap: 10, padding: 13 }, categoryName: { color: COLORS.textPrimary, fontFamily: FONTS.family.bodySemibold, fontSize: 14 }, count: { color: COLORS.textMuted, fontFamily: FONTS.family.body, fontSize: 11, marginTop: 2 }, empty: { backgroundColor: COLORS.card, borderColor: COLORS.border, borderRadius: RADIUS.lg, borderWidth: 1, marginTop: SPACING.sm, padding: SPACING.md } });
