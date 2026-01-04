import React, { useState, useRef, useEffect } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ActivityIndicator,
  Alert,
  Platform,
} from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { CameraView, CameraType, useCameraPermissions } from 'expo-camera';
import * as ImagePicker from 'expo-image-picker';
import Animated, { FadeIn, FadeInDown } from 'react-native-reanimated';
import { api } from '../src/services/api';
import { useUserStore } from '../src/store/userStore';

type ScanType = 'face' | 'hair' | 'scalp' | 'full';

export default function ScanScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { userId, fetchUser } = useUserStore();
  const [permission, requestPermission] = useCameraPermissions();
  const [facing, setFacing] = useState<CameraType>('front');
  const [scanType, setScanType] = useState<ScanType>('full');
  const [analyzing, setAnalyzing] = useState(false);
  const [capturedImage, setCapturedImage] = useState<string | null>(null);
  const [analysisResult, setAnalysisResult] = useState<any>(null);
  const cameraRef = useRef<CameraView>(null);

  const scanTypes = [
    { id: 'face', label: 'Face', icon: 'happy-outline' },
    { id: 'hair', label: 'Hair', icon: 'leaf-outline' },
    { id: 'full', label: 'Full', icon: 'body-outline' },
  ];

  const handleCapture = async () => {
    if (!cameraRef.current) return;

    try {
      const photo = await cameraRef.current.takePictureAsync({
        base64: true,
        quality: 0.7,
      });

      if (photo?.base64) {
        setCapturedImage(photo.base64);
        analyzeImage(photo.base64);
      }
    } catch (error) {
      console.error('Error capturing photo:', error);
      Alert.alert('Error', 'Failed to capture photo. Please try again.');
    }
  };

  const handlePickImage = async () => {
    try {
      const result = await ImagePicker.launchImageLibraryAsync({
        mediaTypes: ImagePicker.MediaTypeOptions.Images,
        allowsEditing: true,
        aspect: [1, 1],
        quality: 0.7,
        base64: true,
      });

      if (!result.canceled && result.assets[0].base64) {
        setCapturedImage(result.assets[0].base64);
        analyzeImage(result.assets[0].base64);
      }
    } catch (error) {
      console.error('Error picking image:', error);
      Alert.alert('Error', 'Failed to pick image. Please try again.');
    }
  };

  const analyzeImage = async (imageBase64: string) => {
    setAnalyzing(true);
    try {
      const response = await api.post('/scan/analyze', {
        user_id: userId,
        image_base64: imageBase64,
        scan_type: scanType,
      });

      setAnalysisResult(response.data.analysis);
      await fetchUser(); // Refresh user profile with updated data
    } catch (error) {
      console.error('Error analyzing image:', error);
      Alert.alert('Error', 'Failed to analyze image. Please try again.');
    } finally {
      setAnalyzing(false);
    }
  };

  const handleRetake = () => {
    setCapturedImage(null);
    setAnalysisResult(null);
  };

  const handleViewRecommendations = () => {
    router.push({
      pathname: '/quiz',
      params: { fromScan: 'true' },
    });
  };

  if (!permission) {
    return (
      <View style={[styles.container, { paddingTop: insets.top }]}>
        <ActivityIndicator size="large" color="#D4AF37" />
      </View>
    );
  }

  if (!permission.granted) {
    return (
      <View style={[styles.container, { paddingTop: insets.top }]}>
        <View style={styles.permissionContainer}>
          <Ionicons name="camera-outline" size={64} color="#D4AF37" />
          <Text style={styles.permissionTitle}>Camera Access Required</Text>
          <Text style={styles.permissionText}>
            We need camera access to analyze your face, hair, and skin for personalized recommendations.
          </Text>
          <TouchableOpacity style={styles.permissionButton} onPress={requestPermission}>
            <Text style={styles.permissionButtonText}>Grant Permission</Text>
          </TouchableOpacity>
          <TouchableOpacity style={styles.galleryButton} onPress={handlePickImage}>
            <Text style={styles.galleryButtonText}>Or choose from gallery</Text>
          </TouchableOpacity>
        </View>
      </View>
    );
  }

  // Results View
  if (analysisResult) {
    return (
      <View style={[styles.container, { paddingTop: insets.top }]}>
        <View style={styles.header}>
          <TouchableOpacity onPress={() => router.back()} style={styles.backButton}>
            <Ionicons name="arrow-back" size={24} color="#FFFFFF" />
          </TouchableOpacity>
          <Text style={styles.headerTitle}>Analysis Results</Text>
          <View style={{ width: 40 }} />
        </View>

        <Animated.ScrollView
          entering={FadeIn}
          showsVerticalScrollIndicator={false}
          contentContainerStyle={styles.resultsContent}
        >
          {/* Face Shape */}
          {analysisResult.face_shape && (
            <Animated.View entering={FadeInDown.delay(100)} style={styles.resultCard}>
              <View style={styles.resultIconContainer}>
                <Ionicons name="happy" size={24} color="#D4AF37" />
              </View>
              <View style={styles.resultInfo}>
                <Text style={styles.resultLabel}>Face Shape</Text>
                <Text style={styles.resultValue}>{analysisResult.face_shape}</Text>
              </View>
            </Animated.View>
          )}

          {/* Skin Type */}
          {analysisResult.skin_type && (
            <Animated.View entering={FadeInDown.delay(200)} style={styles.resultCard}>
              <View style={styles.resultIconContainer}>
                <Ionicons name="water" size={24} color="#D4AF37" />
              </View>
              <View style={styles.resultInfo}>
                <Text style={styles.resultLabel}>Skin Type</Text>
                <Text style={styles.resultValue}>{analysisResult.skin_type}</Text>
              </View>
            </Animated.View>
          )}

          {/* Hair Type */}
          {analysisResult.hair_type && (
            <Animated.View entering={FadeInDown.delay(300)} style={styles.resultCard}>
              <View style={styles.resultIconContainer}>
                <Ionicons name="leaf" size={24} color="#D4AF37" />
              </View>
              <View style={styles.resultInfo}>
                <Text style={styles.resultLabel}>Hair Type</Text>
                <Text style={styles.resultValue}>{analysisResult.hair_type}</Text>
              </View>
            </Animated.View>
          )}

          {/* Concerns */}
          {(analysisResult.skin_concerns?.length > 0 || analysisResult.hair_concerns?.length > 0) && (
            <Animated.View entering={FadeInDown.delay(400)} style={styles.concernsSection}>
              <Text style={styles.sectionTitle}>Detected Concerns</Text>
              <View style={styles.concernsTags}>
                {analysisResult.skin_concerns?.map((concern: string, index: number) => (
                  <View key={`skin-${index}`} style={styles.concernTag}>
                    <Text style={styles.concernTagText}>{concern}</Text>
                  </View>
                ))}
                {analysisResult.hair_concerns?.map((concern: string, index: number) => (
                  <View key={`hair-${index}`} style={styles.concernTag}>
                    <Text style={styles.concernTagText}>{concern}</Text>
                  </View>
                ))}
              </View>
            </Animated.View>
          )}

          {/* Recommendations */}
          {(analysisResult.top_recommendations?.length > 0 || analysisResult.recommended_treatments?.length > 0) && (
            <Animated.View entering={FadeInDown.delay(500)} style={styles.recommendationsSection}>
              <Text style={styles.sectionTitle}>Recommended Treatments</Text>
              {(analysisResult.top_recommendations || analysisResult.recommended_treatments)?.map(
                (rec: string, index: number) => (
                  <View key={index} style={styles.recommendationItem}>
                    <Ionicons name="checkmark-circle" size={20} color="#D4AF37" />
                    <Text style={styles.recommendationText}>{rec}</Text>
                  </View>
                )
              )}
            </Animated.View>
          )}

          {/* Tips */}
          {analysisResult.personalized_tips?.length > 0 && (
            <Animated.View entering={FadeInDown.delay(600)} style={styles.tipsSection}>
              <Text style={styles.sectionTitle}>Personalized Tips</Text>
              {analysisResult.personalized_tips.map((tip: string, index: number) => (
                <View key={index} style={styles.tipItem}>
                  <Ionicons name="bulb" size={16} color="#D4AF37" />
                  <Text style={styles.tipText}>{tip}</Text>
                </View>
              ))}
            </Animated.View>
          )}

          {/* Actions */}
          <View style={styles.actionsContainer}>
            <TouchableOpacity style={styles.primaryButton} onPress={handleViewRecommendations}>
              <Ionicons name="sparkles" size={20} color="#0A0A0A" />
              <Text style={styles.primaryButtonText}>Get Service Recommendations</Text>
            </TouchableOpacity>

            <TouchableOpacity style={styles.secondaryButton} onPress={handleRetake}>
              <Ionicons name="camera" size={20} color="#D4AF37" />
              <Text style={styles.secondaryButtonText}>Take Another Scan</Text>
            </TouchableOpacity>
          </View>
        </Animated.ScrollView>
      </View>
    );
  }

  // Analyzing View
  if (analyzing) {
    return (
      <View style={[styles.container, { paddingTop: insets.top }]}>
        <View style={styles.analyzingContainer}>
          <View style={styles.analyzingIcon}>
            <Ionicons name="scan" size={48} color="#D4AF37" />
          </View>
          <Text style={styles.analyzingTitle}>Analyzing...</Text>
          <Text style={styles.analyzingText}>
            Our AI is examining your {scanType} to provide personalized insights
          </Text>
          <ActivityIndicator size="large" color="#D4AF37" style={{ marginTop: 24 }} />
        </View>
      </View>
    );
  }

  // Camera View
  return (
    <View style={styles.container}>
      <CameraView
        ref={cameraRef}
        style={styles.camera}
        facing={facing}
      >
        <View style={[styles.cameraOverlay, { paddingTop: insets.top }]}>
          {/* Header */}
          <View style={styles.cameraHeader}>
            <TouchableOpacity onPress={() => router.back()} style={styles.backButton}>
              <Ionicons name="close" size={28} color="#FFFFFF" />
            </TouchableOpacity>
            <Text style={styles.cameraTitle}>AI Beauty Scan</Text>
            <TouchableOpacity
              onPress={() => setFacing(facing === 'front' ? 'back' : 'front')}
              style={styles.flipButton}
            >
              <Ionicons name="camera-reverse" size={24} color="#FFFFFF" />
            </TouchableOpacity>
          </View>

          {/* Scan Type Selector */}
          <View style={styles.scanTypeContainer}>
            {scanTypes.map((type) => (
              <TouchableOpacity
                key={type.id}
                style={[
                  styles.scanTypeButton,
                  scanType === type.id && styles.scanTypeButtonActive,
                ]}
                onPress={() => setScanType(type.id as ScanType)}
              >
                <Ionicons
                  name={type.icon as any}
                  size={18}
                  color={scanType === type.id ? '#0A0A0A' : '#FFFFFF'}
                />
                <Text
                  style={[
                    styles.scanTypeText,
                    scanType === type.id && styles.scanTypeTextActive,
                  ]}
                >
                  {type.label}
                </Text>
              </TouchableOpacity>
            ))}
          </View>

          {/* Guide Frame */}
          <View style={styles.guideContainer}>
            <View style={styles.guideFrame}>
              <View style={[styles.guideCorner, styles.topLeft]} />
              <View style={[styles.guideCorner, styles.topRight]} />
              <View style={[styles.guideCorner, styles.bottomLeft]} />
              <View style={[styles.guideCorner, styles.bottomRight]} />
            </View>
            <Text style={styles.guideText}>
              Position your {scanType === 'face' ? 'face' : scanType === 'hair' ? 'hair' : 'face and hair'} within the frame
            </Text>
          </View>

          {/* Controls */}
          <View style={[styles.controlsContainer, { paddingBottom: insets.bottom + 20 }]}>
            <TouchableOpacity style={styles.galleryIconButton} onPress={handlePickImage}>
              <Ionicons name="images" size={28} color="#FFFFFF" />
            </TouchableOpacity>

            <TouchableOpacity style={styles.captureButton} onPress={handleCapture}>
              <View style={styles.captureButtonInner} />
            </TouchableOpacity>

            <View style={{ width: 50 }} />
          </View>
        </View>
      </CameraView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#0A0A0A',
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingVertical: 12,
  },
  backButton: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: 'rgba(255, 255, 255, 0.1)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  headerTitle: {
    fontSize: 18,
    fontWeight: '600',
    color: '#FFFFFF',
  },
  permissionContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    paddingHorizontal: 40,
  },
  permissionTitle: {
    fontSize: 22,
    fontWeight: '600',
    color: '#FFFFFF',
    marginTop: 20,
    textAlign: 'center',
  },
  permissionText: {
    fontSize: 14,
    color: 'rgba(255, 255, 255, 0.6)',
    textAlign: 'center',
    marginTop: 12,
    lineHeight: 22,
  },
  permissionButton: {
    backgroundColor: '#D4AF37',
    paddingVertical: 14,
    paddingHorizontal: 32,
    borderRadius: 25,
    marginTop: 32,
  },
  permissionButtonText: {
    fontSize: 16,
    fontWeight: '600',
    color: '#0A0A0A',
  },
  galleryButton: {
    marginTop: 16,
  },
  galleryButtonText: {
    fontSize: 14,
    color: '#D4AF37',
  },
  camera: {
    flex: 1,
  },
  cameraOverlay: {
    flex: 1,
    backgroundColor: 'transparent',
  },
  cameraHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingVertical: 12,
  },
  cameraTitle: {
    fontSize: 18,
    fontWeight: '600',
    color: '#FFFFFF',
  },
  flipButton: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: 'rgba(0, 0, 0, 0.3)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  scanTypeContainer: {
    flexDirection: 'row',
    justifyContent: 'center',
    gap: 12,
    marginTop: 8,
  },
  scanTypeButton: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 8,
    paddingHorizontal: 16,
    borderRadius: 20,
    backgroundColor: 'rgba(0, 0, 0, 0.4)',
    gap: 6,
  },
  scanTypeButtonActive: {
    backgroundColor: '#D4AF37',
  },
  scanTypeText: {
    fontSize: 14,
    fontWeight: '500',
    color: '#FFFFFF',
  },
  scanTypeTextActive: {
    color: '#0A0A0A',
  },
  guideContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
  },
  guideFrame: {
    width: 280,
    height: 350,
    borderRadius: 20,
    position: 'relative',
  },
  guideCorner: {
    position: 'absolute',
    width: 40,
    height: 40,
    borderColor: '#D4AF37',
    borderWidth: 3,
  },
  topLeft: {
    top: 0,
    left: 0,
    borderBottomWidth: 0,
    borderRightWidth: 0,
    borderTopLeftRadius: 20,
  },
  topRight: {
    top: 0,
    right: 0,
    borderBottomWidth: 0,
    borderLeftWidth: 0,
    borderTopRightRadius: 20,
  },
  bottomLeft: {
    bottom: 0,
    left: 0,
    borderTopWidth: 0,
    borderRightWidth: 0,
    borderBottomLeftRadius: 20,
  },
  bottomRight: {
    bottom: 0,
    right: 0,
    borderTopWidth: 0,
    borderLeftWidth: 0,
    borderBottomRightRadius: 20,
  },
  guideText: {
    fontSize: 14,
    color: '#FFFFFF',
    textAlign: 'center',
    marginTop: 20,
    paddingHorizontal: 40,
  },
  controlsContainer: {
    flexDirection: 'row',
    justifyContent: 'space-around',
    alignItems: 'center',
    paddingHorizontal: 40,
  },
  galleryIconButton: {
    width: 50,
    height: 50,
    borderRadius: 25,
    backgroundColor: 'rgba(0, 0, 0, 0.4)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  captureButton: {
    width: 80,
    height: 80,
    borderRadius: 40,
    borderWidth: 4,
    borderColor: '#D4AF37',
    justifyContent: 'center',
    alignItems: 'center',
  },
  captureButtonInner: {
    width: 64,
    height: 64,
    borderRadius: 32,
    backgroundColor: '#D4AF37',
  },
  analyzingContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    paddingHorizontal: 40,
  },
  analyzingIcon: {
    width: 100,
    height: 100,
    borderRadius: 50,
    backgroundColor: 'rgba(212, 175, 55, 0.1)',
    justifyContent: 'center',
    alignItems: 'center',
    borderWidth: 2,
    borderColor: 'rgba(212, 175, 55, 0.3)',
  },
  analyzingTitle: {
    fontSize: 24,
    fontWeight: '600',
    color: '#FFFFFF',
    marginTop: 24,
  },
  analyzingText: {
    fontSize: 14,
    color: 'rgba(255, 255, 255, 0.6)',
    textAlign: 'center',
    marginTop: 8,
    lineHeight: 22,
  },
  resultsContent: {
    paddingHorizontal: 20,
    paddingBottom: 40,
  },
  resultCard: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(255, 255, 255, 0.05)',
    borderRadius: 16,
    padding: 16,
    marginBottom: 12,
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.08)',
  },
  resultIconContainer: {
    width: 48,
    height: 48,
    borderRadius: 24,
    backgroundColor: 'rgba(212, 175, 55, 0.1)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  resultInfo: {
    marginLeft: 14,
  },
  resultLabel: {
    fontSize: 13,
    color: 'rgba(255, 255, 255, 0.5)',
  },
  resultValue: {
    fontSize: 18,
    fontWeight: '600',
    color: '#FFFFFF',
    textTransform: 'capitalize',
    marginTop: 2,
  },
  sectionTitle: {
    fontSize: 16,
    fontWeight: '600',
    color: '#FFFFFF',
    marginBottom: 12,
  },
  concernsSection: {
    marginTop: 20,
  },
  concernsTags: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  concernTag: {
    backgroundColor: 'rgba(231, 76, 60, 0.2)',
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 16,
  },
  concernTagText: {
    fontSize: 13,
    color: '#E74C3C',
    textTransform: 'capitalize',
  },
  recommendationsSection: {
    marginTop: 24,
    backgroundColor: 'rgba(212, 175, 55, 0.08)',
    borderRadius: 16,
    padding: 16,
  },
  recommendationItem: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 10,
    gap: 10,
  },
  recommendationText: {
    fontSize: 14,
    color: '#FFFFFF',
    flex: 1,
  },
  tipsSection: {
    marginTop: 24,
  },
  tipItem: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    marginBottom: 10,
    gap: 10,
  },
  tipText: {
    fontSize: 14,
    color: 'rgba(255, 255, 255, 0.7)',
    flex: 1,
    lineHeight: 20,
  },
  actionsContainer: {
    marginTop: 32,
    gap: 12,
  },
  primaryButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#D4AF37',
    paddingVertical: 16,
    borderRadius: 28,
    gap: 8,
  },
  primaryButtonText: {
    fontSize: 16,
    fontWeight: '600',
    color: '#0A0A0A',
  },
  secondaryButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: 'rgba(212, 175, 55, 0.1)',
    paddingVertical: 16,
    borderRadius: 28,
    borderWidth: 1,
    borderColor: 'rgba(212, 175, 55, 0.3)',
    gap: 8,
  },
  secondaryButtonText: {
    fontSize: 16,
    fontWeight: '600',
    color: '#D4AF37',
  },
});
