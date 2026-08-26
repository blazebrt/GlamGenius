import React, { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, ScrollView, StyleSheet, Text, TextInput, TouchableOpacity, View } from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';

import { CATEGORY_META, CategoryTile, GuidedSprint, InventoryItemCard, InventoryRecovery } from '../../src/components/inventory/InventoryPieces';
import { INVENTORY_CATEGORIES, InventoryCategory, InventoryItem, InventorySummary, getInventoryItems, getInventorySummary } from '../../src/services/apiV2';
import { COLORS, FONTS, RADIUS, SPACING } from '../../src/theme/colors';
import { categoriesForDomain } from '../../src/navigation/finalIA';

export default function InventoryScreen() {
  const router = useRouter(); const insets = useSafeAreaInsets();
  const params = useLocalSearchParams<{ domain?: string; category?: string }>();
  const domain = typeof params.domain === 'string' ? params.domain : undefined;
  const allowedCategories = categoriesForDomain(domain);
  const requestedCategory = typeof params.category === 'string' && allowedCategories?.includes(params.category as InventoryCategory)
    ? params.category as InventoryCategory : undefined;
  const [summary, setSummary] = useState<InventorySummary | null>(null); const [items, setItems] = useState<InventoryItem[]>([]);
  const [query, setQuery] = useState(''); const [category, setCategory] = useState<InventoryCategory | undefined>(requestedCategory);
  const [showFilters, setShowFilters] = useState(false); const [brand, setBrand] = useState(''); const [colour, setColour] = useState('');
  const [ingredient, setIngredient] = useState(''); const [occasion, setOccasion] = useState(''); const [usageLevel, setUsageLevel] = useState<'unused' | 'low' | 'regular' | undefined>();
  const [loading, setLoading] = useState(true); const [error, setError] = useState(false);
  useEffect(() => { setCategory(requestedCategory); }, [requestedCategory]);
  const load = useCallback(async () => {
    setLoading(true); setError(false);
    try {
      const filters = { q: query || undefined, brand: brand || undefined, colour: colour || undefined, ingredient: ingredient || undefined, occasion: occasion || undefined, usage_level: usageLevel, page_size: 20 };
      const [nextSummary, listings] = await Promise.all([
        getInventorySummary(),
        category ? Promise.all([getInventoryItems({ ...filters, category })]) : allowedCategories
          ? Promise.all(allowedCategories.map((allowed) => getInventoryItems({ ...filters, category: allowed })))
          : Promise.all([getInventoryItems(filters)]),
      ]);
      setSummary(nextSummary);
      const scopedItems = listings.flatMap((listing) => listing.items).filter((item) => !allowedCategories || allowedCategories.includes(item.category));
      setItems([...new Map(scopedItems.map((item) => [item.id, item])).values()]);
    }
    catch (err) { console.warn('inventory load failed', err); setError(true); }
    finally { setLoading(false); }
  }, [query, category, brand, colour, ingredient, occasion, usageLevel, allowedCategories]);
  useEffect(() => { void load(); }, [load]);
  const add = (selected?: InventoryCategory) => {
    const defaultCategory = selected || category || allowedCategories?.[0];
    router.push({ pathname: '/inventory-add', params: { ...(domain ? { domain } : {}), ...(defaultCategory ? { category: defaultCategory } : {}) } });
  };

  if (loading && !summary) return <View style={styles.center}><ActivityIndicator color={COLORS.primary} /><Text style={styles.muted}>Opening your inventory…</Text></View>;
  if (error || !summary) return <View style={[styles.center, { paddingTop: insets.top }]}><InventoryRecovery onRetry={() => void load()} onAdd={() => add()} /></View>;
  return <View style={[styles.container, { paddingTop: insets.top }]}><ScrollView contentContainerStyle={{ padding: SPACING.lg, paddingBottom: insets.bottom + 110 }} keyboardShouldPersistTaps="handled">
    <View style={styles.header}><View><Text style={styles.eyebrow}>{domain === 'care' ? 'YOUR SHELF' : domain === 'style' ? 'YOUR WARDROBE' : 'YOUR COLLECTION'}</Text><Text style={styles.title}>{domain === 'care' ? 'Care items' : domain === 'style' ? 'Style items' : 'Inventory'}</Text></View><TouchableOpacity accessibilityRole="button" accessibilityLabel="Add inventory item" onPress={() => add()} style={styles.add}><Ionicons name="add" size={23} color={COLORS.white} /></TouchableOpacity></View>
    <Text style={styles.subtitle}>Everything you own for getting ready, organised without judgement.</Text>
    <View style={styles.search}><Ionicons name="search-outline" size={19} color={COLORS.textMuted} /><TextInput accessibilityLabel="Search inventory" value={query} onChangeText={setQuery} placeholder="Search brand, colour, ingredient…" placeholderTextColor={COLORS.textMuted} returnKeyType="search" style={styles.searchInput} /><TouchableOpacity accessibilityRole="button" accessibilityLabel="Show inventory filters" onPress={() => setShowFilters(!showFilters)}><Ionicons name="options-outline" size={20} color={COLORS.primary} /></TouchableOpacity></View>
    {showFilters && <View style={styles.filters} accessibilityLabel="Inventory filters"><View style={styles.filterRow}><TextInput accessibilityLabel="Filter by brand" value={brand} onChangeText={setBrand} placeholder="Brand" placeholderTextColor={COLORS.textMuted} style={styles.filterInput} /><TextInput accessibilityLabel="Filter by colour" value={colour} onChangeText={setColour} placeholder="Colour" placeholderTextColor={COLORS.textMuted} style={styles.filterInput} /></View><View style={styles.filterRow}><TextInput accessibilityLabel="Filter by ingredient" value={ingredient} onChangeText={setIngredient} placeholder="Ingredient" placeholderTextColor={COLORS.textMuted} style={styles.filterInput} /><TextInput accessibilityLabel="Filter by occasion" value={occasion} onChangeText={setOccasion} placeholder="Occasion" placeholderTextColor={COLORS.textMuted} style={styles.filterInput} /></View><View style={styles.usageRow}>{(['unused', 'low', 'regular'] as const).map((value) => <TouchableOpacity key={value} accessibilityRole="button" accessibilityLabel={`Filter ${value} usage`} accessibilityState={{ selected: usageLevel === value }} onPress={() => setUsageLevel(usageLevel === value ? undefined : value)} style={[styles.usageChip, usageLevel === value && styles.usageSelected]}><Text style={[styles.usageText, usageLevel === value && styles.usageTextSelected]}>{value}</Text></TouchableOpacity>)}</View></View>}

    {!allowedCategories && summary.total_items < 10 && <GuidedSprint summary={summary} onAdd={add} />}
    <Text style={styles.sectionTitle}>{domain === 'care' ? 'Your shelf' : domain === 'style' ? 'Your wardrobe' : 'Your categories'}</Text><View style={styles.grid}>{(allowedCategories || INVENTORY_CATEGORIES).map((key) => <CategoryTile key={key} category={key} count={summary.categories[key]} onPress={() => setCategory(category === key ? undefined : key)} />)}</View>

    {!allowedCategories && <><Text style={styles.sectionTitle}>Needs your attention</Text><View style={styles.insightRow}>
      <TouchableOpacity accessibilityRole="button" accessibilityLabel="Open low-use products" onPress={() => router.push({ pathname: '/inventory-insights', params: { view: 'low-use' } })} style={styles.insight}><Text style={styles.insightValue}>{summary.low_use_products}</Text><Text style={styles.insightLabel}>Low-Use Products</Text></TouchableOpacity>
      <TouchableOpacity accessibilityRole="button" accessibilityLabel="Open expiring products" onPress={() => router.push({ pathname: '/inventory-insights', params: { view: 'expiring' } })} style={styles.insight}><Text style={styles.insightValue}>{summary.products_expiring_soon}</Text><Text style={styles.insightLabel}>Expiring Soon</Text></TouchableOpacity>
      <TouchableOpacity accessibilityRole="button" accessibilityLabel="Open duplicate candidates" onPress={() => router.push({ pathname: '/inventory-insights', params: { view: 'duplicates' } })} style={styles.insight}><Text style={styles.insightValue}>{summary.duplicate_candidates}</Text><Text style={styles.insightLabel}>Duplicates</Text></TouchableOpacity>
    </View>
      <TouchableOpacity accessibilityRole="button" accessibilityLabel="Open your shelf" onPress={() => router.push('/shelf')} style={styles.shelfBanner}><Ionicons name="flask-outline" size={20} color={COLORS.primary} /><View style={{ flex: 1 }}><Text style={styles.shelfTitle}>Your shelf</Text><Text style={styles.shelfBody}>Skin Care, Hair Care, perfumes and supplements — what fits where, and what needs attention.</Text></View><Ionicons name="chevron-forward" size={18} color={COLORS.primary} /></TouchableOpacity>
      <TouchableOpacity accessibilityRole="button" accessibilityLabel="Open Value to Recover" onPress={() => router.push({ pathname: '/inventory-insights', params: { view: 'value' } })} style={styles.valueBanner}><View><Text style={styles.valueLabel}>ESTIMATED VALUE TO RECOVER</Text><Text style={styles.value}>₹{summary.at_risk_value.toLocaleString('en-IN')}</Text></View><View style={{ flex: 1 }} /><Text style={styles.estimate}>Estimate</Text><Ionicons name="chevron-forward" size={18} color={COLORS.primary} /></TouchableOpacity></>}
    {allowedCategories && <><Text style={styles.sectionTitle}>Owned-item insights</Text><View style={styles.insightRow}>
      <TouchableOpacity accessibilityRole="button" accessibilityLabel={`Open ${domain} low-use products`} onPress={() => router.push({ pathname: '/inventory-insights', params: { view: 'low-use', domain } })} style={styles.insight}><Text style={styles.insightLabel}>Low-use products</Text></TouchableOpacity>
      <TouchableOpacity accessibilityRole="button" accessibilityLabel={`Open ${domain} expiring products`} onPress={() => router.push({ pathname: '/inventory-insights', params: { view: 'expiring', domain } })} style={styles.insight}><Text style={styles.insightLabel}>Expiring products</Text></TouchableOpacity>
      <TouchableOpacity accessibilityRole="button" accessibilityLabel={`Open ${domain} duplicate candidates`} onPress={() => router.push({ pathname: '/inventory-insights', params: { view: 'duplicates', domain } })} style={styles.insight}><Text style={styles.insightLabel}>Duplicate candidates</Text></TouchableOpacity>
    </View></>}

    <View style={styles.sectionHeader}><Text style={styles.sectionTitle}>{category ? `In ${CATEGORY_META[category].label}` : query || brand || colour || ingredient || occasion || usageLevel ? 'Filtered results' : 'Recently added'}</Text>{(query || category || brand || colour || ingredient || occasion || usageLevel) && <TouchableOpacity accessibilityRole="button" accessibilityLabel="Clear inventory filters" onPress={() => { setQuery(''); setCategory(undefined); setBrand(''); setColour(''); setIngredient(''); setOccasion(''); setUsageLevel(undefined); }}><Text style={styles.clear}>Clear</Text></TouchableOpacity>}</View>
    {items.length ? items.map((item) => <InventoryItemCard key={item.id} item={item} onPress={() => router.push({ pathname: '/inventory-item', params: { id: item.id } })} />) : <View style={styles.empty}><Ionicons name="archive-outline" size={28} color={COLORS.primary} /><Text style={styles.emptyTitle}>{query || category ? 'No matching items' : 'Start with one useful item'}</Text><Text style={styles.muted}>You do not need to catalogue everything today.</Text><TouchableOpacity accessibilityRole="button" accessibilityLabel="Add first inventory item" onPress={() => add(category)}><Text style={styles.clear}>Add an item</Text></TouchableOpacity></View>}
  </ScrollView></View>;
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.backgroundSecondary }, center: { flex: 1, backgroundColor: COLORS.background, alignItems: 'center', justifyContent: 'center' }, header: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }, eyebrow: { fontFamily: FONTS.family.bodySemibold, color: COLORS.primary, fontSize: 10, letterSpacing: 1.4 }, title: { fontFamily: FONTS.family.heading, color: COLORS.textPrimary, fontSize: 31, marginTop: 2 }, subtitle: { fontFamily: FONTS.family.body, color: COLORS.textSecondary, fontSize: 13, lineHeight: 19, marginTop: 5, marginBottom: 15 }, add: { width: 44, height: 44, borderRadius: 22, backgroundColor: COLORS.primary, alignItems: 'center', justifyContent: 'center' },
  search: { backgroundColor: COLORS.card, borderRadius: RADIUS.full, borderWidth: 1, borderColor: COLORS.border, paddingHorizontal: 14, flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 16 }, searchInput: { flex: 1, paddingVertical: 12, color: COLORS.textPrimary, fontFamily: FONTS.family.body }, filters: { backgroundColor: COLORS.card, borderRadius: RADIUS.lg, borderWidth: 1, borderColor: COLORS.border, padding: 11, marginTop: -8, marginBottom: 16 }, filterRow: { flexDirection: 'row', gap: 8, marginBottom: 8 }, filterInput: { flex: 1, borderWidth: 1, borderColor: COLORS.border, borderRadius: RADIUS.md, paddingHorizontal: 10, paddingVertical: 9, fontFamily: FONTS.family.body, color: COLORS.textPrimary, fontSize: 12 }, usageRow: { flexDirection: 'row', gap: 7 }, usageChip: { borderWidth: 1, borderColor: COLORS.border, borderRadius: RADIUS.full, paddingHorizontal: 11, paddingVertical: 7 }, usageSelected: { backgroundColor: COLORS.primary, borderColor: COLORS.primary }, usageText: { fontFamily: FONTS.family.bodyMedium, color: COLORS.textSecondary, fontSize: 11, textTransform: 'capitalize' }, usageTextSelected: { color: COLORS.white }, sectionTitle: { fontFamily: FONTS.family.headingMedium, color: COLORS.textPrimary, fontSize: 19, marginTop: 23, marginBottom: 10 }, grid: { flexDirection: 'row', flexWrap: 'wrap', gap: 10 },
  insightRow: { flexDirection: 'row', gap: 8 }, insight: { flex: 1, minHeight: 89, backgroundColor: COLORS.card, borderRadius: RADIUS.md, padding: 11, borderWidth: 1, borderColor: COLORS.border }, insightValue: { fontFamily: FONTS.family.headingMedium, color: COLORS.primary, fontSize: 23 }, insightLabel: { fontFamily: FONTS.family.bodyMedium, color: COLORS.textSecondary, fontSize: 10, lineHeight: 14, marginTop: 5 },
  shelfBanner: { marginTop: 10, backgroundColor: COLORS.card, borderRadius: RADIUS.lg, borderWidth: 1, borderColor: COLORS.border, padding: 13, flexDirection: 'row', alignItems: 'center', gap: 10 }, shelfTitle: { fontFamily: FONTS.family.headingMedium, color: COLORS.textPrimary, fontSize: 15 }, shelfBody: { fontFamily: FONTS.family.body, color: COLORS.textSecondary, fontSize: 11, lineHeight: 16, marginTop: 2 },
  valueBanner: { marginTop: 10, backgroundColor: COLORS.primaryLight, borderRadius: RADIUS.lg, borderWidth: 1, borderColor: COLORS.primaryMuted, padding: 14, flexDirection: 'row', alignItems: 'center', gap: 8 }, valueLabel: { fontFamily: FONTS.family.bodySemibold, color: COLORS.primary, fontSize: 9, letterSpacing: .8 }, value: { fontFamily: FONTS.family.headingMedium, color: COLORS.textPrimary, fontSize: 22, marginTop: 2 }, estimate: { fontFamily: FONTS.family.bodyMedium, color: COLORS.textMuted, fontSize: 10 }, sectionHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }, clear: { fontFamily: FONTS.family.bodySemibold, color: COLORS.primary, fontSize: 13, marginTop: 12 },
  empty: { backgroundColor: COLORS.card, borderRadius: RADIUS.lg, padding: SPACING.lg, alignItems: 'center', borderWidth: 1, borderColor: COLORS.border }, emptyTitle: { fontFamily: FONTS.family.headingMedium, color: COLORS.textPrimary, fontSize: 18, marginTop: 8 }, muted: { fontFamily: FONTS.family.body, color: COLORS.textMuted, fontSize: 12, marginTop: 7, textAlign: 'center' },
});
