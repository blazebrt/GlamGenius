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

const BODY_TYPES = [
  { id: 'slim', label: 'Slim / petite' },
  { id: 'average', label: 'Average' },
  { id: 'athletic', label: 'Athletic' },
  { id: 'curvy', label: 'Curvy' },
  { id: 'plus', label: 'Plus / fuller' },
  { id: 'prefer_not', label: 'Prefer not' },
];

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
  const { user, fetchUser, updateUser, refreshSubscription } = useUserStore();
  const [name, setName] = React.useState(user?.name || '');
  const [city, setCity] = React.useState(user?.city || '');
  const [diet, setDiet] = React.useState(user?.diet || 'veg');
  const [height, setHeight] = React.useState(user?.height_cm ? String(user.height_cm) : '');
  const [weight, setWeight] = React.useState(user?.weight_kg ? String(user.weight_kg) : '');
  const [bodyType, setBodyType] = React.useState(user?.body_type || 'average');
  const [styleVibe, setStyleVibe] = React.useState(user?.style_vibe || 'natural');

  useEffect(() => {
    fetchUser();
    refreshSubscription();
  }, []);

  useEffect(() => {
    setName(user?.name || '');
    setCity(user?.city || '');
    setDiet(user?.diet || 'veg');
    setHeight(user?.height_cm ? String(user.height_cm) : '');
    setWeight(user?.weight_kg ? String(user.weight_kg) : '');
    setBodyType(user?.body_type || 'average');
    setStyleVibe(user?.style_vibe || 'natural');
  }, [user?.id, user?.updated_at]);

  const save = async () => {
    const height_cm = height ? Number(height) : undefined;
    const weight_kg = weight ? Number(weight) : undefined;
    if (height && (Number.isNaN(height_cm!) || height_cm! < 100 || height_cm! > 230)) {
      notify('Check height', 'Enter height in cm between 100 and 230.');
      return;
    }
    if (weight && (Number.isNaN(weight_kg!) || weight_kg! < 30 || weight_kg! > 250)) {
      notify('Check weight', 'Enter weight in kg between 30 and 250.');
      return;
    }
    await updateUser({
      name,
      city,
      diet,
      height_cm,
      weight_kg,
      body_type: bodyType,
      style_vibe: styleVibe,
    });
    notify('Saved', 'Your stylist profile was updated.');
  };

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <ScrollView contentContainerStyle={{ padding: SPACING.lg, paddingBottom: 120 }}>
        <Text style={styles.label}>IN YOUR POCKET</Text>
        <Text style={styles.title}>Stylist profile</Text>
        <Text style={styles.subtitle}>
          Skin tone from checks + height, weight, body frame, and vibe help your coach pick colours, fits, and outfits.
        </Text>

        <View style={styles.planCard}>
          <Text style={styles.planTitle}>{user?.plan === 'plus' ? 'Plus member' : 'Free plan'}</Text>
          <Text style={styles.planSub}>
            {user?.plan === 'plus'
              ? `Unlimited checks · ${user?.plan_expires_at ? new Date(user.plan_expires_at).toLocaleDateString('en-IN') : ''}`
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
          <Stat label="Body" value={user?.body_type || '—'} />
          <Stat label="Vibe" value={user?.style_vibe || '—'} />
        </View>

        <Text style={styles.fieldLabel}>Name</Text>
        <TextInput style={styles.input} value={name} onChangeText={setName} placeholderTextColor={COLORS.textMuted} />

        <Text style={styles.fieldLabel}>City (climate tip)</Text>
        <TextInput style={styles.input} value={city} onChangeText={setCity} placeholder="e.g. Mumbai, Delhi, Bengaluru" placeholderTextColor={COLORS.textMuted} />

        <View style={styles.row2}>
          <View style={{ flex: 1 }}>
            <Text style={styles.fieldLabel}>Height (cm)</Text>
            <TextInput
              style={styles.input}
              value={height}
              onChangeText={setHeight}
              keyboardType="numeric"
              placeholder="165"
              placeholderTextColor={COLORS.textMuted}
            />
          </View>
          <View style={{ width: 12 }} />
          <View style={{ flex: 1 }}>
            <Text style={styles.fieldLabel}>Weight (kg)</Text>
            <TextInput
              style={styles.input}
              value={weight}
              onChangeText={setWeight}
              keyboardType="numeric"
              placeholder="60"
              placeholderTextColor={COLORS.textMuted}
            />
          </View>
        </View>

        <Text style={styles.fieldLabel}>Body frame (for clothing fit)</Text>
        <View style={styles.dietRow}>
          {BODY_TYPES.map((d) => (
            <TouchableOpacity key={d.id} style={[styles.dietChip, bodyType === d.id && styles.dietChipActive]} onPress={() => setBodyType(d.id)}>
              <Text style={[styles.dietText, bodyType === d.id && styles.dietTextActive]}>{d.label}</Text>
            </TouchableOpacity>
          ))}
        </View>

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

        <TouchableOpacity style={styles.linkRow} onPress={() => router.push('/(auth)/welcome')}>
          <Ionicons name="log-in-outline" size={20} color={COLORS.primary} />
          <Text style={styles.linkText}>Sign in / create account</Text>
        </TouchableOpacity>

        <TouchableOpacity style={styles.linkRow} onPress={() => router.push('/style-quiz')}>
          <Ionicons name="clipboard-outline" size={20} color={COLORS.primary} />
          <Text style={styles.linkText}>Retake stylist quiz</Text>
        </TouchableOpacity>

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
  row2: { flexDirection: 'row' },
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
  linkRow: { flexDirection: 'row', alignItems: 'center', gap: 10, marginTop: 18 },
  linkText: { fontFamily: FONTS.family.bodyMedium, fontSize: 14, color: COLORS.primary },
  disclaimer: { marginTop: 28, fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.textMuted, lineHeight: 18 },
});
