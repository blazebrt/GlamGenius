import React, { useEffect, useState } from 'react';
import { Alert, Platform, ScrollView, StyleSheet, Switch, Text, TouchableOpacity, View } from 'react-native';
import { useRouter } from 'expo-router';
import * as Notifications from 'expo-notifications';
import Constants from 'expo-constants';
import { COLORS, FONTS, SPACING } from '../src/theme/colors';
import { getNotificationPreferences, patchNotificationPreferences, registerNotificationDevice, unregisterNotificationDevice, NotificationPreferences } from '../src/services/apiV2';

export default function NotificationsScreen() {
  const router = useRouter();
  const [preferences, setPreferences] = useState<NotificationPreferences | null>(null);
  const [busy, setBusy] = useState(false);
  const [deviceKey] = useState(() => Constants.deviceId || 'expo-device');
  useEffect(() => { void getNotificationPreferences().then((result) => setPreferences(result.preferences)).catch(() => undefined); }, []);

  const update = async (body: Parameters<typeof patchNotificationPreferences>[0]) => {
    try { const result = await patchNotificationPreferences(body); setPreferences(result.preferences); }
    catch { Alert.alert('Could not save', 'Notifications remain unchanged.'); }
  };

  const enableNative = async () => {
    if (Platform.OS === 'web') { Alert.alert('Notifications unavailable', 'Push notifications are not supported on the web.'); return; }
    setBusy(true);
    try {
      const current = await Notifications.getPermissionsAsync();
      const permission = current.status === 'granted' ? current : await Notifications.requestPermissionsAsync();
      if (permission.status !== 'granted') { Alert.alert('Notifications are off', 'You can keep using GlamGenius normally.'); return; }
      const token = await Notifications.getExpoPushTokenAsync();
      await registerNotificationDevice({ device_key: deviceKey, platform: Platform.OS === 'ios' ? 'ios' : 'android', expo_push_token: token.data });
      setPreferences((value) => value ? { ...value, native_push_enabled: true } : value);
    } catch { Alert.alert('Notifications are off', 'We could not register this device. Nothing was enabled.'); }
    finally { setBusy(false); }
  };

  const disableNative = async () => {
    setBusy(true);
    try { await unregisterNotificationDevice(deviceKey); await update({ native_push_enabled: false }); }
    catch { await update({ native_push_enabled: false }); }
    finally { setBusy(false); }
  };

  return <View style={styles.container}><ScrollView contentContainerStyle={{ padding: SPACING.lg, paddingBottom: 80 }}>
    <TouchableOpacity accessibilityRole="button" accessibilityLabel="Back to You" onPress={() => router.back()}><Text style={styles.back}>‹ You</Text></TouchableOpacity>
    <Text style={styles.eyebrow}>YOUR SETTINGS</Text><Text style={styles.title}>Notifications</Text>
    <Text style={styles.body}>Choose if and when GlamGenius may gently remind you. Notifications are never required to use the app.</Text>
    <Row label="Daily appearance notification" value={Boolean(preferences?.enabled)} onValueChange={(value) => void update({ enabled: value })} />
    <Row label="Native push on this device" value={Boolean(preferences?.native_push_enabled)} disabled={busy || !preferences} onValueChange={(value) => void (value ? enableNative() : disableNative())} />
    {Platform.OS === 'web' && <Text style={styles.note}>Push notifications are unavailable on the web.</Text>}
    <Text style={styles.section}>Preferred local hour</Text>
    <View style={styles.hours}>{[7, 8, 9, 18, 19, 20].map((hour) => <TouchableOpacity key={hour} accessibilityRole="button" accessibilityLabel={`Preferred time ${hour}:00`} onPress={() => void update({ preferred_hour: hour })} style={[styles.hour, preferences?.preferred_hour === hour && styles.hourActive]}><Text style={styles.hourText}>{String(hour).padStart(2, '0')}:00</Text></TouchableOpacity>)}</View>
    <Text style={styles.section}>What can notify me</Text>
    {preferences && Object.entries(preferences.modules).map(([module, enabled]) => <Row key={module} label={module === 'skincare' ? 'Skin Care' : module.charAt(0).toUpperCase() + module.slice(1)} value={enabled} onValueChange={(value) => void update({ modules: { [module]: value } })} />)}
    <Text style={styles.section}>Recent status</Text><Text style={styles.body}>Sent and not-sent decisions appear here without claiming device delivery.</Text>
  </ScrollView></View>;
}

function Row({ label, value, onValueChange, disabled }: { label: string; value: boolean; onValueChange: (value: boolean) => void; disabled?: boolean }) {
  return <View style={styles.row}><Text style={styles.rowLabel}>{label}</Text><Switch accessibilityRole="switch" accessibilityLabel={label} value={value} onValueChange={onValueChange} disabled={disabled} trackColor={{ false: COLORS.border, true: COLORS.primary }} /> </View>;
}

const styles = StyleSheet.create({ container: { flex: 1, backgroundColor: COLORS.backgroundSecondary }, back: { color: COLORS.primary, fontFamily: FONTS.family.bodySemibold, fontSize: 14, marginBottom: SPACING.lg }, eyebrow: { color: COLORS.primary, fontFamily: FONTS.family.bodySemibold, fontSize: 10, letterSpacing: 1.4 }, title: { color: COLORS.textPrimary, fontFamily: FONTS.family.heading, fontSize: 30, marginTop: 4 }, body: { color: COLORS.textSecondary, fontFamily: FONTS.family.body, fontSize: 13, lineHeight: 19, marginTop: 6 }, row: { alignItems: 'center', borderBottomColor: COLORS.border, borderBottomWidth: 1, flexDirection: 'row', justifyContent: 'space-between', minHeight: 56 }, rowLabel: { color: COLORS.textPrimary, flex: 1, fontFamily: FONTS.family.body, fontSize: 14 }, section: { color: COLORS.textPrimary, fontFamily: FONTS.family.headingMedium, fontSize: 18, marginTop: SPACING.xl, marginBottom: SPACING.sm }, hours: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 }, hour: { borderColor: COLORS.border, borderRadius: 10, borderWidth: 1, paddingHorizontal: 14, paddingVertical: 10 }, hourActive: { backgroundColor: COLORS.primary, borderColor: COLORS.primary }, hourText: { color: COLORS.textPrimary, fontFamily: FONTS.family.bodySemibold, fontSize: 12 }, note: { color: COLORS.textMuted, fontFamily: FONTS.family.body, fontSize: 12, marginTop: SPACING.sm } });
