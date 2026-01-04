import React, { useEffect, useState, useCallback } from 'react';
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
  { id: 'everyday', label: 'Daily', icon: 'sunny-outline' },
  { id: 'office', label: 'Office', icon: 'briefcase-outline' },
  { id: 'party', label: 'Party', icon: 'sparkles-outline' },
  { id: 'wedding', label: 'Shaadi', icon: 'heart-outline' },
  { id: 'festival', label: 'Festival', icon: 'star-outline' },
  { id: 'date', label: 'Date', icon: 'moon-outline' },
];

const QUICK_ACTIONS = [
  { id: 'scan', label: 'AI Scan', icon: 'scan', color: '#D4AF37', route: '/scan' },
  { id: 'quiz', label: 'Build Profile', icon: 'person-add', color: '#9B59B6', route: '/style-quiz' },
  { id: 'recommend', label: 'Get Advice', icon: 'sparkles', color: '#3498DB', route: '/get-advice' },
];

// Hourly changing tips for Indian beauty care
const BEAUTY_TIPS = [
  { tip: "Apply haldi (turmeric) face pack weekly for natural glow!", icon: "flower" },
  { tip: "Drink nimbu paani (lemon water) daily for clear skin.", icon: "water" },
  { tip: "Use coconut oil for deep hair conditioning - desi beauty secret!", icon: "leaf" },
  { tip: "Rose water toner keeps skin fresh in Indian weather.", icon: "rose" },
  { tip: "Aloe vera gel is perfect for post-sun skin care.", icon: "sunny" },
  { tip: "Multani mitti removes tan and brightens skin naturally.", icon: "sparkles" },
  { tip: "Massage scalp with warm oil before every hair wash.", icon: "hand-left" },
  { tip: "Green tea helps reduce puffy eyes - try ice cubes!", icon: "cafe" },
  { tip: "Besan (gram flour) pack is great for oily skin.", icon: "nutrition" },
  { tip: "Stay hydrated - drink 8 glasses of water daily!", icon: "water" },
  { tip: "Apply sunscreen before stepping out - even on cloudy days.", icon: "shield-checkmark" },
  { tip: "Amla (gooseberry) juice strengthens hair from roots.", icon: "leaf" },
  { tip: "Cucumber slices reduce dark circles naturally.", icon: "eye" },
  { tip: "Hibiscus oil promotes hair growth and prevents greying.", icon: "flower" },
  { tip: "Neem face wash controls acne and pimples effectively.", icon: "medical" },
  { tip: "Papaya pack exfoliates dead skin cells gently.", icon: "nutrition" },
  { tip: "Fenugreek (methi) seeds are amazing for hair health.", icon: "leaf" },
  { tip: "Honey and lemon face mask brightens dull skin.", icon: "sunny" },
  { tip: "Regular threading keeps eyebrows neat and shaped.", icon: "eye" },
  { tip: "Apply kajal/kohl before bed to keep eyes cool.", icon: "eye" },
  { tip: "Curd (dahi) pack adds shine and softness to hair.", icon: "nutrition" },
  { tip: "Sandalwood paste soothes skin and reduces blemishes.", icon: "flower" },
  { tip: "Drink coconut water for natural skin hydration.", icon: "water" },
  { tip: "Regular facials keep skin clean and glowing.", icon: "sparkles" },
];

