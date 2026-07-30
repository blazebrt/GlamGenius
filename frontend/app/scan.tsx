import React, { useEffect, useRef, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ActivityIndicator,
  ScrollView,
  Alert,
  Dimensions,
} from 'react-native';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import Animated, { FadeIn, FadeInDown } from 'react-native-reanimated';
import { CameraView, useCameraPermissions } from 'expo-camera';
import * as ImagePicker from 'expo-image-picker';
import { api } from '../src/services/api';
import { useUserStore } from '../src/store/userStore';
import { COLORS, FONTS, SPACING, RADIUS, SHADOWS } from '../src/theme/colors';

const { width: SCREEN_WIDTH } = Dimensions.get('window');
type ScanPhase = 'camera' | 'processing' | 'results';

const PROCESSING_STEPS = [
  'Reading light & framing',
  'Noting visible skin or hair cues',
  'Building colour & care ideas',
  'Mapping Indian food tips',
  'Preparing your coach plan',
];

export default function ScanScreen() {
  const router = useRouter();
  const params = useLocalSearchParams();
  const insets = useSafeAreaInsets();
  const [permission, requestPermission] = useCameraPermissions();
  const cameraRef = useRef<CameraView>(null);
  const scanType = (params.scanType as string) || 'face';
  const { userId, user, refreshSubscription } = useUserStore();

  const [phase, setPhase] = useState<ScanPhase>('camera');
  const [step, setStep] = useState(0);
  const [analysis, setAnalysis] = useState<any>(null);
  const [errorLimit, setErrorLimit] = useState(false);

  useEffect(() => {
    if (phase !== 'processing') return;
    let i = 0;
    const t = setInterval(() => {
      i = Math.min(i + 1, PROCESSING_STEPS.length - 1);
      setStep(i);
    }, 1600);
    return () => clearInterval(t);
  }, [phase]);

  const runAnalysis = async (base64: string) => {
    setPhase('processing');
    setErrorLimit(false);
    try {
      const res = await api.post('/scan/analyze', {
        user_id: userId || undefined,
        image_base64: base64,
        scan_type: scanType,
        city: user?.city,
        diet: user?.diet,
        budget_range: user?.budget_range,
      });
      setAnalysis(res.data.analysis);
      await refreshSubscription();
      setPhase('results');
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      if (err?.response?.status === 402 || detail?.code === 'SCAN_LIMIT') {
        setErrorLimit(true);
        setPhase('camera');
        Alert.alert(
          'Free checks used',
          detail?.message || 'Upgrade to Plus for unlimited checks.',
          [
            { text: 'Maybe later', style: 'cancel' },
            { text: 'Go Plus', onPress: () => router.push('/subscription') },
          ]
        );
        return;
      }
      Alert.alert('Check failed', 'Could not complete analysis. Please try again.');
      setPhase('camera');
    }
  };

  const takePhoto = async () => {
    try {
      if (!permission?.granted) {
        const r = await requestPermission();
        if (!r.granted) {
          Alert.alert('Camera needed', 'Allow camera access or upload a photo instead.');
          return;
        }
      }
      const photo = await cameraRef.current?.takePictureAsync({ base64: true, quality: 0.7 });
      if (photo?.base64) await runAnalysis(photo.base64);
    } catch {
      Alert.alert('Camera error', 'Try uploading a photo instead.');
    }
  };

  const pickImage = async () => {
    const result = await ImagePicker.launchImageLibraryAsync({
      base64: true,
      quality: 0.7,
      mediaTypes: ImagePicker.MediaTypeOptions.Images,
    });
    if (!result.canceled && result.assets[0]?.base64) {
      await runAnalysis(result.assets[0].base64);
    }
  };

  if (phase === 'processing') {
    return (
      <View style={[styles.container, styles.center, { paddingTop: insets.top }]}>
        <ActivityIndicator size="large" color={COLORS.primary} />
        <Text style={styles.processingTitle}>Building your coach plan</Text>
        <Text style={styles.processingStep}>{PROCESSING_STEPS[step]}</Text>
        <Text style={styles.disclaimerMini}>Not a diagnosis — style & wellness guidance only.</Text>
      </View>
    );
  }

  if (phase === 'results' && analysis) {
    return <ResultsView analysis={analysis} insets={insets} onClose={() => router.back()} onPlan={() => router.push('/get-advice')} />;
  }

  return (
    <View style={[styles.container, { backgroundColor: COLORS.scanBackground }]}>
      <View style={[styles.topBar, { paddingTop: insets.top + 8 }]}>
        <TouchableOpacity onPress={() => router.back()} style={styles.iconBtn}>
          <Ionicons name="close" size={24} color={COLORS.white} />
        </TouchableOpacity>
        <Text style={styles.topTitle}>
          {scanType === 'hair' ? 'Hair check' : scanType === 'hands' ? 'Hands check' : 'Skin check'}
        </Text>
        <View style={{ width: 40 }} />
      </View>

      {permission?.granted ? (
        <CameraView ref={cameraRef} style={styles.camera} facing="front">
          <View style={styles.guideRing} />
        </CameraView>
      ) : (
        <View style={[styles.camera, styles.center]}>
          <Text style={styles.permText}>Camera permission needed, or upload a photo.</Text>
          <TouchableOpacity style={styles.secondaryBtn} onPress={requestPermission}>
            <Text style={styles.secondaryBtnText}>Allow camera</Text>
          </TouchableOpacity>
        </View>
      )}

      <View style={[styles.bottomBar, { paddingBottom: insets.bottom + 20 }]}>
        {errorLimit && (
          <Text style={styles.limitText}>Free monthly checks used — upgrade for more.</Text>
        )}
        <Text style={styles.hint}>Centre yourself in good light, then capture or upload.</Text>
        <View style={styles.actions}>
          <TouchableOpacity style={styles.uploadBtn} onPress={pickImage}>
            <Ionicons name="images-outline" size={22} color={COLORS.white} />
            <Text style={styles.uploadText}>Upload</Text>
          </TouchableOpacity>
          <TouchableOpacity style={styles.shutter} onPress={takePhoto}>
            <View style={styles.shutterInner} />
          </TouchableOpacity>
          <View style={{ width: 72 }} />
        </View>
      </View>
    </View>
  );
}

