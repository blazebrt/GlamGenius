/**
 * One saved look, with the controls that change it.
 *
 * Swapping only ever offers items from the user's own confirmed inventory —
 * the picker is populated from the inventory API, so there is nothing to type
 * and nothing to invent.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, ScrollView, Share, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';

import { Lookboard, ReviseReasons, SLOT_LABELS } from '../src/components/style/StylePieces';
import {
  InventoryCategory, InventoryItem, Look, LookSlot, ReviseReason,
  getInventoryItems, getLook, reviseLook, sendLookFeedback, swapLookItem,
} from '../src/services/apiV2';
import { COLORS, FONTS, RADIUS, SPACING } from '../src/theme/colors';

const SLOT_CATEGORY: Record<LookSlot, InventoryCategory> = {
  clothing: 'wardrobe', shoes: 'shoes', accessories: 'accessories',
  perfume: 'perfumes', hair: 'hair', grooming: 'beauty',
};

export default function LookScreen() {
  const params = useLocalSearchParams<{ id?: string; swapSlot?: string }>();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [look, setLook] = useState<Look | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [swapSlot, setSwapSlot] = useState<LookSlot | null>((params.swapSlot as LookSlot) || null);
  const [choices, setChoices] = useState<InventoryItem[]>([]);
  const [revising, setRevising] = useState(false);

  const load = useCallback(async () => {
    if (!params.id) return;
    setLoading(true);
    setError(false);
    try {
      setLook(await getLook(params.id));
    } catch (err) {
      console.warn('look load failed', err);
      setError(true);
    } finally {
      setLoading(false);
    }
  }, [params.id]);

  useEffect(() => { void load(); }, [load]);

  const openSwap = useCallback(async (slot: LookSlot) => {
    setSwapSlot(slot);
    try {
      const list = await getInventoryItems({ category: SLOT_CATEGORY[slot], verification_state: 'confirmed', page_size: 50 });
      setChoices(list.items);
    } catch (err) {
      console.warn('swap options failed', err);
      setChoices([]);
    }
  }, []);

  useEffect(() => {
    if (params.swapSlot) void openSwap(params.swapSlot as LookSlot);
  }, [params.swapSlot, openSwap]);

  const doSwap = async (item: InventoryItem) => {
    if (!look || !swapSlot) return;
    const current = (look.slots[swapSlot] || []).find((piece) => piece.owned);
    try {
      setLook(await swapLookItem(look.id, swapSlot, item.id, current?.inventory_item_id || undefined));
      setSwapSlot(null);
    } catch (err) {
      console.warn('swap failed', err);
    }
  };

  const doRevise = async (reason?: ReviseReason) => {
    if (!look) return;
    try {
      setLook(await reviseLook(look.id, reason));
      setRevising(false);
    } catch (err) {
      console.warn('revise failed', err);
    }
  };

  if (loading) return <View style={styles.center}><ActivityIndicator color={COLORS.primary} /></View>;
  if (error || !look) {
    return (
      <View style={styles.center}>
        <Text style={styles.body}>We could not open that look.</Text>
        <TouchableOpacity accessibilityRole="button" accessibilityLabel="Try again" onPress={() => void load()}>
          <Text style={styles.link}>Try again</Text>
        </TouchableOpacity>
      </View>
    );
  }

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <View style={styles.top}>
        <TouchableOpacity accessibilityRole="button" accessibilityLabel="Back" onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={24} color={COLORS.textPrimary} />
        </TouchableOpacity>
        <Text style={styles.topTitle}>{look.title}</Text>
        <View style={{ width: 24 }} />
      </View>

      <ScrollView contentContainerStyle={{ padding: SPACING.lg, paddingBottom: insets.bottom + 48 }}>
        <Lookboard
          look={look}
          onSwap={(piece) => void openSwap(piece.slot)}
          onRevise={() => setRevising((value) => !value)}
          onSave={() => void sendLookFeedback(look.id, 'saved').then(setLook)}
          onReject={() => void sendLookFeedback(look.id, 'not_for_me').then(setLook)}
          onShare={() => void Share.share({ title: look.share.title, message: look.share.text })}
        />

        {revising && <ReviseReasons onPick={(reason) => void doRevise(reason)} />}

        {swapSlot && (
          <View style={styles.swapPanel} accessibilityLabel={`Choose a different ${SLOT_LABELS[swapSlot].toLowerCase()}`}>
            <View style={styles.swapHead}>
              <Text style={styles.swapTitle}>Choose a different {SLOT_LABELS[swapSlot].toLowerCase()}</Text>
              <TouchableOpacity accessibilityRole="button" accessibilityLabel="Close swap" onPress={() => setSwapSlot(null)}>
                <Ionicons name="close" size={20} color={COLORS.textMuted} />
              </TouchableOpacity>
            </View>
            <Text style={styles.body}>Only items you have confirmed you own are offered here.</Text>
            {choices.length === 0
              ? <Text style={styles.body}>Nothing confirmed in this category yet.</Text>
              : choices.map((item) => (
                <TouchableOpacity
                  key={item.id}
                  accessibilityRole="button"
                  accessibilityLabel={`Use ${item.display_name}`}
                  onPress={() => void doSwap(item)}
                  style={styles.choice}
                >
                  <Text style={styles.choiceName}>{item.display_name}</Text>
                  <Ionicons name="chevron-forward" size={17} color={COLORS.textMuted} />
                </TouchableOpacity>
              ))}
          </View>
        )}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.backgroundSecondary },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 8, backgroundColor: COLORS.background },
  top: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingHorizontal: SPACING.lg, paddingVertical: 12 },
  topTitle: { fontFamily: FONTS.family.bodySemibold, color: COLORS.textPrimary, flex: 1, textAlign: 'center' },
  body: { fontFamily: FONTS.family.body, fontSize: 13, lineHeight: 19, color: COLORS.textSecondary, marginTop: 6 },
  link: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.primary, marginTop: 8 },
  swapPanel: { backgroundColor: COLORS.card, borderRadius: RADIUS.xl, padding: SPACING.lg, borderWidth: 1, borderColor: COLORS.border, marginTop: SPACING.md },
  swapHead: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  swapTitle: { fontFamily: FONTS.family.headingMedium, fontSize: 18, color: COLORS.textPrimary, flex: 1 },
  choice: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: COLORS.borderLight },
  choiceName: { fontFamily: FONTS.family.bodyMedium, fontSize: 14, color: COLORS.textPrimary, flex: 1 },
});
