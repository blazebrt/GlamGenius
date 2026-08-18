/**
 * Should I buy this?
 *
 * Upload a product screenshot, or type the details. Either way the answer is
 * Buy, Wait or Skip with the whole ROI calculation shown underneath it.
 */
import React, { useState } from 'react';
import { ScrollView, StyleSheet, Text, TextInput, TouchableOpacity, View } from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import * as ImagePicker from 'expo-image-picker';

import {
  DecisionActions, ExtractedItemReview, NewCombinations, OwnedComparisons,
  ROIBreakdown, RiskNotes, ShoppingUpload, VerdictCard,
} from '../src/components/shopping/ShoppingPieces';
import { AnalysisFailedState } from '../src/components/TrustStates';
import { CATEGORY_META } from '../src/components/inventory/InventoryPieces';
import {
  InventoryCategory, PurchaseEvaluation,
  allowanceWasPreserved, evaluateItemDetails, evaluateScreenshot, failureGuidance,
  recordPurchaseDecision, structuredError, uploadMedia,
} from '../src/services/apiV2';
import { COLORS, FONTS, RADIUS, SPACING } from '../src/theme/colors';

/** Categories for the active Style purchase strategy, not the Inventory domain. */
export const MANUAL_PURCHASE_CATEGORIES: InventoryCategory[] = [
  'wardrobe', 'shoes', 'accessories',
];

