/**
 * Membership screen.
 *
 * This screen used to sell ₹249/month and ₹1999/year with "Start monthly" and
 * "Start yearly" buttons. The backend refuses every subscription on principle
 * while the product is invite-only, so tapping either one created an order, got
 * a 403, and told the user **"Payment failed. Please try again."**
 *
 * Nothing was ever going to succeed. The prices are no longer shown as
 * purchasable and the buttons are gone whenever the backend says billing is
 * unavailable. The billing code behind them is untouched — flip
 * SUBSCRIPTIONS_AVAILABLE and the plans come back.
 */
import React, { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { api } from '../src/services/api';
import { useUserStore } from '../src/store/userStore';
import { useConfigStore } from '../src/store/configStore';
import { COLORS, FONTS, RADIUS, SHADOWS, SPACING } from '../src/theme/colors';

export default function SubscriptionScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { userId, refreshSubscription } = useUserStore();
  const [status, setStatus] = useState<any>(null);
  const [loading, setLoading] = useState(false);

  const config = useConfigStore((s) => s.config);
  const loadConfig = useConfigStore((s) => s.load);
  const billingAvailable = useConfigStore((s) => s.billingAvailable);
  const betaMessage = useConfigStore((s) => s.betaMessage);

  useEffect(() => {
    if (!config) void loadConfig();
  }, [config, loadConfig]);

  useEffect(() => {
    if (!userId) return;
    (async () => {
      try {
        const response = await api.get('/subscription/status');
        setStatus(response.data);
      } catch {
        setStatus(null);
      }
    })();
  }, [userId]);

  // Two sources agree before anything is offered for sale: the app-wide config
  // and this user's own subscription status.
  const canBuy = billingAvailable() && status?.subscriptions_available !== false;

  const buy = async (plan: 'plus_monthly' | 'plus_yearly') => {
    if (!userId) {
      Alert.alert('Sign in needed', 'Create an account first.', [
        { text: 'OK', onPress: () => router.push('/(auth)/welcome') },
      ]);
      return;
    }
    setLoading(true);
    try {
      const order = await api.post('/subscription/create-order', { plan });
      await api.post(
        `/subscription/confirm?order_id=${order.data.order_id}&payment_method=upi`
      );
      await refreshSubscription();
      Alert.alert('Membership active', 'Thank you — your plan is now active.', [
        { text: 'Great', onPress: () => router.back() },
      ]);
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      Alert.alert(
        'Not available',
        detail?.message ?? 'This is not available right now.'
      );
    } finally {
      setLoading(false);
    }
  };

  const checksPerMonth = status?.invite_scans_per_month ?? status?.free_scans_per_month;

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <View style={styles.top}>
        <TouchableOpacity
          onPress={() => router.back()}
          accessibilityRole="button"
          accessibilityLabel="Go back"
        >
          <Ionicons name="arrow-back" size={22} color={COLORS.textPrimary} />
        </TouchableOpacity>
        <Text style={styles.topTitle}>Your membership</Text>
        <View style={{ width: 22 }} />
      </View>

      <ScrollView contentContainerStyle={{ padding: SPACING.lg, paddingBottom: 80 }}>
        {!canBuy ? (
          <View testID="beta-membership">
            <Text style={styles.title}>You are in the{'\n'}private beta</Text>
            <Text style={styles.subtitle}>{betaMessage()}</Text>

            <View style={[styles.card, styles.cardHighlight]}>
              <View style={styles.badgeRow}>
                <Ionicons name="sparkles" size={16} color={COLORS.primary} />
                <Text style={styles.badge}>INVITED MEMBER</Text>
              </View>
              <Text style={styles.planName}>Full access, included</Text>
              <Text style={styles.betaNote}>
                Everything in GlamGenius is switched on for you while we are in
                testing. There is nothing to buy and nothing to cancel.
              </Text>

              <Included text="Skin & hair checks with full results" />
              <Included text="Colour, fit and outfit guidance" />
              <Included text="Care routines and ingredient tips" />
              <Included text="Saved history and progress" />

              {typeof checksPerMonth === 'number' && (
                <View style={styles.allowanceRow}>
                  <Text style={styles.allowanceLabel}>Checks this month</Text>
                  <Text style={styles.allowanceValue}>
                    {status?.scans_used_this_month ?? 0} of {checksPerMonth} used
                  </Text>
                </View>
              )}
            </View>

            <Text style={styles.note}>
              When paid plans open, you will see the options here first. We will
              never charge a card without you choosing a plan.
            </Text>
          </View>
        ) : (
          <View testID="paid-plans">
            <Text style={styles.title}>Choose your plan</Text>
            <Text style={styles.subtitle}>
              Full access to checks, colour and fit guidance, and your saved history.
            </Text>

            {status?.plan === 'plus' && (
              <View style={styles.activeBanner}>
                <Text style={styles.activeText}>Your plan is active</Text>
              </View>
            )}

            <View style={styles.card}>
              <Text style={styles.planName}>Monthly</Text>
              <Text style={styles.price}>
                ₹{status?.plus_price_inr}
                <Text style={styles.per}> / month</Text>
              </Text>
              <Included text="Skin & hair checks" />
              <Included text="Full colour and food plans" />
              <Included text="History and trends" />
              <TouchableOpacity
                style={styles.cta}
                disabled={loading || status?.plan === 'plus'}
                onPress={() => buy('plus_monthly')}
                accessibilityRole="button"
                testID="buy-monthly"
              >
                {loading ? (
                  <ActivityIndicator color={COLORS.white} />
                ) : (
                  <Text style={styles.ctaText}>
                    {status?.plan === 'plus' ? 'Current plan' : 'Choose monthly'}
                  </Text>
                )}
              </TouchableOpacity>
            </View>

            <View style={[styles.card, styles.cardHighlight]}>
              <Text style={styles.badge}>BEST VALUE</Text>
              <Text style={styles.planName}>Yearly</Text>
              <Text style={styles.price}>
                ₹{status?.plus_yearly_inr}
                <Text style={styles.per}> / year</Text>
              </Text>
              <Included text="Everything in monthly" />
              <Included text="Save against paying month to month" />
              <TouchableOpacity
                style={styles.cta}
                disabled={loading || status?.plan === 'plus'}
                onPress={() => buy('plus_yearly')}
                accessibilityRole="button"
                testID="buy-yearly"
              >
                <Text style={styles.ctaText}>
                  {status?.plan === 'plus' ? 'Current plan' : 'Choose yearly'}
                </Text>
              </TouchableOpacity>
            </View>

            <Text style={styles.note}>
              This pays for the app coach — not for salon services.
            </Text>
          </View>
        )}
      </ScrollView>
    </View>
  );
}

