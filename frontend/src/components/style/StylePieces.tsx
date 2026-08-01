/**
 * Style Me building blocks.
 *
 * The one rule these components exist to enforce: **owned and optional never
 * look the same.** A piece the user owns and a piece they would have to buy are
 * rendered by different components, with different colour, a different icon and
 * different words, so the difference survives a glance.
 */
import React from 'react';
import { ActivityIndicator, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';

import {
  ComfortPreference, Look, LookPiece, LookSlot, LookVariant, OccasionDefinition, OccasionQuestion,
  ReviseReason, StyleResult, WeatherCondition,
} from '../../services/apiV2';
import { COLORS, FONTS, RADIUS, SPACING } from '../../theme/colors';

export const OCCASION_ICONS: Record<string, string> = {
  everyday: 'sunny-outline', office: 'briefcase-outline', wedding: 'heart-outline',
  festival: 'sparkles-outline', interview: 'document-text-outline', date: 'wine-outline',
  party: 'musical-notes-outline', conference: 'people-outline', business_meeting: 'easel-outline',
  vacation: 'airplane-outline', travel: 'bus-outline', photoshoot: 'camera-outline',
  birthday: 'gift-outline', college: 'school-outline', gym: 'barbell-outline', home: 'home-outline',
};

export const SLOT_LABELS: Record<LookSlot, string> = {
  clothing: 'Clothing', shoes: 'Shoes', accessories: 'Accessories',
  perfume: 'Perfume', hair: 'Hair', grooming: 'Grooming',
};

export const VARIANT_BLURB: Record<LookVariant, string> = {
  recommended: 'The balanced choice',
  comfortable: 'Comfort comes first',
  expressive: 'Makes more of a statement',
};

export const REVISE_REASONS: { key: ReviseReason; label: string }[] = [
  { key: 'too_formal', label: 'Too formal' },
  { key: 'too_casual', label: 'Too casual' },
  { key: 'too_warm', label: 'Too warm' },
  { key: 'too_cold', label: 'Too cold' },
  { key: 'not_my_style', label: 'Not my style' },
  { key: 'want_bolder', label: 'Show me bolder' },
  { key: 'want_simpler', label: 'Show me simpler' },
];

export const WEATHER_OPTIONS: WeatherCondition[] = ['hot', 'warm', 'mild', 'cool', 'cold', 'humid', 'rainy', 'windy'];
export const COMFORT_OPTIONS: { key: ComfortPreference; label: string }[] = [
  { key: 'comfort_first', label: 'Comfort first' },
  { key: 'balanced', label: 'Balanced' },
  { key: 'polish_first', label: 'Polish first' },
];

/** Confidence is about how much we know, so it is described, not just shown. */
export const confidenceLabel = (value: number): string => {
  if (value >= 0.75) return 'Confident — built from a lot of what you told us';
  if (value >= 0.5) return 'Fairly confident — a few details are missing';
  return 'Low confidence — add more detail for a better answer';
};

// --- Occasion selection -----------------------------------------------------

export function OccasionTile({ occasion, selected, onPress }: {
  occasion: OccasionDefinition; selected: boolean; onPress: () => void;
}) {
  return (
    <TouchableOpacity
      accessibilityRole="button"
      accessibilityLabel={`Style me for ${occasion.label}`}
      accessibilityState={{ selected }}
      onPress={onPress}
      style={[styles.occasion, selected && styles.occasionSelected]}
    >
      <Ionicons
        name={(OCCASION_ICONS[occasion.key] || 'shirt-outline') as any}
        size={22}
        color={selected ? COLORS.white : COLORS.primary}
      />
      <Text style={[styles.occasionLabel, selected && styles.occasionLabelSelected]}>{occasion.label}</Text>
    </TouchableOpacity>
  );
}

export function FollowUpQuestion({ question, value, onChange }: {
  question: OccasionQuestion; value?: string; onChange: (value: string | undefined) => void;
}) {
  if (!question.options.length) return null;
  return (
    <View style={styles.question} accessibilityLabel={question.label}>
      <Text style={styles.questionLabel}>{question.label}</Text>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chipRow}>
        {question.options.map((option) => {
          const active = value === option;
          return (
            <TouchableOpacity
              key={option}
              accessibilityRole="button"
              accessibilityLabel={`${question.label} ${option.replace(/_/g, ' ')}`}
              accessibilityState={{ selected: active }}
              onPress={() => onChange(active ? undefined : option)}
              style={[styles.chip, active && styles.chipActive]}
            >
              <Text style={[styles.chipText, active && styles.chipTextActive]}>{option.replace(/_/g, ' ')}</Text>
            </TouchableOpacity>
          );
        })}
      </ScrollView>
    </View>
  );
}

