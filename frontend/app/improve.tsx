/**
 * You → Improve.
 *
 * The routine overview, how consistently it is going, what needs attention,
 * what is running out, what is going unused, and which routine steps have
 * nothing in them.
 *
 * Every module here is conditional on there being something to show. A user
 * who has catalogued nothing sees one honest empty state and a way in, not six
 * cards full of zeroes.
 */
import React, { useCallback, useState } from 'react';
import { RefreshControl, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { useFocusEffect, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';

import {
  ConsistencyCard, EmptyModule, ExpiringSection, LowUseSection, PausedCareProducts, RoutineCard, WarningList,
} from '../src/components/routines/RoutinePieces';
import type { CareProductAction } from '../src/components/routines/RoutinePieces';
import { CareExperienceFeedbackSheet } from '../src/components/routines/CareExperienceFeedback';
import {
  ImproveOverview, Routine, RoutineStep, completeRoutineStep, generateRoutines, getImproveOverview,
  pauseCareProduct, preferCareProduct, resumeCareProduct, simplifyCareRoutine, unpreferCareProduct,
} from '../src/services/apiV2';
import type { CareProductControl } from '../src/services/apiV2';
import { COLORS, FONTS, RADIUS, SPACING } from '../src/theme/colors';

export default function ImproveScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [overview, setOverview] = useState<ImproveOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionBusyId, setActionBusyId] = useState<string | null>(null);
  const [retryCare, setRetryCare] = useState<{ product: CareProductControl; action: CareProductAction } | null>(null);
  const [feedbackTarget, setFeedbackTarget] = useState<{ routine: Routine; step: RoutineStep } | null>(null);

  const load = useCallback(async (mode: 'initial' | 'refresh' = 'initial') => {
    if (mode === 'refresh') setRefreshing(true);
    setError(false);
    try {
      setOverview(await getImproveOverview());
    } catch (err) {
      console.warn('improve load failed', err);
      setError(true);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useFocusEffect(useCallback(() => { void load('refresh'); }, [load]));

  const build = async () => {
    setBusy(true);
    try {
      await generateRoutines({});
      await load('refresh');
    } catch (err) {
      console.warn('generate failed', err);
    } finally {
      setBusy(false);
    }
  };

  const simplify = async () => {
    if (actionBusyId) return;
    setActionBusyId('simplify'); setActionError(null); setRetryCare(null);
    try { await simplifyCareRoutine(); await load('refresh'); }
    catch (err) { console.warn('simplify failed', err); setActionError('We could not simplify your routine just now. Your current routine is unchanged.'); }
    finally { setActionBusyId(null); }
  };

  const careAction = async (product: CareProductControl, action: CareProductAction) => {
    if (actionBusyId) return;
    setActionBusyId(product.inventory_item_id); setActionError(null); setRetryCare(null);
    try {
      if (action === 'pause') await pauseCareProduct(product.inventory_item_id);
      if (action === 'resume') await resumeCareProduct(product.inventory_item_id);
      if (action === 'prefer') await preferCareProduct(product.inventory_item_id);
      if (action === 'unprefer') await unpreferCareProduct(product.inventory_item_id);
      await load('refresh');
    } catch (err) { console.warn('Care choice failed', err); setRetryCare({ product, action }); setActionError('We could not save that routine choice just now. Your current routine is unchanged.'); }
    finally { setActionBusyId(null); }
  };

  const onComplete = async (step: RoutineStep) => {
    if (!step.id) return;
    try {
      await completeRoutineStep(step.id, !step.completed_today);
      await load('refresh');
    } catch (err) { console.warn('complete failed', err); }
  };

  if (loading && !overview) {
    return <View style={styles.centre}><Text style={styles.muted}>Opening your routines…</Text></View>;
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
      >
        <View style={styles.header}>
          <TouchableOpacity accessibilityRole="button" accessibilityLabel="Go back" onPress={() => router.back()}>
            <Ionicons name="chevron-back" size={24} color={COLORS.textPrimary} />
          </TouchableOpacity>
          <View>
            <Text style={styles.eyebrow}>YOU</Text>
            <Text style={styles.title}>Improve</Text>
          </View>
        </View>

        {!overview.has_shelf ? (
          <EmptyModule
            title="Nothing on your shelf yet"
            body="Add a face wash, a moisturiser or a shampoo you already own, and we can build a routine out of what you have."
            actionLabel="Add a product"
            onPress={() => router.push({ pathname: '/inventory-add', params: { category: 'beauty' } })}
          />
        ) : !overview.has_routines ? (
          <EmptyModule
            title="Ready to build your routine"
            body="We will use only the products you have already recorded, in the order they are usually used."
            actionLabel={busy ? 'Building…' : 'Build my routine'}
            onPress={() => void build()}
          />
        ) : (
          <>
            <Text style={styles.section}>Your routines</Text>
            {overview.routine_effort?.can_simplify ? <TouchableOpacity
              accessibilityRole="button"
              accessibilityLabel="Simplify my routine"
              accessibilityState={{ disabled: actionBusyId === 'simplify' }}
              onPress={() => void simplify()}
              disabled={actionBusyId !== null}
              style={styles.secondaryAction}
            >
              <Ionicons name="sparkles-outline" size={18} color={COLORS.primary} />
              <Text style={styles.secondaryActionText}>{actionBusyId === 'simplify' ? 'Simplifying…' : 'Simplify my routine'}</Text>
            </TouchableOpacity> : null}
            {overview.routines.map((routine) => (
              <RoutineCard
                key={routine.id ?? routine.kind}
                routine={routine}
                onComplete={(step) => void onComplete(step)}
                onFeedback={(step) => setFeedbackTarget({ routine, step })}
                careProducts={overview.care_product_controls}
                onCareAction={(product, action) => void careAction(product, action)}
                careActionBusyId={actionBusyId}
              />
            ))}

            <PausedCareProducts products={overview.care_product_controls} onCareAction={(product, action) => void careAction(product, action)} careActionBusyId={actionBusyId} />

            <Text style={styles.section}>How it is going</Text>
            <ConsistencyCard consistency={overview.consistency} />
          </>
        )}

        {!!actionError && <View>
          <Text accessibilityRole="alert" style={styles.actionError}>{actionError}</Text>
          <TouchableOpacity accessibilityRole="button" accessibilityLabel="Try again" onPress={() => retryCare ? void careAction(retryCare.product, retryCare.action) : void simplify()}>
            <Text style={styles.link}>Try again</Text>
          </TouchableOpacity>
        </View>}

        {!!overview.needs_attention.length && (
          <>
            <Text style={styles.section}>Needs your attention</Text>
            <WarningList warnings={overview.needs_attention} />
          </>
        )}

        {overview.has_shelf && (
          <>
            <ExpiringSection report={overview.expiring} />
            <LowUseSection low={overview.low_use} />
          </>
        )}

        {!!overview.missing_categories.length && (
          <>
            <Text style={styles.section}>Steps with nothing in them</Text>
            {/* Named as a category, never as a product to buy. */}
            <WarningList warnings={overview.missing_categories} />
          </>
        )}

        {!!overview.counts.awaiting_confirmation && (
          <TouchableOpacity
            accessibilityRole="button"
            accessibilityLabel="Confirm ingredients we read at low confidence"
            onPress={() => router.push('/(tabs)/inventory')}
            style={styles.confirm}
          >
            <Ionicons name="help-circle-outline" size={19} color={COLORS.primary} />
            <Text style={styles.confirmText}>
              {overview.counts.awaiting_confirmation} ingredient
              {overview.counts.awaiting_confirmation === 1 ? '' : 's'} to confirm. We do not warn about anything until you do.
            </Text>
          </TouchableOpacity>
        )}

        {/* The smallest coherent way into Maintenance from Care, until the
            final navigation gives Care a destination of its own. */}
        <TouchableOpacity
          accessibilityRole="button"
          accessibilityLabel="Open upkeep timing"
          onPress={() => router.push('/(tabs)/services')}
          style={styles.confirm}
        >
          <Ionicons name="time-outline" size={19} color={COLORS.primary} />
          <Text style={styles.confirmText}>
            Upkeep timing — haircuts, trims and other routine upkeep, on the rhythm you choose.
          </Text>
        </TouchableOpacity>

        <Text style={styles.disclaimer}>{overview.disclaimer}</Text>
      </ScrollView>
      {!!feedbackTarget?.step.id && (
        <CareExperienceFeedbackSheet
          open={feedbackTarget !== null}
          subjectType="routine_step"
          subjectId={feedbackTarget.step.id}
          subjectLabel={`${feedbackTarget.routine.label} · ${feedbackTarget.step.label}`}
          onClose={() => setFeedbackTarget(null)}
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.backgroundSecondary },
  centre: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 8, backgroundColor: COLORS.background },
  header: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 6 },
  eyebrow: { fontFamily: FONTS.family.bodySemibold, color: COLORS.primary, fontSize: 10, letterSpacing: 1.4 },
  title: { fontFamily: FONTS.family.heading, color: COLORS.textPrimary, fontSize: 29, marginTop: 2 },
  section: { fontFamily: FONTS.family.headingMedium, color: COLORS.textPrimary, fontSize: 18, marginTop: 22 },
  muted: { fontFamily: FONTS.family.body, color: COLORS.textSecondary, fontSize: 13 },
  link: { fontFamily: FONTS.family.bodySemibold, color: COLORS.primary, fontSize: 13 },
  confirm: { flexDirection: 'row', gap: 9, alignItems: 'flex-start', backgroundColor: COLORS.primaryLight, borderRadius: RADIUS.lg, borderWidth: 1, borderColor: COLORS.primaryMuted, padding: 13, marginTop: 14 },
  confirmText: { flex: 1, fontFamily: FONTS.family.body, color: COLORS.textPrimary, fontSize: 12, lineHeight: 18 },
  disclaimer: { fontFamily: FONTS.family.body, color: COLORS.textMuted, fontSize: 11, textAlign: 'center', marginTop: SPACING.lg },
  secondaryAction: { flexDirection: 'row', alignItems: 'center', alignSelf: 'flex-start', gap: 7, borderWidth: 1, borderColor: COLORS.primaryMuted, borderRadius: RADIUS.full, paddingHorizontal: 13, paddingVertical: 9, marginTop: 10 },
  secondaryActionText: { fontFamily: FONTS.family.bodySemibold, color: COLORS.primary, fontSize: 12 },
  actionError: { fontFamily: FONTS.family.body, color: COLORS.error ?? '#B4231F', fontSize: 12, lineHeight: 17, marginTop: 10 },
});
