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
import { COLORS, FONTS } from '../../src/theme/colors';

export default function ProfileScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { user, fetchUser } = useUserStore();
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchUser().finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <View style={[styles.container, { paddingTop: insets.top }]}>
        <View style={styles.loadingContainer}>
          <ActivityIndicator size="large" color={COLORS.black} />
        </View>
      </View>
    );
  }

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <ScrollView showsVerticalScrollIndicator={false}>
        <Animated.View entering={FadeIn} style={styles.header}>
          <View style={styles.avatar}>
            <Ionicons name="person" size={40} color={COLORS.black} />
          </View>
          <Text style={styles.name}>{user?.name || 'Guest User'}</Text>
          <Text style={styles.email}>{user?.email || 'No email set'}</Text>
        </Animated.View>

        <Animated.View entering={FadeInDown.delay(100)} style={styles.section}>
          <Text style={styles.sectionTitle}>Beauty Profile</Text>
          <View style={styles.profileCard}>
            {user?.skin_type && (
              <View style={styles.profileItem}>
                <Ionicons name="water" size={22} color={COLORS.black} />
                <Text style={styles.profileLabel}>Skin Type</Text>
                <Text style={styles.profileValue}>{user.skin_type}</Text>
              </View>
            )}
            {user?.hair_type && (
              <View style={styles.profileItem}>
                <Ionicons name="leaf" size={22} color={COLORS.black} />
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
              <Ionicons name="notifications-outline" size={22} color={COLORS.textSecondary} />
              <Text style={styles.menuText}>Notifications</Text>
              <Ionicons name="chevron-forward" size={20} color={COLORS.textMuted} />
            </TouchableOpacity>
            <TouchableOpacity style={styles.menuItem}>
              <Ionicons name="help-circle-outline" size={22} color={COLORS.textSecondary} />
              <Text style={styles.menuText}>Help & Support</Text>
              <Ionicons name="chevron-forward" size={20} color={COLORS.textMuted} />
            </TouchableOpacity>
            <TouchableOpacity style={styles.menuItem}>
              <Ionicons name="document-text-outline" size={22} color={COLORS.textSecondary} />
              <Text style={styles.menuText}>Terms & Privacy</Text>
              <Ionicons name="chevron-forward" size={20} color={COLORS.textMuted} />
            </TouchableOpacity>
          </View>
        </Animated.View>

        <Animated.View entering={FadeInDown.delay(300)} style={styles.section}>
          <TouchableOpacity style={styles.logoutBtn} onPress={() => router.replace('/')}>
            <Ionicons name="log-out-outline" size={22} color={COLORS.black} />
            <Text style={styles.logoutText}>Log Out</Text>
          </TouchableOpacity>
        </Animated.View>

        <View style={{ height: 100 }} />
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.background },
  loadingContainer: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  header: { alignItems: 'center', paddingVertical: 32, backgroundColor: COLORS.white, borderBottomWidth: 1, borderBottomColor: COLORS.border },
  avatar: { width: 88, height: 88, borderRadius: 44, backgroundColor: COLORS.primaryLight, justifyContent: 'center', alignItems: 'center', marginBottom: 14, borderWidth: 2, borderColor: COLORS.black },
  name: { fontSize: FONTS.sizes.xl, fontFamily: FONTS.family.bold, color: COLORS.black },
  email: { fontSize: FONTS.sizes.base, fontFamily: FONTS.family.regular, color: COLORS.textSecondary, marginTop: 4 },
  section: { paddingHorizontal: 20, marginTop: 28 },
  sectionTitle: { fontSize: FONTS.sizes.sm, fontFamily: FONTS.family.semibold, color: COLORS.textSecondary, marginBottom: 12, textTransform: 'uppercase', letterSpacing: 1 },
  profileCard: { backgroundColor: COLORS.white, borderRadius: 12, padding: 18, borderWidth: 1, borderColor: COLORS.border },
  profileItem: { flexDirection: 'row', alignItems: 'center', paddingVertical: 14, borderBottomWidth: 1, borderBottomColor: COLORS.borderLight, gap: 14 },
  profileLabel: { flex: 1, fontSize: FONTS.sizes.base, fontFamily: FONTS.family.regular, color: COLORS.textSecondary },
  profileValue: { fontSize: FONTS.sizes.base, fontFamily: FONTS.family.semibold, color: COLORS.black },
  emptyProfile: { alignItems: 'center', paddingVertical: 20 },
  emptyProfileText: { fontSize: FONTS.sizes.base, fontFamily: FONTS.family.regular, color: COLORS.textSecondary, textAlign: 'center' },
  quizBtn: { backgroundColor: COLORS.black, paddingVertical: 12, paddingHorizontal: 24, borderRadius: 24, marginTop: 14 },
  quizBtnText: { fontSize: FONTS.sizes.base, fontFamily: FONTS.family.semibold, color: COLORS.white },
  menuCard: { backgroundColor: COLORS.white, borderRadius: 12, borderWidth: 1, borderColor: COLORS.border },
  menuItem: { flexDirection: 'row', alignItems: 'center', padding: 18, borderBottomWidth: 1, borderBottomColor: COLORS.borderLight, gap: 14 },
  menuText: { flex: 1, fontSize: FONTS.sizes.base, fontFamily: FONTS.family.medium, color: COLORS.textPrimary },
  logoutBtn: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', backgroundColor: COLORS.primaryLight, padding: 18, borderRadius: 12, gap: 10, borderWidth: 1, borderColor: COLORS.border },
  logoutText: { fontSize: FONTS.sizes.base, fontFamily: FONTS.family.semibold, color: COLORS.black },
});
