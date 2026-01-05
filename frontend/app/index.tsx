import React, { useEffect, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  Dimensions,
  ActivityIndicator,
} from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import AsyncStorage from '@react-native-async-storage/async-storage';
import Animated, { FadeIn, FadeInDown, FadeInUp } from 'react-native-reanimated';
import { useUserStore } from '../src/store/userStore';
import { COLORS, FONTS, SPACING, RADIUS } from '../src/theme/colors';

const { width, height } = Dimensions.get('window');

/**
 * WELCOME SCREEN - First Impression
 * 
 * Design Philosophy:
 * - Clean, clinical white space
 * - Premium typography (Playfair headings, Inter body)
 * - Soft medical blue accents
 * - Trust signals through professional copy
 */

export default function WelcomeScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [loading, setLoading] = useState(true);
  const { userId, setUserId, createUser } = useUserStore();

  useEffect(() => {
    checkExistingUser();
  }, []);

  const checkExistingUser = async () => {
    try {
      const storedUserId = await AsyncStorage.getItem('glamgenius_user_id');
      if (storedUserId) {
        setUserId(storedUserId);
        setTimeout(() => {
          router.replace('/(tabs)/home');
        }, 1200);
      } else {
        setLoading(false);
      }
    } catch (error) {
      console.error('Error checking user:', error);
      setLoading(false);
    }
  };

  const handleGetStarted = async () => {
    setLoading(true);
    try {
      const newUser = await createUser('Guest User');
      if (newUser?.id) {
        await AsyncStorage.setItem('glamgenius_user_id', newUser.id);
        router.replace('/(tabs)/home');
      }
    } catch (error) {
      console.error('Error creating user:', error);
    } finally {
      setLoading(false);
    }
  };

  if (loading && userId) {
    return (
      <View style={[styles.container, { paddingTop: insets.top }]}>
        <View style={styles.loadingContainer}>
          <Text style={styles.loadingLogo}>GlamGenius</Text>
          <Text style={styles.loadingTagline}>MEDICAL BEAUTY</Text>
          <ActivityIndicator size="large" color={COLORS.primary} style={{ marginTop: 24 }} />
        </View>
      </View>
    );
  }

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      {/* Decorative Elements */}
      <View style={styles.decorCircle1} />
      <View style={styles.decorCircle2} />

      {/* Header */}
      <Animated.View entering={FadeIn.delay(200)} style={styles.header}>
        <View style={styles.logoMark}>
          <Ionicons name="sparkles" size={32} color={COLORS.primary} />
        </View>
        <Text style={styles.brandName}>GlamGenius</Text>
        <Text style={styles.brandTagline}>MEDICAL BEAUTY</Text>
      </Animated.View>

      {/* Value Proposition */}
      <Animated.View entering={FadeInDown.delay(400)} style={styles.heroSection}>
        <Text style={styles.heroTitle}>
          Clinical-Grade{'\n'}Beauty Analysis
        </Text>
        <Text style={styles.heroSubtitle}>
          AI-powered skin & scalp diagnostics with personalized treatment recommendations
        </Text>
      </Animated.View>

      {/* Features */}
      <Animated.View entering={FadeInDown.delay(600)} style={styles.features}>
        <FeatureItem 
          icon="scan-outline" 
          title="AI Diagnostics"
          description="50+ health markers analyzed"
        />
        <FeatureItem 
          icon="medical-outline" 
          title="Medical Accuracy"
          description="Dermatologist-grade insights"
        />
        <FeatureItem 
          icon="sparkles-outline" 
          title="Smart Treatments"
          description="Personalized recommendations"
        />
      </Animated.View>

      {/* Fixed CTA at bottom */}
      <Animated.View entering={FadeInUp.delay(800)} style={[styles.ctaSection, { paddingBottom: insets.bottom + 20 }]}>
        <TouchableOpacity
          style={styles.ctaButton}
          onPress={handleGetStarted}
          disabled={loading}
          activeOpacity={0.8}
        >
          {loading ? (
            <ActivityIndicator color={COLORS.white} />
          ) : (
            <>
              <Text style={styles.ctaText}>Begin Your Analysis</Text>
              <Ionicons name="arrow-forward" size={20} color={COLORS.white} />
            </>
          )}
        </TouchableOpacity>
        
        <Text style={styles.disclaimer}>
          Your data is processed securely and never shared
        </Text>
      </Animated.View>
    </View>
  );
}

