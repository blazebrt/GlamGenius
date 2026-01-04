import React, { useEffect, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  RefreshControl,
} from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import Animated, { FadeInDown } from 'react-native-reanimated';
import { useUserStore } from '../../src/store/userStore';

const OCCASIONS = [
  { id: 'everyday', label: 'Everyday', icon: 'sunny-outline' },
  { id: 'office', label: 'Office', icon: 'briefcase-outline' },
  { id: 'party', label: 'Party', icon: 'sparkles-outline' },
  { id: 'wedding', label: 'Wedding', icon: 'heart-outline' },
  { id: 'date', label: 'Date Night', icon: 'moon-outline' },
];

const QUICK_ACTIONS = [
  { id: 'scan', label: 'AI Scan', icon: 'scan', color: '#D4AF37', route: '/scan' },
  { id: 'quiz', label: 'Style Quiz', icon: 'help-circle', color: '#9B59B6', route: '/quiz' },
  { id: 'recommend', label: 'Get Advice', icon: 'sparkles', color: '#3498DB', route: '/quiz' },
];

export default function HomeScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { user, fetchUser, userId } = useUserStore();
  const [refreshing, setRefreshing] = useState(false);
  const [greeting, setGreeting] = useState('');

  useEffect(() => {
    const hour = new Date().getHours();
    if (hour < 12) setGreeting('Good Morning');
    else if (hour < 17) setGreeting('Good Afternoon');
    else setGreeting('Good Evening');

    if (userId) {
      fetchUser();
    }
  }, [userId]);

  const onRefresh = async () => {
    setRefreshing(true);
    await fetchUser();
    setRefreshing(false);
  };

  const handleOccasionSelect = (occasion: string) => {
    router.push({ pathname: '/quiz', params: { occasion } });
  };

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <ScrollView
        showsVerticalScrollIndicator={false}
        contentContainerStyle={styles.scrollContent}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#D4AF37" />
        }
      >
        {/* Header */}
        <Animated.View entering={FadeInDown.delay(100)} style={styles.header}>
          <View>
            <Text style={styles.greeting}>{greeting}</Text>
            <Text style={styles.userName}>{user?.name || 'Beauty Enthusiast'}</Text>
          </View>
          <TouchableOpacity style={styles.notificationBtn}>
            <Ionicons name="notifications-outline" size={24} color="#FFFFFF" />
          </TouchableOpacity>
        </Animated.View>

        {/* Profile Summary Card */}
        {user?.face_shape || user?.skin_type ? (
          <Animated.View entering={FadeInDown.delay(200)} style={styles.profileCard}>
            <View style={styles.profileCardHeader}>
              <Ionicons name="person-circle" size={20} color="#D4AF37" />
              <Text style={styles.profileCardTitle}>Your Beauty Profile</Text>
            </View>
            <View style={styles.profileTags}>
              {user?.face_shape && (
                <View style={styles.profileTag}>
                  <Text style={styles.profileTagText}>{user.face_shape} face</Text>
                </View>
              )}
              {user?.skin_type && (
                <View style={styles.profileTag}>
                  <Text style={styles.profileTagText}>{user.skin_type} skin</Text>
                </View>
              )}
              {user?.hair_type && (
                <View style={styles.profileTag}>
                  <Text style={styles.profileTagText}>{user.hair_type} hair</Text>
                </View>
              )}
            </View>
          </Animated.View>
        ) : (
          <Animated.View entering={FadeInDown.delay(200)} style={styles.scanPromptCard}>
            <Ionicons name="scan-circle" size={40} color="#D4AF37" />
            <View style={styles.scanPromptText}>
              <Text style={styles.scanPromptTitle}>Complete Your Profile</Text>
              <Text style={styles.scanPromptDesc}>Take a scan to get personalized recommendations</Text>
            </View>
            <TouchableOpacity
              style={styles.scanPromptBtn}
              onPress={() => router.push('/scan')}
            >
              <Text style={styles.scanPromptBtnText}>Scan Now</Text>
            </TouchableOpacity>
          </Animated.View>
        )}

        {/* Quick Actions */}
        <Animated.View entering={FadeInDown.delay(300)} style={styles.section}>
          <Text style={styles.sectionTitle}>Quick Actions</Text>
          <View style={styles.quickActions}>
            {QUICK_ACTIONS.map((action) => (
              <TouchableOpacity
                key={action.id}
                style={styles.quickActionItem}
                onPress={() => router.push(action.route as any)}
              >
                <View style={[styles.quickActionIcon, { backgroundColor: `${action.color}20` }]}>
                  <Ionicons name={action.icon as any} size={24} color={action.color} />
                </View>
                <Text style={styles.quickActionLabel}>{action.label}</Text>
              </TouchableOpacity>
            ))}
          </View>
        </Animated.View>

        {/* Occasions */}
        <Animated.View entering={FadeInDown.delay(400)} style={styles.section}>
          <Text style={styles.sectionTitle}>What's the Occasion?</Text>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.occasionsScroll}>
            {OCCASIONS.map((occasion) => (
              <TouchableOpacity
                key={occasion.id}
                style={styles.occasionItem}
                onPress={() => handleOccasionSelect(occasion.id)}
              >
                <View style={styles.occasionIcon}>
                  <Ionicons name={occasion.icon as any} size={24} color="#D4AF37" />
                </View>
                <Text style={styles.occasionLabel}>{occasion.label}</Text>
              </TouchableOpacity>
            ))}
          </ScrollView>
        </Animated.View>

        {/* Featured Services */}
        <Animated.View entering={FadeInDown.delay(500)} style={styles.section}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>Featured Services</Text>
            <TouchableOpacity onPress={() => router.push('/(tabs)/services')}>
              <Text style={styles.seeAllText}>See All</Text>
            </TouchableOpacity>
          </View>
          <View style={styles.featuredServices}>
            <TouchableOpacity style={styles.featuredCard}>
              <View style={styles.featuredIconContainer}>
                <Ionicons name="cut" size={28} color="#D4AF37" />
              </View>
              <Text style={styles.featuredTitle}>Luxury Hair Cut</Text>
              <Text style={styles.featuredPrice}>From $80</Text>
            </TouchableOpacity>
            <TouchableOpacity style={styles.featuredCard}>
              <View style={styles.featuredIconContainer}>
                <Ionicons name="flower" size={28} color="#D4AF37" />
              </View>
              <Text style={styles.featuredTitle}>Hydrating Facial</Text>
              <Text style={styles.featuredPrice}>From $120</Text>
            </TouchableOpacity>
          </View>
        </Animated.View>

        {/* Tip of the Day */}
        <Animated.View entering={FadeInDown.delay(600)} style={styles.tipCard}>
          <View style={styles.tipHeader}>
            <Ionicons name="bulb" size={20} color="#D4AF37" />
            <Text style={styles.tipTitle}>Tip of the Day</Text>
          </View>
          <Text style={styles.tipText}>
            Apply sunscreen even on cloudy days! UV rays can penetrate clouds and cause skin damage.
          </Text>
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
  scrollContent: {
    paddingHorizontal: 20,
    paddingBottom: 100,
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: 20,
  },
  greeting: {
    fontSize: 14,
    color: 'rgba(255, 255, 255, 0.6)',
  },
  userName: {
    fontSize: 24,
    fontWeight: '700',
    color: '#FFFFFF',
    marginTop: 4,
  },
  notificationBtn: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: 'rgba(255, 255, 255, 0.1)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  profileCard: {
    backgroundColor: 'rgba(212, 175, 55, 0.1)',
    borderRadius: 16,
    padding: 16,
    borderWidth: 1,
    borderColor: 'rgba(212, 175, 55, 0.2)',
  },
  profileCardHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginBottom: 12,
  },
  profileCardTitle: {
    fontSize: 14,
    fontWeight: '600',
    color: '#D4AF37',
  },
  profileTags: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  profileTag: {
    backgroundColor: 'rgba(255, 255, 255, 0.1)',
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 20,
  },
  profileTagText: {
    fontSize: 12,
    color: '#FFFFFF',
    textTransform: 'capitalize',
  },
  scanPromptCard: {
    backgroundColor: 'rgba(255, 255, 255, 0.05)',
    borderRadius: 16,
    padding: 20,
    flexDirection: 'row',
    alignItems: 'center',
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.1)',
  },
  scanPromptText: {
    flex: 1,
    marginLeft: 12,
  },
  scanPromptTitle: {
    fontSize: 16,
    fontWeight: '600',
    color: '#FFFFFF',
  },
  scanPromptDesc: {
    fontSize: 12,
    color: 'rgba(255, 255, 255, 0.5)',
    marginTop: 2,
  },
  scanPromptBtn: {
    backgroundColor: '#D4AF37',
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderRadius: 20,
  },
  scanPromptBtnText: {
    fontSize: 12,
    fontWeight: '600',
    color: '#0A0A0A',
  },
  section: {
    marginTop: 28,
  },
  sectionHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  sectionTitle: {
    fontSize: 18,
    fontWeight: '600',
    color: '#FFFFFF',
    marginBottom: 16,
  },
  seeAllText: {
    fontSize: 14,
    color: '#D4AF37',
    marginBottom: 16,
  },
  quickActions: {
    flexDirection: 'row',
    justifyContent: 'space-between',
  },
  quickActionItem: {
    alignItems: 'center',
    flex: 1,
  },
  quickActionIcon: {
    width: 56,
    height: 56,
    borderRadius: 28,
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: 8,
  },
  quickActionLabel: {
    fontSize: 12,
    color: 'rgba(255, 255, 255, 0.8)',
  },
  occasionsScroll: {
    marginLeft: -4,
  },
  occasionItem: {
    alignItems: 'center',
    marginRight: 16,
    width: 80,
  },
  occasionIcon: {
    width: 64,
    height: 64,
    borderRadius: 32,
    backgroundColor: 'rgba(212, 175, 55, 0.1)',
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: 8,
    borderWidth: 1,
    borderColor: 'rgba(212, 175, 55, 0.3)',
  },
  occasionLabel: {
    fontSize: 12,
    color: 'rgba(255, 255, 255, 0.8)',
    textAlign: 'center',
  },
  featuredServices: {
    flexDirection: 'row',
    gap: 12,
  },
  featuredCard: {
    flex: 1,
    backgroundColor: 'rgba(255, 255, 255, 0.05)',
    borderRadius: 16,
    padding: 16,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.1)',
  },
  featuredIconContainer: {
    width: 56,
    height: 56,
    borderRadius: 28,
    backgroundColor: 'rgba(212, 175, 55, 0.1)',
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: 12,
  },
  featuredTitle: {
    fontSize: 14,
    fontWeight: '600',
    color: '#FFFFFF',
    textAlign: 'center',
  },
  featuredPrice: {
    fontSize: 12,
    color: '#D4AF37',
    marginTop: 4,
  },
  tipCard: {
    backgroundColor: 'rgba(212, 175, 55, 0.08)',
    borderRadius: 16,
    padding: 16,
    marginTop: 28,
    borderWidth: 1,
    borderColor: 'rgba(212, 175, 55, 0.15)',
  },
  tipHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginBottom: 8,
  },
  tipTitle: {
    fontSize: 14,
    fontWeight: '600',
    color: '#D4AF37',
  },
  tipText: {
    fontSize: 13,
    color: 'rgba(255, 255, 255, 0.7)',
    lineHeight: 20,
  },
});
