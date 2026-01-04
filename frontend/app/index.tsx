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
import { COLORS } from '../src/theme/colors';

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
        // Auto-navigate to home if user exists
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
      {/* Background Decoration */}
      <View style={styles.decorCircle1} />
      <View style={styles.decorCircle2} />

      {/* Logo and Header */}
      <Animated.View entering={FadeIn.delay(200)} style={styles.headerContainer}>
        <View style={styles.logoContainer}>
          <Ionicons name="sparkles" size={40} color={COLORS.primary} />
        </View>
        <Text style={styles.logoText}>GlamGenius</Text>
        <Text style={styles.tagline}>Premium Salon Advisor</Text>
      </Animated.View>

      {/* Features */}
      <Animated.View entering={FadeInDown.delay(400)} style={styles.featuresContainer}>
        <FeatureItem
          icon="scan-outline"
          title="AI Skin & Hair Analysis"
          description="Advanced scanning for personalized insights"
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
              <Ionicons name="arrow-forward" size={20} color={COLORS.white} />
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
        <Ionicons name={icon as any} size={24} color={COLORS.primary} />
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
    backgroundColor: '#0A0A0A',
    paddingHorizontal: 24,
  },
  loadingContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
  },
  decorCircle1: {
    position: 'absolute',
    top: -100,
    right: -100,
    width: 300,
    height: 300,
    borderRadius: 150,
    backgroundColor: 'rgba(212, 175, 55, 0.05)',
  },
  decorCircle2: {
    position: 'absolute',
    bottom: -50,
    left: -100,
    width: 250,
    height: 250,
    borderRadius: 125,
    backgroundColor: 'rgba(212, 175, 55, 0.03)',
  },
  headerContainer: {
    alignItems: 'center',
    marginTop: height * 0.08,
  },
  logoContainer: {
    width: 80,
    height: 80,
    borderRadius: 40,
    backgroundColor: 'rgba(212, 175, 55, 0.1)',
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: 16,
    borderWidth: 1,
    borderColor: 'rgba(212, 175, 55, 0.3)',
  },
  logoText: {
    fontSize: 36,
    fontWeight: '700',
    color: '#FFFFFF',
    letterSpacing: 1,
  },
  tagline: {
    fontSize: 16,
    color: '#D4AF37',
    marginTop: 8,
    letterSpacing: 2,
    textTransform: 'uppercase',
  },
  featuresContainer: {
    marginTop: height * 0.08,
    gap: 20,
  },
  featureItem: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(255, 255, 255, 0.03)',
    padding: 16,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.05)',
  },
  featureIconContainer: {
    width: 48,
    height: 48,
    borderRadius: 24,
    backgroundColor: 'rgba(212, 175, 55, 0.1)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  featureTextContainer: {
    marginLeft: 16,
    flex: 1,
  },
  featureTitle: {
    fontSize: 16,
    fontWeight: '600',
    color: '#FFFFFF',
    marginBottom: 4,
  },
  featureDescription: {
    fontSize: 13,
    color: 'rgba(255, 255, 255, 0.6)',
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
    backgroundColor: '#D4AF37',
    paddingVertical: 16,
    paddingHorizontal: 32,
    borderRadius: 30,
    width: '100%',
    gap: 8,
  },
  ctaText: {
    fontSize: 18,
    fontWeight: '600',
    color: '#0A0A0A',
  },
  disclaimer: {
    fontSize: 12,
    color: 'rgba(255, 255, 255, 0.4)',
    marginTop: 16,
    textAlign: 'center',
  },
});