// --- Processing -------------------------------------------------------------

export function StyleProcessing({ occasionLabel }: { occasionLabel: string }) {
  return (
    <View style={styles.processing} accessibilityLabel="Building your looks" accessibilityRole="progressbar">
      <ActivityIndicator size="large" color={COLORS.primary} />
      <Text style={styles.processingTitle}>Putting together your {occasionLabel.toLowerCase()} looks</Text>
      <Text style={styles.body}>Going through what you own, the weather and the dress code. Only things you have confirmed are used.</Text>
    </View>
  );
}

// --- Pieces -----------------------------------------------------------------

export function OwnedPieceRow({ piece, onSwap }: { piece: LookPiece; onSwap?: () => void }) {
  return (
    <View style={styles.pieceRow} accessibilityLabel={`${piece.display_name}, in your inventory`}>
      <View style={styles.ownedIcon}><Ionicons name="checkmark-circle" size={18} color={COLORS.success} /></View>
      <View style={{ flex: 1 }}>
        <Text style={styles.pieceName}>{piece.display_name}</Text>
        <Text style={styles.ownedTag}>{piece.label}</Text>
      </View>
      {onSwap && (
        <TouchableOpacity
          accessibilityRole="button"
          accessibilityLabel={`Swap ${piece.display_name}`}
          onPress={onSwap}
          style={styles.swapButton}
        >
          <Ionicons name="swap-horizontal" size={16} color={COLORS.primary} />
          <Text style={styles.swapText}>Swap</Text>
        </TouchableOpacity>
      )}
    </View>
  );
}

export function OptionalPieceRow({ piece }: { piece: LookPiece }) {
  return (
    <View style={styles.optionalRow} accessibilityLabel={`${piece.display_name}, optional addition you do not own`}>
      <View style={styles.optionalIcon}><Ionicons name="add-circle-outline" size={18} color={COLORS.warning} /></View>
      <View style={{ flex: 1 }}>
        <Text style={styles.pieceName}>{piece.display_name}</Text>
        <Text style={styles.optionalTag}>{piece.label}</Text>
        {!!piece.role_note && <Text style={styles.optionalWhy}>{piece.role_note}</Text>}
      </View>
    </View>
  );
}

// --- Lookboard --------------------------------------------------------------

