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

const SCAN_OPTIONS = [
  {
    id: 'face',
    title: 'Face & skin check',
    subtitle: 'Tone, texture, everyday care',
    icon: 'happy-outline',
    description: 'Skin tone, undertone, visible texture, colours, ingredients & foods.',
    features: ['Skin tone', 'Colours', 'Ingredients', 'Foods'],
  },
  {
    id: 'hair',
    title: 'Hair & scalp check',
    subtitle: 'Shine, dryness, ends',
    icon: 'cut-outline',
    description: 'Hair look, care ingredients, salon ideas, and plate tips for hair.',
    features: ['Hair look', 'Label tips', 'Salon ideas', 'Foods'],
  },
  {
    id: 'hands',
    title: 'Hands & nails',
    subtitle: 'Hand skin & nail care',
    icon: 'hand-left-outline',
    description: 'Suggestions for healthier-looking hands and nails, including manicure ideas.',
    features: ['Hand skin', 'Nails', 'Care tips', 'Salon ideas'],
  },
];

// Prefer icons that exist across Ionicons sets; fall back safely in UI
const SAFE_ICONS: Record<string, any> = {
  face: 'happy-outline',
  hair: 'cut-outline',
  hands: 'fitness-outline',
};

export default function ScanTabScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [selectedScan, setSelectedScan] = useState<string | null>('face');

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <Animated.View entering={FadeIn} style={styles.header}>
        <Text style={styles.headerLabel}>WELLNESS COACH</Text>
        <Text style={styles.headerTitle}>Skin & hair check</Text>
        <Text style={styles.headerSubtitle}>
          Personal stylist guidance from your photo — not a diagnosis.
        </Text>
      </Animated.View>

      <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={styles.content}>
        <Animated.View entering={FadeInDown.delay(100)}>
          <Text style={styles.sectionTitle}>What would you like to check?</Text>
          {SCAN_OPTIONS.map((option) => (
            <TouchableOpacity
              key={option.id}
              style={[styles.scanCard, selectedScan === option.id && styles.scanCardSelected]}
              onPress={() => setSelectedScan(option.id)}
              activeOpacity={0.75}
            >
              <View style={styles.scanCardHeader}>
                <View style={styles.scanIconContainer}>
                  <Ionicons name={(SAFE_ICONS[option.id] || option.icon) as any} size={26} color={COLORS.primary} />
                </View>
                <View style={styles.scanCardTitles}>
                  <Text style={styles.scanCardTitle}>{option.title}</Text>
                  <Text style={styles.scanCardSubtitle}>{option.subtitle}</Text>
                </View>
                <View style={[styles.radioOuter, selectedScan === option.id && styles.radioOuterSelected]}>
                  {selectedScan === option.id && <View style={styles.radioInner} />}
                </View>
              </View>
              <Text style={styles.scanCardDescription}>{option.description}</Text>
              <View style={styles.featureTags}>
                {option.features.map((feature) => (
                  <View key={feature} style={styles.featureTag}>
                    <Text style={styles.featureTagText}>{feature}</Text>
                  </View>
                ))}
              </View>
            </TouchableOpacity>
          ))}
        </Animated.View>

        <Animated.View entering={FadeInDown.delay(200)} style={styles.howItWorks}>
          <Text style={styles.sectionTitle}>How it works</Text>
          <StepItem number="1" title="Take or upload a photo" description="Good daylight helps the coach see tone and texture." />
          <StepItem number="2" title="Get your coach plan" description="Scores, colours, ingredients, Indian foods, salon ideas." />
          <StepItem number="3" title="Act on this week’s 3 steps" description="Recheck in about 2 weeks to track progress." />
        </Animated.View>

        <View style={styles.disclaimer}>
          <Ionicons name="shield-checkmark-outline" size={18} color={COLORS.primary} />
          <Text style={styles.disclaimerText}>
            General wellness and style guidance from a photo — not medical advice.
          </Text>
        </View>
        <View style={{ height: 120 }} />
      </ScrollView>

      <View style={[styles.ctaContainer, { paddingBottom: insets.bottom + 90 }]}>
        <TouchableOpacity
          style={[styles.ctaButton, !selectedScan && styles.ctaButtonDisabled]}
          disabled={!selectedScan}
          onPress={() =>
            router.push({ pathname: '/scan', params: { scanType: selectedScan || 'face' } })
          }
        >
          <Text style={styles.ctaText}>Continue</Text>
          <Ionicons name="arrow-forward" size={18} color={COLORS.white} />
        </TouchableOpacity>
      </View>
    </View>
  );
}

