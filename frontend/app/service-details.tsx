import React, { useEffect, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  ActivityIndicator,
} from 'react-native';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import Animated, { FadeIn, FadeInDown } from 'react-native-reanimated';
import { api } from '../src/services/api';
import { useCartStore } from '../src/store/cartStore';
import { COLORS, FONTS, SPACING, RADIUS, SHADOWS } from '../src/theme/colors';

interface Service {
  id: string;
  name: string;
  category: string;
  description: string;
  price_range: string;
  base_price: number;
  duration_minutes: number;
  suitable_for: string[];
  benefits: string[];
}

export default function ServiceDetailsScreen() {
  const router = useRouter();
  const params = useLocalSearchParams();
  const insets = useSafeAreaInsets();
  const { addItem, removeItem, items: cartItems } = useCartStore();
  const [service, setService] = useState<Service | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadService();
  }, [params.serviceId]);

  const loadService = async () => {
    if (!params.serviceId) return;

    setLoading(true);
    try {
      const response = await api.get(`/services/${params.serviceId}`);
      setService(response.data);
    } catch (error) {
      console.error('Error loading service:', error);
    } finally {
      setLoading(false);
    }
  };

  const getCategoryIcon = (category: string) => {
    switch (category) {
      case 'Hair': return 'cut-outline';
      case 'Skin': return 'sparkles-outline';
      case 'Scalp': return 'scan-outline';
      case 'Makeup': return 'color-palette-outline';
      case 'Body': return 'body-outline';
      default: return 'sparkles-outline';
    }
  };

  const isInCart = service ? cartItems.some(item => item.id === service.id) : false;

  const handleToggleCart = () => {
    if (!service) return;
    if (isInCart) {
      removeItem(service.id);
    } else {
      addItem({
        id: service.id,
        name: service.name,
        price: service.base_price || 999,
        type: 'service',
        duration: service.duration_minutes || 30,
      });
    }
  };

  if (loading) {
    return (
      <View style={[styles.container, { paddingTop: insets.top }]}>
        <View style={styles.loadingContainer}>
          <ActivityIndicator size="large" color={COLORS.black} />
        </View>
      </View>
    );
  }

  if (!service) {
    return (
      <View style={[styles.container, { paddingTop: insets.top }]}>
        <View style={styles.emptyContainer}>
          <Ionicons name="alert-circle" size={48} color={COLORS.textMuted} />
          <Text style={styles.emptyText}>Service not found</Text>
          <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
            <Text style={styles.backBtnText}>Go Back</Text>
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
          <Ionicons name="arrow-back" size={24} color={COLORS.textPrimary} />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>{service.category}</Text>
        <View style={{ width: 40 }} />
      </View>

      <ScrollView
        showsVerticalScrollIndicator={false}
        contentContainerStyle={styles.scrollContent}
      >
        {/* Hero Section */}
        <Animated.View entering={FadeIn} style={styles.heroSection}>
          <View style={styles.heroIcon}>
            <Ionicons name={getCategoryIcon(service.category) as any} size={48} color={COLORS.black} />
          </View>
          <Text style={styles.serviceName}>{service.name}</Text>
          <View style={styles.categoryBadge}>
            <Text style={styles.categoryBadgeText}>{service.category}</Text>
          </View>
        </Animated.View>

        {/* Metrics */}
        <Animated.View entering={FadeInDown.delay(100)} style={styles.metricsRow}>
          <View style={styles.metricCard}>
            <Ionicons name="time-outline" size={24} color={COLORS.black} />
            <Text style={styles.metricLabel}>Duration</Text>
            <Text style={styles.metricValue}>{service.duration_minutes} min</Text>
          </View>
          <View style={styles.metricCard}>
            <Ionicons name="pricetag-outline" size={24} color={COLORS.black} />
            <Text style={styles.metricLabel}>Price Range</Text>
            <Text style={styles.metricValue}>{service.price_range}</Text>
          </View>
        </Animated.View>

        {/* Description */}
        <Animated.View entering={FadeInDown.delay(200)} style={styles.section}>
          <View style={styles.sectionHeader}>
            <Ionicons name="information-circle" size={20} color={COLORS.black} />
            <Text style={styles.sectionTitle}>About This Service</Text>
          </View>
          <Text style={styles.descriptionText}>{service.description}</Text>
        </Animated.View>

        {/* Benefits */}
        {service.benefits?.length > 0 && (
          <Animated.View entering={FadeInDown.delay(300)} style={styles.section}>
            <View style={styles.sectionHeader}>
              <Ionicons name="checkmark-circle" size={20} color={COLORS.black} />
              <Text style={styles.sectionTitle}>Benefits</Text>
            </View>
            <View style={styles.benefitsContainer}>
              {service.benefits.map((benefit, index) => (
                <View key={index} style={styles.benefitItem}>
                  <Ionicons name="checkmark" size={16} color={COLORS.black} />
                  <Text style={styles.benefitText}>{benefit}</Text>
                </View>
              ))}
            </View>
          </Animated.View>
        )}

        {/* Suitable For */}
        {service.suitable_for?.length > 0 && (
          <Animated.View entering={FadeInDown.delay(400)} style={styles.section}>
            <View style={styles.sectionHeader}>
              <Ionicons name="people" size={20} color={COLORS.black} />
              <Text style={styles.sectionTitle}>Suitable For</Text>
            </View>
            <View style={styles.tagsContainer}>
              {service.suitable_for.map((item, index) => (
                <View key={index} style={styles.tag}>
                  <Text style={styles.tagText}>{item}</Text>
                </View>
              ))}
            </View>
          </Animated.View>
        )}

        <View style={{ height: 100 }} />
      </ScrollView>

      {/* Bottom CTA */}
      <View style={[styles.bottomBar, { paddingBottom: insets.bottom + 16 }]}>
        <TouchableOpacity
          style={[styles.primaryButton, isInCart && styles.primaryButtonActive]}
          onPress={handleToggleCart}
        >
          <Ionicons name={isInCart ? 'checkmark' : 'add'} size={20} color={isInCart ? COLORS.black : COLORS.white} />
          <Text style={[styles.primaryButtonText, isInCart && styles.primaryButtonTextActive]}>
            {isInCart ? 'Added to Cart' : 'Add to Cart'}
          </Text>
        </TouchableOpacity>
      </View>
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
  loadingContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
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
  },
  backBtn: {
    marginTop: 24,
    paddingVertical: 12,
    paddingHorizontal: 24,
    backgroundColor: '#D4AF37',
    borderRadius: 24,
  },
  backBtnText: {
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
    paddingVertical: 32,
  },
  heroIcon: {
    width: 100,
    height: 100,
    borderRadius: 50,
    backgroundColor: 'rgba(212, 175, 55, 0.1)',
    justifyContent: 'center',
    alignItems: 'center',
    borderWidth: 2,
    borderColor: 'rgba(212, 175, 55, 0.3)',
  },
  serviceName: {
    fontSize: 26,
    fontWeight: '700',
    color: '#FFFFFF',
    textAlign: 'center',
    marginTop: 20,
  },
  categoryBadge: {
    backgroundColor: 'rgba(212, 175, 55, 0.15)',
    paddingHorizontal: 16,
    paddingVertical: 6,
    borderRadius: 16,
    marginTop: 12,
  },
  categoryBadgeText: {
    fontSize: 13,
    fontWeight: '500',
    color: '#D4AF37',
  },
  metricsRow: {
    flexDirection: 'row',
    gap: 12,
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
  section: {
    marginTop: 28,
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
  descriptionText: {
    fontSize: 15,
    color: 'rgba(255, 255, 255, 0.7)',
    lineHeight: 24,
  },
  benefitsContainer: {
    backgroundColor: 'rgba(212, 175, 55, 0.08)',
    borderRadius: 16,
    padding: 16,
    borderWidth: 1,
    borderColor: 'rgba(212, 175, 55, 0.15)',
  },
  benefitItem: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 10,
    gap: 10,
  },
  benefitText: {
    fontSize: 14,
    color: '#FFFFFF',
    flex: 1,
  },
  tagsContainer: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  tag: {
    backgroundColor: 'rgba(255, 255, 255, 0.1)',
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 20,
  },
  tagText: {
    fontSize: 13,
    color: 'rgba(255, 255, 255, 0.8)',
    textTransform: 'capitalize',
  },
  ctaContainer: {
    marginTop: 40,
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
});
