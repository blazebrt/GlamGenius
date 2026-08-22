/**
 * Today building blocks.
 *
 * The rule this file exists to enforce: **Today is a short list, not a
 * dashboard.** The backend already decides which optional modules have anything
 * relevant to say, and this renders exactly what it sent. There is no component
 * here that shows a module unconditionally.
 *
 * The three states that are easy to get wrong, and are handled explicitly:
 * loading, stale (showing yesterday's cached plan while the new one arrives),
 * and not-enough-inventory — which is not an error, because nothing failed.
 */
import React from 'react';
import { ActivityIndicator, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';

import { Clarification, DailyPlan, LookPiece, PlanAction, PlanModule, WeatherSnapshot } from '../../services/apiV2';
import { COLORS, FONTS, RADIUS, SPACING } from '../../theme/colors';

export const MODULE_META: Record<PlanModule, { label: string; icon: string }> = {
  outfit: { label: 'Outfit', icon: 'shirt-outline' },
  skincare: { label: 'Skincare', icon: 'sparkles-outline' },
  hair: { label: 'Hair', icon: 'cut-outline' },
  perfume: { label: 'Perfume', icon: 'flower-outline' },
  hydration: { label: 'Hydration', icon: 'water-outline' },
  nutrition: { label: 'Nutrition', icon: 'nutrition-outline' },
  shopping: { label: 'Shopping', icon: 'pricetag-outline' },
};

export const WEATHER_ICONS: Record<string, string> = {
  hot: 'sunny-outline', warm: 'partly-sunny-outline', mild: 'partly-sunny-outline',
  cool: 'cloud-outline', cold: 'snow-outline', humid: 'thermometer-outline',
  rainy: 'rainy-outline', windy: 'flag-outline',
};

/** A plan older than this is worth flagging as possibly out of date. */
export const STALE_AFTER_MINUTES = 12 * 60;

export const isStale = (plan: DailyPlan, now: Date = new Date()): boolean => {
  if (!plan.computed_at) return false;
  const age = (now.getTime() - new Date(plan.computed_at).getTime()) / 60000;
  return age > STALE_AFTER_MINUTES;
};

export const confidenceLabel = (value: number): string => {
  if (value >= 0.7) return 'Built from a lot of what you told us';
  if (value >= 0.45) return 'A few details are missing';
  return 'We are working with very little — add more and this gets better';
};

// --- States -----------------------------------------------------------------

export function TodayLoading() {
  return (
    <View style={styles.centre} accessibilityLabel="Loading today" accessibilityRole="progressbar">
      <ActivityIndicator size="large" color={COLORS.primary} />
      <Text style={styles.body}>Getting your day ready</Text>
    </View>
  );
}

/** Shown over a cached plan while a fresher one is on its way. */
export function StaleNotice({ onRefresh }: { onRefresh: () => void }) {
  return (
    <View style={styles.stale} accessibilityLabel="This plan may be out of date">
      <Ionicons name="time-outline" size={18} color={COLORS.warning} />
      <Text style={styles.staleText}>This was worked out a while ago and may be out of date.</Text>
      <TouchableOpacity accessibilityRole="button" accessibilityLabel="Refresh today" onPress={onRefresh}>
        <Text style={styles.link}>Refresh</Text>
      </TouchableOpacity>
    </View>
  );
}

export function OfflineNotice() {
  return (
    <View style={styles.stale} accessibilityLabel="Showing your saved plan">
      <Ionicons name="cloud-offline-outline" size={18} color={COLORS.textSecondary} />
      <Text style={styles.staleText}>You are offline. This is the plan we saved for you.</Text>
    </View>
  );
}

export function NeedsInventory({ plan, onAdd }: { plan: DailyPlan; onAdd: () => void }) {
  const first = plan.primary[0];
  return (
    <View style={styles.empty} accessibilityLabel="Not enough inventory yet">
      <Ionicons name="shirt-outline" size={30} color={COLORS.primary} />
      <Text style={styles.cardTitle}>{first?.title || 'Add a few things you own'}</Text>
      <Text style={styles.body}>{first?.body || 'We will not invent clothes you do not have.'}</Text>
      <TouchableOpacity accessibilityRole="button" accessibilityLabel="Add items to inventory" onPress={onAdd} style={styles.primary}>
        <Text style={styles.primaryText}>Add items</Text>
      </TouchableOpacity>
    </View>
  );
}

// --- Header -----------------------------------------------------------------

export function TodayHeader({ plan }: { plan: DailyPlan }) {
  return (
    <View accessibilityLabel="Today header">
      <Text style={styles.eyebrow}>{plan.weekday.toUpperCase()}</Text>
      <Text style={styles.title}>{plan.headline}</Text>
      <View style={styles.metaRow}>
        {plan.weather && <WeatherChip weather={plan.weather} />}
        {plan.generated_from === 'cache' && (
          <View style={styles.chip} accessibilityLabel="Loaded from your saved plan">
            <Ionicons name="flash-outline" size={12} color={COLORS.textSecondary} />
            <Text style={styles.chipText}>Saved plan</Text>
          </View>
        )}
      </View>
    </View>
  );
}

export function WeatherChip({ weather }: { weather: WeatherSnapshot }) {
  const range = weather.temp_max_c != null ? ` · ${Math.round(weather.temp_max_c)}°` : '';
  return (
    <View style={styles.chip} accessibilityLabel={`Weather ${weather.condition}`}>
      <Ionicons name={(WEATHER_ICONS[weather.condition] || 'partly-sunny-outline') as any} size={13} color={COLORS.textSecondary} />
      <Text style={styles.chipText}>{weather.condition}{range}</Text>
      {!!weather.attribution && <Text style={styles.chipText}>{weather.attribution}</Text>}
    </View>
  );
}

// --- The one question -------------------------------------------------------

/**
 * Progressive automation: exactly one question, only when the answer would
 * change the plan. Never a form.
 */
export function ClarificationCard({ clarification, onAnswer }: {
  clarification: Clarification; onAnswer: (value: string) => void;
}) {
  return (
    <View style={styles.clarify} accessibilityLabel="One quick question">
      <Text style={styles.eyebrow}>ONE QUICK QUESTION</Text>
      <Text style={styles.cardTitle}>{clarification.question}</Text>
      <Text style={styles.body}>{clarification.why}</Text>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chipRow}>
        {clarification.options.map((option) => (
          <TouchableOpacity
            key={option.value}
            accessibilityRole="button"
            accessibilityLabel={option.label}
            onPress={() => onAnswer(option.value)}
            style={styles.answerChip}
          >
            <Text style={styles.answerChipText}>{option.label}</Text>
          </TouchableOpacity>
        ))}
      </ScrollView>
    </View>
  );
}

