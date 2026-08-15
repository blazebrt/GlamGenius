import React, { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Modal,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';

import { errorMessage } from '../../services/api';
import {
  CareExperienceFeedback,
  CareExperienceFeedbackDimension,
  CareExperienceFeedbackSentiment,
  CareExperienceFeedbackSubjectType,
  deleteCareExperienceFeedback,
  listCareExperienceFeedback,
  recordCareExperienceFeedback,
} from '../../services/apiV2';
import { COLORS, FONTS, RADIUS, SPACING } from '../../theme/colors';

export const CARE_EXPERIENCE_PRODUCT_CATEGORIES = ['beauty', 'hair'] as const;
export const canRecordCareExperienceForCategory = (category: string): boolean =>
  (CARE_EXPERIENCE_PRODUCT_CATEGORIES as readonly string[]).includes(category);

const DIMENSIONS: { key: CareExperienceFeedbackDimension; label: string }[] = [
  { key: 'overall_experience', label: 'Overall experience' },
  { key: 'comfort', label: 'Comfort' },
  { key: 'ease_of_use', label: 'Ease of use' },
  { key: 'routine_fit', label: 'Routine fit' },
];

const SENTIMENTS: { key: CareExperienceFeedbackSentiment; label: string }[] = [
  { key: 'positive', label: 'Positive' },
  { key: 'neutral', label: 'Neutral' },
  { key: 'negative', label: 'Negative' },
];

const dimensionLabel = (value: CareExperienceFeedbackDimension): string =>
  DIMENSIONS.find((row) => row.key === value)?.label ?? value;
const sentimentLabel = (value: CareExperienceFeedbackSentiment): string =>
  SENTIMENTS.find((row) => row.key === value)?.label ?? value;

export interface CareExperienceFeedbackSheetProps {
  open: boolean;
  subjectType: CareExperienceFeedbackSubjectType;
  subjectId: string;
  subjectLabel: string;
  onClose: () => void;
}

export function CareExperienceFeedbackSheet({
  open,
  subjectType,
  subjectId,
  subjectLabel,
  onClose,
}: CareExperienceFeedbackSheetProps) {
  const [history, setHistory] = useState<CareExperienceFeedback[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState(false);
  const [dimension, setDimension] = useState<CareExperienceFeedbackDimension | null>(null);
  const [sentiment, setSentiment] = useState<CareExperienceFeedbackSentiment | null>(null);
  const [note, setNote] = useState('');
  const [saving, setSaving] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [savedMessage, setSavedMessage] = useState('');
  const [actionError, setActionError] = useState('');

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true);
    setHistoryError(false);
    try {
      const result = await listCareExperienceFeedback(subjectType, subjectId);
      setHistory(result.feedback);
    } catch (err) {
      console.warn('Care experience history load failed', err);
      setHistoryError(true);
    } finally {
      setHistoryLoading(false);
    }
  }, [subjectId, subjectType]);

  useEffect(() => {
    if (!open) return;
    setDimension(null);
    setSentiment(null);
    setNote('');
    setSavedMessage('');
    setActionError('');
    void loadHistory();
  }, [loadHistory, open]);

  const save = async () => {
    if (!dimension || !sentiment || saving) return;
    setSaving(true);
    setActionError('');
    try {
      const result = await recordCareExperienceFeedback({
        subject_type: subjectType,
        subject_id: subjectId,
        dimension,
        sentiment,
        ...(note.length > 0 ? { note } : {}),
      });
      setSavedMessage(result.message || 'Saved. This does not change your routine automatically.');
      setDimension(null);
      setSentiment(null);
      setNote('');
      await loadHistory();
    } catch (err) {
      console.warn('Care experience save failed', err);
      setActionError(errorMessage(err, 'We could not save this experience. Your entry is still here to retry.'));
    } finally {
      setSaving(false);
    }
  };

  const remove = async (feedbackId: string) => {
    if (deletingId) return;
    setDeletingId(feedbackId);
    setActionError('');
    try {
      await deleteCareExperienceFeedback(feedbackId);
      await loadHistory();
    } catch (err) {
      console.warn('Care experience delete failed', err);
      setActionError(errorMessage(err, 'We could not delete this entry.'));
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <Modal
      visible={open}
      transparent
      animationType="slide"
      onRequestClose={onClose}
      accessibilityViewIsModal
    >
      <View style={styles.backdrop}>
        <View style={styles.sheet} accessibilityLabel="Your experience feedback">
          <View style={styles.header}>
            <View style={styles.headerCopy}>
              <Text style={styles.eyebrow}>YOUR EXPERIENCE</Text>
              <Text style={styles.title}>Record your experience</Text>
              <Text style={styles.subject}>{subjectLabel}</Text>
            </View>
            <TouchableOpacity
              accessibilityRole="button"
              accessibilityLabel="Close experience feedback"
              onPress={onClose}
              style={styles.close}
              hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
            >
              <Ionicons name="close" size={22} color={COLORS.textPrimary} />
            </TouchableOpacity>
          </View>

          <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
            <Text style={styles.sectionLabel}>WHAT WOULD YOU LIKE TO RECORD?</Text>
            <View style={styles.optionGrid}>
              {DIMENSIONS.map((option) => {
                const selected = dimension === option.key;
                return (
                  <TouchableOpacity
                    key={option.key}
                    accessibilityRole="radio"
                    accessibilityLabel={option.label}
                    accessibilityState={{ selected }}
                    onPress={() => setDimension(option.key)}
                    style={[styles.option, selected && styles.optionSelected]}
                  >
                    <Text style={[styles.optionText, selected && styles.optionTextSelected]}>{option.label}</Text>
                  </TouchableOpacity>
                );
              })}
            </View>

            <Text style={styles.sectionLabel}>HOW WAS IT?</Text>
            <View style={styles.sentimentRow}>
              {SENTIMENTS.map((option) => {
                const selected = sentiment === option.key;
                return (
                  <TouchableOpacity
                    key={option.key}
                    accessibilityRole="radio"
                    accessibilityLabel={option.label}
                    accessibilityState={{ selected }}
                    onPress={() => setSentiment(option.key)}
                    style={[styles.sentiment, selected && styles.optionSelected]}
                  >
                    <Text style={[styles.optionText, selected && styles.optionTextSelected]}>{option.label}</Text>
                  </TouchableOpacity>
                );
              })}
            </View>

            <Text style={styles.noteLabel}>Add a note <Text style={styles.optional}>(optional)</Text></Text>
            <TextInput
              accessibilityLabel="Experience note"
              value={note}
              onChangeText={setNote}
              maxLength={500}
              multiline
              textAlignVertical="top"
              placeholder="Write what you experienced, in your own words"
              placeholderTextColor={COLORS.textMuted}
              style={styles.noteInput}
            />
            <Text style={styles.characterCount}>{note.length} / 500</Text>

            {!!savedMessage && <Text accessibilityLiveRegion="polite" style={styles.saved}>{savedMessage}</Text>}
            {!!actionError && <Text accessibilityRole="alert" style={styles.error}>{actionError}</Text>}

            <TouchableOpacity
              accessibilityRole="button"
              accessibilityLabel="Save experience"
              accessibilityState={{ disabled: !dimension || !sentiment || saving, busy: saving }}
              disabled={!dimension || !sentiment || saving}
              onPress={() => void save()}
              style={[styles.primary, (!dimension || !sentiment || saving) && styles.primaryDisabled]}
            >
              {saving ? <ActivityIndicator color={COLORS.white} /> : <Text style={styles.primaryText}>Save experience</Text>}
            </TouchableOpacity>

            <Text style={styles.historyTitle}>PREVIOUS ENTRIES</Text>
            {historyLoading ? (
              <View style={styles.historyState}><ActivityIndicator color={COLORS.primary} /><Text style={styles.muted}>Loading previous entries…</Text></View>
            ) : historyError ? (
              <View style={styles.historyState}>
                <Text style={styles.error}>We could not retrieve previous entries.</Text>
                <TouchableOpacity accessibilityRole="button" accessibilityLabel="Retry previous entries" onPress={() => void loadHistory()}>
                  <Text style={styles.link}>Try again</Text>
                </TouchableOpacity>
              </View>
            ) : history.length === 0 ? (
              <Text style={styles.muted}>No previous entries for this {subjectType === 'product' ? 'product' : 'routine step'}.</Text>
            ) : (
              history.map((entry) => (
                <View key={entry.id} style={styles.entry}>
                  <View style={styles.entryHeader}>
                    <View style={{ flex: 1 }}>
                      <Text style={styles.entryTitle}>{dimensionLabel(entry.dimension)} · {sentimentLabel(entry.sentiment)}</Text>
                      <Text style={styles.entryDate}>{entry.experienced_on}</Text>
                    </View>
                    <TouchableOpacity
                      accessibilityRole="button"
                      accessibilityLabel={`Delete experience entry from ${entry.experienced_on}`}
                      accessibilityState={{ busy: deletingId === entry.id }}
                      disabled={deletingId !== null}
                      onPress={() => void remove(entry.id)}
                      style={styles.delete}
                    >
                      {deletingId === entry.id ? <ActivityIndicator size="small" color={COLORS.error} /> : <Text style={styles.deleteText}>Delete entry</Text>}
                    </TouchableOpacity>
                  </View>
                  {entry.note !== null && <Text style={styles.entryNote}>{entry.note}</Text>}
                </View>
              ))
            )}
          </ScrollView>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: { flex: 1, justifyContent: 'flex-end', backgroundColor: COLORS.overlay },
  sheet: { maxHeight: '92%', backgroundColor: COLORS.background, borderTopLeftRadius: RADIUS.xl, borderTopRightRadius: RADIUS.xl },
  header: { flexDirection: 'row', alignItems: 'flex-start', paddingHorizontal: SPACING.lg, paddingTop: SPACING.lg, paddingBottom: SPACING.sm },
  headerCopy: { flex: 1 },
  close: { padding: 4 },
  eyebrow: { fontFamily: FONTS.family.bodySemibold, color: COLORS.primary, fontSize: 10, letterSpacing: 1.3 },
  title: { fontFamily: FONTS.family.heading, color: COLORS.textPrimary, fontSize: 25, marginTop: 3 },
  subject: { fontFamily: FONTS.family.body, color: COLORS.textSecondary, fontSize: 12, marginTop: 4 },
  content: { paddingHorizontal: SPACING.lg, paddingBottom: SPACING.xl },
  sectionLabel: { fontFamily: FONTS.family.bodySemibold, color: COLORS.textMuted, fontSize: 10, letterSpacing: 1, marginTop: SPACING.md, marginBottom: SPACING.sm },
  optionGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: SPACING.sm },
  option: { width: '48%', borderWidth: 1, borderColor: COLORS.border, borderRadius: RADIUS.md, backgroundColor: COLORS.card, padding: 13 },
  sentimentRow: { flexDirection: 'row', gap: SPACING.sm },
  sentiment: { flex: 1, borderWidth: 1, borderColor: COLORS.border, borderRadius: RADIUS.md, backgroundColor: COLORS.card, padding: 13, alignItems: 'center' },
  optionSelected: { backgroundColor: COLORS.primaryLight, borderColor: COLORS.primary },
  optionText: { fontFamily: FONTS.family.bodyMedium, color: COLORS.textPrimary, fontSize: 12 },
  optionTextSelected: { color: COLORS.primaryDark },
  noteLabel: { fontFamily: FONTS.family.bodySemibold, color: COLORS.textPrimary, fontSize: 12, marginTop: SPACING.lg, marginBottom: SPACING.sm },
  optional: { fontFamily: FONTS.family.body, color: COLORS.textMuted },
  noteInput: { minHeight: 86, borderWidth: 1, borderColor: COLORS.border, borderRadius: RADIUS.md, backgroundColor: COLORS.card, padding: 12, fontFamily: FONTS.family.body, color: COLORS.textPrimary, fontSize: 13 },
  characterCount: { fontFamily: FONTS.family.body, color: COLORS.textMuted, fontSize: 11, textAlign: 'right', marginTop: 4 },
  primary: { minHeight: 46, backgroundColor: COLORS.primary, borderRadius: RADIUS.full, alignItems: 'center', justifyContent: 'center', paddingHorizontal: SPACING.lg, marginTop: SPACING.md },
  primaryDisabled: { opacity: 0.45 },
  primaryText: { fontFamily: FONTS.family.bodySemibold, color: COLORS.white, fontSize: 13 },
  saved: { fontFamily: FONTS.family.bodyMedium, color: COLORS.primaryDark, backgroundColor: COLORS.primaryLight, borderRadius: RADIUS.md, padding: 12, lineHeight: 18, marginTop: SPACING.md },
  error: { fontFamily: FONTS.family.bodyMedium, color: COLORS.error, fontSize: 12, lineHeight: 18, marginTop: SPACING.sm },
  historyTitle: { fontFamily: FONTS.family.bodySemibold, color: COLORS.textMuted, fontSize: 10, letterSpacing: 1, marginTop: SPACING.xl, marginBottom: SPACING.sm },
  historyState: { alignItems: 'center', gap: SPACING.sm, paddingVertical: SPACING.md },
  muted: { fontFamily: FONTS.family.body, color: COLORS.textSecondary, fontSize: 12, lineHeight: 18 },
  link: { fontFamily: FONTS.family.bodySemibold, color: COLORS.primary, fontSize: 12 },
  entry: { backgroundColor: COLORS.card, borderWidth: 1, borderColor: COLORS.border, borderRadius: RADIUS.md, padding: 12, marginBottom: SPACING.sm },
  entryHeader: { flexDirection: 'row', alignItems: 'flex-start', gap: SPACING.sm },
  entryTitle: { fontFamily: FONTS.family.bodySemibold, color: COLORS.textPrimary, fontSize: 12 },
  entryDate: { fontFamily: FONTS.family.body, color: COLORS.textMuted, fontSize: 11, marginTop: 3 },
  entryNote: { fontFamily: FONTS.family.body, color: COLORS.textSecondary, fontSize: 12, lineHeight: 18, marginTop: SPACING.sm },
  delete: { paddingVertical: 4, paddingHorizontal: 2 },
  deleteText: { fontFamily: FONTS.family.bodyMedium, color: COLORS.error, fontSize: 11 },
});
