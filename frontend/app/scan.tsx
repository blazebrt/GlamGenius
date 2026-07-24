import React, { useState, useRef, useEffect } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ActivityIndicator,
  ScrollView,
  Alert,
  Dimensions,
} from 'react-native';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import Animated, { 
  FadeIn, 
  FadeInDown, 
  FadeInUp,
  useSharedValue, 
  useAnimatedStyle, 
  withRepeat, 
  withTiming, 
  withSequence,
  Easing,
  interpolate,
} from 'react-native-reanimated';
import { CameraView, useCameraPermissions } from 'expo-camera';
import * as ImagePicker from 'expo-image-picker';
import { api } from '../src/services/api';
import { COLORS, FONTS, SPACING, RADIUS, SHADOWS } from '../src/theme/colors';

const { width: SCREEN_WIDTH, height: SCREEN_HEIGHT } = Dimensions.get('window');

/**
 * MEDICAL SCAN SCREEN - Clinical Grade UX
 * 
 * USER JOURNEY MAP:
 * ================
 * 1. CAMERA VIEW (Anxiety Reduction)
 *    - Clear instructions: "Position your scalp in frame"
 *    - Visual guide ring that responds to positioning
 *    - Micro-copy builds confidence: "Looking good!" "Hold still..."
 * 
 * 2. AI PROCESSING (Anxiety Reduction + Anticipation Building)
 *    - Progress indicator with medical terminology
 *    - Shows what's being analyzed: "Analyzing follicle density..."
 *    - Estimated time: "Usually takes 10-15 seconds"
 * 
 * 3. DIAGNOSIS RESULTS (Excitement Building)
 *    - Health scores with positive framing (85% = "Great!")
 *    - Issues framed as "opportunities" not "problems"
 *    - Immediate solution: "Here's your personalized treatment"
 * 
 * 4. TREATMENT BOOKING (Conversion)
 *    - Clear CTA: "Book Your Treatment"
 *    - Social proof: "Join 2,341 others who improved their scalp health"
 */

type ScanPhase = 'camera' | 'processing' | 'results';

// UX MICRO-COPY for scanner guidance
const SCAN_INSTRUCTIONS = {
  face: {
    title: 'Face Scan',
    initial: 'Center your face',
    tooFar: 'Move closer',
    tooClose: 'Move back slightly',
    notCentered: 'Center your face',
    goodPosition: 'Perfect! Hold still',
    capturing: 'Capturing...',
  },
  scalp: {
    title: 'Scalp Scan',
    initial: 'Position scalp in frame',
    tooFar: 'Move closer to hair',
    tooClose: 'Back up a little',
    notCentered: 'Tilt head forward',
    goodPosition: 'Great! Hold steady',
    capturing: 'Scanning...',
  },
  hair: {
    title: 'Hair & Scalp Scan',
    initial: 'Frame your hair & scalp',
    tooFar: 'Move closer to hair',
    tooClose: 'Back up a little',
    notCentered: 'Center your hair',
    goodPosition: 'Great! Hold steady',
    capturing: 'Scanning...',
  },
};

// Processing steps shown during AI analysis
const PROCESSING_STEPS = {
  face: [
    { label: 'Mapping facial structure', duration: 1500 },
    { label: 'Analyzing skin texture', duration: 2000 },
    { label: 'Detecting hydration levels', duration: 1500 },
    { label: 'Evaluating pore health', duration: 1500 },
    { label: 'Generating diagnosis', duration: 2000 },
  ],
  scalp: [
    { label: 'Mapping scalp regions', duration: 1500 },
    { label: 'Analyzing follicle density', duration: 2000 },
    { label: 'Detecting scalp condition', duration: 1500 },
    { label: 'Evaluating oil balance', duration: 1500 },
    { label: 'Generating diagnosis', duration: 2000 },
  ],
  hair: [
    { label: 'Mapping hair structure', duration: 1500 },
    { label: 'Analyzing strand strength', duration: 2000 },
    { label: 'Detecting scalp condition', duration: 1500 },
    { label: 'Evaluating moisture & shine', duration: 1500 },
    { label: 'Generating diagnosis', duration: 2000 },
  ],
};

