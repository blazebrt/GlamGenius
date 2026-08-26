import React, { useCallback, useState } from 'react';
import { ActivityIndicator, RefreshControl, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { useFocusEffect, useLocalSearchParams, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';

import { CareSummary, EventReadyActionRow, MissingInformation } from '../src/components/planner/EventReadyPieces';
import {
  EventReady, OccasionDefinition, generateEventReady, getEventReady, getOccasionTypes,
  patchCalendarEvent, setEventReadyActionComplete,
} from '../src/services/apiV2';
import { COLORS, FONTS, RADIUS, SPACING } from '../src/theme/colors';

const errorMessage = (error: any) => error?.response?.data?.detail?.message || 'We could not refresh this event. Please try again.';

export default function EventReadyScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const params = useLocalSearchParams<{ eventId?: string | string[] }>();
  const eventId = Array.isArray(params.eventId) ? params.eventId[0] : params.eventId;
  const [ready, setReady] = useState<EventReady | null>(null);
  const [occasions, setOccasions] = useState<OccasionDefinition[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [pendingActions, setPendingActions] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (mode: 'initial' | 'refresh' = 'initial') => {
    if (!eventId) { setError('This event is missing.'); setLoading(false); return; }
    if (mode === 'refresh') setRefreshing(true);
    try {
      setReady(await getEventReady(eventId));
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [eventId]);

  useFocusEffect(useCallback(() => { void load(); }, [load]));

  const loadOccasions = async () => {
    if (occasions.length) return;
    try { setOccasions((await getOccasionTypes()).occasions); } catch (err) { setError(errorMessage(err)); }
  };

  const generate = async () => {
    if (!eventId || generating) return;
    setGenerating(true);
    setError(null);
    try { setReady(await generateEventReady(eventId)); } catch (err) { setError(errorMessage(err)); } finally { setGenerating(false); }
  };

  const confirmOccasion = async (occasion: OccasionDefinition) => {
    if (!eventId || confirming || !ready) return;
    setConfirming(true);
    setError(null);
    try {
      await patchCalendarEvent(eventId, { occasion_key: occasion.key });
      // A PATCH changes the server-derived Event Ready read model. Reconcile
      // through the canonical route rather than merging only the CalendarEvent
      // into stale missing-information/style fields in React.
      if (ready.status === 'needs_confirmation') setReady(await generateEventReady(eventId));
      else setReady(await getEventReady(eventId));
    } catch (err) { setError(errorMessage(err)); } finally { setConfirming(false); }
  };

  const toggleAction = async (actionId: string, completed: boolean) => {
    if (!eventId || pendingActions.has(actionId)) return;
    setPendingActions((current) => new Set(current).add(actionId));
    setError(null);
    try { setReady(await setEventReadyActionComplete(eventId, actionId, !completed)); } catch (err) { setError(errorMessage(err)); } finally {
      setPendingActions((current) => { const next = new Set(current); next.delete(actionId); return next; });
    }
  };

  if (loading && !ready) return <View style={styles.center}><ActivityIndicator color={COLORS.primary} /></View>;
  if (!ready) return <View style={[styles.center, { padding: SPACING.lg }]}><Text style={styles.body}>{error || 'This event is not available.'}</Text><TouchableOpacity accessibilityRole="button" onPress={() => void load()} style={styles.primary}><Text style={styles.primaryText}>Try again</Text></TouchableOpacity></View>;

  const event = ready.event;
  const effectivePast = ready.status === 'past' || ready.countdown.days_until < 0;
  const needsConfirmation = !effectivePast && (!event.user_confirmed || !event.occasion_key || ready.status === 'needs_confirmation');
  const countdown = effectivePast ? 'Event passed' : ready.countdown.days_until === 0 ? 'Today' : ready.countdown.days_until === 1 ? 'Tomorrow' : `${ready.countdown.days_until} days to go`;
  const selectedOccasion = occasions.find((occasion) => occasion.key === event.occasion_key);

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <View style={styles.top}>
        <TouchableOpacity accessibilityRole="button" accessibilityLabel="Back" onPress={() => router.back()}><Ionicons name="arrow-back" size={24} color={COLORS.textPrimary} /></TouchableOpacity>
        <Text style={styles.topTitle}>Event Ready</Text>
        <TouchableOpacity accessibilityRole="button" accessibilityLabel="Refresh event" onPress={() => void load('refresh')}><Ionicons name="refresh-outline" size={22} color={COLORS.textPrimary} /></TouchableOpacity>
      </View>
      <ScrollView
        contentContainerStyle={{ padding: SPACING.lg, paddingBottom: insets.bottom + 56 }}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void load('refresh')} tintColor={COLORS.primary} />}
      >
        <Text style={styles.eyebrow}>EVENT READY</Text>
        <Text style={styles.title}>{event.title}</Text>
        <Text style={styles.when}>{event.all_day ? event.local_date : `${event.local_date} · ${event.local_time}`}</Text>
        {!!event.location && <Text style={styles.meta}>{event.location}</Text>}
        <View style={styles.countdown}><Text style={styles.countdownText}>{countdown}</Text></View>
        {!!error && <View style={styles.error}><Ionicons name="alert-circle-outline" size={18} color={COLORS.error} /><Text style={styles.errorText}>{error}</Text></View>}

        {needsConfirmation && <View style={styles.confirmation} accessibilityLabel="Event confirmation">
          <Text style={styles.sectionTitle}>What are you preparing for?</Text>
          <Text style={styles.body}>{event.occasion_key ? `Suggested: ${event.occasion_key.replace(/_/g, ' ')}` : 'Choose an event type when you know it. You can leave this open for now.'}</Text>
          {!occasions.length && <TouchableOpacity accessibilityRole="button" accessibilityLabel="Choose event type" onPress={() => void loadOccasions()} style={styles.outline}><Text style={styles.outlineText}>Choose event type</Text></TouchableOpacity>}
          {!!occasions.length && <View style={styles.chips}>{occasions.map((occasion) => <TouchableOpacity key={occasion.key} accessibilityRole="button" accessibilityLabel={`Confirm ${occasion.label}`} disabled={confirming} onPress={() => void confirmOccasion(occasion)} style={styles.chip}><Text style={styles.chipText}>{occasion.label}</Text></TouchableOpacity>)}</View>}
        </View>}

        {ready.status === 'not_generated' && !effectivePast && !needsConfirmation && <View style={styles.ctaBlock}><Text style={styles.sectionTitle}>A little preparation can make the day easier.</Text><TouchableOpacity accessibilityRole="button" accessibilityLabel="Prepare for this event" disabled={generating} onPress={() => void generate()} style={[styles.primary, generating && styles.disabled]}><Text style={styles.primaryText}>{generating ? 'Preparing…' : 'Prepare for this event'}</Text></TouchableOpacity></View>}
        {effectivePast && <View style={styles.panel}><Text style={styles.sectionTitle}>This event has passed.</Text><Text style={styles.body}>Your event history stays here for reference.</Text></View>}

        {(ready.status === 'preparing' || ready.status === 'event_day') && <>
          <View style={styles.panel} accessibilityLabel="Your look">
            <Text style={styles.panelEyebrow}>YOUR LOOK</Text>
            {ready.style.selected_look ? <>
              <Text style={styles.panelTitle}>{ready.style.selected_look.title}</Text>
              <Text style={styles.panelBody}>{ready.style.selected_look.status}</Text>
              <View style={styles.inlineActions}><TouchableOpacity accessibilityRole="button" accessibilityLabel="Open look" onPress={() => router.push({ pathname: '/look', params: { id: ready.style.selected_look?.id } })}><Text style={styles.link}>Open look</Text></TouchableOpacity><TouchableOpacity accessibilityRole="button" accessibilityLabel="Choose another look" onPress={() => router.push({ pathname: '/style-me', params: { eventReadyEventId: eventId, occasionKey: event.occasion_key || '', eventDate: event.local_date, eventTitle: event.title, dressCode: event.dress_code_hint || '', location: event.location || '' } })}><Text style={styles.link}>Choose another look</Text></TouchableOpacity></View>
            </> : <><Text style={styles.panelTitle}>Choose your event look</Text><Text style={styles.panelBody}>Style will build from what you already own.</Text><TouchableOpacity accessibilityRole="button" accessibilityLabel="Choose a look" onPress={() => router.push({ pathname: '/style-me', params: { eventReadyEventId: eventId, occasionKey: event.occasion_key || '', eventDate: event.local_date, eventTitle: event.title, dressCode: event.dress_code_hint || '', location: event.location || '' } })} style={styles.outline}><Text style={styles.outlineText}>Choose a look</Text></TouchableOpacity></>}
          </View>
          <View style={styles.panel} accessibilityLabel="What needs attention"><Text style={styles.panelEyebrow}>WHAT NEEDS ATTENTION</Text>{ready.timeline.length ? ready.timeline.filter((action) => action.action_key !== 'context:confirm_event').map((action) => <EventReadyActionRow key={action.id} action={action} busy={pendingActions.has(action.id)} onToggle={() => void toggleAction(action.id, action.completed)} />) : <Text style={styles.body}>Nothing extra needs your attention right now.</Text>}</View>
          {!!ready.care && <CareSummary care={ready.care} onOpen={() => router.push('/(tabs)/care')} />}
          <View style={styles.panel} accessibilityLabel="Event context"><Text style={styles.panelEyebrow}>CONTEXT</Text>{ready.context.weather ? <><Text style={styles.panelBody}>Event-day weather: {ready.context.weather.condition}</Text>{!!ready.context.weather.attribution && <Text style={styles.meta}>{ready.context.weather.attribution}</Text>}</> : <Text style={styles.panelBody}>Event-day weather is not available yet.</Text>}{!!ready.context.air_quality && <><Text style={styles.panelBody}>Air quality: {ready.context.air_quality.category || ready.context.air_quality.aqi}</Text>{!!ready.context.air_quality.attribution && <Text style={styles.meta}>{ready.context.air_quality.attribution}</Text>}</>}</View>
          <Text style={styles.progress}>{ready.readiness.completed_actions} of {ready.readiness.total_actions} things done</Text>
        </>}
        {!effectivePast && <MissingInformation keys={ready.missing_information} />}
        {ready.status === 'not_generated' && needsConfirmation && selectedOccasion && <Text style={styles.note}>Confirm the event type above before preparing around it.</Text>}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.backgroundSecondary },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: COLORS.background },
  top: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingHorizontal: SPACING.lg, paddingTop: SPACING.md },
  topTitle: { fontFamily: FONTS.family.bodySemibold, color: COLORS.textPrimary },
  eyebrow: { fontFamily: FONTS.family.bodySemibold, color: COLORS.primary, fontSize: 10, letterSpacing: 1.3 },
  title: { fontFamily: FONTS.family.heading, color: COLORS.textPrimary, fontSize: 30, marginTop: 5 },
  when: { fontFamily: FONTS.family.bodyMedium, color: COLORS.textSecondary, fontSize: 14, marginTop: 8 },
  meta: { fontFamily: FONTS.family.body, color: COLORS.textMuted, fontSize: 12, marginTop: 4 },
  countdown: { alignSelf: 'flex-start', backgroundColor: COLORS.primaryLight, borderRadius: RADIUS.full, paddingHorizontal: 13, paddingVertical: 8, marginTop: SPACING.md },
  countdownText: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.primary },
  error: { flexDirection: 'row', alignItems: 'center', gap: 8, padding: SPACING.sm, borderRadius: RADIUS.md, backgroundColor: COLORS.errorLight, marginTop: SPACING.md },
  errorText: { flex: 1, fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.textPrimary },
  confirmation: { backgroundColor: COLORS.card, borderWidth: 1, borderColor: COLORS.border, borderRadius: RADIUS.lg, padding: SPACING.md, marginTop: SPACING.lg },
  ctaBlock: { marginTop: SPACING.lg },
  sectionTitle: { fontFamily: FONTS.family.headingMedium, fontSize: 18, color: COLORS.textPrimary },
  body: { fontFamily: FONTS.family.body, fontSize: 13, lineHeight: 19, color: COLORS.textSecondary, marginTop: 5 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: SPACING.sm },
  chip: { borderWidth: 1, borderColor: COLORS.border, backgroundColor: COLORS.backgroundSecondary, borderRadius: RADIUS.full, paddingHorizontal: 12, paddingVertical: 8 },
  chipText: { fontFamily: FONTS.family.bodyMedium, color: COLORS.textSecondary, fontSize: 12 },
  primary: { alignItems: 'center', justifyContent: 'center', backgroundColor: COLORS.primary, borderRadius: RADIUS.full, paddingVertical: 14, marginTop: SPACING.md },
  primaryText: { fontFamily: FONTS.family.bodySemibold, fontSize: 14, color: COLORS.white },
  outline: { alignSelf: 'flex-start', borderWidth: 1, borderColor: COLORS.border, borderRadius: RADIUS.full, paddingHorizontal: 14, paddingVertical: 9, marginTop: SPACING.md },
  outlineText: { fontFamily: FONTS.family.bodySemibold, fontSize: 12, color: COLORS.textPrimary },
  panel: { marginTop: SPACING.md },
  panelEyebrow: { fontFamily: FONTS.family.bodySemibold, fontSize: 10, letterSpacing: 1.1, color: COLORS.primary, marginBottom: 6 },
  panelTitle: { fontFamily: FONTS.family.headingMedium, fontSize: 19, color: COLORS.textPrimary },
  panelBody: { fontFamily: FONTS.family.body, fontSize: 12, lineHeight: 18, color: COLORS.textSecondary, marginTop: 4 },
  inlineActions: { flexDirection: 'row', gap: SPACING.md },
  link: { fontFamily: FONTS.family.bodySemibold, fontSize: 12, color: COLORS.primary, marginTop: 10 },
  progress: { fontFamily: FONTS.family.bodyMedium, fontSize: 12, color: COLORS.textMuted, textAlign: 'center', marginTop: SPACING.md },
  note: { fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.textMuted, textAlign: 'center', marginTop: SPACING.lg },
  disabled: { opacity: 0.55 },
});
