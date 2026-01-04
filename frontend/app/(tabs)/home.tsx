import React, { useEffect, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
} from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import Animated, { FadeIn, FadeInDown } from 'react-native-reanimated';
import { useUserStore } from '../../src/store/userStore';
import { COLORS, FONTS } from '../../src/theme/colors';

const QUICK_ACTIONS = [
  { id: 'advice', icon: 'sparkles', label: 'Get Advice', route: '/get-advice' },
  { id: 'scan', icon: 'scan', label: 'AI Scan', route: '/(tabs)/scan-tab' },
  { id: 'quiz', icon: 'clipboard', label: 'Style Quiz', route: '/style-quiz' },
  { id: 'history', icon: 'time', label: 'History', route: '/(tabs)/history' },
];

const FEATURED_SERVICES = [
  { id: '1', name: 'Haircut & Styling', price: '₹499', duration: '45 min', icon: 'cut' },
  { id: '2', name: 'Facial Treatment', price: '₹799', duration: '60 min', icon: 'flower' },
  { id: '3', name: 'Hair Spa', price: '₹999', duration: '75 min', icon: 'leaf' },
];

const BEAUTY_TIPS = [
  "Drink 8 glasses of water daily for glowing skin",
  "Apply sunscreen even on cloudy days - UV rays penetrate clouds",
  "Use a silk pillowcase to prevent hair breakage and frizz",
  "Massage your scalp for 5 minutes before washing to boost growth",
  "Remove makeup before sleeping for clear, healthy skin",
  "Trim hair every 6-8 weeks to prevent split ends",
  "Apply hair oil 30 minutes before washing for deep nourishment",
  "Use lukewarm water, not hot, when washing your face",
  "Always pat dry your face - never rub with a towel",
  "Include omega-3 fatty acids in your diet for shiny hair",
  "Don't skip moisturizer even if you have oily skin",
  "Apply serum while skin is slightly damp for better absorption",
  "Sleep 7-8 hours for natural skin repair and regeneration",
  "Use a wide-tooth comb on wet hair to prevent breakage",
  "Exfoliate your scalp once a week for healthier hair growth",
  "Green tea is excellent for reducing puffiness and dark circles",
  "Apply conditioner only to hair ends, not roots",
  "Vitamin C serums work best when applied in the morning",
  "Never skip the neck when applying skincare products",
  "Use retinol at night - it makes skin sensitive to sunlight",
  "Coconut oil makes an excellent natural makeup remover",
  "Let hair air dry when possible to minimize heat damage",
  "Hyaluronic acid helps lock in moisture for plump skin",
  "Eat foods rich in biotin for stronger hair and nails",
];

