import React, { useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ScrollView,
} from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import Animated, { FadeIn, FadeInDown } from 'react-native-reanimated';
import { COLORS, FONTS, SPACING, RADIUS } from '../../src/theme/colors';

/**
 * SCAN TAB - Entry Point for Medical Beauty Analysis
 * 
 * USER JOURNEY:
 * 1. User selects scan type (Face or Scalp)
 * 2. Sees clear instructions on what will be analyzed
 * 3. Taps to start camera scan
 * 4. Goes through guided scanning process
 * 5. Gets AI diagnosis with treatment recommendations
 */

const SCAN_OPTIONS = [
  {
    id: 'face',
    title: 'Face Scan',
    subtitle: 'Skin Health Analysis',
    icon: 'happy-outline',
    description: 'Analyze skin texture, hydration, pores, and aging signs',
    features: ['Texture & Pores', 'Hydration Level', 'Aging Analysis', 'Pigmentation'],
    color: COLORS.primary,
    bgColor: COLORS.primaryLight,
  },
  {
    id: 'scalp',
    title: 'Scalp Scan',
    subtitle: 'Hair & Scalp Health',
    icon: 'scan-outline',
    description: 'Check hair density, scalp condition, and follicle health',
    features: ['Hair Density', 'Scalp Condition', 'Oil Balance', 'Follicle Health'],
    color: COLORS.success,
    bgColor: COLORS.successLight,
  },
];

export default function ScanTabScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [selectedScan, setSelectedScan] = useState<string | null>(null);

  const handleStartScan = () => {
    router.push({ 
      pathname: '/scan', 
      params: { scanType: selectedScan || 'face' } 
    });
  };

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      {/* Header */}
      <Animated.View entering={FadeIn} style={styles.header}>
        <Text style={styles.headerLabel}>MEDICAL BEAUTY</Text>
        <Text style={styles.headerTitle}>AI Diagnostics</Text>
        <Text style={styles.headerSubtitle}>
          Clinical-grade analysis powered by advanced AI
        </Text>
      </Animated.View>

      <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={styles.content}>
        {/* Scan Type Selection */}
        <Animated.View entering={FadeInDown.delay(100)}>
          <Text style={styles.sectionTitle}>Select Analysis Type</Text>
          
          {SCAN_OPTIONS.map((option, index) => (
            <TouchableOpacity
              key={option.id}
              style={[
                styles.scanCard,
                selectedScan === option.id && styles.scanCardSelected,
              ]}
              onPress={() => setSelectedScan(option.id)}
              activeOpacity={0.7}
            >
              <View style={styles.scanCardHeader}>
                <View style={[styles.scanIconContainer, { backgroundColor: option.bgColor }]}>
                  <Ionicons name={option.icon as any} size={28} color={option.color} />
                </View>
                <View style={styles.scanCardTitles}>
                  <Text style={styles.scanCardTitle}>{option.title}</Text>
                  <Text style={styles.scanCardSubtitle}>{option.subtitle}</Text>
                </View>
                <View style={[
                  styles.radioOuter,
                  selectedScan === option.id && styles.radioOuterSelected
                ]}>
                  {selectedScan === option.id && <View style={styles.radioInner} />}
                </View>
              </View>
              
              <Text style={styles.scanCardDescription}>{option.description}</Text>
              
              <View style={styles.featureTags}>
                {option.features.map((feature, idx) => (
                  <View key={idx} style={styles.featureTag}>
                    <Text style={styles.featureTagText}>{feature}</Text>
                  </View>
                ))}
              </View>
            </TouchableOpacity>
          ))}
        </Animated.View>

        {/* How It Works */}
        <Animated.View entering={FadeInDown.delay(200)} style={styles.howItWorks}>
          <Text style={styles.sectionTitle}>How It Works</Text>
          
          <View style={styles.stepContainer}>
            <StepItem 
              number="1" 
              title="Position Camera" 
              description="Align your face or scalp within the guide frame"
            />
            <StepItem 
              number="2" 
              title="AI Analysis" 
              description="Our AI processes 50+ health markers in seconds"
            />
            <StepItem 
              number="3" 
              title="Get Results" 
              description="Receive detailed diagnosis with treatment options"
            />
          </View>
        </Animated.View>

        {/* Medical Disclaimer */}
        <Animated.View entering={FadeInDown.delay(300)} style={styles.disclaimer}>
          <Ionicons name="shield-checkmark" size={18} color={COLORS.primary} />
          <Text style={styles.disclaimerText}>
            Analysis is for informational purposes. Consult a dermatologist for medical advice.
          </Text>
        </Animated.View>

        <View style={{ height: 120 }} />
      </ScrollView>

      {/* CTA Button */}
      <Animated.View 
        entering={FadeInDown.delay(400)} 
        style={[styles.ctaContainer, { paddingBottom: insets.bottom + 90 }]}
      >
        <TouchableOpacity
          style={[styles.ctaButton, !selectedScan && styles.ctaButtonDisabled]}
          onPress={handleStartScan}
          disabled={!selectedScan}
          activeOpacity={0.8}
        >
          <Ionicons name="camera" size={22} color={COLORS.white} />
          <Text style={styles.ctaButtonText}>
            {selectedScan ? `Start ${selectedScan === 'face' ? 'Face' : 'Scalp'} Scan` : 'Select Scan Type'}
          </Text>
        </TouchableOpacity>
      </Animated.View>
    </View>
  );
}

