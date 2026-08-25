/**
 * Skin & Hair maintenance timing (VC-06).
 *
 * Replaces the V2 placeholder. This screen answers one question — what upkeep
 * is due — for the kinds the customer has chosen to track, on intervals they
 * set themselves. GlamGenius does not book anything, suggest anywhere, or
 * comment on how anyone looks.
 *
 * The route keeps its `services` filename so existing deep links resolve.
 */
import React, { useCallback, useState } from 'react';
import { ActivityIndicator, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useFocusEffect } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { MaintenanceEmpty, MaintenanceRow } from '../../src/components/care/MaintenancePieces';
import {
  MaintenanceOverview,
  getMaintenance,
  recordMaintenanceDone,
  updateMaintenance,
} from '../../src/services/apiV2';
import { COLORS, FONTS, SPACING } from '../../src/theme/colors';

export default function MaintenanceScreen() {
  const insets = useSafeAreaInsets();
  const [overview, setOverview] = useState<MaintenanceOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (mode: 'initial' | 'refresh' = 'initial') => {
    if (mode === 'refresh') setRefreshing(true);
    else setLoading(true);
    try {
      setOverview(await getMaintenance());
      setError(null);
    } catch {
      setError('We could not load your upkeep timing right now.');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useFocusEffect(useCallback(() => { void load(); }, [load]));

  const run = async (action: () => Promise<MaintenanceOverview>) => {
    setBusy(true);
    try {
      setOverview(await action());
      setError(null);
    } catch {
      setError('That change did not save. Please try again.');
    } finally {
      setBusy(false);
    }
  };

  const tracked = overview?.kinds.filter((row) => row.tracked) ?? [];
  const available = overview?.kinds.filter((row) => !row.tracked) ?? [];

  if (loading && !overview) {
    return (
      <View style={[styles.container, styles.centre, { paddingTop: insets.top }]}>
        <ActivityIndicator color={COLORS.primary} />
      </View>
    );
  }

  return (
    <View style={[styles.container, { paddingTop: insets.top }]} testID="maintenance-screen">
      <ScrollView
        contentContainerStyle={styles.body}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void load('refresh')} />}
      >
        <Text style={styles.eyebrow}>CARE</Text>
        <Text style={styles.title}>Upkeep timing</Text>
        <Text style={styles.subtitle}>{overview?.note}</Text>

        {!!error && <Text style={styles.error}>{error}</Text>}

        <Text style={styles.section}>What you track</Text>
        {tracked.length === 0 ? (
          <MaintenanceEmpty />
        ) : (
          tracked.map((row) => (
            <MaintenanceRow
              key={row.kind}
              kind={row}
              busy={busy}
              onTrack={() => void run(() => updateMaintenance(row.kind, { tracked: true }))}
              onUntrack={() => void run(() => updateMaintenance(row.kind, { tracked: false }))}
              onRecordToday={() => void run(() => recordMaintenanceDone(row.kind))}
            />
          ))
        )}

        {available.length > 0 && (
          <>
            <Text style={styles.section}>Add something you already do</Text>
            {available.map((row) => (
              <MaintenanceRow
                key={row.kind}
                kind={row}
                busy={busy}
                onTrack={() => void run(() => updateMaintenance(row.kind, { tracked: true }))}
                onUntrack={() => void run(() => updateMaintenance(row.kind, { tracked: false }))}
                onRecordToday={() => void run(() => recordMaintenanceDone(row.kind))}
              />
            ))}
          </>
        )}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.backgroundSecondary },
  centre: { alignItems: 'center', justifyContent: 'center' },
  body: { padding: SPACING.lg, paddingBottom: SPACING.xl },
  eyebrow: {
    fontFamily: FONTS.family.bodySemibold,
    fontSize: 11,
    color: COLORS.primary,
    letterSpacing: 1.4,
  },
  title: { fontFamily: FONTS.family.heading, fontSize: 28, color: COLORS.textPrimary, marginTop: 4 },
  subtitle: {
    fontFamily: FONTS.family.body,
    fontSize: 14,
    color: COLORS.textSecondary,
    marginTop: 10,
    lineHeight: 21,
  },
  section: {
    fontFamily: FONTS.family.headingMedium,
    fontSize: 18,
    color: COLORS.textPrimary,
    marginTop: SPACING.xl,
  },
  error: { fontFamily: FONTS.family.body, fontSize: 13, color: COLORS.error, marginTop: SPACING.md },
});
