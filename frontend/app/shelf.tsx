/**
 * Inventory → your shelf.
 *
 * Beauty, hair, perfumes and supplements, each with what the deterministic
 * engine worked out about them. Supplements deliberately get a much narrower
 * card than the rest: dates, what you said it is for, and nothing else.
 */
import React, { useCallback, useState } from 'react';
import { RefreshControl, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { useFocusEffect, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';

import {
  EmptyModule, PerfumeCard, SupplementCard, WarningList,
} from '../src/components/routines/RoutinePieces';
import {
  PerfumePick, ShelfSummary, SupplementRow, analyseShelf, getPerfumeRecommendation,
  getShelfSummary, getSupplementsSummary,
} from '../src/services/apiV2';
import { COLORS, FONTS, RADIUS, SPACING } from '../src/theme/colors';

type Tab = 'products' | 'perfumes' | 'supplements';

const TABS: { key: Tab; label: string }[] = [
  { key: 'products', label: 'Beauty & hair' },
  { key: 'perfumes', label: 'Perfumes' },
  { key: 'supplements', label: 'Supplements' },
];

export default function ShelfScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [tab, setTab] = useState<Tab>('products');
  const [summary, setSummary] = useState<ShelfSummary | null>(null);
  const [perfumes, setPerfumes] = useState<{ recommendations: PerfumePick[]; note: string; message: string | null } | null>(null);
  const [supplements, setSupplements] = useState<{
    supplements: SupplementRow[]; we_do_not: string[]; disclaimer: string; message: string | null;
  } | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [analysing, setAnalysing] = useState(false);

  const load = useCallback(async (mode: 'initial' | 'refresh' = 'initial') => {
    if (mode === 'refresh') setRefreshing(true);
    try {
      const [nextSummary, nextPerfumes, nextSupplements] = await Promise.all([
        getShelfSummary(), getPerfumeRecommendation(), getSupplementsSummary(),
      ]);
      setSummary(nextSummary);
      setPerfumes(nextPerfumes);
      setSupplements(nextSupplements);
    } catch (err) {
      console.warn('shelf load failed', err);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useFocusEffect(useCallback(() => { void load('refresh'); }, [load]));

  const reread = async () => {
    setAnalysing(true);
    try { setSummary(await analyseShelf()); }
    catch (err) { console.warn('analyse failed', err); }
    finally { setAnalysing(false); }
  };

  if (loading && !summary) {
    return <View style={styles.centre}><Text style={styles.muted}>Reading your shelf…</Text></View>;
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
            <Text style={styles.eyebrow}>INVENTORY</Text>
            <Text style={styles.title}>Your shelf</Text>
          </View>
        </View>

        <View style={styles.tabs} accessibilityRole="tablist">
          {TABS.map((row) => (
            <TouchableOpacity
              key={row.key}
              accessibilityRole="tab"
              accessibilityLabel={row.label}
              accessibilityState={{ selected: tab === row.key }}
              onPress={() => setTab(row.key)}
              style={[styles.tab, tab === row.key && styles.tabSelected]}
            >
              <Text style={[styles.tabText, tab === row.key && styles.tabTextSelected]}>{row.label}</Text>
            </TouchableOpacity>
          ))}
        </View>

        {tab === 'products' && (summary?.counts.products ? (
          <>
            <View style={styles.statRow}>
              <View style={styles.stat}>
                <Text style={styles.statValue}>{summary.counts.products}</Text>
                <Text style={styles.statLabel}>products</Text>
              </View>
              <View style={styles.stat}>
                <Text style={styles.statValue}>{summary.counts.needs_attention}</Text>
                <Text style={styles.statLabel}>need attention</Text>
              </View>
              <View style={styles.stat}>
                <Text style={styles.statValue}>{summary.counts.awaiting_confirmation}</Text>
                <Text style={styles.statLabel}>to confirm</Text>
              </View>
            </View>
            {!!summary.draft_note && <Text style={styles.muted}>{summary.draft_note}</Text>}

            <TouchableOpacity
              accessibilityRole="button"
              accessibilityLabel="Read my labels again"
              onPress={() => void reread()}
              style={styles.action}
            >
              <Ionicons name="refresh-outline" size={18} color={COLORS.primary} />
              <Text style={styles.actionText}>{analysing ? 'Reading…' : 'Read my labels again'}</Text>
            </TouchableOpacity>

            <WarningList warnings={summary.needs_attention} />

            <TouchableOpacity
              accessibilityRole="button"
              accessibilityLabel="Open routines and improvements"
              onPress={() => router.push('/improve')}
              style={styles.action}
            >
              <Ionicons name="list-outline" size={18} color={COLORS.primary} />
              <Text style={styles.actionText}>See my routines</Text>
            </TouchableOpacity>
          </>
        ) : (
          <EmptyModule
            title="No beauty or hair products yet"
            body="Add one you already own and we can work out where it fits in a routine."
            actionLabel="Add a product"
            onPress={() => router.push({ pathname: '/inventory-add', params: { category: 'beauty' } })}
          />
        ))}

        {tab === 'perfumes' && (perfumes?.recommendations.length ? (
          <>
            {perfumes.recommendations.map((pick) => <PerfumeCard key={pick.inventory_item_id} pick={pick} />)}
            <Text style={styles.muted}>{perfumes.note}</Text>
          </>
        ) : (
          <EmptyModule
            title="No perfumes recorded"
            body={perfumes?.message ?? 'Add one and we can suggest which of yours suits the day.'}
            actionLabel="Add a perfume"
            onPress={() => router.push({ pathname: '/inventory-add', params: { category: 'perfumes' } })}
          />
        ))}

        {tab === 'supplements' && (supplements?.supplements.length ? (
          <>
            {supplements.supplements.map((row) => <SupplementCard key={row.inventory_item_id} row={row} />)}
            <View style={styles.weDoNot} accessibilityLabel="What we do not do">
              <Text style={styles.cardTitle}>What we do not do</Text>
              {supplements.we_do_not.map((line, index) => (
                <Text key={index} style={styles.muted}>• {line}</Text>
              ))}
            </View>
            <Text style={styles.disclaimer}>{supplements.disclaimer}</Text>
          </>
        ) : (
          <EmptyModule
            title="No supplements recorded"
            body={supplements?.message ?? 'We track names and dates only — never how much to take.'}
            actionLabel="Add a supplement"
            onPress={() => router.push({ pathname: '/inventory-add', params: { category: 'supplements' } })}
          />
        ))}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.backgroundSecondary },
  centre: { flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: COLORS.background },
  header: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 14 },
  eyebrow: { fontFamily: FONTS.family.bodySemibold, color: COLORS.primary, fontSize: 10, letterSpacing: 1.4 },
  title: { fontFamily: FONTS.family.heading, color: COLORS.textPrimary, fontSize: 29, marginTop: 2 },
  tabs: { flexDirection: 'row', gap: 7, marginBottom: 12 },
  tab: { flex: 1, paddingVertical: 9, borderRadius: RADIUS.full, borderWidth: 1, borderColor: COLORS.border, alignItems: 'center' },
  tabSelected: { backgroundColor: COLORS.primary, borderColor: COLORS.primary },
  tabText: { fontFamily: FONTS.family.bodyMedium, color: COLORS.textSecondary, fontSize: 11 },
  tabTextSelected: { color: COLORS.white },
  statRow: { flexDirection: 'row', gap: 8 },
  stat: { flex: 1, backgroundColor: COLORS.card, borderRadius: RADIUS.md, padding: 11, borderWidth: 1, borderColor: COLORS.border },
  statValue: { fontFamily: FONTS.family.headingMedium, color: COLORS.primary, fontSize: 22 },
  statLabel: { fontFamily: FONTS.family.body, color: COLORS.textSecondary, fontSize: 10, marginTop: 3 },
  action: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, paddingVertical: 13, borderRadius: RADIUS.lg, backgroundColor: COLORS.card, borderWidth: 1, borderColor: COLORS.border, marginTop: 12 },
  actionText: { fontFamily: FONTS.family.bodySemibold, color: COLORS.textPrimary, fontSize: 12 },
  cardTitle: { fontFamily: FONTS.family.headingMedium, color: COLORS.textPrimary, fontSize: 15 },
  weDoNot: { backgroundColor: COLORS.card, borderRadius: RADIUS.lg, borderWidth: 1, borderColor: COLORS.border, padding: 13, marginTop: 12 },
  muted: { fontFamily: FONTS.family.body, color: COLORS.textSecondary, fontSize: 12, lineHeight: 18, marginTop: 5 },
  disclaimer: { fontFamily: FONTS.family.body, color: COLORS.textMuted, fontSize: 11, marginTop: 12, textAlign: 'center' },
});
