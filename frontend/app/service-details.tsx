import React, { useEffect, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  ActivityIndicator,
} from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { api } from '../src/services/api';
import { COLORS, FONTS, SPACING, RADIUS } from '../src/theme/colors';

export default function ServiceDetails() {
  const { id } = useLocalSearchParams();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [idea, setIdea] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const res = await api.get(`/services/${id}`);
        setIdea(res.data);
      } catch {
        setIdea(null);
      } finally {
        setLoading(false);
      }
    })();
  }, [id]);

  if (loading) {
    return (
      <View style={[styles.container, styles.center, { paddingTop: insets.top }]}>
        <ActivityIndicator color={COLORS.primary} />
      </View>
    );
  }

  if (!idea) {
    return (
      <View style={[styles.container, styles.center, { paddingTop: insets.top }]}>
        <Text style={styles.missing}>Idea not found</Text>
        <TouchableOpacity onPress={() => router.back()}><Text style={styles.link}>Go back</Text></TouchableOpacity>
      </View>
    );
  }

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <View style={styles.top}>
        <TouchableOpacity onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={22} color={COLORS.textPrimary} />
        </TouchableOpacity>
        <Text style={styles.topTitle}>Salon idea</Text>
        <View style={{ width: 22 }} />
      </View>

      <ScrollView contentContainerStyle={{ padding: SPACING.lg, paddingBottom: 80 }}>
        <Text style={styles.category}>{idea.category}</Text>
        <Text style={styles.name}>{idea.name}</Text>
        <Text style={styles.forGoal}>{idea.for_goal}</Text>
        <Text style={styles.desc}>{idea.description}</Text>

        <View style={styles.box}>
          <Text style={styles.boxLabel}>How often (idea)</Text>
          <Text style={styles.boxValue}>{idea.how_often_idea}</Text>
        </View>

        {!!idea.suitable_for?.length && (
          <View style={styles.box}>
            <Text style={styles.boxLabel}>Often considered for</Text>
            <Text style={styles.boxValue}>{idea.suitable_for.join(', ')}</Text>
          </View>
        )}

        <View style={styles.note}>
          <Ionicons name="information-circle-outline" size={18} color={COLORS.primary} />
          <Text style={styles.noteText}>
            This is a suggestion only. GlamGenius does not take payments for salon services or book appointments.
            Visit a salon you trust if this idea feels useful for healthier-looking skin, hair, hands, or nails.
          </Text>
        </View>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.background },
  center: { alignItems: 'center', justifyContent: 'center' },
  top: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: SPACING.lg, paddingVertical: 12,
  },
  topTitle: { fontFamily: FONTS.family.bodySemibold, fontSize: 16, color: COLORS.textPrimary },
  category: { fontFamily: FONTS.family.bodySemibold, fontSize: 12, color: COLORS.accent, textTransform: 'uppercase' },
  name: { fontFamily: FONTS.family.heading, fontSize: 32, color: COLORS.textPrimary, marginTop: 8 },
  forGoal: { fontFamily: FONTS.family.bodyMedium, fontSize: 15, color: COLORS.primary, marginTop: 8 },
  desc: { fontFamily: FONTS.family.body, fontSize: 15, color: COLORS.textSecondary, marginTop: 16, lineHeight: 23 },
  box: {
    marginTop: 16, backgroundColor: COLORS.backgroundSecondary, borderRadius: RADIUS.md, padding: 14,
    borderWidth: 1, borderColor: COLORS.border,
  },
  boxLabel: { fontFamily: FONTS.family.bodySemibold, fontSize: 12, color: COLORS.textMuted },
  boxValue: { fontFamily: FONTS.family.body, fontSize: 14, color: COLORS.textPrimary, marginTop: 4 },
  note: {
    flexDirection: 'row', gap: 10, marginTop: 24, padding: 14, borderRadius: RADIUS.md,
    backgroundColor: COLORS.primaryLight,
  },
  noteText: { flex: 1, fontFamily: FONTS.family.body, fontSize: 13, color: COLORS.primaryDark, lineHeight: 19 },
  missing: { fontFamily: FONTS.family.body, color: COLORS.textSecondary },
  link: { marginTop: 12, fontFamily: FONTS.family.bodySemibold, color: COLORS.primary },
});
