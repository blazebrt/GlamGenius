import React, { useEffect, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  Image,
} from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import Animated, { FadeIn, FadeInDown } from 'react-native-reanimated';
import { useUserStore } from '../../src/store/userStore';
import { api } from '../../src/services/api';

const QUICK_ACTIONS = [
  { id: 'advice', icon: 'sparkles', label: 'Get Advice', route: '/get-advice', color: '#0EA5E9' },
  { id: 'scan', icon: 'scan', label: 'AI Scan', route: '/(tabs)/scan-tab', color: '#10B981' },
  { id: 'quiz', icon: 'clipboard', label: 'Style Quiz', route: '/style-quiz', color: '#8B5CF6' },
  { id: 'history', icon: 'time', label: 'History', route: '/(tabs)/history', color: '#F59E0B' },
];

const FEATURED_SERVICES = [
  { id: '1', name: 'Haircut & Styling', price: '₹499', duration: '45 min', icon: 'cut' },
  { id: '2', name: 'Facial Treatment', price: '₹799', duration: '60 min', icon: 'flower' },
  { id: '3', name: 'Hair Spa', price: '₹999', duration: '75 min', icon: 'leaf' },
];

const BEAUTY_TIPS = [
  "Drink 8 glasses of water daily for glowing skin",
  "Apply sunscreen even on cloudy days",
  "Use a silk pillowcase to prevent hair breakage",
  "Massage your scalp for 5 minutes before washing",
  "Remove makeup before sleeping for clear skin",
  "Trim hair every 6-8 weeks to prevent split ends",
];