function FeatureItem({ icon, title, description }: { icon: string; title: string; description: string }) {
  return (
    <View style={styles.featureItem}>
      <View style={styles.featureIcon}>
        <Ionicons name={icon as any} size={22} color={COLORS.primary} />
      </View>
      <View style={styles.featureContent}>
        <Text style={styles.featureTitle}>{title}</Text>
        <Text style={styles.featureDescription}>{description}</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: COLORS.background,
    paddingHorizontal: SPACING.lg,
  },
  loadingContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
  },
  loadingLogo: {
    fontSize: FONTS.sizes.h1,
    fontFamily: FONTS.family.heading,
    color: COLORS.textPrimary,
  },
  loadingTagline: {
    fontSize: FONTS.sizes.caption,
    fontFamily: FONTS.family.bodySemibold,
    color: COLORS.primary,
    letterSpacing: 3,
    marginTop: 8,
  },
  decorCircle1: {
    position: 'absolute',
    top: -80,
    right: -80,
    width: 200,
    height: 200,
    borderRadius: 100,
    backgroundColor: COLORS.primaryLight,
  },
  decorCircle2: {
    position: 'absolute',
    bottom: 100,
    left: -60,
    width: 140,
    height: 140,
    borderRadius: 70,
    backgroundColor: COLORS.backgroundSecondary,
  },
  header: {
    alignItems: 'center',
    marginTop: height * 0.08,
  },
  logoMark: {
    width: 72,
    height: 72,
    borderRadius: 36,
    backgroundColor: COLORS.primaryLight,
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: SPACING.md,
  },
  brandName: {
    fontSize: FONTS.sizes.displaySm,
    fontFamily: FONTS.family.heading,
    color: COLORS.textPrimary,
    letterSpacing: -0.5,
  },
  brandTagline: {
    fontSize: FONTS.sizes.caption,
    fontFamily: FONTS.family.bodySemibold,
    color: COLORS.primary,
    letterSpacing: 3,
    marginTop: 4,
  },
  heroSection: {
    marginTop: height * 0.06,
    alignItems: 'center',
  },
  heroTitle: {
    fontSize: FONTS.sizes.h1,
    fontFamily: FONTS.family.heading,
    color: COLORS.textPrimary,
    textAlign: 'center',
    lineHeight: 40,
  },
  heroSubtitle: {
    fontSize: FONTS.sizes.body,
    fontFamily: FONTS.family.body,
    color: COLORS.textSecondary,
    textAlign: 'center',
    marginTop: SPACING.md,
    lineHeight: 24,
    paddingHorizontal: SPACING.md,
  },
  features: {
    marginTop: height * 0.05,
    gap: SPACING.md,
  },
  featureItem: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: COLORS.card,
    padding: SPACING.md,
    borderRadius: RADIUS.md,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  featureIcon: {
    width: 48,
    height: 48,
    borderRadius: 24,
    backgroundColor: COLORS.primaryLight,
    justifyContent: 'center',
    alignItems: 'center',
  },
  featureContent: {
    marginLeft: SPACING.md,
    flex: 1,
  },
  featureTitle: {
    fontSize: FONTS.sizes.bodyLg,
    fontFamily: FONTS.family.bodySemibold,
    color: COLORS.textPrimary,
  },
  featureDescription: {
    fontSize: FONTS.sizes.bodySm,
    fontFamily: FONTS.family.body,
    color: COLORS.textSecondary,
    marginTop: 2,
  },
  ctaSection: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    paddingHorizontal: SPACING.lg,
    paddingTop: SPACING.lg,
    backgroundColor: COLORS.background,
    alignItems: 'center',
  },
  ctaButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: COLORS.primary,
    paddingVertical: 18,
    paddingHorizontal: 32,
    borderRadius: RADIUS.xl,
    width: '100%',
    gap: 10,
  },
  ctaText: {
    fontSize: FONTS.sizes.bodyLg,
    fontFamily: FONTS.family.bodySemibold,
    color: COLORS.white,
  },
  disclaimer: {
    fontSize: FONTS.sizes.caption,
    fontFamily: FONTS.family.body,
    color: COLORS.textMuted,
    marginTop: SPACING.md,
    textAlign: 'center',
  },
});
