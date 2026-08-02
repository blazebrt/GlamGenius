/**
 * Paywall and entitlement building blocks.
 *
 * The brief is unusually specific about what this screen must and must not say,
 * and the rules are worth restating where somebody editing it will see them:
 *
 * **Lead with outcomes.** "Plan every week." "Use your complete wardrobe."
 * "Make better shopping decisions." Never "unlimited AI", never "unlimited
 * scans", never "more tokens". The benefit strings come from the server, which
 * refuses at import to serve any plan whose copy contains those phrases — so
 * this component cannot render them even if somebody tries.
 *
 * **Never imply the payment sheet granted anything.** `CheckoutNote` states
 * plainly that access follows the server confirming with the provider. A screen
 * that says "you're in!" the moment a sheet closes is lying about a payment
 * that may still fail.
 *
 * Accessibility and motion: every price and limit is exposed in words through
 * `accessibilityLabel`, and `useReducedMotion` disables the entrance animation
 * for anybody who has asked their device for less movement.
 */
import React from 'react';
import {
  AccessibilityInfo, Animated, Easing, StyleSheet, Text, TouchableOpacity, View,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';

import { Benefit, EntitlementFeature, EntitlementSnapshot, Offer, PlanKey } from '../../services/apiV2';
import { COLORS, FONTS, RADIUS, SPACING } from '../../theme/colors';

/** Whether the user has asked their device to reduce motion. */
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = React.useState(false);
  React.useEffect(() => {
    let alive = true;
    AccessibilityInfo.isReduceMotionEnabled()
      .then((value) => { if (alive) setReduced(value); })
      .catch(() => { /* if we cannot tell, assume no preference */ });
    const listener = AccessibilityInfo.addEventListener('reduceMotionChanged', setReduced);
    return () => { alive = false; listener?.remove?.(); };
  }, []);
  return reduced;
}

/** A price in rupees, in the form Indian readers expect. */
export function formatPrice(amountInr: number): string {
  return `₹${amountInr.toLocaleString('en-IN')}`;
}

export function periodLabel(period: Offer['period']): string {
  if (period === 'monthly') return 'a month';
  if (period === 'yearly') return 'a year';
  return 'one payment';
}

/** The whole offer read aloud, so a screen reader gets price and terms together. */
export function offerLabel(offer: Offer): string {
  const base = `${offer.label}, ${formatPrice(offer.amount_inr)} ${periodLabel(offer.period)}`;
  if (offer.valid_days) return `${base}, valid for ${offer.valid_days} days`;
  return base;
}

export function FadeIn({ children, delay = 0 }: { children: React.ReactNode; delay?: number }) {
  const reduced = useReducedMotion();
  const value = React.useRef(new Animated.Value(reduced ? 1 : 0)).current;

  React.useEffect(() => {
    if (reduced) {
      value.setValue(1);
      return;
    }
    Animated.timing(value, {
      toValue: 1, duration: 260, delay, easing: Easing.out(Easing.ease), useNativeDriver: true,
    }).start();
  }, [reduced, value, delay]);

  return (
    <Animated.View
      style={{
        opacity: value,
        transform: [{ translateY: value.interpolate({ inputRange: [0, 1], outputRange: [8, 0] }) }],
      }}
    >
      {children}
    </Animated.View>
  );
}

export function BenefitRow({ benefit }: { benefit: Benefit }) {
  return (
    <View style={styles.benefit} accessibilityLabel={`${benefit.headline}. ${benefit.detail}`}>
      <Ionicons name="checkmark" size={17} color={COLORS.primary} style={{ marginTop: 2 }} />
      <View style={{ flex: 1 }}>
        {/* The outcome leads. The mechanism is secondary, in smaller type. */}
        <Text style={styles.benefitHeadline}>{benefit.headline}</Text>
        <Text style={styles.benefitDetail}>{benefit.detail}</Text>
      </View>
    </View>
  );
}

export function OfferCard({ offer, selected, onSelect, disabled }: {
  offer: Offer;
  selected: boolean;
  onSelect: (offer: Offer) => void;
  disabled?: boolean;
}) {
  return (
    <TouchableOpacity
      accessibilityRole="radio"
      accessibilityState={{ selected, disabled: !!disabled }}
      accessibilityLabel={offerLabel(offer)}
      onPress={() => onSelect(offer)}
      disabled={disabled}
      style={[styles.offer, selected && styles.offerSelected, disabled && styles.offerDisabled]}
    >
      <View style={styles.offerHead}>
        <View style={{ flex: 1 }}>
          <Text style={styles.offerLabel}>{offer.label}</Text>
          <Text style={styles.offerTagline}>{offer.tagline}</Text>
        </View>
        <View style={{ alignItems: 'flex-end' }}>
          <Text style={styles.price}>{formatPrice(offer.amount_inr)}</Text>
          <Text style={styles.period}>{periodLabel(offer.period)}</Text>
        </View>
      </View>

      {offer.saving_percent !== null && offer.compare_at_inr !== null && (
        <Text
          style={styles.saving}
          accessibilityLabel={`Saves ${offer.saving_percent} percent against paying monthly`}
        >
          Save {offer.saving_percent}% against {formatPrice(offer.compare_at_inr)} monthly
        </Text>
      )}
      {offer.valid_days !== null && (
        <Text style={styles.period}>Yours for {offer.valid_days} days</Text>
      )}

      <View style={{ marginTop: 10 }}>
        {offer.benefits.map((benefit, index) => (
          <BenefitRow key={index} benefit={benefit} />
        ))}
      </View>
    </TouchableOpacity>
  );
}

