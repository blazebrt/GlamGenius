import React, { useEffect, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ScrollView,
  ActivityIndicator,
  Alert,
} from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { api } from '../src/services/api';
import { useUserStore } from '../src/store/userStore';
import { COLORS, FONTS, SPACING, RADIUS, SHADOWS } from '../src/theme/colors';

export default function SubscriptionScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { userId, refreshSubscription } = useUserStore();
  const [config, setConfig] = useState<any>(null);
  const [status, setStatus] = useState<any>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const [c, s] = await Promise.all([
          api.get('/config/public'),
          userId ? api.get('/subscription/status') : Promise.resolve({ data: null }),
        ]);
        setConfig(c.data);
        setStatus(s.data);
      } catch {
        setConfig({ plus_price_inr: 249, plus_yearly_inr: 1999, free_scans_per_month: 1 });
      }
    })();
  }, [userId]);

  const buy = async (plan: 'plus_monthly' | 'plus_yearly') => {
    if (!userId) {
      Alert.alert('Sign in needed', 'Create an account first.', [
        { text: 'OK', onPress: () => router.push('/(auth)/welcome') },
      ]);
      return;
    }
    setLoading(true);
    try {
      const order = await api.post('/subscription/create-order', {
        user_id: userId,
        plan,
        payment_method: 'upi',
      });
      await api.post(`/subscription/confirm?order_id=${order.data.order_id}&payment_method=upi`);
      await refreshSubscription();
      Alert.alert('Plus activated', 'Unlimited skin & hair checks are unlocked.', [
        { text: 'Great', onPress: () => router.back() },
      ]);
    } catch {
      Alert.alert('Payment failed', 'Please try again.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <View style={styles.top}>
        <TouchableOpacity onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={22} color={COLORS.textPrimary} />
        </TouchableOpacity>
        <Text style={styles.topTitle}>GlamGenius Plus</Text>
        <View style={{ width: 22 }} />
      </View>

      <ScrollView contentContainerStyle={{ padding: SPACING.lg, paddingBottom: 80 }}>
        <Text style={styles.title}>Celebrity-level coaching,{'\n'}everyday budget</Text>
        <Text style={styles.subtitle}>
          Free includes {config?.free_scans_per_month ?? 1} checks / month. Plus unlocks unlimited checks, full history, and richer plans.
        </Text>

        {status?.plan === 'plus' && (
          <View style={styles.activeBanner}>
            <Text style={styles.activeText}>You are on Plus</Text>
          </View>
        )}

        <View style={styles.card}>
          <Text style={styles.planName}>Monthly</Text>
          <Text style={styles.price}>₹{config?.plus_price_inr ?? 249}<Text style={styles.per}> / month</Text></Text>
          <Feature text="Unlimited skin & hair checks" />
          <Feature text="Full colour & food plans" />
          <Feature text="Score history & trends" />
          <TouchableOpacity style={styles.cta} disabled={loading || status?.plan === 'plus'} onPress={() => buy('plus_monthly')}>
            {loading ? <ActivityIndicator color={COLORS.white} /> : (
              <Text style={styles.ctaText}>{status?.plan === 'plus' ? 'Already Plus' : 'Start monthly'}</Text>
            )}
          </TouchableOpacity>
        </View>

        <View style={[styles.card, styles.cardHighlight]}>
          <Text style={styles.badge}>BEST VALUE</Text>
          <Text style={styles.planName}>Yearly</Text>
          <Text style={styles.price}>₹{config?.plus_yearly_inr ?? 1999}<Text style={styles.per}> / year</Text></Text>
          <Feature text="Everything in monthly" />
          <Feature text="Save vs paying month to month" />
          <TouchableOpacity style={styles.cta} disabled={loading || status?.plan === 'plus'} onPress={() => buy('plus_yearly')}>
            <Text style={styles.ctaText}>{status?.plan === 'plus' ? 'Already Plus' : 'Start yearly'}</Text>
          </TouchableOpacity>
        </View>

        <Text style={styles.note}>
          Demo billing is enabled for development. Connect Razorpay keys before production. This pays for the app coach — not salon services.
        </Text>
      </ScrollView>
    </View>
  );
}

function Feature({ text }: { text: string }) {
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
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: SPACING.lg, paddingVertical: 12,
  },
  topTitle: { fontFamily: FONTS.family.bodySemibold, fontSize: 16, color: COLORS.textPrimary },
  title: { fontFamily: FONTS.family.heading, fontSize: 30, color: COLORS.textPrimary, lineHeight: 36 },
  subtitle: { fontFamily: FONTS.family.body, fontSize: 14, color: COLORS.textSecondary, marginTop: 10, lineHeight: 21, marginBottom: 20 },
  activeBanner: {
    backgroundColor: COLORS.primaryLight, padding: 12, borderRadius: RADIUS.md, marginBottom: 12,
  },
  activeText: { fontFamily: FONTS.family.bodySemibold, color: COLORS.primary, textAlign: 'center' },
  card: {
    backgroundColor: COLORS.card, borderRadius: RADIUS.xl, padding: 18, marginBottom: 14,
    borderWidth: 1, borderColor: COLORS.border, ...SHADOWS.sm,
  },
  cardHighlight: { borderColor: COLORS.primary, borderWidth: 1.5 },
  badge: { fontFamily: FONTS.family.bodySemibold, fontSize: 11, color: COLORS.accent, letterSpacing: 1, marginBottom: 6 },
  planName: { fontFamily: FONTS.family.headingMedium, fontSize: 22, color: COLORS.textPrimary },
  price: { fontFamily: FONTS.family.bodyBold, fontSize: 28, color: COLORS.primary, marginVertical: 8 },
  per: { fontFamily: FONTS.family.body, fontSize: 14, color: COLORS.textSecondary },
  featureRow: { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 8 },
  featureText: { fontFamily: FONTS.family.body, fontSize: 14, color: COLORS.textPrimary },
  cta: {
    marginTop: 12, backgroundColor: COLORS.primary, borderRadius: RADIUS.lg, paddingVertical: 14, alignItems: 'center',
  },
  ctaText: { fontFamily: FONTS.family.bodySemibold, color: COLORS.white, fontSize: 15 },
  note: { marginTop: 8, fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.textMuted, lineHeight: 18 },
});
