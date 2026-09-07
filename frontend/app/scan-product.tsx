/**
 * The scanner. This is the first thing the app shows.
 *
 * It opens the camera with nothing set up — no account, no invite, no
 * onboarding — because a person standing in a shop holding a packet should get
 * an answer, not a sign-up form. The phone registers itself instead
 * (src/services/productScan.ts), and that identity reaches product data and
 * nothing else.
 *
 * Offline is a normal state here, not an error: answers are cached, scans are
 * queued, and the queue is safe to replay.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { CameraView, useCameraPermissions } from 'expo-camera';
import { useFocusEffect, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';

import { COLORS, FONTS, RADIUS, SPACING } from '../src/theme/colors';
import {
  confirmLabel,
  confirmSkinCareLabel,
  ensureDevice,
  ensureDeviceClaimed,
  fetchSkinCareForYou,
  newScanId,
  readQueue,
  scanBarcode,
  syncQueue,
  type ConfirmedSkinCareLabel,
  type ForYouResponse,
  type ForYouSafetyContext,
  type ScanResult,
  type SkinCareLabelFactsView,
} from '../src/services/productScan';
import { LabelReview, NotFoundResult, OfflineNote, ProductResult } from '../src/components/scan/ScanPieces';
import {
  ConfirmedSkinCareLabelCard,
  LabelTypeChoice,
  SafetyPreflight,
  SkinCareLabelReview,
  type LabelKind,
} from '../src/components/scan/SkinCarePieces';
import { ForYouCard } from '../src/components/scan/ForYouCard';
import { S } from '../src/strings/verdict';
import { transcribeProductLabel, transcribeSkinCareLabel, uploadMedia } from '../src/services/apiV2';
import { errorMessage } from '../src/services/api';
import { useUserStore } from '../src/store/userStore';

/** The symbologies on Indian retail packaging. QR is not one of them. */
const BARCODE_TYPES = ['ean13', 'ean8', 'upc_a', 'upc_e', 'code128', 'itf14'] as const;

type Stage = 'camera' | 'looking' | 'result' | 'label-kind' | 'label' | 'skin-care-confirmed';

type LabelDraft = {
  facts: Record<string, unknown>;
  aiRunId: string;
};

/**
 * One skin-care draft, with the idempotency key it will be confirmed under.
 *
 * The key is minted once per transcription and reused for every retry of that
 * confirmation. A fresh key per HTTP attempt would turn one physical capture
 * into two logical ones; retaking the photograph is what deserves a new key.
 */
type SkinCareDraft = {
  facts: SkinCareLabelFactsView;
  aiRunId: string;
  clientScanId: string;
  ingredientsReadable: boolean;
  message: string | null;
};

