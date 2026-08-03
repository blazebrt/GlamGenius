/**
 * Placeholder — the paywall has been removed while GlamGenius is in its
 * Supabase-cutover beta. There is no purchasable functionality.
 *
 * Prompt 2 deletes the file entirely.
 */
import React from 'react';
import { View, Text, StyleSheet, TouchableOpacity } from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { COLORS, FONTS, SPACING } from '../src/theme/colors';

export default function PaywallPlaceholder() {
  const router = useRouter();
  const insets = useSafeAreaInsets();

  return (
    <View style={[styles.container, { paddingTop: insets.top }]} testID="paywall-placeholder">
      <View style={styles.top}>
        <TouchableOpacity
          testID="paywall-back"
          onPress={() => router.back()}
          accessibilityRole="button"
          accessibilityLabel="Go back"
        >
          <Ionicons name="arrow-back" size={22} color={COLORS.textPrimary} />
        </TouchableOpacity>
        <Text style={styles.topTitle}>Beta access</Text>
        <View style={{ width: 22 }} />
      </View>

      <View style={styles.body}>
        <Text style={styles.title}>Everything is included</Text>
        <Text style={styles.subtitle}>
          Invited members get full access while GlamGenius is in its private
          beta. There is nothing to unlock and nothing to buy.
        </Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.backgroundSecondary },
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
});