export function Lookboard({ look, onSwap, onRevise, onReject, onSave, onShare }: {
  look: Look;
  onSwap?: (piece: LookPiece) => void;
  onRevise?: () => void;
  onReject?: () => void;
  onSave?: () => void;
  onShare?: () => void;
}) {
  return (
    <View style={styles.board} accessibilityLabel={`${look.title} lookboard`}>
      <View style={styles.boardHeader}>
        <View style={{ flex: 1 }}>
          <Text style={styles.eyebrow}>{VARIANT_BLURB[look.variant]}</Text>
          <Text style={styles.boardTitle}>{look.title}</Text>
        </View>
        <View style={styles.countPill}>
          <Text style={styles.countPillText}>{look.owned_item_count} owned</Text>
        </View>
      </View>

      {(['clothing', 'shoes', 'accessories', 'perfume', 'hair', 'grooming'] as LookSlot[]).map((slot) => {
        const pieces = look.slots[slot] || [];
        if (!pieces.length) return null;
        return (
          <View key={slot} style={styles.slotGroup}>
            <Text style={styles.slotLabel}>{SLOT_LABELS[slot]}</Text>
            {pieces.map((piece) =>
              piece.owned
                ? <OwnedPieceRow key={piece.id} piece={piece} onSwap={onSwap ? () => onSwap(piece) : undefined} />
                : <OptionalPieceRow key={piece.id} piece={piece} />
            )}
          </View>
        );
      })}

      {look.optional_addition_count > 0 && (
        <View style={styles.optionalNotice}>
          <Ionicons name="information-circle-outline" size={18} color={COLORS.warning} />
          <Text style={styles.optionalNoticeText}>
            {look.optional_addition_count} item{look.optional_addition_count > 1 ? 's are' : ' is'} not in your inventory. You can wear this look without {look.optional_addition_count > 1 ? 'them' : 'it'}.
          </Text>
        </View>
      )}

      <WhyThisWorks look={look} />
      <ConfidenceRow look={look} />

      <View style={styles.actionRow}>
        {onSave && <TouchableOpacity accessibilityRole="button" accessibilityLabel={`Save ${look.title}`} onPress={onSave} style={styles.primary}><Text style={styles.primaryText}>Save</Text></TouchableOpacity>}
        {onRevise && <TouchableOpacity accessibilityRole="button" accessibilityLabel={`Revise ${look.title}`} onPress={onRevise} style={styles.outline}><Text style={styles.outlineText}>Revise</Text></TouchableOpacity>}
        {onShare && <TouchableOpacity accessibilityRole="button" accessibilityLabel={`Share ${look.title}`} onPress={onShare} style={styles.outline}><Text style={styles.outlineText}>Share</Text></TouchableOpacity>}
        {onReject && <TouchableOpacity accessibilityRole="button" accessibilityLabel={`Reject ${look.title}`} onPress={onReject} style={styles.outline}><Text style={styles.outlineText}>Not for me</Text></TouchableOpacity>}
      </View>
    </View>
  );
}

export function WhyThisWorks({ look }: { look: Look }) {
  return (
    <View style={styles.why} accessibilityLabel="Why this works">
      <Text style={styles.whyTitle}>Why this works</Text>
      <Text style={styles.body}>{look.why_it_works}</Text>
      {!!look.weather_note && <Text style={styles.noteLine}>{look.weather_note}</Text>}
      {!!look.dress_code_note && <Text style={styles.noteLine}>{look.dress_code_note}</Text>}
      {look.preparation_steps.length > 0 && (
        <View style={styles.prep}>
          <Text style={styles.whyTitle}>Before the day</Text>
          {look.preparation_steps.map((step, index) => (
            <Text key={index} style={styles.bullet}>• {step}</Text>
          ))}
        </View>
      )}
    </View>
  );
}

export function ConfidenceRow({ look }: { look: Look }) {
  return (
    <View style={styles.confidence} accessibilityLabel={`Confidence ${Math.round(look.confidence * 100)} percent`}>
      <Text style={styles.confidenceText}>{confidenceLabel(look.confidence)}</Text>
      {look.missing_information.map((note, index) => (
        <Text key={index} style={styles.missing}>• {note}</Text>
      ))}
      {look.explanation_source === 'deterministic' && (
        <Text style={styles.missing}>• This explanation was written from your own recorded details.</Text>
      )}
    </View>
  );
}

export function ReviseReasons({ onPick }: { onPick: (reason: ReviseReason) => void }) {
  return (
    <View style={styles.question} accessibilityLabel="Revise this look">
      <Text style={styles.questionLabel}>What should change?</Text>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chipRow}>
        {REVISE_REASONS.map((reason) => (
          <TouchableOpacity
            key={reason.key}
            accessibilityRole="button"
            accessibilityLabel={reason.label}
            onPress={() => onPick(reason.key)}
            style={styles.chip}
          >
            <Text style={styles.chipText}>{reason.label}</Text>
          </TouchableOpacity>
        ))}
      </ScrollView>
    </View>
  );
}

