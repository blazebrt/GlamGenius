import React, { useMemo } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
} from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { COLORS, FONTS, SPACING, RADIUS } from '../src/theme/colors';

export default function RecommendationsScreen() {
  const { plan: planParam } = useLocalSearchParams();
  const router = useRouter();
  const insets = useSafeAreaInsets();

  const plan = useMemo(() => {
    try {
      return typeof planParam === 'string' ? JSON.parse(planParam) : planParam || {};
    } catch {
      return {};
    }
  }, [planParam]);

  const style = plan.style || {};
  const nutrition = plan.nutrition || {};
  const care = plan.care_ingredients || {};
  const salon = plan.salon_suggestions || [];
  const daily = plan.daily_care || {};

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <View style={styles.top}>
        <TouchableOpacity onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={22} color={COLORS.textPrimary} />
        </TouchableOpacity>
        <Text style={styles.topTitle}>Your plan</Text>
        <View style={{ width: 22 }} />
      </View>

      <ScrollView contentContainerStyle={{ padding: SPACING.lg, paddingBottom: 100 }}>
        <Text style={styles.headline}>{plan.headline || 'Your personal style & wellness plan'}</Text>

        <Text style={styles.section}>Colours & outfits</Text>
        {(style.best_clothing_colors || []).map((c: any, i: number) => (
          <Text key={i} style={styles.line}>• {c.color} — {c.why}</Text>
        ))}
        {(style.wardrobe_ideas_india || []).map((w: any, i: number) => (
          <View key={`w${i}`} style={styles.card}>
            <Text style={styles.cardTitle}>{w.occasion}</Text>
            <Text style={styles.line}>{w.outfit_idea}</Text>
            <Text style={styles.muted}>{w.why_it_works}</Text>
          </View>
        ))}

        <Text style={styles.section}>Daily care</Text>
        {(daily.morning || []).map((s: string, i: number) => (
          <Text key={`m${i}`} style={styles.line}>AM · {s}</Text>
        ))}
        {(daily.evening || []).map((s: string, i: number) => (
          <Text key={`e${i}`} style={styles.line}>PM · {s}</Text>
        ))}

        <Text style={styles.section}>Ingredients to look for</Text>
        {(care.for_skin || []).map((ing: any, i: number) => (
          <Text key={`s${i}`} style={styles.line}>Skin · {ing.ingredient} — {ing.why}</Text>
        ))}
        {(care.for_hair || []).map((ing: any, i: number) => (
          <Text key={`h${i}`} style={styles.line}>Hair · {ing.ingredient} — {ing.why}</Text>
        ))}

        <Text style={styles.section}>Indian foods</Text>
        {(nutrition.ingredients || []).map((n: any, i: number) => (
          <View key={i} style={styles.card}>
            <Text style={styles.cardTitle}>{n.ingredient}</Text>
            <Text style={styles.muted}>{n.why_for_skin_or_hair}</Text>
            {(n.indian_foods || []).map((f: any, j: number) => (
              <Text key={j} style={styles.line}>· {f.food} — {f.serving_idea}</Text>
            ))}
          </View>
        ))}

        <Text style={styles.section}>Salon ideas</Text>
        <Text style={styles.muted}>Suggestions only — no pay or booking in-app.</Text>
        {salon.map((s: any, i: number) => (
          <Text key={i} style={styles.line}>• {s.service}: {s.for}</Text>
        ))}

        <Text style={styles.section}>This week</Text>
        {(plan.top_3_actions_this_week || []).map((a: string, i: number) => (
          <Text key={i} style={styles.line}>{i + 1}. {a}</Text>
        ))}

        <Text style={styles.disclaimer}>
          {plan.disclaimer || 'General wellness and style guidance — not medical advice.'}
        </Text>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.background },
  top: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: SPACING.lg, paddingVertical: 12,
  },
  topTitle: { fontFamily: FONTS.family.bodySemibold, fontSize: 16, color: COLORS.textPrimary },
  headline: { fontFamily: FONTS.family.heading, fontSize: 26, color: COLORS.textPrimary, marginBottom: 8 },
  section: { fontFamily: FONTS.family.headingMedium, fontSize: 18, color: COLORS.textPrimary, marginTop: 22, marginBottom: 8 },
  line: { fontFamily: FONTS.family.body, fontSize: 14, color: COLORS.textPrimary, marginBottom: 6, lineHeight: 20 },
  muted: { fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.textSecondary, marginBottom: 6 },
  card: {
    backgroundColor: COLORS.backgroundSecondary, borderRadius: RADIUS.md, padding: 12, marginBottom: 8,
    borderWidth: 1, borderColor: COLORS.border,
  },
  cardTitle: { fontFamily: FONTS.family.bodySemibold, fontSize: 14, color: COLORS.primary, marginBottom: 4, textTransform: 'capitalize' },
  disclaimer: { marginTop: 28, fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.textMuted, lineHeight: 18 },
});