export function BillingUnavailableNote({ message }: { message: string }) {
  return (
    <View style={styles.banner} accessibilityLabel="Nothing to pay for yet">
      <Ionicons name="information-circle-outline" size={18} color={COLORS.primary} />
      <Text style={styles.bannerText}>{message}</Text>
    </View>
  );
}

/** Says outright that a closed payment sheet is not proof of payment. */
export function CheckoutNote({ simulated }: { simulated: boolean }) {
  return (
    <View style={styles.note} accessibilityLabel="How payment works">
      <Text style={styles.noteText}>
        Access starts once our server has confirmed the payment with the provider — not the
        moment the payment screen closes.
      </Text>
      {simulated && (
        <Text style={styles.simulated}>
          This build is not connected to a real payment provider, so nothing will be charged.
        </Text>
      )}
    </View>
  );
}

export function PlanBadge({ planKey, label }: { planKey: PlanKey; label: string }) {
  return (
    <View
      style={[styles.badge, planKey !== 'free' && styles.badgePaid]}
      accessibilityLabel={`Your plan: ${label}`}
    >
      <Text style={[styles.badgeText, planKey !== 'free' && styles.badgeTextPaid]}>{label}</Text>
    </View>
  );
}

/** One metered feature, with what is left said in words. */
export function EntitlementRow({ feature }: { feature: EntitlementFeature }) {
  const unmetered = feature.limit === null;
  const spoken = unmetered
    ? `${feature.label}: included`
    : `${feature.label}: ${feature.remaining} of ${feature.limit} left`;

  return (
    <View style={styles.entitlement} accessibilityLabel={spoken}>
      <Text style={styles.entitlementLabel}>{feature.label}</Text>
      <Text style={styles.entitlementValue}>
        {unmetered ? 'Included' : `${feature.remaining} of ${feature.limit} left`}
      </Text>
    </View>
  );
}

export function SubscriptionState({ snapshot }: { snapshot: EntitlementSnapshot }) {
  const subscription = snapshot.subscription;
  if (!subscription) return null;

  if (subscription.status === 'grace') {
    return (
      <View style={styles.warning} accessibilityLabel="A payment did not go through">
        <Ionicons name="alert-circle-outline" size={18} color={COLORS.primary} />
        <Text style={styles.bannerText}>
          Your last payment did not go through. Everything keeps working while we retry
          {subscription.grace_until ? ` until ${subscription.grace_until}` : ''}.
        </Text>
      </View>
    );
  }

  if (subscription.cancel_at_period_end) {
    return (
      <View style={styles.banner} accessibilityLabel="Your plan is ending">
        <Ionicons name="time-outline" size={18} color={COLORS.primary} />
        <Text style={styles.bannerText}>
          Cancelled — but you keep everything until {subscription.current_period_end},
          which you have already paid for.
        </Text>
      </View>
    );
  }

  return (
    <View style={styles.banner} accessibilityLabel="Your plan renews">
      <Ionicons name="refresh-outline" size={18} color={COLORS.primary} />
      <Text style={styles.bannerText}>Renews on {subscription.current_period_end}.</Text>
    </View>
  );
}

/** Shown when a limit is reached. Names the outcome, never the model. */
export function LimitReached({ feature, onUpgrade }: {
  feature: EntitlementFeature;
  onUpgrade?: () => void;
}) {
  return (
    <View style={styles.limit} accessibilityLabel={`${feature.label} used up this month`}>
      <Text style={styles.limitTitle}>
        That is all your {feature.label.toLowerCase()} this month
      </Text>
      <Text style={styles.bannerText}>
        Your allowance starts again next month. Plus raises it if you want more before then.
      </Text>
      {!!onUpgrade && (
        <TouchableOpacity accessibilityRole="button" accessibilityLabel="See what Plus includes" onPress={onUpgrade}>
          <Text style={styles.link}>See what Plus includes</Text>
        </TouchableOpacity>
      )}
    </View>
  );
}

