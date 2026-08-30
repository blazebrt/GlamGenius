/**
 * Admin dashboard (private beta operators only).
 *
 * Currently a single tile: reservation throughput — live vs consumed vs
 * expired across the whole project, plus which invites are holding live
 * reservations right now. Poll interval is intentionally slow: this is a
 * "peek in" screen, not a live monitor.
 *
 * The route is guarded on both sides:
 *   • frontend: redirects non-admins home.
 *   • backend:  ``get_current_admin`` returns 403 to non-admins.
 *
 * A non-admin who deep-links here therefore sees the redirect fire
 * immediately, never the tile.
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  RefreshControl,
  TouchableOpacity,
  ActivityIndicator,
} from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { useUserStore } from '../src/store/userStore';
import { getReservationStats, ReservationStats } from '../src/services/apiV2';
import { COLORS, FONTS, SPACING, RADIUS, SHADOWS } from '../src/theme/colors';

export default function AdminDashboard() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { isAdmin, initialized, registrationState } = useUserStore();
  const [stats, setStats] = useState<ReservationStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const data = await getReservationStats();
      setStats(data);
    } catch (err: any) {
      const status = err?.response?.status;
      if (status === 403) {
        setError('You are not an admin on this project.');
      } else {
        setError('Could not load reservation stats.');
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    // Route guard runs after userStore is hydrated so we don't redirect a
    // legitimate admin while the /me response is still in flight.
    if (!initialized) return;
    if (registrationState !== 'registered') {
      router.replace('/(auth)/welcome');
      return;
    }
    if (!isAdmin) {
      router.replace('/(tabs)/today');
      return;
    }
    void load();
  }, [initialized, isAdmin, registrationState, router, load]);

  const onRefresh = () => {
    setRefreshing(true);
    void load();
  };

  if (!isAdmin) return null;

  const totals = stats?.totals ?? { total: 0, active: 0, consumed: 0, expired: 0 };
  const conversion =
    totals.total > 0 ? Math.round((totals.consumed / totals.total) * 100) : 0;

  return (
    <View style={[styles.container, { paddingTop: insets.top + 12 }]} testID="admin-dashboard">
      <View style={styles.header}>
        <TouchableOpacity
          testID="admin-back"
          onPress={() => {
            try {
              if (router.canGoBack()) router.back();
              else router.replace('/(tabs)/today');
            } catch {
              router.replace('/(tabs)/today');
            }
          }}
          style={styles.backButton}
        >
          <Ionicons name="arrow-back" size={22} color={COLORS.textPrimary} />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>Admin</Text>
        <View style={{ width: 22 }} />
      </View>

      <ScrollView
        contentContainerStyle={{ paddingHorizontal: SPACING.lg, paddingBottom: 40 }}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
      >
        <Text style={styles.tileHeading}>Reservation throughput</Text>
        <Text style={styles.tileSub}>
          Where private-beta invites are right now.
        </Text>

        <View style={styles.tile} testID="reservation-stats-tile">
          {loading ? (
            <ActivityIndicator color={COLORS.primary} />
          ) : error ? (
            <Text style={styles.errorText} testID="reservation-stats-error">
              {error}
            </Text>
          ) : (
            <>
              <View style={styles.row}>
                <StatCell
                  label="Live"
                  value={totals.active}
                  tint={COLORS.primaryLight}
                  color={COLORS.primary}
                  testID="reservation-stats-active"
                />
                <StatCell
                  label="Consumed"
                  value={totals.consumed}
                  tint="#E6F7EE"
                  color={COLORS.success}
                  testID="reservation-stats-consumed"
                />
                <StatCell
                  label="Expired"
                  value={totals.expired}
                  tint="#FDF3E7"
                  color={COLORS.warning}
                  testID="reservation-stats-expired"
                />
              </View>
              <View style={styles.footerRow}>
                <Text style={styles.footerText}>
                  {totals.total} reservations · {conversion}% consumed
                </Text>
              </View>
            </>
          )}
        </View>

        {!loading && !error && stats && stats.active_by_invite.length > 0 && (
          <View style={{ marginTop: SPACING.lg }}>
            <Text style={styles.subHeading}>Invites with live reservations</Text>
            {stats.active_by_invite.map((row) => {
              const remaining = Math.max(0, row.max_uses - row.uses_count);
              return (
                <View
                  key={row.invite_id}
                  style={styles.inviteRow}
                  testID={`invite-row-${row.code}`}
                >
                  <View style={{ flex: 1 }}>
                    <Text style={styles.inviteCode}>{row.code}</Text>
                    {!!row.label && (
                      <Text style={styles.inviteLabel}>{row.label}</Text>
                    )}
                  </View>
                  <View style={{ alignItems: 'flex-end' }}>
                    <Text style={styles.inviteLive}>{row.live_reservations} live</Text>
                    <Text style={styles.inviteMeta}>
                      {row.uses_count}/{row.max_uses} used · {remaining} left
                    </Text>
                  </View>
                </View>
              );
            })}
          </View>
        )}

        {stats && (
          <Text style={styles.generatedAt}>
            Generated {new Date(stats.generated_at).toLocaleString()}
          </Text>
        )}

        {/* The knowledge authoring tool. Admin-only on both sides, like this screen. */}
        <TouchableOpacity
          accessibilityRole="button"
          accessibilityLabel="Open knowledge entries"
          onPress={() => router.push('/admin-knowledge')}
          style={styles.toolLink}
        >
          <Text style={styles.toolLinkTitle}>Knowledge entries</Text>
          <Text style={styles.toolLinkBody}>
            Add, review, approve and publish what the product is allowed to say.
          </Text>
        </TouchableOpacity>
      </ScrollView>
    </View>
  );
}

