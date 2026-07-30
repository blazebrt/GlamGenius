import React, { useEffect } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  TextInput,
  Alert,
} from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { useUserStore } from '../../src/store/userStore';
import { COLORS, FONTS, SPACING, RADIUS, SHADOWS } from '../../src/theme/colors';

export default function ProfileScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { user, fetchUser, updateUser, refreshSubscription } = useUserStore();
  const [name, setName] = React.useState(user?.name || '');
  const [city, setCity] = React.useState(user?.city || '');
  const [diet, setDiet] = React.useState(user?.diet || 'veg');

  useEffect(() => {
    fetchUser();
    refreshSubscription();
  }, []);

  useEffect(() => {
    setName(user?.name || '');
    setCity(user?.city || '');
    setDiet(user?.diet || 'veg');
  }, [user?.id]);

  const save = async () => {
    await updateUser({ name, city, diet });
    Alert.alert('Saved', 'Your profile was updated.');
  };

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <ScrollView contentContainerStyle={{ padding: SPACING.lg, paddingBottom: 120 }}>
        <Text style={styles.label}>PROFILE</Text>
        <Text style={styles.title}>Your style profile</Text>

        <View style={styles.planCard}>
          <Text style={styles.planTitle}>{user?.plan === 'plus' ? 'Plus member' : 'Free plan'}</Text>
          <Text style={styles.planSub}>
            {user?.plan === 'plus'
              ? `Unlimited checks · renews/expires ${user?.plan_expires_at ? new Date(user.plan_expires_at).toLocaleDateString('en-IN') : ''}`
              : `${user?.scans_remaining_free ?? 0} of ${user?.free_scans_per_month ?? 2} checks left this month`}
          </Text>
          {user?.plan !== 'plus' && (
            <TouchableOpacity style={styles.upgradeBtn} onPress={() => router.push('/subscription')}>
              <Text style={styles.upgradeText}>Upgrade to Plus</Text>
            </TouchableOpacity>
          )}
        </View>

        <View style={styles.stats}>
          <Stat label="Skin tone" value={user?.skin_tone || '—'} />
          <Stat label="Undertone" value={user?.undertone || '—'} />
          <Stat label="Skin type" value={user?.skin_type || '—'} />
          <Stat label="Hair" value={user?.hair_type || '—'} />
        </View>

        <Text style={styles.fieldLabel}>Name</Text>
        <TextInput style={styles.input} value={name} onChangeText={setName} placeholderTextColor={COLORS.textMuted} />

        <Text style={styles.fieldLabel}>City (climate tip)</Text>
        <TextInput style={styles.input} value={city} onChangeText={setCity} placeholder="e.g. Mumbai, Delhi, Bengaluru" placeholderTextColor={COLORS.textMuted} />

        <Text style={styles.fieldLabel}>Diet</Text>
        <View style={styles.dietRow}>
          {['veg', 'egg', 'non-veg'].map((d) => (
            <TouchableOpacity key={d} style={[styles.dietChip, diet === d && styles.dietChipActive]} onPress={() => setDiet(d)}>
              <Text style={[styles.dietText, diet === d && styles.dietTextActive]}>{d}</Text>
            </TouchableOpacity>
          ))}
        </View>

        <TouchableOpacity style={styles.saveBtn} onPress={save}>
          <Text style={styles.saveText}>Save profile</Text>
        </TouchableOpacity>

        <TouchableOpacity style={styles.linkRow} onPress={() => router.push('/(auth)/welcome')}>
          <Ionicons name="log-in-outline" size={20} color={COLORS.primary} />
          <Text style={styles.linkText}>Sign in / create account</Text>
        </TouchableOpacity>

        <TouchableOpacity style={styles.linkRow} onPress={() => router.push('/style-quiz')}>
          <Ionicons name="clipboard-outline" size={20} color={COLORS.primary} />
          <Text style={styles.linkText}>Retake profile quiz</Text>
        </TouchableOpacity>

        <Text style={styles.disclaimer}>
          GlamGenius is a personal stylist and wellness coach. It does not diagnose medical conditions.
        </Text>
      </ScrollView>
    </View>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.stat}>
      <Text style={styles.statLabel}>{label}</Text>
      <Text style={styles.statValue}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.backgroundSecondary },
  label: { fontFamily: FONTS.family.bodySemibold, fontSize: 11, color: COLORS.primary, letterSpacing: 1.4 },
  title: { fontFamily: FONTS.family.heading, fontSize: 28, color: COLORS.textPrimary, marginTop: 4, marginBottom: 16 },
  planCard: {
    backgroundColor: COLORS.primary, borderRadius: RADIUS.lg, padding: 16, marginBottom: 16, ...SHADOWS.md,
  },
  planTitle: { fontFamily: FONTS.family.headingMedium, fontSize: 20, color: COLORS.white },
  planSub: { fontFamily: FONTS.family.body, fontSize: 13, color: 'rgba(255,255,255,0.85)', marginTop: 6 },
  upgradeBtn: {
    marginTop: 12, alignSelf: 'flex-start', backgroundColor: COLORS.white,
    paddingHorizontal: 14, paddingVertical: 8, borderRadius: RADIUS.full,
  },
  upgradeText: { fontFamily: FONTS.family.bodySemibold, color: COLORS.primary, fontSize: 13 },
  stats: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginBottom: 16 },
  stat: {
    width: '48%', backgroundColor: COLORS.card, borderRadius: RADIUS.md, padding: 12,
    borderWidth: 1, borderColor: COLORS.border,
  },
  statLabel: { fontFamily: FONTS.family.body, fontSize: 11, color: COLORS.textMuted },
  statValue: { fontFamily: FONTS.family.bodySemibold, fontSize: 14, color: COLORS.textPrimary, marginTop: 4, textTransform: 'capitalize' },
  fieldLabel: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.textPrimary, marginBottom: 6, marginTop: 8 },
  input: {
    backgroundColor: COLORS.card, borderWidth: 1, borderColor: COLORS.border, borderRadius: RADIUS.md,
    paddingHorizontal: 14, paddingVertical: 12, fontFamily: FONTS.family.body, fontSize: 15, color: COLORS.textPrimary,
  },
  dietRow: { flexDirection: 'row', gap: 8, marginBottom: 8 },
  dietChip: {
    paddingHorizontal: 14, paddingVertical: 8, borderRadius: RADIUS.full,
    backgroundColor: COLORS.card, borderWidth: 1, borderColor: COLORS.border,
  },
  dietChipActive: { backgroundColor: COLORS.primary, borderColor: COLORS.primary },
  dietText: { fontFamily: FONTS.family.bodyMedium, fontSize: 13, color: COLORS.textSecondary, textTransform: 'capitalize' },
  dietTextActive: { color: COLORS.white },
  saveBtn: {
    marginTop: 20, backgroundColor: COLORS.primary, borderRadius: RADIUS.lg, paddingVertical: 14, alignItems: 'center',
  },
  saveText: { fontFamily: FONTS.family.bodySemibold, color: COLORS.white, fontSize: 15 },
  linkRow: { flexDirection: 'row', alignItems: 'center', gap: 10, marginTop: 18 },
  linkText: { fontFamily: FONTS.family.bodyMedium, fontSize: 14, color: COLORS.primary },
  disclaimer: { marginTop: 28, fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.textMuted, lineHeight: 18 },
});
