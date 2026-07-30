import React, { useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ScrollView,
  ActivityIndicator,
  Alert,
} from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { api } from '../src/services/api';
import { useUserStore } from '../src/store/userStore';
import { usePlanStore } from '../src/store/planStore';
import { COLORS, FONTS, SPACING, RADIUS } from '../src/theme/colors';

const MOODS = ['Fresh & calm', 'Festive glow', 'Polished office', 'Weekend easy', 'Wedding guest'];
const OCCASIONS = ['everyday', 'office', 'festive', 'wedding_guest', 'interview', 'casual'];
const BUDGETS = [
  { id: 'budget', label: 'Budget-friendly' },
  { id: 'mid', label: 'Mid-range' },
  { id: 'comfortable', label: 'A bit more flexible' },
];

export default function GetAdviceScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { userId, user } = useUserStore();
  const setLatestPlan = usePlanStore((s) => s.setLatestPlan);
  const [mood, setMood] = useState(MOODS[0]);
  const [occasion, setOccasion] = useState('everyday');
  const [budget, setBudget] = useState('mid');
  const [loading, setLoading] = useState(false);

  const submit = async () => {
    setLoading(true);
    try {
      const res = await api.post('/plans/style', {
        user_id: userId,
        mood,
        occasion,
        budget_range: budget,
        diet: user?.diet,
        city: user?.city,
        height_cm: user?.height_cm,
        weight_kg: user?.weight_kg,
        body_type: user?.body_type,
        style_vibe: user?.style_vibe,
        follow_trends: true,
      });
      setLatestPlan(res.data.plan);
      router.push('/recommendations');
    } catch (err) {
      console.error('style plan failed', err);
      Alert.alert('Could not build plan', 'Please try again in a moment.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <View style={styles.top}>
        <TouchableOpacity onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={22} color={COLORS.textPrimary} />
        </TouchableOpacity>
        <Text style={styles.topTitle}>Style plan</Text>
        <View style={{ width: 22 }} />
      </View>

      <ScrollView contentContainerStyle={{ padding: SPACING.lg, paddingBottom: 120 }}>
        <Text style={styles.title}>Occasion stylist</Text>
        <Text style={styles.subtitle}>
          Outfits from your skin tone, body profile, occasion and wearable trends — plus care tips. Add height & weight in Profile for sharper fits.
        </Text>

        <Text style={styles.label}>How do you want to feel?</Text>
        <View style={styles.wrap}>
          {MOODS.map((m) => (
            <Chip key={m} label={m} active={mood === m} onPress={() => setMood(m)} />
          ))}
        </View>

        <Text style={styles.label}>Occasion</Text>
        <View style={styles.wrap}>
          {OCCASIONS.map((o) => (
            <Chip key={o} label={o.replace('_', ' ')} active={occasion === o} onPress={() => setOccasion(o)} />
          ))}
        </View>

        <Text style={styles.label}>Spend comfort (for ideas only)</Text>
        <View style={styles.wrap}>
          {BUDGETS.map((b) => (
            <Chip key={b.id} label={b.label} active={budget === b.id} onPress={() => setBudget(b.id)} />
          ))}
        </View>
      </ScrollView>

      <View style={[styles.footer, { paddingBottom: insets.bottom + 16 }]}>
        <TouchableOpacity style={styles.cta} onPress={submit} disabled={loading}>
          {loading ? <ActivityIndicator color={COLORS.white} /> : (
            <Text style={styles.ctaText}>Build my plan</Text>
          )}
        </TouchableOpacity>
      </View>
    </View>
  );
}

function Chip({ label, active, onPress }: { label: string; active: boolean; onPress: () => void }) {
  return (
    <TouchableOpacity onPress={onPress} style={[styles.chip, active && styles.chipActive]}>
      <Text style={[styles.chipText, active && styles.chipTextActive]}>{label}</Text>
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.background },
  top: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: SPACING.lg, paddingVertical: 12,
  },
  topTitle: { fontFamily: FONTS.family.bodySemibold, fontSize: 16, color: COLORS.textPrimary },
  title: { fontFamily: FONTS.family.heading, fontSize: 30, color: COLORS.textPrimary },
  subtitle: { fontFamily: FONTS.family.body, fontSize: 14, color: COLORS.textSecondary, marginTop: 8, marginBottom: 20, lineHeight: 20 },
  label: { fontFamily: FONTS.family.bodySemibold, fontSize: 14, color: COLORS.textPrimary, marginTop: 12, marginBottom: 10 },
  wrap: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  chip: {
    paddingHorizontal: 14, paddingVertical: 10, borderRadius: RADIUS.full,
    backgroundColor: COLORS.backgroundSecondary, borderWidth: 1, borderColor: COLORS.border,
  },
  chipActive: { backgroundColor: COLORS.primary, borderColor: COLORS.primary },
  chipText: { fontFamily: FONTS.family.bodyMedium, fontSize: 13, color: COLORS.textSecondary, textTransform: 'capitalize' },
  chipTextActive: { color: COLORS.white },
  footer: {
    position: 'absolute', left: 0, right: 0, bottom: 0, paddingHorizontal: SPACING.lg, paddingTop: 10,
    borderTopWidth: 1, borderTopColor: COLORS.border, backgroundColor: COLORS.background,
  },
  cta: { backgroundColor: COLORS.primary, borderRadius: RADIUS.lg, paddingVertical: 16, alignItems: 'center' },
  ctaText: { fontFamily: FONTS.family.bodySemibold, fontSize: 16, color: COLORS.white },
});
