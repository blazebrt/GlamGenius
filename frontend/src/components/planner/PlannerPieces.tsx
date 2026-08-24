/**
 * Weekly planner building blocks.
 *
 * Seven days, always Monday to Sunday. Each card carries the four things that
 * make a week glanceable: what you are wearing, the weather, whether the day is
 * locked, and whether anything is repeating.
 *
 * Repetition is *shown*, never prevented. Wearing the same trousers twice in a
 * week is completely normal, and a planner that quietly forbids it is wrong
 * about how people dress.
 */
import React from 'react';
import { ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';

import { CalendarEvent, LaundryState, PlannerDay, WeeklyPlan } from '../../services/apiV2';
import { COLORS, FONTS, RADIUS, SPACING } from '../../theme/colors';
import { WEATHER_ICONS } from '../today/TodayPieces';

export const LAUNDRY_LABELS: Record<LaundryState, string> = {
  clean: 'Ready to wear',
  worn: 'Worn recently',
  in_wash: 'In the wash',
  unavailable: 'Not available',
};

/** Which items appear on more than one day, keyed by day. */
export const repeatedOn = (plan: WeeklyPlan): Record<string, string[]> => {
  const byDate: Record<string, string[]> = {};
  for (const row of plan.repetition?.repeated_items || []) {
    for (const value of row.dates) {
      byDate[value] = [...(byDate[value] || []), row.display_name];
    }
  }
  return byDate;
};

export function WeekEmpty({ onGenerate, busy }: { onGenerate: () => void; busy?: boolean }) {
  return (
    <View style={styles.empty} accessibilityLabel="No plan for this week yet">
      <Ionicons name="calendar-outline" size={30} color={COLORS.primary} />
      <Text style={styles.cardTitle}>Plan your week</Text>
      <Text style={styles.body}>
        Seven days from what you own, taking the weather, your calendar and what you wore recently into account.
      </Text>
      <TouchableOpacity
        accessibilityRole="button"
        accessibilityLabel="Generate this week"
        onPress={onGenerate}
        disabled={busy}
        style={[styles.primary, busy && styles.disabled]}
      >
        <Text style={styles.primaryText}>{busy ? 'Building your week…' : 'Generate this week'}</Text>
      </TouchableOpacity>
    </View>
  );
}

export const occasionLabel = (key: string | null): string | null => {
  if (!key) return null;
  return key.replace(/_/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
};

export function UpcomingEventCard({ event, onPress }: { event: CalendarEvent; onPress: () => void }) {
  const occasion = event.user_confirmed ? occasionLabel(event.occasion_key) : null;
  return (
    <TouchableOpacity
      accessibilityRole="button"
      accessibilityLabel={`Open event ${event.title}`}
      onPress={onPress}
      style={styles.eventCard}
    >
      <View style={styles.eventCardMain}>
        <Text style={styles.eventTitle}>{event.title}</Text>
        <Text style={styles.eventWhen}>
          {event.all_day ? event.local_date : `${event.local_date} · ${event.local_time}`}
        </Text>
        {!!event.location && <Text style={styles.eventMeta}>{event.location}</Text>}
        {!!occasion && <Text style={styles.eventMeta}>{occasion}</Text>}
        {!event.user_confirmed && <Text style={styles.eventNeedsConfirmation}>Event type needs confirmation</Text>}
      </View>
      <Ionicons name="chevron-forward" size={18} color={COLORS.textMuted} />
    </TouchableOpacity>
  );
}

export function UpcomingEvents({ events, onEventPress, onAdd, loading = false, error, onRetry }: {
  events: CalendarEvent[]; onEventPress: (event: CalendarEvent) => void; onAdd: () => void;
  loading?: boolean; error?: string | null; onRetry?: () => void;
}) {
  return (
    <View accessibilityLabel="Upcoming events" style={styles.upcoming}>
      <View style={styles.upcomingHeader}>
        <Text style={styles.sectionTitle}>Upcoming events</Text>
        <TouchableOpacity accessibilityRole="button" accessibilityLabel="Add an event" onPress={onAdd}>
          <Text style={styles.addEventLink}>Add an event</Text>
        </TouchableOpacity>
      </View>
      {loading ? (
        <View style={styles.eventEmpty}><Text style={styles.bodyMuted}>Loading upcoming events…</Text></View>
      ) : error ? (
        <View style={styles.eventEmpty} accessibilityLabel="Upcoming events unavailable">
          <Text style={styles.bodyMuted}>{error}</Text>
          {!!onRetry && <TouchableOpacity accessibilityRole="button" accessibilityLabel="Retry upcoming events" onPress={onRetry}><Text style={styles.addEventLink}>Retry</Text></TouchableOpacity>}
        </View>
      ) : events.length ? events.slice(0, 5).map((event) => (
        <UpcomingEventCard key={event.id} event={event} onPress={() => onEventPress(event)} />
      )) : (
        <View style={styles.eventEmpty}>
          <Text style={styles.bodyMuted}>No important events here yet.</Text>
          <Text style={styles.eventEmptyCopy}>Add one when you want GlamGenius to help you prepare around it.</Text>
        </View>
      )}
    </View>
  );
}

export function GoogleCalendarControl({
  connected, label, lastSyncedAt, busy, onConnect, onSync, onDisconnect,
}: {
  connected: boolean; label?: string | null; lastSyncedAt?: string | null; busy?: boolean;
  onConnect: () => void; onSync: () => void; onDisconnect: () => void;
}) {
  return (
    <View style={styles.googleCard} accessibilityLabel="Google Calendar">
      <View style={{ flex: 1 }}>
        <Text style={styles.sectionTitle}>{connected ? 'Google Calendar connected' : 'Connect Google Calendar'}</Text>
        <Text style={styles.bodyMuted}>{connected ? (label || 'Google Calendar') : 'Bring in upcoming plans so GlamGenius can help you prepare around them.'}</Text>
        {!!connected && !!lastSyncedAt && <Text style={styles.eventMeta}>Last synced {new Date(lastSyncedAt).toLocaleString()}</Text>}
      </View>
      {connected ? (
        <View style={styles.googleActions}>
          <TouchableOpacity accessibilityRole="button" accessibilityLabel="Refresh Google Calendar" onPress={onSync} disabled={busy}><Text style={styles.addEventLink}>{busy ? 'Syncing…' : 'Refresh'}</Text></TouchableOpacity>
          <TouchableOpacity accessibilityRole="button" accessibilityLabel="Disconnect Google Calendar" onPress={onDisconnect} disabled={busy}><Text style={styles.disconnectLink}>Disconnect</Text></TouchableOpacity>
        </View>
      ) : (
        <TouchableOpacity accessibilityRole="button" accessibilityLabel="Connect Google Calendar" onPress={onConnect} disabled={busy} style={styles.googleButton}><Text style={styles.primaryText}>{busy ? 'Opening…' : 'Connect'}</Text></TouchableOpacity>
      )}
    </View>
  );
}

export function DayCard({ day, repeats, selected, onPress, onLock, onRegenerate }: {
  day: PlannerDay;
  repeats?: string[];
  selected?: boolean;
  onPress?: () => void;
  onLock?: () => void;
  onRegenerate?: () => void;
}) {
  const pieces = day.owned_items.filter((piece) => piece.slot === 'clothing' || piece.slot === 'shoes');
  const summary = pieces.map((piece) => piece.display_name).join(' · ');
  return (
    <View style={[styles.day, selected && styles.daySelected]} accessibilityLabel={`${day.weekday} plan`}>
      <TouchableOpacity
        accessibilityRole="button"
        accessibilityLabel={`Open ${day.weekday}`}
        onPress={onPress}
        disabled={!onPress}
        style={styles.dayHead}
      >
        <View style={{ flex: 1 }}>
          <Text style={styles.dayName}>{day.weekday}</Text>
          <Text style={styles.dayDate}>{day.plan_date}</Text>
        </View>
        {day.weather && (
          <View style={styles.chip} accessibilityLabel={`Weather ${day.weather.condition}`}>
            <Ionicons name={(WEATHER_ICONS[day.weather.condition] || 'partly-sunny-outline') as any} size={12} color={COLORS.textSecondary} />
            <Text style={styles.chipText}>{day.weather.condition}</Text>
          </View>
        )}
        {day.locked && (
          <View style={styles.lockChip} accessibilityLabel={`${day.weekday} is locked`}>
            <Ionicons name="lock-closed" size={11} color={COLORS.primary} />
            <Text style={styles.lockText}>Locked</Text>
          </View>
        )}
      </TouchableOpacity>

      {summary ? (
        <Text style={styles.summary}>{summary}</Text>
      ) : (
        <Text style={styles.bodyMuted}>Nothing planned for this day yet.</Text>
      )}

      {!!day.optional_addition_count && (
        <Text style={styles.optional}>
          {day.optional_addition_count} optional addition{day.optional_addition_count > 1 ? 's' : ''} you do not own
        </Text>
      )}

      {!!repeats?.length && (
        <View style={styles.repeat} accessibilityLabel={`Repeats on ${day.weekday}`}>
          <Ionicons name="repeat" size={13} color={COLORS.textSecondary} />
          <Text style={styles.repeatText}>Also worn another day: {repeats.join(', ')}</Text>
        </View>
      )}

      {day.worn && (
        <View style={styles.wornChip} accessibilityLabel={`${day.weekday} already worn`}>
          <Ionicons name="checkmark-circle" size={12} color={COLORS.success} />
          <Text style={styles.wornText}>Worn</Text>
        </View>
      )}

      <View style={styles.dayActions}>
        {onLock && (
          <TouchableOpacity
            accessibilityRole="button"
            accessibilityLabel={day.locked ? `Unlock ${day.weekday}` : `Lock ${day.weekday}`}
            onPress={onLock}
            style={styles.miniButton}
          >
            <Text style={styles.miniButtonText}>{day.locked ? 'Unlock' : 'Lock'}</Text>
          </TouchableOpacity>
        )}
        {onRegenerate && !day.locked && (
          <TouchableOpacity
            accessibilityRole="button"
            accessibilityLabel={`Regenerate ${day.weekday}`}
            onPress={onRegenerate}
            style={styles.miniButton}
          >
            <Text style={styles.miniButtonText}>Redo</Text>
          </TouchableOpacity>
        )}
      </View>
    </View>
  );
}

/** The move control. Two taps rather than a drag, which is far easier to reach. */
export function SwapBar({ from, days, onPick, onCancel }: {
  from: PlannerDay; days: PlannerDay[]; onPick: (plan_date: string) => void; onCancel: () => void;
}) {
  const targets = days.filter((day) => day.plan_date !== from.plan_date && !day.locked);
  return (
    <View style={styles.swapBar} accessibilityLabel={`Move ${from.weekday}`}>
      <View style={styles.swapHead}>
        <Text style={styles.swapTitle}>Move {from.weekday} to…</Text>
        <TouchableOpacity accessibilityRole="button" accessibilityLabel="Cancel move" onPress={onCancel}>
          <Ionicons name="close" size={19} color={COLORS.textMuted} />
        </TouchableOpacity>
      </View>
      {targets.length === 0 ? (
        <Text style={styles.bodyMuted}>Every other day is locked. Unlock one to move this.</Text>
      ) : (
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chipRow}>
          {targets.map((day) => (
            <TouchableOpacity
              key={day.plan_date}
              accessibilityRole="button"
              accessibilityLabel={`Swap with ${day.weekday}`}
              onPress={() => onPick(day.plan_date)}
              style={styles.answerChip}
            >
              <Text style={styles.answerChipText}>{day.weekday}</Text>
            </TouchableOpacity>
          ))}
        </ScrollView>
      )}
    </View>
  );
}

export function LaundryPanel({ plan }: { plan: WeeklyPlan }) {
  const rows = plan.laundry || [];
  if (!rows.length) return null;
  return (
    <View style={styles.panel} accessibilityLabel="Items not available">
      <Text style={styles.panelTitle}>Not available this week</Text>
      {rows.map((row) => (
        <Text key={row.item_id} style={styles.bodyMuted}>
          • {LAUNDRY_LABELS[row.state]}{row.available_from ? ` until ${row.available_from}` : ''}
        </Text>
      ))}
    </View>
  );
}

export function RepetitionPanel({ plan }: { plan: WeeklyPlan }) {
  const rows = plan.repetition?.repeated_items || [];
  if (!rows.length) return null;
  return (
    <View style={styles.panel} accessibilityLabel="Repeated this week">
      <Text style={styles.panelTitle}>Worn more than once</Text>
      {rows.map((row) => (
        <Text key={row.display_name} style={styles.bodyMuted}>• {row.display_name} — {row.times} days</Text>
      ))}
      <Text style={styles.note}>{plan.repetition?.note}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  empty: { alignItems: 'center', gap: 6, padding: SPACING.lg, backgroundColor: COLORS.card, borderRadius: RADIUS.xl, borderWidth: 1, borderColor: COLORS.border },
  cardTitle: { fontFamily: FONTS.family.headingMedium, fontSize: 20, color: COLORS.textPrimary, marginTop: 3 },
  body: { fontFamily: FONTS.family.body, fontSize: 13, lineHeight: 19, color: COLORS.textSecondary, textAlign: 'center' },
  bodyMuted: { fontFamily: FONTS.family.body, fontSize: 12, lineHeight: 18, color: COLORS.textMuted, marginTop: 3 },
  day: { backgroundColor: COLORS.card, borderRadius: RADIUS.lg, borderWidth: 1, borderColor: COLORS.border, padding: SPACING.md, marginBottom: 10 },
  daySelected: { borderColor: COLORS.primary, borderWidth: 2 },
  dayHead: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  dayName: { fontFamily: FONTS.family.bodySemibold, fontSize: 15, color: COLORS.textPrimary },
  dayDate: { fontFamily: FONTS.family.body, fontSize: 11, color: COLORS.textMuted, marginTop: 1 },
  chip: { flexDirection: 'row', alignItems: 'center', gap: 4, paddingHorizontal: 8, paddingVertical: 4, borderRadius: RADIUS.full, backgroundColor: COLORS.backgroundSecondary },
  chipText: { fontFamily: FONTS.family.bodyMedium, fontSize: 10, color: COLORS.textSecondary, textTransform: 'capitalize' },
  lockChip: { flexDirection: 'row', alignItems: 'center', gap: 4, paddingHorizontal: 8, paddingVertical: 4, borderRadius: RADIUS.full, backgroundColor: COLORS.primaryLight },
  lockText: { fontFamily: FONTS.family.bodySemibold, fontSize: 10, color: COLORS.primary },
  wornChip: { flexDirection: 'row', alignItems: 'center', gap: 4, marginTop: 6 },
  wornText: { fontFamily: FONTS.family.bodySemibold, fontSize: 11, color: COLORS.success },
  summary: { fontFamily: FONTS.family.body, fontSize: 13, lineHeight: 19, color: COLORS.textSecondary, marginTop: 8 },
  optional: { fontFamily: FONTS.family.bodyMedium, fontSize: 11, color: COLORS.warning, marginTop: 5 },
  repeat: { flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 7 },
  repeatText: { flex: 1, fontFamily: FONTS.family.body, fontSize: 11, color: COLORS.textSecondary },
  dayActions: { flexDirection: 'row', gap: 8, marginTop: 10 },
  miniButton: { paddingHorizontal: 12, paddingVertical: 7, borderRadius: RADIUS.full, backgroundColor: COLORS.backgroundSecondary },
  miniButtonText: { fontFamily: FONTS.family.bodySemibold, fontSize: 11, color: COLORS.textPrimary },
  swapBar: { backgroundColor: COLORS.primaryLight, borderRadius: RADIUS.lg, padding: SPACING.md, marginBottom: 10 },
  swapHead: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  swapTitle: { fontFamily: FONTS.family.bodySemibold, fontSize: 14, color: COLORS.textPrimary },
  chipRow: { gap: 8, paddingTop: SPACING.sm, paddingRight: SPACING.md },
  answerChip: { paddingHorizontal: 14, paddingVertical: 9, borderRadius: RADIUS.full, backgroundColor: COLORS.primary },
  answerChipText: { fontFamily: FONTS.family.bodySemibold, fontSize: 12, color: COLORS.white },
  panel: { backgroundColor: COLORS.card, borderRadius: RADIUS.lg, borderWidth: 1, borderColor: COLORS.border, padding: SPACING.md, marginTop: SPACING.md },
  panelTitle: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.textPrimary },
  note: { fontFamily: FONTS.family.body, fontSize: 11, color: COLORS.textMuted, marginTop: 8 },
  primary: { alignItems: 'center', justifyContent: 'center', backgroundColor: COLORS.primary, borderRadius: RADIUS.full, paddingHorizontal: 22, paddingVertical: 13, marginTop: SPACING.md },
  primaryText: { fontFamily: FONTS.family.bodySemibold, fontSize: 14, color: COLORS.white },
  disabled: { opacity: 0.6 },
  upcoming: { marginBottom: SPACING.xl },
  googleCard: { flexDirection: 'row', alignItems: 'center', gap: SPACING.md, padding: SPACING.md, borderRadius: RADIUS.lg, backgroundColor: COLORS.background, marginBottom: SPACING.xl, borderWidth: 1, borderColor: COLORS.border },
  googleActions: { alignItems: 'flex-end', gap: 8 },
  googleButton: { borderRadius: RADIUS.full, paddingHorizontal: 14, paddingVertical: 10, backgroundColor: COLORS.primary },
  disconnectLink: { fontFamily: FONTS.family.bodySemibold, fontSize: 12, color: COLORS.error },
  upcomingHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: SPACING.sm },
  sectionTitle: { fontFamily: FONTS.family.headingMedium, fontSize: 20, color: COLORS.textPrimary },
  addEventLink: { fontFamily: FONTS.family.bodySemibold, fontSize: 12, color: COLORS.primary },
  eventCard: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', backgroundColor: COLORS.card, borderRadius: RADIUS.lg, borderWidth: 1, borderColor: COLORS.border, padding: SPACING.md, marginBottom: 8 },
  eventCardMain: { flex: 1, paddingRight: 8 },
  eventTitle: { fontFamily: FONTS.family.bodySemibold, fontSize: 14, color: COLORS.textPrimary },
  eventWhen: { fontFamily: FONTS.family.bodyMedium, fontSize: 12, color: COLORS.textSecondary, marginTop: 4 },
  eventMeta: { fontFamily: FONTS.family.body, fontSize: 11, color: COLORS.textMuted, marginTop: 3 },
  eventNeedsConfirmation: { fontFamily: FONTS.family.bodyMedium, fontSize: 11, color: COLORS.warning, marginTop: 5 },
  eventEmpty: { backgroundColor: COLORS.card, borderRadius: RADIUS.lg, borderWidth: 1, borderColor: COLORS.border, padding: SPACING.md },
  eventEmptyCopy: { fontFamily: FONTS.family.body, fontSize: 12, lineHeight: 18, color: COLORS.textMuted, marginTop: 5 },
});
