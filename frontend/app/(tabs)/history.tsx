import React, { useCallback, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  ActivityIndicator,
  RefreshControl,
} from 'react-native';
import { useFocusEffect, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { api } from '../../src/services/api';
import { useUserStore } from '../../src/store/userStore';
import { COLORS, FONTS, SPACING, RADIUS } from '../../src/theme/colors';

export default function HistoryScreen() {
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { userId } = useUserStore();
  const [scans, setScans] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    if (!userId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const h = await api.get('/api/v2/scan/history');
      setScans(h.data || []);
    } catch {
      setScans([]);
    } finally {
      setLoading(false);
    }
  }, [userId]);

  useFocusEffect(
    useCallback(() => {
      void load();
    }, [load])
  );

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <View style={styles.header}>
        <Text style={styles.label}>PROGRESS</Text>
        <Text style={styles.title}>Your checks</Text>
        <Text style={styles.subtitle}>Review your saved observations and coach plans over time.</Text>
      </View>

      {loading ? (
        <ActivityIndicator style={{ marginTop: 40 }} color={COLORS.primary} />
      ) : (
        <ScrollView
          contentContainerStyle={{ padding: SPACING.lg, paddingBottom: 120 }}
          refreshControl={<RefreshControl refreshing={loading} onRefresh={load} />}
        >
          {!scans.length && (
            <View style={styles.empty}>
              <Ionicons name="time-outline" size={36} color={COLORS.textMuted} />
              <Text style={styles.emptyTitle}>No checks yet</Text>
              <Text style={styles.emptyText}>Run a skin or hair check to start your history.</Text>
              <TouchableOpacity style={styles.cta} onPress={() => router.push('/(tabs)/scan-tab')}>
                <Text style={styles.ctaText}>Start a check</Text>
              </TouchableOpacity>
            </View>
          )}

          {scans.map((s) => {
            const headline = s.analysis?.coach_summary?.headline;
            return (
              <View key={s.id} style={styles.card}>
                <View style={styles.cardTop}>
                  <Text style={styles.scanType}>{(s.scan_type || 'check').toUpperCase()}</Text>
                  <Text style={styles.date}>
                    {s.created_at ? new Date(s.created_at).toLocaleDateString('en-IN') : ''}
                  </Text>
                </View>
                <Text style={styles.headline} numberOfLines={2}>
                  {headline || 'Coach plan saved'}
                </Text>
              </View>
            );
          })}
        </ScrollView>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.backgroundSecondary },
  header: { paddingHorizontal: SPACING.lg, paddingTop: SPACING.md },
  label: { fontFamily: FONTS.family.bodySemibold, fontSize: 11, color: COLORS.primary, letterSpacing: 1.4 },
  title: { fontFamily: FONTS.family.heading, fontSize: 28, color: COLORS.textPrimary, marginTop: 4 },
  subtitle: { fontFamily: FONTS.family.body, fontSize: 13, color: COLORS.textSecondary, marginTop: 6 },
  card: {
    backgroundColor: COLORS.card, borderRadius: RADIUS.lg, padding: 16, marginBottom: 10,
    borderWidth: 1, borderColor: COLORS.border,
  },
  cardTop: { flexDirection: 'row', justifyContent: 'space-between' },
  scanType: { fontFamily: FONTS.family.bodySemibold, fontSize: 11, color: COLORS.primary },
  date: { fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.textMuted },
  headline: { fontFamily: FONTS.family.headingMedium, fontSize: 16, color: COLORS.textPrimary, marginTop: 8 },
  empty: { alignItems: 'center', paddingVertical: 48 },
  emptyTitle: { fontFamily: FONTS.family.headingMedium, fontSize: 18, color: COLORS.textPrimary, marginTop: 12 },
  emptyText: { fontFamily: FONTS.family.body, fontSize: 13, color: COLORS.textSecondary, marginTop: 6 },
  cta: { marginTop: 16, backgroundColor: COLORS.primary, paddingHorizontal: 18, paddingVertical: 12, borderRadius: RADIUS.md },
  ctaText: { fontFamily: FONTS.family.bodySemibold, color: COLORS.white },
});
