import React from 'react';
import { StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';

import { InventoryCategory, InventoryItem, InventorySummary } from '../../services/apiV2';
import { COLORS, FONTS, RADIUS, SPACING } from '../../theme/colors';

export const CATEGORY_META: Record<InventoryCategory, { label: string; icon: string }> = {
  wardrobe: { label: 'Wardrobe', icon: 'shirt-outline' },
  shoes: { label: 'Shoes', icon: 'footsteps-outline' },
  accessories: { label: 'Accessories', icon: 'diamond-outline' },
  beauty: { label: 'Beauty', icon: 'sparkles-outline' },
  hair: { label: 'Hair', icon: 'cut-outline' },
  perfumes: { label: 'Perfumes', icon: 'flower-outline' },
  supplements: { label: 'Supplements', icon: 'leaf-outline' },
};

export const GUIDED_TASKS = [
  { label: 'Add five everyday clothes', category: 'wardrobe' as const, target: 5 },
  { label: 'Add two occasion pieces', category: 'wardrobe' as const, target: 7 },
  { label: 'Add two pairs of shoes', category: 'shoes' as const, target: 2 },
  { label: 'Add current skincare products', category: 'beauty' as const, target: 1 },
  { label: 'Add current hair products', category: 'hair' as const, target: 1 },
];

export const completedGuidedTasks = (counts: Partial<Record<InventoryCategory, number>>) =>
  GUIDED_TASKS.filter((task) => (counts[task.category] || 0) >= task.target).length;

export function GuidedSprint({ summary, onAdd }: { summary: InventorySummary; onAdd: (category: InventoryCategory) => void }) {
  const completed = completedGuidedTasks(summary.categories);
  const next = GUIDED_TASKS.find((task) => (summary.categories[task.category] || 0) < task.target);
  return <View style={styles.sprint} accessibilityLabel="Inventory guided sprint">
    <Text style={styles.eyebrow}>QUICK START</Text>
    <Text style={styles.sprintTitleInverse}>{completed ? `${completed} starter tasks complete` : 'Build a useful inventory first'}</Text>
    <Text style={styles.bodyInverse}>You can receive value now. Catalogue the rest whenever it suits you.</Text>
    {next && <TouchableOpacity accessibilityRole="button" accessibilityLabel={next.label} style={styles.primary} onPress={() => onAdd(next.category)}>
      <Text style={styles.primaryText}>{next.label}</Text><Ionicons name="arrow-forward" size={18} color={COLORS.white} />
    </TouchableOpacity>}
  </View>;
}

export function CategoryTile({ category, count, onPress }: { category: InventoryCategory; count: number; onPress: () => void }) {
  const meta = CATEGORY_META[category];
  return <TouchableOpacity accessibilityRole="button" accessibilityLabel={`${meta.label}, ${count} items`} onPress={onPress} style={styles.category}>
    <View style={styles.categoryIcon}><Ionicons name={meta.icon as any} size={21} color={COLORS.primary} /></View>
    <Text style={styles.categoryName}>{meta.label}</Text><Text style={styles.count}>{count}</Text>
  </TouchableOpacity>;
}

export function InventoryItemCard({ item, onPress }: { item: InventoryItem; onPress: () => void }) {
  return <TouchableOpacity accessibilityRole="button" accessibilityLabel={`Open ${item.display_name}`} onPress={onPress} style={styles.itemCard}>
    <View style={styles.itemIcon}><Ionicons name={CATEGORY_META[item.category].icon as any} size={20} color={COLORS.primary} /></View>
    <View style={{ flex: 1 }}><Text style={styles.itemName}>{item.display_name}</Text><Text style={styles.itemMeta}>{item.brand || CATEGORY_META[item.category].label} · Used {item.usage_count} times</Text></View>
    {item.verification_state === 'draft' && <View style={styles.draft}><Text style={styles.draftText}>Review</Text></View>}
    <Ionicons name="chevron-forward" size={17} color={COLORS.textMuted} />
  </TouchableOpacity>;
}

export function DraftReviewActions({ item, onConfirm, onEdit }: { item: InventoryItem; onConfirm: () => void; onEdit: () => void }) {
  return <View style={styles.review} accessibilityLabel="Extraction review">
    <Text style={styles.eyebrow}>DRAFT · {Math.round(item.confidence * 100)}% CONFIDENCE</Text>
    <Text style={styles.sprintTitle}>Check what the photo suggested</Text>
    <Text style={styles.body}>Nothing becomes verified until you confirm it. Correct anything uncertain first.</Text>
    <View style={styles.actionRow}>
      <TouchableOpacity accessibilityRole="button" accessibilityLabel={`Correct ${item.display_name}`} onPress={onEdit} style={styles.outline}><Text style={styles.outlineText}>Correct</Text></TouchableOpacity>
      <TouchableOpacity accessibilityRole="button" accessibilityLabel={`Confirm ${item.display_name}`} onPress={onConfirm} style={[styles.primary, { flex: 1, marginTop: 0 }]}><Text style={styles.primaryText}>Confirm item</Text></TouchableOpacity>
    </View>
  </View>;
}

export function InventoryRecovery({ onRetry, onAdd }: { onRetry: () => void; onAdd: () => void }) {
  return <View style={styles.recovery}><Text style={styles.sprintTitle}>Your inventory is still here</Text><Text style={styles.body}>We could not load it just now.</Text><TouchableOpacity accessibilityRole="button" accessibilityLabel="Retry inventory" onPress={onRetry} style={styles.primary}><Text style={styles.primaryText}>Try again</Text></TouchableOpacity><TouchableOpacity accessibilityRole="button" accessibilityLabel="Add an item instead" onPress={onAdd}><Text style={styles.link}>Add an item instead</Text></TouchableOpacity></View>;
}

export function EstimateNotice({ explanation }: { explanation: string }) {
  return <View accessibilityLabel="Value to Recover estimate explanation" style={styles.notice}><Ionicons name="information-circle-outline" size={20} color={COLORS.info} /><Text style={styles.noticeText}>{explanation}</Text></View>;
}

const styles = StyleSheet.create({
  sprint: { backgroundColor: COLORS.primary, borderRadius: RADIUS.xl, padding: SPACING.lg }, eyebrow: { fontFamily: FONTS.family.bodySemibold, color: COLORS.accent, fontSize: 10, letterSpacing: 1.3, textTransform: 'uppercase' }, sprintTitle: { fontFamily: FONTS.family.headingMedium, color: COLORS.textPrimary, fontSize: 20, marginTop: 5 }, sprintTitleInverse: { fontFamily: FONTS.family.headingMedium, color: COLORS.white, fontSize: 20, marginTop: 5 }, body: { fontFamily: FONTS.family.body, color: COLORS.textSecondary, fontSize: 13, lineHeight: 19, marginTop: 5 }, bodyInverse: { fontFamily: FONTS.family.body, color: COLORS.primaryMuted, fontSize: 13, lineHeight: 19, marginTop: 5 },
  primary: { marginTop: 16, borderRadius: RADIUS.full, backgroundColor: COLORS.accent, paddingHorizontal: 15, paddingVertical: 11, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8 }, primaryText: { fontFamily: FONTS.family.bodySemibold, color: COLORS.white, fontSize: 13 },
  category: { width: '48%', backgroundColor: COLORS.card, borderRadius: RADIUS.lg, borderWidth: 1, borderColor: COLORS.border, padding: 13, flexDirection: 'row', alignItems: 'center', gap: 9 }, categoryIcon: { width: 36, height: 36, borderRadius: 11, backgroundColor: COLORS.primaryLight, alignItems: 'center', justifyContent: 'center' }, categoryName: { flex: 1, fontFamily: FONTS.family.bodySemibold, color: COLORS.textPrimary, fontSize: 13 }, count: { fontFamily: FONTS.family.headingMedium, color: COLORS.primary, fontSize: 17 },
  itemCard: { backgroundColor: COLORS.card, borderRadius: RADIUS.md, borderWidth: 1, borderColor: COLORS.border, padding: 12, flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 8 }, itemIcon: { width: 38, height: 38, borderRadius: 12, backgroundColor: COLORS.primaryLight, alignItems: 'center', justifyContent: 'center' }, itemName: { fontFamily: FONTS.family.bodySemibold, color: COLORS.textPrimary, fontSize: 14 }, itemMeta: { fontFamily: FONTS.family.body, color: COLORS.textMuted, fontSize: 11, marginTop: 3 }, draft: { backgroundColor: COLORS.warningLight, paddingHorizontal: 8, paddingVertical: 4, borderRadius: RADIUS.full }, draftText: { fontFamily: FONTS.family.bodySemibold, color: COLORS.warning, fontSize: 10 },
  review: { backgroundColor: COLORS.card, borderRadius: RADIUS.lg, borderWidth: 1, borderColor: COLORS.warning, padding: SPACING.lg }, actionRow: { flexDirection: 'row', gap: 10, marginTop: 16 }, outline: { flex: 1, borderWidth: 1, borderColor: COLORS.primary, borderRadius: RADIUS.full, alignItems: 'center', paddingVertical: 11 }, outlineText: { fontFamily: FONTS.family.bodySemibold, color: COLORS.primary }, recovery: { alignItems: 'center', padding: SPACING.xl }, link: { marginTop: 14, color: COLORS.primary, fontFamily: FONTS.family.bodySemibold },
  notice: { flexDirection: 'row', gap: 9, borderRadius: RADIUS.md, backgroundColor: COLORS.infoLight, padding: 12 }, noticeText: { flex: 1, fontFamily: FONTS.family.body, color: COLORS.textSecondary, fontSize: 12, lineHeight: 18 },
});
