/**
 * Knowledge authoring — the internal tool (admin only).
 *
 * Add an entry, work the review queue, approve, publish, reject with a reason,
 * edit into a new version, and bulk-import drafts from pasted CSV.
 *
 * Not a consumer screen and not reachable from one: the route is guarded here
 * and every endpoint behind it is admin-gated server-side. The rules the tool
 * appears to enforce are not enforced here — the server owns them, and this
 * screen reports what it says. In particular, approval without a source URL is
 * refused by the API, and the refusal is shown as-is.
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator, RefreshControl, ScrollView, StyleSheet, Text,
  TextInput, TouchableOpacity, View,
} from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { useUserStore } from '../src/store/userStore';
import {
  KnowledgeEntry, KnowledgeEntryInput, KnowledgeVocabulary,
  approveKnowledgeEntry, createKnowledgeEntry, editKnowledgeEntry,
  getKnowledgeVocabulary, importKnowledgeCsv, listKnowledgeEntries,
  publishKnowledgeEntry, rejectKnowledgeEntry,
} from '../src/services/apiV2';
import { COLORS, FONTS, RADIUS, SPACING } from '../src/theme/colors';

const EMPTY: KnowledgeEntryInput = {
  subject_type: '', subject: '', claim: '', value: '', unit: '',
  source_name: '', source_url: '', evidence_tier: '', notes: '',
};

/** Server messages are written for a person; show them rather than a generic line. */
function messageFor(err: any, fallback: string): string {
  return err?.response?.data?.detail?.message || err?.response?.data?.message || fallback;
}

