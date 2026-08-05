/**
 * Support, with the account context already attached.
 *
 * Nobody should have to explain their own account back to us. The server
 * snapshots the account's state onto the case when it is opened, so a
 * "why was I charged?" question arrives with the answer beside it.
 *
 * Anything mentioning a double charge or a refund is marked urgent by the
 * server — money problems get looked at first.
 */
import React, { useState } from 'react';
import { ScrollView, StyleSheet, Text, TextInput, TouchableOpacity, View } from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';

import { ErrorRecovery, PrimaryButton } from '../src/components/common/FormPieces';
import { openSupportCase } from '../src/services/apiV2';
import { COLORS, FONTS, RADIUS, SPACING } from '../src/theme/colors';

const CATEGORIES = [
  { key: 'account', label: 'My account' },
  { key: 'bug', label: 'Something is broken' },
  { key: 'content', label: 'Something it said' },
  { key: 'other', label: 'Something else' },
] as const;

export default function SupportScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [category, setCategory] = useState<(typeof CATEGORIES)[number]['key']>('account');
  const [subject, setSubject] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const send = async () => {
    if (!subject.trim() || !message.trim()) {
      setError('Tell us what it is about and what happened.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await openSupportCase({ category, subject: subject.trim(), message: message.trim() });
      setSent(result.message);
      setSubject('');
      setMessage('');
    } catch (err) {
      console.warn('support failed', err);
      setError('We could not send that. Your message is still here — try again.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <ScrollView
        contentContainerStyle={{ padding: SPACING.lg, paddingBottom: insets.bottom + 40 }}
        keyboardShouldPersistTaps="handled"
      >
        <View style={styles.header}>
          <TouchableOpacity accessibilityRole="button" accessibilityLabel="Go back" onPress={() => router.back()}>
            <Ionicons name="chevron-back" size={24} color={COLORS.textPrimary} />
          </TouchableOpacity>
          <View>
            <Text style={styles.eyebrow}>HELP</Text>
            <Text style={styles.title}>Get in touch</Text>
          </View>
        </View>

        <Text style={styles.body}>
          Your plan and payment history come through with this, so you do not have to explain
          them.
        </Text>

        {!!sent && (
          <View style={styles.sent} accessibilityLiveRegion="polite" accessibilityLabel="Message sent">
            <Ionicons name="checkmark-circle-outline" size={19} color={COLORS.primary} />
            <Text style={styles.bannerText}>{sent}</Text>
          </View>
        )}
        {!!error && <ErrorRecovery message={error} onRetry={() => setError(null)} />}

        <Text style={styles.label}>What is it about?</Text>
        <View style={styles.chips} accessibilityRole="radiogroup">
          {CATEGORIES.map((row) => (
            <TouchableOpacity
              key={row.key}
              accessibilityRole="radio"
              accessibilityState={{ selected: category === row.key }}
              accessibilityLabel={row.label}
              onPress={() => setCategory(row.key)}
              style={[styles.chip, category === row.key && styles.chipSelected]}
            >
              <Text style={[styles.chipText, category === row.key && styles.chipTextSelected]}>
                {row.label}
              </Text>
            </TouchableOpacity>
          ))}
        </View>

        <Text style={styles.label}>In one line</Text>
        <TextInput
          accessibilityLabel="Subject"
          value={subject}
          onChangeText={setSubject}
          placeholder="Charged twice for Plus"
          placeholderTextColor={COLORS.textMuted}
          style={styles.input}
          maxLength={160}
        />

        <Text style={styles.label}>What happened?</Text>
        <TextInput
          accessibilityLabel="Your message"
          value={message}
          onChangeText={setMessage}
          placeholder="Tell us as much or as little as you like."
          placeholderTextColor={COLORS.textMuted}
          multiline
          style={[styles.input, styles.multiline]}
          maxLength={4000}
        />

        <PrimaryButton label="Send" onPress={() => void send()} busy={busy} />
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.backgroundSecondary },
  header: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 8 },
  eyebrow: { fontFamily: FONTS.family.bodySemibold, color: COLORS.primary, fontSize: 10, letterSpacing: 1.4 },
  title: { fontFamily: FONTS.family.heading, color: COLORS.textPrimary, fontSize: 29, marginTop: 2 },
  body: { fontFamily: FONTS.family.body, color: COLORS.textSecondary, fontSize: 13, lineHeight: 19, marginTop: 8 },
  label: { fontFamily: FONTS.family.bodySemibold, color: COLORS.textPrimary, fontSize: 12, marginTop: 20 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: 9 },
  chip: { paddingHorizontal: 13, paddingVertical: 9, borderRadius: RADIUS.full, borderWidth: 1, borderColor: COLORS.border },
  chipSelected: { backgroundColor: COLORS.primary, borderColor: COLORS.primary },
  chipText: { fontFamily: FONTS.family.bodyMedium, color: COLORS.textSecondary, fontSize: 12 },
  chipTextSelected: { color: COLORS.white },
  input: { borderWidth: 1, borderColor: COLORS.border, borderRadius: RADIUS.md, padding: 12, marginTop: 9, fontFamily: FONTS.family.body, color: COLORS.textPrimary, fontSize: 13, backgroundColor: COLORS.card },
  multiline: { minHeight: 120, textAlignVertical: 'top' },
  sent: { flexDirection: 'row', gap: 9, alignItems: 'flex-start', backgroundColor: COLORS.primaryLight, borderRadius: RADIUS.lg, borderWidth: 1, borderColor: COLORS.primaryMuted, padding: 13, marginTop: 14 },
  bannerText: { flex: 1, fontFamily: FONTS.family.body, color: COLORS.textPrimary, fontSize: 12, lineHeight: 18 },
});