export function PrimaryButton({ label, onPress, busy, disabled }: {
  label: string;
  onPress: () => void;
  busy?: boolean;
  disabled?: boolean;
}) {
  return (
    <TouchableOpacity
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityState={{ disabled: !!disabled || !!busy, busy: !!busy }}
      onPress={onPress}
      disabled={disabled || busy}
      style={[styles.primary, (disabled || busy) && styles.primaryDisabled]}
    >
      <Text style={styles.primaryText}>{busy ? 'One moment…' : label}</Text>
    </TouchableOpacity>
  );
}

export function ErrorRecovery({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <View style={styles.limit} accessibilityLabel="Something went wrong">
      <Text style={styles.limitTitle}>That did not work</Text>
      <Text style={styles.bannerText}>{message}</Text>
      <TouchableOpacity accessibilityRole="button" accessibilityLabel="Try again" onPress={onRetry}>
        <Text style={styles.link}>Try again</Text>
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  offer: { backgroundColor: COLORS.card, borderRadius: RADIUS.lg, borderWidth: 1, borderColor: COLORS.border, padding: 15, marginTop: 12 },
  offerSelected: { borderColor: COLORS.primary, borderWidth: 2 },
  offerDisabled: { opacity: 0.6 },
  offerHead: { flexDirection: 'row', alignItems: 'flex-start', gap: 10 },
  offerLabel: { fontFamily: FONTS.family.headingMedium, color: COLORS.textPrimary, fontSize: 19 },
  offerTagline: { fontFamily: FONTS.family.body, color: COLORS.textSecondary, fontSize: 12, lineHeight: 18, marginTop: 3 },
  price: { fontFamily: FONTS.family.heading, color: COLORS.textPrimary, fontSize: 25 },
  period: { fontFamily: FONTS.family.body, color: COLORS.textMuted, fontSize: 11, marginTop: 2 },
  saving: { fontFamily: FONTS.family.bodySemibold, color: COLORS.primary, fontSize: 11, marginTop: 8 },
  benefit: { flexDirection: 'row', gap: 9, alignItems: 'flex-start', marginTop: 9 },
  benefitHeadline: { fontFamily: FONTS.family.bodySemibold, color: COLORS.textPrimary, fontSize: 13, lineHeight: 19 },
  benefitDetail: { fontFamily: FONTS.family.body, color: COLORS.textSecondary, fontSize: 12, lineHeight: 17, marginTop: 1 },
  banner: { flexDirection: 'row', gap: 9, alignItems: 'flex-start', backgroundColor: COLORS.primaryLight, borderRadius: RADIUS.lg, borderWidth: 1, borderColor: COLORS.primaryMuted, padding: 13, marginTop: 12 },
  warning: { flexDirection: 'row', gap: 9, alignItems: 'flex-start', backgroundColor: COLORS.card, borderRadius: RADIUS.lg, borderWidth: 1, borderColor: COLORS.primary, padding: 13, marginTop: 12 },
  bannerText: { flex: 1, fontFamily: FONTS.family.body, color: COLORS.textPrimary, fontSize: 12, lineHeight: 18 },
  note: { marginTop: 14 },
  noteText: { fontFamily: FONTS.family.body, color: COLORS.textMuted, fontSize: 11, lineHeight: 17 },
  simulated: { fontFamily: FONTS.family.bodyMedium, color: COLORS.primary, fontSize: 11, lineHeight: 17, marginTop: 6 },
  badge: { alignSelf: 'flex-start', paddingHorizontal: 11, paddingVertical: 5, borderRadius: RADIUS.full, borderWidth: 1, borderColor: COLORS.border },
  badgePaid: { backgroundColor: COLORS.primary, borderColor: COLORS.primary },
  badgeText: { fontFamily: FONTS.family.bodySemibold, color: COLORS.textSecondary, fontSize: 11 },
  badgeTextPaid: { color: COLORS.white },
  entitlement: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 10, paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: COLORS.border },
  entitlementLabel: { flex: 1, fontFamily: FONTS.family.body, color: COLORS.textSecondary, fontSize: 12 },
  entitlementValue: { fontFamily: FONTS.family.bodySemibold, color: COLORS.textPrimary, fontSize: 12 },
  limit: { backgroundColor: COLORS.card, borderRadius: RADIUS.lg, borderWidth: 1, borderColor: COLORS.border, padding: SPACING.lg, marginTop: 12 },
  limitTitle: { fontFamily: FONTS.family.headingMedium, color: COLORS.textPrimary, fontSize: 16 },
  link: { fontFamily: FONTS.family.bodySemibold, color: COLORS.primary, fontSize: 13, marginTop: 10 },
  primary: { backgroundColor: COLORS.primary, borderRadius: RADIUS.lg, paddingVertical: 15, alignItems: 'center', marginTop: 16 },
  primaryDisabled: { opacity: 0.5 },
  primaryText: { fontFamily: FONTS.family.bodySemibold, color: COLORS.white, fontSize: 14 },
});
