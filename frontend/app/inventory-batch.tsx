/**
 * Photograph a shelf, keep or drop each thing on it.
 *
 * Capture is the gate on everything else: no verdict about a product is worth
 * anything if we do not know what someone owns, and typing fifteen care
 * products in by hand is why nobody does. One photo, then one tap per thing.
 *
 * Two decisions make the three-minute target reachable:
 *
 * 1. **The tap is optimistic.** The row leaves the list immediately and the
 *    request goes in the background. A slow network costs waiting, not taps.
 * 2. **Nothing is on the shelf until it is tapped.** The server holds
 *    candidates, not items, so abandoning this screen halfway leaves the
 *    inventory exactly as it was.
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
import { errorMessage } from '../src/services/api';
import {
  decideImportCandidates,
  extractInventoryBatch,
  uploadMedia,
  type ImportCandidate,
  type InventoryImport,
} from '../src/services/apiV2';
import {
  CandidateRow,
  CaptureDone,
  CaptureProgress,
  EmptyCapture,
} from '../src/components/inventory/ShelfCapturePieces';

type Stage = 'camera' | 'reading' | 'review' | 'done';

export default function InventoryBatchScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [permission, requestPermission] = useCameraPermissions();
  const cameraRef = useRef<CameraView | null>(null);

  const [stage, setStage] = useState<Stage>('camera');
  const [capture, setCapture] = useState<InventoryImport | null>(null);
  const [pending, setPending] = useState<ImportCandidate[]>([]);
  const [kept, setKept] = useState(0);
  const [dropped, setDropped] = useState(0);
  const [error, setError] = useState<string | null>(null);

  // Taps are recorded here and flushed in the background, so a slow network
  // never sits between two taps.
  const outbox = useRef<{ candidate_id: string; accept: boolean }[]>([]);
  const flushing = useRef(false);
  const jobId = useRef<string | null>(null);

  useEffect(() => {
    if (permission && !permission.granted && permission.canAskAgain) void requestPermission();
  }, [permission, requestPermission]);

  const flush = useCallback(async () => {
    if (flushing.current || !jobId.current || outbox.current.length === 0) return;
    flushing.current = true;
    const sending = outbox.current;
    outbox.current = [];
    try {
      await decideImportCandidates(jobId.current, sending);
    } catch {
      // Put them back; the next tap or the finish button tries again.
      outbox.current = [...sending, ...outbox.current];
    } finally {
      flushing.current = false;
    }
  }, []);

  const takePhoto = useCallback(async () => {
    setError(null);
    setStage('reading');
    try {
      const photo = await cameraRef.current?.takePictureAsync({ quality: 0.7, skipProcessing: true });
      if (!photo?.uri) throw new Error('no photo');
      const asset = await uploadMedia({ uri: photo.uri, name: 'shelf.jpg', type: 'image/jpeg' }, 'inventory_item');
      const found = await extractInventoryBatch(asset.id);
      jobId.current = found.job_id;
      setCapture(found);
      setPending(found.candidates.filter((row) => row.state === 'pending'));
      setKept(0);
      setDropped(0);
      setStage('review');
    } catch (err) {
      setError(errorMessage(err, 'We could not read that photo. Try again with more light.'));
      setStage('camera');
    }
  }, []);

  const decide = useCallback((candidate: ImportCandidate, accept: boolean) => {
    // Optimistic: the row goes now, the request catches up.
    setPending((rows) => rows.filter((row) => row.id !== candidate.id));
    if (accept) setKept((n) => n + 1);
    else setDropped((n) => n + 1);
    outbox.current.push({ candidate_id: candidate.id, accept });
    void flush();
  }, [flush]);

  useEffect(() => {
    if (stage !== 'review' || pending.length > 0) return;
    // Everything decided. Make sure the last taps landed before saying so.
    void flush().then(() => setStage('done'));
  }, [flush, pending.length, stage]);

  const startOver = useCallback(() => {
    jobId.current = null;
    outbox.current = [];
    setCapture(null);
    setPending([]);
    setStage('camera');
  }, []);

  if (permission && !permission.granted && !permission.canAskAgain) {
    return (
      <View style={[styles.container, { paddingTop: insets.top + SPACING.lg }]}>
        <Text style={styles.title}>The camera is switched off</Text>
        <Text style={styles.body}>
          Photographing a shelf needs the camera. Turn it on for GlamGenius in your phone&apos;s settings.
        </Text>
        <TouchableOpacity
          accessibilityRole="button"
          accessibilityLabel="Add an item by hand instead"
          onPress={() => router.replace('/inventory-add')}
          style={styles.linkButton}
        >
          <Text style={styles.linkText}>Add one by hand instead</Text>
        </TouchableOpacity>
      </View>
    );
  }

  if (stage === 'camera' || stage === 'reading') {
    return (
      <View style={styles.camera}>
        {Platform.OS !== 'web' && permission?.granted ? (
          <CameraView ref={cameraRef} style={StyleSheet.absoluteFill} facing="back" testID="shelf-camera" />
        ) : (
          <View style={[StyleSheet.absoluteFill, styles.cameraFallback]} />
        )}
        <View style={[styles.overlay, { paddingTop: insets.top + SPACING.md, paddingBottom: insets.bottom + SPACING.lg }]}>
          <TouchableOpacity
            accessibilityRole="button"
            accessibilityLabel="Close shelf capture"
            onPress={() => router.back()}
            style={styles.close}
          >
            <Ionicons name="close" size={22} color={COLORS.white} />
          </TouchableOpacity>

          <View style={styles.bottom}>
            <Text style={styles.overlayTitle}>Photograph one shelf</Text>
            <Text style={styles.overlayBody}>
              Get the labels facing you and fit one shelf in the frame. We will list what we can read, and
              you keep or drop each one.
            </Text>
            {!!error && <Text style={styles.overlayError}>{error}</Text>}
            <TouchableOpacity
              accessibilityRole="button"
              accessibilityLabel="Take the shelf photo"
              onPress={takePhoto}
              disabled={stage === 'reading'}
              style={styles.shutter}
            >
              {stage === 'reading' ? (
                <ActivityIndicator color={COLORS.white} />
              ) : (
                <Text style={styles.shutterText}>Take the photo</Text>
              )}
            </TouchableOpacity>
          </View>
        </View>
      </View>
    );
  }

  return (
    <ScrollView
      style={styles.container}
      contentContainerStyle={{
        paddingTop: insets.top + SPACING.md,
        paddingBottom: insets.bottom + SPACING.xl,
        gap: SPACING.sm,
      }}
    >
      {stage === 'review' && capture && (
        <>
          <CaptureProgress
            decided={kept + dropped}
            total={capture.detected_count}
            unreadable={capture.unreadable_count}
          />
          {pending.map((candidate) => (
            <CandidateRow
              key={candidate.id}
              candidate={candidate}
              onKeep={() => decide(candidate, true)}
              onDrop={() => decide(candidate, false)}
            />
          ))}
        </>
      )}

      {stage === 'done' && (capture?.detected_count ? (
        <CaptureDone
          kept={kept}
          dropped={dropped}
          onScanAnother={startOver}
          onOpenInventory={() => router.replace('/(tabs)/care')}
        />
      ) : (
        <EmptyCapture onRetake={startOver} />
      ))}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.background, paddingHorizontal: SPACING.lg },
  camera: { flex: 1, backgroundColor: '#000' },
  cameraFallback: { backgroundColor: '#111' },
  overlay: { flex: 1, justifyContent: 'space-between', paddingHorizontal: SPACING.lg },
  close: {
    width: 40, height: 40, borderRadius: 20, alignItems: 'center', justifyContent: 'center',
    backgroundColor: 'rgba(255,255,255,0.16)',
  },
  bottom: { gap: 8 },
  overlayTitle: { fontFamily: FONTS.family.heading, fontSize: 26, color: COLORS.white },
  overlayBody: { fontFamily: FONTS.family.body, fontSize: 14, lineHeight: 20, color: 'rgba(255,255,255,0.82)' },
  overlayError: { fontFamily: FONTS.family.body, fontSize: 13, color: COLORS.errorMuted },
  shutter: {
    backgroundColor: COLORS.primary, borderRadius: RADIUS.lg, paddingVertical: 16,
    alignItems: 'center', marginTop: SPACING.sm,
  },
  shutterText: { fontFamily: FONTS.family.bodySemibold, fontSize: 16, color: COLORS.white },
  title: { fontFamily: FONTS.family.heading, fontSize: 22, color: COLORS.textPrimary },
  body: { fontFamily: FONTS.family.body, fontSize: 14, lineHeight: 21, color: COLORS.textSecondary },
  linkButton: { paddingVertical: 12, alignItems: 'center' },
  linkText: { fontFamily: FONTS.family.bodySemibold, fontSize: 14, color: COLORS.primary },
});
