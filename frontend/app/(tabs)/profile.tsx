import React, { useEffect, useState } from 'react';
import { Alert, Platform, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { useUserStore } from '../../src/store/userStore';
import { cancelPrivacyAccountDeletion, getPrivacyAccountDeletionStatus, requestPrivacyAccountDeletion, type AccountDeletion } from '../../src/services/apiV2';
import { COLORS, FONTS, RADIUS, SHADOWS, SPACING } from '../../src/theme/colors';

export default function ProfileScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { user, fetchUser, logout } = useUserStore();
  const [deletionStatus, setDeletionStatus] = useState<AccountDeletion | null>(null);

  useEffect(() => {
    void fetchUser();
    void getPrivacyAccountDeletionStatus().then(setDeletionStatus).catch(() => setDeletionStatus(null));
  }, [fetchUser]);

  const requestDeletion = () => {
    const confirm = () => requestPrivacyAccountDeletion().then(setDeletionStatus).catch(() => undefined);
    if (Platform.OS === 'web') { if (window.confirm('Delete this account and its data?')) confirm(); return; }
    Alert.alert('Delete account', 'Delete this account and its data?', [{ text: 'Cancel', style: 'cancel' }, { text: 'Delete', style: 'destructive', onPress: confirm }]);
  };

  return <View style={[styles.container, { paddingTop: insets.top }]}>
    <ScrollView contentContainerStyle={{ padding: SPACING.lg, paddingBottom: insets.bottom + 56 }}>
      <Text style={styles.eyebrow}>ACCOUNT</Text>
      <Text style={styles.title}>{user?.name || 'Your account'}</Text>
      <Text style={styles.subtitle}>Account, privacy and notification controls.</Text>

      {deletionStatus && <View style={styles.deletionCard}>
        <Text style={styles.deletionTitle}>Account deletion pending</Text>
        <Text style={styles.deletionText}>Status: {deletionStatus.state}.</Text>
        {deletionStatus.state === 'requested' && <TouchableOpacity accessibilityRole="button" accessibilityLabel="Cancel account deletion" onPress={() => void cancelPrivacyAccountDeletion().then(() => setDeletionStatus(null))} style={styles.cancelButton}><Text style={styles.cancelText}>Cancel deletion</Text></TouchableOpacity>}
      </View>}

      <AccountRow icon="bookmark-outline" title="Memory" description="What GlamGenius remembers and how to remove it." onPress={() => router.push('/memory')} />
      <AccountRow icon="notifications-outline" title="Notifications" description="Optional reminders and device settings." onPress={() => router.push('/notifications')} />
      <AccountRow icon="log-in-outline" title="Sign in or create account" description="Keep product decisions available on this account." onPress={() => router.push('/(auth)/welcome')} />
      <AccountRow icon="log-out-outline" title="Sign out" description="Return to the scanner without an account." onPress={() => void logout().then(() => router.replace('/scan-product'))} />
      {!deletionStatus && <TouchableOpacity accessibilityRole="button" accessibilityLabel="Delete account" onPress={requestDeletion} style={styles.deleteRow}><Ionicons name="trash-outline" size={20} color={COLORS.error} /><Text style={styles.deleteText}>Delete account</Text></TouchableOpacity>}
      <Text style={styles.disclaimer}>Product facts and evidence, not medical advice.</Text>
    </ScrollView>
  </View>;
}

function AccountRow({ icon, title, description, onPress }: { icon: React.ComponentProps<typeof Ionicons>['name']; title: string; description: string; onPress: () => void }) {
  return <TouchableOpacity accessibilityRole="button" accessibilityLabel={`Open ${title}`} onPress={onPress} style={styles.row}>
    <Ionicons name={icon} size={24} color={COLORS.primary} /><View style={{ flex: 1 }}><Text style={styles.rowTitle}>{title}</Text><Text style={styles.rowText}>{description}</Text></View><Ionicons name="chevron-forward" size={18} color={COLORS.textMuted} />
  </TouchableOpacity>;
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.backgroundSecondary }, eyebrow: { color: COLORS.primary, fontFamily: FONTS.family.bodySemibold, fontSize: 11, letterSpacing: 1.4 }, title: { color: COLORS.textPrimary, fontFamily: FONTS.family.heading, fontSize: 28, marginTop: 4 }, subtitle: { color: COLORS.textSecondary, fontFamily: FONTS.family.body, fontSize: 13, lineHeight: 19, marginTop: 6, marginBottom: 12 },
  row: { alignItems: 'center', backgroundColor: COLORS.card, borderColor: COLORS.border, borderRadius: RADIUS.lg, borderWidth: 1, flexDirection: 'row', gap: 12, marginTop: 12, padding: 14 }, rowTitle: { color: COLORS.textPrimary, fontFamily: FONTS.family.bodySemibold, fontSize: 15 }, rowText: { color: COLORS.textSecondary, fontFamily: FONTS.family.body, fontSize: 11, lineHeight: 16, marginTop: 3 },
  deletionCard: { backgroundColor: COLORS.error, borderRadius: RADIUS.lg, marginBottom: 12, padding: 16, ...SHADOWS.md }, deletionTitle: { color: COLORS.white, fontFamily: FONTS.family.headingMedium, fontSize: 18 }, deletionText: { color: COLORS.white, fontFamily: FONTS.family.body, fontSize: 13, marginTop: 4 }, cancelButton: { alignSelf: 'flex-start', backgroundColor: COLORS.white, borderRadius: RADIUS.full, marginTop: 12, paddingHorizontal: 12, paddingVertical: 8 }, cancelText: { color: COLORS.error, fontFamily: FONTS.family.bodySemibold },
  deleteRow: { alignItems: 'center', flexDirection: 'row', gap: 10, marginTop: 24 }, deleteText: { color: COLORS.error, fontFamily: FONTS.family.bodyMedium, fontSize: 14 }, disclaimer: { color: COLORS.textMuted, fontFamily: FONTS.family.body, fontSize: 12, lineHeight: 18, marginTop: 28 },
});