export default function ShoppingCheckScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [busy, setBusy] = useState(false);
  const [manual, setManual] = useState(false);
  const [category, setCategory] = useState<InventoryCategory>('wardrobe');
  const [name, setName] = useState('');
  const [colour, setColour] = useState('');
  const [price, setPrice] = useState('');
  const [evaluation, setEvaluation] = useState<PurchaseEvaluation | null>(null);
  const [error, setError] = useState<any>(null);

  const fromScreenshot = async () => {
    setBusy(true);
    setError(null);
    try {
      const picked = await ImagePicker.launchImageLibraryAsync({ quality: 0.75, mediaTypes: ['images'] });
      if (picked.canceled) return;
      const asset = picked.assets[0];
      const uploaded = await uploadMedia({
        uri: asset.uri,
        name: asset.fileName || `product-${Date.now()}.jpg`,
        type: asset.mimeType || 'image/jpeg',
      });
      setEvaluation(await evaluateScreenshot(uploaded.id, undefined, price ? Number(price) : undefined));
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  const fromDetails = async () => {
    if (!name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      setEvaluation(await evaluateItemDetails(
        { category, display_name: name.trim(), colour: colour.trim() || undefined },
        undefined,
        price ? Number(price) : undefined,
      ));
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  const decide = async (decision: 'bought' | 'waiting' | 'skipped') => {
    if (!evaluation) return;
    try {
      setEvaluation(await recordPurchaseDecision(evaluation.id, decision));
    } catch (err) {
      setError(err);
    }
  };

  const reset = () => { setEvaluation(null); setName(''); setColour(''); setPrice(''); setError(null); };

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <View style={styles.top}>
        <TouchableOpacity accessibilityRole="button" accessibilityLabel="Back" onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={24} color={COLORS.textPrimary} />
        </TouchableOpacity>
        <Text style={styles.topTitle}>Should I buy this?</Text>
        <View style={{ width: 24 }} />
      </View>

      <ScrollView contentContainerStyle={{ padding: SPACING.lg, paddingBottom: insets.bottom + 48 }} keyboardShouldPersistTaps="handled">
        {!!error && (
          <AnalysisFailedState
            message={structuredError(error)?.message || 'We could not check that just now.'}
            guidance={failureGuidance(error)}
            allowancePreserved={allowanceWasPreserved(error)}
            retryable={structuredError(error)?.retryable !== false}
            onRetry={() => (manual ? void fromDetails() : void fromScreenshot())}
            onDismiss={() => setError(null)}
          />
        )}

        {!evaluation && (
          <>
            <ShoppingUpload
              onPickScreenshot={() => void fromScreenshot()}
              onEnterDetails={() => setManual((value) => !value)}
              busy={busy}
            />

            {manual && (
              <View style={styles.card}>
                <Text style={styles.cardTitle}>Tell us what it is</Text>
                <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chipRow}>
                  {MANUAL_PURCHASE_CATEGORIES.map((key) => (
                    <TouchableOpacity
                      key={key}
                      accessibilityRole="button"
                      accessibilityLabel={CATEGORY_META[key].label}
                      accessibilityState={{ selected: category === key }}
                      onPress={() => setCategory(key)}
                      style={[styles.chip, category === key && styles.chipActive]}
                    >
                      <Text style={[styles.chipText, category === key && styles.chipTextActive]}>{CATEGORY_META[key].label}</Text>
                    </TouchableOpacity>
                  ))}
                </ScrollView>

                <Text style={styles.label}>What is it?</Text>
                <TextInput accessibilityLabel="Item name" value={name} onChangeText={setName} placeholder="Olive linen shirt" placeholderTextColor={COLORS.textMuted} style={styles.input} />
                <Text style={styles.label}>Colour · optional</Text>
                <TextInput accessibilityLabel="Item colour" value={colour} onChangeText={setColour} placeholder="Olive green" placeholderTextColor={COLORS.textMuted} style={styles.input} />
                <Text style={styles.label}>Price · optional</Text>
                <TextInput accessibilityLabel="Item price" value={price} onChangeText={setPrice} keyboardType="numeric" placeholder="2199" placeholderTextColor={COLORS.textMuted} style={styles.input} />
                <Text style={styles.hint}>Without a price we skip cost per wear and say so, rather than guessing it.</Text>

                <TouchableOpacity
                  accessibilityRole="button"
                  accessibilityLabel="Check this item"
                  disabled={busy || !name.trim()}
                  onPress={() => void fromDetails()}
                  style={[styles.primary, (busy || !name.trim()) && styles.disabled]}
                >
                  <Text style={styles.primaryText}>Check it</Text>
                </TouchableOpacity>
              </View>
            )}
          </>
        )}

        {evaluation && (
          <>
            <VerdictCard evaluation={evaluation} />
            {evaluation.candidate && <ExtractedItemReview candidate={evaluation.candidate} />}
            <NewCombinations count={evaluation.new_combinations} />
            <ROIBreakdown roi={evaluation.appearance_roi} />
            <OwnedComparisons similar={evaluation.similar_owned_products} alternatives={evaluation.existing_alternatives} />
            <RiskNotes evaluation={evaluation} />
            <DecisionActions current={evaluation.decision?.decision} onDecide={(value) => void decide(value)} />
            {!!evaluation.disclaimer && <Text style={styles.hint}>{evaluation.disclaimer}</Text>}
            <TouchableOpacity accessibilityRole="button" accessibilityLabel="Check something else" onPress={reset}>
              <Text style={styles.link}>Check something else</Text>
            </TouchableOpacity>
          </>
        )}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.backgroundSecondary },
  top: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingHorizontal: SPACING.lg, paddingVertical: 12 },
  topTitle: { fontFamily: FONTS.family.bodySemibold, color: COLORS.textPrimary },
  card: { backgroundColor: COLORS.card, borderRadius: RADIUS.xl, padding: SPACING.lg, borderWidth: 1, borderColor: COLORS.border, marginBottom: SPACING.md },
  cardTitle: { fontFamily: FONTS.family.headingMedium, fontSize: 18, color: COLORS.textPrimary, marginBottom: 8 },
  chipRow: { gap: 8, paddingRight: SPACING.md, paddingBottom: 4 },
  chip: { paddingHorizontal: 14, paddingVertical: 8, borderRadius: RADIUS.full, backgroundColor: COLORS.backgroundSecondary, borderWidth: 1, borderColor: COLORS.border },
  chipActive: { backgroundColor: COLORS.primary, borderColor: COLORS.primary },
  chipText: { fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.textSecondary },
  chipTextActive: { color: COLORS.white },
  label: { fontFamily: FONTS.family.bodySemibold, fontSize: 12, color: COLORS.textPrimary, marginTop: SPACING.md, marginBottom: 5 },
  input: { backgroundColor: COLORS.background, borderRadius: RADIUS.md, borderWidth: 1, borderColor: COLORS.border, paddingHorizontal: 14, paddingVertical: 11, fontFamily: FONTS.family.body, fontSize: 14, color: COLORS.textPrimary },
  hint: { fontFamily: FONTS.family.body, fontSize: 11, lineHeight: 16, color: COLORS.textMuted, marginTop: 8, textAlign: 'center' },
  primary: { alignItems: 'center', justifyContent: 'center', backgroundColor: COLORS.primary, borderRadius: RADIUS.full, paddingVertical: 14, marginTop: SPACING.lg },
  primaryText: { fontFamily: FONTS.family.bodySemibold, fontSize: 14, color: COLORS.white },
  disabled: { opacity: 0.5 },
  link: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.primary, textAlign: 'center', marginTop: SPACING.md },
});
