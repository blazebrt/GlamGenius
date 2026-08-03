/**
 * Salon ideas tab — temporarily unavailable while the salon ideas domain is
 * being reworked for V2. The V1 `/salon-ideas` endpoint is gone; the V2
 * catalogue is a static JSON asset that has not yet shipped.
 *
 * The tab is currently `href: null` in `_layout.tsx`, so nothing links to
 * this screen. Deep links resolve here.
 */
import React from 'react';
import { View, Text, StyleSheet, ScrollView } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { COLORS, FONTS, SPACING } from '../../src/theme/colors';

export default function SalonIdeasPlaceholder() {
  const insets = useSafeAreaInsets();
  return (
    <View style={[styles.container, { paddingTop: insets.top }]} testID="salon-ideas-placeholder">
      <ScrollView contentContainerStyle={styles.body}>
        <Text style={styles.label}>OPTIONAL</Text>
        <Text style={styles.title}>Salon ideas</Text>
        <Text style={styles.subtitle}>
          Salon suggestions are being reworked and will return in a later beta.
          There are no prices and no booking in the app.
        </Text>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.backgroundSecondary },
  body: { padding: SPACING.lg },
  label: {
    fontFamily: FONTS.family.bodySemibold,
    fontSize: 11,
    color: COLORS.primary,
    letterSpacing: 1.4,
  },
  title: {
    fontFamily: FONTS.family.heading,
    fontSize: 28,
    color: COLORS.textPrimary,
    marginTop: 4,
  },
  subtitle: {
    fontFamily: FONTS.family.body,
    fontSize: 14,
    color: COLORS.textSecondary,
    marginTop: 10,
    lineHeight: 21,
  },
});
