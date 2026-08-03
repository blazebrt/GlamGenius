/**
 * Deep-link auth callback (§2 hardening spec).
 *
 * Supabase redirects here after email confirmation, magic-link sign-in, and
 * password recovery. The Supabase SDK has already parsed the URL fragment
 * (``detectSessionInUrl`` on web, ``expo-linking`` on native) and dispatched
 * an ``onAuthStateChange`` event by the time we mount, so this screen's job
 * is simply to route the user to the correct next step based on the
 * resolved ``registrationState``:
 *
 *   registered              → /(tabs)/today
 *   registration_pending    → /(auth)/registration-incomplete
 *   signed_out              → /(auth)/welcome
 */
import React, { useEffect } from 'react';
import { View, Text, ActivityIndicator, StyleSheet } from 'react-native';
import { useRouter } from 'expo-router';
import { useUserStore } from '../../src/store/userStore';
import { COLORS, FONTS, SPACING } from '../../src/theme/colors';

export default function AuthCallback() {
  const router = useRouter();
  const { initialized, registrationState } = useUserStore();

  useEffect(() => {
    if (!initialized) return;
    if (registrationState === 'registered') {
      router.replace('/(tabs)/today');
    } else if (registrationState === 'registration_pending') {
      router.replace('/(auth)/registration-incomplete');
    } else {
      router.replace('/(auth)/welcome');
    }
  }, [initialized, registrationState, router]);

  return (
    <View style={styles.container} testID="auth-callback">
      <ActivityIndicator color={COLORS.primary} size="large" />
      <Text style={styles.text}>Finishing sign-in…</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: COLORS.background,
    alignItems: 'center',
    justifyContent: 'center',
    padding: SPACING.lg,
    gap: 12,
  },
  text: {
    fontFamily: FONTS.family.body,
    color: COLORS.textSecondary,
    fontSize: 14,
  },
});
