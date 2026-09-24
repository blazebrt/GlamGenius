/**
 * Registration incomplete screen (§2 hardening spec).
 *
 * Shown when the Supabase session exists but ``/api/v2/me`` returned 403
 * REGISTRATION_REQUIRED. Two common cases land here:
 *
 * 1. The user completed Supabase sign-up with email confirmation. They just
 *    confirmed their email and their Supabase session refreshed via the
 *    deep-link callback. We hold their reservation challenge in AsyncStorage
 *    and can now call ``/access/register`` to finalise.
 *
 * 2. The user signed in but had never completed invite redemption. Their
 *    reservation challenge is not in storage; they must sign out and start
 *    again with an invite code.
 *
 * Navigation (``src/navigation/authRoutes.ts``): an account that turns out to
 * be registered already goes to the product home. One that this screen has
 * just finished registering goes to onboarding instead, exactly once. While
 * that finish is running, the "registered" effect does not route at all, so it
 * cannot race the onboarding navigation.
 */
import React, { useEffect, useRef, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ActivityIndicator,
  Alert,
  Platform,
} from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { useUserStore } from '../../src/store/userStore';
import {
  FIRST_REGISTRATION_ROUTE,
  PRODUCT_HOME_ROUTE,
  SIGNED_OUT_ROUTE,
} from '../../src/navigation/authRoutes';
import { COLORS, FONTS, SPACING, RADIUS } from '../../src/theme/colors';

function notify(title: string, message: string) {
  if (Platform.OS === 'web') window.alert(`${title}\n\n${message}`);
  else Alert.alert(title, message);
}

export default function RegistrationIncomplete() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const {
    finishPendingRegistration,
    logout,
    pendingChallenge,
    session,
    registrationState,
  } = useUserStore();
  const [busy, setBusy] = useState(false);
  // True from the moment this screen starts finishing a registration. The
  // finish owns the navigation from then on: onboarding when it succeeds.
  const finishing = useRef(false);

  const hasChallenge = !!pendingChallenge;

  useEffect(() => {
    if (registrationState === 'registered') {
      // Registered by the finish below: that path goes to onboarding itself.
      if (finishing.current) return;
      // Already registered when this screen was reached: the product home.
      router.replace(PRODUCT_HOME_ROUTE);
    } else if (!session) {
      router.replace(SIGNED_OUT_ROUTE);
    }
  }, [registrationState, session, router]);

  const handleFinish = async () => {
    finishing.current = true;
    setBusy(true);
    try {
      const result = await finishPendingRegistration();
      if (result.ok) {
        // The only navigation for a newly created account, and it happens once.
        router.replace(FIRST_REGISTRATION_ROUTE);
        return;
      }
      finishing.current = false;
      if (useUserStore.getState().registrationState === 'registered') {
        // Not created here (it already existed): an ordinary registered account.
        router.replace(PRODUCT_HOME_ROUTE);
      } else if (result.code === 'reservation_expired') {
        notify(
          'Reservation expired',
          result.message ??
            'Your invite reservation expired. Please start again with your invite code.'
        );
        await logout();
        router.replace(SIGNED_OUT_ROUTE);
      } else {
        notify('Could not finish', result.message ?? 'Please try again.');
      }
    } catch (error) {
      finishing.current = false;
      throw error;
    } finally {
      setBusy(false);
    }
  };

  const handleStartOver = async () => {
    await logout();
    router.replace(SIGNED_OUT_ROUTE);
  };

  return (
    <View style={[styles.container, { paddingTop: insets.top + 20 }]} testID="registration-incomplete">
      <Ionicons name="mail-open-outline" size={44} color={COLORS.primary} />
      <Text style={styles.title}>Almost there</Text>
      {hasChallenge ? (
        <>
          <Text style={styles.body}>
            We are holding your invite for up to 30 minutes. If you confirmed
            your email in another window, tap the button below to complete
            registration.
          </Text>
          <TouchableOpacity
            testID="registration-finish"
            style={styles.primary}
            onPress={handleFinish}
            disabled={busy}
          >
            {busy ? (
              <ActivityIndicator color={COLORS.white} />
            ) : (
              <Text style={styles.primaryText}>Finish creating my account</Text>
            )}
          </TouchableOpacity>
        </>
      ) : (
        <>
          <Text style={styles.body}>
            Your Supabase account is signed in, but your GlamGenius registration
            has not been finished. Please sign out and start again with your
            invite code.
          </Text>
        </>
      )}
      <TouchableOpacity
        testID="registration-start-over"
        style={styles.secondary}
        onPress={handleStartOver}
      >
        <Text style={styles.secondaryText}>Start again with a new invite</Text>
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: COLORS.background,
    paddingHorizontal: SPACING.lg,
    alignItems: 'flex-start',
    gap: 12,
  },
  title: {
    fontFamily: FONTS.family.heading,
    fontSize: 30,
    color: COLORS.textPrimary,
    marginTop: 12,
  },
  body: {
    fontFamily: FONTS.family.body,
    fontSize: 15,
    color: COLORS.textSecondary,
    lineHeight: 22,
    marginTop: 4,
  },
  primary: {
    backgroundColor: COLORS.primary,
    borderRadius: RADIUS.lg,
    paddingVertical: 16,
    paddingHorizontal: 20,
    marginTop: 24,
    alignSelf: 'stretch',
    alignItems: 'center',
  },
  primaryText: { color: COLORS.white, fontFamily: FONTS.family.bodySemibold, fontSize: 16 },
  secondary: {
    marginTop: 16,
    paddingVertical: 14,
    alignSelf: 'stretch',
    alignItems: 'center',
  },
  secondaryText: {
    color: COLORS.primary,
    fontFamily: FONTS.family.bodyMedium,
    fontSize: 14,
  },
});