function StatCell({
  label,
  value,
  tint,
  color,
  testID,
}: {
  label: string;
  value: number;
  tint: string;
  color: string;
  testID: string;
}) {
  return (
    <View style={[styles.statCell, { backgroundColor: tint }]} testID={testID}>
      <Text style={[styles.statValue, { color }]}>{value}</Text>
      <Text style={styles.statLabel}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.background },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: SPACING.lg,
    paddingBottom: 12,
  },
  backButton: { padding: 4 },
  headerTitle: {
    fontFamily: FONTS.family.headingMedium,
    fontSize: 18,
    color: COLORS.textPrimary,
  },
  tileHeading: {
    fontFamily: FONTS.family.heading,
    fontSize: 26,
    color: COLORS.textPrimary,
    marginTop: 8,
  },
  tileSub: {
    fontFamily: FONTS.family.body,
    fontSize: 14,
    color: COLORS.textSecondary,
    marginTop: 4,
    marginBottom: SPACING.md,
  },
  toolLink: {
    backgroundColor: COLORS.card,
    borderColor: COLORS.border,
    borderRadius: RADIUS.lg,
    borderWidth: 1,
    marginTop: SPACING.lg,
    padding: SPACING.md,
  },
  toolLinkTitle: {
    color: COLORS.textPrimary,
    fontFamily: FONTS.family.headingMedium,
    fontSize: 17,
  },
  toolLinkBody: {
    color: COLORS.textSecondary,
    fontFamily: FONTS.family.body,
    fontSize: 13,
    marginTop: 4,
  },
  tile: {
    backgroundColor: COLORS.card,
    borderRadius: RADIUS.xl,
    padding: SPACING.md,
    borderWidth: 1,
    borderColor: COLORS.border,
    ...SHADOWS.sm,
  },
  row: { flexDirection: 'row', gap: 10 },
  statCell: {
    flex: 1,
    paddingVertical: 18,
    paddingHorizontal: 12,
    borderRadius: RADIUS.lg,
    alignItems: 'center',
  },
  statValue: {
    fontFamily: FONTS.family.headingMedium,
    fontSize: 28,
    lineHeight: 32,
  },
  statLabel: {
    fontFamily: FONTS.family.bodyMedium,
    fontSize: 12,
    color: COLORS.textSecondary,
    marginTop: 4,
    letterSpacing: 0.3,
    textTransform: 'uppercase',
  },
  footerRow: {
    marginTop: 14,
    paddingTop: 12,
    borderTopWidth: 1,
    borderTopColor: COLORS.border,
    alignItems: 'center',
  },
  footerText: {
    fontFamily: FONTS.family.body,
    fontSize: 13,
    color: COLORS.textSecondary,
  },
  errorText: {
    fontFamily: FONTS.family.body,
    fontSize: 14,
    color: COLORS.textSecondary,
    textAlign: 'center',
  },
  subHeading: {
    fontFamily: FONTS.family.headingMedium,
    fontSize: 15,
    color: COLORS.textPrimary,
    marginBottom: 10,
  },
  inviteRow: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: COLORS.card,
    padding: 14,
    borderRadius: RADIUS.md,
    borderWidth: 1,
    borderColor: COLORS.border,
    marginBottom: 8,
  },
  inviteCode: {
    fontFamily: FONTS.family.bodySemibold,
    fontSize: 15,
    color: COLORS.textPrimary,
    letterSpacing: 0.5,
  },
  inviteLabel: {
    fontFamily: FONTS.family.body,
    fontSize: 12,
    color: COLORS.textSecondary,
    marginTop: 2,
  },
  inviteLive: {
    fontFamily: FONTS.family.bodySemibold,
    fontSize: 14,
    color: COLORS.primary,
  },
  inviteMeta: {
    fontFamily: FONTS.family.body,
    fontSize: 11,
    color: COLORS.textMuted,
    marginTop: 2,
  },
  generatedAt: {
    marginTop: SPACING.lg,
    fontFamily: FONTS.family.body,
    fontSize: 11,
    color: COLORS.textMuted,
    textAlign: 'center',
  },
});
