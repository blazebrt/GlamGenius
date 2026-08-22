import React, { useEffect, useMemo, useState } from 'react';
import { ActivityIndicator, ScrollView, StyleSheet, Switch, Text, TextInput, TouchableOpacity, View } from 'react-native';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';

import { addCalendarEvent, CalendarEventInput, getOccasionTypes, OccasionDefinition } from '../src/services/apiV2';
import { COLORS, FONTS, RADIUS, SPACING } from '../src/theme/colors';

const DEFAULT_TIME = '12:00';

export default function EventAddScreen() {
  const router = useRouter();
  const [title, setTitle] = useState('');
  const [date, setDate] = useState('');
  const [time, setTime] = useState(DEFAULT_TIME);
  const [allDay, setAllDay] = useState(false);
  const [location, setLocation] = useState('');
  const [occasions, setOccasions] = useState<OccasionDefinition[]>([]);
  const [occasionKey, setOccasionKey] = useState<string | undefined>();
  const [dressCode, setDressCode] = useState<string | undefined>();
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getOccasionTypes().then((result) => setOccasions(result.occasions)).catch(() => setError('We could not load event types right now.')).finally(() => setLoading(false));
  }, []);

  const selectedOccasion = useMemo(
    () => occasions.find((occasion) => occasion.key === occasionKey),
    [occasionKey, occasions],
  );

  const submit = async () => {
    if (busy) return;
    setError(null);
    if (!title.trim()) { setError('Add a title for this event.'); return; }
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) { setError('Use a date like 2026-09-12.'); return; }
    const chosenTime = allDay ? '00:00' : time;
    if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(chosenTime)) { setError('Use a time like 18:30.'); return; }
    const [year, month, day] = date.split('-').map(Number);
    const dateCheck = new Date(year, month - 1, day);
    const local = new Date(`${date}T${chosenTime}:00`);
    if (Number.isNaN(local.getTime()) || dateCheck.getFullYear() !== year || dateCheck.getMonth() !== month - 1 || dateCheck.getDate() !== day) {
      setError('That date or time is not valid.');
      return;
    }
    const body: CalendarEventInput = {
      title: title.trim(),
      starts_at: local.toISOString(),
      all_day: allDay,
      ...(location.trim() ? { location: location.trim() } : {}),
      ...(occasionKey ? { occasion_key: occasionKey as CalendarEventInput['occasion_key'] } : {}),
      ...(dressCode ? { dress_code_hint: dressCode } : {}),
    };
    setBusy(true);
    try {
      const result = await addCalendarEvent(body);
      router.replace({ pathname: '/event-ready', params: { eventId: result.event.id } });
    } catch (err: any) {
      setError(err?.response?.data?.detail?.message || 'We could not add that event. Please try again.');
    } finally {
      setBusy(false);
    }
  };

  if (loading) return <View style={styles.center}><ActivityIndicator color={COLORS.primary} /></View>;

  return (
    <View style={styles.container}>
      <View style={styles.top}>
        <TouchableOpacity accessibilityRole="button" accessibilityLabel="Back" onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={24} color={COLORS.textPrimary} />
        </TouchableOpacity>
        <Text style={styles.topTitle}>Add an important event</Text>
        <View style={{ width: 24 }} />
      </View>
      <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
        <Text style={styles.eyebrow}>EVENT READY</Text>
        <Text style={styles.title}>What is coming up?</Text>
        <Text style={styles.body}>Add one when you want GlamGenius to help you prepare around it.</Text>
        {!!error && <Text accessibilityLabel="Event form error" style={styles.error}>{error}</Text>}

        <Text style={styles.label}>Title</Text>
        <TextInput accessibilityLabel="Event title" value={title} onChangeText={setTitle} placeholder="Wedding, presentation…" placeholderTextColor={COLORS.textMuted} style={styles.input} />
        <Text style={styles.label}>Date</Text>
        <TextInput accessibilityLabel="Event date" value={date} onChangeText={setDate} placeholder="YYYY-MM-DD" placeholderTextColor={COLORS.textMuted} keyboardType="numbers-and-punctuation" style={styles.input} />
        <View style={styles.timeRow}>
          <View style={{ flex: 1 }}>
            <Text style={styles.label}>Time</Text>
            <TextInput accessibilityLabel="Event time" value={time} onChangeText={setTime} placeholder="HH:MM" placeholderTextColor={COLORS.textMuted} keyboardType="numbers-and-punctuation" style={styles.input} />
          </View>
          <View style={styles.switchRow}>
            <Text style={styles.label}>All day</Text>
            <Switch accessibilityLabel="All day event" value={allDay} onValueChange={setAllDay} trackColor={{ false: COLORS.border, true: COLORS.primaryLight }} thumbColor={allDay ? COLORS.primary : COLORS.textMuted} />
          </View>
        </View>
        <Text style={styles.label}>Event type (optional)</Text>
        <View style={styles.chips}>
          {occasions.map((occasion) => (
            <TouchableOpacity key={occasion.key} accessibilityRole="button" accessibilityLabel={`Event type ${occasion.label}`} onPress={() => { setOccasionKey(occasion.key); setDressCode(undefined); }} style={[styles.chip, occasionKey === occasion.key && styles.chipSelected]}>
              <Text style={[styles.chipText, occasionKey === occasion.key && styles.chipTextSelected]}>{occasion.label}</Text>
            </TouchableOpacity>
          ))}
        </View>
        {!!selectedOccasion && <>
          <Text style={styles.label}>Dress code (optional)</Text>
          <View style={styles.chips}>
            {selectedOccasion.dress_codes.map((code) => (
              <TouchableOpacity key={code} accessibilityRole="button" accessibilityLabel={`Dress code ${code}`} onPress={() => setDressCode(code)} style={[styles.chip, dressCode === code && styles.chipSelected]}>
                <Text style={[styles.chipText, dressCode === code && styles.chipTextSelected]}>{code.replace(/_/g, ' ')}</Text>
              </TouchableOpacity>
            ))}
          </View>
        </>}
        <Text style={styles.label}>Location (optional)</Text>
        <TextInput accessibilityLabel="Event location" value={location} onChangeText={setLocation} placeholder="Where it is" placeholderTextColor={COLORS.textMuted} style={styles.input} />
        <TouchableOpacity accessibilityRole="button" accessibilityLabel="Add event" disabled={busy} onPress={() => void submit()} style={[styles.primary, busy && styles.disabled]}>
          <Text style={styles.primaryText}>{busy ? 'Adding…' : 'Add event'}</Text>
        </TouchableOpacity>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.backgroundSecondary },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: COLORS.background },
  top: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingHorizontal: SPACING.lg, paddingTop: SPACING.lg },
  topTitle: { fontFamily: FONTS.family.bodySemibold, color: COLORS.textPrimary },
  content: { padding: SPACING.lg, paddingBottom: 56 },
  eyebrow: { fontFamily: FONTS.family.bodySemibold, color: COLORS.primary, fontSize: 10, letterSpacing: 1.3, marginTop: SPACING.lg },
  title: { fontFamily: FONTS.family.heading, color: COLORS.textPrimary, fontSize: 29, marginTop: 5, marginBottom: 6 },
  body: { fontFamily: FONTS.family.body, color: COLORS.textSecondary, fontSize: 13, lineHeight: 19, marginBottom: SPACING.md },
  label: { fontFamily: FONTS.family.bodySemibold, color: COLORS.textPrimary, fontSize: 12, marginTop: SPACING.md, marginBottom: 6 },
  input: { backgroundColor: COLORS.card, borderColor: COLORS.border, borderWidth: 1, borderRadius: RADIUS.md, paddingHorizontal: 13, paddingVertical: 11, color: COLORS.textPrimary, fontFamily: FONTS.family.body, fontSize: 14 },
  timeRow: { flexDirection: 'row', alignItems: 'flex-end', gap: SPACING.md },
  switchRow: { alignItems: 'center', paddingBottom: 10 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  chip: { borderRadius: RADIUS.full, borderWidth: 1, borderColor: COLORS.border, backgroundColor: COLORS.card, paddingHorizontal: 12, paddingVertical: 8 },
  chipSelected: { borderColor: COLORS.primary, backgroundColor: COLORS.primaryLight },
  chipText: { fontFamily: FONTS.family.bodyMedium, color: COLORS.textSecondary, fontSize: 12, textTransform: 'capitalize' },
  chipTextSelected: { color: COLORS.primary },
  error: { color: COLORS.error, fontFamily: FONTS.family.bodyMedium, fontSize: 12, marginBottom: 4 },
  primary: { alignItems: 'center', justifyContent: 'center', backgroundColor: COLORS.primary, borderRadius: RADIUS.full, paddingVertical: 14, marginTop: SPACING.xl },
  primaryText: { fontFamily: FONTS.family.bodySemibold, fontSize: 14, color: COLORS.white },
  disabled: { opacity: 0.6 },
});
