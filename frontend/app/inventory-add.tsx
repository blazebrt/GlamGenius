import React, { useMemo, useRef, useState } from 'react';
import { ActivityIndicator, ScrollView, StyleSheet, Text, TextInput, TouchableOpacity, View } from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import * as ImagePicker from 'expo-image-picker';

import { CATEGORY_META } from '../src/components/inventory/InventoryPieces';
import { INVENTORY_CATEGORIES, InventoryCategory, createInventoryItem, extractInventoryItem, uploadMedia } from '../src/services/apiV2';
import { errorMessage } from '../src/services/api';
import { categoriesForDomain } from '../src/navigation/finalIA';
import { COLORS, FONTS, RADIUS, SPACING } from '../src/theme/colors';

const EXTRA_FIELD: Record<InventoryCategory, { key: string; label: string; placeholder: string }> = {
  wardrobe: { key: 'colour', label: 'Colour', placeholder: 'Teal' }, shoes: { key: 'shoe_type', label: 'Shoe type', placeholder: 'Loafers' },
  accessories: { key: 'accessory_type', label: 'Accessory type', placeholder: 'Earrings' }, beauty: { key: 'product_type', label: 'Product type', placeholder: 'Serum' },
  hair: { key: 'product_type', label: 'Product type', placeholder: 'Shampoo' }, perfumes: { key: 'fragrance_family', label: 'Fragrance family', placeholder: 'Floral' },
  supplements: { key: 'user_entered_purpose', label: 'Your inventory note', placeholder: 'Why you keep this item' },
};

