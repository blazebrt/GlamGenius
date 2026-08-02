import React, { useEffect, useRef, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ActivityIndicator,
  ScrollView,
  Dimensions,
  Platform,
  TextInput,
} from 'react-native';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import Animated, { FadeIn, FadeInDown } from 'react-native-reanimated';
import { CameraView, useCameraPermissions } from 'expo-camera';
import * as ImagePicker from 'expo-image-picker';
import { api, errorMessage, isRateLimited } from '../src/services/api';
import { notify } from '../src/services/notify';
import { useUserStore } from '../src/store/userStore';
import { usePlanStore } from '../src/store/planStore';
import { useConfigStore } from '../src/store/configStore';
import { classifyFailure, Failure } from '../src/services/failure';
import { getConsent, setConsent } from '../src/services/apiV2';
import {
  AnalysisFailedState,
  BetaFeatureUnavailableState,
  LowQualityImageState,
  PhotoAnalysisConsentControl,
  ProviderUnavailableState,
} from '../src/components/TrustStates';
import { COLORS, FONTS, SPACING, RADIUS, SHADOWS } from '../src/theme/colors';

const { width: SCREEN_WIDTH } = Dimensions.get('window');
// 'failed' is a first-class phase. A failure renders its own screen rather than
// a toast over a half-built result — the app must never show a personalised
// result after a failure.
type ScanPhase = 'camera' | 'processing' | 'results' | 'failed';
const IS_WEB = Platform.OS === 'web';

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
  const setLatestScan = usePlanStore((s) => s.setLatestScan);

  const [phase, setPhase] = useState<ScanPhase>('camera');
  const [step, setStep] = useState(0);
  const [analysis, setAnalysis] = useState<any>(null);
  const [preview, setPreview] = useState<any>(null);
  const [errorLimit, setErrorLimit] = useState(false);
  const [cameraError, setCameraError] = useState(false);
  const [previewInvite, setPreviewInvite] = useState('');
  const [failure, setFailure] = useState<Failure | null>(null);
  const [photoConsent, setPhotoConsentState] = useState(false);
  const [consentSaving, setConsentSaving] = useState(false);
  // Kept so "Try again" re-sends the same photo instead of making the user
  // take a new one after a failure that was never their fault.
  const [lastImage, setLastImage] = useState<string | null>(null);
  const billingAvailable = useConfigStore((s) => s.billingAvailable);
  const betaMessage = useConfigStore((s) => s.betaMessage);

  // Signed-out visitors get a free teaser instead of the full check.
  const isPreviewMode = !userId;

  useEffect(() => {
    if (!userId) return;
    let active = true;
    getConsent()
      .then((summary) => {
        if (active) setPhotoConsentState(summary.photo_analysis.granted);
      })
      .catch(() => {
        if (active) setPhotoConsentState(false);
      });
    return () => {
      active = false;
    };
  }, [userId]);

  useEffect(() => {
    if (phase !== 'processing') return;
    let i = 0;
    const t = setInterval(() => {
      i = Math.min(i + 1, PROCESSING_STEPS.length - 1);
      setStep(i);
    }, 1600);
    return () => clearInterval(t);
  }, [phase]);

  const runPreview = async (base64: string) => {
    if (!photoConsent) {
      notify('Consent needed', 'Please agree before sending a photo for analysis.');
      setPhase('camera');
      return;
    }
    if (!previewInvite.trim()) {
      notify(
        'Invite required',
        'GlamGenius is invite-only. Enter your invite code to try a preview.',
      );
      setPhase('camera');
      return;
    }
    setPhase('processing');
    setErrorLimit(false);
    setFailure(null);
    setLastImage(base64);
    try {
      const res = await api.post('/scan/preview', {
        image_base64: base64,
        scan_type: scanType,
        invite_code: previewInvite.trim(),
        photo_analysis_consent: true,
      });
      setPreview(res.data);
      setPhase('results');
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      if (err?.response?.status === 429) {
        notify(
          'Free previews used',
          typeof detail === 'object' ? detail?.message : 'Please create an account to continue.',
        );
        setErrorLimit(true);
        setPhase('camera');
        return;
      }
      if (err?.response?.status === 400) {
        notify(
          'Invite needed',
          typeof detail === 'object' ? (detail?.message || 'Check your invite code.') : String(detail || 'Check your invite code.'),
        );
        setPhase('camera');
        return;
      }
      // Anything else — including the analysis genuinely failing — gets the
      // full explanation, not a one-line toast.
      setFailure(classifyFailure(err));
      setPhase('failed');
    }
  };

  const runAnalysis = async (base64: string) => {
    if (isPreviewMode) {
      await runPreview(base64);
      return;
    }
    if (!photoConsent) {
      notify('Consent needed', 'Please agree before sending a photo for analysis.');
      setPhase('camera');
      return;
    }
    setPhase('processing');
    setErrorLimit(false);
    setFailure(null);
    setLastImage(base64);
    try {
      // No user_id — the server identifies us from the login token.
      const res = await api.post('/scan/analyze', {
        image_base64: base64,
        scan_type: scanType,
        city: user?.city,
        diet: user?.diet,
        budget_range: user?.budget_range,
        height_cm: user?.height_cm,
        weight_kg: user?.weight_kg,
        body_type: user?.body_type,
        style_vibe: user?.style_vibe,
      });
      setAnalysis(res.data.analysis);
      setLatestScan(res.data.analysis);
      await refreshSubscription();
      setPhase('results');
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      if (isRateLimited(err)) {
        // A short-term speed limit, not the monthly allowance. Upgrading
        // would not help, so don't offer it.
        setPhase('camera');
        notify('Slow down a moment', errorMessage(err, 'Please try again in a little while.'));
        return;
      }
      if (err?.response?.status === 402 || detail?.code === 'SCAN_LIMIT') {
        setErrorLimit(true);
        setPhase('camera');
        // No upgrade offer while billing is off — the old "Go Plus" button led
        // to a paywall that could only ever refuse the payment.
        notify(
          'Monthly checks used',
          detail?.message || 'Your allowance resets next month.',
        );
        return;
      }
      const classified = classifyFailure(err);
      if (classified.kind === 'consent_required') {
        setPhotoConsentState(false);
        setPhase('camera');
        notify('Consent needed', classified.message);
        return;
      }
      setFailure(classified);
      setPhase('failed');
    }
  };

  const retryLastAnalysis = () => {
    setFailure(null);
    if (lastImage) {
      void runAnalysis(lastImage);
    } else {
      setPhase('camera');
    }
  };

  const dismissFailure = () => {
    setFailure(null);
    setPhase('camera');
  };

  const updatePhotoConsent = async (next: boolean) => {
    if (isPreviewMode) {
      setPhotoConsentState(next);
      return;
    }

    setConsentSaving(true);
    try {
      const summary = await setConsent(next);
      setPhotoConsentState(summary.photo_analysis.granted);
    } catch (err) {
      console.warn('consent update failed', err);
      notify('Could not save consent', 'Please check your connection and try again.');
    } finally {
      setConsentSaving(false);
    }
  };

  const takePhoto = async () => {
    try {
      if (IS_WEB || cameraError) {
        await pickImage();
        return;
      }
      if (!permission?.granted) {
        const r = await requestPermission();
        if (!r.granted) {
          notify('Camera needed', 'Allow camera access or upload a photo instead.');
          return;
        }
      }
      const photo = await cameraRef.current?.takePictureAsync({ base64: true, quality: 0.7 });
      if (photo?.base64) await runAnalysis(photo.base64);
    } catch (err) {
      console.warn('camera capture failed', err);
      setCameraError(true);
      notify('Camera error', 'Try uploading a photo instead.');
    }
  };

  const pickImage = async () => {
    try {
      const result = await ImagePicker.launchImageLibraryAsync({
        base64: true,
        quality: 0.7,
        mediaTypes: ['images'],
      });
      if (!result.canceled && result.assets[0]?.base64) {
        await runAnalysis(result.assets[0].base64);
      }
    } catch (err) {
      console.error('image pick failed', err);
      notify('Upload failed', 'Could not open your photo library. Try another image.');
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

  if (phase === 'failed' && failure) {
    return (
      <View style={[styles.container, styles.center, { paddingTop: insets.top }]}>
        {failure.kind === 'low_quality' ? (
          <LowQualityImageState
            guidance={failure.guidance}
            onRetake={() => {
              setFailure(null);
              setPhase('camera');
            }}
            onChooseAnother={IS_WEB ? undefined : pickImage}
          />
        ) : failure.kind === 'provider_unavailable' ? (
          <ProviderUnavailableState
            retryable={failure.retryable}
            onRetry={retryLastAnalysis}
            onDismiss={dismissFailure}
          />
        ) : failure.kind === 'allowance_exhausted' && !billingAvailable() ? (
          <BetaFeatureUnavailableState
            message={`${failure.message} ${betaMessage()}`}
            onDismiss={dismissFailure}
          />
        ) : (
          <AnalysisFailedState
            message={failure.message}
            guidance={failure.guidance}
            allowancePreserved={failure.allowancePreserved}
            retryable={failure.retryable}
            onRetry={retryLastAnalysis}
            onDismiss={dismissFailure}
          />
        )}
      </View>
    );
  }

  if (phase === 'results' && preview) {
    return (
      <PreviewView
        preview={preview}
        insets={insets}
        onSignUp={() => router.push('/(auth)/welcome')}
        onClose={() => {
          try {
            if (router.canGoBack()) router.back();
            else router.replace('/');
          } catch {
            router.replace('/');
          }
        }}
      />
    );
  }

  if (phase === 'results' && analysis) {
    return (
      <ResultsView
        analysis={analysis}
        insets={insets}
        onClose={() => {
          try {
            if (router.canGoBack()) router.back();
            else router.replace('/(tabs)/today');
          } catch {
            router.replace('/(tabs)/today');
          }
        }}
        onPlan={() => router.push('/get-advice')}
      />
    );
  }

  const showCamera = !IS_WEB && !cameraError && !!permission?.granted;

  return (
    <View style={[styles.container, { backgroundColor: COLORS.scanBackground }]}>
      <View style={[styles.topBar, { paddingTop: insets.top + 8 }]}>
        <TouchableOpacity
          onPress={() => {
            try {
              if (router.canGoBack()) router.back();
              else router.replace('/(tabs)/scan-tab');
            } catch {
              router.replace('/(tabs)/today');
            }
          }}
          style={styles.iconBtn}
        >
          <Ionicons name="close" size={24} color={COLORS.white} />
        </TouchableOpacity>
        <Text style={styles.topTitle}>
          {scanType === 'hair' ? 'Hair check' : scanType === 'hands' ? 'Hands check' : 'Skin check'}
        </Text>
        <View style={{ width: 40 }} />
      </View>

      {showCamera ? (
        <CameraView
          ref={cameraRef}
          style={styles.camera}
          facing="front"
          onMountError={() => setCameraError(true)}
        >
          <View style={styles.guideRing} />
        </CameraView>
      ) : (
        <View style={[styles.camera, styles.center]}>
          <Ionicons name="cloud-upload-outline" size={42} color={COLORS.white} />
          <Text style={styles.permText}>
            {IS_WEB
              ? 'On web, upload a clear face/hair photo to run your check.'
              : 'Camera unavailable — upload a photo instead.'}
          </Text>
          {!IS_WEB && (
            <TouchableOpacity
              style={styles.secondaryBtn}
              onPress={async () => {
                try {
                  await requestPermission();
                } catch {
                  setCameraError(true);
                }
              }}
            >
              <Text style={styles.secondaryBtnText}>Allow camera</Text>
            </TouchableOpacity>
          )}
          <TouchableOpacity style={[styles.secondaryBtn, { marginTop: 10 }]} onPress={pickImage}>
            <Text style={styles.secondaryBtnText}>Upload photo</Text>
          </TouchableOpacity>
        </View>
      )}

      <View style={[styles.bottomBar, { paddingBottom: insets.bottom + 20 }]}>
        {errorLimit && (
          <Text style={styles.limitText}>Monthly checks used. Your allowance resets next month.</Text>
        )}
        {isPreviewMode && (
          <>
            <Text style={styles.hint}>
              Invite-only private test — enter your invite code to try a preview.
            </Text>
            <TextInput
              style={styles.inviteInput}
              placeholder="Invite code"
              placeholderTextColor="rgba(255,255,255,0.5)"
              autoCapitalize="characters"
              autoCorrect={false}
              value={previewInvite}
              onChangeText={setPreviewInvite}
            />
          </>
        )}
        <PhotoAnalysisConsentControl
          checked={photoConsent}
          busy={consentSaving}
          onChange={(next) => void updatePhotoConsent(next)}
        />
        <Text style={styles.hint}>
          {IS_WEB ? 'Upload a well-lit photo to continue.' : 'Centre yourself in good light, then capture or upload.'}
        </Text>
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

function PreviewView({
  preview,
  insets,
  onSignUp,
  onClose,
}: {
  preview: any;
  insets: any;
  onSignUp: () => void;
  onClose: () => void;
}) {
  const profile = preview.profile || {};
  const colors = preview.best_clothing_colors || [];
  const locked = preview.locked || [];
  const moreColors = Math.max(0, (preview.total_colors_found || 0) - colors.length);

  return (
    <View style={[styles.container, { backgroundColor: COLORS.backgroundSecondary, paddingTop: insets.top }]}>
      <View style={styles.resultsHeader}>
        <TouchableOpacity onPress={onClose}><Ionicons name="close" size={24} color={COLORS.textPrimary} /></TouchableOpacity>
        <Text style={styles.resultsTitle}>Your colours</Text>
        <View style={{ width: 24 }} />
      </View>

      <ScrollView contentContainerStyle={{ padding: SPACING.lg, paddingBottom: 160 }}>
        <Animated.View entering={FadeInDown} style={styles.previewCard}>
          <Text style={styles.sectionTitle}>What we can see</Text>
          <Text style={styles.previewTone}>
            {profile.skin_tone ? `${profile.skin_tone} skin tone` : 'Skin tone read'}
            {profile.undertone ? ` · ${profile.undertone} undertone` : ''}
          </Text>
        </Animated.View>

        <Animated.View entering={FadeInDown.delay(100)} style={styles.previewCard}>
          <Text style={styles.sectionTitle}>Colours that suit you</Text>
          {colors.map((c: any, i: number) => (
            <View key={i} style={styles.previewColorRow}>
              <View style={[styles.previewSwatch, { backgroundColor: c.hex_hint || COLORS.primary }]} />
              <View style={{ flex: 1 }}>
                <Text style={styles.previewColorName}>{c.color}</Text>
                {!!c.why && <Text style={styles.previewColorWhy}>{c.why}</Text>}
              </View>
            </View>
          ))}
          {moreColors > 0 && (
            <Text style={styles.previewMore}>+{moreColors} more in your full result</Text>
          )}
        </Animated.View>

        <Animated.View entering={FadeInDown.delay(200)} style={[styles.previewCard, styles.previewLockedCard]}>
          <View style={styles.previewLockRow}>
            <Ionicons name="lock-closed" size={18} color={COLORS.primary} />
            <Text style={styles.sectionTitle}>  Create a free account to unlock</Text>
          </View>
          {locked.map((item: string, i: number) => (
            <Text key={i} style={styles.previewLockedItem}>• {item}</Text>
          ))}
        </Animated.View>

        <Text style={styles.previewDisclaimer}>{preview.meta?.disclaimer}</Text>
      </ScrollView>

      <View style={[styles.previewCtaBar, { paddingBottom: insets.bottom + 16 }]}>
        <TouchableOpacity style={styles.previewCtaButton} onPress={onSignUp} activeOpacity={0.85}>
          <Text style={styles.previewCtaText}>Create free account</Text>
          <Ionicons name="arrow-forward" size={18} color={COLORS.white} />
        </TouchableOpacity>
        <Text style={styles.previewCtaNote}>Free — saves your result and your history</Text>
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
  const fashion = analysis.fashion || {};
  const style = analysis.fashion || analysis.style || {};
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

        <Section title="Your fashion stylist">
          <View style={styles.colorRow}>
            {(style.best_clothing_colors || []).map((c: any, i: number) => (
              <View key={i} style={styles.colorItem}>
                <View style={[styles.swatch, { backgroundColor: c.hex_hint || COLORS.primary }]} />
                <Text style={styles.colorName}>{c.color}</Text>
                <Text style={styles.colorWhy}>{c.why}</Text>
              </View>
            ))}
          </View>
          {(fashion.silhouettes_for_you || []).map((s: any, i: number) => (
            <Text key={`sil${i}`} style={styles.obsText}>· {s.silhouette} — {s.why}</Text>
          ))}
          {(fashion.fits_and_proportions || []).map((t: string, i: number) => (
            <Text key={`fp${i}`} style={styles.obsWhy}>· {t}</Text>
          ))}
          {(style.wardrobe_ideas_india || []).map((w: any, i: number) => (
            <View key={i} style={styles.outfitCard}>
              <Text style={styles.outfitOcc}>{w.occasion}</Text>
              <Text style={styles.outfitIdea}>{w.outfit_idea}</Text>
              <Text style={styles.colorWhy}>{w.why_it_works}</Text>
              {!!w.trend_note && <Text style={styles.colorWhy}>Trend: {w.trend_note}</Text>}
            </View>
          ))}
          {(fashion.current_trends_to_try || []).map((t: any, i: number) => (
            <View key={`tr${i}`} style={styles.outfitCard}>
              <Text style={styles.outfitOcc}>{t.trend}</Text>
              <Text style={styles.outfitIdea}>{t.how_to_wear_it_for_you}</Text>
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
  camera: { flex: 1, marginTop: 80, marginBottom: 235, marginHorizontal: 16, borderRadius: 24, overflow: 'hidden' },
  guideRing: {
    position: 'absolute', alignSelf: 'center', top: '22%',
    width: SCREEN_WIDTH * 0.62, height: SCREEN_WIDTH * 0.72, borderRadius: SCREEN_WIDTH * 0.31,
    borderWidth: 2, borderColor: 'rgba(255,255,255,0.7)',
  },
  bottomBar: { position: 'absolute', left: 0, right: 0, bottom: 0, paddingHorizontal: 20 },
  hint: { color: 'rgba(255,255,255,0.85)', textAlign: 'center', marginBottom: 16, fontFamily: FONTS.family.body, fontSize: 13 },
  inviteInput: {
    backgroundColor: 'rgba(255,255,255,0.12)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.25)',
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 12,
    marginBottom: 12,
    color: COLORS.white,
    fontFamily: FONTS.family.body,
    fontSize: 15,
    textAlign: 'center',
    letterSpacing: 1,
  },
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
  previewCard: {
    backgroundColor: COLORS.card, borderRadius: RADIUS.xl, padding: SPACING.lg, marginBottom: SPACING.md,
    borderWidth: 1, borderColor: COLORS.border, ...SHADOWS.sm,
  },
  previewTone: {
    fontFamily: FONTS.family.bodySemibold, fontSize: 16, color: COLORS.primary,
    textTransform: 'capitalize',
  },
  previewColorRow: { flexDirection: 'row', alignItems: 'center', gap: 12, marginBottom: 12 },
  previewSwatch: { width: 44, height: 44, borderRadius: 10, borderWidth: 1, borderColor: COLORS.border },
  previewColorName: { fontFamily: FONTS.family.bodySemibold, fontSize: 14, color: COLORS.textPrimary },
  previewColorWhy: { fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.textSecondary, marginTop: 2, lineHeight: 17 },
  previewMore: { fontFamily: FONTS.family.bodyMedium, fontSize: 13, color: COLORS.primary, marginTop: 4 },
  previewLockedCard: { backgroundColor: COLORS.primaryLight, borderColor: COLORS.primary },
  previewLockRow: { flexDirection: 'row', alignItems: 'center', marginBottom: 10 },
  previewLockedItem: {
    fontFamily: FONTS.family.body, fontSize: 14, color: COLORS.textSecondary, lineHeight: 24,
  },
  previewDisclaimer: {
    marginTop: SPACING.lg, fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.textMuted,
    lineHeight: 18, textAlign: 'center',
  },
  previewCtaBar: {
    position: 'absolute', left: 0, right: 0, bottom: 0, paddingHorizontal: SPACING.lg, paddingTop: 12,
    backgroundColor: COLORS.backgroundSecondary, borderTopWidth: 1, borderTopColor: COLORS.border,
  },
  previewCtaButton: {
    backgroundColor: COLORS.primary, borderRadius: RADIUS.lg, paddingVertical: 16,
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8,
  },
  previewCtaText: { fontFamily: FONTS.family.bodySemibold, fontSize: 16, color: COLORS.white },
  previewCtaNote: {
    fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.textMuted,
    textAlign: 'center', marginTop: 8,
  },
  scoreCard: {
    backgroundColor: COLORS.card, borderRadius: RADIUS.xl, padding: SPACING.lg,
    borderWidth: 1, borderColor: COLORS.border, ...SHADOWS.sm,
  },
  headline: { fontFamily: FONTS.family.heading, fontSize: 22, color: COLORS.textPrimary, marginBottom: 16 },
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
