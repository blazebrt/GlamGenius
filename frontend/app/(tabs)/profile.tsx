import React, { useEffect } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  TextInput,
  Alert,
  Platform,
} from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { useUserStore } from '../../src/store/userStore';
import { COLORS, FONTS, SPACING, RADIUS, SHADOWS } from '../../src/theme/colors';
import { requestPrivacyAccountDeletion, getPrivacyAccountDeletionStatus, cancelPrivacyAccountDeletion, AccountDeletion } from '../../src/services/apiV2';

const VIBES = [
  { id: 'natural', label: 'Natural' },
  { id: 'polished', label: 'Polished' },
  { id: 'festive', label: 'Festive' },
  { id: 'classic', label: 'Classic' },
  { id: 'trendy', label: 'Trendy' },
];

function notify(title: string, message: string) {
  if (Platform.OS === 'web') window.alert(`${title}\n\n${message}`);
  else Alert.alert(title, message);
}

export default function ProfileScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { user, fetchUser, updateUser, logout } = useUserStore();
  const [name, setName] = React.useState(user?.name || '');
  const [city, setCity] = React.useState(user?.city || '');
  const [diet, setDiet] = React.useState(user?.diet || 'veg');
  const [height, setHeight] = React.useState(user?.height_cm ? String(user.height_cm) : '');
  const [styleVibe, setStyleVibe] = React.useState(user?.style_vibe || 'natural');
  const [deletionStatus, setDeletionStatus] = React.useState<AccountDeletion | null>(null);

  useEffect(() => {
    void fetchUser();
    getPrivacyAccountDeletionStatus()
      .then(setDeletionStatus)
      .catch((e) => {
         if (e.response?.status !== 404) {
           console.warn('Failed to fetch deletion status', e);
         }
      });
  }, [fetchUser]);

  useEffect(() => {
    setName(user?.name || '');
    setCity(user?.city || '');
    setDiet(user?.diet || 'veg');
    setHeight(user?.height_cm ? String(user.height_cm) : '');
    setStyleVibe(user?.style_vibe || 'natural');
  }, [user?.id, user?.updated_at, user?.name, user?.city, user?.diet, user?.height_cm, user?.style_vibe]);

  const save = async () => {
    const height_cm = height ? Number(height) : undefined;
    if (height && (Number.isNaN(height_cm!) || height_cm! < 100 || height_cm! > 230)) {
      notify('Check height', 'Enter height in cm between 100 and 230.');
      return;
    }
    await updateUser({
      name,
      city,
      diet,
      height_cm,
      style_vibe: styleVibe,
    });
    notify('Saved', 'Your stylist profile was updated.');
  };

  const handleDeleteAccount = () => {
    if (Platform.OS === 'web') {
      if (window.confirm('Are you sure you want to delete your account? This will permanently remove all your data.')) {
        requestPrivacyAccountDeletion().then(setDeletionStatus).catch((e) => notify('Error', 'Failed to request deletion.'));
      }
    } else {
      Alert.alert(
        'Delete Account',
        'Are you sure you want to delete your account? This will permanently remove all your data.',
        [
          { text: 'Cancel', style: 'cancel' },
          { text: 'Delete', style: 'destructive', onPress: () => {
            requestPrivacyAccountDeletion().then(setDeletionStatus).catch((e) => notify('Error', 'Failed to request deletion.'));
          } },
        ]
      );
    }
  };

  const handleCancelDeletion = () => {
    cancelPrivacyAccountDeletion()
      .then(() => setDeletionStatus(null))
      .catch((e) => notify('Error', e.response?.data?.detail?.message || 'Failed to cancel deletion.'));
  };

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <ScrollView contentContainerStyle={{ padding: SPACING.lg, paddingBottom: 120 }}>
        <Text style={styles.label}>IN YOUR POCKET</Text>
        <Text style={styles.title}>Stylist profile</Text>
        <Text style={styles.subtitle}>
          Keep account basics here. My Appearance holds your confirmed style, fit, goals, and reviewable observations.
        </Text>

        <View style={styles.planCard}>
          <Text style={styles.planTitle}>Private beta member</Text>
          <Text style={styles.planSub} testID="beta-plan-note">
            Full access is included with your invite — nothing to pay.
          </Text>
        </View>

        {deletionStatus && (
          <View style={[styles.planCard, { backgroundColor: COLORS.error }]}>
            <Text style={styles.planTitle}>Account Deletion Pending</Text>
            <Text style={styles.planSub}>
              Your account is scheduled for deletion (Status: {deletionStatus.status}).
            </Text>
            <TouchableOpacity style={[styles.upgradeBtn, { marginTop: 16 }]} onPress={handleCancelDeletion}>
              <Text style={[styles.upgradeText, { color: COLORS.error }]}>Cancel Deletion</Text>
            </TouchableOpacity>
          </View>
        )}

        <View style={styles.stats}>
          <Stat label="Skin tone" value={user?.skin_tone || '—'} />
          <Stat label="Undertone" value={user?.undertone || '—'} />
          <Stat label="City" value={user?.city || '—'} />
          <Stat label="Vibe" value={user?.style_vibe || '—'} />
        </View>

        <Text style={styles.fieldLabel}>Name</Text>
        <TextInput style={styles.input} value={name} onChangeText={setName} placeholderTextColor={COLORS.textMuted} />

        <Text style={styles.fieldLabel}>City (climate tip)</Text>
        <TextInput style={styles.input} value={city} onChangeText={setCity} placeholder="e.g. Mumbai, Delhi, Bengaluru" placeholderTextColor={COLORS.textMuted} />

        <Text style={styles.fieldLabel}>Height (cm, optional)</Text>
        <TextInput
          style={styles.input}
          value={height}
          onChangeText={setHeight}
          keyboardType="numeric"
          placeholder="165"
          placeholderTextColor={COLORS.textMuted}
        />

        <Text style={styles.fieldLabel}>Style vibe</Text>
        <View style={styles.dietRow}>
          {VIBES.map((d) => (
            <TouchableOpacity key={d.id} style={[styles.dietChip, styleVibe === d.id && styles.dietChipActive]} onPress={() => setStyleVibe(d.id)}>
              <Text style={[styles.dietText, styleVibe === d.id && styles.dietTextActive]}>{d.label}</Text>
            </TouchableOpacity>
          ))}
        </View>

        <Text style={styles.fieldLabel}>Diet</Text>
        <View style={styles.dietRow}>
          {['veg', 'egg', 'non-veg'].map((d) => (
            <TouchableOpacity key={d} style={[styles.dietChip, diet === d && styles.dietChipActive]} onPress={() => setDiet(d)}>
              <Text style={[styles.dietText, diet === d && styles.dietTextActive]}>{d}</Text>
            </TouchableOpacity>
          ))}
        </View>

        <TouchableOpacity style={styles.saveBtn} onPress={save}>
          <Text style={styles.saveText}>Save stylist profile</Text>
        </TouchableOpacity>

        <TouchableOpacity accessibilityRole="button" accessibilityLabel="Open My Appearance" style={styles.appearanceCard} onPress={() => router.push('/my-appearance')}>
          <Ionicons name="person-circle-outline" size={28} color={COLORS.primary} />
          <View style={{ flex: 1 }}><Text style={styles.appearanceTitle}>My Appearance</Text><Text style={styles.appearanceText}>Verified attributes, suggestions, fit preferences, goals, and readiness</Text></View>
          <Ionicons name="chevron-forward" size={20} color={COLORS.textMuted} />
        </TouchableOpacity>

        {/* The "Your plan" card was removed with the payment UI cleanup.
            Paid membership is a future workstream; no CTA appears here. */}

        <TouchableOpacity accessibilityRole="button" accessibilityLabel="Open Progress" style={styles.appearanceCard} onPress={() => router.push('/progress')}>
          <Ionicons name="trending-up-outline" size={28} color={COLORS.primary} />
          <View style={{ flex: 1 }}><Text style={styles.appearanceTitle}>Progress</Text><Text style={styles.appearanceText}>Explainable numbers, your goals, and what you have reached — no overall rating</Text></View>
          <Ionicons name="chevron-forward" size={20} color={COLORS.textMuted} />
        </TouchableOpacity>

        <TouchableOpacity accessibilityRole="button" accessibilityLabel="Open Memory" style={styles.appearanceCard} onPress={() => router.push('/memory')}>
          <Ionicons name="bookmark-outline" size={28} color={COLORS.primary} />
          <View style={{ flex: 1 }}><Text style={styles.appearanceTitle}>Memory</Text><Text style={styles.appearanceText}>What GlamGenius remembers, where it came from, and how to change or delete it</Text></View>
          <Ionicons name="chevron-forward" size={20} color={COLORS.textMuted} />
        </TouchableOpacity>

        <TouchableOpacity accessibilityRole="button" accessibilityLabel="Open Improve" style={styles.appearanceCard} onPress={() => router.push('/improve')}>
          <Ionicons name="list-outline" size={28} color={COLORS.primary} />
          <View style={{ flex: 1 }}><Text style={styles.appearanceTitle}>Improve</Text><Text style={styles.appearanceText}>Your routines, how they are going, and what on your shelf needs attention</Text></View>
          <Ionicons name="chevron-forward" size={20} color={COLORS.textMuted} />
        </TouchableOpacity>

        <TouchableOpacity style={styles.linkRow} onPress={() => router.push('/(auth)/welcome')}>
          <Ionicons name="log-in-outline" size={20} color={COLORS.primary} />
          <Text style={styles.linkText}>Sign in / create account</Text>
        </TouchableOpacity>

        <TouchableOpacity style={styles.linkRow} onPress={() => router.push('/style-quiz')}>
          <Ionicons name="clipboard-outline" size={20} color={COLORS.primary} />
          <Text style={styles.linkText}>Retake stylist quiz</Text>
        </TouchableOpacity>

        <TouchableOpacity
          style={styles.linkRow}
          onPress={async () => {
            await logout();
            router.replace('/(auth)/welcome');
          }}
        >
          <Ionicons name="log-out-outline" size={20} color={COLORS.primary} />
          <Text style={styles.linkText}>Sign out</Text>
        </TouchableOpacity>

        {!deletionStatus && (
          <TouchableOpacity style={[styles.linkRow, { marginTop: 24 }]} onPress={handleDeleteAccount}>
            <Ionicons name="trash-outline" size={20} color={COLORS.error} />
            <Text style={[styles.linkText, { color: COLORS.error }]}>Delete Account</Text>
          </TouchableOpacity>
        )}

        <Text style={styles.disclaimer}>
          GlamGenius is your pocket fashion stylist and wellness coach. It does not diagnose medical conditions.
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
  title: { fontFamily: FONTS.family.heading, fontSize: 28, color: COLORS.textPrimary, marginTop: 4 },
  subtitle: { fontFamily: FONTS.family.body, fontSize: 13, color: COLORS.textSecondary, marginTop: 6, marginBottom: 16, lineHeight: 19 },
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
  dietRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginBottom: 8 },
  dietChip: {
    paddingHorizontal: 14, paddingVertical: 8, borderRadius: RADIUS.full,
    backgroundColor: COLORS.card, borderWidth: 1, borderColor: COLORS.border,
  },
  dietChipActive: { backgroundColor: COLORS.primary, borderColor: COLORS.primary },
  dietText: { fontFamily: FONTS.family.bodyMedium, fontSize: 13, color: COLORS.textSecondary },
  dietTextActive: { color: COLORS.white },
  saveBtn: {
    marginTop: 20, backgroundColor: COLORS.primary, borderRadius: RADIUS.lg, paddingVertical: 14, alignItems: 'center',
  },
  saveText: { fontFamily: FONTS.family.bodySemibold, color: COLORS.white, fontSize: 15 },
  appearanceCard: { flexDirection: 'row', alignItems: 'center', gap: 12, marginTop: 18, padding: 14, backgroundColor: COLORS.card, borderWidth: 1, borderColor: COLORS.border, borderRadius: RADIUS.lg },
  appearanceTitle: { fontFamily: FONTS.family.bodySemibold, color: COLORS.textPrimary, fontSize: 15 },
  appearanceText: { fontFamily: FONTS.family.body, color: COLORS.textSecondary, fontSize: 11, lineHeight: 16, marginTop: 3 },
  linkRow: { flexDirection: 'row', alignItems: 'center', gap: 10, marginTop: 18 },
  linkText: { fontFamily: FONTS.family.bodyMedium, fontSize: 14, color: COLORS.primary },
  disclaimer: { marginTop: 28, fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.textMuted, lineHeight: 18 },
});
