/**
 * Check a product you're considering — Step 14's secondary purchase entry.
 *
 * For the product a person cannot barcode-scan: a skin-care, hair-care or
 * fragrance product they are thinking about buying. Scan stays the primary
 * path; this screen says so first and offers the way back.
 *
 * Nothing here is new judgement. It reuses the existing candidate flow — the
 * inspection, the correction and confirmation, the canonical Care and
 * Fragrance checks and their result cards, the one-tap decision — and asks the
 * Step 14 purchase check only which of them applies. That check routes on the
 * server, so this screen shows a purchase answer only when the server routed
 * the candidate to Care or Fragrance and every fact it uses is confirmed:
 *
 *   decided                 -> the canonical Care or Fragrance result
 *   not_enough_information  -> the existing review, to confirm the facts
 *   prohibited              -> the supplement boundary, never a purchase UI
 *   unsupported             -> a plain refusal, never Care by default
 *
 * A candidate is not a shelf item: nothing here writes to the inventory.
 * No free-text search, no catalogue, no feed. Every word is keyed in
 * src/strings/purchaseOs.ts.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ScrollView, StyleSheet, Text, TextInput, TouchableOpacity, View } from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import * as ImagePicker from 'expo-image-picker';

import { PURCHASE_OS } from '../src/strings/purchaseOs';
import { CareCandidateReview, CarePurchaseResult } from '../src/components/shopping/CareShoppingPieces';
import { FragranceCandidateReview, FragranceShoppingResult } from '../src/components/shopping/FragranceShoppingPieces';
import { PurchaseMemoryCard } from '../src/components/shopping/PurchaseMemoryCard';
import {
  confirmPurchaseCandidate, getCandidatePurchaseCheck, getCarePurchaseCheck, getFragrancePurchaseCheck,
  getPurchaseStrategies, inspectPurchaseCandidate, recordCarePurchaseDecision,
  recordPurchaseCandidateDecision, uploadMedia,
  type CareCandidateInspection, type CarePurchaseCheck, type FragranceCandidateInspection,
  type FragrancePurchaseCheck, type PurchaseDecisionValue, type PurchaseOsCheck,
  type PurchaseStrategiesResponse,
} from '../src/services/apiV2';
import { COLORS, FONTS, RADIUS, SPACING } from '../src/theme/colors';

type CategoryKey = keyof typeof PURCHASE_OS.candidate.category;
type Inspection = CareCandidateInspection | FragranceCandidateInspection;
type ContextOption = { key: string; label: string };

const CARE_CATEGORIES: readonly string[] = ['beauty', 'hair'];
const isCategoryKey = (key: string): key is CategoryKey => key in PURCHASE_OS.candidate.category;

export default function PurchaseCandidateScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const copy = PURCHASE_OS.candidate;

  const [registry, setRegistry] = useState<PurchaseStrategiesResponse | null>(null);
  const [category, setCategory] = useState<CategoryKey | null>(null);
  const [mode, setMode] = useState<'details' | 'photo' | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const retryAction = useRef<(() => Promise<void>) | null>(null);

  const [name, setName] = useState('');
  const [brand, setBrand] = useState('');
  const [productType, setProductType] = useState('');
  const [ingredients, setIngredients] = useState('');
  const [size, setSize] = useState('');
  const [concentration, setConcentration] = useState('');
  const [occasions, setOccasions] = useState<string[]>([]);
  const [seasons, setSeasons] = useState<string[]>([]);
  const [price, setPrice] = useState('');

  const [inspection, setInspection] = useState<Inspection | null>(null);
  const [purchaseCheck, setPurchaseCheck] = useState<PurchaseOsCheck | null>(null);
  const [careCheck, setCareCheck] = useState<CarePurchaseCheck | null>(null);
  const [fragranceCheck, setFragranceCheck] = useState<FragrancePurchaseCheck | null>(null);
  const [correcting, setCorrecting] = useState(false);
  const [deciding, setDeciding] = useState(false);

  // One owner for every request: a failure shows one keyed line and one retry.
  const run = useCallback(async (action: () => Promise<void>) => {
    retryAction.current = action;
    setBusy(true);
    setFailed(false);
    try {
      await action();
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    void run(async () => { setRegistry(await getPurchaseStrategies()); });
  }, [run]);

  // Only categories the server's registry lists as active or prohibited, and
  // only ones this screen has words for. Anything else is simply not offered.
  const offered = (registry?.strategies ?? [])
    .filter((strategy) => strategy.state === 'active' || strategy.state === 'prohibited')
    .flatMap((strategy) => strategy.categories.map((row) => ({ key: row.key as string, state: strategy.state })))
    .filter((row): row is { key: CategoryKey; state: 'active' | 'prohibited' } => isCategoryKey(row.key));
  const selectedState = offered.find((row) => row.key === category)?.state ?? null;
  const isFragrance = category === 'perfumes';
  const contextOptions = registry?.fragrance_context_options ?? { occasions: [] as ContextOption[], seasons: [] as ContextOption[] };

  const reset = () => {
    setMode(null); setInspection(null); setPurchaseCheck(null); setCareCheck(null); setFragranceCheck(null);
    setCorrecting(false); setFailed(false); setName(''); setBrand(''); setProductType(''); setIngredients('');
    setSize(''); setConcentration(''); setOccasions([]); setSeasons([]); setPrice('');
  };

  /** Ask the Purchase OS which answer applies, then load only that one. */
  const route = async (candidateId: string) => {
    const check = await getCandidatePurchaseCheck(candidateId);
    setPurchaseCheck(check);
    setCareCheck(null);
    setFragranceCheck(null);
    if (check.decision.state !== 'decided') return;
    if (check.context.strategy === 'care_purchase') setCareCheck(await getCarePurchaseCheck(candidateId));
    else if (check.context.strategy === 'fragrance_purchase') setFragranceCheck(await getFragrancePurchaseCheck(candidateId));
  };

  const prefill = (result: Inspection) => {
    const candidate = result.candidate;
    const details = (candidate.details || {}) as Record<string, unknown>;
    const text = (value: unknown) => (typeof value === 'string' ? value : '');
    setName(candidate.display_name);
    setBrand(candidate.brand || '');
    setProductType(text(details.product_type ?? details.fragrance_family));
    setIngredients(text(details.ingredients_text));
    setSize(text(details.size));
    setConcentration(text(details.concentration));
    setOccasions(Array.isArray(details.occasion) ? details.occasion as string[] : []);
    setSeasons(Array.isArray(details.season) ? details.season as string[] : []);
    setPrice(candidate.price == null ? '' : String(candidate.price));
  };

  const inspect = (body: Parameters<typeof inspectPurchaseCandidate>[0]) => run(async () => {
    const result = await inspectPurchaseCandidate(body);
    setInspection(result);
    prefill(result);
    await route(result.candidate.id);
  });

  const trimmed = (value: string) => value.trim() || undefined;
  const amount = (value: string) => (value.trim() ? Number(value) : undefined);

  const checkDetails = () => {
    if (!category || !name.trim()) return;
    if (isFragrance) {
      void inspect({
        source: 'manual', expected_category: 'perfumes',
        item: {
          category: 'perfumes', display_name: name.trim(), brand: trimmed(brand), price: amount(price),
          details: { fragrance_family: trimmed(productType), concentration: trimmed(concentration), occasion: occasions, season: seasons },
        },
      });
      return;
    }
    void inspect({
      source: 'manual',
      item: {
        category: category as 'beauty' | 'hair', display_name: name.trim(), brand: trimmed(brand), price: amount(price),
        details: { product_type: trimmed(productType), ingredients_text: trimmed(ingredients), size: trimmed(size) },
      },
    });
  };

  const checkPhoto = () => {
    if (!category) return;
    void run(async () => {
      const picked = await ImagePicker.launchImageLibraryAsync({ quality: 0.75, mediaTypes: ['images'] });
      if (picked.canceled || !picked.assets[0]) return;
      const asset = picked.assets[0];
      const uploaded = await uploadMedia({
        uri: asset.uri, name: asset.fileName || `candidate-${Date.now()}.jpg`, type: asset.mimeType || 'image/jpeg',
      });
      const result = await inspectPurchaseCandidate({ source: 'screenshot', media_asset_id: uploaded.id, expected_category: category });
      setInspection(result);
      prefill(result);
      await route(result.candidate.id);
    });
  };

  /** The person confirms (or corrects) what was read; only then can it be checked. */
  const confirm = () => {
    if (!inspection) return;
    const candidate = inspection.candidate;
    void run(async () => {
      const details = isFragrance
        ? { fragrance_family: trimmed(productType) ?? null, concentration: trimmed(concentration) ?? null, occasion: occasions, season: seasons }
        : { product_type: trimmed(productType), size: trimmed(size), ingredients_text: trimmed(ingredients) };
      const confirmed = await confirmPurchaseCandidate(candidate.id, {
        display_name: name.trim() || candidate.display_name,
        brand: brand.trim() || null,
        details,
        price: price.trim() ? Number(price) : null,
        currency: candidate.currency,
      });
      setInspection(confirmed);
      setCorrecting(false);
      await route(confirmed.candidate.id);
    });
  };

  /** One tap. The decision is memory: it never adds anything to the shelf. */
  const decide = (decision: PurchaseDecisionValue) => {
    if (!inspection || deciding) return;
    const id = inspection.candidate.id;
    setDeciding(true);
    setFailed(false);
    const save = careCheck
      ? recordCarePurchaseDecision(id, decision, undefined, careCheck.assessment.plan_date)
      : recordPurchaseCandidateDecision(id, decision);
    void save
      .then(async (saved) => {
        if (careCheck) setCareCheck({ ...careCheck, decision: saved });
        if (fragranceCheck) setFragranceCheck({ ...fragranceCheck, decision: saved });
        // Memory is read back from the Purchase OS, so the card never shows
        // what this person chose before they changed it.
        setPurchaseCheck(await getCandidatePurchaseCheck(id));
      })
      .catch(() => setFailed(true))
      .finally(() => setDeciding(false));
  };

  const toggle = (values: string[], setValues: (next: string[]) => void, key: string) =>
    setValues(values.includes(key) ? values.filter((value) => value !== key) : [...values, key]);

  const memory = purchaseCheck?.memory;
  const memoryCard = memory ? (
    <PurchaseMemoryCard
      guardState={memory.guard_state ?? null}
      occurredAt={memory.most_recent_exact?.occurred_at ?? null}
      considerationCount={memory.prior_consideration_count ?? 0}
    />
  ) : null;

  const state = purchaseCheck?.decision.state ?? null;
  const showBoundary = selectedState === 'prohibited' || state === 'prohibited';
  const showForm = !showBoundary && !!category && ((mode === 'details' && !inspection) || correcting);

  const input = (label: string, value: string, onChange: (next: string) => void, options: { multiline?: boolean; numeric?: boolean } = {}) => (
    <View key={label}>
      <Text style={styles.label}>{label}</Text>
      <TextInput
        accessibilityLabel={label}
        value={value}
        onChangeText={onChange}
        multiline={options.multiline}
        keyboardType={options.numeric ? 'numeric' : 'default'}
        style={[styles.input, options.multiline && styles.multiline]}
      />
    </View>
  );

  const chips = (options: ContextOption[], values: string[], setValues: (next: string[]) => void) => (
    <View style={styles.chipRow}>
      {options.map((option) => {
        const selected = values.includes(option.key);
        return (
          <TouchableOpacity
            key={option.key}
            accessibilityRole="button"
            accessibilityLabel={option.label}
            accessibilityState={{ selected }}
            onPress={() => toggle(values, setValues, option.key)}
            style={[styles.chip, selected && styles.chipSelected]}
          >
            <Text style={[styles.chipText, selected && styles.chipTextSelected]}>{option.label}</Text>
          </TouchableOpacity>
        );
      })}
    </View>
  );

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <View style={styles.top}>
        <TouchableOpacity accessibilityRole="button" accessibilityLabel={copy.back} onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={24} color={COLORS.textPrimary} />
        </TouchableOpacity>
        <Text style={styles.topTitle} accessibilityRole="header">{copy.title}</Text>
        <View style={styles.topSpacer} />
      </View>
      <ScrollView contentContainerStyle={{ padding: SPACING.lg, paddingBottom: insets.bottom + 48 }} keyboardShouldPersistTaps="handled">
        {/* Scan first, always: this entry is for what cannot be scanned. */}
        <View style={styles.scanFirst}>
          <Text style={styles.body}>{copy.scanFirst}</Text>
          <TouchableOpacity accessibilityRole="button" accessibilityLabel={copy.scanAction} onPress={() => router.replace('/scan-product')}>
            <Text style={styles.link}>{copy.scanAction}</Text>
          </TouchableOpacity>
        </View>

        {failed && (
          <View style={styles.card} accessibilityRole="alert">
            <Text style={styles.body}>{copy.failed}</Text>
            <TouchableOpacity
              accessibilityRole="button" accessibilityLabel={copy.retry}
              onPress={() => { if (retryAction.current) void run(retryAction.current); }}
            >
              <Text style={styles.link}>{copy.retry}</Text>
            </TouchableOpacity>
          </View>
        )}

        {!inspection && (
          <>
            <Text style={styles.section}>{copy.categoryTitle}</Text>
            <View style={styles.chipRow}>
              {offered.map((row) => {
                const selected = row.key === category;
                return (
                  <TouchableOpacity
                    key={row.key}
                    accessibilityRole="button"
                    accessibilityLabel={copy.category[row.key]}
                    accessibilityState={{ selected }}
                    onPress={() => { reset(); setCategory(row.key); }}
                    style={[styles.chip, selected && styles.chipSelected]}
                  >
                    <Text style={[styles.chipText, selected && styles.chipTextSelected]}>{copy.category[row.key]}</Text>
                  </TouchableOpacity>
                );
              })}
            </View>
          </>
        )}

        {showBoundary && (
          <View style={styles.card} testID="supplement-boundary">
            <Text style={styles.cardTitle}>{PURCHASE_OS.supplementBoundary.title}</Text>
            <Text style={styles.body}>{PURCHASE_OS.supplementBoundary.body}</Text>
            <TouchableOpacity
              accessibilityRole="button"
              accessibilityLabel={PURCHASE_OS.supplementBoundary.action}
              onPress={() => router.push({ pathname: '/inventory-add', params: { category: 'supplements' } })}
            >
              <Text style={styles.link}>{PURCHASE_OS.supplementBoundary.action}</Text>
            </TouchableOpacity>
          </View>
        )}

        {!showBoundary && selectedState === 'active' && !inspection && (
          <View style={styles.chipRow}>
            <TouchableOpacity
              accessibilityRole="button" accessibilityLabel={copy.mode.details}
              accessibilityState={{ selected: mode === 'details' }}
              onPress={() => setMode('details')} style={[styles.chip, mode === 'details' && styles.chipSelected]}
            >
              <Text style={[styles.chipText, mode === 'details' && styles.chipTextSelected]}>{copy.mode.details}</Text>
            </TouchableOpacity>
            <TouchableOpacity
              accessibilityRole="button" accessibilityLabel={copy.mode.photo}
              accessibilityState={{ disabled: busy }} disabled={busy}
              onPress={() => { setMode('photo'); checkPhoto(); }} style={styles.chip}
            >
              <Text style={styles.chipText}>{copy.mode.photo}</Text>
            </TouchableOpacity>
          </View>
        )}

        {showForm && (
          <View style={styles.card}>
            {input(copy.field.name, name, setName)}
            {input(copy.field.brand, brand, setBrand)}
            {isFragrance ? (
              <>
                {input(copy.field.family, productType, setProductType)}
                {input(copy.field.concentration, concentration, setConcentration)}
                {chips(contextOptions.occasions, occasions, setOccasions)}
                {chips(contextOptions.seasons, seasons, setSeasons)}
              </>
            ) : (
              <>
                {input(copy.field.productType, productType, setProductType)}
                {input(copy.field.ingredients, ingredients, setIngredients, { multiline: true })}
                {input(copy.field.size, size, setSize)}
              </>
            )}
            {input(copy.field.price, price, setPrice, { numeric: true })}
            <TouchableOpacity
              accessibilityRole="button"
              accessibilityLabel={correcting ? copy.confirm : copy.check}
              accessibilityState={{ disabled: busy || !name.trim() }}
              disabled={busy || !name.trim()}
              onPress={correcting ? confirm : checkDetails}
              style={[styles.primary, (busy || !name.trim()) && styles.disabled]}
            >
              <Text style={styles.primaryText}>{busy ? copy.working : correcting ? copy.confirm : copy.check}</Text>
            </TouchableOpacity>
            <Text style={styles.note}>{copy.notInInventory}</Text>
          </View>
        )}

        {!!inspection && state === 'not_enough_information' && !correcting && (
          <>
            <Text style={styles.body}>{copy.confirmationRequired}</Text>
            {CARE_CATEGORIES.includes(inspection.candidate.category) ? (
              <CareCandidateReview
                inspection={inspection as CareCandidateInspection}
                onConfirm={confirm}
                onCorrect={() => setCorrecting(true)}
              />
            ) : (
              <FragranceCandidateReview
                inspection={inspection as FragranceCandidateInspection}
                onConfirm={confirm}
                onCorrect={() => setCorrecting(true)}
              />
            )}
          </>
        )}

        {state === 'unsupported' && <Text style={styles.body}>{copy.unsupported}</Text>}

        {state === 'decided' && careCheck && (
          <CarePurchaseResult check={careCheck} onReset={reset} busy={deciding} onDecide={decide} purchaseMemory={memoryCard} />
        )}
        {state === 'decided' && fragranceCheck && (
          <FragranceShoppingResult check={fragranceCheck} onReset={reset} busy={deciding} onDecide={decide} purchaseMemory={memoryCard} />
        )}

        {!!inspection && state !== 'decided' && (
          <TouchableOpacity accessibilityRole="button" accessibilityLabel={copy.startOver} onPress={reset}>
            <Text style={styles.link}>{copy.startOver}</Text>
          </TouchableOpacity>
        )}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.background },
  top: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: SPACING.lg, paddingVertical: SPACING.sm },
  topTitle: { flex: 1, textAlign: 'center', fontFamily: FONTS.family.headingMedium, fontSize: 17, color: COLORS.textPrimary },
  topSpacer: { width: 24 },
  scanFirst: { backgroundColor: COLORS.card, borderRadius: RADIUS.xl, padding: SPACING.md, borderWidth: 1, borderColor: COLORS.border, marginBottom: SPACING.md },
  card: { backgroundColor: COLORS.card, borderRadius: RADIUS.xl, padding: SPACING.lg, borderWidth: 1, borderColor: COLORS.border, marginBottom: SPACING.md },
  cardTitle: { fontFamily: FONTS.family.headingMedium, fontSize: 18, color: COLORS.textPrimary },
  section: { fontFamily: FONTS.family.bodySemibold, fontSize: 14, color: COLORS.textPrimary, marginBottom: SPACING.sm },
  body: { fontFamily: FONTS.family.body, fontSize: 13, lineHeight: 19, color: COLORS.textSecondary, marginTop: 4 },
  note: { fontFamily: FONTS.family.body, fontSize: 11, lineHeight: 16, color: COLORS.textMuted, marginTop: SPACING.sm },
  label: { fontFamily: FONTS.family.bodySemibold, fontSize: 12, color: COLORS.textPrimary, marginTop: SPACING.sm },
  input: { borderWidth: 1, borderColor: COLORS.border, borderRadius: RADIUS.md, paddingHorizontal: 12, paddingVertical: 10, marginTop: 4, fontFamily: FONTS.family.body, fontSize: 14, color: COLORS.textPrimary },
  multiline: { minHeight: 80, textAlignVertical: 'top' },
  chipRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginBottom: SPACING.md },
  chip: { borderRadius: RADIUS.full, borderWidth: 1, borderColor: COLORS.border, paddingHorizontal: 14, paddingVertical: 9 },
  chipSelected: { backgroundColor: COLORS.primary, borderColor: COLORS.primary },
  chipText: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.textPrimary },
  chipTextSelected: { color: COLORS.white },
  primary: { alignItems: 'center', justifyContent: 'center', backgroundColor: COLORS.primary, borderRadius: RADIUS.full, paddingVertical: 13, marginTop: SPACING.md },
  primaryText: { fontFamily: FONTS.family.bodySemibold, fontSize: 14, color: COLORS.white },
  disabled: { opacity: 0.6 },
  link: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.primary, textAlign: 'center', marginTop: SPACING.sm, paddingVertical: SPACING.xs },
});
