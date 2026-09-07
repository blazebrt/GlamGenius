/**
 * The two skin facts FOR YOU reads.
 *
 * Deliberately narrow. This is not a profile redesign and not a general
 * attribute editor: it edits exactly the two controlled facts the governed
 * chain matches against, with exactly the values the backend vocabulary
 * defines. Every choice is a button — there is no text input, because a free
 * string could never match a reviewed rule and would only look like it might.
 *
 * The backend already records a direct profile PATCH as user_declared,
 * confidence 1.0, confirmed. The phone does not restate that trust: sending
 * source or verification metadata from here would let a client assert how much
 * its own input should be believed.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { ActivityIndicator, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { COLORS, FONTS, RADIUS, SPACING } from '../src/theme/colors';
import { getAppearanceProfile, patchAppearanceProfile } from '../src/services/apiV2';
import { errorMessage } from '../src/services/api';

export const SKIN_USUAL_FEEL_KEY = 'care_skin_usual_feel';
export const SKIN_SENSITIVITY_KEY = 'care_skin_sensitivity';

/** Exactly the backend vocabulary. No other value may leave this screen. */
export const SKIN_USUAL_FEEL_OPTIONS: { value: string; label: string }[] = [
  { value: 'comfortable', label: 'Comfortable' },
  { value: 'often_dry_or_tight', label: 'Often dry or tight' },
  { value: 'often_oily', label: 'Often oily' },
  { value: 'mixed', label: 'Mixed' },
  { value: 'not_sure', label: 'Not sure' },
];

export const SKIN_SENSITIVITY_OPTIONS: { value: string; label: string }[] = [
  { value: 'rarely_reactive', label: 'Rarely reactive' },
  { value: 'sometimes_reactive', label: 'Sometimes reactive' },
  { value: 'often_reactive', label: 'Often reactive' },
  { value: 'not_sure', label: 'Not sure' },
];

export const FOR_YOU_PROFILE_COPY = {
  title: 'Skin details used by FOR YOU',
  body: 'These two answers are what your personal result is matched against. You can change them any time.',
  usualFeel: 'How does your skin usually feel?',
  sensitivity: 'How often does your skin react to products?',
  save: 'Save',
  saving: 'Saving…',
  saved: 'Saved.',
  incomplete: 'Answer both questions to save.',
  back: 'Back',
} as const;

function Question({
  prompt,
  options,
  value,
  onChange,
  testIDPrefix,
}: {
  prompt: string;
  options: { value: string; label: string }[];
  value: string | null;
  onChange: (next: string) => void;
  testIDPrefix: string;
}) {
  return (
    <View style={styles.card}>
      <Text style={styles.question}>{prompt}</Text>
      {options.map((option) => {
        const on = value === option.value;
        return (
          <TouchableOpacity
            key={option.value}
            accessibilityRole="radio"
            accessibilityState={{ selected: on, checked: on }}
            accessibilityLabel={option.label}
            testID={`${testIDPrefix}-${option.value}`}
            onPress={() => onChange(option.value)}
            style={[styles.choice, on && styles.choiceOn]}
          >
            <Text style={[styles.choiceText, on && styles.choiceTextOn]}>{option.label}</Text>
          </TouchableOpacity>
        );
      })}
    </View>
  );
}

