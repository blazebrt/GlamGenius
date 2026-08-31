import React, { useCallback, useEffect, useState } from 'react';
import { CameraView, useCameraPermissions } from 'expo-camera';
import { useRouter } from 'expo-router';
import { Platform, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { ensureDevice, scanBarcode, type ScanResult } from '../src/services/productScan';
import { ProductResult, NotFoundResult } from '../src/components/scan/ScanPieces';
import { COLORS, FONTS, SPACING } from '../src/theme/colors';

export default function ScanProductScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [permission, requestPermission] = useCameraPermissions();
  const [result, setResult] = useState<ScanResult | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { void ensureDevice().catch(() => undefined); }, []);
  const onBarcode = useCallback(async ({ data }: { data: string }) => {
    if (busy || !data) return;
    setBusy(true);
    try { setResult(await scanBarcode(data)); } finally { setBusy(false); }
  }, [busy]);
  if (!permission?.granted && permission?.canAskAgain) {
    void requestPermission();
  }
  if (result) {
    return <View style={[styles.result, { paddingTop: insets.top + SPACING.lg }]}>
      {result.found ? <ProductResult result={result} onCaptureLabel={() => router.push('/(auth)/welcome')} onScanAgain={() => setResult(null)} /> : <NotFoundResult result={result} onCaptureLabel={() => router.push('/(auth)/welcome')} onScanAgain={() => setResult(null)} />}
      <TouchableOpacity accessibilityRole="button" accessibilityLabel="Scan another product" onPress={() => setResult(null)} style={styles.button}><Text style={styles.buttonText}>Scan another product</Text></TouchableOpacity>
    </View>;
  }
  return <View style={styles.camera}>
    {Platform.OS !== 'web' && permission?.granted ? <CameraView style={StyleSheet.absoluteFill} facing="back" onBarcodeScanned={onBarcode} barcodeScannerSettings={{ barcodeTypes: ['ean13', 'ean8', 'upc_a', 'upc_e', 'code128', 'itf14'] }} /> : <View style={[StyleSheet.absoluteFill, styles.fallback]} />}
    <View style={[styles.overlay, { paddingTop: insets.top + SPACING.md, paddingBottom: insets.bottom + SPACING.lg }]}>
      <View style={styles.header}><Text style={styles.brand}>GlamGenius</Text><TouchableOpacity accessibilityRole="button" accessibilityLabel="Open account" onPress={() => router.push('/(tabs)/you')}><Text style={styles.link}>Account</Text></TouchableOpacity></View>
      <View style={styles.reticle} />
      <View><Text style={styles.title}>Scan a product</Text><Text style={styles.body}>Point at a barcode to see the facts, evidence and a clearer next step.</Text></View>
    </View>
  </View>;
}
const styles = StyleSheet.create({
  camera: { flex: 1, backgroundColor: '#000' }, fallback: { backgroundColor: '#111' },
  overlay: { flex: 1, justifyContent: 'space-between', paddingHorizontal: SPACING.lg },
  header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  brand: { fontFamily: FONTS.family.heading, fontSize: 20, color: COLORS.white }, link: { color: COLORS.white, fontFamily: FONTS.family.bodySemibold },
  reticle: { alignSelf: 'center', width: '78%', aspectRatio: 1.6, borderWidth: 2, borderColor: 'rgba(255,255,255,0.85)', borderRadius: 16 },
  title: { fontFamily: FONTS.family.heading, fontSize: 26, color: COLORS.white }, body: { marginTop: 6, color: 'rgba(255,255,255,0.82)', fontFamily: FONTS.family.body, fontSize: 14 },
  result: { flex: 1, backgroundColor: COLORS.background, paddingHorizontal: SPACING.lg }, button: { marginTop: SPACING.md, padding: SPACING.md, backgroundColor: COLORS.primary, borderRadius: 12, alignItems: 'center' }, buttonText: { color: COLORS.white, fontFamily: FONTS.family.bodySemibold },
});