export default function InventoryAddScreen() {
  const router = useRouter(); const insets = useSafeAreaInsets(); const params = useLocalSearchParams<{ category?: string; domain?: string }>();
  const allowedCategories = categoriesForDomain(params.domain);
  const initial = allowedCategories
    ? allowedCategories.includes(params.category as InventoryCategory) ? params.category as InventoryCategory : allowedCategories[0]
    : INVENTORY_CATEGORIES.includes(params.category as InventoryCategory) ? params.category as InventoryCategory : 'wardrobe';
  const [category, setCategory] = useState<InventoryCategory>(initial); const [name, setName] = useState(''); const [brand, setBrand] = useState('');
  const [extra, setExtra] = useState(''); const [price, setPrice] = useState(''); const [expiry, setExpiry] = useState('');
  const [saving, setSaving] = useState(false); const [message, setMessage] = useState('');
  const mutationId = useRef(`inventory-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`).current;
  const field = useMemo(() => EXTRA_FIELD[category], [category]);
  const details = () => {
    const result: Record<string, any> = {}; if (extra.trim()) result[field.key] = extra.trim();
    if (expiry.trim() && ['beauty', 'hair', 'supplements'].includes(category)) result.expiry_date = expiry.trim();
    if (category === 'supplements') result.supplement_name = name.trim(); return result;
  };
  const saveManual = async () => {
    if (!name.trim()) { setMessage('Give the item a short name.'); return; }
    setSaving(true); setMessage('');
    try { const item = await createInventoryItem({ category, display_name: name.trim(), brand: brand.trim() || undefined, purchase_price: price ? Number(price) : undefined, details: details(), client_mutation_id: mutationId }); router.replace({ pathname: '/inventory-item', params: { id: item.id } }); }
    catch (err) { setMessage(errorMessage(err, 'Could not save yet. Your entries remain here so you can retry.')); }
    finally { setSaving(false); }
  };
  const capture = async (source: 'camera' | 'library') => {
    setSaving(true); setMessage('');
    try {
      if (source === 'camera') { const permission = await ImagePicker.requestCameraPermissionsAsync(); if (!permission.granted) { setMessage('Camera permission is needed to photograph an item.'); return; } }
      const result = source === 'camera' ? await ImagePicker.launchCameraAsync({ quality: .75, mediaTypes: ['images'] }) : await ImagePicker.launchImageLibraryAsync({ quality: .75, mediaTypes: ['images'] });
      if (result.canceled) return; const asset = result.assets[0];
      const uploaded = await uploadMedia({ uri: asset.uri, name: asset.fileName || `inventory-${Date.now()}.jpg`, type: asset.mimeType || 'image/jpeg' });
      const extracted = await extractInventoryItem(uploaded.id, category, source === 'camera' ? 'item_photo' : 'screenshot');
      router.replace({ pathname: '/inventory-item', params: { id: extracted.item.id } });
    } catch (err) { setMessage(errorMessage(err, 'We could not create a reliable draft from that image. Try another image or add the item manually.')); }
    finally { setSaving(false); }
  };
  return <View style={[styles.container, { paddingTop: insets.top }]}><View style={styles.top}><TouchableOpacity accessibilityRole="button" accessibilityLabel="Back to inventory" onPress={() => router.back()}><Ionicons name="arrow-back" size={24} color={COLORS.textPrimary} /></TouchableOpacity><Text style={styles.topTitle}>Add item</Text><View style={{ width: 24 }} /></View><ScrollView contentContainerStyle={{ padding: SPACING.lg, paddingBottom: insets.bottom + 40 }} keyboardShouldPersistTaps="handled">
    <Text style={styles.eyebrow}>ONE ITEM AT A TIME</Text><Text style={styles.title}>What are you adding?</Text><Text style={styles.body}>Start with the items you use most. You can fill in more detail later.</Text>
    <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.categories}>{(allowedCategories || INVENTORY_CATEGORIES).map((key) => <TouchableOpacity key={key} accessibilityRole="button" accessibilityLabel={CATEGORY_META[key].label} accessibilityState={{ selected: category === key }} onPress={() => { setCategory(key); setExtra(''); }} style={[styles.chip, category === key && styles.chipSelected]}><Text style={[styles.chipText, category === key && styles.chipTextSelected]}>{CATEGORY_META[key].label}</Text></TouchableOpacity>)}</ScrollView>
    <View style={styles.captureCard}><Text style={styles.captureTitle}>Create a draft from an image</Text><Text style={styles.body}>AI suggestions stay unverified until you review them.</Text><View style={styles.captureRow}><TouchableOpacity accessibilityRole="button" accessibilityLabel="Photograph one item" disabled={saving} onPress={() => void capture('camera')} style={styles.captureButton}><Ionicons name="camera-outline" size={20} color={COLORS.primary} /><Text style={styles.captureText}>Photo</Text></TouchableOpacity><TouchableOpacity accessibilityRole="button" accessibilityLabel="Import product screenshot" disabled={saving} onPress={() => void capture('library')} style={styles.captureButton}><Ionicons name="image-outline" size={20} color={COLORS.primary} /><Text style={styles.captureText}>Screenshot</Text></TouchableOpacity></View><Text style={styles.batchNote}>Shelf, wardrobe and video batch capture are being quality-tested and are not claimed as accurate yet.</Text></View>
    <Text style={styles.or}>OR ADD MANUALLY</Text>
    <Text style={styles.label}>Item name</Text><TextInput accessibilityLabel="Inventory item name" value={name} onChangeText={setName} placeholder="Cotton work kurta" placeholderTextColor={COLORS.textMuted} style={styles.input} />
    <Text style={styles.label}>Brand · optional</Text><TextInput accessibilityLabel="Inventory item brand" value={brand} onChangeText={setBrand} placeholder="Brand or local maker" placeholderTextColor={COLORS.textMuted} style={styles.input} />
    <Text style={styles.label}>{field.label} · optional</Text><TextInput accessibilityLabel={field.label} value={extra} onChangeText={setExtra} placeholder={field.placeholder} placeholderTextColor={COLORS.textMuted} style={styles.input} />
    {['beauty', 'hair', 'supplements'].includes(category) && <><Text style={styles.label}>Expiry date · optional</Text><TextInput accessibilityLabel="Expiry date" value={expiry} onChangeText={setExpiry} placeholder="YYYY-MM-DD" placeholderTextColor={COLORS.textMuted} style={styles.input} /></>}
    <Text style={styles.label}>Purchase price · optional</Text><TextInput accessibilityLabel="Purchase price" value={price} onChangeText={setPrice} keyboardType="decimal-pad" placeholder="₹" placeholderTextColor={COLORS.textMuted} style={styles.input} />
    {category === 'supplements' && <Text style={styles.safety}>Inventory tracking only. GlamGenius does not provide supplement dosage or medical advice. Follow the label and guidance from a qualified professional.</Text>}
    {!!message && <Text accessibilityRole="alert" style={styles.error}>{message}</Text>}
    <TouchableOpacity accessibilityRole="button" accessibilityLabel="Save inventory item" disabled={saving} onPress={() => void saveManual()} style={styles.primary}>{saving ? <ActivityIndicator color={COLORS.white} /> : <Text style={styles.primaryText}>Save item</Text>}</TouchableOpacity>
  </ScrollView></View>;
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.backgroundSecondary }, top: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: SPACING.lg, paddingVertical: 12 }, topTitle: { fontFamily: FONTS.family.bodySemibold, color: COLORS.textPrimary }, eyebrow: { fontFamily: FONTS.family.bodySemibold, color: COLORS.primary, fontSize: 10, letterSpacing: 1.3 }, title: { fontFamily: FONTS.family.heading, color: COLORS.textPrimary, fontSize: 29, marginTop: 5 }, body: { fontFamily: FONTS.family.body, color: COLORS.textSecondary, fontSize: 13, lineHeight: 19, marginTop: 5 }, categories: { gap: 8, paddingVertical: 16 }, chip: { borderWidth: 1, borderColor: COLORS.border, backgroundColor: COLORS.card, borderRadius: RADIUS.full, paddingHorizontal: 13, paddingVertical: 8 }, chipSelected: { backgroundColor: COLORS.primary, borderColor: COLORS.primary }, chipText: { fontFamily: FONTS.family.bodyMedium, color: COLORS.textSecondary, fontSize: 12 }, chipTextSelected: { color: COLORS.white },
  captureCard: { backgroundColor: COLORS.card, borderRadius: RADIUS.lg, padding: 15, borderWidth: 1, borderColor: COLORS.border }, captureTitle: { fontFamily: FONTS.family.headingMedium, color: COLORS.textPrimary, fontSize: 18 }, captureRow: { flexDirection: 'row', gap: 10, marginTop: 13 }, captureButton: { flex: 1, borderWidth: 1, borderColor: COLORS.primary, borderRadius: RADIUS.md, padding: 11, alignItems: 'center', justifyContent: 'center', flexDirection: 'row', gap: 7 }, captureText: { fontFamily: FONTS.family.bodySemibold, color: COLORS.primary }, batchNote: { fontFamily: FONTS.family.body, color: COLORS.textMuted, fontSize: 10, lineHeight: 15, marginTop: 11 }, or: { fontFamily: FONTS.family.bodySemibold, color: COLORS.textMuted, fontSize: 10, letterSpacing: 1.1, marginVertical: 18, textAlign: 'center' },
  label: { fontFamily: FONTS.family.bodySemibold, color: COLORS.textPrimary, fontSize: 12, marginTop: 13, marginBottom: 6 }, input: { backgroundColor: COLORS.card, borderRadius: RADIUS.md, borderWidth: 1, borderColor: COLORS.border, paddingHorizontal: 13, paddingVertical: 11, fontFamily: FONTS.family.body, color: COLORS.textPrimary }, safety: { backgroundColor: COLORS.infoLight, borderRadius: RADIUS.md, padding: 12, fontFamily: FONTS.family.body, color: COLORS.textSecondary, fontSize: 11, lineHeight: 17, marginTop: 15 }, error: { fontFamily: FONTS.family.bodyMedium, color: COLORS.error, fontSize: 12, lineHeight: 18, marginTop: 12 }, primary: { marginTop: 20, backgroundColor: COLORS.primary, borderRadius: RADIUS.full, minHeight: 48, alignItems: 'center', justifyContent: 'center' }, primaryText: { fontFamily: FONTS.family.bodySemibold, color: COLORS.white },
});
