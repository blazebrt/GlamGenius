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
import Animated, { FadeIn, FadeInDown } from 'react-native-reanimated';
import { useUserStore } from '../../src/store/userStore';

export default function ProfileScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { user, loadUser, logout } = useUserStore();
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadUser().finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <View style={[styles.container, { paddingTop: insets.top }]}>
        <View style={styles.loadingContainer}>
          <ActivityIndicator size="large" color="#0EA5E9" />
        </View>
      </View>
    );
  }

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <ScrollView showsVerticalScrollIndicator={false}>
        <Animated.View entering={FadeIn} style={styles.header}>
          <View style={styles.avatar}>
            <Ionicons name="person" size={40} color="#0EA5E9" />
          </View>
          <Text style={styles.name}>{user?.name || 'Guest User'}</Text>
          <Text style={styles.email}>{user?.email || 'No email set'}</Text>
        </Animated.View>

        <Animated.View entering={FadeInDown.delay(100)} style={styles.section}>
          <Text style={styles.sectionTitle}>Beauty Profile</Text>
          <View style={styles.profileCard}>
            {user?.skin_type && (
              <View style={styles.profileItem}>
                <Ionicons name="water" size={20} color="#0EA5E9" />
                <Text style={styles.profileLabel}>Skin Type</Text>
                <Text style={styles.profileValue}>{user.skin_type}</Text>
              </View>
            )}
            {user?.hair_type && (
              <View style={styles.profileItem}>
                <Ionicons name="leaf" size={20} color="#10B981" />
                <Text style={styles.profileLabel}>Hair Type</Text>
                <Text style={styles.profileValue}>{user.hair_type}</Text>
              </View>
            )}
            {!user?.skin_type && !user?.hair_type && (
              <View style={styles.emptyProfile}>
                <Text style={styles.emptyProfileText}>Complete the Style Quiz to build your profile</Text>
                <TouchableOpacity style={styles.quizBtn} onPress={() => router.push('/style-quiz')}>
                  <Text style={styles.quizBtnText}>Take Quiz</Text>
                </TouchableOpacity>
              </View>
            )}
          </View>
        </Animated.View>

        <Animated.View entering={FadeInDown.delay(200)} style={styles.section}>
          <Text style={styles.sectionTitle}>Settings</Text>
          <View style={styles.menuCard}>
            <TouchableOpacity style={styles.menuItem}>
              <Ionicons name="notifications-outline" size={22} color="#64748B" />
              <Text style={styles.menuText}>Notifications</Text>
              <Ionicons name="chevron-forward" size={20} color="#94A3B8" />
            </TouchableOpacity>
            <TouchableOpacity style={styles.menuItem}>
              <Ionicons name="help-circle-outline" size={22} color="#64748B" />
              <Text style={styles.menuText}>Help & Support</Text>
              <Ionicons name="chevron-forward" size={20} color="#94A3B8" />
            </TouchableOpacity>
            <TouchableOpacity style={styles.menuItem}>
              <Ionicons name="document-text-outline" size={22} color="#64748B" />
              <Text style={styles.menuText}>Terms & Privacy</Text>
              <Ionicons name="chevron-forward" size={20} color="#94A3B8" />
            </TouchableOpacity>
          </View>
        </Animated.View>

        <Animated.View entering={FadeInDown.delay(300)} style={styles.section}>
          <TouchableOpacity style={styles.logoutBtn} onPress={logout}>
            <Ionicons name="log-out-outline" size={22} color="#EF4444" />
            <Text style={styles.logoutText}>Log Out</Text>
          </TouchableOpacity>
        </Animated.View>

        <View style={{ height: 100 }} />
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#F8FAFC' },
  loadingContainer: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  header: { alignItems: 'center', paddingVertical: 30, backgroundColor: '#FFFFFF', borderBottomWidth: 1, borderBottomColor: '#E2E8F0' },
  avatar: { width: 80, height: 80, borderRadius: 40, backgroundColor: '#E0F2FE', justifyContent: 'center', alignItems: 'center', marginBottom: 12 },
  name: { fontSize: 22, fontWeight: '700', color: '#1E293B' },
  email: { fontSize: 14, color: '#64748B', marginTop: 4 },
  section: { paddingHorizontal: 20, marginTop: 24 },
  sectionTitle: { fontSize: 14, fontWeight: '600', color: '#64748B', marginBottom: 12, textTransform: 'uppercase', letterSpacing: 0.5 },
  profileCard: { backgroundColor: '#FFFFFF', borderRadius: 16, padding: 16, borderWidth: 1, borderColor: '#E2E8F0' },
  profileItem: { flexDirection: 'row', alignItems: 'center', paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: '#F1F5F9', gap: 12 },
  profileLabel: { flex: 1, fontSize: 14, color: '#64748B' },
  profileValue: { fontSize: 14, fontWeight: '600', color: '#1E293B' },
  emptyProfile: { alignItems: 'center', paddingVertical: 16 },
  emptyProfileText: { fontSize: 14, color: '#64748B', textAlign: 'center' },
  quizBtn: { backgroundColor: '#0EA5E9', paddingVertical: 10, paddingHorizontal: 20, borderRadius: 20, marginTop: 12 },
  quizBtnText: { fontSize: 14, fontWeight: '600', color: '#FFFFFF' },
  menuCard: { backgroundColor: '#FFFFFF', borderRadius: 16, borderWidth: 1, borderColor: '#E2E8F0' },
  menuItem: { flexDirection: 'row', alignItems: 'center', padding: 16, borderBottomWidth: 1, borderBottomColor: '#F1F5F9', gap: 12 },
  menuText: { flex: 1, fontSize: 15, color: '#1E293B' },
  logoutBtn: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', backgroundColor: '#FEE2E2', padding: 16, borderRadius: 16, gap: 10 },
  logoutText: { fontSize: 15, fontWeight: '600', color: '#EF4444' },
});
