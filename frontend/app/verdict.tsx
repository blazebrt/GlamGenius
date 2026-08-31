/**
 * The verdict screen.
 *
 * Three seconds, no reading. The colour block fills the top of the screen and
 * carries the answer on its own — green is BUY, yellow is WAIT, red is SKIP —
 * so somebody who has never seen this app knows what to do before their eye
 * has focused on the letter, let alone the sentence under it.
 *
 * Below that, exactly three lines: what to do, one number in something you can
 * picture, and a better one nearby. Then Why, Listen and Share.
 *
 * No string in this file. Everything comes from src/strings/verdict.ts, so the
 * copy can be reviewed against LEGAL_RULES.md and translated without anyone
 * opening a component.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Modal, Platform, ScrollView, Share, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import * as ImagePicker from 'expo-image-picker';
import { Ionicons } from '@expo/vector-icons';

import { S, t } from '../src/strings/verdict';
import { COLORS, FONTS, SPACING } from '../src/theme/colors';
import {
  buildVerdict, type VerdictIngredient, type VerdictSource,
} from '../src/services/verdictModel';
import { isSpeechAvailable, speak, stopSpeaking } from '../src/services/speech';
import {
  flushReports, makeReport, submitReport, type ReportReason,
} from '../src/services/errorReports';
import { getProductVerdict } from '../src/services/verdictClient';
import { OpenFoodFactsAttribution } from '../src/components/common/OpenFoodFactsAttribution';
import {
  ComponentRow, FactorSection, GradeBlock, IngredientDetail, IngredientList,
  NotGradedCard, ReportSheet, UnknownCard, VerdictActions, VerdictLines,
} from '../src/components/verdict/VerdictPieces';

export default function VerdictScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { barcode } = useLocalSearchParams<{ barcode?: string }>();

  const [source, setSource] = useState<VerdictSource | null>(null);
  const [loadState, setLoadState] = useState<'loading' | 'ready' | 'failed'>('loading');
  const [tab, setTab] = useState<'verdict' | 'why' | 'ingredients'>('verdict');
  const [expanded, setExpanded] = useState<string | null>(null);
  const [speaking, setSpeaking] = useState(false);
  const [reportSubject, setReportSubject] = useState<string | null>(null);
  const [photoUri, setPhotoUri] = useState<string | null>(null);
  const [reportBusy, setReportBusy] = useState(false);
  const [explaining, setExplaining] = useState<VerdictIngredient | null>(null);
  const [reportStatus, setReportStatus] = useState<string | null>(null);
  const [explanation, setExplanation] = useState<{ explanation: string; rule: string | null } | null>(null);

  const load = useCallback(() => {
    if (!barcode) return;
    setLoadState('loading');
    void getProductVerdict(barcode)
      .then((next) => { setSource(next); setLoadState('ready'); })
      .catch(() => { setSource(null); setLoadState('failed'); });
  }, [barcode]);

  useEffect(() => {
    load();
    // Anything held from a previous session goes now.
    void flushReports().catch(() => undefined);
  }, [load]);

  useEffect(() => () => { void stopSpeaking(); }, []);

  const view = useMemo(() => (source ? buildVerdict(source) : null), [source]);

  const onListen = useCallback(() => {
    if (!view) return;
    if (speaking) {
      void stopSpeaking();
      setSpeaking(false);
      return;
    }
    void speak(view.spoken, {
      onStart: () => setSpeaking(true),
      onDone: () => setSpeaking(false),
      onError: () => setSpeaking(false),
    });
  }, [speaking, view]);

  const onShare = useCallback(() => {
    if (!view) return;
    void Share.share({ message: `${view.verdict}. ${view.action} ${view.everydayNumber}`.trim() });
  }, [view]);

  const openReport = useCallback((subject: string) => {
    setReportSubject(subject);
    setPhotoUri(null);
    setReportStatus(null);
  }, []);

  const addPhoto = useCallback(async () => {
    const permission = await ImagePicker.requestCameraPermissionsAsync();
    if (!permission.granted) return;
    const shot = await ImagePicker.launchCameraAsync({ quality: 0.6 });
    if (!shot.canceled && shot.assets[0]?.uri) setPhotoUri(shot.assets[0].uri);
  }, []);

  const sendReport = useCallback(async (reason: ReportReason) => {
    if (!reportSubject) return;
    setReportBusy(true);
    const sent = await submitReport(makeReport({
      barcode: barcode ?? null, subject: reportSubject, reason, photo_uri: photoUri,
    }));
    setReportStatus(sent ? S.report.sent : S.report.failed);
    setReportBusy(false);
    setTimeout(() => setReportSubject(null), 1600);
  }, [barcode, photoUri, reportSubject]);

  if (!view || !source) {
    return (
      <View style={[styles.container, styles.centred, { paddingTop: insets.top }]}>
        {loadState === 'loading' ? (
          <Text style={styles.stateBody}>{S.loading.working}</Text>
        ) : (
          <>
            <Text style={styles.stateTitle}>{S.loading.failedTitle}</Text>
            <Text style={styles.stateBody}>{S.loading.failedBody}</Text>
            <TouchableOpacity
              accessibilityRole="button" accessibilityLabel={S.loading.retry}
              onPress={load} style={styles.stateButton}
            >
              <Text style={styles.stateButtonText}>{S.loading.retry}</Text>
            </TouchableOpacity>
            <TouchableOpacity
              accessibilityRole="button" accessibilityLabel={S.loading.back}
              onPress={() => router.back()} style={styles.link}
            >
              <Text style={styles.linkText}>{S.loading.back}</Text>
            </TouchableOpacity>
          </>
        )}
      </View>
    );
  }

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <View style={styles.header}>
        <TouchableOpacity
          accessibilityRole="button" accessibilityLabel={S.primary.scanAnother}
          onPress={() => router.back()} hitSlop={12}
        >
          <Ionicons name="chevron-back" size={26} color={COLORS.textPrimary} />
        </TouchableOpacity>
        <Text style={styles.productName} numberOfLines={1}>{source.productName}</Text>
        <TouchableOpacity
          accessibilityRole="button"
          accessibilityLabel={t(S.a11y.report, { subject: source.productName })}
          onPress={() => openReport(source.productName)} hitSlop={12}
        >
          <Text style={styles.reportTop}>{S.report.trigger}</Text>
        </TouchableOpacity>
      </View>

      <ScrollView
        contentContainerStyle={{ padding: SPACING.lg, paddingBottom: insets.bottom + SPACING.xl }}
      >
        {tab === 'verdict' && (
          <>
            {!!source.taxonomy && (
              <Text style={styles.category}>{`${S.taxonomy[source.taxonomy.category as keyof typeof S.taxonomy]} · ${S.taxonomy[source.taxonomy.subcategory as keyof typeof S.taxonomy]}`}</Text>
            )}
            <GradeBlock view={view} />
            {source.outcome === 'graded' && (
              <VerdictLines view={view} onReport={openReport} />
            )}
            <FactorSection title={S.factors.lowers} rows={source.lowers ?? []} empty={S.factors.noLowers}
              onExplain={(row) => setExplanation(row)} />
            <FactorSection title={S.factors.helps} rows={source.helps ?? []} empty={S.factors.noHelps}
              onExplain={(row) => setExplanation(row)} />
            {source.outcome === 'not_graded' && (
              <NotGradedCard quantity={source.quantityGuidance} purity={source.purityNote} />
            )}
            {source.outcome === 'not_enough_information' && (
              <UnknownCard
                missing={source.missing ?? []}
                onSendPhoto={() => openReport(source.productName)}
              />
            )}
            <VerdictActions
              onWhy={() => setTab('why')}
              onListen={onListen}
              onShare={onShare}
              speaking={speaking}
              speechAvailable={isSpeechAvailable()}
            />
            <TouchableOpacity
              accessibilityRole="button" accessibilityLabel={S.primary.ingredients}
              onPress={() => setTab('ingredients')} style={styles.link}
            >
              <Text style={styles.linkText}>{S.primary.ingredients}</Text>
            </TouchableOpacity>
          </>
        )}

        {tab === 'why' && (
          <>
            <Text style={styles.sectionTitle}>{S.why.title}</Text>
            <Text style={styles.sectionSubtitle}>{S.why.subtitle}</Text>
            {source.components.map((component) => (
              <ComponentRow
                key={component.key}
                component={component}
                expanded={expanded === component.key}
                onToggle={() => setExpanded(expanded === component.key ? null : component.key)}
              />
            ))}
            <TouchableOpacity
              accessibilityRole="button" accessibilityLabel={S.primary.ingredients}
              onPress={() => setTab('ingredients')} style={styles.link}
            >
              <Text style={styles.linkText}>{S.primary.ingredients}</Text>
            </TouchableOpacity>
          </>
        )}

        {tab === 'ingredients' && (
          <>
            <Text style={styles.sectionTitle}>{S.ingredients.title}</Text>
            <Text style={styles.sectionSubtitle}>{S.ingredients.subtitle}</Text>
            <IngredientList
              ingredients={source.ingredients}
              onReport={openReport}
              onExplain={setExplaining}
            />
          </>
        )}

        {tab !== 'verdict' && (
          <TouchableOpacity
            accessibilityRole="button" accessibilityLabel={S.why.title}
            onPress={() => setTab('verdict')} style={styles.link}
          >
            <Text style={styles.linkText}>{S.primary.scanAnother}</Text>
          </TouchableOpacity>
        )}

        {/*
          A licence condition, not a footer. The name, ingredients and
          nutrition on all three tabs can come from Open Food Facts, so where
          they do, this renders with them.
        */}
        {!!source.attribution && <OpenFoodFactsAttribution />}
      </ScrollView>

      <Modal
        visible={explaining !== null}
        animationType="slide"
        transparent
        onRequestClose={() => setExplaining(null)}
      >
        <View style={styles.explanationBackdrop}>
          <View style={styles.explanationCard}>
            {explaining && <IngredientDetail ingredient={explaining} />}
            <TouchableOpacity
              accessibilityRole="button"
              accessibilityLabel={S.report.cancel}
              onPress={() => setExplaining(null)}
              style={styles.link}
            >
              <Text style={styles.linkText}>{S.report.cancel}</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>

      <Modal
        visible={reportSubject !== null}
        animationType={Platform.OS === 'web' ? 'none' : 'slide'}
        onRequestClose={() => setReportSubject(null)}
      >
        <View style={{ flex: 1, paddingTop: insets.top }}>
          <ReportSheet
            subject={reportSubject ?? ''}
            onPick={(reason) => void sendReport(reason)}
            onCancel={() => setReportSubject(null)}
            onAddPhoto={() => void addPhoto()}
            photoAdded={photoUri !== null}
            busy={reportBusy}
            status={reportStatus}
          />
        </View>
      </Modal>
      <Modal visible={explanation !== null} transparent animationType="fade" onRequestClose={() => setExplanation(null)}>
        <View style={styles.explanationBackdrop}>
          <View style={styles.explanationCard}>
            <Text style={styles.cardTitle}>{S.factors.details}</Text>
            <Text style={styles.body}>{explanation ? (S.factors[explanation.explanation as keyof typeof S.factors] ?? S.factors.lower_label_fact) : ''}</Text>
            <TouchableOpacity accessibilityRole="button" accessibilityLabel={S.report.cancel} onPress={() => setExplanation(null)} style={styles.link}>
              <Text style={styles.linkText}>{S.report.cancel}</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  centred: { alignItems: 'center', justifyContent: 'center', padding: SPACING.lg },
  stateTitle: {
    color: COLORS.textPrimary, fontFamily: FONTS.family.heading, fontSize: 22,
    marginBottom: SPACING.sm, textAlign: 'center',
  },
  stateBody: {
    color: COLORS.textMuted, fontFamily: FONTS.family.body, fontSize: 15,
    lineHeight: 22, marginBottom: SPACING.lg, textAlign: 'center',
  },
  stateButton: {
    backgroundColor: COLORS.primary, borderRadius: 12,
    paddingHorizontal: SPACING.lg, paddingVertical: SPACING.md,
  },
  stateButtonText: {
    color: COLORS.textInverse, fontFamily: FONTS.family.bodySemibold, fontSize: 16,
  },
  container: { flex: 1, backgroundColor: COLORS.background },
  header: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: SPACING.lg, paddingVertical: SPACING.sm, gap: SPACING.sm,
  },
  productName: {
    flex: 1, fontFamily: FONTS.family.bodyMedium, fontSize: 15, color: COLORS.textSecondary,
  },
  category: { fontFamily: FONTS.family.bodyMedium, fontSize: 13, color: COLORS.textSecondary, marginBottom: SPACING.sm },
  reportTop: { fontFamily: FONTS.family.bodySemibold, fontSize: 12, color: COLORS.primary },
  sectionTitle: { fontFamily: FONTS.family.heading, fontSize: 26, color: COLORS.textPrimary },
  sectionSubtitle: {
    fontFamily: FONTS.family.body, fontSize: 14, color: COLORS.textSecondary,
    marginTop: 4, marginBottom: SPACING.sm,
  },
  link: { paddingVertical: SPACING.md, alignItems: 'center' },
  linkText: { fontFamily: FONTS.family.bodySemibold, fontSize: 14, color: COLORS.primary },
  body: { fontFamily: FONTS.family.body, fontSize: 14, color: COLORS.textSecondary, marginTop: SPACING.sm },
  cardTitle: { fontFamily: FONTS.family.heading, fontSize: 22, color: COLORS.textPrimary },
  explanationBackdrop: { flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: '#00000066', padding: SPACING.lg },
  explanationCard: { width: '100%', borderRadius: 16, backgroundColor: COLORS.card, padding: SPACING.lg },
});