function StepItem({ number, title, description }: { number: string; title: string; description: string }) {
  return (
    <View style={styles.stepItem}>
      <View style={styles.stepNumber}>
        <Text style={styles.stepNumberText}>{number}</Text>
      </View>
      <View style={styles.stepContent}>
        <Text style={styles.stepTitle}>{title}</Text>
        <Text style={styles.stepDescription}>{description}</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: COLORS.background,
  },
  header: {
    paddingHorizontal: SPACING.lg,
    paddingTop: SPACING.md,
    paddingBottom: SPACING.lg,
    borderBottomWidth: 1,
    borderBottomColor: COLORS.borderLight,
  },
  headerLabel: {
    fontSize: FONTS.sizes.caption,
    fontFamily: FONTS.family.bodySemibold,
    color: COLORS.primary,
    letterSpacing: 2,
    marginBottom: 4,
  },
  headerTitle: {
    fontSize: FONTS.sizes.h1,
    fontFamily: FONTS.family.heading,
    color: COLORS.textPrimary,
    marginBottom: 4,
  },
  headerSubtitle: {
    fontSize: FONTS.sizes.body,
    fontFamily: FONTS.family.body,
    color: COLORS.textSecondary,
  },
  content: {
    paddingHorizontal: SPACING.lg,
    paddingTop: SPACING.lg,
  },
  sectionTitle: {
    fontSize: FONTS.sizes.h4,
    fontFamily: FONTS.family.heading,
    color: COLORS.textPrimary,
    marginBottom: SPACING.md,
  },
  scanCard: {
    backgroundColor: COLORS.card,
    borderRadius: RADIUS.lg,
    padding: SPACING.lg,
    marginBottom: SPACING.md,
    borderWidth: 2,
    borderColor: COLORS.border,
  },
  scanCardSelected: {
    borderColor: COLORS.primary,
    backgroundColor: COLORS.primaryLight,
  },
  scanCardHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: SPACING.md,
  },
  scanIconContainer: {
    width: 56,
    height: 56,
    borderRadius: RADIUS.xl,
    justifyContent: 'center',
    alignItems: 'center',
  },
  scanCardTitles: {
    flex: 1,
    marginLeft: SPACING.md,
  },
  scanCardTitle: {
    fontSize: FONTS.sizes.h3,
    fontFamily: FONTS.family.heading,
    color: COLORS.textPrimary,
  },
  scanCardSubtitle: {
    fontSize: FONTS.sizes.bodySm,
    fontFamily: FONTS.family.body,
    color: COLORS.textSecondary,
    marginTop: 2,
  },
  radioOuter: {
    width: 24,
    height: 24,
    borderRadius: 12,
    borderWidth: 2,
    borderColor: COLORS.border,
    justifyContent: 'center',
    alignItems: 'center',
  },
  radioOuterSelected: {
    borderColor: COLORS.primary,
  },
  radioInner: {
    width: 12,
    height: 12,
    borderRadius: 6,
    backgroundColor: COLORS.primary,
  },
  scanCardDescription: {
    fontSize: FONTS.sizes.body,
    fontFamily: FONTS.family.body,
    color: COLORS.textSecondary,
    lineHeight: 22,
    marginBottom: SPACING.md,
  },
  featureTags: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  featureTag: {
    backgroundColor: COLORS.backgroundSecondary,
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: RADIUS.full,
  },
  featureTagText: {
    fontSize: FONTS.sizes.bodySm,
    fontFamily: FONTS.family.bodyMedium,
    color: COLORS.textSecondary,
  },
  howItWorks: {
    marginTop: SPACING.xl,
  },
  stepContainer: {
    gap: SPACING.md,
  },
  stepItem: {
    flexDirection: 'row',
    alignItems: 'flex-start',
  },
  stepNumber: {
    width: 32,
    height: 32,
    borderRadius: 16,
    backgroundColor: COLORS.primaryLight,
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: SPACING.md,
  },
  stepNumberText: {
    fontSize: FONTS.sizes.body,
    fontFamily: FONTS.family.bodySemibold,
    color: COLORS.primary,
  },
  stepContent: {
    flex: 1,
  },
  stepTitle: {
    fontSize: FONTS.sizes.bodyLg,
    fontFamily: FONTS.family.bodySemibold,
    color: COLORS.textPrimary,
    marginBottom: 2,
  },
  stepDescription: {
    fontSize: FONTS.sizes.body,
    fontFamily: FONTS.family.body,
    color: COLORS.textSecondary,
  },
  disclaimer: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: COLORS.infoLight,
    padding: SPACING.md,
    borderRadius: RADIUS.md,
    marginTop: SPACING.xl,
    gap: 10,
  },
  disclaimerText: {
    flex: 1,
    fontSize: FONTS.sizes.bodySm,
    fontFamily: FONTS.family.body,
    color: COLORS.textSecondary,
    lineHeight: 20,
  },
  ctaContainer: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    paddingHorizontal: SPACING.lg,
    backgroundColor: COLORS.background,
    borderTopWidth: 1,
    borderTopColor: COLORS.borderLight,
    paddingTop: SPACING.md,
  },
  ctaButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: COLORS.primary,
    paddingVertical: 18,
    borderRadius: RADIUS.xl,
    gap: 10,
  },
  ctaButtonDisabled: {
    backgroundColor: COLORS.accentMuted,
  },
  ctaButtonText: {
    fontSize: FONTS.sizes.bodyLg,
    fontFamily: FONTS.family.bodySemibold,
    color: COLORS.white,
  },
});