function ResultsView({
  analysis,
  insets,
  onClose,
  onPlan,
}: {
  analysis: any;
  insets: any;
  onClose: () => void;
  onPlan: () => void;
}) {
  const scores = analysis.wellness_scores || {};
  const style = analysis.style || {};
  const care = analysis.care_ingredients || {};
  const nutrition = analysis.nutrition || {};
  const salon = analysis.salon_suggestions || [];
  const summary = analysis.coach_summary || {};
  const observations = analysis.observations || [];
  const daily = analysis.daily_care || {};
  const profile = analysis.profile || {};

  return (
    <View style={[styles.container, { backgroundColor: COLORS.backgroundSecondary, paddingTop: insets.top }]}>
      <View style={styles.resultsHeader}>
        <TouchableOpacity onPress={onClose}><Ionicons name="close" size={24} color={COLORS.textPrimary} /></TouchableOpacity>
        <Text style={styles.resultsTitle}>Your coach plan</Text>
        <View style={{ width: 24 }} />
      </View>

      <ScrollView contentContainerStyle={{ padding: SPACING.lg, paddingBottom: 140 }}>
        <Animated.View entering={FadeIn} style={styles.scoreCard}>
          <Text style={styles.headline}>{summary.headline || 'Your personalised plan'}</Text>
          <View style={styles.scoreRow}>
            <ScorePill label="Skin" value={scores.skin_score} />
            <ScorePill label="Hair" value={scores.hair_score} />
            <ScorePill label="Overall" value={scores.overall_score} />
          </View>
          <Text style={styles.scoreNotes}>{scores.score_notes}</Text>
          <Text style={styles.profileLine}>
            {[profile.skin_tone, profile.undertone && `${profile.undertone} undertone`, profile.skin_type_visible]
              .filter(Boolean)
              .join(' · ')}
          </Text>
        </Animated.View>

        <Section title="What I noticed">
          {observations.map((o: any, i: number) => (
            <View key={i} style={styles.obsCard}>
              <Text style={styles.obsArea}>{o.area} · {o.level}</Text>
              <Text style={styles.obsText}>{o.what_i_see}</Text>
              <Text style={styles.obsWhy}>{o.why_it_matters}</Text>
            </View>
          ))}
        </Section>

        <Section title="Colours that suit you">
          <View style={styles.colorRow}>
            {(style.best_clothing_colors || []).map((c: any, i: number) => (
              <View key={i} style={styles.colorItem}>
                <View style={[styles.swatch, { backgroundColor: c.hex_hint || COLORS.primary }]} />
                <Text style={styles.colorName}>{c.color}</Text>
                <Text style={styles.colorWhy}>{c.why}</Text>
              </View>
            ))}
          </View>
          {(style.wardrobe_ideas_india || []).map((w: any, i: number) => (
            <View key={i} style={styles.outfitCard}>
              <Text style={styles.outfitOcc}>{w.occasion}</Text>
              <Text style={styles.outfitIdea}>{w.outfit_idea}</Text>
              <Text style={styles.colorWhy}>{w.why_it_works}</Text>
            </View>
          ))}
          {!!style.metal_and_accessories && (
            <Text style={styles.metaLine}>Metals: {style.metal_and_accessories}</Text>
          )}
        </Section>

        <Section title="Daily care">
          <CareList label="Morning" items={daily.morning} />
          <CareList label="Evening" items={daily.evening} />
          <CareList label="Weekly" items={daily.weekly} />
          {!!daily.climate_note && <Text style={styles.metaLine}>{daily.climate_note}</Text>}
        </Section>

        <Section title="Ingredients to look for">
          <Text style={styles.subHead}>For skin</Text>
          {(care.for_skin || []).map((ing: any, i: number) => (
            <IngredientCard key={`s${i}`} ing={ing} />
          ))}
          <Text style={[styles.subHead, { marginTop: 12 }]}>For hair</Text>
          {(care.for_hair || []).map((ing: any, i: number) => (
            <IngredientCard key={`h${i}`} ing={ing} />
          ))}
          {(care.ingredients_to_go_easy_on || []).length > 0 && (
            <>
              <Text style={[styles.subHead, { marginTop: 12 }]}>Go easy on</Text>
              {(care.ingredients_to_go_easy_on || []).map((ing: any, i: number) => (
                <Text key={i} style={styles.obsText}>• {ing.ingredient} — {ing.why}</Text>
              ))}
            </>
          )}
          {!!care.simple_shopping_rule && (
            <Text style={styles.metaLine}>{care.simple_shopping_rule}</Text>
          )}
        </Section>

        <Section title="Eat for healthier-looking skin & hair">
          {(nutrition.ingredients || []).map((n: any, i: number) => (
            <View key={i} style={styles.obsCard}>
              <Text style={styles.obsArea}>{n.ingredient}</Text>
              <Text style={styles.obsText}>{n.why_for_skin_or_hair}</Text>
              {(n.indian_foods || []).map((f: any, j: number) => (
                <Text key={j} style={styles.foodLine}>
                  · {f.food} — {f.serving_idea} ({f.how_often})
                </Text>
              ))}
            </View>
          ))}
          {(nutrition.simple_plate_ideas || []).map((p: string, i: number) => (
            <Text key={i} style={styles.foodLine}>{p}</Text>
          ))}
          {!!nutrition.hydration && <Text style={styles.metaLine}>{nutrition.hydration}</Text>}
        </Section>

        <Section title="Salon ideas (optional)">
          <Text style={styles.sectionNote}>Suggestions only — visit a salon if you like. No booking or prices here.</Text>
          {salon.map((s: any, i: number) => (
            <View key={i} style={styles.obsCard}>
              <Text style={styles.obsArea}>{s.service} · {s.priority}</Text>
              <Text style={styles.obsText}>{s.for}</Text>
              <Text style={styles.obsWhy}>{s.why_suggest} · {s.how_often_idea}</Text>
            </View>
          ))}
        </Section>

        <Section title="This week">
          {(summary.top_3_actions_this_week || []).map((a: string, i: number) => (
            <Text key={i} style={styles.foodLine}>{i + 1}. {a}</Text>
          ))}
          {!!summary.recheck_in_days && (
            <Text style={styles.metaLine}>Recheck in about {summary.recheck_in_days} days.</Text>
          )}
        </Section>

        <Text style={styles.disclaimerBox}>
          {analysis?.meta?.disclaimer || 'General wellness and style guidance — not medical advice.'}
        </Text>
      </ScrollView>

      <View style={[styles.resultsFooter, { paddingBottom: insets.bottom + 16 }]}>
        <TouchableOpacity style={styles.primaryBtn} onPress={onPlan}>
          <Text style={styles.primaryBtnText}>Build occasion style plan</Text>
        </TouchableOpacity>
      </View>
    </View>
  );
}

