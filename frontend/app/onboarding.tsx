import React, { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, ScrollView, StyleSheet, Text, TextInput, TouchableOpacity, View } from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import * as ImagePicker from 'expo-image-picker';

import { GoalChooser, OnboardingRecovery, validateGoal } from '../src/components/onboarding/OnboardingPieces';
import { ObservationReviewCard } from '../src/components/profile/ObservationReviewCard';
import {
  BaselineResult, OnboardingStatus, ProfileObservation, completeOnboarding,
  confirmProfileObservation, editProfileObservation, getOnboardingStatus,
  getProfileObservations, rejectProfileObservation, runBaselineAnalysis,
  saveOnboardingStep, setConsent,
} from '../src/services/apiV2';
import { notify } from '../src/services/notify';
import { COLORS, FONTS, RADIUS, SHADOWS, SPACING } from '../src/theme/colors';

const STYLE_OPTIONS = ['Modern Indian', 'Minimal', 'Classic', 'Street style', 'Romantic', 'Fusion'];

export default function OnboardingScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [status, setStatus] = useState<OnboardingStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [goal, setGoal] = useState('');
  const [style, setStyle] = useState('');
  const [colours, setColours] = useState('');
  const [topSize, setTopSize] = useState('');
  const [bottomSize, setBottomSize] = useState('');
  const [city, setCity] = useState('');
  const [workContext, setWorkContext] = useState('');
  const [photoConsent, setPhotoConsentState] = useState(false);
  const [baseline, setBaseline] = useState<BaselineResult | null>(null);
  const [observations, setObservations] = useState<ProfileObservation[]>([]);

  const load = useCallback(async () => {
    setLoading(true); setLoadError(false);
    try {
      const next = await getOnboardingStatus();
      setStatus(next);
      const answers = next.answers || {};
      setGoal(answers.goal?.current_goal || '');
      setStyle(answers.style_preferences?.preferred_style || '');
      setColours((answers.style_preferences?.favourite_colours || []).join(', '));
      setTopSize(answers.fit_preferences?.usual_top_size || '');
      setBottomSize(answers.fit_preferences?.usual_bottom_size || '');
      setCity(answers.lifestyle?.city || '');
      setWorkContext(answers.lifestyle?.work_context || '');
      if (next.current_step === 'confirm_attributes') setObservations(await getProfileObservations());
    } catch (err) {
      console.warn('onboarding load failed', err); setLoadError(true);
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const home = () => router.replace('/(tabs)/today');
  const save = async (step: string, data: Record<string, any> = {}, skipped = false) => {
    setSaving(true);
    try {
      const next = await saveOnboardingStep(step, data, skipped);
      setStatus(next);
      if (next.current_step === 'confirm_attributes') setObservations(await getProfileObservations());
    } catch (err: any) {
      notify('Could not save', err?.response?.data?.detail?.message || 'Your progress was not lost. Please try again.');
    } finally { setSaving(false); }
  };

  const choosePhoto = async () => {
    if (!photoConsent) {
      notify('Consent needed', 'Please agree before sending a photo for analysis.'); return;
    }
    setSaving(true);
    try {
      await setConsent(true);
      const picked = await ImagePicker.launchImageLibraryAsync({ base64: true, quality: 0.7, mediaTypes: ['images'] });
      if (picked.canceled) return;
      const image = picked.assets[0]?.base64;
      if (!image) throw new Error('No image data');
      const result = await runBaselineAnalysis(image);
      setBaseline(result);
      setObservations(result.observations);
      await save('appearance_photo');
    } catch (err: any) {
      notify('Photo not analysed', err?.response?.data?.detail?.message || 'Try another photo, or skip this optional step.');
    } finally { setSaving(false); }
  };

  const updateObservation = (next: ProfileObservation) =>
    setObservations((rows) => rows.map((row) => row.id === next.id ? next : row));

  if (loading) return <View style={[styles.center, { paddingTop: insets.top }]}><ActivityIndicator color={COLORS.primary} /><Text style={styles.loadingText}>Restoring your progress…</Text></View>;
  if (loadError || !status) return <View style={[styles.center, { paddingTop: insets.top }]}><OnboardingRecovery onRetry={() => void load()} onHome={home} /></View>;

  const step = status.current_step;
  const stepNumber = Math.max(1, status.steps.indexOf(step) + 1);
  const finish = async () => {
    setSaving(true);
    try { setStatus(await completeOnboarding()); } catch { notify('Almost there', 'Please complete the first question, then try again.'); } finally { setSaving(false); }
  };

  if (status.status === 'completed') {
    const result = status.first_result || status.recommendation_preview || {};
    return <View style={[styles.center, { paddingTop: insets.top, paddingHorizontal: SPACING.lg }]}>
      <Ionicons name="sparkles" size={40} color={COLORS.primary} />
      <Text style={styles.completeTitle}>{result.headline || 'Your appearance profile is ready'}</Text>
      <Text style={styles.completeText}>{result.recommendation}</Text>
      <TouchableOpacity accessibilityRole="button" accessibilityLabel="Open My Appearance" style={styles.primary} onPress={() => router.replace('/my-appearance')}><Text style={styles.primaryText}>Open My Appearance</Text></TouchableOpacity>
      <TouchableOpacity accessibilityRole="button" accessibilityLabel="Go to Today" onPress={home}><Text style={styles.skipText}>Go to Today</Text></TouchableOpacity>
    </View>;
  }

  return (
    <View style={[styles.container, { paddingTop: insets.top + 8 }]}>
      <View style={styles.topBar}>
        <TouchableOpacity accessibilityRole="button" accessibilityLabel="Leave onboarding" onPress={home}><Ionicons name="close" size={24} color={COLORS.textPrimary} /></TouchableOpacity>
        <Text style={styles.progress}>Step {stepNumber} of {status.steps.length}</Text>
        <View style={{ width: 24 }} />
      </View>
      <ScrollView contentContainerStyle={[styles.content, { paddingBottom: insets.bottom + 32 }]} keyboardShouldPersistTaps="handled">
        {step === 'goal' && <>
          <Text style={styles.eyebrow}>LET’S START WITH NOW</Text>
          <Text style={styles.title}>What are you preparing for?</Text>
          <Text style={styles.subtitle}>One concrete goal helps us be useful immediately. You can change it later.</Text>
          <GoalChooser value={goal} onChange={setGoal} />
          <PrimaryButton label="Continue" busy={saving} onPress={() => {
            const error = validateGoal(goal);
            if (error) notify('Choose one', error);
            else void save('goal', { current_goal: goal });
          }} />
        </>}

        {step === 'style_preferences' && <>
          <Text style={styles.eyebrow}>YOUR STYLE</Text><Text style={styles.title}>What feels most like you?</Text>
          <Text style={styles.subtitle}>Choose one starting point—not a permanent label.</Text>
          <View style={styles.chips}>{STYLE_OPTIONS.map((item) => <Chip key={item} label={item} selected={style === item} onPress={() => setStyle(item)} />)}</View>
          <Field label="Favourite colours (optional)" value={colours} onChange={setColours} placeholder="Teal, ivory, rust" />
          <PrimaryButton label="Continue" busy={saving} disabled={!style} onPress={() => void save('style_preferences', { preferred_style: style, favourite_colours: splitList(colours) })} />
          <SkipButton onPress={() => void save('style_preferences', {}, true)} />
        </>}

        {step === 'fit_preferences' && <>
          <Text style={styles.eyebrow}>COMFORT & FIT</Text><Text style={styles.title}>Add sizes if you know them</Text>
          <Text style={styles.subtitle}>No weight is needed. Sizes only help us give more practical fit guidance.</Text>
          <Field label="Usual top size" value={topSize} onChange={setTopSize} placeholder="M, 40, or your usual label" />
          <Field label="Usual bottom size" value={bottomSize} onChange={setBottomSize} placeholder="M, 32, or your usual label" />
          <PrimaryButton label="Continue" busy={saving} onPress={() => void save('fit_preferences', cleanData({ usual_top_size: topSize, usual_bottom_size: bottomSize }))} />
          <SkipButton onPress={() => void save('fit_preferences', {}, true)} />
        </>}

        {step === 'lifestyle' && <>
          <Text style={styles.eyebrow}>REAL LIFE</Text><Text style={styles.title}>Where should your style work?</Text>
          <Text style={styles.subtitle}>A little context keeps recommendations practical for your routine and climate.</Text>
          <Field label="City (optional)" value={city} onChange={setCity} placeholder="Pune" />
          <Field label="Work or daily context (optional)" value={workContext} onChange={setWorkContext} placeholder="Hybrid office, college, home…" />
          <PrimaryButton label="Continue" busy={saving} onPress={() => void save('lifestyle', cleanData({ city, work_context: workContext }))} />
          <SkipButton onPress={() => void save('lifestyle', {}, true)} />
        </>}

        {step === 'appearance_photo' && <>
          <Text style={styles.eyebrow}>OPTIONAL PHOTO</Text><Text style={styles.title}>Find a colour starting point</Text>
          <Text style={styles.subtitle}>A clear photo can suggest a palette and reviewable attributes. The photo is analysed for this step and never stored.</Text>
          <TouchableOpacity accessibilityRole="checkbox" accessibilityLabel="Allow optional appearance photo analysis" accessibilityState={{ checked: photoConsent }} onPress={() => setPhotoConsentState(!photoConsent)} style={styles.consent}>
            <Ionicons name={photoConsent ? 'checkbox' : 'square-outline'} size={24} color={COLORS.primary} />
            <Text style={styles.consentText}>I agree to send this photo for AI analysis. It is not stored.</Text>
          </TouchableOpacity>
          <PrimaryButton label="Choose a photo" busy={saving} disabled={!photoConsent} onPress={() => void choosePhoto()} />
          <SkipButton label="Skip photo" onPress={() => void save('appearance_photo', {}, true)} />
        </>}

        {step === 'colour_palette' && <>
          <Text style={styles.eyebrow}>YOUR COLOUR START</Text><Text style={styles.title}>{baseline?.status === 'low_quality' ? 'No guessing from this photo' : 'Colours to explore'}</Text>
          <Text style={styles.subtitle}>{baseline?.message || 'Your optional photo was processed without storing it. You can review its suggestions next.'}</Text>
          {baseline?.guidance?.map((line) => <Text key={line} style={styles.guidance}>• {line}</Text>)}
          <View style={styles.palette}>{baseline?.colour_palette.map((colour) => <View key={colour.name} style={styles.paletteCard}><View style={[styles.swatch, { backgroundColor: colour.hex || COLORS.primary }]} /><Text style={styles.paletteName}>{colour.name}</Text><Text style={styles.paletteWhy}>{colour.why}</Text></View>)}</View>
          <PrimaryButton label="Review suggestions" busy={saving} onPress={() => void save('colour_palette')} />
          <SkipButton label="Continue without palette" onPress={() => void save('colour_palette', {}, true)} />
        </>}

        {step === 'confirm_attributes' && <>
          <Text style={styles.eyebrow}>YOU DECIDE</Text><Text style={styles.title}>Review every suggestion</Text>
          <Text style={styles.subtitle}>These are observations, not facts. Confirm, edit, reject, or choose not sure.</Text>
          {observations.filter((row) => row.verification_state !== 'rejected').map((row) => <ObservationReviewCard key={row.id} observation={row} busy={saving}
            onConfirm={() => void confirmProfileObservation(row.id).then(updateObservation)}
            onReject={() => void rejectProfileObservation(row.id).then(updateObservation)}
            onNotSure={() => void editProfileObservation(row.id, row.value, 'not_sure').then(updateObservation)}
            onEdit={(value) => void editProfileObservation(row.id, Array.isArray(row.value) ? splitList(value) : value).then(updateObservation)} />)}
          {!observations.length && <Text style={styles.empty}>No suggestions to review. That is completely fine.</Text>}
          <PrimaryButton label="Continue" busy={saving} onPress={() => void save('confirm_attributes')} />
          <SkipButton onPress={() => void save('confirm_attributes', {}, true)} />
        </>}

        {step === 'preview' && <>
          <Text style={styles.eyebrow}>FIRST RESULT</Text><Text style={styles.title}>Your useful starting point is ready</Text>
          <Text style={styles.subtitle}>We’ll use your goal and confirmed preferences, and clearly show where adding context would improve an answer.</Text>
          <View style={styles.resultCard}><Ionicons name="sparkles-outline" size={26} color={COLORS.primary} /><Text style={styles.resultTitle}>Start with one repeatable look</Text><Text style={styles.resultText}>Choose a comfortable base for {goal || 'your current goal'}, then vary one colour or accessory at a time.</Text></View>
          <PrimaryButton label="Finish onboarding" busy={saving} onPress={() => void finish()} />
        </>}
      </ScrollView>
    </View>
  );
}

const splitList = (value: string) => value.split(',').map((item) => item.trim()).filter(Boolean);
const cleanData = (data: Record<string, string>) => Object.fromEntries(Object.entries(data).filter(([, value]) => value.trim()));

function PrimaryButton({ label, onPress, busy, disabled = false }: { label: string; onPress: () => void; busy: boolean; disabled?: boolean }) {
  return <TouchableOpacity accessibilityRole="button" accessibilityLabel={label} accessibilityState={{ disabled: busy || disabled }} disabled={busy || disabled} onPress={onPress} style={[styles.primary, (busy || disabled) && styles.disabled]}>{busy ? <ActivityIndicator color={COLORS.white} /> : <Text style={styles.primaryText}>{label}</Text>}</TouchableOpacity>;
}
function SkipButton({ onPress, label = 'Skip for now' }: { onPress: () => void; label?: string }) { return <TouchableOpacity accessibilityRole="button" accessibilityLabel={label} onPress={onPress}><Text style={styles.skipText}>{label}</Text></TouchableOpacity>; }
function Chip({ label, selected, onPress }: { label: string; selected: boolean; onPress: () => void }) { return <TouchableOpacity accessibilityRole="radio" accessibilityLabel={label} accessibilityState={{ selected }} onPress={onPress} style={[styles.chip, selected && styles.chipSelected]}><Text style={[styles.chipText, selected && styles.chipTextSelected]}>{label}</Text></TouchableOpacity>; }
function Field({ label, value, onChange, placeholder }: { label: string; value: string; onChange: (v: string) => void; placeholder: string }) { return <View style={{ marginTop: 14 }}><Text style={styles.fieldLabel}>{label}</Text><TextInput accessibilityLabel={label} style={styles.input} value={value} onChangeText={onChange} placeholder={placeholder} placeholderTextColor={COLORS.textMuted} /></View>; }

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.background }, center: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: COLORS.background }, loadingText: { marginTop: 12, color: COLORS.textSecondary, fontFamily: FONTS.family.body },
  topBar: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingHorizontal: SPACING.lg, paddingVertical: 10 }, progress: { fontFamily: FONTS.family.bodySemibold, fontSize: 12, color: COLORS.textMuted },
  content: { padding: SPACING.lg }, eyebrow: { fontFamily: FONTS.family.bodySemibold, fontSize: 11, color: COLORS.primary, letterSpacing: 1.5 }, title: { fontFamily: FONTS.family.heading, fontSize: 30, lineHeight: 37, color: COLORS.textPrimary, marginTop: 8 }, subtitle: { fontFamily: FONTS.family.body, fontSize: 14, lineHeight: 21, color: COLORS.textSecondary, marginTop: 8, marginBottom: 20 },
  primary: { backgroundColor: COLORS.primary, borderRadius: RADIUS.lg, paddingVertical: 15, alignItems: 'center', marginTop: 20, ...SHADOWS.sm }, disabled: { opacity: 0.45 }, primaryText: { color: COLORS.white, fontFamily: FONTS.family.bodySemibold, fontSize: 15 }, skipText: { textAlign: 'center', color: COLORS.textSecondary, fontFamily: FONTS.family.bodyMedium, marginTop: 16 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 9 }, chip: { borderWidth: 1, borderColor: COLORS.border, backgroundColor: COLORS.card, borderRadius: RADIUS.full, paddingHorizontal: 14, paddingVertical: 10 }, chipSelected: { borderColor: COLORS.primary, backgroundColor: COLORS.primaryLight }, chipText: { color: COLORS.textSecondary, fontFamily: FONTS.family.bodyMedium }, chipTextSelected: { color: COLORS.primary, fontFamily: FONTS.family.bodySemibold },
  fieldLabel: { fontFamily: FONTS.family.bodySemibold, color: COLORS.textPrimary, fontSize: 13, marginBottom: 6 }, input: { borderWidth: 1, borderColor: COLORS.border, backgroundColor: COLORS.card, borderRadius: RADIUS.md, paddingHorizontal: 14, paddingVertical: 13, color: COLORS.textPrimary, fontFamily: FONTS.family.body },
  consent: { flexDirection: 'row', gap: 10, alignItems: 'flex-start', backgroundColor: COLORS.primaryLight, padding: 14, borderRadius: RADIUS.md }, consentText: { flex: 1, color: COLORS.textPrimary, fontFamily: FONTS.family.body, fontSize: 13, lineHeight: 19 },
  guidance: { color: COLORS.textSecondary, fontFamily: FONTS.family.body, fontSize: 13, marginBottom: 6 }, palette: { gap: 10 }, paletteCard: { backgroundColor: COLORS.card, borderWidth: 1, borderColor: COLORS.border, borderRadius: RADIUS.md, padding: 12 }, swatch: { width: 28, height: 28, borderRadius: 14 }, paletteName: { fontFamily: FONTS.family.bodySemibold, color: COLORS.textPrimary, marginTop: 8, textTransform: 'capitalize' }, paletteWhy: { fontFamily: FONTS.family.body, color: COLORS.textSecondary, fontSize: 12, marginTop: 4 },
  empty: { fontFamily: FONTS.family.body, color: COLORS.textMuted, textAlign: 'center', paddingVertical: 28 }, resultCard: { backgroundColor: COLORS.card, padding: 20, borderRadius: RADIUS.xl, borderWidth: 1, borderColor: COLORS.border }, resultTitle: { fontFamily: FONTS.family.headingMedium, fontSize: 20, color: COLORS.textPrimary, marginTop: 10 }, resultText: { fontFamily: FONTS.family.body, fontSize: 14, lineHeight: 21, color: COLORS.textSecondary, marginTop: 8 },
  completeTitle: { fontFamily: FONTS.family.heading, fontSize: 28, color: COLORS.textPrimary, textAlign: 'center', marginTop: 14 }, completeText: { fontFamily: FONTS.family.body, fontSize: 14, lineHeight: 21, color: COLORS.textSecondary, textAlign: 'center', marginTop: 10 },
});
