/**
 * Step 16: one disclosed outbound link, subordinate to the decision.
 *
 * Rendered only for a handoff the server made available and the screen
 * matched to the pack and the decision it already shows. Otherwise there is
 * nothing at all: no empty card, no disabled button, no "coming soon".
 *
 * The disclosure comes first, in the same block as the action, and it stays
 * where it is after the link is opened. The action is a plain link in the
 * quiet style of the other lower-screen links: never a filled button, never
 * above or louder than the decision.
 *
 * The link opens a partner's search for one barcode. A listing there may be a
 * different batch or label than the pack that was graded, so the person is
 * told to scan the pack they receive before using it.
 *
 * No string is written here. Every word comes from src/strings/commerce.ts.
 */
import React, { useCallback, useRef, useState } from 'react';
import { StyleSheet, Text, TouchableOpacity, View } from 'react-native';

import {
  commerceOpenEvent, openCommerceHandoff, recordCommerceOpen, type CommerceHandoff as Handoff,
} from '../../services/commerce';
import { COMMERCE, fill } from '../../strings/commerce';
import { COLORS, FONTS, SPACING } from '../../theme/colors';

export function CommerceHandoff({
  handoff,
  signedIn,
  testID,
}: {
  handoff: Handoff;
  /** Opens are recorded for signed-in accounts only; an anonymous open is not. */
  signedIn: boolean;
  testID?: string;
}) {
  const [failed, setFailed] = useState(false);
  const opening = useRef(false);
  const partner = COMMERCE.partners[handoff.partner.key];
  const disclosure = COMMERCE.disclosure[handoff.partner.key];
  const action = COMMERCE.action[handoff.target];

  const onOpen = useCallback(async () => {
    if (opening.current) return;
    opening.current = true;
    try {
      const opened = await openCommerceHandoff(handoff);
      setFailed(!opened);
      if (opened && signedIn) void recordCommerceOpen(commerceOpenEvent(handoff));
    } finally {
      opening.current = false;
    }
  }, [handoff, signedIn]);

  return (
    <View style={styles.container} testID={testID}>
      <Text style={styles.disclosure}>{disclosure}</Text>
      <TouchableOpacity
        accessibilityRole="link"
        accessibilityLabel={fill(COMMERCE.a11y.action, { action, partner })}
        onPress={() => void onOpen()}
        hitSlop={8}
      >
        <Text style={styles.action}>{action}</Text>
      </TouchableOpacity>
      <Text style={styles.note}>{fill(COMMERCE.destination, { partner })}</Text>
      <Text style={styles.note}>{COMMERCE.packNotice}</Text>
      {failed && <Text style={styles.note} accessibilityLiveRegion="polite">{COMMERCE.openFailed}</Text>}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { marginTop: SPACING.md },
  // Readable, never muted into fine print: the disclosure has to be seen.
  disclosure: { color: COLORS.textSecondary, fontFamily: FONTS.family.body, fontSize: 13, lineHeight: 18 },
  // The same weight as the screen's other quiet links, and no more.
  action: { color: COLORS.primary, fontFamily: FONTS.family.bodySemibold, fontSize: 14, marginTop: SPACING.xs },
  note: { color: COLORS.textSecondary, fontFamily: FONTS.family.body, fontSize: 12, lineHeight: 17, marginTop: 2 },
});
