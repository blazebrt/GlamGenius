/**
 * The centre tab: the two Phase 4 decisions, side by side.
 *
 * These are the two questions the app exists to answer right now — "what do I
 * wear to this" and "should I buy this" — so they get the centre button rather
 * than being buried a level down.
 */
import React, { useCallback, useState } from 'react';
import { ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { useFocusEffect, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';

import { OCCASION_ICONS } from '../../src/components/style/StylePieces';
import { InventorySummary, OccasionDefinition, getInventorySummary, getOccasionTypes } from '../../src/services/apiV2';
import { COLORS, FONTS, RADIUS, SPACING } from '../../src/theme/colors';

// The occasions people actually reach for first. The full sixteen are one tap
// away on the Style Me screen.
const QUICK_KEYS = ['office', 'wedding', 'interview', 'date', 'festival', 'everyday'];

export default function StyleMeTabScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [summary, setSummary] = useState<InventorySummary | null>(null);
  const [occasions, setOccasions] = useState<OccasionDefinition[]>([]);

  useFocusEffect(
    useCallback(() => {
      let active = true;
      void (async () => {
        try {
          const [counts, types] = await Promise.all([getInventorySummary(), getOccasionTypes()]);
          if (active) { setSummary(counts); setOccasions(types.occasions); }
        } catch (err) {
          console.warn('style hub load failed', err);
        }
      })();
      return () => { active = false; };
    }, [])
  );

  const quick = occasions.filter((occasion) => QUICK_KEYS.includes(occasion.key));
  const confirmed = summary?.total_items ?? 0;

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <ScrollView contentContainerStyle={{ padding: SPACING.lg, paddingBottom: insets.bottom + 110 }}>
        <Text style={styles.eyebrow}>YOUR DECISIONS</Text>
        <Text style={styles.title}>Style Me</Text>
        <Text style={styles.body}>
          Built from what you actually own. Anything you would need to buy is always marked as optional.
        </Text>

        <TouchableOpacity
          accessibilityRole="button"
          accessibilityLabel="Style me for an occasion"
          onPress={() => router.push('/style-me')}
          style={styles.hero}
        >
          <Ionicons name="sparkles" size={26} color={COLORS.white} />
          <View style={{ flex: 1 }}>
            <Text style={styles.heroTitle}>Style me for an occasion</Text>
            <Text style={styles.heroBody}>Three different looks from your own wardrobe</Text>
          </View>
          <Ionicons name="arrow-forward" size={20} color={COLORS.white} />
        </TouchableOpacity>

        <TouchableOpacity
          accessibilityRole="button"
          accessibilityLabel="Should I buy this"
          onPress={() => router.push('/shopping-check')}
          style={styles.secondary}
        >
          <Ionicons name="pricetag-outline" size={24} color={COLORS.primary} />
          <View style={{ flex: 1 }}>
            <Text style={styles.secondaryTitle}>Should I buy this?</Text>
            <Text style={styles.body}>Buy, Wait or Skip, with the reasoning shown</Text>
          </View>
          <Ionicons name="chevron-forward" size={18} color={COLORS.textMuted} />
        </TouchableOpacity>

        {quick.length > 0 && (
          <>
            <Text style={styles.sectionTitle}>Start with an occasion</Text>
            <View style={styles.quickGrid}>
              {quick.map((occasion) => (
                <TouchableOpacity
                  key={occasion.key}
                  accessibilityRole="button"
                  accessibilityLabel={`Style me for ${occasion.label}`}
                  onPress={() => router.push('/style-me')}
                  style={styles.quick}
                >
                  <Ionicons name={(OCCASION_ICONS[occasion.key] || 'shirt-outline') as any} size={20} color={COLORS.primary} />
                  <Text style={styles.quickLabel}>{occasion.label}</Text>
                </TouchableOpacity>
              ))}
            </View>
          </>
        )}

        <View style={styles.note}>
          <Ionicons name="information-circle-outline" size={18} color={COLORS.info} />
          <Text style={styles.noteText}>
            {confirmed > 0
              ? `${confirmed} confirmed item${confirmed === 1 ? '' : 's'} in your inventory. Only confirmed items are used in a look.`
              : 'Add a few things you own so we can build looks from them. We will never invent clothes you do not have.'}
          </Text>
        </View>

        <TouchableOpacity accessibilityRole="button" accessibilityLabel="Open my inventory" onPress={() => router.push('/(tabs)/inventory')}>
          <Text style={styles.link}>Manage my inventory</Text>
        </TouchableOpacity>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.backgroundSecondary },
  eyebrow: { fontFamily: FONTS.family.bodySemibold, color: COLORS.primary, fontSize: 10, letterSpacing: 1.3 },
  title: { fontFamily: FONTS.family.heading, color: COLORS.textPrimary, fontSize: 29, marginTop: 5, marginBottom: 6 },
  body: { fontFamily: FONTS.family.body, color: COLORS.textSecondary, fontSize: 13, lineHeight: 19 },
  hero: { flexDirection: 'row', alignItems: 'center', gap: 12, backgroundColor: COLORS.primary, borderRadius: RADIUS.xl, padding: SPACING.lg, marginTop: SPACING.lg },
  heroTitle: { fontFamily: FONTS.family.headingMedium, fontSize: 19, color: COLORS.white },
  heroBody: { fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.primaryMuted, marginTop: 3 },
  secondary: { flexDirection: 'row', alignItems: 'center', gap: 12, backgroundColor: COLORS.card, borderRadius: RADIUS.xl, padding: SPACING.lg, borderWidth: 1, borderColor: COLORS.border, marginTop: SPACING.md },
  secondaryTitle: { fontFamily: FONTS.family.headingMedium, fontSize: 17, color: COLORS.textPrimary },
  sectionTitle: { fontFamily: FONTS.family.headingMedium, fontSize: 17, color: COLORS.textPrimary, marginTop: SPACING.xl, marginBottom: SPACING.sm },
  quickGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  quick: { width: '31%', alignItems: 'center', gap: 6, paddingVertical: SPACING.md, borderRadius: RADIUS.lg, backgroundColor: COLORS.card, borderWidth: 1, borderColor: COLORS.border },
  quickLabel: { fontFamily: FONTS.family.bodyMedium, fontSize: 11, color: COLORS.textPrimary, textAlign: 'center' },
  note: { flexDirection: 'row', gap: 10, alignItems: 'flex-start', marginTop: SPACING.xl, padding: SPACING.md, borderRadius: RADIUS.md, backgroundColor: COLORS.infoLight },
  noteText: { flex: 1, fontFamily: FONTS.family.body, fontSize: 12, lineHeight: 18, color: COLORS.textSecondary },
  link: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.primary, textAlign: 'center', marginTop: SPACING.lg },
});