function StepItem({ number, title, description }: { number: string; title: string; description: string }) {
  return (
    <View style={styles.stepRow}>
      <View style={styles.stepNum}><Text style={styles.stepNumText}>{number}</Text></View>
      <View style={{ flex: 1 }}>
        <Text style={styles.stepTitle}>{title}</Text>
        <Text style={styles.stepDesc}>{description}</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.backgroundSecondary },
  header: { paddingHorizontal: SPACING.lg, paddingTop: SPACING.md, paddingBottom: SPACING.sm },
  headerLabel: { fontFamily: FONTS.family.bodySemibold, fontSize: 11, color: COLORS.primary, letterSpacing: 1.5 },
  headerTitle: { fontFamily: FONTS.family.heading, fontSize: 28, color: COLORS.textPrimary, marginTop: 4 },
  headerSubtitle: { fontFamily: FONTS.family.body, fontSize: 14, color: COLORS.textSecondary, marginTop: 6, lineHeight: 20 },
  content: { paddingHorizontal: SPACING.lg },
  sectionTitle: { fontFamily: FONTS.family.headingMedium, fontSize: 18, color: COLORS.textPrimary, marginBottom: 12, marginTop: 8 },
  scanCard: {
    backgroundColor: COLORS.card, borderRadius: RADIUS.lg, padding: 16, marginBottom: 12,
    borderWidth: 1.5, borderColor: COLORS.border,
  },
  scanCardSelected: { borderColor: COLORS.primary, backgroundColor: COLORS.primaryLight },
  scanCardHeader: { flexDirection: 'row', alignItems: 'center' },
  scanIconContainer: {
    width: 48, height: 48, borderRadius: 14, backgroundColor: COLORS.card,
    alignItems: 'center', justifyContent: 'center', marginRight: 12,
  },
  scanCardTitles: { flex: 1 },
  scanCardTitle: { fontFamily: FONTS.family.bodySemibold, fontSize: 16, color: COLORS.textPrimary },
  scanCardSubtitle: { fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.textSecondary, marginTop: 2 },
  radioOuter: {
    width: 22, height: 22, borderRadius: 11, borderWidth: 2, borderColor: COLORS.borderDark,
    alignItems: 'center', justifyContent: 'center',
  },
  radioOuterSelected: { borderColor: COLORS.primary },
  radioInner: { width: 10, height: 10, borderRadius: 5, backgroundColor: COLORS.primary },
  scanCardDescription: { fontFamily: FONTS.family.body, fontSize: 13, color: COLORS.textSecondary, marginTop: 12, lineHeight: 19 },
  featureTags: { flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginTop: 12 },
  featureTag: { backgroundColor: COLORS.backgroundTertiary, paddingHorizontal: 10, paddingVertical: 4, borderRadius: RADIUS.full },
  featureTagText: { fontFamily: FONTS.family.bodyMedium, fontSize: 11, color: COLORS.textSecondary },
  howItWorks: { marginTop: SPACING.lg },
  stepRow: { flexDirection: 'row', gap: 12, marginBottom: 14 },
  stepNum: {
    width: 28, height: 28, borderRadius: 14, backgroundColor: COLORS.primary,
    alignItems: 'center', justifyContent: 'center',
  },
  stepNumText: { fontFamily: FONTS.family.bodyBold, fontSize: 12, color: COLORS.white },
  stepTitle: { fontFamily: FONTS.family.bodySemibold, fontSize: 14, color: COLORS.textPrimary },
  stepDesc: { fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.textSecondary, marginTop: 2 },
  disclaimer: {
    flexDirection: 'row', gap: 10, alignItems: 'flex-start', marginTop: SPACING.lg,
    backgroundColor: COLORS.card, padding: 14, borderRadius: RADIUS.md, borderWidth: 1, borderColor: COLORS.border,
  },
  disclaimerText: { flex: 1, fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.textSecondary, lineHeight: 18 },
  ctaContainer: {
    position: 'absolute', left: 0, right: 0, bottom: 0, paddingHorizontal: SPACING.lg, paddingTop: 12,
    backgroundColor: COLORS.backgroundSecondary, borderTopWidth: 1, borderTopColor: COLORS.border,
  },
  ctaButton: {
    backgroundColor: COLORS.primary, borderRadius: RADIUS.lg, paddingVertical: 16,
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8,
  },
  ctaButtonDisabled: { opacity: 0.45 },
  ctaText: { fontFamily: FONTS.family.bodySemibold, fontSize: 16, color: COLORS.white },
});
