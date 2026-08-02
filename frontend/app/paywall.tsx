/**
 * The paywall, and the plan screen it becomes once you have paid.
 *
 * Structured the way the brief asks: outcomes first, prices second, mechanism
 * last. Every benefit line comes from the server, and the server refuses to
 * serve copy containing "unlimited AI", "unlimited scans" or "more tokens" — so
 * this screen cannot show them.
 *
 * While billing is switched off (the whole private beta), this still renders
 * the plans and says plainly that there is nothing to pay for yet. Hiding them
 * would only surprise people later.
 */
import React, { useCallback, useState } from 'react';
import { RefreshControl, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { useFocusEffect, useLocalSearchParams, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';

import {
  BillingUnavailableNote, CheckoutNote, EntitlementRow, ErrorRecovery, FadeIn, OfferCard,
  PlanBadge, PrimaryButton, SubscriptionState,
} from '../src/components/billing/PaywallPieces';
import {
  EntitlementSnapshot, Offer, buyEventPass, cancelCheckout, getEntitlements, getOffers,
  startCheckout,
} from '../src/services/apiV2';
import { COLORS, FONTS, RADIUS, SPACING } from '../src/theme/colors';

export default function PaywallScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const params = useLocalSearchParams<{ occasion_id?: string; reason?: string }>();

  const [offers, setOffers] = useState<Offer[]>([]);
  const [available, setAvailable] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [simulated, setSimulated] = useState(false);
  const [snapshot, setSnapshot] = useState<EntitlementSnapshot | null>(null);
  const [selected, setSelected] = useState<Offer | null>(null);
  const [pendingOrder, setPendingOrder] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (mode: 'initial' | 'refresh' = 'initial') => {
    if (mode === 'refresh') setRefreshing(true);
    setError(null);
    try {
      const [catalogue, entitlements] = await Promise.all([getOffers(), getEntitlements()]);
      setOffers(catalogue.offers);
      setAvailable(catalogue.billing_available);
      setMessage(catalogue.message);
      setSimulated(catalogue.provider.simulated);
      setSnapshot(entitlements);
      setSelected((current) => current ?? catalogue.offers.find((row) => row.key === 'plus_monthly') ?? catalogue.offers[0] ?? null);
    } catch (err) {
      console.warn('paywall load failed', err);
      setError('We could not load the plans just now.');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useFocusEffect(useCallback(() => { void load('refresh'); }, [load]));

  const onBuy = async () => {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      const session = params.occasion_id && selected.plan_key === 'event_pass'
        ? await buyEventPass(String(params.occasion_id))
        : await startCheckout(selected.key);
      setPendingOrder(session.order_id);
      // A real provider sheet would open here. Whatever it returns, access is
      // granted by the server's webhook — never by this screen.
      await load('refresh');
    } catch (err: any) {
      const code = err?.response?.data?.detail?.code;
      setError(
        code === 'SUBSCRIPTIONS_UNAVAILABLE'
          ? 'There is nothing to pay for yet — GlamGenius is still in its private beta.'
          : 'We could not start that payment. Nothing has been charged.',
      );
    } finally {
      setBusy(false);
    }
  };

  const onCancelCheckout = async () => {
    if (!pendingOrder) return;
    try {
      await cancelCheckout(pendingOrder);
      setPendingOrder(null);
      await load('refresh');
    } catch (err) { console.warn('cancel failed', err); }
  };

  if (loading && !offers.length) {
    return <View style={styles.centre}><Text style={styles.muted}>Loading plans…</Text></View>;
  }

  const paid = snapshot && snapshot.plan_key !== 'free';

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <ScrollView
        contentContainerStyle={{ padding: SPACING.lg, paddingBottom: insets.bottom + 40 }}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void load('refresh')} tintColor={COLORS.primary} />}
      >
        <View style={styles.header}>
          <TouchableOpacity accessibilityRole="button" accessibilityLabel="Go back" onPress={() => router.back()}>
            <Ionicons name="chevron-back" size={24} color={COLORS.textPrimary} />
          </TouchableOpacity>
          {!!snapshot && <PlanBadge planKey={snapshot.plan_key} label={snapshot.plan_label} />}
        </View>

        <FadeIn>
          {/* Editorial hierarchy: the outcome is the headline, not the price. */}
          <Text style={styles.eyebrow}>GLAMGENIUS</Text>
          <Text style={styles.title}>
            {paid ? 'Your plan' : 'Get more out of what you already own'}
          </Text>
          <Text style={styles.subtitle}>
            {params.reason
              ? String(params.reason)
              : 'Plan your week, use your whole wardrobe, and decide what is actually worth buying.'}
          </Text>
        </FadeIn>

        {!available && !!message && <BillingUnavailableNote message={message} />}
        {!!snapshot && <SubscriptionState snapshot={snapshot} />}
        {!!error && <ErrorRecovery message={error} onRetry={() => void load('refresh')} />}

        {offers.map((offer, index) => (
          <FadeIn key={offer.key} delay={60 * (index + 1)}>
            <OfferCard
              offer={offer}
              selected={selected?.key === offer.key}
              onSelect={setSelected}
              disabled={!available}
            />
          </FadeIn>
        ))}

        {!!selected && (
          <PrimaryButton
            label={available ? `Continue with ${selected.label}` : 'Not on sale yet'}
            onPress={() => void onBuy()}
            busy={busy}
            disabled={!available}
          />
        )}

        {!!pendingOrder && (
          <TouchableOpacity
            accessibilityRole="button"
            accessibilityLabel="Cancel this payment"
            onPress={() => void onCancelCheckout()}
          >
            <Text style={styles.link}>Cancel this payment</Text>
          </TouchableOpacity>
        )}

        <CheckoutNote simulated={simulated} />

        {!!snapshot?.features.length && (
          <>
            <Text style={styles.section}>What you have right now</Text>
            <View style={styles.card}>
              {snapshot.features.map((feature) => (
                <EntitlementRow key={feature.feature} feature={feature} />
              ))}
            </View>
            <Text style={styles.muted}>{snapshot.offline_note}</Text>
          </>
        )}

        <TouchableOpacity
          accessibilityRole="button"
          accessibilityLabel="Get help with billing"
          onPress={() => router.push('/support')}
          style={styles.action}
        >
          <Ionicons name="help-buoy-outline" size={18} color={COLORS.primary} />
          <Text style={styles.actionText}>Something wrong with a payment?</Text>
        </TouchableOpacity>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.backgroundSecondary },
  centre: { flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: COLORS.background },
  header: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 },
  eyebrow: { fontFamily: FONTS.family.bodySemibold, color: COLORS.primary, fontSize: 10, letterSpacing: 1.4 },
  title: { fontFamily: FONTS.family.heading, color: COLORS.textPrimary, fontSize: 31, lineHeight: 38, marginTop: 4 },
  subtitle: { fontFamily: FONTS.family.body, color: COLORS.textSecondary, fontSize: 14, lineHeight: 21, marginTop: 9 },
  section: { fontFamily: FONTS.family.headingMedium, color: COLORS.textPrimary, fontSize: 18, marginTop: 26 },
  card: { backgroundColor: COLORS.card, borderRadius: RADIUS.lg, borderWidth: 1, borderColor: COLORS.border, paddingHorizontal: 13, marginTop: 10 },
  muted: { fontFamily: FONTS.family.body, color: COLORS.textMuted, fontSize: 11, lineHeight: 17, marginTop: 10 },
  link: { fontFamily: FONTS.family.bodySemibold, color: COLORS.primary, fontSize: 13, marginTop: 14, textAlign: 'center' },
  action: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, paddingVertical: 13, borderRadius: RADIUS.lg, backgroundColor: COLORS.card, borderWidth: 1, borderColor: COLORS.border, marginTop: 22 },
  actionText: { fontFamily: FONTS.family.bodySemibold, color: COLORS.textPrimary, fontSize: 12 },
});
