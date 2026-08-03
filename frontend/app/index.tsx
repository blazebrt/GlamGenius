import React, { useEffect, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ActivityIndicator,
  Dimensions,
} from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import AsyncStorage from '@react-native-async-storage/async-storage';
import Animated, { FadeIn, FadeInDown, FadeInUp } from 'react-native-reanimated';
import { useUserStore } from '../src/store/userStore';
import { COLORS, FONTS, SPACING, RADIUS } from '../src/theme/colors';

const { width } = Dimensions.get('window');

export default function WelcomeScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [loading, setLoading] = useState(true);
  const { userId } = useUserStore();

  useEffect(() => {
    checkExistingUser();
    // Runs once on mount. Session hydration happens in userStore.initializeUser,
    // which the root layout kicks off before this screen mounts.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const checkExistingUser = async () => {
    try {
      // Legacy value from before the Supabase cutover — cleared for good.
      await AsyncStorage.removeItem('glamgenius_user_id').catch(() => {});
      if (useUserStore.getState().userId) {
        setTimeout(() => router.replace('/(tabs)/today'), 900);
      } else {
        setLoading(false);
      }
    } catch {
      setLoading(false);
    }
  };

  // Invite-only private beta: signed-out users go straight to the invite flow.
  const handleGetStarted = () => {
    router.push('/(auth)/welcome');
  };

  if (loading && userId) {
    return (
      <View style={[styles.container, { paddingTop: insets.top }]}>
        <View style={styles.loadingContainer}>
          <Text style={styles.loadingLogo}>GlamGenius</Text>
          <Text style={styles.loadingTagline}>SKIN · HAIR · STYLE</Text>
          <ActivityIndicator size="large" color={COLORS.primary} style={{ marginTop: 24 }} />
        </View>
      </View>
    );
  }

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <View style={styles.decorCircle1} />
      <View style={styles.decorCircle2} />

      <Animated.View entering={FadeIn.delay(200)} style={styles.header}>
        <View style={styles.logoMark}>
          <Ionicons name="leaf-outline" size={30} color={COLORS.primary} />
        </View>
        <Text style={styles.brandName}>GlamGenius</Text>
        <Text style={styles.brandTagline}>STYLIST · WELLNESS · SKIN & HAIR</Text>
      </Animated.View>

      <Animated.View entering={FadeInDown.delay(400)} style={styles.heroSection}>
        <Text style={styles.heroTitle}>Your fashion{'\n'}stylist in{'\n'}your pocket</Text>
        <Text style={styles.heroSubtitle}>
          One coach for clothing that flatters your tone & frame, skin & hair habits, and occasion-ready looks — without celebrity budgets.
        </Text>
      </Animated.View>

      <Animated.View entering={FadeInDown.delay(600)} style={styles.features}>
        <FeatureItem icon="shirt-outline" title="Fashion stylist" description="Colours, fits, occasions & wearable trends" />
        <FeatureItem icon="scan-outline" title="Skin & hair advisor" description="Visible checks that inform your style" />
        <FeatureItem icon="nutrition-outline" title="Wellness coach" description="Ingredients, Indian foods & simple routines" />
      </Animated.View>

      <Animated.View entering={FadeInUp.delay(800)} style={[styles.ctaSection, { paddingBottom: insets.bottom + 20 }]}>
        <TouchableOpacity style={styles.ctaButton} onPress={handleGetStarted} disabled={loading} activeOpacity={0.85}>
          {loading ? (
            <ActivityIndicator color={COLORS.white} />
          ) : (
            <>
              <Text style={styles.ctaText}>Join the private beta</Text>
              <Ionicons name="arrow-forward" size={20} color={COLORS.white} />
            </>
          )}
        </TouchableOpacity>
        <Text style={styles.freeNote}>Invite-only — enter your code on the next screen</Text>
        <TouchableOpacity onPress={() => router.push('/(auth)/welcome')} style={{ marginTop: 14 }}>
          <Text style={styles.secondaryLink}>Already have an account? Sign in</Text>
        </TouchableOpacity>
        <Text style={styles.disclaimer}>General wellness & style guidance — not medical advice.</Text>
      </Animated.View>
    </View>
  );
}

function FeatureItem({ icon, title, description }: { icon: any; title: string; description: string }) {
  return (
    <View style={styles.featureItem}>
      <View style={styles.featureIcon}>
        <Ionicons name={icon} size={20} color={COLORS.primary} />
      </View>
      <View style={{ flex: 1 }}>
        <Text style={styles.featureTitle}>{title}</Text>
        <Text style={styles.featureDesc}>{description}</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.background, paddingHorizontal: SPACING.lg },
  loadingContainer: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  loadingLogo: { fontFamily: FONTS.family.heading, fontSize: 36, color: COLORS.textPrimary },
  loadingTagline: { fontFamily: FONTS.family.bodyMedium, fontSize: 12, color: COLORS.primary, letterSpacing: 3, marginTop: 8 },
  decorCircle1: {
    position: 'absolute', top: -80, right: -60, width: 220, height: 220, borderRadius: 110,
    backgroundColor: COLORS.primaryLight, opacity: 0.7,
  },
  decorCircle2: {
    position: 'absolute', bottom: 120, left: -80, width: 200, height: 200, borderRadius: 100,
    backgroundColor: COLORS.accentLight, opacity: 0.55,
  },
  header: { marginTop: SPACING.xl, alignItems: 'flex-start' },
  logoMark: {
    width: 56, height: 56, borderRadius: 16, backgroundColor: COLORS.primaryLight,
    alignItems: 'center', justifyContent: 'center', marginBottom: SPACING.sm,
  },
  brandName: { fontFamily: FONTS.family.heading, fontSize: 34, color: COLORS.textPrimary },
  brandTagline: { fontFamily: FONTS.family.bodyMedium, fontSize: 11, color: COLORS.primary, letterSpacing: 2.5, marginTop: 4 },
  heroSection: { marginTop: SPACING.xl, maxWidth: width - 48 },
  heroTitle: { fontFamily: FONTS.family.heading, fontSize: 36, lineHeight: 42, color: COLORS.textPrimary },
  heroSubtitle: { fontFamily: FONTS.family.body, fontSize: 15, lineHeight: 22, color: COLORS.textSecondary, marginTop: SPACING.md },
  features: { marginTop: SPACING.xl, gap: SPACING.md },
  featureItem: { flexDirection: 'row', alignItems: 'center', gap: SPACING.md },
  featureIcon: {
    width: 40, height: 40, borderRadius: 12, backgroundColor: COLORS.card,
    alignItems: 'center', justifyContent: 'center', borderWidth: 1, borderColor: COLORS.border,
  },
  featureTitle: { fontFamily: FONTS.family.bodySemibold, fontSize: 15, color: COLORS.textPrimary },
  featureDesc: { fontFamily: FONTS.family.body, fontSize: 13, color: COLORS.textSecondary, marginTop: 2 },
  ctaSection: { marginTop: 'auto' },
  ctaButton: {
    backgroundColor: COLORS.primary, borderRadius: RADIUS.lg, paddingVertical: 16, paddingHorizontal: 20,
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 10,
  },
  ctaText: { fontFamily: FONTS.family.bodySemibold, fontSize: 16, color: COLORS.white },
  freeNote: { fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.textMuted, textAlign: 'center', marginTop: 10 },
  secondaryLink: { fontFamily: FONTS.family.bodyMedium, fontSize: 14, color: COLORS.primary, textAlign: 'center' },
  disclaimer: { fontFamily: FONTS.family.body, fontSize: 11, color: COLORS.textMuted, textAlign: 'center', marginTop: 12 },
});