// --- The outfit -------------------------------------------------------------

export function OutfitCard({ plan, onSwap, onWore, onNotForMe, onUnavailable }: {
  plan: DailyPlan;
  onSwap?: (piece: LookPiece) => void;
  onWore?: () => void;
  onNotForMe?: () => void;
  onUnavailable?: (piece: LookPiece) => void;
}) {
  const look = plan.outfit;
  if (!look) return null;
  return (
    <View style={styles.outfit} accessibilityLabel="Today's outfit">
      <Text style={styles.eyebrow}>WEAR THIS</Text>
      <Text style={styles.cardTitle}>{look.title}</Text>

      {look.owned_items.map((piece) => (
        <View key={piece.id} style={styles.pieceRow} accessibilityLabel={`${piece.display_name}, in your inventory`}>
          <Ionicons name="checkmark-circle" size={17} color={COLORS.success} />
          <View style={{ flex: 1 }}>
            <Text style={styles.pieceName}>{piece.display_name}</Text>
            <Text style={styles.ownedTag}>{piece.label}</Text>
          </View>
          {onSwap && (
            <TouchableOpacity accessibilityRole="button" accessibilityLabel={`Swap ${piece.display_name}`} onPress={() => onSwap(piece)} style={styles.miniButton}>
              <Text style={styles.miniButtonText}>Swap</Text>
            </TouchableOpacity>
          )}
          {onUnavailable && (
            <TouchableOpacity accessibilityRole="button" accessibilityLabel={`${piece.display_name} is not available`} onPress={() => onUnavailable(piece)} style={styles.miniButton}>
              <Text style={styles.miniButtonText}>Can’t wear</Text>
            </TouchableOpacity>
          )}
        </View>
      ))}

      {look.optional_additions.map((piece) => (
        <View key={piece.id} style={styles.optionalRow} accessibilityLabel={`${piece.display_name}, optional addition you do not own`}>
          <Ionicons name="add-circle-outline" size={17} color={COLORS.warning} />
          <View style={{ flex: 1 }}>
            <Text style={styles.pieceName}>{piece.display_name}</Text>
            <Text style={styles.optionalTag}>{piece.label}</Text>
          </View>
        </View>
      ))}

      {!!look.why_it_works && (
        <View style={styles.why} accessibilityLabel="Why this works">
          <Text style={styles.whyTitle}>Why this</Text>
          <Text style={styles.body}>{look.why_it_works}</Text>
        </View>
      )}

      <View style={styles.actionRow}>
        {onWore && (
          <TouchableOpacity accessibilityRole="button" accessibilityLabel="I wore this" onPress={onWore} style={[styles.primary, plan.worn && styles.primaryDone]}>
            <Ionicons name={plan.worn ? 'checkmark' : 'checkmark-outline'} size={16} color={COLORS.white} />
            <Text style={styles.primaryText}>{plan.worn ? 'Worn' : 'I wore this'}</Text>
          </TouchableOpacity>
        )}
        {onNotForMe && (
          <TouchableOpacity accessibilityRole="button" accessibilityLabel="Show me something else" onPress={onNotForMe} style={styles.outline}>
            <Text style={styles.outlineText}>Something else</Text>
          </TouchableOpacity>
        )}
      </View>
    </View>
  );
}

