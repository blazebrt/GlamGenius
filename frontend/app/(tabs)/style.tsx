import React, { useCallback, useState } from 'react';
import { ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { useFocusEffect, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';

import { CATEGORY_META } from '../../src/components/inventory/InventoryPieces';
import { STYLE_CATEGORIES } from '../../src/navigation/finalIA';
import { getInventorySummary, InventoryCategory, InventorySummary } from '../../src/services/apiV2';
import { COLORS, FONTS, RADIUS, SPACING } from '../../src/theme/colors';

export default function StyleScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [summary, setSummary] = useState<InventorySummary | null>(null);

  useFocusEffect(useCallback(() => {
    let active = true;
    void getInventorySummary().then((value) => { if (active) setSummary(value); }).catch(() => undefined);
    return () => { active = false; };
  }, []));

  const openCollection = (category?: InventoryCategory) => router.push({
    pathname: '/(tabs)/inventory', params: { domain: 'style', ...(category ? { category } : {}) },
  });

  return <View style={[styles.container, { paddingTop: insets.top }]}>
    <ScrollView contentContainerStyle={{ padding: SPACING.lg, paddingBottom: insets.bottom + 110 }}>
      <Text style={styles.eyebrow}>STYLE</Text>
      <Text style={styles.title}>Your wearable appearance</Text>
      <Text style={styles.body}>Make the most of what you own, for the day and the occasions that matter.</Text>

      <TouchableOpacity accessibilityRole="button" accessibilityLabel="Style me for an occasion" onPress={() => router.push('/style-me')} style={styles.hero}>
        <Ionicons name="sparkles-outline" size={25} color={COLORS.white} />
        <View style={{ flex: 1 }}><Text style={styles.heroTitle}>Style me for an occasion</Text><Text style={styles.heroBody}>Looks built from your own wardrobe</Text></View>
        <Ionicons name="arrow-forward" size={19} color={COLORS.white} />
      </TouchableOpacity>

      <TouchableOpacity accessibilityRole="button" accessibilityLabel="Should I buy this" onPress={() => router.push('/shopping-check')} style={styles.card}>
        <Ionicons name="pricetag-outline" size={23} color={COLORS.primary} />
        <View style={{ flex: 1 }}><Text style={styles.cardTitle}>Should I buy this?</Text><Text style={styles.body}>An owned-first purchase decision, with the reasoning shown.</Text></View>
        <Ionicons name="chevron-forward" size={18} color={COLORS.textMuted} />
      </TouchableOpacity>

      <View style={styles.sectionHeader}><Text style={styles.section}>What you own</Text><TouchableOpacity accessibilityRole="button" accessibilityLabel="Manage your wardrobe" onPress={() => openCollection()}><Text style={styles.link}>Manage</Text></TouchableOpacity></View>
      <View style={styles.grid}>{STYLE_CATEGORIES.map((category) => <CategoryEntry key={category} category={category} count={summary?.categories[category] || 0} onPress={() => openCollection(category)} />)}</View>
      {!summary?.total_items && <View style={styles.empty}><Text style={styles.emptyTitle}>Start with one thing you already wear</Text><Text style={styles.body}>You do not need to catalogue everything today.</Text><TouchableOpacity accessibilityRole="button" accessibilityLabel="Add a wardrobe item" onPress={() => router.push({ pathname: '/inventory-add', params: { category: 'wardrobe' } })}><Text style={styles.link}>Add a wardrobe item</Text></TouchableOpacity></View>}
    </ScrollView>
  </View>;
}

function CategoryEntry({ category, count, onPress }: { category: InventoryCategory; count: number; onPress: () => void }) {
  const meta = CATEGORY_META[category];
  return <TouchableOpacity accessibilityRole="button" accessibilityLabel={`Open ${meta.label}`} onPress={onPress} style={styles.category}>
    <Ionicons name={meta.icon as never} size={21} color={COLORS.primary} /><View style={{ flex: 1 }}><Text style={styles.categoryName}>{meta.label}</Text><Text style={styles.count}>{count} owned</Text></View><Ionicons name="chevron-forward" size={16} color={COLORS.textMuted} />
  </TouchableOpacity>;
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.backgroundSecondary }, eyebrow: { color: COLORS.primary, fontFamily: FONTS.family.bodySemibold, fontSize: 10, letterSpacing: 1.4 }, title: { color: COLORS.textPrimary, fontFamily: FONTS.family.heading, fontSize: 29, marginTop: 5 }, body: { color: COLORS.textSecondary, fontFamily: FONTS.family.body, fontSize: 13, lineHeight: 19, marginTop: 4 }, hero: { alignItems: 'center', backgroundColor: COLORS.primary, borderRadius: RADIUS.xl, flexDirection: 'row', gap: 12, marginTop: SPACING.lg, padding: SPACING.lg }, heroTitle: { color: COLORS.white, fontFamily: FONTS.family.headingMedium, fontSize: 18 }, heroBody: { color: COLORS.primaryMuted, fontFamily: FONTS.family.body, fontSize: 12, marginTop: 3 }, card: { alignItems: 'center', backgroundColor: COLORS.card, borderColor: COLORS.border, borderRadius: RADIUS.lg, borderWidth: 1, flexDirection: 'row', gap: 11, marginTop: SPACING.md, padding: SPACING.md }, cardTitle: { color: COLORS.textPrimary, fontFamily: FONTS.family.headingMedium, fontSize: 16 }, sectionHeader: { alignItems: 'center', flexDirection: 'row', justifyContent: 'space-between', marginTop: SPACING.xl }, section: { color: COLORS.textPrimary, fontFamily: FONTS.family.headingMedium, fontSize: 19 }, link: { color: COLORS.primary, fontFamily: FONTS.family.bodySemibold, fontSize: 13, marginTop: 8 }, grid: { gap: 9, marginTop: SPACING.sm }, category: { alignItems: 'center', backgroundColor: COLORS.card, borderColor: COLORS.border, borderRadius: RADIUS.lg, borderWidth: 1, flexDirection: 'row', gap: 10, padding: 13 }, categoryName: { color: COLORS.textPrimary, fontFamily: FONTS.family.bodySemibold, fontSize: 14 }, count: { color: COLORS.textMuted, fontFamily: FONTS.family.body, fontSize: 11, marginTop: 2 }, empty: { backgroundColor: COLORS.card, borderColor: COLORS.border, borderRadius: RADIUS.lg, borderWidth: 1, marginTop: SPACING.xl, padding: SPACING.lg }, emptyTitle: { color: COLORS.textPrimary, fontFamily: FONTS.family.headingMedium, fontSize: 17 },
});
