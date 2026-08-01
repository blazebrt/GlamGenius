import React, { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';

import { ObservationReviewCard } from '../src/components/profile/ObservationReviewCard';
import {
  AppearanceProfile, ProfileObservation, confirmProfileObservation,
  editProfileObservation, getAppearanceProfile, getProfileObservations,
  rejectProfileObservation,
} from '../src/services/apiV2';
import { COLORS, FONTS, RADIUS, SPACING } from '../src/theme/colors';

const SECTION_TITLES: Record<string, string> = {
  appearance: 'Verified appearance', style: 'Style preferences', fit: 'Fit preferences',
  goals: 'Goals', lifestyle: 'Lifestyle context', constraints: 'Your constraints', wellness: 'Optional routine context',
};

export default function MyAppearanceScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [profile, setProfile] = useState<AppearanceProfile | null>(null);
  const [observations, setObservations] = useState<ProfileObservation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const load = useCallback(async () => {
    setLoading(true); setError(false);
    try {
      const [nextProfile, nextObservations] = await Promise.all([getAppearanceProfile(), getProfileObservations()]);
      setProfile(nextProfile); setObservations(nextObservations);
    } catch (err) { console.warn('appearance profile load failed', err); setError(true); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const update = (next: ProfileObservation) => {
    setObservations((rows) => rows.map((row) => row.id === next.id ? next : row));
    if (next.verification_state === 'confirmed') void getAppearanceProfile().then(setProfile);
  };
  if (loading) return <View style={styles.center}><ActivityIndicator color={COLORS.primary} /><Text style={styles.muted}>Loading My Appearance…</Text></View>;
  if (error || !profile) return <View style={styles.center}><Text style={styles.title}>Your profile is still here</Text><Text style={styles.muted}>We could not load it just now.</Text><TouchableOpacity accessibilityRole="button" accessibilityLabel="Retry My Appearance" onPress={() => void load()} style={styles.retry}><Text style={styles.retryText}>Try again</Text></TouchableOpacity></View>;

  const pending = observations.filter((row) => ['unverified', 'not_sure'].includes(row.verification_state));
  const sections = Object.entries(SECTION_TITLES);
  return <View style={[styles.container, { paddingTop: insets.top }]}>
    <View style={styles.topBar}><TouchableOpacity accessibilityRole="button" accessibilityLabel="Back" onPress={() => router.canGoBack() ? router.back() : router.replace('/(tabs)/profile')}><Ionicons name="arrow-back" size={24} color={COLORS.textPrimary} /></TouchableOpacity><Text style={styles.topTitle}>My Appearance</Text><View style={{ width: 24 }} /></View>
    <ScrollView contentContainerStyle={{ padding: SPACING.lg, paddingBottom: insets.bottom + 40 }}>
      <Text style={styles.eyebrow}>YOUR DIGITAL TWIN</Text><Text style={styles.title}>What GlamGenius knows</Text>
      <Text style={styles.subtitle}>Confirmed facts stay yours. Suggestions remain separate until you confirm them.</Text>
      <Text style={styles.sectionTitle}>Decision readiness</Text>
      {profile.readiness.map((item) => <View key={item.area} style={styles.readiness}><Ionicons name={item.ready ? 'checkmark-circle' : 'add-circle-outline'} size={21} color={item.ready ? COLORS.success : COLORS.warning} /><Text style={styles.readinessText}>{item.message}</Text></View>)}

      <View style={styles.version}><Text style={styles.versionText}>Profile version {profile.version}</Text><Text style={styles.versionText}>Weight is not required</Text></View>

      {pending.length > 0 && <><Text style={styles.sectionTitle}>Suggestions for you to review</Text>{pending.map((row) => <ObservationReviewCard key={row.id} observation={row}
        onConfirm={() => void confirmProfileObservation(row.id).then(update)}
        onReject={() => void rejectProfileObservation(row.id).then(update)}
        onNotSure={() => void editProfileObservation(row.id, row.value, 'not_sure').then(update)}
        onEdit={(value) => void editProfileObservation(row.id, Array.isArray(row.value) ? value.split(',').map((v) => v.trim()).filter(Boolean) : value).then(update)} />)}</>}

      {sections.map(([section, title]) => {
        const rows = profile.attributes.filter((row) => row.section === section && row.verification_state === 'confirmed');
        if (!rows.length) return null;
        return <View key={section}><Text style={styles.sectionTitle}>{title}</Text><View style={styles.card}>{rows.map((row) => <View key={row.key} style={styles.attribute}><View style={{ flex: 1 }}><Text style={styles.attributeLabel}>{row.label}</Text><Text style={styles.attributeValue}>{Array.isArray(row.value) ? row.value.join(', ') : String(row.value)}</Text></View><View style={styles.sourcePill}><Text style={styles.sourceText}>{sourceLabel(row.source)}</Text></View></View>)}</View></View>;
      })}
      <TouchableOpacity accessibilityRole="button" accessibilityLabel="Continue onboarding" onPress={() => router.push('/onboarding')} style={styles.secondary}><Text style={styles.secondaryText}>{profile.attributes.length ? 'Improve my profile' : 'Start my profile'}</Text></TouchableOpacity>
    </ScrollView>
  </View>;
}

const sourceLabel = (source: string) => ({ user_declared: 'You', photo_observed: 'Photo · confirmed', stylist_verified: 'Stylist verified', integration: 'Connected source' }[source] || 'Confirmed');
const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.backgroundSecondary }, center: { flex: 1, backgroundColor: COLORS.background, alignItems: 'center', justifyContent: 'center', padding: SPACING.lg }, topBar: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: SPACING.lg, paddingVertical: 12 }, topTitle: { fontFamily: FONTS.family.bodySemibold, color: COLORS.textPrimary },
  eyebrow: { fontFamily: FONTS.family.bodySemibold, fontSize: 11, color: COLORS.primary, letterSpacing: 1.4 }, title: { fontFamily: FONTS.family.heading, color: COLORS.textPrimary, fontSize: 29, marginTop: 5 }, subtitle: { fontFamily: FONTS.family.body, color: COLORS.textSecondary, fontSize: 14, lineHeight: 21, marginTop: 8 }, muted: { fontFamily: FONTS.family.body, color: COLORS.textSecondary, marginTop: 10 },
  sectionTitle: { fontFamily: FONTS.family.headingMedium, color: COLORS.textPrimary, fontSize: 19, marginTop: 24, marginBottom: 10 }, readiness: { flexDirection: 'row', alignItems: 'center', gap: 9, backgroundColor: COLORS.card, borderRadius: RADIUS.md, padding: 12, marginBottom: 8 }, readinessText: { flex: 1, fontFamily: FONTS.family.bodyMedium, color: COLORS.textPrimary, fontSize: 13 }, version: { flexDirection: 'row', justifyContent: 'space-between', marginTop: 12 }, versionText: { fontFamily: FONTS.family.body, color: COLORS.textMuted, fontSize: 11 },
  card: { backgroundColor: COLORS.card, borderRadius: RADIUS.lg, borderWidth: 1, borderColor: COLORS.border, paddingHorizontal: 14 }, attribute: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 13, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: COLORS.border }, attributeLabel: { fontFamily: FONTS.family.body, color: COLORS.textMuted, fontSize: 11 }, attributeValue: { fontFamily: FONTS.family.bodySemibold, color: COLORS.textPrimary, fontSize: 14, marginTop: 3, textTransform: 'capitalize' }, sourcePill: { backgroundColor: COLORS.primaryLight, borderRadius: RADIUS.full, paddingHorizontal: 9, paddingVertical: 5 }, sourceText: { fontFamily: FONTS.family.bodyMedium, color: COLORS.primary, fontSize: 10 },
  secondary: { borderWidth: 1, borderColor: COLORS.primary, borderRadius: RADIUS.lg, padding: 14, alignItems: 'center', marginTop: 24 }, secondaryText: { fontFamily: FONTS.family.bodySemibold, color: COLORS.primary }, retry: { backgroundColor: COLORS.primary, borderRadius: RADIUS.full, paddingHorizontal: 22, paddingVertical: 11, marginTop: 18 }, retryText: { color: COLORS.white, fontFamily: FONTS.family.bodySemibold },
});
