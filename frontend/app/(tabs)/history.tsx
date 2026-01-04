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
      <View style={styles.header}>
        <Text style={styles.headerTitle}>Visit History</Text>
        <Text style={styles.headerSubtitle}>Your past appointments</Text>
      </View>

      {loading ? (
        <View style={styles.loadingContainer}>
          <ActivityIndicator size="large" color="#0EA5E9" />
        </View>
      ) : visits.length === 0 ? (
        <View style={styles.emptyContainer}>
          <View style={styles.emptyIcon}>
            <Ionicons name="time-outline" size={48} color="#E2E8F0" />
          </View>
          <Text style={styles.emptyTitle}>No visits yet</Text>
          <Text style={styles.emptySubtitle}>Your appointment history will appear here</Text>
          <TouchableOpacity style={styles.bookButton} onPress={() => router.push('/get-advice')}>
            <Text style={styles.bookButtonText}>Book Now</Text>
          </TouchableOpacity>
        </View>
      ) : (
        <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={styles.listContent}>
          {visits.map((visit, index) => (
            <Animated.View key={visit.id} entering={FadeInDown.delay(index * 50)}>
              <View style={styles.visitCard}>
                <View style={styles.visitHeader}>
                  <View style={styles.visitDate}>
                    <Ionicons name="calendar-outline" size={16} color="#0EA5E9" />
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
                      <Ionicons key={star} name={star <= visit.rating ? 'star' : 'star-outline'} size={16} color="#F59E0B" />
                    ))}
                  </View>
                )}
              </View>
            </Animated.View>
          ))}
        </ScrollView>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#F8FAFC' },
  header: { paddingHorizontal: 20, paddingVertical: 16 },
  headerTitle: { fontSize: 24, fontWeight: '700', color: '#1E293B' },
  headerSubtitle: { fontSize: 14, color: '#64748B', marginTop: 4 },
  loadingContainer: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  emptyContainer: { flex: 1, justifyContent: 'center', alignItems: 'center', paddingHorizontal: 40 },
  emptyIcon: { width: 80, height: 80, borderRadius: 40, backgroundColor: '#F1F5F9', justifyContent: 'center', alignItems: 'center', marginBottom: 16 },
  emptyTitle: { fontSize: 18, fontWeight: '600', color: '#1E293B' },
  emptySubtitle: { fontSize: 14, color: '#64748B', marginTop: 4, textAlign: 'center' },
  bookButton: { backgroundColor: '#0EA5E9', paddingVertical: 12, paddingHorizontal: 28, borderRadius: 24, marginTop: 20 },
  bookButtonText: { fontSize: 14, fontWeight: '600', color: '#FFFFFF' },
  listContent: { paddingHorizontal: 20, paddingBottom: 100 },
  visitCard: { backgroundColor: '#FFFFFF', borderRadius: 16, padding: 18, marginBottom: 12, borderWidth: 1, borderColor: '#E2E8F0' },
  visitHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 },
  visitDate: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  visitDateText: { fontSize: 14, fontWeight: '500', color: '#1E293B' },
  statusBadge: { backgroundColor: '#F1F5F9', paddingHorizontal: 10, paddingVertical: 4, borderRadius: 12 },
  statusCompleted: { backgroundColor: '#D1FAE5' },
  statusText: { fontSize: 12, fontWeight: '500', color: '#64748B' },
  statusTextCompleted: { color: '#059669' },
  visitServices: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  serviceTag: { backgroundColor: '#E0F2FE', paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8 },
  serviceTagText: { fontSize: 12, fontWeight: '500', color: '#0284C7' },
  visitNotes: { fontSize: 13, color: '#64748B', marginTop: 12, fontStyle: 'italic' },
  ratingContainer: { flexDirection: 'row', marginTop: 12, gap: 2 },
});
