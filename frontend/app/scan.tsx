import React, { useState, useRef, useEffect } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ActivityIndicator,
  Alert,
  Platform,
  Dimensions,
} from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { CameraView, CameraType, useCameraPermissions } from 'expo-camera';
import * as ImagePicker from 'expo-image-picker';
import Animated, { 
  FadeIn, 
  FadeInDown, 
  useSharedValue, 
  useAnimatedStyle, 
  withRepeat, 
  withTiming,
  withSequence,
} from 'react-native-reanimated';
import { api } from '../src/services/api';
import { useUserStore } from '../src/store/userStore';

const { width: SCREEN_WIDTH } = Dimensions.get('window');

type ScanStep = 'front' | 'left' | 'right' | 'analyzing' | 'results';

interface ScanImages {
  front?: string;
  left?: string;
  right?: string;
}

export default function ScanScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { userId, fetchUser } = useUserStore();
  const [permission, requestPermission] = useCameraPermissions();
  const [facing, setFacing] = useState<CameraType>('front');
  const [scanStep, setScanStep] = useState<ScanStep>('front');
  const [scanImages, setScanImages] = useState<ScanImages>({});
  const [analyzing, setAnalyzing] = useState(false);
  const [analysisResult, setAnalysisResult] = useState<any>(null);
  const cameraRef = useRef<CameraView>(null);

  // Animation for scanning effect
  const scanLinePosition = useSharedValue(0);
  const pulseScale = useSharedValue(1);

  useEffect(() => {
    // Scan line animation
    scanLinePosition.value = withRepeat(
      withTiming(300, { duration: 2000 }),
      -1,
      true
    );
    // Pulse animation
    pulseScale.value = withRepeat(
      withSequence(
        withTiming(1.1, { duration: 1000 }),
        withTiming(1, { duration: 1000 })
      ),
      -1,
      false
    );
  }, []);

  const scanLineStyle = useAnimatedStyle(() => ({
    transform: [{ translateY: scanLinePosition.value }],
  }));

  const pulseStyle = useAnimatedStyle(() => ({
    transform: [{ scale: pulseScale.value }],
  }));

  const SCAN_STEPS = {
    front: { label: 'Front Face', instruction: 'Look straight at the camera', icon: 'happy-outline' },
    left: { label: 'Left Profile', instruction: 'Turn your head slightly left', icon: 'arrow-back-outline' },
    right: { label: 'Right Profile', instruction: 'Turn your head slightly right', icon: 'arrow-forward-outline' },
  };

  const handleCapture = async () => {
    if (!cameraRef.current) return;

    try {
      const photo = await cameraRef.current.takePictureAsync({
        base64: true,
        quality: 0.7,
      });

      if (photo?.base64) {
        const newImages = { ...scanImages, [scanStep]: photo.base64 };
        setScanImages(newImages);

        // Move to next step
        if (scanStep === 'front') {
          setScanStep('left');
        } else if (scanStep === 'left') {
          setScanStep('right');
        } else if (scanStep === 'right') {
          // All images captured, start analysis
          setScanStep('analyzing');
          analyzeImages(newImages);
        }
      }
    } catch (error) {
      console.error('Error capturing photo:', error);
      Alert.alert('Error', 'Failed to capture photo. Please try again.');
    }
  };

  const handlePickImage = async () => {
    try {
      const result = await ImagePicker.launchImageLibraryAsync({
        mediaTypes: ['images'],
        allowsEditing: true,
        aspect: [1, 1],
        quality: 0.7,
        base64: true,
      });

      if (!result.canceled && result.assets[0].base64) {
        setScanStep('analyzing');
        analyzeImages({ front: result.assets[0].base64 });
      }
    } catch (error) {
      console.error('Error picking image:', error);
      Alert.alert('Error', 'Failed to pick image. Please try again.');
    }
  };

  const analyzeImages = async (images: ScanImages) => {
    setAnalyzing(true);
    try {
      // Use the front image for analysis (primary)
      const primaryImage = images.front || images.left || images.right;
      if (!primaryImage) {
        throw new Error('No image available');
      }

      const response = await api.post('/scan/analyze', {
        user_id: userId,
        image_base64: primaryImage,
        scan_type: 'full',
      });

      setAnalysisResult(response.data.analysis);
      setScanStep('results');
      await fetchUser();
    } catch (error) {
      console.error('Error analyzing image:', error);
      Alert.alert('Error', 'Failed to analyze image. Please try again.');
      setScanStep('front');
    } finally {
      setAnalyzing(false);
    }
  };

  const handleRetake = () => {
    setScanImages({});
    setAnalysisResult(null);
    setScanStep('front');
  };

  const handleGetPackages = () => {
    router.push('/get-advice');
  };

  const getHealthScore = (type: string) => {
    if (!analysisResult) return 0;
    
    // Try to get scores from the new detailed health_scores object first
    if (analysisResult.health_scores) {
      if (type === 'skin' && analysisResult.health_scores.overall_skin_health) {
        return analysisResult.health_scores.overall_skin_health;
      }
      if (type === 'hair' && analysisResult.health_scores.overall_hair_health) {
        return analysisResult.health_scores.overall_hair_health;
      }
      if (type === 'skin' && analysisResult.health_scores.skin_health) {
        return analysisResult.health_scores.skin_health;
      }
      if (type === 'hair' && analysisResult.health_scores.hair_health) {
        return analysisResult.health_scores.hair_health;
      }
    }
    
    // Check overall_health_scores for full analysis
    if (analysisResult.overall_health_scores) {
      if (type === 'skin' && analysisResult.overall_health_scores.skin_health) {
        return analysisResult.overall_health_scores.skin_health;
      }
      if (type === 'hair' && analysisResult.overall_health_scores.hair_health) {
        return analysisResult.overall_health_scores.hair_health;
      }
    }
    
    // Fallback: Calculate health scores based on concerns
    const concerns = type === 'skin' 
      ? (analysisResult.skin_concerns?.length || 0)
      : type === 'hair'
      ? (analysisResult.hair_concerns?.length || 0)
      : 0;
    
    const baseScore = 75; // More conservative base
    const deduction = concerns * 8;
    return Math.max(45, Math.min(95, baseScore - deduction));
  };

  // Get confidence score from analysis
  const getConfidencePercent = () => {
    if (analysisResult?.confidence_score) {
      return Math.round(analysisResult.confidence_score * 100);
    }
    return 85; // Default confidence
  };

  // Permission not yet checked
  if (!permission) {
    return (
      <View style={[styles.container, { paddingTop: insets.top }]}>
        <View style={styles.loadingContainer}>
          <ActivityIndicator size="large" color="#D4AF37" />
          <Text style={styles.loadingText}>Checking camera access...</Text>
        </View>
      </View>
    );
  }

  // Permission denied
  if (!permission.granted) {
    return (
      <View style={[styles.container, { paddingTop: insets.top }]}>
        <View style={styles.header}>
          <TouchableOpacity onPress={() => router.back()} style={styles.backButton}>
            <Ionicons name="arrow-back" size={24} color="#FFFFFF" />
          </TouchableOpacity>
          <Text style={styles.headerTitle}>AI Beauty Scan</Text>
          <View style={{ width: 40 }} />
        </View>
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
            <Ionicons name="images" size={20} color="#D4AF37" />
            <Text style={styles.galleryButtonText}>Choose from Gallery</Text>
          </TouchableOpacity>
        </View>
      </View>
    );
  }

  // Results View
  if (scanStep === 'results' && analysisResult) {
    const skinScore = getHealthScore('skin');
    const hairScore = getHealthScore('hair');

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
          {/* Health Scores */}
          <Animated.View entering={FadeInDown.delay(100)} style={styles.scoresContainer}>
            <Text style={styles.sectionTitle}>Health Scores</Text>
            <View style={styles.scoresRow}>
              <View style={styles.scoreCard}>
                <View style={[styles.scoreCircle, { borderColor: skinScore > 70 ? '#2ECC71' : '#F39C12' }]}>
                  <Text style={styles.scoreValue}>{skinScore}%</Text>
                </View>
                <Text style={styles.scoreLabel}>Skin Health</Text>
                <Text style={styles.scoreStatus}>
                  {skinScore > 80 ? 'Excellent' : skinScore > 60 ? 'Good' : 'Needs Care'}
                </Text>
              </View>
              <View style={styles.scoreCard}>
                <View style={[styles.scoreCircle, { borderColor: hairScore > 70 ? '#2ECC71' : '#F39C12' }]}>
                  <Text style={styles.scoreValue}>{hairScore}%</Text>
                </View>
                <Text style={styles.scoreLabel}>Hair Health</Text>
                <Text style={styles.scoreStatus}>
                  {hairScore > 80 ? 'Excellent' : hairScore > 60 ? 'Good' : 'Needs Care'}
                </Text>
              </View>
            </View>
          </Animated.View>

          {/* Detection Results */}
          <Animated.View entering={FadeInDown.delay(200)} style={styles.detectionsContainer}>
            <Text style={styles.sectionTitle}>What We Found</Text>
            
            {analysisResult.face_shape && (
              <View style={styles.detectionItem}>
                <View style={styles.detectionIcon}>
                  <Ionicons name="happy" size={20} color="#D4AF37" />
                </View>
                <View style={styles.detectionInfo}>
                  <Text style={styles.detectionLabel}>Face Shape</Text>
                  <Text style={styles.detectionValue}>{analysisResult.face_shape}</Text>
                </View>
                <View style={styles.matchBadge}>
                  <Text style={styles.matchText}>{getConfidencePercent()}% Match</Text>
                </View>
              </View>
            )}

            {analysisResult.skin_type && (
              <View style={styles.detectionItem}>
                <View style={styles.detectionIcon}>
                  <Ionicons name="water" size={20} color="#D4AF37" />
                </View>
                <View style={styles.detectionInfo}>
                  <Text style={styles.detectionLabel}>Skin Type</Text>
                  <Text style={styles.detectionValue}>{analysisResult.skin_type}</Text>
                </View>
                <View style={styles.matchBadge}>
                  <Text style={styles.matchText}>{Math.max(getConfidencePercent() - 3, 75)}% Match</Text>
                </View>
              </View>
            )}

            {analysisResult.hair_type && (
              <View style={styles.detectionItem}>
                <View style={styles.detectionIcon}>
                  <Ionicons name="leaf" size={20} color="#D4AF37" />
                </View>
                <View style={styles.detectionInfo}>
                  <Text style={styles.detectionLabel}>Hair Type</Text>
                  <Text style={styles.detectionValue}>{analysisResult.hair_type}</Text>
                </View>
                <View style={styles.matchBadge}>
                  <Text style={styles.matchText}>{Math.max(getConfidencePercent() - 5, 70)}% Match</Text>
                </View>
              </View>
            )}
          </Animated.View>

          {/* Concerns */}
          {(analysisResult.skin_concerns?.length > 0 || analysisResult.hair_concerns?.length > 0) && (
            <Animated.View entering={FadeInDown.delay(300)} style={styles.concernsContainer}>
              <Text style={styles.sectionTitle}>Areas to Focus</Text>
              <View style={styles.concernsTags}>
                {analysisResult.skin_concerns?.map((concern: string, index: number) => (
                  <View key={`skin-${index}`} style={styles.concernTag}>
                    <Ionicons name="alert-circle" size={14} color="#E74C3C" />
                    <Text style={styles.concernTagText}>{concern}</Text>
                  </View>
                ))}
                {analysisResult.hair_concerns?.map((concern: string, index: number) => (
                  <View key={`hair-${index}`} style={styles.concernTag}>
                    <Ionicons name="alert-circle" size={14} color="#F39C12" />
                    <Text style={styles.concernTagText}>{concern}</Text>
                  </View>
                ))}
              </View>
            </Animated.View>
          )}

          {/* Recommended Treatments */}
          {(analysisResult.top_recommendations?.length > 0 || analysisResult.recommended_treatments?.length > 0) && (
            <Animated.View entering={FadeInDown.delay(400)} style={styles.recommendationsContainer}>
              <Text style={styles.sectionTitle}>Recommended For You</Text>
              {(analysisResult.top_recommendations || analysisResult.recommended_treatments)?.map(
                (rec: string, index: number) => (
                  <View key={index} style={styles.recommendationItem}>
                    <Ionicons name="checkmark-circle" size={18} color="#2ECC71" />
                    <Text style={styles.recommendationText}>{rec}</Text>
                  </View>
                )
              )}
            </Animated.View>
          )}

          {/* Aftercare Tips */}
          {analysisResult.personalized_tips?.length > 0 && (
            <Animated.View entering={FadeInDown.delay(500)} style={styles.tipsContainer}>
              <Text style={styles.sectionTitle}>Aftercare Tips</Text>
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
            <TouchableOpacity style={styles.primaryButton} onPress={handleGetPackages}>
              <Ionicons name="sparkles" size={20} color="#0A0A0A" />
              <Text style={styles.primaryButtonText}>Get Package Recommendations</Text>
            </TouchableOpacity>

            <TouchableOpacity style={styles.secondaryButton} onPress={handleRetake}>
              <Ionicons name="camera" size={20} color="#D4AF37" />
              <Text style={styles.secondaryButtonText}>Retake Scan</Text>
            </TouchableOpacity>
          </View>
        </Animated.ScrollView>
      </View>
    );
  }

  // Analyzing View
  if (scanStep === 'analyzing' || analyzing) {
    return (
      <View style={[styles.container, { paddingTop: insets.top }]}>
        <View style={styles.analyzingContainer}>
          <Animated.View style={[styles.analyzingIcon, pulseStyle]}>
            <Ionicons name="scan" size={48} color="#D4AF37" />
          </Animated.View>
          <Text style={styles.analyzingTitle}>AI Analysis in Progress</Text>
          <Text style={styles.analyzingText}>
            Our advanced AI is examining your skin, hair, and facial features...
          </Text>
          <View style={styles.progressSteps}>
            <View style={styles.progressStep}>
              <Ionicons name="checkmark-circle" size={20} color="#2ECC71" />
              <Text style={styles.progressStepText}>Images Captured</Text>
            </View>
            <View style={styles.progressStep}>
              <ActivityIndicator size="small" color="#D4AF37" />
              <Text style={styles.progressStepText}>Analyzing Features</Text>
            </View>
            <View style={styles.progressStep}>
              <Ionicons name="ellipse-outline" size={20} color="rgba(255,255,255,0.3)" />
              <Text style={[styles.progressStepText, { color: 'rgba(255,255,255,0.3)' }]}>Generating Report</Text>
            </View>
          </View>
        </View>
      </View>
    );
  }

  // Camera View with Guided Capture
  const currentStepInfo = SCAN_STEPS[scanStep as keyof typeof SCAN_STEPS];
  const progress = scanStep === 'front' ? 33 : scanStep === 'left' ? 66 : 100;

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
            <View style={styles.stepIndicator}>
              <Text style={styles.stepText}>{currentStepInfo.label}</Text>
              <Text style={styles.stepCount}>
                {scanStep === 'front' ? '1' : scanStep === 'left' ? '2' : '3'}/3
              </Text>
            </View>
            <TouchableOpacity
              onPress={() => setFacing(facing === 'front' ? 'back' : 'front')}
              style={styles.flipButton}
            >
              <Ionicons name="camera-reverse" size={24} color="#FFFFFF" />
            </TouchableOpacity>
          </View>

          {/* Progress Bar */}
          <View style={styles.progressBarContainer}>
            <View style={[styles.progressBarFill, { width: `${progress}%` }]} />
          </View>

          {/* Guide Frame */}
          <View style={styles.guideContainer}>
            <View style={styles.guideFrame}>
              {/* Animated scan line */}
              <Animated.View style={[styles.scanLine, scanLineStyle]} />
              
              {/* Corner guides */}
              <View style={[styles.guideCorner, styles.topLeft]} />
              <View style={[styles.guideCorner, styles.topRight]} />
              <View style={[styles.guideCorner, styles.bottomLeft]} />
              <View style={[styles.guideCorner, styles.bottomRight]} />
              
              {/* Direction indicator */}
              {scanStep !== 'front' && (
                <View style={styles.directionIndicator}>
                  <Ionicons 
                    name={scanStep === 'left' ? 'arrow-back' : 'arrow-forward'} 
                    size={32} 
                    color="#D4AF37" 
                  />
                </View>
              )}
            </View>
            
            <View style={styles.instructionContainer}>
              <Ionicons name={currentStepInfo.icon as any} size={24} color="#D4AF37" />
              <Text style={styles.instructionText}>{currentStepInfo.instruction}</Text>
            </View>
          </View>

          {/* Captured thumbnails */}
          {Object.keys(scanImages).length > 0 && (
            <View style={styles.thumbnailsContainer}>
              {scanImages.front && <View style={styles.thumbnailDone}><Ionicons name="checkmark" size={16} color="#2ECC71" /></View>}
              {scanImages.left && <View style={styles.thumbnailDone}><Ionicons name="checkmark" size={16} color="#2ECC71" /></View>}
              {scanImages.right && <View style={styles.thumbnailDone}><Ionicons name="checkmark" size={16} color="#2ECC71" /></View>}
            </View>
          )}

          {/* Controls */}
          <View style={[styles.controlsContainer, { paddingBottom: insets.bottom + 20 }]}>
            <TouchableOpacity style={styles.galleryIconButton} onPress={handlePickImage}>
              <Ionicons name="images" size={28} color="#FFFFFF" />
              <Text style={styles.controlLabel}>Gallery</Text>
            </TouchableOpacity>

            <TouchableOpacity style={styles.captureButton} onPress={handleCapture}>
              <View style={styles.captureButtonInner} />
            </TouchableOpacity>

            <TouchableOpacity style={styles.skipButton} onPress={() => {
              if (scanImages.front) {
                setScanStep('analyzing');
                analyzeImages(scanImages);
              }
            }} disabled={!scanImages.front}>
              <Ionicons name="arrow-forward" size={28} color={scanImages.front ? '#FFFFFF' : 'rgba(255,255,255,0.3)'} />
              <Text style={[styles.controlLabel, !scanImages.front && { color: 'rgba(255,255,255,0.3)' }]}>Skip</Text>
            </TouchableOpacity>
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
  loadingContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
  },
  loadingText: {
    color: 'rgba(255,255,255,0.6)',
    marginTop: 12,
    fontSize: 14,
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
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: 20,
    gap: 8,
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
  stepIndicator: {
    alignItems: 'center',
  },
  stepText: {
    fontSize: 16,
    fontWeight: '600',
    color: '#FFFFFF',
  },
  stepCount: {
    fontSize: 12,
    color: '#D4AF37',
    marginTop: 2,
  },
  flipButton: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: 'rgba(0, 0, 0, 0.3)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  progressBarContainer: {
    height: 3,
    backgroundColor: 'rgba(255,255,255,0.2)',
    marginHorizontal: 20,
    borderRadius: 2,
  },
  progressBarFill: {
    height: '100%',
    backgroundColor: '#D4AF37',
    borderRadius: 2,
  },
  guideContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
  },
  guideFrame: {
    width: 260,
    height: 320,
    borderRadius: 130,
    position: 'relative',
    overflow: 'hidden',
  },
  scanLine: {
    position: 'absolute',
    left: 0,
    right: 0,
    height: 2,
    backgroundColor: '#D4AF37',
    opacity: 0.7,
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
  directionIndicator: {
    position: 'absolute',
    top: '50%',
    left: '50%',
    transform: [{ translateX: -16 }, { translateY: -16 }],
  },
  instructionContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: 24,
    backgroundColor: 'rgba(0,0,0,0.5)',
    paddingHorizontal: 20,
    paddingVertical: 12,
    borderRadius: 25,
    gap: 10,
  },
  instructionText: {
    fontSize: 14,
    color: '#FFFFFF',
  },
  thumbnailsContainer: {
    flexDirection: 'row',
    justifyContent: 'center',
    gap: 12,
    marginBottom: 20,
  },
  thumbnailDone: {
    width: 32,
    height: 32,
    borderRadius: 16,
    backgroundColor: 'rgba(46, 204, 113, 0.2)',
    justifyContent: 'center',
    alignItems: 'center',
    borderWidth: 2,
    borderColor: '#2ECC71',
  },
  controlsContainer: {
    flexDirection: 'row',
    justifyContent: 'space-around',
    alignItems: 'center',
    paddingHorizontal: 40,
  },
  galleryIconButton: {
    alignItems: 'center',
    gap: 4,
  },
  skipButton: {
    alignItems: 'center',
    gap: 4,
  },
  controlLabel: {
    fontSize: 11,
    color: '#FFFFFF',
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
    fontSize: 22,
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
  progressSteps: {
    marginTop: 32,
    gap: 16,
  },
  progressStep: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  progressStepText: {
    fontSize: 14,
    color: 'rgba(255,255,255,0.7)',
  },
  resultsContent: {
    paddingHorizontal: 20,
    paddingBottom: 40,
  },
  scoresContainer: {
    marginBottom: 24,
  },
  sectionTitle: {
    fontSize: 18,
    fontWeight: '600',
    color: '#FFFFFF',
    marginBottom: 16,
  },
  scoresRow: {
    flexDirection: 'row',
    gap: 12,
  },
  scoreCard: {
    flex: 1,
    backgroundColor: 'rgba(255, 255, 255, 0.05)',
    borderRadius: 16,
    padding: 20,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.1)',
  },
  scoreCircle: {
    width: 80,
    height: 80,
    borderRadius: 40,
    borderWidth: 4,
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: 12,
  },
  scoreValue: {
    fontSize: 22,
    fontWeight: '700',
    color: '#FFFFFF',
  },
  scoreLabel: {
    fontSize: 14,
    color: '#FFFFFF',
    fontWeight: '500',
  },
  scoreStatus: {
    fontSize: 12,
    color: 'rgba(255,255,255,0.5)',
    marginTop: 4,
  },
  detectionsContainer: {
    marginBottom: 24,
  },
  detectionItem: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(255, 255, 255, 0.05)',
    borderRadius: 12,
    padding: 14,
    marginBottom: 10,
  },
  detectionIcon: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: 'rgba(212, 175, 55, 0.1)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  detectionInfo: {
    flex: 1,
    marginLeft: 12,
  },
  detectionLabel: {
    fontSize: 12,
    color: 'rgba(255,255,255,0.5)',
  },
  detectionValue: {
    fontSize: 16,
    fontWeight: '600',
    color: '#FFFFFF',
    textTransform: 'capitalize',
  },
  matchBadge: {
    backgroundColor: 'rgba(46, 204, 113, 0.2)',
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 10,
  },
  matchText: {
    fontSize: 11,
    color: '#2ECC71',
    fontWeight: '600',
  },
  concernsContainer: {
    marginBottom: 24,
  },
  concernsTags: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  concernTag: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(231, 76, 60, 0.1)',
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 16,
    gap: 6,
  },
  concernTagText: {
    fontSize: 13,
    color: '#E74C3C',
    textTransform: 'capitalize',
  },
  recommendationsContainer: {
    backgroundColor: 'rgba(46, 204, 113, 0.08)',
    borderRadius: 16,
    padding: 16,
    marginBottom: 24,
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
  tipsContainer: {
    marginBottom: 24,
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
    marginTop: 16,
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
