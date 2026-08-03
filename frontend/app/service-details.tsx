/**
 * Salon idea detail — placeholder while the V2 salon catalogue is being
 * reworked. The V1 `/services/:id` endpoint has been removed.
 */
import React from 'react';
import { View, Text, StyleSheet, TouchableOpacity, ScrollView } from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { COLORS, FONTS, SPACING } from '../src/theme/colors';

export default function ServiceDetailsPlaceholder() {
  const router = useRouter();
  const insets = useSafeAreaInsets();

  return (
    <View style={[styles.container, { paddingTop: insets.top }]} testID="service-details-placeholder">
      <View style={styles.top}>
        <TouchableOpacity onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={22} color={COLORS.textPrimary} />
        </TouchableOpacity>
        <Text style={styles.topTitle}>Salon idea</Text>
        <View style={{ width: 22 }} />
      </View>
      <ScrollView contentContainerStyle={styles.body}>
        <Text style={styles.title}>Coming back soon</Text>
        <Text style={styles.subtitle}>
          Salon ideas are being reworked and are not available in the current
          beta. GlamGenius does not book appointments or take payments for
          salon services.
        </Text>
      </ScrollView>
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
  body: { padding: SPACING.lg },
  title: {
    fontFamily: FONTS.family.heading,
    fontSize: 28,
    color: COLORS.textPrimary,
  },
  subtitle: {
    fontFamily: FONTS.family.body,
    fontSize: 14,
    color: COLORS.textSecondary,
    marginTop: 12,
    lineHeight: 22,
  },
});
