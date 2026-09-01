/**
 * What GlamGenius is, for someone who has just scanned something and wants to
 * know who is answering.
 *
 * This used to be the launch screen. The camera is the launch screen now — a
 * person holding a packet should get an answer before they get a pitch — so
 * this sits one tap away, from the scanner and from the sign-in flow.
 */
import React from 'react';
import { Dimensions, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';

import { COLORS, FONTS, RADIUS, SPACING } from '../src/theme/colors';
import { EntryBrandTagline, EntryFeatures, EntryHero } from './index';

const { width } = Dimensions.get('window');

export default function IntroScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();

  return (
    <ScrollView
      style={styles.container}
      contentContainerStyle={{ paddingTop: insets.top + SPACING.lg, paddingBottom: insets.bottom + SPACING.xl }}
    >
      <View style={styles.header}>
        <View style={styles.logoMark}>
          <Ionicons name="leaf-outline" size={30} color={COLORS.primary} />
        </View>
        <Text style={styles.brandName}>GlamGenius</Text>
        <EntryBrandTagline />
      </View>

      <View style={styles.heroSection}>
        <EntryHero />
      </View>

      <View style={styles.features}>
        <EntryFeatures />
      </View>

      <TouchableOpacity
        accessibilityRole="button"
        accessibilityLabel="Back to scanning"
        onPress={() => router.replace('/scan-product')}
        style={styles.scanButton}
      >
        <Ionicons name="barcode-outline" size={20} color={COLORS.primary} />
        <Text style={styles.scanText}>Back to scanning</Text>
      </TouchableOpacity>

      <TouchableOpacity
        accessibilityRole="button"
        accessibilityLabel="Join the private beta"
        onPress={() => router.push('/(auth)/welcome')}
        style={styles.ctaButton}
      >
        <Text style={styles.ctaText}>Join the private beta</Text>
        <Ionicons name="arrow-forward" size={20} color={COLORS.white} />
      </TouchableOpacity>
      <Text style={styles.freeNote}>Invite-only — enter your code on the next screen</Text>

      <TouchableOpacity
        accessibilityRole="button"
        accessibilityLabel="Sign in to an existing account"
        onPress={() => router.push('/(auth)/welcome')}
        style={{ marginTop: 14 }}
      >
        <Text style={styles.secondaryLink}>Already have an account? Sign in</Text>
      </TouchableOpacity>

      <Text style={styles.disclaimer}>Product facts and evidence, not medical advice.</Text>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.background, paddingHorizontal: SPACING.lg },
  header: { alignItems: 'flex-start' },
  logoMark: {
    width: 56, height: 56, borderRadius: 16, backgroundColor: COLORS.primaryLight,
    alignItems: 'center', justifyContent: 'center', marginBottom: SPACING.sm,
  },
  brandName: { fontFamily: FONTS.family.heading, fontSize: 34, color: COLORS.textPrimary },
  heroSection: { marginTop: SPACING.xl, maxWidth: width - 48 },
  features: { marginTop: SPACING.xl, gap: SPACING.md },
  scanButton: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8,
    borderRadius: RADIUS.lg, paddingVertical: 14, borderWidth: 1, borderColor: COLORS.primary,
    marginTop: SPACING.xl,
  },
  scanText: { fontFamily: FONTS.family.bodySemibold, fontSize: 15, color: COLORS.primary },
  ctaButton: {
    backgroundColor: COLORS.primary, borderRadius: RADIUS.lg, paddingVertical: 16, paddingHorizontal: 20,
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 10, marginTop: SPACING.md,
  },
  ctaText: { fontFamily: FONTS.family.bodySemibold, fontSize: 16, color: COLORS.white },
  freeNote: { fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.textMuted, textAlign: 'center', marginTop: 10 },
  secondaryLink: { fontFamily: FONTS.family.bodyMedium, fontSize: 14, color: COLORS.primary, textAlign: 'center' },
  disclaimer: { fontFamily: FONTS.family.body, fontSize: 11, color: COLORS.textMuted, textAlign: 'center', marginTop: 12 },
});
