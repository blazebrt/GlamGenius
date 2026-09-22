/**
 * Product Watch — a quiet request to hear about one exact pack.
 *
 * Secondary to the verdict on purpose. It sits below the official record, in
 * the same understated card as the shelf, with no bell, no red and no alarm
 * vocabulary: watching records that a person asked, and says nothing about the
 * product.
 *
 * Three things this component deliberately does not do:
 *
 * * It never asks for notification permission. Watching and being told are
 *   separate choices, and the second one belongs to the Notifications screen.
 * * It never decides whether a pack can be watched. The server proves that
 *   from this phone's own confirmed capture and says so in ``watchable``.
 * * It never shows one product's state under another. Every value is stored
 *   with the barcode it was fetched for and read back only when that barcode
 *   is the one being rendered, so the A→B render window cannot leak.
 */
import React, { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, StyleSheet, Text, TouchableOpacity, View } from 'react-native';

import {
  readProductWatch, unwatchProduct, watchProduct, type ProductWatchState,
} from '../../services/productScan';
import { S } from '../../strings/productWatch';
import { COLORS, FONTS, RADIUS, SPACING } from '../../theme/colors';

type Signed<T> = { barcode: string; value: T };

function deliveryOff(state: ProductWatchState): boolean {
  const delivery = state.delivery;
  return !delivery || !delivery.notifications_enabled || !delivery.product_watch_enabled || !delivery.native_push_enabled;
}

export function ProductWatch({ barcode }: { barcode: string }) {
  const [signed, setSigned] = useState<Signed<ProductWatchState> | null>(null);
  const [busyFor, setBusyFor] = useState<string | null>(null);
  const [failedFor, setFailedFor] = useState<string | null>(null);
  const token = useRef(0);
  // A synchronous guard as well as the rendered one: two taps inside one frame
  // both see the button enabled, and the second must not send a second request.
  const inFlight = useRef(false);

  const state = signed !== null && signed.barcode === barcode && signed.value?.barcode === barcode
    ? signed.value : null;
  const busy = busyFor === barcode;
  const failed = failedFor === barcode;

  useEffect(() => {
    const current = ++token.current;
    inFlight.current = false;
    setSigned(null);
    setBusyFor(null);
    setFailedFor(null);
    void readProductWatch(barcode).then((value) => {
      if (token.current === current && value?.barcode === barcode) setSigned({ barcode, value });
    }).catch(() => { /* Unknown is shown as nothing, never as a guess. */ });
  }, [barcode]);

  if (!state) return null;

  const run = async (request: () => Promise<ProductWatchState>) => {
    if (inFlight.current) return;
    inFlight.current = true;
    const current = token.current;
    setBusyFor(barcode);
    setFailedFor(null);
    try {
      const value = await request();
      if (token.current === current && value?.barcode === barcode) setSigned({ barcode, value });
    } catch {
      if (token.current === current) {
        setFailedFor(barcode);
        // The server is the truth. Show what it now says rather than what the
        // tap hoped for; if even that fails, the last known state stands.
        try {
          const value = await readProductWatch(barcode);
          if (token.current === current && value?.barcode === barcode) setSigned({ barcode, value });
        } catch { /* keep the last state the server confirmed */ }
      }
    } finally {
      if (token.current === current) {
        inFlight.current = false;
        setBusyFor(null);
      }
    }
  };

  const anchorable = state.anchorable_label_version;
  const canAnchor = state.watchable && typeof anchorable === 'number' && anchorable >= 1;
  const watch = () => { if (canAnchor) void run(() => watchProduct(barcode, anchorable)); };
  const stop = () => { void run(() => unwatchProduct(barcode)); };

  return (
    <View style={styles.wrap} testID="product-watch">
      <Text style={styles.heading}>{S.heading}</Text>
      {state.watching ? (
        <>
          <Text style={styles.status} accessibilityLabel={S.a11y.watching}>{S.watching}</Text>
          {!state.watching_this_pack && <Text style={styles.copy}>{S.otherPack}</Text>}
          {deliveryOff(state) && <Text style={styles.copy}>{S.deliveryOff}</Text>}
          {!state.watching_this_pack && canAnchor && (
            <TouchableOpacity
              accessibilityRole="button" accessibilityLabel={S.a11y.watchThisPack}
              accessibilityState={{ disabled: busy, busy }}
              disabled={busy} style={styles.button} onPress={watch}
            >
              {busy ? <ActivityIndicator color={COLORS.primary} /> : <Text style={styles.buttonText}>{S.watchThisPack}</Text>}
            </TouchableOpacity>
          )}
          <TouchableOpacity
            accessibilityRole="button" accessibilityLabel={S.a11y.stop}
            accessibilityState={{ disabled: busy, busy }}
            disabled={busy} onPress={stop}
          >
            <Text style={styles.link}>{S.stop}</Text>
          </TouchableOpacity>
        </>
      ) : canAnchor ? (
        <>
          <Text style={styles.copy}>{S.explain}</Text>
          <TouchableOpacity
            accessibilityRole="button" accessibilityLabel={S.a11y.watch}
            accessibilityState={{ disabled: busy, busy }}
            disabled={busy} style={styles.button} onPress={watch}
          >
            {busy ? <ActivityIndicator color={COLORS.primary} /> : <Text style={styles.buttonText}>{S.watch}</Text>}
          </TouchableOpacity>
        </>
      ) : (
        <Text style={styles.copy}>{S.needsConfirmedPack}</Text>
      )}
      {failed && <Text style={styles.copy}>{S.failed}</Text>}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { marginTop: SPACING.lg, padding: SPACING.md, borderRadius: RADIUS.md, backgroundColor: COLORS.card },
  heading: { fontFamily: FONTS.family.bodySemibold, color: COLORS.textSecondary, fontSize: 11, letterSpacing: 1.2 },
  status: { fontFamily: FONTS.family.bodySemibold, color: COLORS.textPrimary, fontSize: 15, marginTop: SPACING.xs },
  copy: { fontFamily: FONTS.family.body, color: COLORS.textSecondary, fontSize: 13, lineHeight: 19, marginTop: SPACING.xs },
  button: {
    marginTop: SPACING.md, borderColor: COLORS.primary, borderWidth: 1, alignItems: 'center',
    padding: SPACING.sm, borderRadius: RADIUS.md,
  },
  buttonText: { color: COLORS.primary, fontFamily: FONTS.family.bodySemibold },
  link: { marginTop: SPACING.sm, color: COLORS.primary, fontFamily: FONTS.family.bodySemibold },
});
