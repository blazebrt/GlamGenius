import React, { useEffect, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  ActivityIndicator,
} from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { api } from '../../src/services/api';
import { COLORS, FONTS, SPACING, RADIUS, SHADOWS } from '../../src/theme/colors';

export default function SalonIdeasScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [ideas, setIdeas] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const res = await api.get('/salon-ideas');
        setIdeas(res.data);
      } catch {
        setIdeas([]);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const categories = Array.from(new Set(ideas.map((i) => i.category)));
  const shown = filter ? ideas.filter((i) => i.category === filter) : ideas;

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <View style={styles.header}>
        <Text style={styles.label}>OPTIONAL</Text>
        <Text style={styles.title}>Salon ideas</Text>
        <Text style={styles.subtitle}>
          Soft suggestions for healthier-looking skin, hair, hands & nails. No prices. No booking in the app.
        </Text>
      </View>

      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.filters} contentContainerStyle={{ paddingHorizontal: SPACING.lg, gap: 8 }}>
        <Chip label="All" active={!filter} onPress={() => setFilter(null)} />
        {categories.map((c) => (
          <Chip key={c} label={c} active={filter === c} onPress={() => setFilter(c)} />
        ))}
      </ScrollView>

      {loading ? (
        <ActivityIndicator style={{ marginTop: 40 }} color={COLORS.primary} />
      ) : (
        <ScrollView contentContainerStyle={{ padding: SPACING.lg, paddingBottom: 120 }}>
          {shown.map((idea) => (
            <TouchableOpacity
              key={idea.id}
              style={styles.card}
              onPress={() => router.push({ pathname: '/service-details', params: { id: idea.id } })}
            >
              <View style={styles.cardTop}>
                <Text style={styles.category}>{idea.category}</Text>
                <Ionicons name="chevron-forward" size={18} color={COLORS.textMuted} />
              </View>
              <Text style={styles.name}>{idea.name}</Text>
              <Text style={styles.forGoal}>{idea.for_goal}</Text>
              <Text style={styles.desc} numberOfLines={2}>{idea.description}</Text>
              <Text style={styles.often}>Idea: {idea.how_often_idea}</Text>
            </TouchableOpacity>
          ))}
        </ScrollView>
      )}
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
  container: { flex: 1, backgroundColor: COLORS.backgroundSecondary },
  header: { paddingHorizontal: SPACING.lg, paddingTop: SPACING.md },
  label: { fontFamily: FONTS.family.bodySemibold, fontSize: 11, color: COLORS.primary, letterSpacing: 1.4 },
  title: { fontFamily: FONTS.family.heading, fontSize: 28, color: COLORS.textPrimary, marginTop: 4 },
  subtitle: { fontFamily: FONTS.family.body, fontSize: 13, color: COLORS.textSecondary, marginTop: 6, lineHeight: 19 },
  filters: { marginTop: 16, maxHeight: 44 },
  chip: {
    paddingHorizontal: 14, paddingVertical: 8, borderRadius: RADIUS.full,
    backgroundColor: COLORS.card, borderWidth: 1, borderColor: COLORS.border,
  },
  chipActive: { backgroundColor: COLORS.primary, borderColor: COLORS.primary },
  chipText: { fontFamily: FONTS.family.bodyMedium, fontSize: 13, color: COLORS.textSecondary },
  chipTextActive: { color: COLORS.white },
  card: {
    backgroundColor: COLORS.card, borderRadius: RADIUS.lg, padding: 16, marginBottom: 12,
    borderWidth: 1, borderColor: COLORS.border, ...SHADOWS.sm,
  },
  cardTop: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  category: { fontFamily: FONTS.family.bodySemibold, fontSize: 11, color: COLORS.accent, textTransform: 'uppercase' },
  name: { fontFamily: FONTS.family.headingMedium, fontSize: 20, color: COLORS.textPrimary, marginTop: 6 },
  forGoal: { fontFamily: FONTS.family.bodyMedium, fontSize: 13, color: COLORS.primary, marginTop: 4 },
  desc: { fontFamily: FONTS.family.body, fontSize: 13, color: COLORS.textSecondary, marginTop: 8, lineHeight: 19 },
  often: { fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.textMuted, marginTop: 10 },
});
