/**
 * Launch.
 *
 * The camera is the first screen. Someone standing in a shop holding a packet
 * should get an answer before they are asked to sign up, so a signed-out launch
 * goes straight to the scanner and the introduction sits one tap away at
 * `/intro`. A signed-in launch goes to Today, exactly as before.
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
  const [loading, setLoading] = useState(true);
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
      if (useUserStore.getState().userId) {
        setTimeout(() => router.replace('/(tabs)/today'), 900);
        return;
      }
    } catch {
      // Fall through to the scanner: it needs nothing to work.
    }
    setLoading(false);
    router.replace('/scan-product');
  };

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <View style={styles.loadingContainer}>
        <Text style={styles.loadingLogo}>GlamGenius</Text>
        <Text style={styles.loadingTagline}>YOUR APPEARANCE · ORGANISED</Text>
        {(loading || userId) && (
          <ActivityIndicator size="large" color={COLORS.primary} style={{ marginTop: 24 }} />
        )}
      </View>
    </View>
  );
}

export function EntryBrandTagline() {
  return <Text style={styles.brandTagline}>STYLE · CARE · PLAN</Text>;
}

export function EntryHero() {
  return <>
    <Text style={styles.heroTitle}>Know what to wear.{"\n"}Know what needs attention.</Text>
    <Text style={styles.heroSubtitle}>
      GlamGenius brings your wardrobe, care shelf, routines, occasions and preferences into one calm daily plan — starting with what you already own.
    </Text>
  </>;
}

export function EntryFeatures() {
  return <>
    <FeatureItem icon="sunny-outline" title="Today, decided" description="A clear owned-first outfit and the few things worth your attention." />
    <FeatureItem icon="shirt-outline" title="Style from your wardrobe" description="Occasion looks and purchase decisions without buying pressure." />
    <FeatureItem icon="leaf-outline" title="Care and planning together" description="Routines, upkeep, events and progress in one place." />
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
