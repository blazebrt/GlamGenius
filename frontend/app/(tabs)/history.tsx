import React, { useEffect, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  ActivityIndicator,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import Animated, { FadeInDown } from 'react-native-reanimated';
import { useUserStore } from '../../src/store/userStore';
import { api } from '../../src/services/api';

interface ScanHistory {
  id: string;
  scan_type: string;
  analysis: any;
  created_at: string;
}

interface Recommendation {
  id: string;
  occasion: string;
  budget: string;
  services: any[];
  created_at: string;
}

export default function HistoryScreen() {
  const insets = useSafeAreaInsets();
  const { userId } = useUserStore();
  const [activeTab, setActiveTab] = useState<'scans' | 'recommendations'>('scans');
  const [scanHistory, setScanHistory] = useState<ScanHistory[]>([]);
  const [recommendations, setRecommendations] = useState<Recommendation[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadHistory();
  }, [userId]);

  const loadHistory = async () => {
    if (!userId) return;

    setLoading(true);
    try {
      const [scansRes, recsRes] = await Promise.all([
        api.get(`/scan/history/${userId}`),
        api.get(`/recommendations/history/${userId}`),
      ]);
      setScanHistory(scansRes.data);
      setRecommendations(recsRes.data);
    } catch (error) {
      console.error('Error loading history:', error);
    } finally {
      setLoading(false);
    }
  };

  const formatDate = (dateString: string) => {
    const date = new Date(dateString);
    return date.toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    });
  };

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      {/* Header */}
      <View style={styles.header}>
        <Text style={styles.headerTitle}>Your History</Text>
        <Text style={styles.headerSubtitle}>Track your beauty journey</Text>
      </View>

      {/* Tabs */}
      <View style={styles.tabsContainer}>
        <TouchableOpacity
          style={[styles.tab, activeTab === 'scans' && styles.tabActive]}
          onPress={() => setActiveTab('scans')}
        >
          <Ionicons
            name="scan"
            size={18}
            color={activeTab === 'scans' ? '#0A0A0A' : '#D4AF37'}
          />
          <Text style={[styles.tabText, activeTab === 'scans' && styles.tabTextActive]}>
            Scans
          </Text>
        </TouchableOpacity>
        <TouchableOpacity
          style={[styles.tab, activeTab === 'recommendations' && styles.tabActive]}
          onPress={() => setActiveTab('recommendations')}
        >
          <Ionicons
            name="sparkles"
            size={18}
            color={activeTab === 'recommendations' ? '#0A0A0A' : '#D4AF37'}
          />
          <Text style={[styles.tabText, activeTab === 'recommendations' && styles.tabTextActive]}>
            Recommendations
          </Text>
        </TouchableOpacity>
      </View>

      {/* Content */}
      {loading ? (
        <View style={styles.loadingContainer}>
          <ActivityIndicator size="large" color="#D4AF37" />
        </View>
      ) : (
        <ScrollView
          showsVerticalScrollIndicator={false}
          contentContainerStyle={styles.scrollContent}
        >
          {activeTab === 'scans' ? (
            scanHistory.length > 0 ? (
              scanHistory.map((scan, index) => (
                <Animated.View
                  key={scan.id}
                  entering={FadeInDown.delay(index * 50)}
                >
                  <View style={styles.historyCard}>
                    <View style={styles.historyCardHeader}>
                      <View style={styles.historyIconContainer}>
                        <Ionicons name="scan" size={20} color="#D4AF37" />
                      </View>
                      <View style={styles.historyInfo}>
                        <Text style={styles.historyTitle}>
                          {scan.scan_type.charAt(0).toUpperCase() + scan.scan_type.slice(1)} Scan
                        </Text>
                        <Text style={styles.historyDate}>{formatDate(scan.created_at)}</Text>
                      </View>
                    </View>
                    {scan.analysis && (
                      <View style={styles.analysisPreview}>
                        {scan.analysis.face_shape && (
                          <View style={styles.analysisBadge}>
                            <Text style={styles.analysisBadgeText}>
                              {scan.analysis.face_shape} face
                            </Text>
                          </View>
                        )}
                        {scan.analysis.skin_type && (
                          <View style={styles.analysisBadge}>
                            <Text style={styles.analysisBadgeText}>
                              {scan.analysis.skin_type} skin
                            </Text>
                          </View>
                        )}
                        {scan.analysis.hair_type && (
                          <View style={styles.analysisBadge}>
                            <Text style={styles.analysisBadgeText}>
                              {scan.analysis.hair_type} hair
                            </Text>
                          </View>
                        )}
                      </View>
                    )}
                  </View>
                </Animated.View>
              ))
            ) : (
              <View style={styles.emptyState}>
                <Ionicons name="scan-outline" size={48} color="rgba(255,255,255,0.2)" />
                <Text style={styles.emptyText}>No scans yet</Text>
                <Text style={styles.emptySubtext}>Take your first scan to get started</Text>
              </View>
            )
          ) : recommendations.length > 0 ? (
            recommendations.map((rec, index) => (
              <Animated.View
                key={rec.id}
                entering={FadeInDown.delay(index * 50)}
              >
                <View style={styles.historyCard}>
                  <View style={styles.historyCardHeader}>
                    <View style={[styles.historyIconContainer, { backgroundColor: 'rgba(155, 89, 182, 0.1)' }]}>
                      <Ionicons name="sparkles" size={20} color="#9B59B6" />
                    </View>
                    <View style={styles.historyInfo}>
                      <Text style={styles.historyTitle}>
                        {rec.occasion ? `${rec.occasion.charAt(0).toUpperCase() + rec.occasion.slice(1)} Look` : 'Personalized'}
                      </Text>
                      <Text style={styles.historyDate}>{formatDate(rec.created_at)}</Text>
                    </View>
                  </View>
                  {rec.services && rec.services.length > 0 && (
                    <View style={styles.servicesPreview}>
                      {rec.services.slice(0, 2).map((service: any, idx: number) => (
                        <Text key={idx} style={styles.servicePreviewText}>
                          • {service.name}
                        </Text>
                      ))}
                      {rec.services.length > 2 && (
                        <Text style={styles.servicePreviewMore}>
                          +{rec.services.length - 2} more
                        </Text>
                      )}
                    </View>
                  )}
                </View>
              </Animated.View>
            ))
          ) : (
            <View style={styles.emptyState}>
              <Ionicons name="sparkles-outline" size={48} color="rgba(255,255,255,0.2)" />
              <Text style={styles.emptyText}>No recommendations yet</Text>
              <Text style={styles.emptySubtext}>Take a quiz to get personalized advice</Text>
            </View>
          )}
        </ScrollView>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#0A0A0A',
  },
  header: {
    paddingHorizontal: 20,
    paddingVertical: 16,
  },
  headerTitle: {
    fontSize: 28,
    fontWeight: '700',
    color: '#FFFFFF',
  },
  headerSubtitle: {
    fontSize: 14,
    color: 'rgba(255, 255, 255, 0.5)',
    marginTop: 4,
  },
  tabsContainer: {
    flexDirection: 'row',
    paddingHorizontal: 20,
    gap: 12,
    marginBottom: 16,
  },
  tab: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 12,
    borderRadius: 12,
    backgroundColor: 'rgba(212, 175, 55, 0.1)',
    borderWidth: 1,
    borderColor: 'rgba(212, 175, 55, 0.3)',
    gap: 8,
  },
  tabActive: {
    backgroundColor: '#D4AF37',
    borderColor: '#D4AF37',
  },
  tabText: {
    fontSize: 14,
    fontWeight: '600',
    color: '#D4AF37',
  },
  tabTextActive: {
    color: '#0A0A0A',
  },
  loadingContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
  },
  scrollContent: {
    paddingHorizontal: 20,
    paddingBottom: 100,
  },
  historyCard: {
    backgroundColor: 'rgba(255, 255, 255, 0.05)',
    borderRadius: 16,
    padding: 16,
    marginBottom: 12,
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.08)',
  },
  historyCardHeader: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  historyIconContainer: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: 'rgba(212, 175, 55, 0.1)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  historyInfo: {
    marginLeft: 12,
    flex: 1,
  },
  historyTitle: {
    fontSize: 16,
    fontWeight: '600',
    color: '#FFFFFF',
  },
  historyDate: {
    fontSize: 12,
    color: 'rgba(255, 255, 255, 0.4)',
    marginTop: 2,
  },
  analysisPreview: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    marginTop: 12,
    gap: 8,
  },
  analysisBadge: {
    backgroundColor: 'rgba(255, 255, 255, 0.1)',
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 12,
  },
  analysisBadgeText: {
    fontSize: 12,
    color: 'rgba(255, 255, 255, 0.7)',
    textTransform: 'capitalize',
  },
  servicesPreview: {
    marginTop: 12,
  },
  servicePreviewText: {
    fontSize: 13,
    color: 'rgba(255, 255, 255, 0.6)',
    marginBottom: 4,
  },
  servicePreviewMore: {
    fontSize: 12,
    color: '#D4AF37',
    marginTop: 4,
  },
  emptyState: {
    alignItems: 'center',
    paddingVertical: 60,
  },
  emptyText: {
    fontSize: 18,
    fontWeight: '600',
    color: 'rgba(255, 255, 255, 0.6)',
    marginTop: 16,
  },
  emptySubtext: {
    fontSize: 14,
    color: 'rgba(255, 255, 255, 0.3)',
    marginTop: 4,
  },
});