export default function AdminKnowledgeScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { isAdmin, initialized, registrationState } = useUserStore();

  const [vocabulary, setVocabulary] = useState<KnowledgeVocabulary | null>(null);
  const [entries, setEntries] = useState<KnowledgeEntry[]>([]);
  const [filterType, setFilterType] = useState<string>('');
  const [filterStatus, setFilterStatus] = useState<string>('');
  const [form, setForm] = useState<KnowledgeEntryInput>(EMPTY);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [rejectingId, setRejectingId] = useState<string | null>(null);
  const [rejectReason, setRejectReason] = useState('');
  const [csv, setCsv] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [vocab, queue] = await Promise.all([
        getKnowledgeVocabulary(),
        listKnowledgeEntries({
          subject_type: filterType || undefined,
          status: filterStatus || undefined,
        }),
      ]);
      setVocabulary(vocab);
      setEntries(queue.entries);
    } catch (err: any) {
      setError(err?.response?.status === 403
        ? 'You are not an admin on this project.'
        : messageFor(err, 'Could not load the queue.'));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [filterStatus, filterType]);

  useEffect(() => {
    if (!initialized) return;
    if (registrationState !== 'registered') { router.replace('/(auth)/welcome'); return; }
    if (!isAdmin) { router.replace('/(tabs)/today'); return; }
    void load();
  }, [initialized, isAdmin, load, registrationState, router]);

  const act = async (label: string, run: () => Promise<unknown>) => {
    setBusy(true); setError(null); setNotice(null);
    try {
      await run();
      setNotice(label);
      await load();
    } catch (err: any) {
      setError(messageFor(err, 'That did not work.'));
    } finally {
      setBusy(false);
    }
  };

  const submit = () => act(editingId ? 'Saved.' : 'Draft added.', async () => {
    const saved = editingId
      ? await editKnowledgeEntry(editingId, form)
      : await createKnowledgeEntry(form);
    setForm(EMPTY);
    setEditingId(null);
    return saved;
  });

  const startEdit = (entry: KnowledgeEntry) => {
    setEditingId(entry.id);
    setForm({
      subject_type: entry.subject_type, subject: entry.subject, claim: entry.claim,
      value: entry.value || '', unit: entry.unit || '',
      source_name: entry.source?.name || '', source_url: entry.source?.url || '',
      evidence_tier: entry.evidence_tier || '', notes: entry.notes || '',
    });
  };

  if (!initialized || loading) {
    return <View style={styles.centre}><ActivityIndicator color={COLORS.primary} /></View>;
  }

  return (
    <ScrollView
      style={styles.container}
      contentContainerStyle={{ padding: SPACING.lg, paddingTop: insets.top + SPACING.md, paddingBottom: insets.bottom + 60 }}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); void load(); }} />}
    >
      <Text style={styles.eyebrow}>INTERNAL</Text>
      <Text style={styles.title}>Knowledge entries</Text>
      <Text style={styles.body}>
        Everything starts as a draft. An entry cannot be approved without a source
        link somebody can open, and editing something published creates a new
        version rather than changing what is already out there.
      </Text>

      {!!error && <View style={styles.error}><Text style={styles.errorText}>{error}</Text></View>}
      {!!notice && <View style={styles.notice}><Text style={styles.noticeText}>{notice}</Text></View>}

      {/* ---- The form ---- */}
      <View style={styles.card} accessibilityLabel={editingId ? 'Edit entry' : 'Add entry'}>
        <Text style={styles.cardTitle}>{editingId ? 'Edit entry' : 'Add an entry'}</Text>
        <Field label="Subject type" value={form.subject_type} placeholder="ingredient"
               onChange={(v) => setForm({ ...form, subject_type: v })} />
        <Field label="Subject" value={form.subject} placeholder="turmeric"
               onChange={(v) => setForm({ ...form, subject: v })} />
        <Field label="Claim" value={form.claim} multiline placeholder="What is true, in one sentence"
               onChange={(v) => setForm({ ...form, claim: v })} />
        <View style={styles.row}>
          <View style={{ flex: 1 }}>
            <Field label="Value" value={form.value || ''} placeholder="600"
                   onChange={(v) => setForm({ ...form, value: v })} />
          </View>
          <View style={{ flex: 1 }}>
            <Field label="Unit" value={form.unit || ''} placeholder="mg per 100g"
                   onChange={(v) => setForm({ ...form, unit: v })} />
          </View>
        </View>
        <Field label="Source name" value={form.source_name} placeholder="IFCT 2017"
               onChange={(v) => setForm({ ...form, source_name: v })} />
        <Field label="Source URL" value={form.source_url || ''} placeholder="https://…"
               onChange={(v) => setForm({ ...form, source_url: v })} />

        <Text style={styles.label}>Evidence tier</Text>
        <View style={styles.chipWrap}>
          {(vocabulary?.evidence_tiers || []).map((tier) => (
            <TouchableOpacity
              key={tier}
              accessibilityRole="button"
              accessibilityLabel={`Tier ${tier}`}
              onPress={() => setForm({ ...form, evidence_tier: tier })}
              style={[styles.chip, form.evidence_tier === tier && styles.chipOn]}
            >
              <Text style={[styles.chipText, form.evidence_tier === tier && styles.chipTextOn]}>
                {tier.replace(/_/g, ' ')}
              </Text>
            </TouchableOpacity>
          ))}
        </View>

        <Field label="Notes" value={form.notes || ''} multiline placeholder="Anything a reviewer should know"
               onChange={(v) => setForm({ ...form, notes: v })} />

        <View style={styles.row}>
          <TouchableOpacity
            accessibilityRole="button"
            accessibilityLabel={editingId ? 'Save entry' : 'Add draft'}
            disabled={busy}
            onPress={submit}
            style={[styles.primary, busy && styles.disabled]}
          >
            <Text style={styles.primaryText}>{editingId ? 'Save' : 'Add draft'}</Text>
          </TouchableOpacity>
          {!!editingId && (
            <TouchableOpacity accessibilityRole="button" accessibilityLabel="Cancel edit"
                              onPress={() => { setEditingId(null); setForm(EMPTY); }}>
              <Text style={styles.link}>Cancel</Text>
            </TouchableOpacity>
          )}
        </View>
      </View>

      {/* ---- Filters ---- */}
      <Text style={styles.section}>Review queue</Text>
      <View style={styles.chipWrap}>
        <FilterChip label="All types" on={!filterType} onPress={() => setFilterType('')} />
        {(vocabulary?.subject_types || []).map((type) => (
          <FilterChip key={type} label={type} on={filterType === type} onPress={() => setFilterType(type)} />
        ))}
      </View>
      <View style={styles.chipWrap}>
        <FilterChip label="All statuses" on={!filterStatus} onPress={() => setFilterStatus('')} />
        {(vocabulary?.statuses || []).map((s) => (
          <FilterChip key={s} label={s} on={filterStatus === s} onPress={() => setFilterStatus(s)} />
        ))}
      </View>

      {/* ---- The queue ---- */}
      {entries.length === 0 && <Text style={styles.body}>Nothing here yet.</Text>}
      {entries.map((entry) => (
        <View key={entry.id} style={styles.card} accessibilityLabel={`Entry ${entry.subject}`}>
          <View style={styles.row}>
            <Text style={styles.status}>{entry.status}</Text>
            <Text style={styles.version}>v{entry.version}</Text>
          </View>
          <Text style={styles.cardTitle}>{entry.subject}</Text>
          <Text style={styles.body}>{entry.claim}</Text>
          {!!entry.value && <Text style={styles.meta}>{entry.value} {entry.unit || ''}</Text>}
          {!!entry.evidence_tier && <Text style={styles.meta}>{entry.evidence_tier.replace(/_/g, ' ')}</Text>}
          {!!entry.source && (
            <Text style={styles.meta}>
              {entry.source.name}{entry.source.url ? ` — ${entry.source.url}` : ' — no link yet'}
            </Text>
          )}
          {!!entry.rejection_reason && (
            <Text style={styles.rejected}>Rejected: {entry.rejection_reason}</Text>
          )}

          <View style={styles.row}>
            {entry.status === 'draft' && (
              <TouchableOpacity accessibilityRole="button" accessibilityLabel={`Approve ${entry.subject}`}
                                disabled={busy} onPress={() => act('Approved.', () => approveKnowledgeEntry(entry.id))}>
                <Text style={styles.link}>Approve</Text>
              </TouchableOpacity>
            )}
            {entry.status === 'approved' && (
              <TouchableOpacity accessibilityRole="button" accessibilityLabel={`Publish ${entry.subject}`}
                                disabled={busy} onPress={() => act('Published.', () => publishKnowledgeEntry(entry.id))}>
                <Text style={styles.link}>Publish</Text>
              </TouchableOpacity>
            )}
            {(entry.status === 'draft' || entry.status === 'approved') && (
              <TouchableOpacity accessibilityRole="button" accessibilityLabel={`Reject ${entry.subject}`}
                                onPress={() => { setRejectingId(entry.id); setRejectReason(''); }}>
                <Text style={styles.link}>Reject</Text>
              </TouchableOpacity>
            )}
            <TouchableOpacity accessibilityRole="button" accessibilityLabel={`Edit ${entry.subject}`}
                              onPress={() => startEdit(entry)}>
              <Text style={styles.link}>Edit</Text>
            </TouchableOpacity>
          </View>

          {rejectingId === entry.id && (
            <View>
              <Field label="Why is this being rejected?" value={rejectReason} multiline
                     placeholder="The reason is kept with the entry"
                     onChange={setRejectReason} />
              <TouchableOpacity
                accessibilityRole="button"
                accessibilityLabel="Confirm rejection"
                disabled={busy || !rejectReason.trim()}
                onPress={() => act('Rejected.', async () => {
                  const done = await rejectKnowledgeEntry(entry.id, rejectReason);
                  setRejectingId(null);
                  return done;
                })}
                style={[styles.primary, (!rejectReason.trim() || busy) && styles.disabled]}
              >
                <Text style={styles.primaryText}>Confirm rejection</Text>
              </TouchableOpacity>
            </View>
          )}
        </View>
      ))}

      {/* ---- CSV import ---- */}
      <Text style={styles.section}>Bulk import</Text>
      <View style={styles.card} accessibilityLabel="Bulk import">
        <Text style={styles.body}>
          Paste CSV with these columns: {(vocabulary?.csv_columns || []).join(', ')}.
          Everything imported arrives as a draft and still has to be approved.
        </Text>
        <Field label="CSV" value={csv} multiline placeholder="subject_type,subject_key,claim,…"
               onChange={setCsv} />
        <TouchableOpacity
          accessibilityRole="button"
          accessibilityLabel="Import CSV as drafts"
          disabled={busy || !csv.trim()}
          onPress={() => act('Imported as drafts.', async () => {
            const result = await importKnowledgeCsv(csv);
            setCsv('');
            if (result.error_count) {
              setError(`${result.created_count} added. ${result.error_count} row(s) skipped: `
                + result.errors.map((e) => `line ${e.line} — ${e.message}`).join('; '));
            }
            return result;
          })}
          style={[styles.primary, (busy || !csv.trim()) && styles.disabled]}
        >
          <Text style={styles.primaryText}>Import as drafts</Text>
        </TouchableOpacity>
      </View>
    </ScrollView>
  );
}

