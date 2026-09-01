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
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';

import { COLORS, FONTS, RADIUS, SPACING } from '../src/theme/colors';
import {
  confirmLabel,
  ensureDevice,
  readQueue,
  scanBarcode,
  syncQueue,
  type ScanResult,
} from '../src/services/productScan';
import { LabelReview, NotFoundResult, OfflineNote, ProductResult } from '../src/components/scan/ScanPieces';
import { S } from '../src/strings/verdict';
import { transcribeProductLabel, uploadMedia } from '../src/services/apiV2';
import { errorMessage } from '../src/services/api';
import { useUserStore } from '../src/store/userStore';

/** The symbologies on Indian retail packaging. QR is not one of them. */
const BARCODE_TYPES = ['ean13', 'ean8', 'upc_a', 'upc_e', 'code128', 'itf14'] as const;

type Stage = 'camera' | 'looking' | 'result' | 'label';

export default function ScanProductScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [permission, requestPermission] = useCameraPermissions();
  const { userId } = useUserStore();

  const [stage, setStage] = useState<Stage>('camera');
  const [result, setResult] = useState<ScanResult | null>(null);
  const [queued, setQueued] = useState(0);
  const [labelFacts, setLabelFacts] = useState<Record<string, unknown> | null>(null);
  const [labelBusy, setLabelBusy] = useState(false);
  const [labelError, setLabelError] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState<string | null>(null);
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
    setLabelFacts(null);
    setLabelError(null);
    setConfirmed(null);
    setStage('camera');
  }, []);

  const captureLabel = useCallback(() => {
    if (!userId) {
      // Reading a label costs a model call, so it is attached to an account.
      // Scanning itself never is.
      router.push('/(auth)/welcome');
      return;
    }
    setStage('label');
    setLabelFacts(null);
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
      const read = await transcribeProductLabel(result.barcode, asset.id);
      setLabelFacts(read.facts);
    } catch (err) {
      setLabelError(errorMessage(err, 'We could not read that photo. Try again with more light.'));
    } finally {
      setLabelBusy(false);
    }
  }, [labelBusy, result]);

  /**
   * The VC-07 confirm: the person says it is right, and only then it counts.
   *
   * The record is read back afterwards so they see the confidence their
   * confirmation actually produced, rather than being told it worked.
   */
  const acceptLabel = useCallback(async () => {
    if (!result || !labelFacts) return;
    setLabelBusy(true);
    try {
      const saved = await confirmLabel(result.barcode, labelFacts);
      setConfirmed(saved
        ? `Saved. ${saved.confidence.text}`
        : 'Saved on this phone. It will sync when you are back online.');
      setLabelFacts(null);
      setResult(await scanBarcode(result.barcode));
      setStage('result');
    } catch (err) {
      setLabelError(errorMessage(err, 'We could not save that just now. Try again in a moment.'));
    } finally {
      setLabelBusy(false);
    }
  }, [labelFacts, result]);

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
      {stage === 'label' && !labelFacts && Platform.OS !== 'web' && permission?.granted && (
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

      {stage === 'label' && labelFacts && (
        <LabelReview
          facts={labelFacts}
          busy={labelBusy}
          onConfirm={acceptLabel}
          onRetake={() => { setLabelFacts(null); setLabelError(null); }}
        />
      )}

      {stage === 'label' && !labelFacts && (
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
