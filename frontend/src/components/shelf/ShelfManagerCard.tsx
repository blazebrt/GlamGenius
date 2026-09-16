import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ActivityIndicator, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { useRouter } from 'expo-router';

import {
  getShelfManager,
  respondToShelfManager,
  type ShelfManagerChoice,
  type ShelfManagerDecision,
  type ShelfManagerQueue,
} from '../../services/apiV2';
import { S } from '../../strings/shelfManager';
import { COLORS, FONTS, RADIUS, SPACING } from '../../theme/colors';

/**
 * One decision, at the top of the Skin & Hair Care tab.
 *
 * Three things about this file are deliberate.
 *
 * **It says nothing of its own.** The decision, the reason, the button and the
 * override all arrive from the server as reviewed copy. The only words written
 * here are the two headings and the "n more after this" line, which live in
 * ``src/strings/shelfManager.ts``.
 *
 * **It cannot break the tab.** If the manager call fails the card renders
 * nothing at all and the rest of the shelf is untouched. A decision queue going
 * down must never take the inventory with it.
 *
 * **Its busy state is bound to the decision, not to the component.** Everything
 * that can be rendered is stored together with the decision signature it
 * belongs to, and is read back only while that signature still matches what is
 * on screen. Props and state move at different moments — React renders before
 * effects run — so an effect that clears state cannot prevent the frame in
 * which the previous decision's spinner sits under the next decision's words.
 * Comparing signatures during render closes that window rather than shortening
 * it.
 */

/** The exact decision a piece of state belongs to. */
function decisionSignature(decision: ShelfManagerDecision): string {
  return `${decision.decision_key}|${decision.decision_fingerprint}`;
}

type SignedValue<T> = { signature: string; value: T };

