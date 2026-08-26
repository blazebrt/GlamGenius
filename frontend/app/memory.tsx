/**
 * You → Memory. The Memory Control screen.
 *
 * Everything GlamGenius remembers, why it remembers it, where it came from,
 * and — the point of the screen — the ability to change or remove any of it.
 *
 * Two things are deliberate.
 *
 * Every fact states outright whether it is **currently shaping suggestions**.
 * "We remember this" and "we are acting on this" are different claims, and a
 * person deciding whether to delete something needs the second one.
 *
 * Deleting is a single tap with no confirmation dialogue asking the user to
 * reconsider. Nagging somebody out of deleting their own data is a dark
 * pattern; the copy tells them what will happen and then does it.
 */
import React, { useCallback, useState } from 'react';
import {
  Alert, RefreshControl, ScrollView, StyleSheet, Text, TextInput, TouchableOpacity, View,
} from 'react-native';
import { useFocusEffect, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';

import {
  EmptyState, MemoryCategoryRow, MemoryFactCard,
} from '../src/components/progress/ProgressPieces';
import {
  MemoryCategorySummary, MemoryFact, MemoryOverview, deleteMemory, exportMemory, getMemory,
  patchMemory, setMemoryCategory,
} from '../src/services/apiV2';
import { COLORS, FONTS, RADIUS, SPACING } from '../src/theme/colors';

export default function MemoryScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [overview, setOverview] = useState<MemoryOverview | null>(null);
  const [editing, setEditing] = useState<MemoryFact | null>(null);
  const [draft, setDraft] = useState('');
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(false);
  const [status, setStatus] = useState<string | null>(null);

  const load = useCallback(async (mode: 'initial' | 'refresh' = 'initial') => {
    if (mode === 'refresh') setRefreshing(true);
    setError(false);
    try {
      setOverview(await getMemory());
    } catch (err) {
      console.warn('memory load failed', err);
      setError(true);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useFocusEffect(useCallback(() => { void load('refresh'); }, [load]));

  const onConfirm = async (fact: MemoryFact) => {
    try {
      await patchMemory(fact.id, { verification_state: 'confirmed' });
      setStatus('Confirmed. We will lean on that one more.');
      await load('refresh');
    } catch (err) { console.warn('confirm failed', err); }
  };

  const onCorrect = (fact: MemoryFact) => {
    setEditing(fact);
    setDraft(fact.fact);
  };

  const saveCorrection = async () => {
    if (!editing || !draft.trim()) return;
    try {
      await patchMemory(editing.id, { fact: draft.trim() });
      setStatus('Updated. Your version is what we will use from now on.');
      setEditing(null);
      setDraft('');
      await load('refresh');
    } catch (err) { console.warn('correct failed', err); }
  };

  const onDelete = async (fact: MemoryFact) => {
    try {
      const result = await deleteMemory(fact.id);
      setStatus(result.message);
      await load('refresh');
    } catch (err) { console.warn('delete failed', err); }
  };

  const onToggleCategory = async (category: MemoryCategorySummary) => {
    try {
      const result = await setMemoryCategory(category.key, !category.enabled);
      setStatus(result.message);
      await load('refresh');
    } catch (err) { console.warn('category toggle failed', err); }
  };

  const onExport = async () => {
    try {
      const data = await exportMemory();
      Alert.alert(
        'Your memory export',
        `${data.total} things remembered, ${data.deleted_count} of them deleted. ` +
        'The full export, including what was removed and when, has been fetched.',
      );
      setStatus(data.note);
    } catch (err) { console.warn('export failed', err); }
  };

  if (loading && !overview) {
    return <View style={styles.centre}><Text style={styles.muted}>Opening what we remember…</Text></View>;
  }

  if (error || !overview) {
    return (
      <View style={[styles.centre, { paddingTop: insets.top }]}>
        <Text style={styles.muted}>We could not load this right now.</Text>
        <TouchableOpacity accessibilityRole="button" accessibilityLabel="Try again" onPress={() => void load()}>
          <Text style={styles.link}>Try again</Text>
        </TouchableOpacity>
      </View>
    );
  }

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <ScrollView
        contentContainerStyle={{ padding: SPACING.lg, paddingBottom: insets.bottom + 40 }}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void load('refresh')} tintColor={COLORS.primary} />}
        keyboardShouldPersistTaps="handled"
      >
        <View style={styles.header}>
          <TouchableOpacity accessibilityRole="button" accessibilityLabel="Go back" onPress={() => router.back()}>
            <Ionicons name="chevron-back" size={24} color={COLORS.textPrimary} />
          </TouchableOpacity>
          <View>
            <Text style={styles.eyebrow}>YOU</Text>
            <Text style={styles.title}>Memory</Text>
          </View>
        </View>

        <Text style={styles.intro}>{overview.note}</Text>
        <Text style={styles.count}>
          {overview.facts.length} remembered · {overview.influencing_count} currently used in suggestions
        </Text>

        {!!status && <Text style={styles.status} accessibilityLiveRegion="polite">{status}</Text>}

        {editing && (
          <View style={styles.editor} accessibilityLabel="Correcting a memory">
            <Text style={styles.cardTitle}>Put it in your own words</Text>
            <TextInput
              accessibilityLabel="Corrected memory"
              value={draft}
              onChangeText={setDraft}
              multiline
              style={styles.input}
              placeholder="What we should remember instead"
              placeholderTextColor={COLORS.textMuted}
            />
            <View style={styles.editorActions}>
              <TouchableOpacity accessibilityRole="button" accessibilityLabel="Save correction" onPress={() => void saveCorrection()}>
                <Text style={styles.link}>Save</Text>
              </TouchableOpacity>
              <TouchableOpacity accessibilityRole="button" accessibilityLabel="Cancel correction" onPress={() => { setEditing(null); setDraft(''); }}>
                <Text style={styles.muted}>Cancel</Text>
              </TouchableOpacity>
            </View>
          </View>
        )}

        {overview.facts.length ? (
          <>
            <Text style={styles.section}>What we remember</Text>
            {overview.facts.map((fact) => (
              <MemoryFactCard
                key={fact.id}
                fact={fact}
                onConfirm={(row) => void onConfirm(row)}
                onCorrect={onCorrect}
                onDelete={(row) => void onDelete(row)}
              />
            ))}
          </>
        ) : (
          <EmptyState
            title="Nothing remembered yet"
            body="As you use the app we will pick up preferences — and everything we pick up will show here, where you can change or remove it."
          />
        )}

        <Text style={styles.section}>What we are allowed to remember</Text>
        <Text style={styles.body}>
          Switching a category off stops us using it straight away. Nothing is deleted, so you
          can switch it back on later.
        </Text>
        <View style={styles.card}>
          {overview.categories.map((category) => (
            <MemoryCategoryRow
              key={category.key}
              category={category}
              onToggle={(row) => void onToggleCategory(row)}
            />
          ))}
        </View>

        <TouchableOpacity
          accessibilityRole="button"
          accessibilityLabel="Export everything we remember"
          onPress={() => void onExport()}
          style={styles.action}
        >
          <Ionicons name="download-outline" size={18} color={COLORS.primary} />
          <Text style={styles.actionText}>Export everything, including deletions</Text>
        </TouchableOpacity>

        <TouchableOpacity
          accessibilityRole="button"
          accessibilityLabel="Open privacy settings"
          onPress={() => router.push('/(tabs)/you')}
          style={styles.action}
        >
          <Ionicons name="lock-closed-outline" size={18} color={COLORS.primary} />
          <Text style={styles.actionText}>Privacy and your data</Text>
        </TouchableOpacity>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.backgroundSecondary },
  centre: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 8, backgroundColor: COLORS.background },
  header: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 6 },
  eyebrow: { fontFamily: FONTS.family.bodySemibold, color: COLORS.primary, fontSize: 10, letterSpacing: 1.4 },
  title: { fontFamily: FONTS.family.heading, color: COLORS.textPrimary, fontSize: 29, marginTop: 2 },
  intro: { fontFamily: FONTS.family.body, color: COLORS.textSecondary, fontSize: 13, lineHeight: 19, marginTop: 10 },
  count: { fontFamily: FONTS.family.bodyMedium, color: COLORS.textMuted, fontSize: 11, marginTop: 8 },
  status: { fontFamily: FONTS.family.bodyMedium, color: COLORS.primary, fontSize: 12, lineHeight: 18, marginTop: 10 },
  section: { fontFamily: FONTS.family.headingMedium, color: COLORS.textPrimary, fontSize: 18, marginTop: 22 },
  body: { fontFamily: FONTS.family.body, color: COLORS.textSecondary, fontSize: 12, lineHeight: 18, marginTop: 5 },
  muted: { fontFamily: FONTS.family.body, color: COLORS.textMuted, fontSize: 12 },
  link: { fontFamily: FONTS.family.bodySemibold, color: COLORS.primary, fontSize: 13 },
  card: { backgroundColor: COLORS.card, borderRadius: RADIUS.lg, borderWidth: 1, borderColor: COLORS.border, paddingHorizontal: 13, marginTop: 10 },
  cardTitle: { fontFamily: FONTS.family.headingMedium, color: COLORS.textPrimary, fontSize: 15 },
  editor: { backgroundColor: COLORS.card, borderRadius: RADIUS.lg, borderWidth: 1, borderColor: COLORS.primaryMuted, padding: 13, marginTop: 12 },
  input: { borderWidth: 1, borderColor: COLORS.border, borderRadius: RADIUS.md, padding: 10, marginTop: 9, minHeight: 66, fontFamily: FONTS.family.body, color: COLORS.textPrimary, fontSize: 13, textAlignVertical: 'top' },
  editorActions: { flexDirection: 'row', gap: 18, alignItems: 'center', marginTop: 10 },
  action: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, paddingVertical: 13, borderRadius: RADIUS.lg, backgroundColor: COLORS.card, borderWidth: 1, borderColor: COLORS.border, marginTop: 12 },
  actionText: { fontFamily: FONTS.family.bodySemibold, color: COLORS.textPrimary, fontSize: 12 },
});
