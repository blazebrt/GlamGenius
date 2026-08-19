/** The single customer destination for Style and Care purchase checks. */
import React, { useEffect, useMemo, useState } from 'react';
import { ScrollView, StyleSheet, Text, TextInput, TouchableOpacity, View } from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import * as ImagePicker from 'expo-image-picker';

import {
  DecisionActions, ExtractedItemReview, NewCombinations, OwnedComparisons,
  ROIBreakdown, RiskNotes, ShoppingUpload, VerdictCard,
} from '../src/components/shopping/ShoppingPieces';
import { CareCandidateReview, CarePurchaseResult } from '../src/components/shopping/CareShoppingPieces';
import { AnalysisFailedState } from '../src/components/TrustStates';
import {
  CareCandidateInspection, CarePurchaseCheck, CarePurchaseItemInput, InventoryCategory,
  PurchaseEvaluation, PurchaseStrategy, confirmPurchaseCandidate, evaluateItemDetails,
  evaluateScreenshot, failureGuidance, getCarePurchaseCheck, getPurchaseStrategies,
  inspectPurchaseCandidate, recordPurchaseDecision, structuredError, uploadMedia,
} from '../src/services/apiV2';
import { COLORS, FONTS, RADIUS, SPACING } from '../src/theme/colors';

export default function ShoppingCheckScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [strategies, setStrategies] = useState<PurchaseStrategy[]>([]);
  const [busy, setBusy] = useState(false);
  const [manual, setManual] = useState(false);
  const [category, setCategory] = useState<InventoryCategory | null>(null);
  const [name, setName] = useState('');
  const [brand, setBrand] = useState('');
  const [productType, setProductType] = useState('');
  const [ingredients, setIngredients] = useState('');
  const [size, setSize] = useState('');
  const [colour, setColour] = useState('');
  const [price, setPrice] = useState('');
  const [evaluation, setEvaluation] = useState<PurchaseEvaluation | null>(null);
  const [careCandidate, setCareCandidate] = useState<CareCandidateInspection | null>(null);
  const [careCheck, setCareCheck] = useState<CarePurchaseCheck | null>(null);
  const [editingCare, setEditingCare] = useState(false);
  const [error, setError] = useState<any>(null);

  useEffect(() => {
    let mounted = true;
    void getPurchaseStrategies().then((response) => {
      if (!mounted) return;
      const active = response.strategies.flatMap((strategy) => strategy.state === 'active' ? strategy.categories : []);
      setStrategies(response.strategies);
      setCategory(active[0]?.key || null);
    }).catch((err) => { if (mounted) setError(err); });
    return () => { mounted = false; };
  }, []);

  const activeCategories = useMemo(
    () => strategies.flatMap((strategy) => strategy.state === 'active' ? strategy.categories : []),
    [strategies],
  );
  const selectedStrategy = strategies.find((strategy) => strategy.categories.some((row) => row.key === category));
  const isCare = selectedStrategy?.key === 'care_purchase' && (category === 'beauty' || category === 'hair');

  const clearError = () => setError(null);
  const reset = () => {
    setEvaluation(null); setCareCandidate(null); setCareCheck(null); setEditingCare(false); setName(''); setBrand('');
    setProductType(''); setIngredients(''); setSize(''); setColour(''); setPrice(''); setError(null);
  };

  const inspectCare = async (body: Parameters<typeof inspectPurchaseCandidate>[0]) => {
    setBusy(true); clearError();
    try {
      const result = await inspectPurchaseCandidate(body);
      setCareCandidate(result);
      setName(result.candidate.display_name);
      setBrand(result.candidate.brand || '');
      setProductType(result.candidate.details?.product_type || '');
      setIngredients(result.candidate.details?.ingredients_text || '');
      setSize(result.candidate.details?.size || '');
      setPrice(result.candidate.price == null ? '' : String(result.candidate.price));
      if (result.facts_trusted) setCareCheck(await getCarePurchaseCheck(result.candidate.id));
    } catch (err) { setError(err); } finally { setBusy(false); }
  };

  const fromScreenshot = async () => {
    if (!category) return;
    setBusy(true); clearError();
    try {
      const picked = await ImagePicker.launchImageLibraryAsync({ quality: 0.75, mediaTypes: ['images'] });
      if (picked.canceled) return;
      const asset = picked.assets[0];
      const uploaded = await uploadMedia({ uri: asset.uri, name: asset.fileName || `product-${Date.now()}.jpg`, type: asset.mimeType || 'image/jpeg' });
      if (isCare) await inspectCare({ source: 'screenshot', media_asset_id: uploaded.id });
      else setEvaluation(await evaluateScreenshot(uploaded.id, undefined, price ? Number(price) : undefined));
    } catch (err) { setError(err); } finally { setBusy(false); }
  };

  const fromDetails = async () => {
    if (!category || !name.trim()) return;
    if (isCare) {
      const item: CarePurchaseItemInput = {
        category: category as 'beauty' | 'hair', display_name: name.trim(), brand: brand.trim() || undefined,
        details: { product_type: productType.trim() || undefined, ingredients_text: ingredients.trim() || undefined, size: size.trim() || undefined },
        price: price ? Number(price) : undefined,
      };
      await inspectCare({ source: 'manual', item });
      return;
    }
    setBusy(true); clearError();
    try { setEvaluation(await evaluateItemDetails({ category, display_name: name.trim(), colour: colour.trim() || undefined }, undefined, price ? Number(price) : undefined)); }
    catch (err) { setError(err); } finally { setBusy(false); }
  };

  const confirmCare = async () => {
    if (!careCandidate) return;
    if (careCandidate.facts_trusted) { setCareCheck(await getCarePurchaseCheck(careCandidate.candidate.id)); return; }
    setBusy(true); clearError();
    try {
      const confirmed = await confirmPurchaseCandidate(careCandidate.candidate.id, {
        display_name: name.trim() || careCandidate.candidate.display_name,
        brand: brand.trim() || undefined,
        details: {
          product_type: productType.trim() || undefined,
          size: size.trim() || undefined,
          ingredients_text: ingredients.trim() || undefined,
        },
        price: price ? Number(price) : undefined,
        currency: careCandidate.candidate.currency,
      });
      setCareCandidate(confirmed); setEditingCare(false); setCareCheck(await getCarePurchaseCheck(confirmed.candidate.id));
    } catch (err) { setError(err); } finally { setBusy(false); }
  };

  const decide = async (decision: 'bought' | 'waiting' | 'skipped') => {
    if (!evaluation) return;
    try { setEvaluation(await recordPurchaseDecision(evaluation.id, decision)); } catch (err) { setError(err); }
  };

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <View style={styles.top}>
        <TouchableOpacity accessibilityRole="button" accessibilityLabel="Back" onPress={() => router.back()}><Ionicons name="arrow-back" size={24} color={COLORS.textPrimary} /></TouchableOpacity>
        <Text style={styles.topTitle}>Should I buy this?</Text><View style={{ width: 24 }} />
      </View>
      <ScrollView contentContainerStyle={{ padding: SPACING.lg, paddingBottom: insets.bottom + 48 }} keyboardShouldPersistTaps="handled">
        {!!error && <AnalysisFailedState message={structuredError(error)?.message || 'We could not check that just now.'} guidance={failureGuidance(error)} allowancePreserved={false} retryable={structuredError(error)?.retryable !== false} onRetry={() => void (isCare ? (careCandidate ? confirmCare() : fromDetails()) : (manual ? fromDetails() : fromScreenshot()))} onDismiss={clearError} />}

        {!evaluation && !careCheck && !careCandidate && (
          <>
            <View style={styles.card} accessibilityLabel="Choose what you are checking">
              <Text style={styles.eyebrow}>CHOOSE A CATEGORY</Text><Text style={styles.title}>What are you considering?</Text>
              {!strategies.length && !error && <Text style={styles.body}>Loading the active purchase categories…</Text>}
              <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chipRow}>
                {activeCategories.map((row) => <TouchableOpacity key={row.key} accessibilityRole="button" accessibilityLabel={row.label} accessibilityState={{ selected: category === row.key }} onPress={() => setCategory(row.key)} style={[styles.chip, category === row.key && styles.chipActive]}><Text style={[styles.chipText, category === row.key && styles.chipTextActive]}>{row.label}</Text></TouchableOpacity>)}
              </ScrollView>
              {!strategies.length && error && <Text style={styles.warn}>We could not load purchase categories. Please retry.</Text>}
            </View>
            {!!category && <ShoppingUpload onPickScreenshot={() => void fromScreenshot()} onEnterDetails={() => setManual((value) => !value)} busy={busy} />}
            {manual && !!category && (
              <View style={styles.card}>
                <Text style={styles.cardTitle}>{isCare ? 'Tell us about the product' : 'Tell us what it is'}</Text>
                <Text style={styles.label}>Product name</Text><TextInput accessibilityLabel="Product name" value={name} onChangeText={setName} placeholder="Daily cleanser" placeholderTextColor={COLORS.textMuted} style={styles.input} />
                {isCare ? <>
                  <Text style={styles.label}>Brand · optional</Text><TextInput accessibilityLabel="Product brand" value={brand} onChangeText={setBrand} placeholder="Brand" placeholderTextColor={COLORS.textMuted} style={styles.input} />
                  <Text style={styles.label}>Product type · optional</Text><TextInput accessibilityLabel="Product type" value={productType} onChangeText={setProductType} placeholder="Cleanser, serum, shampoo" placeholderTextColor={COLORS.textMuted} style={styles.input} />
                  <Text style={styles.label}>Ingredients from label · optional</Text><TextInput accessibilityLabel="Ingredients from label" value={ingredients} onChangeText={setIngredients} placeholder="Paste the label text" placeholderTextColor={COLORS.textMuted} style={[styles.input, styles.multiline]} multiline />
                  <Text style={styles.label}>Size · optional</Text><TextInput accessibilityLabel="Product size" value={size} onChangeText={setSize} placeholder="100 ml" placeholderTextColor={COLORS.textMuted} style={styles.input} />
                </> : <>
                  <Text style={styles.label}>Colour · optional</Text><TextInput accessibilityLabel="Item colour" value={colour} onChangeText={setColour} placeholder="Olive green" placeholderTextColor={COLORS.textMuted} style={styles.input} />
                </>}
                <Text style={styles.label}>Price · optional</Text><TextInput accessibilityLabel="Product price" value={price} onChangeText={setPrice} keyboardType="numeric" placeholder="2199" placeholderTextColor={COLORS.textMuted} style={styles.input} />
                <Text style={styles.hint}>{isCare ? 'We use only the facts you provide. Missing information may lead to Wait.' : 'Without a price we say so, rather than guessing.'}</Text>
                <TouchableOpacity accessibilityRole="button" accessibilityLabel="Check this item" disabled={busy || !name.trim()} onPress={() => void fromDetails()} style={[styles.primary, (busy || !name.trim()) && styles.disabled]}><Text style={styles.primaryText}>Check it</Text></TouchableOpacity>
              </View>
            )}
          </>
        )}

        {careCandidate && !careCheck && !editingCare && <CareCandidateReview inspection={careCandidate} onConfirm={() => void confirmCare()} onCorrect={() => setEditingCare(true)} />}
        {careCandidate && !careCheck && editingCare && (
          <View style={styles.card} accessibilityLabel="Correct Care product facts">
            <Text style={styles.cardTitle}>Correct what we read</Text>
            <Text style={styles.label}>Product name</Text><TextInput accessibilityLabel="Corrected product name" value={name} onChangeText={setName} style={styles.input} />
            <Text style={styles.label}>Brand</Text><TextInput accessibilityLabel="Corrected product brand" value={brand} onChangeText={setBrand} style={styles.input} />
            <Text style={styles.label}>Product type</Text><TextInput accessibilityLabel="Corrected product type" value={productType} onChangeText={setProductType} style={styles.input} />
            <Text style={styles.label}>Ingredients from label</Text><TextInput accessibilityLabel="Corrected ingredients from label" value={ingredients} onChangeText={setIngredients} style={[styles.input, styles.multiline]} multiline />
            <Text style={styles.label}>Size</Text><TextInput accessibilityLabel="Corrected product size" value={size} onChangeText={setSize} style={styles.input} />
            <Text style={styles.label}>Price</Text><TextInput accessibilityLabel="Corrected product price" value={price} onChangeText={setPrice} keyboardType="numeric" style={styles.input} />
            <TouchableOpacity accessibilityRole="button" accessibilityLabel="Save corrected product facts" onPress={() => void confirmCare()} style={styles.primary}><Text style={styles.primaryText}>Confirm corrections</Text></TouchableOpacity>
          </View>
        )}
        {careCheck && <CarePurchaseResult check={careCheck} onReset={reset} />}
        {evaluation && <>
          <VerdictCard evaluation={evaluation} />{evaluation.candidate && <ExtractedItemReview candidate={evaluation.candidate} />}
          <NewCombinations count={evaluation.new_combinations} /><ROIBreakdown roi={evaluation.appearance_roi} /><OwnedComparisons similar={evaluation.similar_owned_products} alternatives={evaluation.existing_alternatives} /><RiskNotes evaluation={evaluation} />
          <DecisionActions current={evaluation.decision?.decision} onDecide={(value) => void decide(value)} />{!!evaluation.disclaimer && <Text style={styles.hint}>{evaluation.disclaimer}</Text>}
          <TouchableOpacity accessibilityRole="button" accessibilityLabel="Check something else" onPress={reset}><Text style={styles.link}>Check something else</Text></TouchableOpacity>
        </>}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.backgroundSecondary }, top: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingHorizontal: SPACING.lg, paddingVertical: 12 }, topTitle: { fontFamily: FONTS.family.bodySemibold, color: COLORS.textPrimary },
  card: { backgroundColor: COLORS.card, borderRadius: RADIUS.xl, padding: SPACING.lg, borderWidth: 1, borderColor: COLORS.border, marginBottom: SPACING.md }, cardTitle: { fontFamily: FONTS.family.headingMedium, fontSize: 18, color: COLORS.textPrimary, marginBottom: 8 }, eyebrow: { fontFamily: FONTS.family.bodySemibold, color: COLORS.accent, fontSize: 10, letterSpacing: 1.2, textTransform: 'uppercase' }, title: { fontFamily: FONTS.family.headingMedium, fontSize: 18, color: COLORS.textPrimary, marginTop: 4 }, body: { fontFamily: FONTS.family.body, fontSize: 13, lineHeight: 19, color: COLORS.textSecondary, marginTop: 6 }, chipRow: { gap: 8, paddingRight: SPACING.md, paddingVertical: 10 }, chip: { paddingHorizontal: 14, paddingVertical: 8, borderRadius: RADIUS.full, backgroundColor: COLORS.backgroundSecondary, borderWidth: 1, borderColor: COLORS.border }, chipActive: { backgroundColor: COLORS.primary, borderColor: COLORS.primary }, chipText: { fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.textSecondary }, chipTextActive: { color: COLORS.white }, label: { fontFamily: FONTS.family.bodySemibold, fontSize: 12, color: COLORS.textPrimary, marginTop: SPACING.md, marginBottom: 5 }, input: { backgroundColor: COLORS.background, borderRadius: RADIUS.md, borderWidth: 1, borderColor: COLORS.border, paddingHorizontal: 14, paddingVertical: 11, fontFamily: FONTS.family.body, fontSize: 14, color: COLORS.textPrimary }, multiline: { minHeight: 76, textAlignVertical: 'top' }, hint: { fontFamily: FONTS.family.body, fontSize: 11, lineHeight: 16, color: COLORS.textMuted, marginTop: 8, textAlign: 'center' }, warn: { fontFamily: FONTS.family.bodyMedium, fontSize: 12, lineHeight: 18, color: COLORS.warning, marginTop: 7 }, primary: { alignItems: 'center', justifyContent: 'center', backgroundColor: COLORS.primary, borderRadius: RADIUS.full, paddingVertical: 14, marginTop: SPACING.lg }, primaryText: { fontFamily: FONTS.family.bodySemibold, fontSize: 14, color: COLORS.white }, disabled: { opacity: 0.5 }, link: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.primary, textAlign: 'center', marginTop: SPACING.md },
});
