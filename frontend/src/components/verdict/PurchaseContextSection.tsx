/**
 * YOUR PURCHASE CONTEXT — Step 14, beneath the Product Result's decision.
 *
 * One quiet section, not a dashboard. It composes what the authorities around
 * the Product Result already know — the official record, the shelf, the
 * person's own earlier choice, verified changes, the one comparable option —
 * into rows, and renders only the rows that have something to say.
 *
 * It never repeats the verdict. The decision is the block at the top of the
 * screen; this section says where that decision came from and what surrounds
 * it. Every word is keyed in src/strings/purchaseOs.ts.
 */
import React from 'react';
import { Linking, StyleSheet, Text, TouchableOpacity, View } from 'react-native';

import { PURCHASE_OS, fill } from '../../strings/purchaseOs';
import type { PurchaseOsCheck } from '../../services/apiV2';
import { answerLabel, contextRows } from '../../services/purchaseOsModel';
import { COLORS, FONTS, RADIUS, SPACING } from '../../theme/colors';

export function PurchaseContextSection({ check }: { check: PurchaseOsCheck }) {
  const rows = contextRows(check);
  return (
    <View style={styles.card} testID="purchase-context">
      <Text style={styles.eyebrow} accessibilityRole="header">{PURCHASE_OS.context.title}</Text>
      {rows.map((row) => (
        <View
          key={row.key}
          style={styles.row}
          testID={`purchase-context-${row.key}`}
          accessible={row.key === 'why'}
          accessibilityLabel={row.key === 'why' ? answerLabel(check) : undefined}
        >
          <Text style={styles.title}>{row.title}</Text>
          {row.lines.map((line) => <Text key={line} style={styles.body}>{line}</Text>)}
          {!!row.source && (
            <TouchableOpacity
              accessibilityRole="link"
              accessibilityLabel={fill(PURCHASE_OS.context.official.openSource, { name: row.source.name })}
              onPress={() => { void Linking.openURL(row.source!.url); }}
              style={styles.link}
            >
              <Text style={styles.linkText}>
                {fill(PURCHASE_OS.context.official.openSource, { name: row.source.name })}
              </Text>
            </TouchableOpacity>
          )}
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: COLORS.card, borderRadius: RADIUS.xl, padding: SPACING.lg,
    borderWidth: 1, borderColor: COLORS.border, marginTop: SPACING.md, marginBottom: SPACING.md,
  },
  eyebrow: {
    fontFamily: FONTS.family.bodySemibold, color: COLORS.accent, fontSize: 11,
    letterSpacing: 1.2, textTransform: 'uppercase',
  },
  row: { marginTop: SPACING.md },
  title: { fontFamily: FONTS.family.bodySemibold, fontSize: 14, color: COLORS.textPrimary },
  body: { fontFamily: FONTS.family.body, fontSize: 13, lineHeight: 19, color: COLORS.textSecondary, marginTop: 4 },
  link: { paddingVertical: SPACING.sm },
  linkText: { fontFamily: FONTS.family.bodySemibold, fontSize: 13, color: COLORS.primary },
});