function Included({ text }: { text: string }) {
  return (
    <View style={styles.featureRow}>
      <Ionicons name="checkmark-circle" size={18} color={COLORS.primary} />
      <Text style={styles.featureText}>{text}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.background },
  top: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: SPACING.lg,
    paddingVertical: 12,
  },
  topTitle: {
    fontFamily: FONTS.family.bodySemibold,
    fontSize: 16,
    color: COLORS.textPrimary,
  },
  title: {
    fontFamily: FONTS.family.heading,
    fontSize: 30,
    color: COLORS.textPrimary,
    lineHeight: 36,
  },
  subtitle: {
    fontFamily: FONTS.family.body,
    fontSize: 14,
    color: COLORS.textSecondary,
    marginTop: 10,
    lineHeight: 21,
    marginBottom: 20,
  },
  activeBanner: {
    backgroundColor: COLORS.primaryLight,
    padding: 12,
    borderRadius: RADIUS.md,
    marginBottom: 12,
  },
  activeText: {
    fontFamily: FONTS.family.bodySemibold,
    color: COLORS.primary,
    textAlign: 'center',
  },
  card: {
    backgroundColor: COLORS.card,
    borderRadius: RADIUS.xl,
    padding: 18,
    marginBottom: 14,
    borderWidth: 1,
    borderColor: COLORS.border,
    ...SHADOWS.sm,
  },
  cardHighlight: { borderColor: COLORS.primary, borderWidth: 1.5 },
  badgeRow: { flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 6 },
  badge: {
    fontFamily: FONTS.family.bodySemibold,
    fontSize: 11,
    color: COLORS.primary,
    letterSpacing: 1,
  },
  planName: {
    fontFamily: FONTS.family.headingMedium,
    fontSize: 22,
    color: COLORS.textPrimary,
  },
  betaNote: {
    fontFamily: FONTS.family.body,
    fontSize: 14,
    color: COLORS.textSecondary,
    lineHeight: 21,
    marginTop: 6,
    marginBottom: 14,
  },
  price: {
    fontFamily: FONTS.family.bodyBold,
    fontSize: 28,
    color: COLORS.primary,
    marginVertical: 8,
  },
  per: { fontFamily: FONTS.family.body, fontSize: 14, color: COLORS.textSecondary },
  featureRow: { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 8 },
  featureText: {
    fontFamily: FONTS.family.body,
    fontSize: 14,
    color: COLORS.textPrimary,
    flex: 1,
  },
  allowanceRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    borderTopWidth: 1,
    borderTopColor: COLORS.borderLight,
    marginTop: 10,
    paddingTop: 12,
  },
  allowanceLabel: {
    fontFamily: FONTS.family.body,
    fontSize: 13,
    color: COLORS.textSecondary,
  },
  allowanceValue: {
    fontFamily: FONTS.family.bodySemibold,
    fontSize: 13,
    color: COLORS.textPrimary,
  },
  cta: {
    marginTop: 12,
    backgroundColor: COLORS.primary,
    borderRadius: RADIUS.lg,
    paddingVertical: 14,
    alignItems: 'center',
  },
  ctaText: {
    fontFamily: FONTS.family.bodySemibold,
    color: COLORS.white,
    fontSize: 15,
  },
  note: {
    marginTop: 8,
    fontFamily: FONTS.family.body,
    fontSize: 12,
    color: COLORS.textMuted,
    lineHeight: 18,
  },
});
