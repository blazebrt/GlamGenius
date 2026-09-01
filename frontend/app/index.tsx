/**
 * Launch.
 *
 * The camera is the first screen. Someone standing in a shop holding a packet
 * should get an answer before they are asked to sign up. Authentication adds
 * account capabilities without changing the product home.
 *
 * The three entry components below are the introduction's content. They live
 * here because `/intro` composes them and the closure test reads them from
 * this module.
 */
import React, { useEffect, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ActivityIndicator,
} from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { Ionicons } from '@expo/vector-icons';
import { useUserStore } from '../src/store/userStore';
import { ensureDevice, syncQueue } from '../src/services/productScan';
import { COLORS, FONTS, SPACING } from '../src/theme/colors';

export default function LaunchScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [loading] = useState(true);
  const { userId } = useUserStore();

  useEffect(() => {
    routeOnLaunch();
    // Runs once on mount. Session hydration happens in userStore.initializeUser,
    // which the root layout kicks off before this screen mounts.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const routeOnLaunch = async () => {
    // Register the phone and flush any scans it held, whether or not anyone is
    // signed in. Neither call blocks the screen: scanning works offline.
    void ensureDevice().then(() => syncQueue()).catch(() => undefined);
    try {
      // Clear any orphaned installation identifier.
      await AsyncStorage.removeItem('glamgenius_user_id').catch(() => {});
      setTimeout(() => router.replace('/scan-product'), 300);
    } catch {
      // Fall through to the scanner: it needs nothing to work.
    }
    router.replace('/scan-product');
  };

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <View style={styles.loadingContainer}>
        <Text style={styles.loadingLogo}>GlamGenius</Text>
        <Text style={styles.loadingTagline}>PRODUCTS · UNDERSTOOD</Text>
        {(loading || userId) && (
          <ActivityIndicator size="large" color={COLORS.primary} style={{ marginTop: 24 }} />
        )}
      </View>
    </View>
  );
}

export function EntryBrandTagline() {
  return <Text style={styles.brandTagline}>SCAN · DECIDE · UNDERSTAND</Text>;
}

export function EntryHero() {
  return <>
    <Text style={styles.heroTitle}>Scan a product.{"\n"}Make a clearer decision.</Text>
    <Text style={styles.heroSubtitle}>
      GlamGenius helps you understand products from product facts and evidence.
    </Text>
  </>;
}

export function EntryFeatures() {
  return <>
    <FeatureItem icon="barcode-outline" title="Scan a product" description="Start without an account and read what the pack can support." />
    <FeatureItem icon="checkmark-circle-outline" title="See the decision" description="Read the facts, source material and next action together." />
    <FeatureItem icon="repeat-outline" title="Scan again" description="Use the same product decision flow whenever it matters." />
  </>;
}

function FeatureItem({ icon, title, description }: { icon: any; title: string; description: string }) {
  return (
    <View style={styles.featureItem}>
      <View style={styles.featureIcon}>
        <Ionicons name={icon} size={20} color={COLORS.primary} />
      </View>
      <View style={{ flex: 1 }}>
        <Text style={styles.featureTitle}>{title}</Text>
        <Text style={styles.featureDesc}>{description}</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.background, paddingHorizontal: SPACING.lg },
  loadingContainer: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  loadingLogo: { fontFamily: FONTS.family.heading, fontSize: 36, color: COLORS.textPrimary },
  loadingTagline: { fontFamily: FONTS.family.bodyMedium, fontSize: 12, color: COLORS.primary, letterSpacing: 3, marginTop: 8 },
  brandTagline: { fontFamily: FONTS.family.bodyMedium, fontSize: 11, color: COLORS.primary, letterSpacing: 2.5, marginTop: 4 },
  heroTitle: { fontFamily: FONTS.family.heading, fontSize: 36, lineHeight: 42, color: COLORS.textPrimary },
  heroSubtitle: { fontFamily: FONTS.family.body, fontSize: 15, lineHeight: 22, color: COLORS.textSecondary, marginTop: SPACING.md },
  featureItem: { flexDirection: 'row', alignItems: 'center', gap: SPACING.md },
  featureIcon: {
    width: 40, height: 40, borderRadius: 12, backgroundColor: COLORS.card,
    alignItems: 'center', justifyContent: 'center', borderWidth: 1, borderColor: COLORS.border,
  },
  featureTitle: { fontFamily: FONTS.family.bodySemibold, fontSize: 15, color: COLORS.textPrimary },
  featureDesc: { fontFamily: FONTS.family.body, fontSize: 13, color: COLORS.textSecondary, marginTop: 2 },
});