function Field({ label, value, onChange, placeholder, multiline }: {
  label: string; value: string; onChange: (value: string) => void;
  placeholder?: string; multiline?: boolean;
}) {
  return (
    <View>
      <Text style={styles.label}>{label}</Text>
      <TextInput
        accessibilityLabel={label}
        value={value}
        onChangeText={onChange}
        placeholder={placeholder}
        placeholderTextColor={COLORS.textMuted}
        multiline={multiline}
        style={[styles.input, multiline && styles.inputTall]}
      />
    </View>
  );
}

function FilterChip({ label, on, onPress }: { label: string; on: boolean; onPress: () => void }) {
  return (
    <TouchableOpacity accessibilityRole="button" accessibilityLabel={`Filter ${label}`}
                      onPress={onPress} style={[styles.chip, on && styles.chipOn]}>
      <Text style={[styles.chipText, on && styles.chipTextOn]}>{label.replace(/_/g, ' ')}</Text>
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  container: { backgroundColor: COLORS.backgroundSecondary, flex: 1 },
  centre: { alignItems: 'center', backgroundColor: COLORS.backgroundSecondary, flex: 1, justifyContent: 'center' },
  eyebrow: { color: COLORS.primary, fontFamily: FONTS.family.bodySemibold, fontSize: 10, letterSpacing: 1.4 },
  title: { color: COLORS.textPrimary, fontFamily: FONTS.family.heading, fontSize: 27, marginTop: 4 },
  section: { color: COLORS.textPrimary, fontFamily: FONTS.family.headingMedium, fontSize: 19, marginTop: SPACING.xl },
  body: { color: COLORS.textSecondary, fontFamily: FONTS.family.body, fontSize: 13, lineHeight: 19, marginTop: 6 },
  meta: { color: COLORS.textMuted, fontFamily: FONTS.family.body, fontSize: 12, marginTop: 3 },
  card: { backgroundColor: COLORS.card, borderColor: COLORS.border, borderRadius: RADIUS.lg, borderWidth: 1, marginTop: SPACING.md, padding: SPACING.md },
  cardTitle: { color: COLORS.textPrimary, fontFamily: FONTS.family.headingMedium, fontSize: 17, marginTop: 4 },
  label: { color: COLORS.textSecondary, fontFamily: FONTS.family.bodySemibold, fontSize: 12, marginTop: SPACING.sm },
  input: { backgroundColor: COLORS.backgroundSecondary, borderColor: COLORS.border, borderRadius: RADIUS.md, borderWidth: 1, color: COLORS.textPrimary, fontFamily: FONTS.family.body, fontSize: 14, marginTop: 4, padding: 10 },
  inputTall: { minHeight: 74, textAlignVertical: 'top' },
  row: { alignItems: 'center', flexDirection: 'row', gap: SPACING.md, marginTop: SPACING.sm },
  chipWrap: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: SPACING.sm },
  chip: { backgroundColor: COLORS.backgroundSecondary, borderColor: COLORS.border, borderRadius: RADIUS.full, borderWidth: 1, paddingHorizontal: 12, paddingVertical: 7 },
  chipOn: { backgroundColor: COLORS.primary, borderColor: COLORS.primary },
  chipText: { color: COLORS.textSecondary, fontFamily: FONTS.family.body, fontSize: 12 },
  chipTextOn: { color: COLORS.white },
  primary: { alignItems: 'center', backgroundColor: COLORS.primary, borderRadius: RADIUS.md, marginTop: SPACING.md, paddingHorizontal: 18, paddingVertical: 12 },
  disabled: { opacity: 0.5 },
  primaryText: { color: COLORS.white, fontFamily: FONTS.family.bodySemibold, fontSize: 14 },
  link: { color: COLORS.primary, fontFamily: FONTS.family.bodySemibold, fontSize: 13 },
  status: { color: COLORS.primary, fontFamily: FONTS.family.bodySemibold, fontSize: 11, letterSpacing: 1 },
  version: { color: COLORS.textMuted, fontFamily: FONTS.family.body, fontSize: 11 },
  rejected: { color: COLORS.error, fontFamily: FONTS.family.body, fontSize: 12, marginTop: 6 },
  error: { backgroundColor: COLORS.card, borderColor: COLORS.error, borderRadius: RADIUS.md, borderWidth: 1, marginTop: SPACING.md, padding: SPACING.md },
  errorText: { color: COLORS.error, fontFamily: FONTS.family.body, fontSize: 13 },
  notice: { backgroundColor: COLORS.card, borderColor: COLORS.border, borderRadius: RADIUS.md, borderWidth: 1, marginTop: SPACING.md, padding: SPACING.md },
  noticeText: { color: COLORS.textPrimary, fontFamily: FONTS.family.body, fontSize: 13 },
});
