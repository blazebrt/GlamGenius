import React, { useState, useRef, useEffect } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ActivityIndicator,
  ScrollView,
  Alert,
} from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import Animated, { FadeIn, FadeInDown, useSharedValue, useAnimatedStyle, withRepeat, withTiming, Easing } from 'react-native-reanimated';
import { CameraView, useCameraPermissions } from 'expo-camera';
import * as ImagePicker from 'expo-image-picker';
import { api } from '../src/services/api';

type ScanStep = 'intro' | 'scanning' | 'analyzing' | 'results';
type ScanType = 'face' | 'hair' | 'scalp' | 'full';

export default function ScanScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [permission, requestPermission] = useCameraPermissions();
  const cameraRef = useRef<CameraView>(null);

  const [step, setStep] = useState<ScanStep>('intro');
  const [scanType, setScanType] = useState<ScanType>('face');
  const [capturedImage, setCapturedImage] = useState<string | null>(null);
  const [analysisResult, setAnalysisResult] = useState<any>(null);

  const pulseScale = useSharedValue(1);

  useEffect(() => {
    pulseScale.value = withRepeat(withTiming(1.1, { duration: 1000, easing: Easing.ease }), -1, true);
  }, []);

  const pulseStyle = useAnimatedStyle(() => ({ transform: [{ scale: pulseScale.value }] }));

  const handleTakePhoto = async () => {
    if (cameraRef.current) {
      try {
        const photo = await cameraRef.current.takePictureAsync({ base64: true, quality: 0.7 });
        if (photo?.base64) {
          setCapturedImage(photo.base64);
          setStep('analyzing');
          analyzeImage(photo.base64);
        }
      } catch (error) {
        Alert.alert('Error', 'Failed to take photo');
      }
    }
  };

  const handlePickImage = async () => {
    const result = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ImagePicker.MediaTypeOptions.Images, base64: true, quality: 0.7 });
    if (!result.canceled && result.assets[0]?.base64) {
      setCapturedImage(result.assets[0].base64);
      setStep('analyzing');
      analyzeImage(result.assets[0].base64);
    }
  };

  const analyzeImage = async (base64: string) => {
    try {
      const response = await api.post('/scan/analyze', { image_base64: base64, scan_type: scanType });
      setAnalysisResult(response.data);
      setStep('results');
    } catch (error) {
      Alert.alert('Error', 'Analysis failed. Please try again.');
      setStep('intro');
    }
  };

  const resetScan = () => {
    setStep('intro');
    setCapturedImage(null);
    setAnalysisResult(null);
  };

  const getHealthScore = (type: string) => {
    if (!analysisResult) return 0;
    if (analysisResult.health_scores) {
      if (type === 'skin' && analysisResult.health_scores.overall_skin_health) return analysisResult.health_scores.overall_skin_health;
      if (type === 'hair' && analysisResult.health_scores.overall_hair_health) return analysisResult.health_scores.overall_hair_health;
    }
    return 75;
  };

  // Permission loading
  if (!permission) {
    return <View style={[styles.container, { paddingTop: insets.top }]}><ActivityIndicator size="large" color="#0EA5E9" /></View>;
  }

  // Permission denied
  if (!permission.granted) {
    return (
      <View style={[styles.container, { paddingTop: insets.top }]}>
        <View style={styles.permissionContainer}>
          <Ionicons name="camera-outline" size={64} color="#E2E8F0" />
          <Text style={styles.permissionTitle}>Camera Access Needed</Text>
          <Text style={styles.permissionText}>Allow camera access to use AI Beauty Scan</Text>
          <TouchableOpacity style={styles.permissionBtn} onPress={requestPermission}>
            <Text style={styles.permissionBtnText}>Allow Camera</Text>
          </TouchableOpacity>
          <TouchableOpacity style={styles.galleryBtn} onPress={handlePickImage}>
            <Ionicons name="images" size={20} color="#0EA5E9" />
            <Text style={styles.galleryBtnText}>Choose from Gallery</Text>
          </TouchableOpacity>
        </View>
      </View>
    );
  }

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      {/* Header */}
      <View style={styles.header}>
        <TouchableOpacity onPress={() => step === 'intro' ? router.back() : resetScan()} style={styles.headerBtn}>
          <Ionicons name={step === 'intro' ? 'arrow-back' : 'close'} size={24} color="#1E293B" />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>AI Beauty Scan</Text>
        <View style={{ width: 40 }} />
      </View>

      {/* Intro */}
      {step === 'intro' && (
        <ScrollView contentContainerStyle={styles.introContent}>
          <Animated.View entering={FadeIn} style={styles.scanTypeSection}>
            <Text style={styles.sectionTitle}>What would you like to analyze?</Text>
            <View style={styles.scanTypes}>
              {[{ id: 'face', label: 'Face', icon: 'happy' }, { id: 'hair', label: 'Hair', icon: 'leaf' }, { id: 'scalp', label: 'Scalp', icon: 'ellipse' }, { id: 'full', label: 'Full Analysis', icon: 'body' }].map(type => (
                <TouchableOpacity key={type.id} style={[styles.scanTypeCard, scanType === type.id && styles.scanTypeCardActive]} onPress={() => setScanType(type.id as ScanType)}>
                  <Ionicons name={type.icon as any} size={24} color={scanType === type.id ? '#FFFFFF' : '#0EA5E9'} />
                  <Text style={[styles.scanTypeLabel, scanType === type.id && styles.scanTypeLabelActive]}>{type.label}</Text>
                </TouchableOpacity>
              ))}
            </View>
          </Animated.View>

          <Animated.View entering={FadeInDown.delay(100)} style={styles.cameraPreview}>
            <CameraView ref={cameraRef} style={styles.camera} facing="front">
              <View style={styles.cameraOverlay}>
                <Animated.View style={[styles.scanFrame, pulseStyle]} />
              </View>
            </CameraView>
          </Animated.View>

          <Animated.View entering={FadeInDown.delay(200)} style={styles.actionButtons}>
            <TouchableOpacity style={styles.captureBtn} onPress={handleTakePhoto}>
              <View style={styles.captureBtnInner} />
            </TouchableOpacity>
            <TouchableOpacity style={styles.galleryBtn} onPress={handlePickImage}>
              <Ionicons name="images" size={20} color="#0EA5E9" />
              <Text style={styles.galleryBtnText}>Gallery</Text>
            </TouchableOpacity>
          </Animated.View>
        </ScrollView>
      )}

      {/* Analyzing */}
      {step === 'analyzing' && (
        <View style={styles.analyzingContainer}>
          <ActivityIndicator size="large" color="#0EA5E9" />
          <Text style={styles.analyzingTitle}>Analyzing...</Text>
          <Text style={styles.analyzingText}>Our AI is examining your {scanType}</Text>
        </View>
      )}

      {/* Results */}
      {step === 'results' && analysisResult && (
        <ScrollView contentContainerStyle={styles.resultsContent}>
          <Animated.View entering={FadeIn} style={styles.scoresSection}>
            <Text style={styles.sectionTitle}>Health Scores</Text>
            <View style={styles.scoresGrid}>
              <View style={styles.scoreCard}>
                <Text style={styles.scoreValue}>{getHealthScore('skin')}%</Text>
                <Text style={styles.scoreLabel}>Skin Health</Text>
              </View>
              <View style={styles.scoreCard}>
                <Text style={styles.scoreValue}>{getHealthScore('hair')}%</Text>
                <Text style={styles.scoreLabel}>Hair Health</Text>
              </View>
            </View>
          </Animated.View>

          {analysisResult.skin_type && (
            <Animated.View entering={FadeInDown.delay(100)} style={styles.infoCard}>
              <Ionicons name="water" size={20} color="#0EA5E9" />
              <View style={styles.infoContent}>
                <Text style={styles.infoLabel}>Skin Type</Text>
                <Text style={styles.infoValue}>{analysisResult.skin_type}</Text>
              </View>
            </Animated.View>
          )}

          {analysisResult.skin_concerns?.length > 0 && (
            <Animated.View entering={FadeInDown.delay(200)} style={styles.concernsCard}>
              <Text style={styles.concernsTitle}>Concerns Detected</Text>
              {analysisResult.skin_concerns.map((concern: any, i: number) => (
                <View key={i} style={styles.concernItem}>
                  <View style={styles.concernDot} />
                  <Text style={styles.concernText}>{typeof concern === 'string' ? concern : concern.concern}</Text>
                </View>
              ))}
            </Animated.View>
          )}

          {analysisResult.recommended_treatments?.length > 0 && (
            <Animated.View entering={FadeInDown.delay(300)} style={styles.treatmentsCard}>
              <Text style={styles.sectionTitle}>Recommended Treatments</Text>
              {analysisResult.recommended_treatments.slice(0, 3).map((treatment: any, i: number) => (
                <View key={i} style={styles.treatmentItem}>
                  <Ionicons name="checkmark-circle" size={18} color="#10B981" />
                  <Text style={styles.treatmentText}>{typeof treatment === 'string' ? treatment : treatment.treatment}</Text>
                </View>
              ))}
            </Animated.View>
          )}

          <View style={styles.resultActions}>
            <TouchableOpacity style={styles.primaryBtn} onPress={() => router.push('/get-advice')}>
              <Text style={styles.primaryBtnText}>Get Personalized Package</Text>
            </TouchableOpacity>
            <TouchableOpacity style={styles.secondaryBtn} onPress={resetScan}>
              <Text style={styles.secondaryBtnText}>Scan Again</Text>
            </TouchableOpacity>
          </View>
        </ScrollView>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#F8FAFC' },
  header: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 16, paddingVertical: 12 },
  headerBtn: { width: 40, height: 40, borderRadius: 20, backgroundColor: '#FFFFFF', justifyContent: 'center', alignItems: 'center', borderWidth: 1, borderColor: '#E2E8F0' },
  headerTitle: { fontSize: 18, fontWeight: '600', color: '#1E293B' },
  permissionContainer: { flex: 1, justifyContent: 'center', alignItems: 'center', padding: 40 },
  permissionTitle: { fontSize: 20, fontWeight: '600', color: '#1E293B', marginTop: 16 },
  permissionText: { fontSize: 14, color: '#64748B', marginTop: 8, textAlign: 'center' },
  permissionBtn: { backgroundColor: '#0EA5E9', paddingVertical: 14, paddingHorizontal: 32, borderRadius: 24, marginTop: 24 },
  permissionBtnText: { fontSize: 16, fontWeight: '600', color: '#FFFFFF' },
  introContent: { padding: 20, paddingBottom: 40 },
  sectionTitle: { fontSize: 16, fontWeight: '600', color: '#1E293B', marginBottom: 14 },
  scanTypeSection: { marginBottom: 20 },
  scanTypes: { flexDirection: 'row', flexWrap: 'wrap', gap: 10 },
  scanTypeCard: { width: '47%', backgroundColor: '#FFFFFF', borderRadius: 14, padding: 16, alignItems: 'center', borderWidth: 1, borderColor: '#E2E8F0' },
  scanTypeCardActive: { backgroundColor: '#0EA5E9', borderColor: '#0EA5E9' },
  scanTypeLabel: { fontSize: 14, fontWeight: '500', color: '#1E293B', marginTop: 8 },
  scanTypeLabelActive: { color: '#FFFFFF' },
  cameraPreview: { borderRadius: 20, overflow: 'hidden', marginBottom: 20 },
  camera: { height: 350, backgroundColor: '#000' },
  cameraOverlay: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  scanFrame: { width: 220, height: 280, borderWidth: 3, borderColor: '#0EA5E9', borderRadius: 20 },
  actionButtons: { alignItems: 'center', gap: 16 },
  captureBtn: { width: 72, height: 72, borderRadius: 36, backgroundColor: '#FFFFFF', justifyContent: 'center', alignItems: 'center', borderWidth: 4, borderColor: '#0EA5E9' },
  captureBtnInner: { width: 56, height: 56, borderRadius: 28, backgroundColor: '#0EA5E9' },
  galleryBtn: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 12, paddingHorizontal: 20 },
  galleryBtnText: { fontSize: 14, fontWeight: '500', color: '#0EA5E9' },
  analyzingContainer: { flex: 1, justifyContent: 'center', alignItems: 'center', gap: 16 },
  analyzingTitle: { fontSize: 20, fontWeight: '600', color: '#1E293B' },
  analyzingText: { fontSize: 14, color: '#64748B' },
  resultsContent: { padding: 20, paddingBottom: 40 },
  scoresSection: { marginBottom: 20 },
  scoresGrid: { flexDirection: 'row', gap: 12 },
  scoreCard: { flex: 1, backgroundColor: '#E0F2FE', borderRadius: 16, padding: 20, alignItems: 'center' },
  scoreValue: { fontSize: 32, fontWeight: '700', color: '#0EA5E9' },
  scoreLabel: { fontSize: 13, color: '#0284C7', marginTop: 4 },
  infoCard: { flexDirection: 'row', alignItems: 'center', backgroundColor: '#FFFFFF', borderRadius: 14, padding: 16, marginBottom: 12, gap: 12, borderWidth: 1, borderColor: '#E2E8F0' },
  infoContent: { flex: 1 },
  infoLabel: { fontSize: 12, color: '#64748B' },
  infoValue: { fontSize: 15, fontWeight: '600', color: '#1E293B' },
  concernsCard: { backgroundColor: '#FEF2F2', borderRadius: 16, padding: 18, marginBottom: 12 },
  concernsTitle: { fontSize: 14, fontWeight: '600', color: '#991B1B', marginBottom: 12 },
  concernItem: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 8 },
  concernDot: { width: 6, height: 6, borderRadius: 3, backgroundColor: '#EF4444' },
  concernText: { fontSize: 13, color: '#B91C1C' },
  treatmentsCard: { backgroundColor: '#FFFFFF', borderRadius: 16, padding: 18, marginBottom: 20, borderWidth: 1, borderColor: '#E2E8F0' },
  treatmentItem: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 10 },
  treatmentText: { flex: 1, fontSize: 14, color: '#1E293B' },
  resultActions: { gap: 12 },
  primaryBtn: { backgroundColor: '#0EA5E9', paddingVertical: 16, borderRadius: 28, alignItems: 'center' },
  primaryBtnText: { fontSize: 16, fontWeight: '600', color: '#FFFFFF' },
  secondaryBtn: { backgroundColor: '#FFFFFF', paddingVertical: 14, borderRadius: 28, alignItems: 'center', borderWidth: 1, borderColor: '#E2E8F0' },
  secondaryBtnText: { fontSize: 14, fontWeight: '500', color: '#64748B' },
});