export default function HomeScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { user, loadUser } = useUserStore();
  const [tipIndex, setTipIndex] = useState(0);

  useEffect(() => {
    loadUser();
    // Change tip every hour
    const hour = new Date().getHours();
    setTipIndex(hour % BEAUTY_TIPS.length);
  }, []);

  const getGreeting = () => {
    const hour = new Date().getHours();
    if (hour < 12) return 'Good Morning';
    if (hour < 17) return 'Good Afternoon';
    return 'Good Evening';
  };

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <ScrollView showsVerticalScrollIndicator={false}>
        {/* Header */}
        <Animated.View entering={FadeIn} style={styles.header}>
          <View>
            <Text style={styles.greeting}>{getGreeting()}</Text>
            <Text style={styles.userName}>{user?.name || 'Welcome!'}</Text>
          </View>
          <TouchableOpacity
            style={styles.profileButton}
            onPress={() => router.push('/(tabs)/profile')}
          >
            <Ionicons name="person" size={22} color="#0EA5E9" />
          </TouchableOpacity>
        </Animated.View>

        {/* Tip of the Hour */}
        <Animated.View entering={FadeInDown.delay(100)} style={styles.tipCard}>
          <View style={styles.tipHeader}>
            <Ionicons name="bulb" size={18} color="#F59E0B" />
            <Text style={styles.tipLabel}>Tip of the Hour</Text>
          </View>
          <Text style={styles.tipText}>{BEAUTY_TIPS[tipIndex]}</Text>
        </Animated.View>

        {/* Quick Actions */}
        <Animated.View entering={FadeInDown.delay(200)} style={styles.section}>
          <Text style={styles.sectionTitle}>Quick Actions</Text>
          <View style={styles.actionsGrid}>
            {QUICK_ACTIONS.map((action) => (
              <TouchableOpacity
                key={action.id}
                style={styles.actionCard}
                onPress={() => router.push(action.route as any)}
              >
                <View style={[styles.actionIcon, { backgroundColor: `${action.color}15` }]}>
                  <Ionicons name={action.icon as any} size={24} color={action.color} />
                </View>
                <Text style={styles.actionLabel}>{action.label}</Text>
              </TouchableOpacity>
            ))}
          </View>
        </Animated.View>

        {/* Featured Services */}
        <Animated.View entering={FadeInDown.delay(300)} style={styles.section}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>Popular Services</Text>
            <TouchableOpacity onPress={() => router.push('/(tabs)/services')}>
              <Text style={styles.seeAll}>See All</Text>
            </TouchableOpacity>
          </View>
          <ScrollView horizontal showsHorizontalScrollIndicator={false}>
            {FEATURED_SERVICES.map((service) => (
              <TouchableOpacity key={service.id} style={styles.serviceCard}>
                <View style={styles.serviceIcon}>
                  <Ionicons name={service.icon as any} size={24} color="#0EA5E9" />
                </View>
                <Text style={styles.serviceName}>{service.name}</Text>
                <View style={styles.serviceMeta}>
                  <Text style={styles.servicePrice}>{service.price}</Text>
                  <Text style={styles.serviceDuration}>{service.duration}</Text>
                </View>
              </TouchableOpacity>
            ))}
          </ScrollView>
        </Animated.View>

        {/* CTA Banner */}
        <Animated.View entering={FadeInDown.delay(400)} style={styles.ctaBanner}>
          <View style={styles.ctaContent}>
            <Text style={styles.ctaTitle}>Get Personalized Advice</Text>
            <Text style={styles.ctaSubtitle}>AI-powered recommendations just for you</Text>
            <TouchableOpacity
              style={styles.ctaButton}
              onPress={() => router.push('/get-advice')}
            >
              <Text style={styles.ctaButtonText}>Start Now</Text>
              <Ionicons name="arrow-forward" size={18} color="#FFFFFF" />
            </TouchableOpacity>
          </View>
        </Animated.View>

        <View style={{ height: 100 }} />
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#F8FAFC',
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: 20,
    paddingVertical: 16,
  },
  greeting: {
    fontSize: 14,
    color: '#64748B',
  },
  userName: {
    fontSize: 24,
    fontWeight: '700',
    color: '#1E293B',
    marginTop: 2,
  },
  profileButton: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: '#E0F2FE',
    justifyContent: 'center',
    alignItems: 'center',
  },
  tipCard: {
    marginHorizontal: 20,
    backgroundColor: '#FFFBEB',
    borderRadius: 16,
    padding: 16,
    borderWidth: 1,
    borderColor: '#FEF3C7',
  },
  tipHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginBottom: 8,
  },
  tipLabel: {
    fontSize: 13,
    fontWeight: '600',
    color: '#B45309',
  },
  tipText: {
    fontSize: 14,
    color: '#92400E',
    lineHeight: 20,
  },
  section: {
    marginTop: 24,
    paddingHorizontal: 20,
  },
  sectionHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 16,
  },
  sectionTitle: {
    fontSize: 18,
    fontWeight: '600',
    color: '#1E293B',
    marginBottom: 16,
  },
  seeAll: {
    fontSize: 14,
    color: '#0EA5E9',
    fontWeight: '500',
  },
  actionsGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 12,
  },
  actionCard: {
    width: '47%',
    backgroundColor: '#FFFFFF',
    borderRadius: 16,
    padding: 16,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: '#E2E8F0',
  },
  actionIcon: {
    width: 52,
    height: 52,
    borderRadius: 26,
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: 12,
  },
  actionLabel: {
    fontSize: 14,
    fontWeight: '500',
    color: '#1E293B',
  },
  serviceCard: {
    width: 160,
    backgroundColor: '#FFFFFF',
    borderRadius: 16,
    padding: 16,
    marginRight: 12,
    borderWidth: 1,
    borderColor: '#E2E8F0',
  },
  serviceIcon: {
    width: 48,
    height: 48,
    borderRadius: 24,
    backgroundColor: '#E0F2FE',
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: 12,
  },
  serviceName: {
    fontSize: 14,
    fontWeight: '600',
    color: '#1E293B',
    marginBottom: 8,
  },
  serviceMeta: {
    flexDirection: 'row',
    justifyContent: 'space-between',
  },
  servicePrice: {
    fontSize: 14,
    fontWeight: '600',
    color: '#0EA5E9',
  },
  serviceDuration: {
    fontSize: 12,
    color: '#64748B',
  },
  ctaBanner: {
    marginHorizontal: 20,
    marginTop: 24,
    backgroundColor: '#0EA5E9',
    borderRadius: 20,
    padding: 24,
  },
  ctaContent: {
    alignItems: 'flex-start',
  },
  ctaTitle: {
    fontSize: 20,
    fontWeight: '700',
    color: '#FFFFFF',
  },
  ctaSubtitle: {
    fontSize: 14,
    color: 'rgba(255,255,255,0.8)',
    marginTop: 4,
    marginBottom: 16,
  },
  ctaButton: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(255,255,255,0.2)',
    paddingHorizontal: 20,
    paddingVertical: 12,
    borderRadius: 24,
    gap: 8,
  },
  ctaButtonText: {
    fontSize: 14,
    fontWeight: '600',
    color: '#FFFFFF',
  },
});