export default function ScanScreen() {
  const router = useRouter();
  const params = useLocalSearchParams();
  const insets = useSafeAreaInsets();
  const [permission, requestPermission] = useCameraPermissions();
  const cameraRef = useRef<CameraView>(null);

  const scanType = (params.scanType as string) || 'face';
  const instructions = SCAN_INSTRUCTIONS[scanType as keyof typeof SCAN_INSTRUCTIONS] || SCAN_INSTRUCTIONS.face;
  const isHair = scanType === 'hair' || scanType === 'scalp';
  const scanNoun = isHair ? 'Hair & Scalp' : 'Skin';

  const [phase, setPhase] = useState<ScanPhase>('camera');
  const [guidanceText, setGuidanceText] = useState(instructions.initial);
  const [isReady, setIsReady] = useState(false);
  const [currentProcessingStep, setCurrentProcessingStep] = useState(0);
  const [analysisResult, setAnalysisResult] = useState<any>(null);
  const [userId, setUserId] = useState<string | null>(null);

  // Get user ID on mount
  useEffect(() => {
    const getUserId = async () => {
      try {
        const AsyncStorage = require('@react-native-async-storage/async-storage').default;
        const storedUserId = await AsyncStorage.getItem('glamgenius_user_id');
        setUserId(storedUserId);
      } catch (error) {
        console.log('Could not get user ID:', error);
      }
    };
    getUserId();
  }, []);

  // Animation values
  const ringProgress = useSharedValue(0);
  const ringScale = useSharedValue(1);
  const processingProgress = useSharedValue(0);

  // Simulate position detection and ring fill
  useEffect(() => {
    if (phase === 'camera') {
      // Simulate detecting good position after 2 seconds
      const positionTimer = setTimeout(() => {
        setGuidanceText(instructions.goodPosition);
        setIsReady(true);
        ringProgress.value = withTiming(1, { duration: 1500 });
      }, 2000);

      // Pulse animation for ring
      ringScale.value = withRepeat(
        withSequence(
          withTiming(1.05, { duration: 800, easing: Easing.ease }),
          withTiming(1, { duration: 800, easing: Easing.ease })
        ),
        -1,
        true
      );

      return () => clearTimeout(positionTimer);
    }
  }, [phase]);

  // Process steps animation during AI analysis
  useEffect(() => {
    if (phase === 'processing') {
      const steps = PROCESSING_STEPS[scanType as keyof typeof PROCESSING_STEPS] || PROCESSING_STEPS.face;
      let stepIndex = 0;
      let totalDuration = 0;

      const runStep = () => {
        if (stepIndex < steps.length) {
          setCurrentProcessingStep(stepIndex);
          processingProgress.value = withTiming((stepIndex + 1) / steps.length, { 
            duration: steps[stepIndex].duration 
          });
          
          setTimeout(() => {
            stepIndex++;
            runStep();
          }, steps[stepIndex].duration);
        }
      };

      runStep();
    }
  }, [phase]);

  const ringAnimatedStyle = useAnimatedStyle(() => ({
    transform: [{ scale: ringScale.value }],
    borderColor: interpolate(ringProgress.value, [0, 1], [0, 1]) > 0.5 
      ? COLORS.success 
      : COLORS.scanRing,
  }));

  const progressBarStyle = useAnimatedStyle(() => ({
    width: `${processingProgress.value * 100}%`,
  }));

  const handleCapture = async () => {
    if (!isReady) return;
    
    setGuidanceText(instructions.capturing);
    
    if (cameraRef.current) {
      try {
        const photo = await cameraRef.current.takePictureAsync({ base64: true, quality: 0.7 });
        if (photo?.base64) {
          setPhase('processing');
          analyzeImage(photo.base64);
        }
      } catch (error) {
        Alert.alert('Error', 'Failed to capture. Please try again.');
      }
    }
  };

  const handlePickImage = async () => {
    try {
      const result = await ImagePicker.launchImageLibraryAsync({ 
        mediaTypes: ImagePicker.MediaTypeOptions.Images, 
        base64: true, 
        quality: 0.8 
      });
      
      if (!result.canceled && result.assets[0]?.base64) {
        setPhase('processing');
        await analyzeImage(result.assets[0].base64);
      }
    } catch (error) {
      console.log('Gallery picker error:', error);
      Alert.alert('Error', 'Could not open gallery. Please try again.');
    }
  };

  const analyzeImage = async (base64: string) => {
    try {
      // Fix base64 padding if needed
      let cleanBase64 = base64;
      if (cleanBase64.includes(',')) {
        cleanBase64 = cleanBase64.split(',')[1];
      }
      // Ensure proper padding
      while (cleanBase64.length % 4 !== 0) {
        cleanBase64 += '=';
      }
      
      const response = await api.post('/scan/analyze', { 
        image_base64: cleanBase64, 
        scan_type: scanType,
        user_id: userId || 'anonymous'
      });
      
      // Parse the response - analysis is nested inside
      const result = response.data.analysis || response.data;
      
      // Wait for processing animation to complete
      setTimeout(() => {
        setAnalysisResult(result);
        setPhase('results');
      }, 2500);
    } catch (error: any) {
      console.log('Analysis error:', error?.response?.data || error);
      Alert.alert('Analysis Failed', 'Please try again with better lighting and a clearer image.');
      setPhase('camera');
      setIsReady(false);
      setGuidanceText(instructions.initial);
    }
  };

  const getHealthScoreColor = (score: number) => {
    if (score >= 80) return COLORS.healthExcellent;
    if (score >= 60) return COLORS.healthGood;
    if (score >= 40) return COLORS.healthFair;
    return COLORS.healthPoor;
  };

  const getHealthLabel = (score: number) => {
    if (score >= 80) return 'Excellent';
    if (score >= 60) return 'Good';
    if (score >= 40) return 'Fair';
    return 'Needs Attention';
  };

  // Robustly extract the headline score regardless of scan type
  const getOverallScore = (result: any): number => {
    if (!result) return 0;
    const hs = result.health_scores || {};
    const candidate =
      result.overall_score ??
      hs.overall_skin_health ??
      hs.overall_hair_health ??
      hs.overall_scalp_health ??
      result.overall_health_scores?.skin_health ??
      result.overall_health_scores?.hair_health;
    const n = Number(candidate);
    return Number.isFinite(n) && n > 0 ? Math.round(n) : 72;
  };

  // Convert snake_case metric keys to readable labels
  const formatMetricLabel = (key: string) =>
    key
      .replace(/_/g, ' ')
      .replace(/\b\w/g, (c) => c.toUpperCase())
      .replace(/\bScore\b/g, '')
      .trim();

  // Normalise concerns (skin or hair) to a common shape
  const getConcerns = (result: any): any[] => {
    const raw = result?.skin_concerns || result?.hair_concerns || result?.scalp_concerns || [];
    return Array.isArray(raw) ? raw.slice(0, 5) : [];
  };

  const getTreatments = (result: any): any[] => {
    const raw = result?.recommended_treatments || result?.top_recommendations || [];
    return Array.isArray(raw) ? raw.slice(0, 4) : [];
  };

  const getOutcomes = (result: any): any[] => {
    const raw = result?.expected_outcomes || [];
    return Array.isArray(raw) ? raw.slice(0, 4) : [];
  };

  // Permission handling
  if (!permission) {
    return (
      <View style={[styles.container, styles.centerContent]}>
        <ActivityIndicator size="large" color={COLORS.primary} />
      </View>
    );
  }

  if (!permission.granted) {
    return (
      <View style={[styles.container, { paddingTop: insets.top }]}>
        <View style={styles.permissionContainer}>
          <View style={styles.permissionIcon}>
            <Ionicons name="camera-outline" size={48} color={COLORS.primary} />
          </View>
          <Text style={styles.permissionTitle}>Camera Access Required</Text>
          <Text style={styles.permissionText}>
            We need camera access to perform your {scanType} analysis. Your photos are processed securely and never stored.
          </Text>
          <TouchableOpacity style={styles.permissionBtn} onPress={requestPermission}>
            <Text style={styles.permissionBtnText}>Enable Camera</Text>
          </TouchableOpacity>
          <TouchableOpacity style={styles.galleryBtn} onPress={handlePickImage}>
            <Ionicons name="images-outline" size={20} color={COLORS.primary} />
            <Text style={styles.galleryBtnText}>Choose from Gallery</Text>
          </TouchableOpacity>
        </View>
      </View>
    );
  }

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      {/* PHASE 1: CAMERA VIEW */}
      {phase === 'camera' && (
        <View style={styles.cameraContainer}>
          {/* Header */}
          <View style={styles.cameraHeader}>
            <TouchableOpacity style={styles.closeBtn} onPress={() => router.back()}>
              <Ionicons name="close" size={24} color={COLORS.white} />
            </TouchableOpacity>
            <View style={styles.scanTypeIndicator}>
              <Ionicons 
                name={isHair ? 'cut-outline' : 'happy-outline'} 
                size={18} 
                color={COLORS.white} 
              />
              <Text style={styles.scanTypeText}>{instructions.title}</Text>
            </View>
            <TouchableOpacity style={styles.helpBtn}>
              <Ionicons name="help-circle-outline" size={24} color={COLORS.white} />
            </TouchableOpacity>
          </View>

          {/* Camera View */}
          <CameraView 
            ref={cameraRef} 
            style={styles.camera} 
            facing={isHair ? 'back' : 'front'}
          >
            {/* Scan Guide Overlay */}
            <View style={styles.scanOverlay}>
              {/* Guide Ring */}
              <Animated.View style={[styles.guideRing, ringAnimatedStyle]}>
                {isReady && (
                  <View style={styles.readyIndicator}>
                    <Ionicons name="checkmark" size={32} color={COLORS.success} />
                  </View>
                )}
              </Animated.View>

              {/* Guidance Text */}
              <Animated.View entering={FadeIn} style={styles.guidanceContainer}>
                <Text style={styles.guidanceText}>{guidanceText}</Text>
                {isHair && (
                  <Text style={styles.guidanceSubtext}>
                    Part your hair to show the scalp clearly
                  </Text>
                )}
              </Animated.View>
            </View>
          </CameraView>

          {/* Bottom Controls */}
          <View style={[styles.cameraControls, { paddingBottom: insets.bottom + 20 }]}>
            <TouchableOpacity style={styles.galleryTrigger} onPress={handlePickImage}>
              <Ionicons name="images" size={24} color={COLORS.white} />
            </TouchableOpacity>
            
            <TouchableOpacity 
              style={[styles.captureBtn, isReady && styles.captureBtnReady]} 
              onPress={handleCapture}
              disabled={!isReady}
            >
              <View style={[styles.captureBtnInner, isReady && styles.captureBtnInnerReady]} />
            </TouchableOpacity>
            
            <TouchableOpacity style={styles.flipBtn}>
              <Ionicons name="camera-reverse" size={24} color={COLORS.white} />
            </TouchableOpacity>
          </View>

          {/* Tips Banner */}
          <Animated.View entering={FadeInUp.delay(500)} style={styles.tipsBanner}>
            <Ionicons name="bulb-outline" size={16} color={COLORS.primary} />
            <Text style={styles.tipsText}>
              {isHair 
                ? 'Part your hair to expose the scalp clearly'
                : 'Remove glasses and ensure even lighting'
              }
            </Text>
          </Animated.View>
        </View>
      )}

      {/* PHASE 2: AI PROCESSING */}
      {phase === 'processing' && (
        <View style={styles.processingContainer}>
          <Animated.View entering={FadeIn} style={styles.processingContent}>
            {/* Animated Scanner */}
            <View style={styles.processingIcon}>
              <Animated.View style={styles.processingRing}>
                <ActivityIndicator size="large" color={COLORS.primary} />
              </Animated.View>
            </View>

            <Text style={styles.processingTitle}>Analyzing Your {scanNoun}</Text>
            
            {/* Current Step */}
            <Text style={styles.processingStep}>
              {(PROCESSING_STEPS[scanType as keyof typeof PROCESSING_STEPS] || PROCESSING_STEPS.face)[currentProcessingStep]?.label}
            </Text>

            {/* Progress Bar */}
            <View style={styles.progressBarContainer}>
              <Animated.View style={[styles.progressBar, progressBarStyle]} />
            </View>

            <Text style={styles.processingNote}>
              Our AI examines 50+ health markers for accurate diagnosis
            </Text>
          </Animated.View>
        </View>
      )}

      {/* PHASE 3: RESULTS */}
      {phase === 'results' && analysisResult && (() => {
        const overallScore = getOverallScore(analysisResult);
        const metrics = Object.entries(analysisResult.health_scores || {}).filter(
          ([k]) => !/overall/i.test(k)
        );
        const concerns = getConcerns(analysisResult);
        const treatments = getTreatments(analysisResult);
        const outcomes = getOutcomes(analysisResult);
        const detailed = analysisResult.detailed_analysis || {};
        const detailedEntries = Object.entries(detailed).filter(
          ([, v]) => typeof v === 'string' && (v as string).length > 0
        );
        const summary = analysisResult.overall_assessment || '';
        return (
        <ScrollView 
          style={styles.resultsContainer} 
          contentContainerStyle={{ paddingBottom: insets.bottom + 130 }}
          showsVerticalScrollIndicator={false}
        >
          {/* Results Header */}
          <Animated.View entering={FadeIn} style={styles.resultsHeader}>
            <Text style={styles.resultsLabel}>DIAGNOSIS COMPLETE</Text>
            <Text style={styles.resultsTitle}>Your {scanNoun} Analysis</Text>
          </Animated.View>

          {/* Health Score Card */}
          <Animated.View entering={FadeInDown.delay(100)} style={styles.scoreCard}>
            <View style={styles.scoreRing}>
              <Text style={[styles.scoreValue, { color: getHealthScoreColor(overallScore) }]}>
                {overallScore}
              </Text>
              <Text style={styles.scoreLabel}>Health Score</Text>
            </View>
            <Text style={[styles.scoreStatus, { color: getHealthScoreColor(overallScore) }]}>
              {getHealthLabel(overallScore)}
            </Text>
            <Text style={styles.scoreDescription}>
              {summary || (overallScore >= 70
                ? "Your results look promising! Here's how to maintain and improve."
                : "We've identified some areas for improvement. Let's create a plan.")}
            </Text>
          </Animated.View>

          {/* Detailed Metric Breakdown */}
          {metrics.length > 0 && (
            <Animated.View entering={FadeInDown.delay(150)} style={styles.conditionsCard}>
              <Text style={styles.cardTitle}>Detailed Breakdown</Text>
              <Text style={styles.cardSubtitle}>
                Your {scanNoun.toLowerCase()} health across key markers
              </Text>
              {metrics.map(([key, val]) => {
                const score = Number(val) || 0;
                return (
                  <View key={key} style={styles.metricRow}>
                    <View style={styles.metricHeader}>
                      <Text style={styles.metricLabel}>{formatMetricLabel(key)}</Text>
                      <Text style={[styles.metricValue, { color: getHealthScoreColor(score) }]}>{score}</Text>
                    </View>
                    <View style={styles.metricTrack}>
                      <View style={[styles.metricFill, { width: `${Math.min(score, 100)}%`, backgroundColor: getHealthScoreColor(score) }]} />
                    </View>
                  </View>
                );
              })}
            </Animated.View>
          )}

          {/* Detected Conditions - Framed Positively */}
          {concerns.length > 0 && (
            <Animated.View entering={FadeInDown.delay(200)} style={styles.conditionsCard}>
              <Text style={styles.cardTitle}>Areas of Focus</Text>
              <Text style={styles.cardSubtitle}>These are opportunities for improvement</Text>
              {concerns.map((concern: any, idx: number) => {
                const isObj = typeof concern === 'object' && concern !== null;
                const name = isObj ? (concern.concern || concern.name || concern.issue) : concern;
                const severity = isObj ? concern.severity : null;
                const location = isObj ? concern.location : null;
                const note = isObj ? concern.clinical_note : null;
                return (
                  <View key={idx} style={styles.conditionItem}>
                    <View style={styles.conditionIcon}>
                      <Ionicons name="alert-circle" size={20} color={COLORS.warning} />
                    </View>
                    <View style={styles.conditionContent}>
                      <View style={styles.conditionTitleRow}>
                        <Text style={styles.conditionName}>{name}</Text>
                        {severity ? (
                          <View style={styles.severityTag}>
                            <Text style={styles.severityText}>{severity}</Text>
                          </View>
                        ) : null}
                      </View>
                      {location ? <Text style={styles.conditionMeta}>Location: {location}</Text> : null}
                      <Text style={styles.conditionNote}>{note || 'Treatable with proper care'}</Text>
                    </View>
                  </View>
                );
              })}
            </Animated.View>
          )}

          {/* Zone-by-Zone analysis */}
          {detailedEntries.length > 0 && (
            <Animated.View entering={FadeInDown.delay(250)} style={styles.conditionsCard}>
              <Text style={styles.cardTitle}>Zone-by-Zone</Text>
              <Text style={styles.cardSubtitle}>Detailed observations per area</Text>
              {detailedEntries.map(([area, desc]) => (
                <View key={area} style={styles.zoneItem}>
                  <Text style={styles.zoneLabel}>{formatMetricLabel(area)}</Text>
                  <Text style={styles.zoneText}>{desc as string}</Text>
                </View>
              ))}
            </Animated.View>
          )}

          {/* Recommended Treatments with Outcomes */}
          {treatments.length > 0 && (
            <Animated.View entering={FadeInDown.delay(300)} style={styles.treatmentsCard}>
              <Text style={styles.cardTitle}>Recommended Treatments</Text>
              <Text style={styles.cardSubtitle}>Personalized for your diagnosis</Text>
              {treatments.map((t: any, idx: number) => {
                const isObj = typeof t === 'object' && t !== null;
                const tname = isObj ? (t.treatment || t.service || t.name) : t;
                const reason = isObj ? (t.reason || t.why_recommended) : null;
                const outcome = isObj ? (t.expected_results || t.expected_result) : null;
                const price = isObj ? (t.price_range_inr || t.price_range) : null;
                return (
                  <View key={idx} style={styles.treatmentBlock}>
                    <View style={styles.treatmentTop}>
                      <View style={styles.treatmentIcon}>
                        <Ionicons name="sparkles" size={18} color={COLORS.success} />
                      </View>
                      <Text style={styles.treatmentName}>{tname}</Text>
                      {price ? <Text style={styles.treatmentPrice}>{price}</Text> : null}
                    </View>
                    {reason ? <Text style={styles.treatmentReason}>{reason}</Text> : null}
                    {outcome ? (
                      <View style={styles.outcomePill}>
                        <Ionicons name="trending-up" size={14} color={COLORS.success} />
                        <Text style={styles.outcomePillText}>{outcome}</Text>
                      </View>
                    ) : null}
                  </View>
                );
              })}
            </Animated.View>
          )}

          {/* What To Expect - Outcomes Timeline */}
          {outcomes.length > 0 && (
            <Animated.View entering={FadeInDown.delay(350)} style={styles.treatmentsCard}>
              <Text style={styles.cardTitle}>What To Expect</Text>
              <Text style={styles.cardSubtitle}>Your transformation journey with treatment</Text>
              {outcomes.map((o: any, idx: number) => {
                const isObj = typeof o === 'object' && o !== null;
                const tf = isObj ? o.timeframe : `Step ${idx + 1}`;
                const imp = isObj ? o.improvement : o;
                return (
                  <View key={idx} style={styles.timelineItem}>
                    <View style={styles.timelineLeft}>
                      <View style={styles.timelineDot} />
                      {idx < outcomes.length - 1 && <View style={styles.timelineLine} />}
                    </View>
                    <View style={styles.timelineContent}>
                      <Text style={styles.timelineTime}>{tf}</Text>
                      <Text style={styles.timelineText}>{imp}</Text>
                    </View>
                  </View>
                );
              })}
            </Animated.View>
          )}

          {/* Social Proof */}
          <Animated.View entering={FadeInDown.delay(400)} style={styles.socialProof}>
            <Ionicons name="people" size={18} color={COLORS.success} />
            <Text style={styles.socialProofText}>
              2,341 others improved their {isHair ? 'hair' : 'skin'} health this month
            </Text>
          </Animated.View>
        </ScrollView>
        );
      })()}

      {/* Fixed CTA for Results */}
      {phase === 'results' && (
        <Animated.View 
          entering={FadeInUp} 
          style={[styles.resultsCta, { paddingBottom: insets.bottom + 16 }]}
        >
          <TouchableOpacity 
            style={styles.primaryCta} 
            onPress={() => router.push('/get-advice')}
          >
            <Text style={styles.primaryCtaText}>Get Your Treatment Plan</Text>
            <Ionicons name="arrow-forward" size={20} color={COLORS.white} />
          </TouchableOpacity>
          <TouchableOpacity 
            style={styles.secondaryCta} 
            onPress={() => { setPhase('camera'); setIsReady(false); setGuidanceText(instructions.initial); }}
          >
            <Text style={styles.secondaryCtaText}>Scan Again</Text>
          </TouchableOpacity>
        </Animated.View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: COLORS.background,
  },
  centerContent: {
    justifyContent: 'center',
    alignItems: 'center',
  },

  // Permission Screen
  permissionContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    paddingHorizontal: 40,
  },
  permissionIcon: {
    width: 100,
    height: 100,
    borderRadius: 50,
    backgroundColor: COLORS.primaryLight,
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: SPACING.lg,
  },
  permissionTitle: {
    fontSize: FONTS.sizes.h2,
    fontFamily: FONTS.family.heading,
    color: COLORS.textPrimary,
    marginBottom: SPACING.sm,
    textAlign: 'center',
  },
  permissionText: {
    fontSize: FONTS.sizes.body,
    fontFamily: FONTS.family.body,
    color: COLORS.textSecondary,
    textAlign: 'center',
    lineHeight: 24,
    marginBottom: SPACING.xl,
  },
  permissionBtn: {
    backgroundColor: COLORS.primary,
    paddingVertical: 16,
    paddingHorizontal: 40,
    borderRadius: RADIUS.xl,
    marginBottom: SPACING.md,
  },
  permissionBtnText: {
    fontSize: FONTS.sizes.bodyLg,
    fontFamily: FONTS.family.bodySemibold,
    color: COLORS.white,
  },
  galleryBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingVertical: 12,
  },
  galleryBtnText: {
    fontSize: FONTS.sizes.body,
    fontFamily: FONTS.family.bodyMedium,
    color: COLORS.primary,
  },

  // Camera Phase
  cameraContainer: {
    flex: 1,
    backgroundColor: COLORS.scanBackground,
  },
  cameraHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: SPACING.md,
    paddingVertical: SPACING.sm,
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    zIndex: 10,
  },
  closeBtn: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: 'rgba(255,255,255,0.2)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  scanTypeIndicator: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(255,255,255,0.2)',
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderRadius: RADIUS.full,
    gap: 6,
  },
  scanTypeText: {
    fontSize: FONTS.sizes.body,
    fontFamily: FONTS.family.bodySemibold,
    color: COLORS.white,
  },
  helpBtn: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: 'rgba(255,255,255,0.2)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  camera: {
    flex: 1,
  },
  scanOverlay: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
  },
  guideRing: {
    width: 280,
    height: 280,
    borderRadius: 140,
    borderWidth: 4,
    borderColor: COLORS.scanRing,
    justifyContent: 'center',
    alignItems: 'center',
  },
  readyIndicator: {
    width: 60,
    height: 60,
    borderRadius: 30,
    backgroundColor: 'rgba(16, 185, 129, 0.2)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  guidanceContainer: {
    position: 'absolute',
    bottom: 180,
    alignItems: 'center',
  },
  guidanceText: {
    fontSize: FONTS.sizes.h3,
    fontFamily: FONTS.family.bodySemibold,
    color: COLORS.white,
    textShadowColor: 'rgba(0,0,0,0.5)',
    textShadowOffset: { width: 0, height: 1 },
    textShadowRadius: 4,
  },
  guidanceSubtext: {
    fontSize: FONTS.sizes.body,
    fontFamily: FONTS.family.body,
    color: 'rgba(255,255,255,0.8)',
    marginTop: 4,
  },
  cameraControls: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-around',
    paddingHorizontal: 40,
    paddingTop: SPACING.lg,
    backgroundColor: 'rgba(0,0,0,0.4)',
  },
  galleryTrigger: {
    width: 50,
    height: 50,
    borderRadius: 25,
    backgroundColor: 'rgba(255,255,255,0.2)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  captureBtn: {
    width: 80,
    height: 80,
    borderRadius: 40,
    borderWidth: 4,
    borderColor: 'rgba(255,255,255,0.5)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  captureBtnReady: {
    borderColor: COLORS.white,
  },
  captureBtnInner: {
    width: 64,
    height: 64,
    borderRadius: 32,
    backgroundColor: COLORS.white,
  },
  captureBtnInnerReady: {
    backgroundColor: COLORS.white,
  },
  flipBtn: {
    width: 50,
    height: 50,
    borderRadius: 25,
    backgroundColor: 'rgba(255,255,255,0.2)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  tipsBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: COLORS.white,
    paddingHorizontal: SPACING.md,
    paddingVertical: SPACING.sm,
    gap: 8,
    position: 'absolute',
    bottom: 160,
    left: SPACING.lg,
    right: SPACING.lg,
    borderRadius: RADIUS.md,
  },
  tipsText: {
    flex: 1,
    fontSize: FONTS.sizes.bodySm,
    fontFamily: FONTS.family.body,
    color: COLORS.textSecondary,
  },

  // Processing Phase
  processingContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    paddingHorizontal: 40,
  },
  processingContent: {
    alignItems: 'center',
  },
  processingIcon: {
    marginBottom: SPACING.xl,
  },
  processingRing: {
    width: 100,
    height: 100,
    borderRadius: 50,
    backgroundColor: COLORS.primaryLight,
    justifyContent: 'center',
    alignItems: 'center',
  },
  processingTitle: {
    fontSize: FONTS.sizes.h2,
    fontFamily: FONTS.family.heading,
    color: COLORS.textPrimary,
    marginBottom: SPACING.md,
    textAlign: 'center',
  },
  processingStep: {
    fontSize: FONTS.sizes.body,
    fontFamily: FONTS.family.bodyMedium,
    color: COLORS.primary,
    marginBottom: SPACING.lg,
    textAlign: 'center',
  },
  progressBarContainer: {
    width: '100%',
    height: 6,
    backgroundColor: COLORS.backgroundTertiary,
    borderRadius: 3,
    marginBottom: SPACING.lg,
    overflow: 'hidden',
  },
  progressBar: {
    height: '100%',
    backgroundColor: COLORS.primary,
    borderRadius: 3,
  },
  processingNote: {
    fontSize: FONTS.sizes.bodySm,
    fontFamily: FONTS.family.body,
    color: COLORS.textMuted,
    textAlign: 'center',
  },

  // Results Phase
  resultsContainer: {
    flex: 1,
    paddingHorizontal: SPACING.lg,
  },
  resultsHeader: {
    paddingTop: SPACING.lg,
    marginBottom: SPACING.lg,
  },
  resultsLabel: {
    fontSize: FONTS.sizes.caption,
    fontFamily: FONTS.family.bodySemibold,
    color: COLORS.success,
    letterSpacing: 2,
    marginBottom: 4,
  },
  resultsTitle: {
    fontSize: FONTS.sizes.h1,
    fontFamily: FONTS.family.heading,
    color: COLORS.textPrimary,
  },
  scoreCard: {
    backgroundColor: COLORS.card,
    borderRadius: RADIUS.lg,
    padding: SPACING.xl,
    alignItems: 'center',
    marginBottom: SPACING.md,
    ...SHADOWS.md,
  },
  scoreRing: {
    width: 120,
    height: 120,
    borderRadius: 60,
    backgroundColor: COLORS.backgroundSecondary,
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: SPACING.md,
  },
  scoreValue: {
    fontSize: FONTS.sizes.display,
    fontFamily: FONTS.family.heading,
  },
  scoreLabel: {
    fontSize: FONTS.sizes.caption,
    fontFamily: FONTS.family.body,
    color: COLORS.textMuted,
  },
  scoreStatus: {
    fontSize: FONTS.sizes.h3,
    fontFamily: FONTS.family.heading,
    marginBottom: SPACING.sm,
  },
  scoreDescription: {
    fontSize: FONTS.sizes.body,
    fontFamily: FONTS.family.body,
    color: COLORS.textSecondary,
    textAlign: 'center',
    lineHeight: 22,
  },
  conditionsCard: {
    backgroundColor: COLORS.card,
    borderRadius: RADIUS.lg,
    padding: SPACING.lg,
    marginBottom: SPACING.md,
    ...SHADOWS.sm,
  },
  cardTitle: {
    fontSize: FONTS.sizes.h4,
    fontFamily: FONTS.family.heading,
    color: COLORS.textPrimary,
    marginBottom: 2,
  },
  cardSubtitle: {
    fontSize: FONTS.sizes.bodySm,
    fontFamily: FONTS.family.body,
    color: COLORS.textMuted,
    marginBottom: SPACING.md,
  },
  conditionItem: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: SPACING.sm,
    borderBottomWidth: 1,
    borderBottomColor: COLORS.borderLight,
  },
  conditionIcon: {
    width: 36,
    height: 36,
    borderRadius: 18,
    backgroundColor: COLORS.warningLight,
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: SPACING.md,
  },
  conditionContent: {
    flex: 1,
  },
  conditionName: {
    fontSize: FONTS.sizes.body,
    fontFamily: FONTS.family.bodySemibold,
    color: COLORS.textPrimary,
  },
  conditionNote: {
    fontSize: FONTS.sizes.bodySm,
    fontFamily: FONTS.family.body,
    color: COLORS.textMuted,
  },
  treatmentsCard: {
    backgroundColor: COLORS.card,
    borderRadius: RADIUS.lg,
    padding: SPACING.lg,
    marginBottom: SPACING.md,
    ...SHADOWS.sm,
  },
  treatmentItem: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: SPACING.md,
    borderBottomWidth: 1,
    borderBottomColor: COLORS.borderLight,
  },
  treatmentIcon: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: COLORS.successLight,
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: SPACING.md,
  },
  treatmentContent: {
    flex: 1,
  },
  treatmentName: {
    fontSize: FONTS.sizes.body,
    fontFamily: FONTS.family.bodySemibold,
    color: COLORS.textPrimary,
  },
  treatmentPrice: {
    fontSize: FONTS.sizes.bodySm,
    fontFamily: FONTS.family.body,
    color: COLORS.success,
  },
  socialProof: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    paddingVertical: SPACING.md,
  },
  socialProofText: {
    fontSize: FONTS.sizes.bodySm,
    fontFamily: FONTS.family.body,
    color: COLORS.textSecondary,
  },

  // Metric breakdown bars
  metricRow: {
    marginBottom: SPACING.md,
  },
  metricHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 6,
  },
  metricLabel: {
    fontSize: FONTS.sizes.bodySm,
    fontFamily: FONTS.family.bodyMedium,
    color: COLORS.textPrimary,
    flex: 1,
  },
  metricValue: {
    fontSize: FONTS.sizes.body,
    fontFamily: FONTS.family.bodySemibold,
  },
  metricTrack: {
    height: 8,
    backgroundColor: COLORS.backgroundTertiary,
    borderRadius: 4,
    overflow: 'hidden',
  },
  metricFill: {
    height: '100%',
    borderRadius: 4,
  },

  // Concern enhancements
  conditionTitleRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  severityTag: {
    backgroundColor: COLORS.backgroundSecondary,
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: RADIUS.full,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  severityText: {
    fontSize: FONTS.sizes.micro,
    fontFamily: FONTS.family.bodySemibold,
    color: COLORS.textSecondary,
    textTransform: 'capitalize',
  },
  conditionMeta: {
    fontSize: FONTS.sizes.caption,
    fontFamily: FONTS.family.body,
    color: COLORS.textMuted,
    marginTop: 2,
  },

  // Zone-by-zone
  zoneItem: {
    paddingVertical: SPACING.sm,
    borderBottomWidth: 1,
    borderBottomColor: COLORS.borderLight,
  },
  zoneLabel: {
    fontSize: FONTS.sizes.bodySm,
    fontFamily: FONTS.family.bodySemibold,
    color: COLORS.textPrimary,
    marginBottom: 2,
  },
  zoneText: {
    fontSize: FONTS.sizes.bodySm,
    fontFamily: FONTS.family.body,
    color: COLORS.textSecondary,
    lineHeight: 19,
  },

  // Treatment blocks with outcomes
  treatmentBlock: {
    paddingVertical: SPACING.md,
    borderBottomWidth: 1,
    borderBottomColor: COLORS.borderLight,
  },
  treatmentTop: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  treatmentReason: {
    fontSize: FONTS.sizes.bodySm,
    fontFamily: FONTS.family.body,
    color: COLORS.textSecondary,
    marginTop: 6,
    lineHeight: 19,
  },
  outcomePill: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    backgroundColor: COLORS.successLight,
    borderRadius: RADIUS.md,
    paddingHorizontal: 10,
    paddingVertical: 8,
    marginTop: 8,
  },
  outcomePillText: {
    flex: 1,
    fontSize: FONTS.sizes.bodySm,
    fontFamily: FONTS.family.bodyMedium,
    color: COLORS.textPrimary,
    lineHeight: 18,
  },

  // Outcomes timeline
  timelineItem: {
    flexDirection: 'row',
    paddingTop: SPACING.sm,
  },
  timelineLeft: {
    alignItems: 'center',
    width: 24,
  },
  timelineDot: {
    width: 12,
    height: 12,
    borderRadius: 6,
    backgroundColor: COLORS.primary,
    marginTop: 4,
  },
  timelineLine: {
    flex: 1,
    width: 2,
    backgroundColor: COLORS.border,
    marginTop: 2,
  },
  timelineContent: {
    flex: 1,
    paddingBottom: SPACING.md,
    marginLeft: SPACING.sm,
  },
  timelineTime: {
    fontSize: FONTS.sizes.bodySm,
    fontFamily: FONTS.family.bodySemibold,
    color: COLORS.textPrimary,
  },
  timelineText: {
    fontSize: FONTS.sizes.bodySm,
    fontFamily: FONTS.family.body,
    color: COLORS.textSecondary,
    marginTop: 2,
    lineHeight: 19,
  },
  resultsCta: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    backgroundColor: COLORS.background,
    paddingHorizontal: SPACING.lg,
    paddingTop: SPACING.md,
    borderTopWidth: 1,
    borderTopColor: COLORS.borderLight,
  },
  primaryCta: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: COLORS.primary,
    paddingVertical: 18,
    borderRadius: RADIUS.xl,
    gap: 10,
    marginBottom: SPACING.sm,
  },
  primaryCtaText: {
    fontSize: FONTS.sizes.bodyLg,
    fontFamily: FONTS.family.bodySemibold,
    color: COLORS.white,
  },
  secondaryCta: {
    alignItems: 'center',
    paddingVertical: SPACING.sm,
  },
  secondaryCtaText: {
    fontSize: FONTS.sizes.body,
    fontFamily: FONTS.family.bodyMedium,
    color: COLORS.textSecondary,
  },
});
