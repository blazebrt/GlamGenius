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
import { COLORS, FONTS, SPACING, RADIUS, SHADOWS } from '../../src/theme/colors';

/**
 * HOME SCREEN - Dashboard for Medical Beauty
 * 
 * Design Philosophy:
 * - Clean medical aesthetic with warm touches
 * - Playfair Display for premium headlines
 * - Inter for clear data presentation
 * - Soft blue accents for clinical trust
 */

const QUICK_ACTIONS = [
  { id: 'scan', icon: 'scan-outline', label: 'AI Scan', route: '/(tabs)/scan-tab', color: COLORS.primary },
  { id: 'advice', icon: 'sparkles-outline', label: 'Get Advice', route: '/get-advice', color: COLORS.success },
  { id: 'quiz', icon: 'clipboard-outline', label: 'Style Quiz', route: '/style-quiz', color: COLORS.warning },
  { id: 'history', icon: 'time-outline', label: 'History', route: '/(tabs)/history', color: COLORS.accent },
];

const FEATURED_SERVICES = [
  { id: '1', name: 'Scalp Treatment', price: '₹1,499', duration: '60 min', icon: 'fitness-outline', tag: 'Popular' },
  { id: '2', name: 'HydraFacial', price: '₹2,999', duration: '75 min', icon: 'diamond-outline', tag: 'Premium' },
  { id: '3', name: 'Hair Spa', price: '₹999', duration: '45 min', icon: 'cut-outline', tag: null },
];

const BEAUTY_TIPS = [
  { tip: "SPF 50 is essential even on cloudy days", source: "Dermatology Journal 2026" },
  { tip: "Scalp massage for 5 mins boosts follicle health by 23%", source: "Trichology Research" },
  { tip: "Hydration affects skin elasticity within 72 hours", source: "Clinical Skincare" },
  { tip: "Retinol should only be used at night", source: "AAD Guidelines" },
  { tip: "Silk pillowcases reduce hair breakage by 40%", source: "Hair Science Institute" },
  { tip: "Double cleansing removes 90% more impurities", source: "Skin Health Weekly" },
  { tip: "Vitamin C serums are most effective before 10AM", source: "Cosmetic Chemistry" },
  { tip: "Cold water closes pores after cleansing", source: "Aesthetic Medicine" },
];

