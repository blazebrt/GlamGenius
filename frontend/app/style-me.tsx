/**
 * Style Me — pick an occasion, answer only what matters, get three looks.
 *
 * The screen has four states and never blurs them together: choosing,
 * processing, looks, and not-enough-inventory. The last one is not an error
 * screen, because nothing failed.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { ActivityIndicator, ScrollView, Share, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';

import {
  EntitlementNote, FollowUpQuestion, Lookboard, NotEnoughInventory, OccasionTile, StyleProcessing,
} from '../src/components/style/StylePieces';
import { AnalysisFailedState } from '../src/components/TrustStates';
import {
  Look, LookPiece, OccasionDefinition, OccasionInput, StyleResult,
  allowanceWasPreserved, failureGuidance, getOccasionTypes, reviseLook,
  sendLookFeedback, setEventReadyLook, structuredError, styleForOccasion,
} from '../src/services/apiV2';
import { COLORS, FONTS, RADIUS, SPACING } from '../src/theme/colors';

type Stage = 'choose' | 'processing' | 'result';

export function buildOccasionInput(
  selected: OccasionDefinition,
  answers: Record<string, string | undefined>,
  event?: { eventDate?: string; eventTitle?: string; dressCode?: string; location?: string },
): OccasionInput {
  const body: OccasionInput = {
    occasion_key: selected.key,
    ...(event?.eventDate ? { event_date: event.eventDate } : {}),
    ...(event?.eventTitle ? { title: event.eventTitle } : {}),
    ...(event?.dressCode ? { dress_code: event.dressCode } : {}),
    ...(event?.location ? { location: event.location } : {}),
  };
  for (const [key, value] of Object.entries(answers)) if (value) (body as any)[key] = value;
  return body;
}

export default function StyleMeScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const params = useLocalSearchParams<{ eventReadyEventId?: string | string[]; occasionKey?: string | string[]; eventDate?: string | string[]; eventTitle?: string | string[]; dressCode?: string | string[]; location?: string | string[] }>();
  const param = (value?: string | string[]) => Array.isArray(value) ? value[0] : value;
  const eventReadyEventId = param(params.eventReadyEventId);
  const eventMode = Boolean(eventReadyEventId);
  const eventOccasionKey = param(params.occasionKey);
  const eventDate = param(params.eventDate);
  const eventTitle = param(params.eventTitle);
  const eventDressCode = param(params.dressCode);
  const eventLocation = param(params.location);
  const [occasions, setOccasions] = useState<OccasionDefinition[]>([]);
  const [selected, setSelected] = useState<OccasionDefinition | null>(null);
  const [answers, setAnswers] = useState<Record<string, string | undefined>>({});
  const [stage, setStage] = useState<Stage>('choose');
  const [result, setResult] = useState<StyleResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<any>(null);
  const [linkingLookId, setLinkingLookId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const definitions = (await getOccasionTypes()).occasions;
      setOccasions(definitions);
      if (eventMode && eventOccasionKey) {
        const eventOccasion = definitions.find((occasion) => occasion.key === eventOccasionKey);
        if (eventOccasion) setSelected(eventOccasion);
      }
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }, [eventMode, eventOccasionKey]);

  useEffect(() => { void load(); }, [load]);

  // Only the questions this occasion actually needs, and only the ones we can
  // present as a choice.
  const questions = useMemo(
    () => (selected?.questions || []).filter((question) => question.options.length > 0),
    [selected]
  );

  const pick = (occasion: OccasionDefinition) => {
    setSelected(occasion);
    setAnswers({});
    setResult(null);
    setStage('choose');
  };

  const run = async () => {
    if (!selected) return;
    setStage('processing');
    setError(null);
    const body = buildOccasionInput(selected, answers, eventMode ? { eventDate, eventTitle, dressCode: eventDressCode, location: eventLocation } : undefined);
    try {
      setResult(await styleForOccasion(body));
      setStage('result');
    } catch (err) {
      setError(err);
      setStage('choose');
    }
  };

  const linkLookToEvent = async (look: Look) => {
    if (!eventReadyEventId || linkingLookId) return;
    setLinkingLookId(look.id);
    setError(null);
    try {
      await setEventReadyLook(eventReadyEventId, look.id);
      router.replace({ pathname: '/event-ready', params: { eventId: eventReadyEventId } });
    } catch (err) {
      setError(err);
    } finally {
      setLinkingLookId(null);
    }
  };

  const replaceLook = (updated: Look) =>
    setResult((current) => current && {
      ...current,
      looks: current.looks.map((look) => (look.id === updated.id ? updated : look)),
    });

  const revise = async (look: Look) => {
    try {
      replaceLook(await reviseLook(look.id));
    } catch (err) {
      setError(err);
    }
  };

  const feedback = async (look: Look, rating: 'saved' | 'not_for_me') => {
    try {
      replaceLook(await sendLookFeedback(look.id, rating));
    } catch (err) {
      setError(err);
    }
  };

  const share = async (look: Look) => {
    // Only the shareable payload the backend built, never the raw look.
    await Share.share({ title: look.share.title, message: look.share.text });
  };

  const swap = (look: Look, piece: LookPiece) =>
    router.push({ pathname: '/look', params: { id: look.id, swapSlot: piece.slot } });

  if (loading) {
    return <View style={styles.center}><ActivityIndicator color={COLORS.primary} /></View>;
  }

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <View style={styles.top}>
        <TouchableOpacity accessibilityRole="button" accessibilityLabel="Back" onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={24} color={COLORS.textPrimary} />
        </TouchableOpacity>
        <Text style={styles.topTitle}>Style Me</Text>
        <View style={{ width: 24 }} />
      </View>

      <ScrollView contentContainerStyle={{ padding: SPACING.lg, paddingBottom: insets.bottom + 48 }}>
        {stage === 'processing' && <StyleProcessing occasionLabel={selected?.label || 'occasion'} />}

        {stage !== 'processing' && (
          <>
            {!!error && (
              <AnalysisFailedState
                message={structuredError(error)?.message || 'We could not build your looks just now.'}
                guidance={failureGuidance(error)}
                allowancePreserved={allowanceWasPreserved(error)}
                retryable={structuredError(error)?.retryable !== false}
                onRetry={() => (selected ? void run() : void load())}
                onDismiss={() => setError(null)}
              />
            )}

            {stage === 'choose' && (
              <>
                <Text style={styles.eyebrow}>{eventMode ? 'EVENT MODE' : 'WHAT ARE YOU DRESSING FOR?'}</Text>
                <Text style={styles.title}>{eventMode ? `Looks for ${eventTitle || 'your event'}` : 'Style me for an occasion'}</Text>
                <Text style={styles.body}>
                  We build looks only from things you have confirmed you own. Anything you would need to buy is marked clearly.
                </Text>

                {eventMode ? (
                  <View style={styles.detail} accessibilityLabel="Event occasion">
                    <Text style={styles.detailTitle}>{selected?.label || 'Event occasion'}</Text>
                    <Text style={styles.body}>This event type is confirmed in Event Ready.</Text>
                  </View>
                ) : (
                  <View style={styles.grid}>
                    {occasions.map((occasion) => (
                      <OccasionTile
                        key={occasion.key}
                        occasion={occasion}
                        selected={selected?.key === occasion.key}
                        onPress={() => pick(occasion)}
                      />
                    ))}
                  </View>
                )}

                {selected && (
                  <View style={styles.detail}>
                    <Text style={styles.detailTitle}>{selected.label}</Text>
                    <Text style={styles.body}>{selected.notes}</Text>
                    {questions.map((question) => (
                      <FollowUpQuestion
                        key={question.key}
                        question={question}
                        value={answers[question.key]}
                        onChange={(value) => setAnswers((current) => ({ ...current, [question.key]: value }))}
                      />
                    ))}
                    <TouchableOpacity
                      accessibilityRole="button"
                      accessibilityLabel={`Build my ${selected.label} looks`}
                      onPress={() => void run()}
                      style={styles.primary}
                    >
                      <Text style={styles.primaryText}>Build my looks</Text>
                      <Ionicons name="arrow-forward" size={18} color={COLORS.white} />
                    </TouchableOpacity>
                  </View>
                )}
              </>
            )}

            {stage === 'result' && result && (
              result.status === 'not_enough_inventory'
                ? <NotEnoughInventory result={result} onAdd={() => router.push('/inventory-add')} />
                : (
                  <>
                    <Text style={styles.eyebrow}>{result.looks.length} OPTION{result.looks.length > 1 ? 'S' : ''}</Text>
                    <Text style={styles.title}>{result.occasion.title}</Text>
                    <EntitlementNote result={result} />
                    {result.looks.map((look) => (
                      <Lookboard
                        key={look.id}
                        look={look}
                        onSwap={(piece) => swap(look, piece)}
                        onRevise={() => void revise(look)}
                        onSave={() => void feedback(look, 'saved')}
                        onReject={() => void feedback(look, 'not_for_me')}
                        onShare={() => void share(look)}
                        onUseForEvent={eventMode ? () => void linkLookToEvent(look) : undefined}
                      />
                    ))}
                    {!!result.disclaimer && <Text style={styles.disclaimer}>{result.disclaimer}</Text>}
                    <TouchableOpacity
                      accessibilityRole="button"
                      accessibilityLabel="Style me for something else"
                      onPress={() => { setStage('choose'); setResult(null); }}
                    >
                      <Text style={styles.link}>Style me for something else</Text>
                    </TouchableOpacity>
                  </>
                )
            )}
          </>
        )}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.backgroundSecondary },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: COLORS.background },
  top: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingHorizontal: SPACING.lg, paddingVertical: 12 },
  topTitle: { fontFamily: FONTS.family.bodySemibold, color: COLORS.textPrimary },
  eyebrow: { fontFamily: FONTS.family.bodySemibold, color: COLORS.primary, fontSize: 10, letterSpacing: 1.3 },
  title: { fontFamily: FONTS.family.heading, color: COLORS.textPrimary, fontSize: 29, marginTop: 5, marginBottom: 6 },
  body: { fontFamily: FONTS.family.body, color: COLORS.textSecondary, fontSize: 13, lineHeight: 19, marginBottom: 12 },
  grid: { flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'flex-start', marginTop: 6 },
  detail: { marginTop: SPACING.lg, backgroundColor: COLORS.card, borderRadius: RADIUS.xl, padding: SPACING.lg, borderWidth: 1, borderColor: COLORS.border },
  detailTitle: { fontFamily: FONTS.family.headingMedium, fontSize: 20, color: COLORS.textPrimary, marginBottom: 4 },
  primary: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, backgroundColor: COLORS.primary, borderRadius: RADIUS.full, paddingVertical: 14, marginTop: SPACING.lg },
  primaryText: { fontFamily: FONTS.family.bodySemibold, fontSize: 14, color: COLORS.white },
  link: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.primary, textAlign: 'center', marginTop: SPACING.md },
  disclaimer: { fontFamily: FONTS.family.body, fontSize: 11, color: COLORS.textMuted, textAlign: 'center', marginTop: SPACING.sm },
});