// --- Actions ----------------------------------------------------------------

export function ActionRow({ action, onComplete }: { action: PlanAction; onComplete?: () => void }) {
  const meta = MODULE_META[action.module];
  return (
    <View style={[styles.action, action.completed && styles.actionDone]} accessibilityLabel={action.title}>
      <Ionicons name={(meta?.icon || 'ellipse-outline') as any} size={19} color={action.completed ? COLORS.textMuted : COLORS.primary} />
      <View style={{ flex: 1 }}>
        <Text style={[styles.actionTitle, action.completed && styles.struck]}>{action.title}</Text>
        {!!action.body && <Text style={styles.body}>{action.body}</Text>}
        {!!action.relevance && <Text style={styles.relevance}>{action.relevance}</Text>}
      </View>
      {onComplete && (
        <TouchableOpacity
          accessibilityRole="checkbox"
          accessibilityState={{ checked: action.completed }}
          accessibilityLabel={`Mark ${action.title} ${action.completed ? 'not done' : 'done'}`}
          onPress={onComplete}
          style={styles.check}
        >
          <Ionicons name={action.completed ? 'checkbox' : 'square-outline'} size={22} color={action.completed ? COLORS.primary : COLORS.textMuted} />
        </TouchableOpacity>
      )}
    </View>
  );
}

/**
 * The optional modules, collapsed behind a disclosure. They are extra by
 * definition, so they must not compete with the outfit for attention.
 */
export function OptionalModules({ actions, expanded, onToggle, onComplete }: {
  actions: PlanAction[];
  expanded: boolean;
  onToggle: () => void;
  onComplete?: (action: PlanAction) => void;
}) {
  if (!actions.length) return null;
  return (
    <View accessibilityLabel="Also today">
      <TouchableOpacity
        accessibilityRole="button"
        accessibilityLabel={expanded ? 'Hide the rest of today' : `Show ${actions.length} more for today`}
        accessibilityState={{ expanded }}
        onPress={onToggle}
        style={styles.disclosure}
      >
        <Text style={styles.disclosureText}>Also today · {actions.length}</Text>
        <Ionicons name={expanded ? 'chevron-up' : 'chevron-down'} size={18} color={COLORS.textSecondary} />
      </TouchableOpacity>
      {expanded && actions.map((action) => (
        <ActionRow key={action.id} action={action} onComplete={onComplete ? () => onComplete(action) : undefined} />
      ))}
    </View>
  );
}