export default function HomeScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { user, fetchUser } = useUserStore();
  const [currentTip, setCurrentTip] = useState(0);

  useEffect(() => {
    fetchUser();
    // Rotate tips based on hour
    const hour = new Date().getHours();
    setCurrentTip(hour % BEAUTY_TIPS.length);
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
            <Ionicons name="person-outline" size={22} color={COLORS.primary} />
          </TouchableOpacity>
        </Animated.View>

        {/* Medical Tip Card */}
        <Animated.View entering={FadeInDown.delay(100)} style={styles.tipCard}>
          <View style={styles.tipBadge}>
            <Ionicons name="medical-outline" size={14} color={COLORS.primary} />
            <Text style={styles.tipBadgeText}>CLINICAL TIP</Text>
          </View>
          <Text style={styles.tipText}>{BEAUTY_TIPS[currentTip].tip}</Text>
          <Text style={styles.tipSource}>— {BEAUTY_TIPS[currentTip].source}</Text>
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
                activeOpacity={0.7}
              >
                <View style={[styles.actionIcon, { backgroundColor: `${action.color}15` }]}>
                  <Ionicons name={action.icon as any} size={24} color={action.color} />
                </View>
                <Text style={styles.actionLabel}>{action.label}</Text>
              </TouchableOpacity>
            ))}
          </View>
        </Animated.View>

        {/* Featured Services */}
        <Animated.View entering={FadeInDown.delay(300)} style={styles.section}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>Popular Treatments</Text>
            <TouchableOpacity onPress={() => router.push('/(tabs)/services')}>
              <Text style={styles.seeAllText}>View All</Text>
            </TouchableOpacity>
          </View>
          <ScrollView horizontal showsHorizontalScrollIndicator={false}>
            {FEATURED_SERVICES.map((service) => (
              <TouchableOpacity key={service.id} style={styles.serviceCard} activeOpacity={0.7}>
                {service.tag && (
                  <View style={styles.serviceTag}>
                    <Text style={styles.serviceTagText}>{service.tag}</Text>
                  </View>
                )}
                <View style={styles.serviceIcon}>
                  <Ionicons name={service.icon as any} size={24} color={COLORS.primary} />
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
          <View style={styles.ctaIcon}>
            <Ionicons name="scan" size={28} color={COLORS.white} />
          </View>
          <View style={styles.ctaContent}>
            <Text style={styles.ctaTitle}>Get Your Diagnosis</Text>
            <Text style={styles.ctaSubtitle}>AI-powered skin & scalp analysis</Text>
          </View>
          <TouchableOpacity
            style={styles.ctaButton}
            onPress={() => router.push('/(tabs)/scan-tab')}
          >
            <Text style={styles.ctaButtonText}>Start</Text>
            <Ionicons name="arrow-forward" size={16} color={COLORS.primary} />
          </TouchableOpacity>
        </Animated.View>

        <View style={{ height: 120 }} />
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: COLORS.backgroundSecondary,
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: SPACING.lg,
    paddingVertical: SPACING.md,
    backgroundColor: COLORS.background,
  },
  greeting: {
    fontSize: FONTS.sizes.body,
    fontFamily: FONTS.family.body,
    color: COLORS.textSecondary,
  },
  userName: {
    fontSize: FONTS.sizes.h2,
    fontFamily: FONTS.family.heading,
    color: COLORS.textPrimary,
    marginTop: 2,
  },
  profileButton: {
    width: 48,
    height: 48,
    borderRadius: 24,
    backgroundColor: COLORS.primaryLight,
    justifyContent: 'center',
    alignItems: 'center',
  },
  tipCard: {
    marginHorizontal: SPACING.lg,
    marginTop: SPACING.md,
    backgroundColor: COLORS.background,
    borderRadius: RADIUS.lg,
    padding: SPACING.lg,
    borderLeftWidth: 4,
    borderLeftColor: COLORS.primary,
    ...SHADOWS.sm,
  },
  tipBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    marginBottom: SPACING.sm,
  },
  tipBadgeText: {
    fontSize: FONTS.sizes.micro,
    fontFamily: FONTS.family.bodySemibold,
    color: COLORS.primary,
    letterSpacing: 1,
  },
  tipText: {
    fontSize: FONTS.sizes.bodyLg,
    fontFamily: FONTS.family.bodyMedium,
    color: COLORS.textPrimary,
    lineHeight: 26,
  },
  tipSource: {
    fontSize: FONTS.sizes.caption,
    fontFamily: FONTS.family.body,
    color: COLORS.textMuted,
    marginTop: SPACING.sm,
    fontStyle: 'italic',
  },
  section: {
    marginTop: SPACING.xl,
    paddingHorizontal: SPACING.lg,
  },
  sectionHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: SPACING.md,
  },
  sectionTitle: {
    fontSize: FONTS.sizes.h3,
    fontFamily: FONTS.family.heading,
    color: COLORS.textPrimary,
    marginBottom: SPACING.md,
  },
  seeAllText: {
    fontSize: FONTS.sizes.body,
    fontFamily: FONTS.family.bodyMedium,
    color: COLORS.primary,
  },
  actionsGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: SPACING.md,
  },
  actionCard: {
    width: '47%',
    backgroundColor: COLORS.background,
    borderRadius: RADIUS.lg,
    padding: SPACING.lg,
    alignItems: 'center',
    ...SHADOWS.sm,
  },
  actionIcon: {
    width: 56,
    height: 56,
    borderRadius: 28,
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: SPACING.sm,
  },
  actionLabel: {
    fontSize: FONTS.sizes.body,
    fontFamily: FONTS.family.bodySemibold,
    color: COLORS.textPrimary,
  },
  serviceCard: {
    width: 160,
    backgroundColor: COLORS.background,
    borderRadius: RADIUS.lg,
    padding: SPACING.md,
    marginRight: SPACING.md,
    ...SHADOWS.sm,
  },
  serviceTag: {
    position: 'absolute',
    top: SPACING.sm,
    right: SPACING.sm,
    backgroundColor: COLORS.primaryLight,
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: RADIUS.sm,
  },
  serviceTagText: {
    fontSize: FONTS.sizes.micro,
    fontFamily: FONTS.family.bodySemibold,
    color: COLORS.primary,
  },
  serviceIcon: {
    width: 48,
    height: 48,
    borderRadius: 24,
    backgroundColor: COLORS.primaryLight,
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: SPACING.sm,
  },
  serviceName: {
    fontSize: FONTS.sizes.body,
    fontFamily: FONTS.family.bodySemibold,
    color: COLORS.textPrimary,
    marginBottom: SPACING.sm,
  },
  serviceMeta: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  servicePrice: {
    fontSize: FONTS.sizes.body,
    fontFamily: FONTS.family.bodyBold,
    color: COLORS.primary,
  },
  serviceDuration: {
    fontSize: FONTS.sizes.caption,
    fontFamily: FONTS.family.body,
    color: COLORS.textMuted,
  },
  ctaBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    marginHorizontal: SPACING.lg,
    marginTop: SPACING.xl,
    backgroundColor: COLORS.primary,
    borderRadius: RADIUS.lg,
    padding: SPACING.lg,
  },
  ctaIcon: {
    width: 52,
    height: 52,
    borderRadius: 26,
    backgroundColor: 'rgba(255,255,255,0.2)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  ctaContent: {
    flex: 1,
    marginLeft: SPACING.md,
  },
  ctaTitle: {
    fontSize: FONTS.sizes.bodyLg,
    fontFamily: FONTS.family.bodySemibold,
    color: COLORS.white,
  },
  ctaSubtitle: {
    fontSize: FONTS.sizes.bodySm,
    fontFamily: FONTS.family.body,
    color: 'rgba(255,255,255,0.8)',
    marginTop: 2,
  },
  ctaButton: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: COLORS.white,
    paddingVertical: 10,
    paddingHorizontal: 16,
    borderRadius: RADIUS.full,
    gap: 6,
  },
  ctaButtonText: {
    fontSize: FONTS.sizes.body,
    fontFamily: FONTS.family.bodySemibold,
    color: COLORS.primary,
  },
});
