/**
 * Check a product you're considering — Step 14's secondary purchase entry.
 *
 * For the product a person cannot barcode-scan: a skin-care, hair-care or
 * fragrance product they are thinking about buying. Scan stays the primary
 * path; this screen says so first and offers the way back.
 *
 * No free text, anywhere (PRODUCT_CONSTITUTION.md: structured choices only).
 * The customer picks a category, gives a photo or screenshot, and reviews what
 * was read from it. If the read is right they confirm it; if it is wrong they
 * read another photo. Nothing is ever typed — not a name, a brand, an
 * ingredient list or a price. Fragrance alone has structured choices (where
 * and when it would be used), picked from the server's own options.
 *
 *   category → photo → extracted facts → confirm OR read another photo
 *            → canonical Care / Fragrance check
 *
 * Nothing here is new judgement. It reuses the existing candidate inspection,
 * confirmation, canonical checks, result cards and one-tap decision, and asks
 * the Step 14 purchase check only which of them applies. That check routes on
 * the server, so this screen shows a purchase answer only when the server
 * routed the candidate to Care or Fragrance and its facts are confirmed:
 *
 *   decided                 -> the canonical Care or Fragrance result
 *   not_enough_information  -> the extracted facts, to confirm or replace
 *   prohibited              -> the supplement boundary, never a purchase UI
 *   unsupported             -> a plain refusal, never Care by default
 *
 * Confirmation keeps the photo's provenance: a Care read is confirmed as it
 * is, and a Fragrance read keeps every extracted fact while only its
 * structured occasions and seasons are set. A candidate is not a shelf item:
 * nothing here writes to the inventory. Every word is keyed in
 * src/strings/purchaseOs.ts.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
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
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const retryAction = useRef<(() => Promise<void>) | null>(null);

  // The only customer choices on this screen besides the category: structured
  // Fragrance context, picked from the server's own options.
  const [occasions, setOccasions] = useState<string[]>([]);
  const [seasons, setSeasons] = useState<string[]>([]);

  const [inspection, setInspection] = useState<Inspection | null>(null);
  const [purchaseCheck, setPurchaseCheck] = useState<PurchaseOsCheck | null>(null);
  const [careCheck, setCareCheck] = useState<CarePurchaseCheck | null>(null);
  const [fragranceCheck, setFragranceCheck] = useState<FragrancePurchaseCheck | null>(null);
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
  const contextOptions = registry?.fragrance_context_options ?? { occasions: [] as ContextOption[], seasons: [] as ContextOption[] };

  const clearCandidate = () => {
    setInspection(null); setPurchaseCheck(null); setCareCheck(null); setFragranceCheck(null);
    setOccasions([]); setSeasons([]); setFailed(false);
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

  /** A photo or screenshot, read into a draft candidate. The only way in. */
  const readPhoto = () => {
    if (!category) return;
    void run(async () => {
      const picked = await ImagePicker.launchImageLibraryAsync({ quality: 0.75, mediaTypes: ['images'] });
      if (picked.canceled || !picked.assets[0]) return;
      const asset = picked.assets[0];
      const uploaded = await uploadMedia({
        uri: asset.uri, name: asset.fileName || `candidate-${Date.now()}.jpg`, type: asset.mimeType || 'image/jpeg',
      });
      const result = await inspectPurchaseCandidate({ source: 'screenshot', media_asset_id: uploaded.id, expected_category: category });
      clearCandidate();
      setInspection(result);
      const details = (result.candidate.details || {}) as Record<string, unknown>;
      setOccasions(Array.isArray(details.occasion) ? details.occasion as string[] : []);
      setSeasons(Array.isArray(details.season) ? details.season as string[] : []);
      await route(result.candidate.id);
    });
  };

  /**
   * The person confirms what was read. Care is confirmed exactly as read; a
   * Fragrance read keeps every extracted fact and only its structured context
   * is set. Provenance is untouched: a photo read stays a photo read.
   */
  const confirm = () => {
    if (!inspection) return;
    const candidate = inspection.candidate;
    void run(async () => {
      const body = candidate.category === 'perfumes'
        ? { details: { ...(candidate.details as Record<string, unknown>), occasion: occasions, season: seasons } }
        : {};
      const confirmed = await confirmPurchaseCandidate(candidate.id, body);
      setInspection(confirmed);
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
  const reviewing = !!inspection && state === 'not_enough_information';

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
      <ScrollView contentContainerStyle={{ padding: SPACING.lg, paddingBottom: insets.bottom + 48 }}>
        {/* Scan first, always: this entry is for what cannot be scanned. */}
        <View style={styles.scanFirst} testID="scan-first">
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
                    onPress={() => { clearCandidate(); setCategory(row.key); }}
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
          <View style={styles.card}>
            <Text style={styles.body}>{copy.photo.hint}</Text>
            <TouchableOpacity
              accessibilityRole="button"
              accessibilityLabel={copy.photo.action}
              accessibilityState={{ disabled: busy }}
              disabled={busy}
              onPress={readPhoto}
              style={[styles.primary, busy && styles.disabled]}
            >
              <Text style={styles.primaryText}>{busy ? copy.photo.working : copy.photo.action}</Text>
            </TouchableOpacity>
            <Text style={styles.note}>{copy.notInInventory}</Text>
          </View>
        )}

        {reviewing && inspection && (
          <>
            <Text style={styles.body}>{copy.confirmationRequired}</Text>
            {CARE_CATEGORIES.includes(inspection.candidate.category) ? (
              <CareCandidateReview inspection={inspection as CareCandidateInspection} onConfirm={confirm} />
            ) : (
              <>
                <View style={styles.card} testID="fragrance-context-choices">
                  <Text style={styles.label}>{copy.context.occasions}</Text>
                  {chips(contextOptions.occasions, occasions, setOccasions)}
                  <Text style={styles.label}>{copy.context.seasons}</Text>
                  {chips(contextOptions.seasons, seasons, setSeasons)}
                </View>
                <FragranceCandidateReview inspection={inspection as FragranceCandidateInspection} onConfirm={confirm} />
              </>
            )}
            <TouchableOpacity
              accessibilityRole="button"
              accessibilityLabel={copy.photo.another}
              accessibilityState={{ disabled: busy }}
              disabled={busy}
              onPress={readPhoto}
              style={styles.outline}
            >
              <Text style={styles.outlineText}>{copy.photo.another}</Text>
            </TouchableOpacity>
          </>
        )}

        {state === 'unsupported' && <Text style={styles.body}>{copy.unsupported}</Text>}

        {state === 'decided' && careCheck && (
          <CarePurchaseResult check={careCheck} onReset={clearCandidate} busy={deciding} onDecide={decide} purchaseMemory={memoryCard} />
        )}
        {state === 'decided' && fragranceCheck && (
          <FragranceShoppingResult check={fragranceCheck} onReset={clearCandidate} busy={deciding} onDecide={decide} purchaseMemory={memoryCard} />
        )}

        {!!inspection && state !== 'decided' && (
          <TouchableOpacity accessibilityRole="button" accessibilityLabel={copy.startOver} onPress={clearCandidate}>
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
  label: { fontFamily: FONTS.family.bodySemibold, fontSize: 12, color: COLORS.textPrimary, marginTop: SPACING.sm, marginBottom: SPACING.xs },
  chipRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginBottom: SPACING.md },
  chip: { borderRadius: RADIUS.full, borderWidth: 1, borderColor: COLORS.border, paddingHorizontal: 14, paddingVertical: 9 },
  chipSelected: { backgroundColor: COLORS.primary, borderColor: COLORS.primary },
  chipText: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.textPrimary },
  chipTextSelected: { color: COLORS.white },
  primary: { alignItems: 'center', justifyContent: 'center', backgroundColor: COLORS.primary, borderRadius: RADIUS.full, paddingVertical: 13, marginTop: SPACING.md },
  primaryText: { fontFamily: FONTS.family.bodySemibold, fontSize: 14, color: COLORS.white },
  outline: { alignItems: 'center', justifyContent: 'center', borderRadius: RADIUS.full, paddingVertical: 11, borderWidth: 1, borderColor: COLORS.border, marginTop: SPACING.sm },
  outlineText: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.textPrimary },
  disabled: { opacity: 0.6 },
  link: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.primary, textAlign: 'center', marginTop: SPACING.sm, paddingVertical: SPACING.xs },
});