export default function ScanProductScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [permission, requestPermission] = useCameraPermissions();
  const { userId } = useUserStore();

  const [stage, setStage] = useState<Stage>('camera');
  const [result, setResult] = useState<ScanResult | null>(null);
  const [queued, setQueued] = useState(0);
  const [labelDraft, setLabelDraft] = useState<LabelDraft | null>(null);
  const [labelBusy, setLabelBusy] = useState(false);
  const [labelError, setLabelError] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState<string | null>(null);
  const [labelKind, setLabelKind] = useState<LabelKind | null>(null);
  const [skinDraft, setSkinDraft] = useState<SkinCareDraft | null>(null);
  const [skinConfirmed, setSkinConfirmed] = useState<ConfirmedSkinCareLabel | null>(null);
  const [skinFacts, setSkinFacts] = useState<SkinCareLabelFactsView | null>(null);
  // Session-only. Never stored, never logged, never sent to analytics.
  const [safety, setSafety] = useState<ForYouSafetyContext | null>(null);
  const [forYou, setForYou] = useState<ForYouResponse | null>(null);
  const [forYouLoading, setForYouLoading] = useState(false);
  const [forYouFailed, setForYouFailed] = useState(false);
  const cameraRef = useRef<CameraView | null>(null);
  // One barcode at a time: the camera fires this many times a second.
  const busy = useRef(false);

  useEffect(() => {
    // Register the phone and flush anything held from a previous session.
    void ensureDevice().then(() => syncQueue()).catch(() => undefined);
    void readQueue().then((q) => setQueued(q.length)).catch(() => undefined);
    if (permission && !permission.granted && permission.canAskAgain) {
      void requestPermission();
    }
    // Runs once; the permission prompt is re-checked by the effect below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (permission && !permission.granted && permission.canAskAgain) void requestPermission();
  }, [permission, requestPermission]);

  const handleBarcode = useCallback(async ({ data }: { data: string }) => {
    if (busy.current || !data) return;
    busy.current = true;
    setStage('looking');
    try {
      const found = await scanBarcode(data);
      setResult(found);
      setStage('result');
    } finally {
      const remaining = await readQueue().catch(() => []);
      setQueued(remaining.length);
      busy.current = false;
    }
  }, []);

  const scanAgain = useCallback(() => {
    setResult(null);
    setLabelDraft(null);
    setLabelError(null);
    setConfirmed(null);
    setLabelKind(null);
    setSkinDraft(null);
    setSkinConfirmed(null);
    setSkinFacts(null);
    setSafety(null);
    setForYou(null);
    setForYouFailed(false);
    setStage('camera');
  }, []);

  const captureLabel = useCallback(() => {
    if (!userId) {
      // Reading a label costs a model call, so it is attached to an account.
      // Scanning itself never is.
      router.push('/(auth)/welcome');
      return;
    }
    // The category is asked, never inferred. Which route the capture travels
    // down is the assertion, so the person has to make it.
    setStage('label-kind');
    setLabelDraft(null);
    setSkinDraft(null);
  }, [router, userId]);

  /**
   * Settle the category, and for skin care settle device ownership too.
   *
   * Step 8J refuses a confirmation on a device this account does not own, and
   * that refusal would otherwise arrive *after* a model call had been spent on
   * the photograph. Claiming first costs a cheap request; getting it wrong
   * costs the person a wasted capture.
   */
  const chooseLabelKind = useCallback(async (kind: LabelKind) => {
    setLabelError(null);
    if (kind === 'skin_care') {
      if (!userId) { router.push('/(auth)/welcome'); return; }
      const owned = await ensureDeviceClaimed(userId);
      if (!owned) {
        setLabelError('This phone is not linked to your account yet. Try again in a moment.');
        return;
      }
    }
    setLabelKind(kind);
    setStage('label');
  }, [router, userId]);

  /** Take the photo, read it, and show what came back. Nothing is saved yet. */
  const captureAndRead = useCallback(async () => {
    if (!result || labelBusy) return;
    setLabelBusy(true);
    setLabelError(null);
    try {
      const photo = await cameraRef.current?.takePictureAsync({ quality: 0.7, skipProcessing: true });
      if (!photo?.uri) throw new Error('no photo');
      const asset = await uploadMedia(
        { uri: photo.uri, name: 'label.jpg', type: 'image/jpeg' },
        'inventory_item',
      );
      if (labelKind === 'skin_care') {
        const read = await transcribeSkinCareLabel(result.barcode, asset.id);
        const aiRunId = read.provenance?.ai_run_id;
        if (typeof aiRunId !== 'string' || !aiRunId.trim()) {
          setLabelError(S.labelReview.missingConfirmationReference);
          return;
        }
        // One key per transcription, minted here and reused for every retry
        // of this confirmation.
        setSkinDraft({
          facts: read.facts,
          aiRunId,
          clientScanId: newScanId(),
          ingredientsReadable: read.ingredients_readable !== false,
          message: read.message ?? null,
        });
        return;
      }
      const read = await transcribeProductLabel(result.barcode, asset.id);
      const aiRunId = read.provenance?.ai_run_id;
      if (typeof aiRunId !== 'string' || !aiRunId.trim()) {
        setLabelError(S.labelReview.missingConfirmationReference);
        return;
      }
      setLabelDraft({ facts: read.facts, aiRunId });
    } catch (err) {
      setLabelError(errorMessage(err, 'We could not read that photo. Try again with more light.'));
    } finally {
      setLabelBusy(false);
    }
  }, [labelBusy, labelKind, result]);

  /**
   * The VC-07 confirm: the person says it is right, and only then it counts.
   *
   * The record is read back afterwards so they see the confidence their
   * confirmation actually produced, rather than being told it worked.
   */
  const acceptLabel = useCallback(async () => {
    if (!result || !labelDraft) return;
    setLabelBusy(true);
    setLabelError(null);
    try {
      const saved = await confirmLabel(result.barcode, labelDraft.aiRunId);
      if (!saved) {
        setLabelError(S.labelReview.saveFailed);
        return;
      }
      setConfirmed(`Saved. ${saved.confidence.text}`);
      setLabelDraft(null);
      setResult(await scanBarcode(result.barcode));
      setStage('result');
    } catch (err) {
      setLabelError(errorMessage(err, 'We could not save that just now. Try again in a moment.'));
    } finally {
      setLabelBusy(false);
    }
  }, [labelDraft, result]);

  /**
   * Ask Step 8K about the confirmed pack.
   *
   * Never cached: the answer depends on the current pack, live profile facts,
   * live evidence and the active release, any of which can change between two
   * identical requests.
   */
  const loadForYou = useCallback(async (barcode: string, context: ForYouSafetyContext) => {
    setForYouLoading(true);
    setForYouFailed(false);
    try {
      const answer = await fetchSkinCareForYou(barcode, context);
      setForYou(answer);
      setForYouFailed(answer === null);
    } catch {
      // A technical failure is never a decision.
      setForYou(null);
      setForYouFailed(true);
    } finally {
      setForYouLoading(false);
    }
  }, []);

  const submitSafety = useCallback((context: ForYouSafetyContext) => {
    if (!result) return;
    setSafety(context);
    void loadForYou(result.barcode, context);
  }, [loadForYou, result]);

  /**
   * Confirm the skin-care label.
   *
   * The one thing this must never do afterwards is scan the barcode again.
   * Step 8K reads the device's *newest* scan event as the current physical
   * pack, so a plain lookup here would replace the confirmation and Step 8K
   * would correctly answer that no pack has been confirmed. The generic food
   * path re-scans; this one must not.
   */
  const acceptSkinCareLabel = useCallback(async () => {
    if (!result || !skinDraft || labelBusy) return;
    if (!skinDraft.ingredientsReadable) return;
    setLabelBusy(true);
    setLabelError(null);
    try {
      const saved = await confirmSkinCareLabel(
        result.barcode, skinDraft.aiRunId, skinDraft.clientScanId,
      );
      if (!saved) {
        setLabelError(S.labelReview.saveFailed);
        return;
      }
      setSkinConfirmed(saved);
      setSkinFacts(skinDraft.facts);
      setSkinDraft(null);
      setStage('skin-care-confirmed');
    } catch (err) {
      setLabelError(errorMessage(err, 'We could not save that just now. Try again in a moment.'));
    } finally {
      setLabelBusy(false);
    }
  }, [labelBusy, result, skinDraft]);

  /**
   * Re-ask Step 8K when this screen regains focus.
   *
   * Editing a skin fact must be able to change or withdraw a decision without
   * rescanning anything: the product evidence is bound to the snapshot, but the
   * personal context is live.
   */
  useFocusEffect(
    useCallback(() => {
      if (stage !== 'skin-care-confirmed' || !result || !safety) return;
      void loadForYou(result.barcode, safety);
    }, [loadForYou, result, safety, stage]),
  );

  if (permission && !permission.granted && !permission.canAskAgain) {
    return (
      <View style={[styles.container, { paddingTop: insets.top + SPACING.lg }]}>
        <Text style={styles.title}>The camera is switched off</Text>
        <Text style={styles.body}>
          Scanning needs the camera. Turn it on for GlamGenius in your phone&apos;s settings, then come back.
        </Text>
        <TouchableOpacity
          accessibilityRole="button"
          accessibilityLabel="Continue without scanning"
          onPress={() => router.replace('/intro')}
          style={styles.linkButton}
        >
          <Text style={styles.linkText}>Continue without scanning</Text>
        </TouchableOpacity>
      </View>
    );
  }

  if (stage === 'camera') {
    return (
      <View style={styles.camera}>
        {Platform.OS !== 'web' && permission?.granted ? (
          <CameraView
            ref={cameraRef}
            style={StyleSheet.absoluteFill}
            facing="back"
            barcodeScannerSettings={{ barcodeTypes: [...BARCODE_TYPES] }}
            onBarcodeScanned={handleBarcode}
            testID="scan-camera"
          />
        ) : (
          <View style={[StyleSheet.absoluteFill, styles.cameraFallback]} />
        )}

        <View style={[styles.overlay, { paddingTop: insets.top + SPACING.md, paddingBottom: insets.bottom + SPACING.lg }]}>
          <View style={styles.topRow}>
            <Text style={styles.overlayBrand}>GlamGenius</Text>
            <TouchableOpacity
              accessibilityRole="button"
              accessibilityLabel={userId ? 'Open account' : 'About GlamGenius'}
              onPress={() => router.push(userId ? '/(tabs)/you' : '/intro')}
              style={styles.topButton}
            >
              <Text style={styles.topButtonText}>{userId ? 'Account' : 'About'}</Text>
            </TouchableOpacity>
          </View>

          <View style={styles.reticle} />

          <View style={styles.bottom}>
            <Text style={styles.overlayTitle}>Point at a barcode</Text>
            <Text style={styles.overlayBody}>
              No account needed. We will say what we know, and how far it can be trusted.
            </Text>
            {queued > 0 && (
              <Text style={styles.overlayQueue}>
                {queued} scan{queued === 1 ? '' : 's'} saved on this phone, waiting to sync.
              </Text>
            )}
          </View>
        </View>
      </View>
    );
  }

  return (
    <ScrollView
      style={styles.container}
      contentContainerStyle={{ paddingTop: insets.top + SPACING.md, paddingBottom: insets.bottom + SPACING.xl, gap: SPACING.md }}
    >
      {stage === 'label' && !labelDraft && !skinDraft && Platform.OS !== 'web' && permission?.granted && (
        <View style={styles.labelPreview}>
          <CameraView ref={cameraRef} style={StyleSheet.absoluteFill} facing="back" testID="label-camera" />
        </View>
      )}

      {stage === 'looking' && (
        <View style={styles.looking}>
          <ActivityIndicator color={COLORS.primary} />
          <Text style={styles.body}>Looking it up…</Text>
        </View>
      )}

      {result?.offline && <OfflineNote queued={queued} />}

      {stage === 'result' && result && (result.found
        ? <ProductResult result={result} onCaptureLabel={captureLabel} onScanAgain={scanAgain} />
        : <NotFoundResult result={result} onCaptureLabel={captureLabel} onScanAgain={scanAgain} />)}

      {stage === 'result' && result?.found && (
        <TouchableOpacity
          accessibilityRole="button"
          accessibilityLabel={S.primary.why}
          onPress={() => router.push({ pathname: '/verdict', params: { barcode: result.barcode } })}
          style={styles.primaryButton}
        >
          <Text style={styles.primaryText}>{S.primary.why}</Text>
        </TouchableOpacity>
      )}

      {stage === 'label-kind' && (
        <>
          <LabelTypeChoice onChoose={(kind) => void chooseLabelKind(kind)} onCancel={scanAgain} />
          {!!labelError && <Text style={styles.error}>{labelError}</Text>}
        </>
      )}

      {stage === 'label' && skinDraft && (
        <SkinCareLabelReview
          facts={skinDraft.facts}
          ingredientsReadable={skinDraft.ingredientsReadable}
          message={skinDraft.message}
          busy={labelBusy}
          onConfirm={acceptSkinCareLabel}
          onRetake={() => { setSkinDraft(null); setLabelError(null); }}
        />
      )}

      {stage === 'skin-care-confirmed' && skinConfirmed && skinFacts && result && (
        <>
          <ConfirmedSkinCareLabelCard
            barcode={result.barcode}
            facts={skinFacts}
            confirmed={skinConfirmed}
            onScanAgain={scanAgain}
          />
          {!safety ? (
            <SafetyPreflight onSubmit={submitSafety} busy={forYouLoading} />
          ) : (
            <ForYouCard
              response={forYou}
              loading={forYouLoading}
              failed={forYouFailed}
              onRetry={() => void loadForYou(result.barcode, safety)}
              onAddSkinDetails={() => router.push('/for-you-profile')}
            />
          )}
        </>
      )}

      {stage === 'label' && labelDraft && (
        <LabelReview
          facts={labelDraft.facts}
          busy={labelBusy}
          onConfirm={acceptLabel}
          onRetake={() => { setLabelDraft(null); setLabelError(null); }}
        />
      )}

      {stage === 'label' && !labelDraft && !skinDraft && (
        <View style={styles.card}>
          <Ionicons name="camera-outline" size={24} color={COLORS.primary} />
          <Text style={styles.title}>Photograph the label</Text>
          <Text style={styles.body}>
            Hold the pack steady so the ingredient list and the nutrition table are both in frame. We read
            what is printed, show it back to you, and save nothing until you say it is right.
          </Text>
          {!!labelError && <Text style={styles.error}>{labelError}</Text>}
          <TouchableOpacity
            accessibilityRole="button"
            accessibilityLabel="Take the label photo"
            onPress={captureAndRead}
            disabled={labelBusy}
            style={styles.primaryButton}
          >
            <Text style={styles.primaryText}>{labelBusy ? 'Reading the label…' : 'Take the photo'}</Text>
          </TouchableOpacity>
          <TouchableOpacity
            accessibilityRole="button"
            accessibilityLabel="Cancel label capture"
            onPress={scanAgain}
            style={styles.linkButton}
          >
            <Text style={styles.linkText}>Not now</Text>
          </TouchableOpacity>
        </View>
      )}

      {!!confirmed && (
        <Text style={styles.confirmed} accessibilityLabel="Label saved">{confirmed}</Text>
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.background, paddingHorizontal: SPACING.lg },
  camera: { flex: 1, backgroundColor: '#000' },
  cameraFallback: { backgroundColor: '#111' },
  overlay: { flex: 1, justifyContent: 'space-between', paddingHorizontal: SPACING.lg },
  topRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  overlayBrand: { fontFamily: FONTS.family.heading, fontSize: 20, color: COLORS.white },
  topButton: { paddingHorizontal: 14, paddingVertical: 8, borderRadius: RADIUS.md, backgroundColor: 'rgba(255,255,255,0.16)' },
  topButtonText: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.white },
  reticle: {
    alignSelf: 'center', width: '78%', aspectRatio: 1.6, borderRadius: RADIUS.lg,
    borderWidth: 2, borderColor: 'rgba(255,255,255,0.85)',
  },
  bottom: { gap: 6 },
  overlayTitle: { fontFamily: FONTS.family.heading, fontSize: 24, color: COLORS.white },
  overlayBody: { fontFamily: FONTS.family.body, fontSize: 14, color: 'rgba(255,255,255,0.82)' },
  overlayQueue: { fontFamily: FONTS.family.body, fontSize: 12, color: 'rgba(255,255,255,0.7)', marginTop: 4 },
  looking: { alignItems: 'center', gap: SPACING.sm, paddingVertical: SPACING.xl },
  card: {
    backgroundColor: COLORS.card, borderRadius: RADIUS.lg, padding: SPACING.lg,
    borderWidth: 1, borderColor: COLORS.border, gap: SPACING.sm,
  },
  title: { fontFamily: FONTS.family.heading, fontSize: 22, color: COLORS.textPrimary },
  body: { fontFamily: FONTS.family.body, fontSize: 14, lineHeight: 21, color: COLORS.textSecondary },
  labelPreview: { height: 220, borderRadius: RADIUS.lg, overflow: 'hidden', backgroundColor: '#000' },
  error: { fontFamily: FONTS.family.body, fontSize: 13, color: COLORS.error },
  confirmed: { fontFamily: FONTS.family.bodySemibold, fontSize: 14, color: COLORS.success, textAlign: 'center' },
  primaryButton: {
    backgroundColor: COLORS.primary, borderRadius: RADIUS.md, paddingVertical: 14,
    alignItems: 'center', marginTop: SPACING.sm,
  },
  primaryText: { fontFamily: FONTS.family.bodySemibold, fontSize: 15, color: COLORS.white },
  linkButton: { paddingVertical: 12, alignItems: 'center' },
  linkText: { fontFamily: FONTS.family.bodySemibold, fontSize: 14, color: COLORS.primary },
});
