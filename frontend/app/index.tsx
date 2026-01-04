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
import Animated, { FadeIn, FadeInDown } from 'react-native-reanimated';
import { useUserStore } from '../src/store/userStore';
import { COLORS, FONTS } from '../src/theme/colors';

const { width, height } = Dimensions.get('window');

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
        }, 1500);
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
          <Text style={styles.logoText}>GlamGenius</Text>
          <ActivityIndicator size="large" color={COLORS.primary} style={{ marginTop: 20 }} />
        </View>
      </View>
    );
  }

  return (
    <View style={[styles.container, { paddingTop: insets.top, paddingBottom: insets.bottom }]}>
      {/* Subtle Background Lines */}
      <View style={styles.decorLine1} />
      <View style={styles.decorLine2} />

      {/* Logo and Header */}
      <Animated.View entering={FadeIn.delay(200)} style={styles.headerContainer}>
        <View style={styles.logoContainer}>
          <Ionicons name="sparkles" size={36} color={COLORS.black} />
        </View>
        <Text style={styles.logoText}>GlamGenius</Text>
        <Text style={styles.tagline}>MEDICAL BEAUTY • SALON</Text>
      </Animated.View>

      {/* Features */}
      <Animated.View entering={FadeInDown.delay(400)} style={styles.featuresContainer}>
        <FeatureItem
          icon="scan-outline"
          title="AI Skin & Hair Analysis"
          description="Clinical-grade scanning for personalized insights"
        />
        <FeatureItem
          icon="sparkles-outline"
          title="Smart Recommendations"
          description="Budget & occasion-based service bundles"
        />
        <FeatureItem
          icon="calendar-outline"
          title="Track Your Journey"
          description="Progress tracking & visit history"
        />
      </Animated.View>

      {/* CTA Button */}
      <Animated.View entering={FadeInDown.delay(600)} style={styles.ctaContainer}>
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
              <Text style={styles.ctaText}>Get Started</Text>
              <Ionicons name="arrow-forward" size={22} color={COLORS.white} />
            </>
          )}
        </TouchableOpacity>
        <Text style={styles.disclaimer}>
          Your personalized beauty journey begins here
        </Text>
      </Animated.View>
    </View>
  );
}

function FeatureItem({ icon, title, description }: { icon: string; title: string; description: string }) {
  return (
    <View style={styles.featureItem}>
      <View style={styles.featureIconContainer}>
        <Ionicons name={icon as any} size={26} color={COLORS.black} />
      </View>
      <View style={styles.featureTextContainer}>
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
    paddingHorizontal: 24,
  },
  loadingContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
  },
  decorLine1: {
    position: 'absolute',
    top: 120,
    right: -50,
    width: 200,
    height: 1,
    backgroundColor: COLORS.border,
    transform: [{ rotate: '-45deg' }],
  },
  decorLine2: {
    position: 'absolute',
    bottom: 200,
    left: -50,
    width: 180,
    height: 1,
    backgroundColor: COLORS.border,
    transform: [{ rotate: '45deg' }],
  },
  headerContainer: {
    alignItems: 'center',
    marginTop: height * 0.1,
  },
  logoContainer: {
    width: 80,
    height: 80,
    borderRadius: 40,
    backgroundColor: COLORS.primaryLight,
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: 20,
    borderWidth: 2,
    borderColor: COLORS.black,
  },
  logoText: {
    fontSize: FONTS.sizes['4xl'],
    fontFamily: FONTS.family.bold,
    color: COLORS.black,
    letterSpacing: -1,
  },
  tagline: {
    fontSize: FONTS.sizes.sm,
    fontFamily: FONTS.family.medium,
    color: COLORS.textSecondary,
    marginTop: 8,
    letterSpacing: 3,
  },
  featuresContainer: {
    marginTop: height * 0.07,
    gap: 16,
  },
  featureItem: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: COLORS.card,
    padding: 18,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  featureIconContainer: {
    width: 52,
    height: 52,
    borderRadius: 26,
    backgroundColor: COLORS.primaryLight,
    justifyContent: 'center',
    alignItems: 'center',
  },
  featureTextContainer: {
    marginLeft: 16,
    flex: 1,
  },
  featureTitle: {
    fontSize: FONTS.sizes.lg,
    fontFamily: FONTS.family.semibold,
    color: COLORS.textPrimary,
    marginBottom: 4,
  },
  featureDescription: {
    fontSize: FONTS.sizes.sm,
    fontFamily: FONTS.family.regular,
    color: COLORS.textSecondary,
  },
  ctaContainer: {
    position: 'absolute',
    bottom: 50,
    left: 24,
    right: 24,
    alignItems: 'center',
  },
  ctaButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: COLORS.black,
    paddingVertical: 18,
    paddingHorizontal: 32,
    borderRadius: 30,
    width: '100%',
    gap: 10,
  },
  ctaText: {
    fontSize: FONTS.sizes.lg,
    fontFamily: FONTS.family.semibold,
    color: COLORS.white,
  },
  disclaimer: {
    fontSize: FONTS.sizes.sm,
    fontFamily: FONTS.family.regular,
    color: COLORS.textMuted,
    marginTop: 16,
    textAlign: 'center',
  },
});
