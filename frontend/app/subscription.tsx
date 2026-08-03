/**
 * Placeholder — the paid-membership screen has been removed while GlamGenius
 * is in its Supabase-cutover beta. There is nothing to buy in the app.
 *
 * This file is retained only so any deep link to `/subscription` still resolves
 * to a friendly screen instead of a 404. Prompt 2 deletes the file entirely.
 */
import React from 'react';
import { View, Text, StyleSheet, TouchableOpacity } from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { COLORS, FONTS, RADIUS, SPACING } from '../src/theme/colors';

export default function MembershipPlaceholder() {
  const router = useRouter();
  const insets = useSafeAreaInsets();

  return (
    <View style={[styles.container, { paddingTop: insets.top }]} testID="membership-placeholder">
      <View style={styles.top}>
        <TouchableOpacity
          testID="membership-back"
          onPress={() => router.back()}
          accessibilityRole="button"
          accessibilityLabel="Go back"
        >
          <Ionicons name="arrow-back" size={22} color={COLORS.textPrimary} />
        </TouchableOpacity>
        <Text style={styles.topTitle}>Membership</Text>
        <View style={{ width: 22 }} />
      </View>

      <View style={styles.body}>
        <Text style={styles.title}>You are in the private beta</Text>
        <Text style={styles.subtitle}>
          There is nothing to pay for right now. Everything in GlamGenius is
          switched on for invited members.
        </Text>
      </View>
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
  topTitle: { fontFamily: FONTS.family.bodySemibold, fontSize: 16, color: COLORS.textPrimary },
  body: { padding: SPACING.lg, marginTop: SPACING.xl },
  title: {
    fontFamily: FONTS.family.heading,
    fontSize: 28,
    color: COLORS.textPrimary,
    lineHeight: 34,
  },
  subtitle: {
    fontFamily: FONTS.family.body,
    fontSize: 15,
    color: COLORS.textSecondary,
    marginTop: 14,
    lineHeight: 22,
  },
  cta: {
    marginTop: SPACING.xl,
    backgroundColor: COLORS.primary,
    borderRadius: RADIUS.lg,
    paddingVertical: 14,
    alignItems: 'center',
  },
  ctaText: { fontFamily: FONTS.family.bodySemibold, color: COLORS.white, fontSize: 15 },
});