export default function ForYouProfileScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [usualFeel, setUsualFeel] = useState<string | null>(null);
  const [sensitivity, setSensitivity] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let alive = true;
    void getAppearanceProfile()
      .then((profile) => {
        if (!alive) return;
        const find = (key: string) =>
          profile.attributes.find((attribute) => attribute.key === key)?.value;
        const feel = find(SKIN_USUAL_FEEL_KEY);
        const react = find(SKIN_SENSITIVITY_KEY);
        // Only preselect a value the vocabulary actually contains.
        if (typeof feel === 'string' && SKIN_USUAL_FEEL_OPTIONS.some((o) => o.value === feel)) {
          setUsualFeel(feel);
        }
        if (
          typeof react === 'string'
          && SKIN_SENSITIVITY_OPTIONS.some((o) => o.value === react)
        ) {
          setSensitivity(react);
        }
      })
      .catch(() => undefined)
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, []);

  const complete = useMemo(() => Boolean(usualFeel && sensitivity), [usualFeel, sensitivity]);

  const save = useCallback(async () => {
    if (!usualFeel || !sensitivity || busy) return;
    setBusy(true);
    setError(null);
    try {
      await patchAppearanceProfile([
        { key: SKIN_USUAL_FEEL_KEY, value: usualFeel },
        { key: SKIN_SENSITIVITY_KEY, value: sensitivity },
      ]);
      setSaved(true);
      router.back();
    } catch (err) {
      setError(errorMessage(err, 'We could not save that just now. Try again in a moment.'));
    } finally {
      setBusy(false);
    }
  }, [busy, router, sensitivity, usualFeel]);

  if (loading) {
    return (
      <View style={[styles.container, styles.centre, { paddingTop: insets.top + SPACING.lg }]}>
        <ActivityIndicator color={COLORS.primary} />
      </View>
    );
  }

  return (
    <ScrollView
      style={styles.container}
      contentContainerStyle={{
        paddingTop: insets.top + SPACING.lg,
        paddingBottom: insets.bottom + SPACING.xl,
        gap: SPACING.md,
      }}
    >
      <Text style={styles.title}>{FOR_YOU_PROFILE_COPY.title}</Text>
      <Text style={styles.body}>{FOR_YOU_PROFILE_COPY.body}</Text>

      <Question
        prompt={FOR_YOU_PROFILE_COPY.usualFeel}
        options={SKIN_USUAL_FEEL_OPTIONS}
        value={usualFeel}
        onChange={setUsualFeel}
        testIDPrefix="usual-feel"
      />
      <Question
        prompt={FOR_YOU_PROFILE_COPY.sensitivity}
        options={SKIN_SENSITIVITY_OPTIONS}
        value={sensitivity}
        onChange={setSensitivity}
        testIDPrefix="sensitivity"
      />

      {!complete && <Text style={styles.hint}>{FOR_YOU_PROFILE_COPY.incomplete}</Text>}
      {!!error && <Text style={styles.error}>{error}</Text>}
      {saved && <Text style={styles.saved}>{FOR_YOU_PROFILE_COPY.saved}</Text>}

      <TouchableOpacity
        accessibilityRole="button"
        accessibilityLabel={FOR_YOU_PROFILE_COPY.save}
        accessibilityState={{ disabled: !complete || busy }}
        testID="for-you-profile-save"
        onPress={save}
        disabled={!complete || busy}
        style={[styles.primaryButton, (!complete || busy) && styles.primaryDisabled]}
      >
        <Text style={styles.primaryText}>
          {busy ? FOR_YOU_PROFILE_COPY.saving : FOR_YOU_PROFILE_COPY.save}
        </Text>
      </TouchableOpacity>

      <TouchableOpacity
        accessibilityRole="button"
        accessibilityLabel={FOR_YOU_PROFILE_COPY.back}
        onPress={() => router.back()}
        style={styles.linkButton}
      >
        <Text style={styles.linkText}>{FOR_YOU_PROFILE_COPY.back}</Text>
      </TouchableOpacity>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.background, paddingHorizontal: SPACING.lg },
  centre: { alignItems: 'center', justifyContent: 'center' },
  title: { fontFamily: FONTS.family.heading, fontSize: 26, color: COLORS.textPrimary },
  body: { fontFamily: FONTS.family.body, fontSize: 14, lineHeight: 21, color: COLORS.textSecondary },
  card: {
    backgroundColor: COLORS.card, borderRadius: RADIUS.lg, padding: SPACING.lg,
    borderWidth: 1, borderColor: COLORS.border, gap: SPACING.sm,
  },
  question: { fontFamily: FONTS.family.bodySemibold, fontSize: 16, color: COLORS.textPrimary },
  choice: {
    borderWidth: 1, borderColor: COLORS.border, borderRadius: RADIUS.md,
    paddingVertical: 14, paddingHorizontal: SPACING.md, backgroundColor: COLORS.background,
  },
  choiceOn: { borderColor: COLORS.primary, backgroundColor: COLORS.card },
  choiceText: { fontFamily: FONTS.family.bodySemibold, fontSize: 15, color: COLORS.textPrimary },
  choiceTextOn: { color: COLORS.primary },
  hint: { fontFamily: FONTS.family.body, fontSize: 13, color: COLORS.textSecondary },
  error: { fontFamily: FONTS.family.body, fontSize: 13, color: COLORS.error },
  saved: { fontFamily: FONTS.family.bodySemibold, fontSize: 14, color: COLORS.success },
  primaryButton: {
    backgroundColor: COLORS.primary, borderRadius: RADIUS.md, paddingVertical: 14, alignItems: 'center',
  },
  primaryDisabled: { opacity: 0.5 },
  primaryText: { fontFamily: FONTS.family.bodySemibold, fontSize: 15, color: COLORS.white },
  linkButton: { paddingVertical: 12, alignItems: 'center' },
  linkText: { fontFamily: FONTS.family.bodySemibold, fontSize: 14, color: COLORS.primary },
});