export default function HomeScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { user, fetchUser } = useUserStore();
  const [tipIndex, setTipIndex] = useState(0);

  useEffect(() => {
    fetchUser();
    // Set initial tip based on hour
    const hour = new Date().getHours();
    setTipIndex(hour % BEAUTY_TIPS.length);
    
    // Update tip every hour (3600000ms = 1 hour)
    const tipInterval = setInterval(() => {
      const currentHour = new Date().getHours();
      setTipIndex(currentHour % BEAUTY_TIPS.length);
    }, 3600000);
    
    return () => clearInterval(tipInterval);
  }, []);

  const getGreeting = () => {
    const hour = new Date().getHours();
    if (hour < 12) return 'Good Morning';
    if (hour < 17) return 'Good Afternoon';
    return 'Good Evening';
  };

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <ScrollView showsVerticalScrollIndicator={false}>
        {/* Header */}
        <Animated.View entering={FadeIn} style={styles.header}>
          <View>
            <Text style={styles.greeting}>{getGreeting()}</Text>
            <Text style={styles.userName}>{user?.name || 'Welcome!'}</Text>
          </View>
          <TouchableOpacity
            style={styles.profileButton}
            onPress={() => router.push('/(tabs)/profile')}
          >
            <Ionicons name="person" size={22} color={COLORS.black} />
          </TouchableOpacity>
        </Animated.View>

        {/* Tip of the Hour */}
        <Animated.View entering={FadeInDown.delay(100)} style={styles.tipCard}>
          <View style={styles.tipHeader}>
            <Ionicons name="bulb-outline" size={20} color={COLORS.black} />
            <Text style={styles.tipLabel}>Tip of the Hour</Text>
          </View>
          <Text style={styles.tipText}>{BEAUTY_TIPS[tipIndex]}</Text>
        </Animated.View>

        {/* Quick Actions */}
        <Animated.View entering={FadeInDown.delay(200)} style={styles.section}>
          <Text style={styles.sectionTitle}>Quick Actions</Text>
          <View style={styles.actionsGrid}>
            {QUICK_ACTIONS.map((action) => (
              <TouchableOpacity
                key={action.id}
                style={styles.actionCard}
                onPress={() => router.push(action.route as any)}
              >
                <View style={styles.actionIcon}>
                  <Ionicons name={action.icon as any} size={26} color={COLORS.black} />
                </View>
                <Text style={styles.actionLabel}>{action.label}</Text>
              </TouchableOpacity>
            ))}
          </View>
        </Animated.View>

        {/* Featured Services */}
        <Animated.View entering={FadeInDown.delay(300)} style={styles.section}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>Popular Services</Text>
            <TouchableOpacity onPress={() => router.push('/(tabs)/services')}>
              <Text style={styles.seeAll}>See All</Text>
            </TouchableOpacity>
          </View>
          <ScrollView horizontal showsHorizontalScrollIndicator={false}>
            {FEATURED_SERVICES.map((service) => (
              <TouchableOpacity key={service.id} style={styles.serviceCard}>
                <View style={styles.serviceIcon}>
                  <Ionicons name={service.icon as any} size={24} color={COLORS.black} />
                </View>
                <Text style={styles.serviceName}>{service.name}</Text>
                <View style={styles.serviceMeta}>
                  <Text style={styles.servicePrice}>{service.price}</Text>
                  <Text style={styles.serviceDuration}>{service.duration}</Text>
                </View>
              </TouchableOpacity>
            ))}
          </ScrollView>
        </Animated.View>

        {/* CTA Banner */}
        <Animated.View entering={FadeInDown.delay(400)} style={styles.ctaBanner}>
          <View style={styles.ctaContent}>
            <Text style={styles.ctaTitle}>Get Personalized Advice</Text>
            <Text style={styles.ctaSubtitle}>AI-powered recommendations just for you</Text>
            <TouchableOpacity
              style={styles.ctaButton}
              onPress={() => router.push('/get-advice')}
            >
              <Text style={styles.ctaButtonText}>Start Now</Text>
              <Ionicons name="arrow-forward" size={18} color={COLORS.white} />
            </TouchableOpacity>
          </View>
        </Animated.View>

        <View style={{ height: 100 }} />
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: COLORS.background,
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: 20,
    paddingVertical: 16,
  },
  greeting: {
    fontSize: FONTS.sizes.base,
    fontFamily: FONTS.family.regular,
    color: COLORS.textSecondary,
  },
  userName: {
    fontSize: FONTS.sizes['2xl'],
    fontFamily: FONTS.family.bold,
    color: COLORS.black,
    marginTop: 2,
  },
  profileButton: {
    width: 48,
    height: 48,
    borderRadius: 24,
    backgroundColor: COLORS.primaryLight,
    justifyContent: 'center',
    alignItems: 'center',
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  tipCard: {
    marginHorizontal: 20,
    backgroundColor: COLORS.backgroundSecondary,
    borderRadius: 12,
    padding: 18,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  tipHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginBottom: 10,
  },
  tipLabel: {
    fontSize: FONTS.sizes.sm,
    fontFamily: FONTS.family.semibold,
    color: COLORS.black,
    textTransform: 'uppercase',
    letterSpacing: 1,
  },
  tipText: {
    fontSize: FONTS.sizes.base,
    fontFamily: FONTS.family.regular,
    color: COLORS.textPrimary,
    lineHeight: 24,
  },
  section: {
    marginTop: 28,
    paddingHorizontal: 20,
  },
  sectionHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 16,
  },
  sectionTitle: {
    fontSize: FONTS.sizes.xl,
    fontFamily: FONTS.family.bold,
    color: COLORS.black,
    marginBottom: 16,
  },
  seeAll: {
    fontSize: FONTS.sizes.base,
    fontFamily: FONTS.family.medium,
    color: COLORS.textSecondary,
    textDecorationLine: 'underline',
  },
  actionsGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 12,
  },
  actionCard: {
    width: '47%',
    backgroundColor: COLORS.white,
    borderRadius: 12,
    padding: 18,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  actionIcon: {
    width: 56,
    height: 56,
    borderRadius: 28,
    backgroundColor: COLORS.primaryLight,
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: 12,
  },
  actionLabel: {
    fontSize: FONTS.sizes.base,
    fontFamily: FONTS.family.semibold,
    color: COLORS.textPrimary,
  },
  serviceCard: {
    width: 170,
    backgroundColor: COLORS.white,
    borderRadius: 12,
    padding: 18,
    marginRight: 12,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  serviceIcon: {
    width: 52,
    height: 52,
    borderRadius: 26,
    backgroundColor: COLORS.primaryLight,
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: 14,
  },
  serviceName: {
    fontSize: FONTS.sizes.base,
    fontFamily: FONTS.family.semibold,
    color: COLORS.textPrimary,
    marginBottom: 10,
  },
  serviceMeta: {
    flexDirection: 'row',
    justifyContent: 'space-between',
  },
  servicePrice: {
    fontSize: FONTS.sizes.base,
    fontFamily: FONTS.family.bold,
    color: COLORS.black,
  },
  serviceDuration: {
    fontSize: FONTS.sizes.sm,
    fontFamily: FONTS.family.regular,
    color: COLORS.textMuted,
  },
  ctaBanner: {
    marginHorizontal: 20,
    marginTop: 28,
    backgroundColor: COLORS.black,
    borderRadius: 16,
    padding: 24,
  },
  ctaContent: {
    alignItems: 'flex-start',
  },
  ctaTitle: {
    fontSize: FONTS.sizes.xl,
    fontFamily: FONTS.family.bold,
    color: COLORS.white,
  },
  ctaSubtitle: {
    fontSize: FONTS.sizes.base,
    fontFamily: FONTS.family.regular,
    color: 'rgba(255,255,255,0.7)',
    marginTop: 6,
    marginBottom: 18,
  },
  ctaButton: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(255,255,255,0.15)',
    paddingHorizontal: 22,
    paddingVertical: 14,
    borderRadius: 24,
    gap: 8,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.3)',
  },
  ctaButtonText: {
    fontSize: FONTS.sizes.base,
    fontFamily: FONTS.family.semibold,
    color: COLORS.white,
  },
});
