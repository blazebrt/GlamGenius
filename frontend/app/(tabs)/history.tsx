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
import Animated, { FadeInDown } from 'react-native-reanimated';
import { api } from '../../src/services/api';
import { useUserStore } from '../../src/store/userStore';
import { COLORS, FONTS, SPACING, RADIUS } from '../../src/theme/colors';

export default function HistoryScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { userId } = useUserStore();
  const [visits, setVisits] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadHistory();
  }, []);

  const loadHistory = async () => {
    if (!userId) {
      setVisits([]);
      setLoading(false);
      return;
    }
    try {
      const response = await api.get(`/visits/${userId}`);
      setVisits(response.data);
    } catch (error) {
      console.error('Error loading history:', error);
    } finally {
      setLoading(false);
    }
  };

  const formatDate = (dateString: string) => {
    const date = new Date(dateString);
    return date.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
  };

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      {/* Header */}
      <View style={styles.header}>
        <Text style={styles.headerLabel}>YOUR JOURNEY</Text>
        <Text style={styles.headerTitle}>Visit History</Text>
      </View>

      {loading ? (
        <View style={styles.loadingContainer}>
          <ActivityIndicator size="large" color={COLORS.black} />
        </View>
      ) : visits.length === 0 ? (
        <View style={styles.emptyContainer}>
          <View style={styles.emptyIcon}>
            <Ionicons name="time-outline" size={48} color={COLORS.textMuted} />
          </View>
          <Text style={styles.emptyTitle}>No visits yet</Text>
          <Text style={styles.emptySubtitle}>Your appointment history will appear here after your first booking</Text>
          <TouchableOpacity style={styles.bookButton} onPress={() => router.push('/get-advice')}>
            <Ionicons name="sparkles" size={18} color={COLORS.white} />
            <Text style={styles.bookButtonText}>Get Personalized Advice</Text>
          </TouchableOpacity>
        </View>
      ) : (
        <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={styles.listContent}>
          {visits.map((visit, index) => (
            <Animated.View key={visit.id} entering={FadeInDown.delay(index * 50)}>
              <View style={styles.visitCard}>
                <View style={styles.visitHeader}>
                  <View style={styles.visitDate}>
                    <Ionicons name="calendar-outline" size={16} color={COLORS.black} />
                    <Text style={styles.visitDateText}>{formatDate(visit.date)}</Text>
                  </View>
                  <View style={[styles.statusBadge, visit.status === 'completed' && styles.statusCompleted]}>
                    <Text style={[styles.statusText, visit.status === 'completed' && styles.statusTextCompleted]}>
                      {visit.status || 'Completed'}
                    </Text>
                  </View>
                </View>
                <View style={styles.visitServices}>
                  {visit.services?.map((service: string, i: number) => (
                    <View key={i} style={styles.serviceTag}>
                      <Text style={styles.serviceTagText}>{service}</Text>
                    </View>
                  ))}
                </View>
                {visit.notes && <Text style={styles.visitNotes}>{visit.notes}</Text>}
                {visit.rating && (
                  <View style={styles.ratingContainer}>
                    {[1, 2, 3, 4, 5].map(star => (
                      <Ionicons key={star} name={star <= visit.rating ? 'star' : 'star-outline'} size={16} color={COLORS.black} />
                    ))}
                  </View>
                )}
              </View>
            </Animated.View>
          ))}
          <View style={{ height: 100 }} />
        </ScrollView>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { 
    flex: 1, 
    backgroundColor: COLORS.backgroundSecondary,
  },
  header: { 
    paddingHorizontal: SPACING.lg, 
    paddingVertical: SPACING.md,
    backgroundColor: COLORS.background,
  },
  headerLabel: {
    fontSize: FONTS.sizes.caption,
    fontFamily: FONTS.family.bodySemibold,
    color: COLORS.primary,
    letterSpacing: 2,
  },
  headerTitle: { 
    fontSize: FONTS.sizes.h1, 
    fontFamily: FONTS.family.heading, 
    color: COLORS.textPrimary,
    marginTop: 4,
  },
  loadingContainer: { 
    flex: 1, 
    justifyContent: 'center', 
    alignItems: 'center',
  },
  emptyContainer: { 
    flex: 1, 
    justifyContent: 'center', 
    alignItems: 'center', 
    paddingHorizontal: SPACING.xl,
  },
  emptyIcon: { 
    width: 100, 
    height: 100, 
    borderRadius: 50, 
    backgroundColor: COLORS.backgroundSecondary, 
    justifyContent: 'center', 
    alignItems: 'center', 
    marginBottom: SPACING.lg,
    borderWidth: 2,
    borderColor: COLORS.border,
  },
  emptyTitle: { 
    fontSize: FONTS.sizes.h2, 
    fontFamily: FONTS.family.heading, 
    color: COLORS.textPrimary,
  },
  emptySubtitle: { 
    fontSize: FONTS.sizes.body, 
    fontFamily: FONTS.family.body,
    color: COLORS.textSecondary, 
    marginTop: SPACING.sm, 
    textAlign: 'center',
    lineHeight: 22,
  },
  bookButton: { 
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: COLORS.black, 
    paddingVertical: 16, 
    paddingHorizontal: 28, 
    borderRadius: RADIUS.full, 
    marginTop: SPACING.xl,
    gap: SPACING.sm,
  },
  bookButtonText: { 
    fontSize: FONTS.sizes.body, 
    fontFamily: FONTS.family.bodySemibold, 
    color: COLORS.white,
  },
  listContent: { 
    paddingHorizontal: SPACING.lg, 
    paddingTop: SPACING.md,
  },
  visitCard: { 
    backgroundColor: COLORS.card, 
    borderRadius: RADIUS.lg, 
    padding: SPACING.lg, 
    marginBottom: SPACING.md, 
    borderWidth: 1, 
    borderColor: COLORS.border,
  },
  visitHeader: { 
    flexDirection: 'row', 
    justifyContent: 'space-between', 
    alignItems: 'center', 
    marginBottom: SPACING.md,
  },
  visitDate: { 
    flexDirection: 'row', 
    alignItems: 'center', 
    gap: SPACING.xs,
  },
  visitDateText: { 
    fontSize: FONTS.sizes.body, 
    fontFamily: FONTS.family.bodySemibold, 
    color: COLORS.textPrimary,
  },
  statusBadge: { 
    backgroundColor: COLORS.backgroundSecondary, 
    paddingHorizontal: SPACING.sm, 
    paddingVertical: 4, 
    borderRadius: RADIUS.full,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  statusCompleted: { 
    backgroundColor: COLORS.black,
    borderColor: COLORS.black,
  },
  statusText: { 
    fontSize: FONTS.sizes.caption, 
    fontFamily: FONTS.family.bodySemibold, 
    color: COLORS.textMuted,
    textTransform: 'capitalize',
  },
  statusTextCompleted: { 
    color: COLORS.white,
  },
  visitServices: { 
    flexDirection: 'row', 
    flexWrap: 'wrap', 
    gap: SPACING.xs,
  },
  serviceTag: { 
    backgroundColor: COLORS.backgroundSecondary, 
    paddingHorizontal: SPACING.sm, 
    paddingVertical: 6, 
    borderRadius: RADIUS.md,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  serviceTagText: { 
    fontSize: FONTS.sizes.bodySm, 
    fontFamily: FONTS.family.bodyMedium, 
    color: COLORS.textSecondary,
  },
  visitNotes: { 
    fontSize: FONTS.sizes.bodySm, 
    fontFamily: FONTS.family.body,
    color: COLORS.textSecondary, 
    marginTop: SPACING.md, 
    fontStyle: 'italic',
    lineHeight: 20,
  },
  ratingContainer: { 
    flexDirection: 'row', 
    marginTop: SPACING.md, 
    gap: 2,
  },
});
