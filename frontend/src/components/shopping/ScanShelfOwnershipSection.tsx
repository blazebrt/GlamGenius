import React, { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { useRouter } from 'expo-router';
import { addScanProductToShelf, readScanShelfStatus, type ScanShelfIdentity, type ScanShelfStatus } from '../../services/apiV2';
import { COLORS, FONTS, RADIUS, SPACING } from '../../theme/colors';

function sameShelfIdentity(candidate: unknown, expected: ScanShelfIdentity): boolean {
  if (!candidate || typeof candidate !== 'object') return false;
  const value = candidate as Partial<ScanShelfIdentity>;
  return value.barcode === expected.barcode
    && value.label_snapshot_id === expected.label_snapshot_id
    && value.label_version === expected.label_version
    && value.content_fingerprint === expected.content_fingerprint;
}

export function ScanShelfOwnershipSection({ identity }: { identity: ScanShelfIdentity }) {
  const { barcode, label_snapshot_id, label_version, content_fingerprint } = identity;
  const router = useRouter();
  const [status, setStatus] = useState<ScanShelfStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const token = useRef(0);
  const mutationKey = useRef<{ signature: string; id: string } | null>(null);
  const signature = `${barcode}|${label_snapshot_id}|${label_version}|${content_fingerprint}`;

  useEffect(() => {
    const current = ++token.current;
    setStatus(null);
    setBusy(false);
    const expected = { barcode, label_snapshot_id, label_version, content_fingerprint };
    void readScanShelfStatus(expected).then((value) => {
      if (token.current === current && sameShelfIdentity(value?.identity, expected)) setStatus(value);
    }).catch(() => { if (token.current === current) setStatus(null); });
  }, [barcode, label_snapshot_id, label_version, content_fingerprint]);

  if (!status || status.status === 'not_eligible' || status.status === 'not_enough_information') return null;
  if (status.status === 'owned') {
    if (!status.inventory_item_id) return null;
    return <View style={styles.wrap}><Text style={styles.title}>ON YOUR SHELF</Text><TouchableOpacity accessibilityRole="button" onPress={() => router.push(`/inventory-item?id=${status.inventory_item_id}`)}><Text style={styles.link}>View on your shelf</Text></TouchableOpacity></View>;
  }
  const add = async () => {
    setBusy(true);
    if (mutationKey.current?.signature !== signature) mutationKey.current = { signature, id: `${Date.now()}-${Math.random().toString(36).slice(2, 10)}` };
    const current = token.current;
    const expected = { barcode, label_snapshot_id, label_version, content_fingerprint };
    try {
      const value = await addScanProductToShelf({ ...expected, client_mutation_id: mutationKey.current.id });
      if (token.current === current && sameShelfIdentity(value?.identity, expected)) setStatus(value);
    } catch { /* Product Truth stays usable if this private action fails. */ } finally { if (token.current === current) setBusy(false); }
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
