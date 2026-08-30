/**
 * Choosing an event look, inside Event Ready.
 *
 * The recommendation backend is retained as infrastructure for Event Ready
 * (PRODUCT_CONSTITUTION.md, master rule). This component is the only thing that
 * drives it: it is reached from the Event Ready screen, it always carries an
 * event, and there is no way to open it on its own. It is not a Style screen
 * and there is no route to it.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { ActivityIndicator, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';

import {
  FollowUpQuestion, Lookboard, NotEnoughInventory, StyleProcessing, WhyThisWorks,
} from '../style/StylePieces';
import {
  Look, OccasionDefinition, OccasionInput, StyleResult,
  getOccasionTypes, setEventReadyLook, structuredError, styleForOccasion,
} from '../../services/apiV2';
import { COLORS, FONTS, RADIUS, SPACING } from '../../theme/colors';

export type EventForLook = {
  eventId: string;
  occasionKey?: string | null;
  eventDate?: string | null;
  eventTitle?: string | null;
  dressCode?: string | null;
  location?: string | null;
};

/**
 * Build the styling request for an event.
 *
 * The event is the source of truth for date, title, dress code and location, so
 * those are applied last: a follow-up answer must not be able to contradict the
 * confirmed event.
 */
export function buildEventOccasionInput(
  selected: OccasionDefinition,
  answers: Record<string, string | undefined>,
  event: EventForLook,
): OccasionInput {
  const body: OccasionInput = { occasion_key: selected.key };
  for (const [key, value] of Object.entries(answers)) if (value) (body as any)[key] = value;
  if (event.eventDate) body.event_date = event.eventDate;
  if (event.eventTitle) body.title = event.eventTitle;
  if (event.dressCode) body.dress_code = event.dressCode;
  if (event.location) body.location = event.location;
  body.occasion_key = selected.key;
  return body;
}

type Stage = 'choose' | 'processing' | 'result';

export function EventLookPicker({ event, onLinked, onCancel }: {
  event: EventForLook;
  onLinked: () => void;
  onCancel: () => void;
}) {
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
      const match = definitions.find((occasion) => occasion.key === event.occasionKey);
      setSelected(match || definitions[0] || null);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }, [event.occasionKey]);

  useEffect(() => { void load(); }, [load]);

  // Only questions this occasion needs, and never the dress code when the event
  // already fixed one.
  const questions = useMemo(
    () => (selected?.questions || []).filter(
      (question) => question.options.length > 0 && !(event.dressCode && question.key === 'dress_code'),
    ),
    [event.dressCode, selected],
  );

  const run = async () => {
    if (!selected) return;
    setStage('processing');
    setError(null);
    try {
      setResult(await styleForOccasion(buildEventOccasionInput(selected, answers, event)));
      setStage('result');
    } catch (err) {
      setError(err);
      setStage('choose');
    }
  };

  const chooseLook = async (look: Look) => {
    if (linkingLookId) return;
    setLinkingLookId(look.id);
    setError(null);
    try {
      await setEventReadyLook(event.eventId, look.id);
      onLinked();
    } catch (err) {
      setError(err);
    } finally {
      setLinkingLookId(null);
    }
  };

  if (loading) {
    return <View style={styles.panel} accessibilityLabel="Loading look options">
      <ActivityIndicator color={COLORS.primary} />
    </View>;
  }

  return (
    <View style={styles.panel} accessibilityLabel="Choose a look for this event">
      <Text style={styles.eyebrow}>CHOOSE A LOOK</Text>
      <Text style={styles.title}>{event.eventTitle || 'Your event'}</Text>
      <Text style={styles.body}>Built from what you already own.</Text>

      {!!error && <Text style={styles.error}>{structuredError(error)?.message || 'Something went wrong. Please try again.'}</Text>}

      {stage === 'processing' && <StyleProcessing occasionLabel={selected?.label || 'event'} />}

      {stage === 'choose' && <>
        {occasions.length > 1 && !event.occasionKey && (
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chipRow}>
            {occasions.map((occasion) => (
              <TouchableOpacity
                key={occasion.key}
                accessibilityRole="button"
                accessibilityLabel={`Occasion ${occasion.label}`}
                onPress={() => { setSelected(occasion); setAnswers({}); }}
                style={[styles.chip, selected?.key === occasion.key && styles.chipOn]}
              >
                <Text style={[styles.chipText, selected?.key === occasion.key && styles.chipTextOn]}>{occasion.label}</Text>
              </TouchableOpacity>
            ))}
          </ScrollView>
        )}
        {questions.map((question) => (
          <FollowUpQuestion
            key={question.key}
            question={question}
            value={answers[question.key]}
            onChange={(value) => setAnswers((previous) => ({ ...previous, [question.key]: value }))}
          />
        ))}
        <TouchableOpacity
          accessibilityRole="button"
          accessibilityLabel="Build looks for this event"
          disabled={!selected}
          onPress={() => void run()}
          style={[styles.primary, !selected && styles.primaryOff]}
        >
          <Text style={styles.primaryText}>Build my looks</Text>
        </TouchableOpacity>
      </>}

      {stage === 'result' && result && <>
        {result.looks.length === 0
          ? <NotEnoughInventory result={result} onAdd={onCancel} />
          : result.looks.map((look) => (
            <View key={look.id}>
              <Lookboard look={look} onUseForEvent={() => void chooseLook(look)} />
              {!!look.why_it_works && <WhyThisWorks look={look} />}
            </View>
          ))}
      </>}

      <TouchableOpacity accessibilityRole="button" accessibilityLabel="Cancel choosing a look" onPress={onCancel}>
        <Text style={styles.link}>Not now</Text>
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  panel: { backgroundColor: COLORS.card, borderColor: COLORS.border, borderRadius: RADIUS.lg, borderWidth: 1, marginTop: SPACING.md, padding: SPACING.lg },
  eyebrow: { color: COLORS.primary, fontFamily: FONTS.family.bodySemibold, fontSize: 10, letterSpacing: 1.4 },
  title: { color: COLORS.textPrimary, fontFamily: FONTS.family.headingMedium, fontSize: 20, marginTop: 4 },
  body: { color: COLORS.textSecondary, fontFamily: FONTS.family.body, fontSize: 13, lineHeight: 19, marginTop: 4 },
  error: { color: COLORS.error, fontFamily: FONTS.family.body, fontSize: 13, marginTop: SPACING.sm },
  chipRow: { gap: 8, paddingVertical: SPACING.sm },
  chip: { backgroundColor: COLORS.backgroundSecondary, borderColor: COLORS.border, borderRadius: RADIUS.full, borderWidth: 1, paddingHorizontal: 14, paddingVertical: 8 },
  chipOn: { backgroundColor: COLORS.primary, borderColor: COLORS.primary },
  chipText: { color: COLORS.textSecondary, fontFamily: FONTS.family.body, fontSize: 13 },
  chipTextOn: { color: COLORS.white },
  primary: { alignItems: 'center', backgroundColor: COLORS.primary, borderRadius: RADIUS.lg, marginTop: SPACING.md, padding: 14 },
  primaryOff: { opacity: 0.5 },
  primaryText: { color: COLORS.white, fontFamily: FONTS.family.bodySemibold, fontSize: 15 },
  link: { color: COLORS.primary, fontFamily: FONTS.family.bodySemibold, fontSize: 13, marginTop: SPACING.md },
});