export function MissingInformation({ plan }: { plan: DailyPlan }) {
  if (!plan.missing_information.length) return null;
  return (
    <View style={styles.missing} accessibilityLabel="What we did not know">
      <Text style={styles.relevance}>{confidenceLabel(plan.confidence)}</Text>
      {plan.missing_information.map((note, index) => (
        <Text key={index} style={styles.relevance}>• {note}</Text>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  centre: { alignItems: 'center', gap: SPACING.sm, padding: SPACING.xl },
  eyebrow: { fontFamily: FONTS.family.bodySemibold, color: COLORS.primary, fontSize: 10, letterSpacing: 1.3 },
  title: { fontFamily: FONTS.family.heading, color: COLORS.textPrimary, fontSize: 28, marginTop: 4 },
  metaRow: { flexDirection: 'row', gap: 8, marginTop: 10, flexWrap: 'wrap' },
  chip: { flexDirection: 'row', alignItems: 'center', gap: 5, paddingHorizontal: 10, paddingVertical: 5, borderRadius: RADIUS.full, backgroundColor: COLORS.backgroundSecondary },
  chipText: { fontFamily: FONTS.family.bodyMedium, fontSize: 11, color: COLORS.textSecondary, textTransform: 'capitalize' },
  stale: { flexDirection: 'row', alignItems: 'center', gap: 8, padding: SPACING.sm, borderRadius: RADIUS.md, backgroundColor: COLORS.warningLight, marginBottom: SPACING.md },
  staleText: { flex: 1, fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.textSecondary },
  link: { fontFamily: FONTS.family.bodySemibold, fontSize: 12, color: COLORS.primary },
  empty: { alignItems: 'center', gap: 6, padding: SPACING.lg, backgroundColor: COLORS.card, borderRadius: RADIUS.xl, borderWidth: 1, borderColor: COLORS.border },
  clarify: { backgroundColor: COLORS.primaryLight, borderRadius: RADIUS.xl, padding: SPACING.lg, marginBottom: SPACING.md },
  chipRow: { gap: 8, paddingTop: SPACING.sm, paddingRight: SPACING.md },
  answerChip: { paddingHorizontal: 14, paddingVertical: 9, borderRadius: RADIUS.full, backgroundColor: COLORS.primary },
  answerChipText: { fontFamily: FONTS.family.bodySemibold, fontSize: 12, color: COLORS.white },
  outfit: { backgroundColor: COLORS.card, borderRadius: RADIUS.xl, padding: SPACING.lg, borderWidth: 1, borderColor: COLORS.border, marginBottom: SPACING.md },
  cardTitle: { fontFamily: FONTS.family.headingMedium, fontSize: 20, color: COLORS.textPrimary, marginTop: 3, marginBottom: 4 },
  pieceRow: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 9, borderBottomWidth: 1, borderBottomColor: COLORS.borderLight },
  optionalRow: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 9, paddingHorizontal: 10, marginTop: 6, borderRadius: RADIUS.md, backgroundColor: COLORS.warningLight, borderWidth: 1, borderStyle: 'dashed', borderColor: COLORS.warningMuted },
  pieceName: { fontFamily: FONTS.family.bodyMedium, fontSize: 14, color: COLORS.textPrimary },
  ownedTag: { fontFamily: FONTS.family.body, fontSize: 11, color: COLORS.success, marginTop: 2 },
  optionalTag: { fontFamily: FONTS.family.bodySemibold, fontSize: 11, color: COLORS.warning, marginTop: 2 },
  miniButton: { paddingHorizontal: 10, paddingVertical: 6, borderRadius: RADIUS.full, backgroundColor: COLORS.primaryLight },
  miniButtonText: { fontFamily: FONTS.family.bodySemibold, fontSize: 11, color: COLORS.primary },
  why: { marginTop: SPACING.md, paddingTop: SPACING.md, borderTopWidth: 1, borderTopColor: COLORS.borderLight },
  whyTitle: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.textPrimary, marginBottom: 4 },
  body: { fontFamily: FONTS.family.body, fontSize: 13, lineHeight: 19, color: COLORS.textSecondary },
  actionRow: { flexDirection: 'row', gap: 8, marginTop: SPACING.md, flexWrap: 'wrap' },
  primary: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 7, backgroundColor: COLORS.primary, borderRadius: RADIUS.full, paddingHorizontal: 20, paddingVertical: 12, marginTop: SPACING.sm },
  primaryDone: { backgroundColor: COLORS.success },
  primaryText: { fontFamily: FONTS.family.bodySemibold, fontSize: 14, color: COLORS.white },
  outline: { alignItems: 'center', justifyContent: 'center', borderRadius: RADIUS.full, paddingHorizontal: 18, paddingVertical: 12, borderWidth: 1, borderColor: COLORS.border, marginTop: SPACING.sm },
  outlineText: { fontFamily: FONTS.family.bodySemibold, fontSize: 14, color: COLORS.textPrimary },
  action: { flexDirection: 'row', alignItems: 'flex-start', gap: 11, backgroundColor: COLORS.card, borderRadius: RADIUS.lg, borderWidth: 1, borderColor: COLORS.border, padding: SPACING.md, marginBottom: 8 },
  actionDone: { opacity: 0.6 },
  actionTitle: { fontFamily: FONTS.family.bodySemibold, fontSize: 14, color: COLORS.textPrimary },
  struck: { textDecorationLine: 'line-through' },
  relevance: { fontFamily: FONTS.family.body, fontSize: 11, lineHeight: 16, color: COLORS.textMuted, marginTop: 3 },
  check: { paddingLeft: 4 },
  disclosure: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingVertical: 12, marginTop: SPACING.sm },
  disclosureText: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.textSecondary },
  missing: { marginTop: SPACING.md, padding: SPACING.sm, borderRadius: RADIUS.md, backgroundColor: COLORS.backgroundSecondary },
});
