import React, { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { useRouter } from 'expo-router';
import { addScanProductToShelf, readScanShelfStatus, type ScanShelfIdentity, type ScanShelfStatus } from '../../services/apiV2';
import { COLORS, FONTS, RADIUS, SPACING } from '../../theme/colors';

/**
 * Private shelf ownership for one exact scanned pack.
 *
 * Every renderable piece of state here is stored together with the identity
 * signature it was fetched for, and is read back only when that signature
 * still matches the identity being rendered. That is the point of this file.
 *
 * Why clearing state in an effect is not enough: props change and React
 * renders *before* effects run. On the transition from pack A to pack B there
 * is therefore a committed render in which the props are B's and the state is
 * still A's, and an effect that clears state cannot prevent that render — it
 * only cleans up afterwards. In that window the previous version showed A's
 * "ON YOUR SHELF" heading under B, offered a link to A's inventory item, and
 * inherited A's disabled button. Comparing signatures during render closes the
 * window rather than shortening it.
 */

function shelfSignature(identity: ScanShelfIdentity): string {
  return `${identity.barcode}|${identity.label_snapshot_id}|${identity.label_version}|${identity.content_fingerprint}`;
}

function sameShelfIdentity(candidate: unknown, expected: ScanShelfIdentity): boolean {
  if (!candidate || typeof candidate !== 'object') return false;
  const value = candidate as Partial<ScanShelfIdentity>;
  return value.barcode === expected.barcode
    && value.label_snapshot_id === expected.label_snapshot_id
    && value.label_version === expected.label_version
    && value.content_fingerprint === expected.content_fingerprint;
}

/** A value and the exact identity signature it belongs to, never separated. */
type SignedValue<T> = { signature: string; value: T };

export function ScanShelfOwnershipSection({ identity }: { identity: ScanShelfIdentity }) {
  const { barcode, label_snapshot_id, label_version, content_fingerprint } = identity;
  const router = useRouter();
  const [signedStatus, setSignedStatus] = useState<SignedValue<ScanShelfStatus> | null>(null);
  const [signedBusy, setSignedBusy] = useState<SignedValue<boolean> | null>(null);
  const token = useRef(0);
  const mutationKey = useRef<{ signature: string; id: string } | null>(null);
  const signature = shelfSignature(identity);

  // Read back only what belongs to the identity being rendered right now.
  // Anything carrying a different signature is another pack's answer and is
  // not ours to show, whatever the effects have or have not run yet.
  const status = signedStatus !== null && signedStatus.signature === signature ? signedStatus.value : null;
  const busy = signedBusy !== null && signedBusy.signature === signature ? signedBusy.value : false;

  useEffect(() => {
    const current = ++token.current;
    const expected = { barcode, label_snapshot_id, label_version, content_fingerprint };
    const expectedSignature = shelfSignature(expected);
    // Housekeeping, not the boundary guarantee: the signature checks above
    // already make this state unreadable under any other identity. Dropping it
    // stops one pack's earlier answer being shown again unrefreshed if the
    // same pack comes back around within this mount.
    setSignedStatus(null);
    setSignedBusy(null);
    void readScanShelfStatus(expected).then((value) => {
      if (token.current === current && sameShelfIdentity(value?.identity, expected)) {
        setSignedStatus({ signature: expectedSignature, value });
      }
    }).catch(() => { if (token.current === current) setSignedStatus(null); });
  }, [barcode, label_snapshot_id, label_version, content_fingerprint]);

  if (!status || status.status === 'not_eligible' || status.status === 'not_enough_information') return null;
  if (status.status === 'owned') {
    if (!status.inventory_item_id) return null;
    return <View style={styles.wrap}><Text style={styles.title}>ON YOUR SHELF</Text><TouchableOpacity accessibilityRole="button" onPress={() => router.push(`/inventory-item?id=${status.inventory_item_id}`)}><Text style={styles.link}>View on your shelf</Text></TouchableOpacity></View>;
  }
  const add = async () => {
    const expected = { barcode, label_snapshot_id, label_version, content_fingerprint };
    const expectedSignature = shelfSignature(expected);
    setSignedBusy({ signature: expectedSignature, value: true });
    if (mutationKey.current?.signature !== expectedSignature) mutationKey.current = { signature: expectedSignature, id: `${Date.now()}-${Math.random().toString(36).slice(2, 10)}` };
    const current = token.current;
    try {
      const value = await addScanProductToShelf({ ...expected, client_mutation_id: mutationKey.current.id });
      if (token.current === current && sameShelfIdentity(value?.identity, expected)) {
        setSignedStatus({ signature: expectedSignature, value });
      }
    } catch { /* Product Truth stays usable if this private action fails. */ } finally { if (token.current === current) setSignedBusy({ signature: expectedSignature, value: false }); }
  };
  return <View style={styles.wrap}><Text style={styles.title}>SHELF</Text><Text style={styles.copy}>This records that you own this exact product.</Text><TouchableOpacity accessibilityRole="button" accessibilityLabel="Add to shelf" disabled={busy} style={styles.button} onPress={() => void add()}>{busy ? <ActivityIndicator color={COLORS.textInverse} /> : <Text style={styles.buttonText}>Add to shelf</Text>}</TouchableOpacity></View>;
}

const styles = StyleSheet.create({
  wrap: { marginTop: SPACING.xl, padding: SPACING.md, borderRadius: RADIUS.md, backgroundColor: COLORS.card },
  title: { fontFamily: FONTS.family.heading, color: COLORS.textPrimary, fontSize: 18 },
  copy: { fontFamily: FONTS.family.body, color: COLORS.textSecondary, marginTop: SPACING.xs },
  button: { marginTop: SPACING.md, backgroundColor: COLORS.primary, alignItems: 'center', padding: SPACING.md, borderRadius: RADIUS.md },
  buttonText: { color: COLORS.textInverse, fontFamily: FONTS.family.bodySemibold },
  link: { marginTop: SPACING.sm, color: COLORS.primary, fontFamily: FONTS.family.bodySemibold },
});