export default function HomeScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { user, fetchUser, userId } = useUserStore();
  const [refreshing, setRefreshing] = useState(false);
  const [greeting, setGreeting] = useState('');
  const [currentTip, setCurrentTip] = useState(BEAUTY_TIPS[0]);

  useEffect(() => {
    const hour = new Date().getHours();
    if (hour < 12) setGreeting('Good Morning');
    else if (hour < 17) setGreeting('Good Afternoon');
    else setGreeting('Good Evening');

    // Set tip based on current hour
    const tipIndex = hour % BEAUTY_TIPS.length;
    setCurrentTip(BEAUTY_TIPS[tipIndex]);

    if (userId) {
      fetchUser();
    }

    // Update tip every hour
    const interval = setInterval(() => {
      const newHour = new Date().getHours();
      const newTipIndex = newHour % BEAUTY_TIPS.length;
      setCurrentTip(BEAUTY_TIPS[newTipIndex]);
    }, 60 * 60 * 1000); // Every hour

    return () => clearInterval(interval);
  }, [userId]);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    await fetchUser();
    // Update tip on refresh too
    const hour = new Date().getHours();
    const tipIndex = hour % BEAUTY_TIPS.length;
    setCurrentTip(BEAUTY_TIPS[tipIndex]);
    setRefreshing(false);
  }, [fetchUser]);

  const handleOccasionSelect = (occasion: string) => {
    router.push({ pathname: '/get-advice', params: { occasion } });
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
            <Text style={styles.userName}>{user?.name || 'Beauty Lover'}</Text>
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
              <Text style={styles.scanPromptDesc}>Take AI scan for personalized packages</Text>
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
          <Text style={styles.sectionTitle}>Aaj Ka Mood? (Today's Occasion)</Text>
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
            <Text style={styles.sectionTitle}>Popular Packages</Text>
            <TouchableOpacity onPress={() => router.push('/(tabs)/services')}>
              <Text style={styles.seeAllText}>See All</Text>
            </TouchableOpacity>
          </View>
          <View style={styles.featuredServices}>
            <TouchableOpacity style={styles.featuredCard} onPress={() => router.push('/(tabs)/services')}>
              <View style={styles.featuredIconContainer}>
                <Ionicons name="cut" size={28} color="#D4AF37" />
              </View>
              <Text style={styles.featuredTitle}>Hair Styling</Text>
              <Text style={styles.featuredPrice}>From ₹499</Text>
              <View style={styles.valueBadge}>
                <Text style={styles.valueBadgeText}>Value Deal</Text>
              </View>
            </TouchableOpacity>
            <TouchableOpacity style={styles.featuredCard} onPress={() => router.push('/(tabs)/services')}>
              <View style={styles.featuredIconContainer}>
                <Ionicons name="flower" size={28} color="#D4AF37" />
              </View>
              <Text style={styles.featuredTitle}>Glow Facial</Text>
              <Text style={styles.featuredPrice}>From ₹799</Text>
              <View style={styles.valueBadge}>
                <Text style={styles.valueBadgeText}>Best Seller</Text>
              </View>
            </TouchableOpacity>
          </View>
        </Animated.View>

        {/* Tip of the Hour */}
        <Animated.View entering={FadeInDown.delay(600)} style={styles.tipCard}>
          <View style={styles.tipHeader}>
            <Ionicons name={currentTip.icon as any} size={20} color="#D4AF37" />
            <Text style={styles.tipTitle}>Beauty Tip of the Hour</Text>
          </View>
          <Text style={styles.tipText}>{currentTip.tip}</Text>
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
    textAlign: 'center',
  },
  occasionsScroll: {
    marginLeft: -4,
  },
  occasionItem: {
    alignItems: 'center',
    marginRight: 16,
    width: 70,
  },
  occasionIcon: {
    width: 60,
    height: 60,
    borderRadius: 30,
    backgroundColor: 'rgba(212, 175, 55, 0.1)',
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: 8,
    borderWidth: 1,
    borderColor: 'rgba(212, 175, 55, 0.3)',
  },
  occasionLabel: {
    fontSize: 11,
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
    fontSize: 14,
    fontWeight: '700',
    color: '#D4AF37',
    marginTop: 4,
  },
  valueBadge: {
    backgroundColor: 'rgba(46, 204, 113, 0.2)',
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 10,
    marginTop: 8,
  },
  valueBadgeText: {
    fontSize: 10,
    color: '#2ECC71',
    fontWeight: '600',
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
