import React from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
} from 'react-native';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import Animated, { FadeIn, FadeInDown } from 'react-native-reanimated';

export default function RecommendationsScreen() {
  const router = useRouter();
  const params = useLocalSearchParams();
  const insets = useSafeAreaInsets();

  const recommendations = params.recommendations
    ? JSON.parse(params.recommendations as string)
    : null;

  if (!recommendations) {
    return (
      <View style={[styles.container, { paddingTop: insets.top }]}>
        <View style={styles.emptyContainer}>
          <Ionicons name="alert-circle" size={48} color="#D4AF37" />
          <Text style={styles.emptyText}>No recommendations available</Text>
          <TouchableOpacity style={styles.backButton} onPress={() => router.back()}>
            <Text style={styles.backButtonText}>Go Back</Text>
          </TouchableOpacity>
        </View>
      </View>
    );
  }

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      {/* Header */}
      <View style={styles.header}>
        <TouchableOpacity onPress={() => router.back()} style={styles.headerBackButton}>
          <Ionicons name="arrow-back" size={24} color="#FFFFFF" />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>Your Recommendations</Text>
        <View style={{ width: 40 }} />
      </View>

      <ScrollView
        showsVerticalScrollIndicator={false}
        contentContainerStyle={styles.scrollContent}
      >
        {/* Hero Section */}
        <Animated.View entering={FadeIn} style={styles.heroSection}>
          <View style={styles.heroIcon}>
            <Ionicons name="sparkles" size={32} color="#D4AF37" />
          </View>
          <Text style={styles.heroTitle}>Personalized For You</Text>
          <Text style={styles.heroSubtitle}>
            Based on your profile, preferences, and occasion
          </Text>
        </Animated.View>

        {/* Services */}
        {recommendations.services?.length > 0 && (
          <Animated.View entering={FadeInDown.delay(100)} style={styles.section}>
            <View style={styles.sectionHeader}>
              <Ionicons name="star" size={20} color="#D4AF37" />
              <Text style={styles.sectionTitle}>Recommended Services</Text>
            </View>
            {recommendations.services.map((service: any, index: number) => (
              <View key={index} style={styles.serviceCard}>
                <View style={styles.serviceIconContainer}>
                  <Ionicons name="checkmark-circle" size={24} color="#D4AF37" />
                </View>
                <View style={styles.serviceInfo}>
                  <Text style={styles.serviceName}>{service.name}</Text>
                  {service.reason && (
                    <Text style={styles.serviceReason}>{service.reason}</Text>
                  )}
                  {service.expected_result && (
                    <Text style={styles.serviceResult}>
                      Expected: {service.expected_result}
                    </Text>
                  )}
                </View>
              </View>
            ))}
          </Animated.View>
        )}

        {/* Stylist Level */}
        {recommendations.stylist_level && (
          <Animated.View entering={FadeInDown.delay(200)} style={styles.highlightCard}>
            <Ionicons name="person" size={24} color="#D4AF37" />
            <View style={styles.highlightInfo}>
              <Text style={styles.highlightLabel}>Recommended Stylist</Text>
              <Text style={styles.highlightValue}>{recommendations.stylist_level}</Text>
            </View>
          </Animated.View>
        )}

        {/* Estimated Cost & Duration */}
        <Animated.View entering={FadeInDown.delay(250)} style={styles.metricsRow}>
          {recommendations.total_estimated_cost && (
            <View style={styles.metricCard}>
              <Ionicons name="wallet-outline" size={24} color="#D4AF37" />
              <Text style={styles.metricLabel}>Estimated Cost</Text>
              <Text style={styles.metricValue}>{recommendations.total_estimated_cost}</Text>
            </View>
          )}
          {recommendations.appointment_duration && (
            <View style={styles.metricCard}>
              <Ionicons name="time-outline" size={24} color="#D4AF37" />
              <Text style={styles.metricLabel}>Duration</Text>
              <Text style={styles.metricValue}>{recommendations.appointment_duration}</Text>
            </View>
          )}
        </Animated.View>

        {/* Expected Outcome */}
        {recommendations.expected_outcome && (
          <Animated.View entering={FadeInDown.delay(300)} style={styles.section}>
            <View style={styles.sectionHeader}>
              <Ionicons name="eye" size={20} color="#D4AF37" />
              <Text style={styles.sectionTitle}>Expected Outcome</Text>
            </View>
            <View style={styles.outcomeCard}>
              <Text style={styles.outcomeText}>{recommendations.expected_outcome}</Text>
            </View>
          </Animated.View>
        )}

        {/* Add-ons */}
        {recommendations.add_ons?.length > 0 && (
          <Animated.View entering={FadeInDown.delay(350)} style={styles.section}>
            <View style={styles.sectionHeader}>
              <Ionicons name="add-circle" size={20} color="#D4AF37" />
              <Text style={styles.sectionTitle}>Recommended Add-ons</Text>
            </View>
            <View style={styles.tagsContainer}>
              {recommendations.add_ons.map((addon: string, index: number) => (
                <View key={index} style={styles.tag}>
                  <Text style={styles.tagText}>{addon}</Text>
                </View>
              ))}
            </View>
          </Animated.View>
        )}

        {/* Aftercare Tips */}
        {recommendations.aftercare_tips?.length > 0 && (
          <Animated.View entering={FadeInDown.delay(400)} style={styles.section}>
            <View style={styles.sectionHeader}>
              <Ionicons name="heart" size={20} color="#D4AF37" />
              <Text style={styles.sectionTitle}>Aftercare Tips</Text>
            </View>
            <View style={styles.tipsCard}>
              {recommendations.aftercare_tips.map((tip: string, index: number) => (
                <View key={index} style={styles.tipItem}>
                  <Ionicons name="checkmark" size={16} color="#D4AF37" />
                  <Text style={styles.tipText}>{tip}</Text>
                </View>
              ))}
            </View>
          </Animated.View>
        )}

        {/* Maintenance Tips */}
        {recommendations.maintenance_tips?.length > 0 && (
          <Animated.View entering={FadeInDown.delay(450)} style={styles.section}>
            <View style={styles.sectionHeader}>
              <Ionicons name="calendar" size={20} color="#D4AF37" />
              <Text style={styles.sectionTitle}>Maintenance Tips</Text>
            </View>
            <View style={styles.tipsCard}>
              {recommendations.maintenance_tips.map((tip: string, index: number) => (
                <View key={index} style={styles.tipItem}>
                  <Ionicons name="calendar-outline" size={16} color="#D4AF37" />
                  <Text style={styles.tipText}>{tip}</Text>
                </View>
              ))}
            </View>
          </Animated.View>
        )}

        {/* Upsell Suggestions */}
        {recommendations.upsell_suggestions?.length > 0 && (
          <Animated.View entering={FadeInDown.delay(500)} style={styles.section}>
            <View style={styles.sectionHeader}>
              <Ionicons name="trending-up" size={20} color="#D4AF37" />
              <Text style={styles.sectionTitle}>Future Recommendations</Text>
            </View>
            <View style={styles.upsellContainer}>
              {recommendations.upsell_suggestions.map((suggestion: string, index: number) => (
                <View key={index} style={styles.upsellItem}>
                  <Ionicons name="arrow-forward-circle" size={18} color="#9B59B6" />
                  <Text style={styles.upsellText}>{suggestion}</Text>
                </View>
              ))}
            </View>
          </Animated.View>
        )}

        {/* Actions */}
        <Animated.View entering={FadeInDown.delay(550)} style={styles.actionsContainer}>
          <TouchableOpacity
            style={styles.primaryButton}
            onPress={() => router.push('/(tabs)/services')}
          >
            <Ionicons name="grid" size={20} color="#0A0A0A" />
            <Text style={styles.primaryButtonText}>Browse All Services</Text>
          </TouchableOpacity>

          <TouchableOpacity
            style={styles.secondaryButton}
            onPress={() => router.push('/(tabs)/home')}
          >
            <Ionicons name="home" size={20} color="#D4AF37" />
            <Text style={styles.secondaryButtonText}>Back to Home</Text>
          </TouchableOpacity>
        </Animated.View>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#0A0A0A',
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingVertical: 12,
  },
  headerBackButton: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: 'rgba(255, 255, 255, 0.1)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  headerTitle: {
    fontSize: 18,
    fontWeight: '600',
    color: '#FFFFFF',
  },
  emptyContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    paddingHorizontal: 40,
  },
  emptyText: {
    fontSize: 16,
    color: 'rgba(255, 255, 255, 0.6)',
    marginTop: 16,
    textAlign: 'center',
  },
  backButton: {
    marginTop: 24,
    paddingVertical: 12,
    paddingHorizontal: 24,
    backgroundColor: '#D4AF37',
    borderRadius: 24,
  },
  backButtonText: {
    fontSize: 14,
    fontWeight: '600',
    color: '#0A0A0A',
  },
  scrollContent: {
    paddingHorizontal: 20,
    paddingBottom: 40,
  },
  heroSection: {
    alignItems: 'center',
    paddingVertical: 24,
  },
  heroIcon: {
    width: 70,
    height: 70,
    borderRadius: 35,
    backgroundColor: 'rgba(212, 175, 55, 0.1)',
    justifyContent: 'center',
    alignItems: 'center',
    borderWidth: 2,
    borderColor: 'rgba(212, 175, 55, 0.3)',
  },
  heroTitle: {
    fontSize: 24,
    fontWeight: '700',
    color: '#FFFFFF',
    marginTop: 16,
  },
  heroSubtitle: {
    fontSize: 14,
    color: 'rgba(255, 255, 255, 0.5)',
    textAlign: 'center',
    marginTop: 4,
  },
  section: {
    marginTop: 24,
  },
  sectionHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginBottom: 12,
  },
  sectionTitle: {
    fontSize: 16,
    fontWeight: '600',
    color: '#FFFFFF',
  },
  serviceCard: {
    flexDirection: 'row',
    backgroundColor: 'rgba(255, 255, 255, 0.05)',
    borderRadius: 16,
    padding: 16,
    marginBottom: 12,
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.08)',
  },
  serviceIconContainer: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: 'rgba(212, 175, 55, 0.1)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  serviceInfo: {
    flex: 1,
    marginLeft: 14,
  },
  serviceName: {
    fontSize: 16,
    fontWeight: '600',
    color: '#FFFFFF',
  },
  serviceReason: {
    fontSize: 13,
    color: 'rgba(255, 255, 255, 0.6)',
    marginTop: 4,
  },
  serviceResult: {
    fontSize: 12,
    color: '#D4AF37',
    marginTop: 6,
  },
  highlightCard: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(212, 175, 55, 0.08)',
    borderRadius: 16,
    padding: 16,
    marginTop: 16,
    borderWidth: 1,
    borderColor: 'rgba(212, 175, 55, 0.2)',
    gap: 14,
  },
  highlightInfo: {
    flex: 1,
  },
  highlightLabel: {
    fontSize: 12,
    color: 'rgba(255, 255, 255, 0.5)',
  },
  highlightValue: {
    fontSize: 18,
    fontWeight: '600',
    color: '#FFFFFF',
    marginTop: 2,
  },
  metricsRow: {
    flexDirection: 'row',
    gap: 12,
    marginTop: 16,
  },
  metricCard: {
    flex: 1,
    backgroundColor: 'rgba(255, 255, 255, 0.05)',
    borderRadius: 16,
    padding: 16,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.08)',
  },
  metricLabel: {
    fontSize: 12,
    color: 'rgba(255, 255, 255, 0.5)',
    marginTop: 8,
  },
  metricValue: {
    fontSize: 16,
    fontWeight: '600',
    color: '#FFFFFF',
    marginTop: 4,
  },
  outcomeCard: {
    backgroundColor: 'rgba(46, 204, 113, 0.1)',
    borderRadius: 16,
    padding: 16,
    borderWidth: 1,
    borderColor: 'rgba(46, 204, 113, 0.2)',
  },
  outcomeText: {
    fontSize: 14,
    color: '#FFFFFF',
    lineHeight: 22,
  },
  tagsContainer: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  tag: {
    backgroundColor: 'rgba(212, 175, 55, 0.15)',
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 20,
  },
  tagText: {
    fontSize: 13,
    color: '#D4AF37',
  },
  tipsCard: {
    backgroundColor: 'rgba(255, 255, 255, 0.05)',
    borderRadius: 16,
    padding: 16,
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.08)',
  },
  tipItem: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    marginBottom: 10,
    gap: 10,
  },
  tipText: {
    fontSize: 14,
    color: 'rgba(255, 255, 255, 0.7)',
    flex: 1,
    lineHeight: 20,
  },
  upsellContainer: {
    backgroundColor: 'rgba(155, 89, 182, 0.1)',
    borderRadius: 16,
    padding: 16,
    borderWidth: 1,
    borderColor: 'rgba(155, 89, 182, 0.2)',
  },
  upsellItem: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 10,
    gap: 10,
  },
  upsellText: {
    fontSize: 14,
    color: 'rgba(255, 255, 255, 0.7)',
    flex: 1,
  },
  actionsContainer: {
    marginTop: 32,
    gap: 12,
  },
  primaryButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#D4AF37',
    paddingVertical: 16,
    borderRadius: 28,
    gap: 8,
  },
  primaryButtonText: {
    fontSize: 16,
    fontWeight: '600',
    color: '#0A0A0A',
  },
  secondaryButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: 'rgba(212, 175, 55, 0.1)',
    paddingVertical: 16,
    borderRadius: 28,
    borderWidth: 1,
    borderColor: 'rgba(212, 175, 55, 0.3)',
    gap: 8,
  },
  secondaryButtonText: {
    fontSize: 16,
    fontWeight: '600',
    color: '#D4AF37',
  },
});
