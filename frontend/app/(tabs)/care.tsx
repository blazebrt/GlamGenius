import React, { useCallback, useState } from 'react';
import { ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { useFocusEffect, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';

import { CATEGORY_META } from '../../src/components/inventory/InventoryPieces';
import { CARE_CATEGORIES } from '../../src/navigation/finalIA';
import { getInventorySummary, InventoryCategory, InventorySummary } from '../../src/services/apiV2';
import { COLORS, FONTS, RADIUS, SPACING } from '../../src/theme/colors';

export default function CareScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [summary, setSummary] = useState<InventorySummary | null>(null);
  useFocusEffect(useCallback(() => { let active = true; void getInventorySummary().then((value) => { if (active) setSummary(value); }).catch(() => undefined); return () => { active = false; }; }, []));
  const openCollection = (category?: InventoryCategory) => router.push({ pathname: '/(tabs)/inventory', params: { domain: 'care', ...(category ? { category } : {}) } });

  return <View style={[styles.container, { paddingTop: insets.top }]}><ScrollView contentContainerStyle={{ padding: SPACING.lg, paddingBottom: insets.bottom + 110 }}>
    <Text style={styles.eyebrow}>CARE</Text><Text style={styles.title}>Your routines and shelf</Text><Text style={styles.body}>The products you own, the routines you choose, and safe upkeep timing.</Text>
    <Entry icon="list-outline" title="Your routines" body="Build, simplify, and adjust routines from what you already own." label="Open routines" onPress={() => router.push('/improve')} />
    <Entry icon="flask-outline" title="Your shelf" body="See what is on your shelf and what needs attention." label="Open your shelf" onPress={() => router.push('/shelf')} />
    <Entry icon="calendar-outline" title="Upkeep timing" body="Track the care rhythms you have chosen." label="Open upkeep timing" onPress={() => router.push('/(tabs)/services')} />
    <View style={styles.sectionHeader}><Text style={styles.section}>Owned care items</Text><TouchableOpacity accessibilityRole="button" accessibilityLabel="Manage your care items" onPress={() => openCollection()}><Text style={styles.link}>Manage</Text></TouchableOpacity></View>
    <View style={styles.grid}>{CARE_CATEGORIES.map((category) => <CategoryEntry key={category} category={category} count={summary?.categories[category] || 0} onPress={() => openCollection(category)} />)}</View>
    {!summary?.total_items && <View style={styles.empty}><Text style={styles.emptyTitle}>Start with one product you already own</Text><Text style={styles.body}>Care is for your shelf and routines, never a prompt to buy something.</Text><TouchableOpacity accessibilityRole="button" accessibilityLabel="Add a Skin Care item" onPress={() => router.push({ pathname: '/inventory-add', params: { category: 'beauty' } })}><Text style={styles.link}>Add a Skin Care item</Text></TouchableOpacity></View>}
  </ScrollView></View>;
}

function Entry({ icon, title, body, label, onPress }: { icon: string; title: string; body: string; label: string; onPress: () => void }) { return <TouchableOpacity accessibilityRole="button" accessibilityLabel={label} onPress={onPress} style={styles.entry}><Ionicons name={icon as never} size={23} color={COLORS.primary} /><View style={{ flex: 1 }}><Text style={styles.entryTitle}>{title}</Text><Text style={styles.body}>{body}</Text></View><Ionicons name="chevron-forward" size={18} color={COLORS.textMuted} /></TouchableOpacity>; }
function CategoryEntry({ category, count, onPress }: { category: InventoryCategory; count: number; onPress: () => void }) { const meta = CATEGORY_META[category]; return <TouchableOpacity accessibilityRole="button" accessibilityLabel={`Open ${meta.label}`} onPress={onPress} style={styles.category}><Ionicons name={meta.icon as never} size={20} color={COLORS.primary} /><View style={{ flex: 1 }}><Text style={styles.categoryName}>{meta.label}</Text><Text style={styles.count}>{count} owned</Text></View><Ionicons name="chevron-forward" size={16} color={COLORS.textMuted} /></TouchableOpacity>; }

const styles = StyleSheet.create({ container: { flex: 1, backgroundColor: COLORS.backgroundSecondary }, eyebrow: { color: COLORS.primary, fontFamily: FONTS.family.bodySemibold, fontSize: 10, letterSpacing: 1.4 }, title: { color: COLORS.textPrimary, fontFamily: FONTS.family.heading, fontSize: 29, marginTop: 5 }, body: { color: COLORS.textSecondary, fontFamily: FONTS.family.body, fontSize: 13, lineHeight: 19, marginTop: 3 }, entry: { alignItems: 'center', backgroundColor: COLORS.card, borderColor: COLORS.border, borderRadius: RADIUS.lg, borderWidth: 1, flexDirection: 'row', gap: 11, marginTop: SPACING.md, padding: SPACING.md }, entryTitle: { color: COLORS.textPrimary, fontFamily: FONTS.family.headingMedium, fontSize: 16 }, sectionHeader: { alignItems: 'center', flexDirection: 'row', justifyContent: 'space-between', marginTop: SPACING.xl }, section: { color: COLORS.textPrimary, fontFamily: FONTS.family.headingMedium, fontSize: 19 }, link: { color: COLORS.primary, fontFamily: FONTS.family.bodySemibold, fontSize: 13, marginTop: 8 }, grid: { gap: 9, marginTop: SPACING.sm }, category: { alignItems: 'center', backgroundColor: COLORS.card, borderColor: COLORS.border, borderRadius: RADIUS.lg, borderWidth: 1, flexDirection: 'row', gap: 10, padding: 13 }, categoryName: { color: COLORS.textPrimary, fontFamily: FONTS.family.bodySemibold, fontSize: 14 }, count: { color: COLORS.textMuted, fontFamily: FONTS.family.body, fontSize: 11, marginTop: 2 }, empty: { backgroundColor: COLORS.card, borderColor: COLORS.border, borderRadius: RADIUS.lg, borderWidth: 1, marginTop: SPACING.xl, padding: SPACING.lg }, emptyTitle: { color: COLORS.textPrimary, fontFamily: FONTS.family.headingMedium, fontSize: 17 }, });