/**
 * Shown when the engine refused to guess. This is deliberately not styled as an
 * error: nothing went wrong, there just is not enough confirmed inventory yet.
 */
export function NotEnoughInventory({ result, onAdd }: { result: StyleResult; onAdd: () => void }) {
  return (
    <View style={styles.empty} accessibilityLabel="Not enough inventory yet">
      <Ionicons name="shirt-outline" size={30} color={COLORS.primary} />
      <Text style={styles.boardTitle}>We need a bit more to work with</Text>
      <Text style={styles.body}>{result.message}</Text>
      {(result.guidance || []).map((line, index) => (
        <Text key={index} style={styles.bullet}>• {line}</Text>
      ))}
      <TouchableOpacity accessibilityRole="button" accessibilityLabel="Add items to inventory" onPress={onAdd} style={styles.primary}>
        <Text style={styles.primaryText}>Add items</Text>
      </TouchableOpacity>
    </View>
  );
}

export function EntitlementNote({ result }: { result: StyleResult }) {
  const { remaining, included } = result.entitlement;
  return (
    <Text style={styles.missing} accessibilityLabel="Remaining allowance">
      {remaining} of {included} style requests left this month.
    </Text>
  );
}

const styles = StyleSheet.create({
  occasion: { width: 104, alignItems: 'center', gap: 6, paddingVertical: SPACING.md, borderRadius: RADIUS.lg, backgroundColor: COLORS.card, borderWidth: 1, borderColor: COLORS.border, margin: 4 },
  occasionSelected: { backgroundColor: COLORS.primary, borderColor: COLORS.primary },
  occasionLabel: { fontFamily: FONTS.family.bodyMedium, fontSize: 12, color: COLORS.textPrimary, textAlign: 'center' },
  occasionLabelSelected: { color: COLORS.white },
  question: { marginTop: SPACING.md },
  questionLabel: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.textPrimary, marginBottom: 6 },
  chipRow: { gap: 8, paddingRight: SPACING.md },
  chip: { paddingHorizontal: 14, paddingVertical: 8, borderRadius: RADIUS.full, backgroundColor: COLORS.backgroundSecondary, borderWidth: 1, borderColor: COLORS.border },
  chipActive: { backgroundColor: COLORS.primary, borderColor: COLORS.primary },
  chipText: { fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.textSecondary, textTransform: 'capitalize' },
  chipTextActive: { color: COLORS.white },
  processing: { alignItems: 'center', gap: SPACING.sm, padding: SPACING.xl },
  processingTitle: { fontFamily: FONTS.family.headingMedium, fontSize: 18, color: COLORS.textPrimary, textAlign: 'center' },
  board: { backgroundColor: COLORS.card, borderRadius: RADIUS.xl, padding: SPACING.lg, borderWidth: 1, borderColor: COLORS.border, marginBottom: SPACING.md },
  boardHeader: { flexDirection: 'row', alignItems: 'flex-start', gap: SPACING.sm },
  eyebrow: { fontFamily: FONTS.family.bodySemibold, color: COLORS.accent, fontSize: 10, letterSpacing: 1.2, textTransform: 'uppercase' },
  boardTitle: { fontFamily: FONTS.family.headingMedium, fontSize: 20, color: COLORS.textPrimary, marginTop: 3 },
  countPill: { backgroundColor: COLORS.successLight, borderRadius: RADIUS.full, paddingHorizontal: 10, paddingVertical: 4 },
  countPillText: { fontFamily: FONTS.family.bodySemibold, fontSize: 11, color: COLORS.success },
  slotGroup: { marginTop: SPACING.md },
  slotLabel: { fontFamily: FONTS.family.bodySemibold, fontSize: 11, letterSpacing: 1, textTransform: 'uppercase', color: COLORS.textMuted, marginBottom: 6 },
  pieceRow: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 8, borderBottomWidth: 1, borderBottomColor: COLORS.borderLight },
  optionalRow: { flexDirection: 'row', alignItems: 'flex-start', gap: 10, paddingVertical: 8, paddingHorizontal: 10, borderRadius: RADIUS.md, backgroundColor: COLORS.warningLight, borderWidth: 1, borderColor: COLORS.warningMuted, borderStyle: 'dashed', marginTop: 4 },
  ownedIcon: { width: 26, alignItems: 'center' },
  optionalIcon: { width: 26, alignItems: 'center', paddingTop: 2 },
  pieceName: { fontFamily: FONTS.family.bodyMedium, fontSize: 14, color: COLORS.textPrimary },
  ownedTag: { fontFamily: FONTS.family.body, fontSize: 11, color: COLORS.success, marginTop: 2 },
  optionalTag: { fontFamily: FONTS.family.bodySemibold, fontSize: 11, color: COLORS.warning, marginTop: 2 },
  optionalWhy: { fontFamily: FONTS.family.body, fontSize: 11, color: COLORS.textSecondary, marginTop: 2 },
  swapButton: { flexDirection: 'row', alignItems: 'center', gap: 4, paddingHorizontal: 10, paddingVertical: 6, borderRadius: RADIUS.full, backgroundColor: COLORS.primaryLight },
  swapText: { fontFamily: FONTS.family.bodySemibold, fontSize: 11, color: COLORS.primary },
  optionalNotice: { flexDirection: 'row', gap: 8, alignItems: 'flex-start', marginTop: SPACING.md, padding: SPACING.sm, borderRadius: RADIUS.md, backgroundColor: COLORS.warningLight },
  optionalNoticeText: { flex: 1, fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.textSecondary, lineHeight: 17 },
  why: { marginTop: SPACING.md, paddingTop: SPACING.md, borderTopWidth: 1, borderTopColor: COLORS.borderLight },
  whyTitle: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.textPrimary, marginBottom: 4 },
  body: { fontFamily: FONTS.family.body, fontSize: 13, lineHeight: 19, color: COLORS.textSecondary },
  noteLine: { fontFamily: FONTS.family.body, fontSize: 12, lineHeight: 18, color: COLORS.textSecondary, marginTop: 6 },
  prep: { marginTop: SPACING.md },
  bullet: { fontFamily: FONTS.family.body, fontSize: 12, lineHeight: 18, color: COLORS.textSecondary, marginTop: 3 },
  confidence: { marginTop: SPACING.md, padding: SPACING.sm, borderRadius: RADIUS.md, backgroundColor: COLORS.backgroundSecondary },
  confidenceText: { fontFamily: FONTS.family.bodyMedium, fontSize: 12, color: COLORS.textPrimary },
  missing: { fontFamily: FONTS.family.body, fontSize: 11, color: COLORS.textMuted, marginTop: 3, lineHeight: 16 },
  actionRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: SPACING.md },
  primary: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, backgroundColor: COLORS.primary, borderRadius: RADIUS.full, paddingHorizontal: 20, paddingVertical: 12, marginTop: SPACING.sm },
  primaryText: { fontFamily: FONTS.family.bodySemibold, fontSize: 14, color: COLORS.white },
  outline: { alignItems: 'center', justifyContent: 'center', borderRadius: RADIUS.full, paddingHorizontal: 18, paddingVertical: 12, borderWidth: 1, borderColor: COLORS.border, marginTop: SPACING.sm },
  outlineText: { fontFamily: FONTS.family.bodySemibold, fontSize: 14, color: COLORS.textPrimary },
  empty: { alignItems: 'center', gap: 6, padding: SPACING.lg, backgroundColor: COLORS.card, borderRadius: RADIUS.xl, borderWidth: 1, borderColor: COLORS.border },
});