function ScorePill({ label, value }: { label: string; value?: number }) {
  return (
    <View style={styles.scorePill}>
      <Text style={styles.scoreValue}>{value ?? '—'}</Text>
      <Text style={styles.scoreLabel}>{label}</Text>
    </View>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Animated.View entering={FadeInDown} style={{ marginTop: SPACING.lg }}>
      <Text style={styles.sectionTitle}>{title}</Text>
      {children}
    </Animated.View>
  );
}

function CareList({ label, items }: { label: string; items?: string[] }) {
  if (!items?.length) return null;
  return (
    <View style={{ marginBottom: 10 }}>
      <Text style={styles.subHead}>{label}</Text>
      {items.map((item, i) => (
        <Text key={i} style={styles.foodLine}>· {item}</Text>
      ))}
    </View>
  );
}

function IngredientCard({ ing }: { ing: any }) {
  return (
    <View style={styles.obsCard}>
      <Text style={styles.obsArea}>{ing.ingredient}</Text>
      <Text style={styles.obsText}>{ing.why}</Text>
      <Text style={styles.obsWhy}>
        Use: {ing.where_to_use} · Start: {ing.how_often_start}
      </Text>
      {!!ing.india_label_names && (
        <Text style={styles.metaLine}>Labels: {(ing.india_label_names || []).join(', ')}</Text>
      )}
      {!!ing.caution && <Text style={styles.caution}>Caution: {ing.caution}</Text>}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  center: { alignItems: 'center', justifyContent: 'center', padding: 24 },
  topBar: {
    position: 'absolute', top: 0, left: 0, right: 0, zIndex: 2,
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 16,
  },
  iconBtn: { width: 40, height: 40, alignItems: 'center', justifyContent: 'center' },
  topTitle: { fontFamily: FONTS.family.bodySemibold, color: COLORS.white, fontSize: 16 },
  camera: { flex: 1, marginTop: 80, marginBottom: 160, marginHorizontal: 16, borderRadius: 24, overflow: 'hidden' },
  guideRing: {
    position: 'absolute', alignSelf: 'center', top: '22%',
    width: SCREEN_WIDTH * 0.62, height: SCREEN_WIDTH * 0.72, borderRadius: SCREEN_WIDTH * 0.31,
    borderWidth: 2, borderColor: 'rgba(255,255,255,0.7)',
  },
  bottomBar: { position: 'absolute', left: 0, right: 0, bottom: 0, paddingHorizontal: 20 },
  hint: { color: 'rgba(255,255,255,0.85)', textAlign: 'center', marginBottom: 16, fontFamily: FONTS.family.body, fontSize: 13 },
  limitText: { color: '#FECACA', textAlign: 'center', marginBottom: 8, fontFamily: FONTS.family.bodyMedium, fontSize: 13 },
  actions: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  uploadBtn: { width: 72, alignItems: 'center', gap: 4 },
  uploadText: { color: COLORS.white, fontSize: 12, fontFamily: FONTS.family.bodyMedium },
  shutter: {
    width: 74, height: 74, borderRadius: 37, borderWidth: 3, borderColor: COLORS.white,
    alignItems: 'center', justifyContent: 'center',
  },
  shutterInner: { width: 60, height: 60, borderRadius: 30, backgroundColor: COLORS.white },
  permText: { color: COLORS.white, textAlign: 'center', marginBottom: 16, fontFamily: FONTS.family.body },
  secondaryBtn: { backgroundColor: COLORS.primary, paddingHorizontal: 18, paddingVertical: 12, borderRadius: RADIUS.md },
  secondaryBtnText: { color: COLORS.white, fontFamily: FONTS.family.bodySemibold },
  processingTitle: { marginTop: 20, fontFamily: FONTS.family.heading, fontSize: 24, color: COLORS.textPrimary },
  processingStep: { marginTop: 8, fontFamily: FONTS.family.body, fontSize: 14, color: COLORS.textSecondary },
  disclaimerMini: { marginTop: 24, fontSize: 12, color: COLORS.textMuted, textAlign: 'center', fontFamily: FONTS.family.body },
  resultsHeader: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: SPACING.lg, paddingVertical: 12,
  },
  resultsTitle: { fontFamily: FONTS.family.headingMedium, fontSize: 20, color: COLORS.textPrimary },
  scoreCard: {
    backgroundColor: COLORS.card, borderRadius: RADIUS.xl, padding: SPACING.lg,
    borderWidth: 1, borderColor: COLORS.border, ...SHADOWS.sm,
  },
  headline: { fontFamily: FONTS.family.heading, fontSize: 22, color: COLORS.textPrimary, marginBottom: 16 },
  scoreRow: { flexDirection: 'row', gap: 10 },
  scorePill: {
    flex: 1, backgroundColor: COLORS.primaryLight, borderRadius: RADIUS.md, paddingVertical: 12, alignItems: 'center',
  },
  scoreValue: { fontFamily: FONTS.family.bodyBold, fontSize: 22, color: COLORS.primary },
  scoreLabel: { fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.textSecondary, marginTop: 2 },
  scoreNotes: { marginTop: 12, fontFamily: FONTS.family.body, fontSize: 13, color: COLORS.textSecondary, lineHeight: 19 },
  profileLine: { marginTop: 8, fontFamily: FONTS.family.bodyMedium, fontSize: 12, color: COLORS.primary },
  sectionTitle: { fontFamily: FONTS.family.headingMedium, fontSize: 18, color: COLORS.textPrimary, marginBottom: 10 },
  sectionNote: { fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.textSecondary, marginBottom: 8 },
  obsCard: {
    backgroundColor: COLORS.card, borderRadius: RADIUS.md, padding: 14, marginBottom: 8,
    borderWidth: 1, borderColor: COLORS.border,
  },
  obsArea: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.primary, textTransform: 'capitalize' },
  obsText: { fontFamily: FONTS.family.body, fontSize: 14, color: COLORS.textPrimary, marginTop: 4, lineHeight: 20 },
  obsWhy: { fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.textSecondary, marginTop: 4 },
  colorRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 10 },
  colorItem: { width: '30%', marginBottom: 8 },
  swatch: { height: 44, borderRadius: 10, marginBottom: 6, borderWidth: 1, borderColor: COLORS.border },
  colorName: { fontFamily: FONTS.family.bodySemibold, fontSize: 12, color: COLORS.textPrimary },
  colorWhy: { fontFamily: FONTS.family.body, fontSize: 11, color: COLORS.textSecondary, marginTop: 2 },
  outfitCard: {
    backgroundColor: COLORS.card, borderRadius: RADIUS.md, padding: 12, marginTop: 8,
    borderWidth: 1, borderColor: COLORS.border,
  },
  outfitOcc: { fontFamily: FONTS.family.bodySemibold, fontSize: 12, color: COLORS.accent, textTransform: 'capitalize' },
  outfitIdea: { fontFamily: FONTS.family.bodyMedium, fontSize: 14, color: COLORS.textPrimary, marginTop: 4 },
  metaLine: { fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.textSecondary, marginTop: 8 },
  subHead: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.textPrimary, marginBottom: 6 },
  foodLine: { fontFamily: FONTS.family.body, fontSize: 13, color: COLORS.textPrimary, marginBottom: 4, lineHeight: 19 },
  caution: { fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.warning, marginTop: 6 },
  disclaimerBox: {
    marginTop: SPACING.xl, fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.textMuted,
    lineHeight: 18, textAlign: 'center',
  },
  resultsFooter: {
    position: 'absolute', left: 0, right: 0, bottom: 0, paddingHorizontal: SPACING.lg, paddingTop: 10,
    backgroundColor: COLORS.backgroundSecondary, borderTopWidth: 1, borderTopColor: COLORS.border,
  },
  primaryBtn: {
    backgroundColor: COLORS.primary, borderRadius: RADIUS.lg, paddingVertical: 16, alignItems: 'center',
  },
  primaryBtnText: { fontFamily: FONTS.family.bodySemibold, fontSize: 15, color: COLORS.white },
});