export function ShelfManagerCard({
  reloadToken = 0,
  onChanged,
}: {
  /** Bump to re-read the queue, e.g. when the screen regains focus. */
  reloadToken?: number;
  /** Called after an answer is applied, so the screen can refresh its own data. */
  onChanged?: () => void;
}) {
  const router = useRouter();
  const [queue, setQueue] = useState<ShelfManagerQueue | null>(null);
  const [signedBusy, setSignedBusy] = useState<SignedValue<boolean> | null>(null);
  const request = useRef(0);
  const mutationKey = useRef<{ signature: string; id: string } | null>(null);

  const load = useCallback(async () => {
    const current = ++request.current;
    try {
      const value = await getShelfManager();
      if (request.current === current) setQueue(value);
    } catch {
      // The manager is one card on a screen full of other things. If it cannot
      // answer, it shows nothing; the shelf carries on.
      if (request.current === current) setQueue(null);
    }
  }, []);

  useEffect(() => { void load(); }, [load, reloadToken]);

  const primary = queue?.primary ?? null;
  const signature = primary ? decisionSignature(primary) : null;
  // Read back only what belongs to the decision being rendered right now.
  const busy = signedBusy !== null && signedBusy.signature === signature ? signedBusy.value : false;

  if (!primary || !signature) return null;

  /** Returns whether this answer landed and is still the current one. */
  const answer = async (choice: ShelfManagerChoice): Promise<boolean> => {
    const decision = primary;
    const expected = decisionSignature(decision);
    // One submission key per decision, reused on a retry of that same decision
    // and never shared with a different one.
    if (mutationKey.current?.signature !== expected) {
      mutationKey.current = {
        signature: expected,
        id: `mgr-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`,
      };
    }
    setSignedBusy({ signature: expected, value: true });
    const current = ++request.current;
    try {
      const value = await respondToShelfManager({
        decision_key: decision.decision_key,
        decision_fingerprint: decision.decision_fingerprint,
        choice,
        client_mutation_id: mutationKey.current.id,
      });
      if (request.current !== current) return false;
      setQueue(value);
      onChanged?.();
      return true;
    } catch {
      // The shelf moved under us, or the answer did not land. Re-read rather
      // than guess: the server is the only thing that knows what is true now.
      if (request.current === current) await load();
      return false;
    } finally {
      // Clear the busy flag this call set, and only that one. Keying it on the
      // request token instead would leave the spinner running forever whenever
      // the catch re-read the queue, because the re-read takes the next token.
      setSignedBusy((value) => (
        value?.signature === expected ? { signature: expected, value: false } : value
      ));
    }
  };

  const openFor = (decision: ShelfManagerDecision) => {
    switch (decision.action.kind) {
      case 'confirm_label':
      case 'record_date':
      case 'open_inventory_item':
        if (decision.action.inventory_item_id) {
          router.push(`/inventory-item?id=${decision.action.inventory_item_id}`);
        }
        return;
      case 'add_owned_product':
        router.push({
          pathname: '/inventory-add',
          params: { category: decision.category === 'hair' ? 'hair' : 'beauty' },
        });
        return;
      case 'open_routine':
        router.push('/improve');
        return;
      default:
        return;
    }
  };

  const accept = async () => {
    const decision = primary;
    const landed = await answer('accept');
    // A navigation action is recorded and then taken. Recording it resolves
    // nothing on its own, which is exactly what the server says too.
    //
    // It is only taken if the answer landed. A refused answer means the server
    // would not produce that decision any more — the product may be gone — so
    // opening the screen it named would send somebody somewhere that is no
    // longer true. They stay here and see what the manager says now.
    if (landed && !decision.action.mutates) openFor(decision);
  };

  const giveBack = primary.kind === 'give_back';
  const heading = giveBack ? S.headingGiveBack : S.heading;
  const remaining = queue ? queue.remaining_count : 0;

  return (
    <View
      style={styles.card}
      accessibilityLabel={`${heading}. ${primary.decision} ${primary.reason}. ${primary.evidence_note}`}
    >
      <Text style={styles.eyebrow}>{heading}</Text>
      <Text style={styles.decision}>{primary.decision}</Text>
      <Text style={styles.reason}>{primary.reason}</Text>
      {/*
        LEGAL_RULES.md rule 6: every negative statement carries its source,
        visible without tapping. The note names what the decision was worked
        out from — a date the person recorded, a reviewed rule, a confirmed
        ingredient — so no claim on this card stands on its own word.
      */}
      {!!primary.evidence_note && (
        <Text style={styles.evidence}>{primary.evidence_note}</Text>
      )}

      <TouchableOpacity
        accessibilityRole="button"
        accessibilityLabel={primary.action.label}
        accessibilityState={{ disabled: busy, busy }}
        disabled={busy}
        onPress={() => void accept()}
        style={[styles.primaryButton, busy && styles.primaryButtonBusy]}
      >
        {busy
          ? <ActivityIndicator color={COLORS.textInverse} />
          : <Text style={styles.primaryButtonText}>{primary.action.label}</Text>}
      </TouchableOpacity>

      <TouchableOpacity
        accessibilityRole="button"
        accessibilityLabel={primary.override.label}
        accessibilityState={{ disabled: busy }}
        disabled={busy}
        onPress={() => void answer('override')}
        style={styles.override}
      >
        <Text style={styles.overrideText}>{primary.override.label}</Text>
      </TouchableOpacity>

      {remaining > 0 && (
        <Text style={styles.remaining}>{S.remaining(remaining)}</Text>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: COLORS.card,
    borderRadius: RADIUS.lg,
    borderWidth: 1,
    borderColor: COLORS.border,
    padding: SPACING.md,
    marginBottom: SPACING.md,
  },
  eyebrow: {
    fontFamily: FONTS.family.bodySemibold,
    color: COLORS.primary,
    fontSize: 10,
    letterSpacing: 1.4,
  },
  decision: {
    fontFamily: FONTS.family.headingMedium,
    color: COLORS.textPrimary,
    fontSize: 18,
    marginTop: 6,
  },
  reason: {
    fontFamily: FONTS.family.body,
    color: COLORS.textSecondary,
    fontSize: 13,
    lineHeight: 19,
    marginTop: 5,
  },
  evidence: {
    fontFamily: FONTS.family.body,
    color: COLORS.textMuted,
    fontSize: 11,
    lineHeight: 16,
    marginTop: 6,
  },
  primaryButton: {
    marginTop: SPACING.md,
    backgroundColor: COLORS.primary,
    alignItems: 'center',
    paddingVertical: 13,
    borderRadius: RADIUS.md,
  },
  primaryButtonBusy: { opacity: 0.7 },
  primaryButtonText: {
    color: COLORS.textInverse,
    fontFamily: FONTS.family.bodySemibold,
    fontSize: 13,
  },
  override: { alignItems: 'center', paddingVertical: 11 },
  overrideText: {
    fontFamily: FONTS.family.bodyMedium,
    color: COLORS.textSecondary,
    fontSize: 12,
  },
  remaining: {
    fontFamily: FONTS.family.body,
    color: COLORS.textMuted,
    fontSize: 11,
    textAlign: 'center',
  },
});
