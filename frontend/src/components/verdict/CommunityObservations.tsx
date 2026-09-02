import React from 'react';
import { Linking, StyleSheet, Text, TouchableOpacity, View } from 'react-native';

import type { VerdictSource } from '../../services/verdictModel';
import { S } from '../../strings/verdict';
import { COLORS, FONTS, RADIUS, SPACING } from '../../theme/colors';

/**
 * Shopper observations, below the scientific evidence and never competing with it.
 *
 * Renders nothing at all when there is no public signal. That silence is
 * deliberate: no signal can mean below threshold, outside the window, display
 * switched off, or a batch signal about a lot this shopper is not holding, so
 * "no reports" would be a claim the data does not support.
 *
 * No alarm styling, no photographs, no names. A count, what was seen, and a
 * visible way for the brand to answer.
 */
/** An openable HTTPS address, or the brand has no visible way to answer. */
export function isBrandReplyUrl(url: string | null | undefined): url is string {
  if (typeof url !== 'string' || url.trim() === '') return false;
  try {
    const parsed = new URL(url.trim());
    return parsed.protocol === 'https:' && parsed.host !== '';
  } catch {
    return false;
  }
}

export function CommunityObservations({
  communityObservations,
  onReport,
}: {
  communityObservations: VerdictSource['communityObservations'];
  onReport?: () => void;
}) {
  const signals = communityObservations?.signals ?? [];
  const replyUrl = communityObservations?.brand_reply_url ?? null;
  // Defence in depth. The server already fails closed, but the constitutional
  // rule — a visible right of reply before any shopper claim is published —
  // belongs to the rendered surface too. If the brand has no openable HTTPS
  // address here, the card does not render at all: publishing the claim and
  // merely omitting the link is the failure this guards against.
  const canPublish =
    communityObservations?.public_enabled === true
    && signals.length > 0
    && isBrandReplyUrl(replyUrl);
  if (!canPublish) return null;
  return (
    <View style={styles.container} accessible={false}>
      <Text style={styles.heading} accessibilityRole="header">{S.communityObservations.heading}</Text>
      <Text style={styles.disclosure}>{S.communityObservations.disclosure}</Text>
      {signals.map((signal) => {
        const observation =
          S.communityObservations.observation[signal.observation_code] ?? signal.observation_code;
        const line = S.communityObservations.reportedBy(signal.independent_reporters, observation);
        const batch = signal.scope === 'batch' && signal.batch_number ? signal.batch_number : null;
        return (
          <View
            key={`${signal.observation_code}:${signal.batch_number ?? 'product'}`}
            style={styles.row}
            accessible
            accessibilityLabel={
              batch
                ? `${line}. ${S.communityObservations.batch} ${batch}. ${S.communityObservations.disclosure}`
                : `${line}. ${S.communityObservations.disclosure}`
            }
          >
            <Text style={styles.line}>{line}</Text>
            {!!batch && (
              <Text style={styles.context}>{S.communityObservations.batch}: {batch}</Text>
            )}
          </View>
        );
      })}
      {!!replyUrl && (
        <TouchableOpacity
          accessibilityRole="link"
          accessibilityLabel={S.communityObservations.brandRightOfReply}
          onPress={() => void Linking.openURL(replyUrl)}
        >
          <Text style={styles.link}>{S.communityObservations.brandRightOfReply}</Text>
        </TouchableOpacity>
      )}
      {!!onReport && (
        <TouchableOpacity
          accessibilityRole="button"
          accessibilityLabel={S.communityObservations.reportAction}
          onPress={onReport}
        >
          <Text style={styles.secondaryAction}>{S.communityObservations.reportAction}</Text>
        </TouchableOpacity>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  // Deliberately the same quiet card as the rest of the lower screen. Alarm
  // red belongs to a regulator's finding, not to a count of shoppers.
  container: {
    backgroundColor: COLORS.card, borderColor: COLORS.border, borderRadius: RADIUS.md,
    borderWidth: 1, marginTop: SPACING.md, padding: SPACING.md,
  },
  heading: { color: COLORS.textPrimary, fontFamily: FONTS.family.heading, fontSize: 17 },
  disclosure: {
    color: COLORS.textSecondary, fontFamily: FONTS.family.body, fontSize: 13,
    lineHeight: 18, marginTop: SPACING.xs,
  },
  row: { marginTop: SPACING.sm },
  line: { color: COLORS.textPrimary, fontFamily: FONTS.family.body, fontSize: 14, lineHeight: 20 },
  context: { color: COLORS.textMuted, fontFamily: FONTS.family.body, fontSize: 12, marginTop: 2 },
  link: { color: COLORS.primary, fontFamily: FONTS.family.body, fontSize: 14, marginTop: SPACING.sm },
  secondaryAction: {
    color: COLORS.textSecondary, fontFamily: FONTS.family.body, fontSize: 14, marginTop: SPACING.sm,
  },
});
