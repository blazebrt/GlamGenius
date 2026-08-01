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
import { useConfigStore } from '../../src/store/configStore';
import { COLORS, FONTS, SPACING, RADIUS, SHADOWS } from '../../src/theme/colors';

// "Go Plus" used to sit here and lead to a paywall that could only refuse the
// payment. The membership entry stays — people should be able to see what they
// have — but it no longer sells anything while billing is off.
const QUICK_ACTIONS = [
  { id: 'scan', icon: 'scan-outline', label: 'Skin check', route: '/(tabs)/scan-tab', color: COLORS.primary },
  { id: 'plan', icon: 'shirt-outline', label: 'Outfit plan', route: '/get-advice', color: COLORS.accent },
  { id: 'quiz', icon: 'clipboard-outline', label: 'Stylist quiz', route: '/style-quiz', color: COLORS.info },
];

const MEMBERSHIP_ACTION = {
  id: 'membership',
  icon: 'sparkles-outline',
  label: 'Membership',
  route: '/subscription',
  color: COLORS.warning,
};

const UPGRADE_ACTION = {
  id: 'plus',
  icon: 'diamond-outline',
  label: 'Go Plus',
  route: '/subscription',
  color: COLORS.warning,
};

const TIPS = [
  { tip: 'Add height & weight in Profile — your stylist uses them with skin tone for better outfit fits.', source: 'Stylist tip' },
  { tip: 'SPF every morning helps skin look more even — and your clothing colours photograph better.', source: 'Everyday care' },
  { tip: 'Amla, guava, and seasonal citrus are easy vitamin C options in Indian kitchens.', source: 'Food tip' },
  { tip: 'If skin looks oily, check labels for niacinamide or salicylic acid — start slow.', source: 'Label tip' },
  { tip: 'Deep teal, mustard, and maroon often flatter warm wheatish to medium tones.', source: 'Colour tip' },
];

const SALON_PREVIEWS = [
  { id: '1', name: 'Hair spa', for: 'Healthier-looking shine' },
  { id: '4', name: 'Manicure', for: 'Hand skin & nails' },
  { id: '2', name: 'Cleanup facial', for: 'Fresher-looking face' },
];

