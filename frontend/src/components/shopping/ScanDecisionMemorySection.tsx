import React, { useState } from 'react';
import { View, Text, TouchableOpacity, StyleSheet, ActivityIndicator } from 'react-native';
import { COLORS, FONTS, SPACING, RADIUS } from '../../theme/colors';
import { PurchaseMemoryCard } from './PurchaseMemoryCard';
import { saveScanDecision, type ScanDecisionMemory } from '../../services/apiV2';
import { S } from '../../strings/verdict';

export function ScanDecisionMemorySection({
  barcode,
  labelVersion,
  contentFingerprint,
  memory,
  onMemoryUpdated
}: {
  barcode: string;
  labelVersion: number;
  contentFingerprint: string;
  memory: ScanDecisionMemory;
  onMemoryUpdated: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [reconsidering, setReconsidering] = useState(false);

  const handleDecision = async (decision: 'BUY' | 'WAIT' | 'SKIP') => {
    setBusy(true);
    try {
      await saveScanDecision(barcode, {
        decision,
        label_version: labelVersion,
        content_fingerprint: contentFingerprint
      });
      setReconsidering(false);
      onMemoryUpdated();
    } catch {
      // API failures must not break Product Truth
    } finally {
      setBusy(false);
    }
  };

  const hasDecision = !!memory.decision && !reconsidering;
  const stateMap: Record<string, 'exact_prior_bought' | 'exact_prior_waiting' | 'exact_prior_skipped'> = {
    BUY: 'exact_prior_bought',
    WAIT: 'exact_prior_waiting',
    SKIP: 'exact_prior_skipped'
  };
  const guardState = memory.decision ? stateMap[memory.decision.decision] : null;

  return (
    <View style={styles.container}>
      <Text style={styles.title}>{S.forYou?.title || 'FOR YOU'}</Text>
      
      {busy ? (
        <View style={styles.loadingContainer}><ActivityIndicator color={COLORS.primary} /></View>
      ) : (
        <>
          {hasDecision && guardState && (
            <PurchaseMemoryCard
              guardState={guardState}
              occurredAt={memory.decision?.occurred_at || null}
              considerationCount={memory.history.length}
            />
          )}

          {!hasDecision && (
            <View style={styles.buttonRow}>
              <TouchableOpacity accessibilityRole="button" style={[styles.actionButton, { backgroundColor: COLORS.positive }]} onPress={() => void handleDecision('BUY')}><Text style={styles.actionText}>BUY</Text></TouchableOpacity>
              <TouchableOpacity accessibilityRole="button" style={[styles.actionButton, { backgroundColor: COLORS.warning }]} onPress={() => void handleDecision('WAIT')}><Text style={styles.actionText}>WAIT</Text></TouchableOpacity>
              <TouchableOpacity accessibilityRole="button" style={[styles.actionButton, { backgroundColor: COLORS.negative }]} onPress={() => void handleDecision('SKIP')}><Text style={styles.actionText}>SKIP</Text></TouchableOpacity>
            </View>
          )}

          {hasDecision && (
            <TouchableOpacity accessibilityRole="button" style={styles.reconsiderButton} onPress={() => setReconsidering(true)}>
              <Text style={styles.reconsiderText}>Reconsider</Text>
            </TouchableOpacity>
          )}
          {reconsidering && (
            <TouchableOpacity accessibilityRole="button" style={styles.reconsiderButton} onPress={() => setReconsidering(false)}>
              <Text style={styles.reconsiderText}>Cancel</Text>
            </TouchableOpacity>
          )}
        </>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { marginTop: SPACING.xl },
  title: { fontFamily: FONTS.family.heading, fontSize: 26, color: COLORS.textPrimary, marginBottom: SPACING.sm },
  buttonRow: { flexDirection: 'row', gap: SPACING.sm, marginBottom: SPACING.md },
  actionButton: { flex: 1, padding: SPACING.md, borderRadius: RADIUS.md, alignItems: 'center' },
  actionText: { color: COLORS.textInverse, fontFamily: FONTS.family.bodySemibold, fontSize: 14 },
  loadingContainer: { padding: SPACING.xl, alignItems: 'center' },
  reconsiderButton: { alignItems: 'center', padding: SPACING.md },
  reconsiderText: { color: COLORS.primary, fontFamily: FONTS.family.bodySemibold, fontSize: 14 }
});