export default function HomeScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { user, fetchUser, refreshSubscription } = useUserStore();
  const [tipIndex, setTipIndex] = useState(0);
  const billingAvailable = useConfigStore((s) => s.billingAvailable);
  const loadConfig = useConfigStore((s) => s.load);
  const configLoaded = useConfigStore((s) => s.loaded);

  useEffect(() => {
    fetchUser();
    refreshSubscription();
    if (!configLoaded) void loadConfig();
    setTipIndex(new Date().getHours() % TIPS.length);
  }, []);

  const quickActions = [
    ...QUICK_ACTIONS,
    billingAvailable() ? UPGRADE_ACTION : MEMBERSHIP_ACTION,
  ];

  const greeting = () => {
    const h = new Date().getHours();
    if (h < 12) return 'Good morning';
    if (h < 17) return 'Good afternoon';
    return 'Good evening';
  };

  // "Unlimited checks" was never true — invited members have a monthly
  // allowance like everyone else, it is just a larger one. Show the real number.
  const plan = billingAvailable() && user?.plan === 'plus' ? 'Plus' : 'Private beta';
  const remaining =
    typeof user?.scans_remaining_free === 'number'
      ? `${user.scans_remaining_free} checks left this month`
      : 'Checks included';

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <ScrollView showsVerticalScrollIndicator={false}>
        <Animated.View entering={FadeIn} style={styles.header}>
          <View>
            <Text style={styles.greeting}>{greeting()}</Text>
            <Text style={styles.userName}>{user?.name || 'Welcome'}</Text>
          </View>
          <TouchableOpacity style={styles.profileButton} onPress={() => router.push('/(tabs)/profile')}>
            <Ionicons name="person-outline" size={22} color={COLORS.primary} />
          </TouchableOpacity>
        </Animated.View>

        <Animated.View entering={FadeInDown.delay(80)} style={styles.planChip}>
          <Ionicons name="leaf-outline" size={16} color={COLORS.primary} />
          <Text style={styles.planChipText}>{plan} · {remaining}</Text>
          {billingAvailable() && user?.plan !== 'plus' && (
            <TouchableOpacity onPress={() => router.push('/subscription')}>
              <Text style={styles.upgradeText}>Upgrade</Text>
            </TouchableOpacity>
          )}
        </Animated.View>

        <Animated.View entering={FadeInDown.delay(120)} style={styles.tipCard}>
          <Text style={styles.tipBadge}>TODAY&apos;S TIP</Text>
          <Text style={styles.tipText}>{TIPS[tipIndex].tip}</Text>
          <Text style={styles.tipSource}>— {TIPS[tipIndex].source}</Text>
        </Animated.View>

        <Animated.View entering={FadeInDown.delay(180)} style={styles.section}>
          <Text style={styles.sectionTitle}>Quick actions</Text>
          <View style={styles.actionsGrid}>
            {quickActions.map((action) => (
              <TouchableOpacity
                key={action.id}
                style={styles.actionCard}
                onPress={() => router.push(action.route as any)}
                activeOpacity={0.75}
              >
                <View style={[styles.actionIcon, { backgroundColor: `${action.color}18` }]}>
                  <Ionicons name={action.icon as any} size={22} color={action.color} />
                </View>
                <Text style={styles.actionLabel}>{action.label}</Text>
              </TouchableOpacity>
            ))}
          </View>
        </Animated.View>

        <Animated.View entering={FadeInDown.delay(240)} style={styles.section}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>Salon ideas</Text>
            <TouchableOpacity onPress={() => router.push('/services')}>
              <Text style={styles.seeAll}>Browse</Text>
            </TouchableOpacity>
          </View>
          <Text style={styles.sectionSub}>Suggestions only — no booking or payment in the app.</Text>
          {SALON_PREVIEWS.map((s) => (
            <TouchableOpacity
              key={s.id}
              style={styles.ideaRow}
              onPress={() => router.push({ pathname: '/service-details', params: { id: s.id } })}
            >
              <View style={{ flex: 1 }}>
                <Text style={styles.ideaName}>{s.name}</Text>
                <Text style={styles.ideaFor}>{s.for}</Text>
              </View>
              <Ionicons name="chevron-forward" size={18} color={COLORS.textMuted} />
            </TouchableOpacity>
          ))}
        </Animated.View>

        <Animated.View entering={FadeInDown.delay(300)} style={styles.ctaBanner}>
          <View style={styles.ctaIcon}>
            <Ionicons name="scan" size={26} color={COLORS.white} />
          </View>
          <View style={{ flex: 1 }}>
            <Text style={styles.ctaTitle}>Start a skin or hair check</Text>
            <Text style={styles.ctaSubtitle}>Colours, care ingredients, foods & salon ideas</Text>
          </View>
          <TouchableOpacity style={styles.ctaButton} onPress={() => router.push('/(tabs)/scan-tab')}>
            <Text style={styles.ctaButtonText}>Go</Text>
          </TouchableOpacity>
        </Animated.View>

        <View style={{ height: 120 }} />
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.backgroundSecondary },
  header: {
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center',
    paddingHorizontal: SPACING.lg, paddingVertical: SPACING.md,
  },
  greeting: { fontFamily: FONTS.family.body, fontSize: 14, color: COLORS.textSecondary },
  userName: { fontFamily: FONTS.family.heading, fontSize: 26, color: COLORS.textPrimary, marginTop: 2 },
  profileButton: {
    width: 44, height: 44, borderRadius: 22, backgroundColor: COLORS.card,
    alignItems: 'center', justifyContent: 'center', borderWidth: 1, borderColor: COLORS.border, ...SHADOWS.sm,
  },
  planChip: {
    marginHorizontal: SPACING.lg, marginBottom: SPACING.md, paddingHorizontal: 14, paddingVertical: 10,
    backgroundColor: COLORS.primaryLight, borderRadius: RADIUS.full, flexDirection: 'row', alignItems: 'center', gap: 8,
  },
  planChipText: { flex: 1, fontFamily: FONTS.family.bodyMedium, fontSize: 13, color: COLORS.primaryDark },
  upgradeText: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.accent },
  tipCard: {
    marginHorizontal: SPACING.lg, backgroundColor: COLORS.card, borderRadius: RADIUS.lg, padding: SPACING.lg,
    borderWidth: 1, borderColor: COLORS.border, ...SHADOWS.sm,
  },
  tipBadge: { fontFamily: FONTS.family.bodySemibold, fontSize: 11, color: COLORS.primary, letterSpacing: 1.2 },
  tipText: { fontFamily: FONTS.family.body, fontSize: 15, color: COLORS.textPrimary, marginTop: 8, lineHeight: 22 },
  tipSource: { fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.textMuted, marginTop: 8 },
  section: { marginTop: SPACING.xl, paddingHorizontal: SPACING.lg },
  sectionHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  sectionTitle: { fontFamily: FONTS.family.headingMedium, fontSize: 20, color: COLORS.textPrimary },
  sectionSub: { fontFamily: FONTS.family.body, fontSize: 13, color: COLORS.textSecondary, marginTop: 4, marginBottom: 12 },
  seeAll: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.primary },
  actionsGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 12, marginTop: 12 },
  actionCard: {
    width: '47%', backgroundColor: COLORS.card, borderRadius: RADIUS.lg, padding: 14,
    borderWidth: 1, borderColor: COLORS.border, ...SHADOWS.sm,
  },
  actionIcon: { width: 42, height: 42, borderRadius: 12, alignItems: 'center', justifyContent: 'center', marginBottom: 10 },
  actionLabel: { fontFamily: FONTS.family.bodySemibold, fontSize: 14, color: COLORS.textPrimary },
  ideaRow: {
    flexDirection: 'row', alignItems: 'center', backgroundColor: COLORS.card, borderRadius: RADIUS.md,
    padding: 14, marginBottom: 8, borderWidth: 1, borderColor: COLORS.border,
  },
  ideaName: { fontFamily: FONTS.family.bodySemibold, fontSize: 15, color: COLORS.textPrimary },
  ideaFor: { fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.textSecondary, marginTop: 2 },
  ctaBanner: {
    marginHorizontal: SPACING.lg, marginTop: SPACING.xl, backgroundColor: COLORS.primary, borderRadius: RADIUS.xl,
    padding: SPACING.lg, flexDirection: 'row', alignItems: 'center', gap: 12,
  },
  ctaIcon: {
    width: 48, height: 48, borderRadius: 14, backgroundColor: 'rgba(255,255,255,0.15)',
    alignItems: 'center', justifyContent: 'center',
  },
  ctaTitle: { fontFamily: FONTS.family.bodySemibold, fontSize: 15, color: COLORS.white },
  ctaSubtitle: { fontFamily: FONTS.family.body, fontSize: 12, color: 'rgba(255,255,255,0.8)', marginTop: 2 },
  ctaButton: { backgroundColor: COLORS.white, paddingHorizontal: 14, paddingVertical: 8, borderRadius: RADIUS.full },
  ctaButtonText: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.primary },
});
