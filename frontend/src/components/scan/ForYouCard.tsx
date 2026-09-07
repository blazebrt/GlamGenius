/**
 * The personal layer for a confirmed skin-care pack.
 *
 * This component renders. It does not decide, and almost every rule below
 * exists to keep it that way.
 *
 * - The verdict word comes from `verdict_text`, never from `action`. There is
 *   no local action-label map and no `.toUpperCase()`: governed copy is
 *   versioned server-side, and a client that formats the action would silently
 *   fork from it.
 * - The reason sentence comes from `reason_text`, never from `reason_key`.
 *   There is no key-to-prose table here. One reason key is read, and only to
 *   decide whether to offer a navigation button.
 * - There is no signal-to-action logic. This file never sees a signal.
 * - There is no fallback verdict. An unrecognised status shows no verdict at
 *   all, because "we do not know what this means" is not a reason to guess.
 * - One neutral treatment for every action. BUY-green and SKIP-red would make
 *   the client an interpretation layer through styling.
 *
 * It also fails closed on a malformed response, and the distinction matters.
 * A *governed non-decision* carries a sentence explaining an absence, which is
 * safe to show. A *malformed presentable* carries a product claim whose
 * evidence chain is incomplete — and printing that sentence without its
 * citation would be exactly the unsourced claim the chain exists to prevent.
 * So the two are handled separately: the first shows the server's sentence,
 * the second shows nothing but neutral structural copy.
 *
 * Every branch below carries a distinct `testID` so a tester on a physical
 * phone can name the state the card is in without reading this file, and so
 * that "malformed presentable", "governed non-decision", "hard handoff" and
 * "technical failure" can never be mistaken for one another during
 * qualification. The hooks name what is already on screen; none of them
 * exposes a release, an evidence id, a safety flag or a payload.
 */
import React from 'react';
import { Linking, StyleSheet, Text, TouchableOpacity, View } from 'react-native';

import { COLORS, FONTS, RADIUS, SPACING } from '../../theme/colors';
import {
  REASON_KEY_PERSONAL_CONTEXT,
  type ForYouResponse,
} from '../../services/productScan';

/** Structural UI copy. None of it says anything about a product. */
export const FOR_YOU_COPY = {
  heading: 'FOR YOU',
  subheading: 'The personal layer for the pack you just confirmed.',
  unavailable: 'FOR YOU is temporarily unavailable.',
  retry: 'Try again',
  openSource: 'Open source',
  addSkinDetails: 'Add skin details',
  checking: 'Checking FOR YOU…',
  linkFailed: 'That link could not be opened on this phone.',
  notAvailable: 'This result is not available right now.',
} as const;

const STATUS_HANDOFF = 'handoff_required';
const STATUS_PRESENTABLE = 'decision_presentable';

/**
 * Is this genuinely a complete, presentable decision?
 *
 * All four together or none of it. A verdict without its reason is an
 * unexplained claim; a reason without its source is an unsourced one; and a
 * citation with no openable URL is a source nobody can check.
 */
export function isPresentable(response: ForYouResponse | null): boolean {
  if (!response) return false;
  const result = response.result;
  if (result.status !== STATUS_PRESENTABLE) return false;
  return Boolean(
    result.verdict_text?.trim()
    && result.reason_text?.trim()
    && result.citation
    && result.citation.canonical_url?.trim(),
  );
}

function Citation({ citation }: { citation: NonNullable<ForYouResponse['result']['citation']> }) {
  const [failed, setFailed] = React.useState(false);
  const open = React.useCallback(() => {
    // Exactly the URL the server selected. Never a publisher homepage, never a
    // search, never a reconstruction.
    Linking.openURL(citation.canonical_url).catch(() => setFailed(true));
  }, [citation.canonical_url]);

  return (
    <View style={styles.source} testID="for-you-source" accessibilityLabel="Source for this result">
      <Text style={styles.sourceTitle}>{citation.title}</Text>
      <Text style={styles.sourceMeta}>{citation.publisher}</Text>
      {!!citation.locator && <Text style={styles.sourceMeta}>{citation.locator}</Text>}
      {failed && (
        <Text style={styles.error} testID="for-you-source-link-failed" accessibilityRole="alert">
          {FOR_YOU_COPY.linkFailed}
        </Text>
      )}
      <TouchableOpacity
        accessibilityRole="link"
        accessibilityLabel={FOR_YOU_COPY.openSource}
        testID="for-you-open-source"
        onPress={open}
        style={styles.sourceButton}
      >
        <Text style={styles.sourceButtonText}>{FOR_YOU_COPY.openSource}</Text>
      </TouchableOpacity>
    </View>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <View style={styles.card} testID="for-you-card" accessibilityLabel="FOR YOU">
      <Text style={styles.eyebrow} accessibilityRole="header">{FOR_YOU_COPY.heading}</Text>
      {children}
    </View>
  );
}

export function ForYouCard({
  response,
  loading,
  failed,
  onRetry,
  onAddSkinDetails,
}: {
  response: ForYouResponse | null;
  loading?: boolean;
  /** True for a network failure or a 503. Never turned into a verdict. */
  failed?: boolean;
  onRetry: () => void;
  onAddSkinDetails: () => void;
}) {
  if (loading) {
    return (
      <Shell>
        <Text style={styles.body} testID="for-you-loading" accessibilityRole="alert">
          {FOR_YOU_COPY.checking}
        </Text>
      </Shell>
    );
  }

  if (failed || !response) {
    // Technical unavailability is never a decision. No verdict, no WAIT, and
    // nothing about the exception, the rules or the release.
    return (
      <Shell>
        <Text style={styles.body} testID="for-you-technical-unavailable" accessibilityRole="alert">
          {FOR_YOU_COPY.unavailable}
        </Text>
        <TouchableOpacity
          accessibilityRole="button"
          accessibilityLabel={FOR_YOU_COPY.retry}
          testID="for-you-retry"
          onPress={onRetry}
          style={styles.secondaryButton}
        >
          <Text style={styles.secondaryText}>{FOR_YOU_COPY.retry}</Text>
        </TouchableOpacity>
      </Shell>
    );
  }

  const result = response.result;

  if (result.status === STATUS_HANDOFF) {
    // The safety authority's own words, unchanged. No verdict is offered
    // alongside a hand-over, and this client writes nothing medical around it.
    return (
      <Shell>
        <Text style={styles.body} testID="for-you-handoff" accessibilityRole="alert">
          {result.handoff?.message ?? result.reason_text}
        </Text>
      </Shell>
    );
  }

  if (result.status === STATUS_PRESENTABLE && !isPresentable(response)) {
    // A decision that claims to be presentable but is missing its verdict, its
    // reason or its openable source.
    //
    // This is NOT a governed non-decision, and must not be shown as one. Its
    // reason_text is a *product or personal claim* — the very thing the
    // evidence chain exists to license — so printing it while the citation is
    // absent would put an unsourced claim on screen. No source, no claim: the
    // whole thing is withheld, including the profile-gap affordance, which
    // would otherwise leak that a real evaluation happened.
    return (
      <Shell>
        <Text style={styles.body} testID="for-you-malformed-presentable" accessibilityRole="alert">
          {FOR_YOU_COPY.notAvailable}
        </Text>
      </Shell>
    );
  }

  if (!isPresentable(response)) {
    // A legitimate governed non-decision. The server's sentence explains an
    // absence rather than asserting anything about the product, so it is shown.
    const sentence = result.reason_text?.trim() || FOR_YOU_COPY.notAvailable;
    return (
      <Shell>
        <Text style={styles.body} testID="for-you-nondecision" accessibilityRole="alert">
          {sentence}
        </Text>
        {result.reason_key === REASON_KEY_PERSONAL_CONTEXT && (
          <TouchableOpacity
            accessibilityRole="button"
            accessibilityLabel={FOR_YOU_COPY.addSkinDetails}
            testID="for-you-add-skin-details"
            onPress={onAddSkinDetails}
            style={styles.secondaryButton}
          >
            <Text style={styles.secondaryText}>{FOR_YOU_COPY.addSkinDetails}</Text>
          </TouchableOpacity>
        )}
      </Shell>
    );
  }

  return (
    <Shell>
      <Text style={styles.subheading}>{FOR_YOU_COPY.subheading}</Text>
      {/* The server's word, printed as given. */}
      <Text
        style={styles.verdict}
        testID="for-you-verdict"
        accessibilityRole="header"
        accessibilityLabel={`Verdict: ${result.verdict_text}`}
      >
        {result.verdict_text}
      </Text>
      <Text style={styles.reason} testID="for-you-reason">{result.reason_text}</Text>
      {result.citation && <Citation citation={result.citation} />}
    </Shell>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: COLORS.card,
    borderRadius: RADIUS.lg,
    padding: SPACING.lg,
    borderWidth: 1,
    borderColor: COLORS.border,
    gap: SPACING.sm,
  },
  eyebrow: {
    fontFamily: FONTS.family.bodySemibold,
    fontSize: 11,
    letterSpacing: 1.6,
    color: COLORS.primary,
  },
  subheading: { fontFamily: FONTS.family.body, fontSize: 13, color: COLORS.textSecondary },
  // One neutral treatment, whatever the action. Colour must not interpret.
  verdict: {
    fontFamily: FONTS.family.heading,
    fontSize: 30,
    color: COLORS.textPrimary,
    marginTop: SPACING.xs,
  },
  reason: {
    fontFamily: FONTS.family.body,
    fontSize: 15,
    lineHeight: 23,
    color: COLORS.textPrimary,
  },
  body: { fontFamily: FONTS.family.body, fontSize: 14, lineHeight: 21, color: COLORS.textSecondary },
  error: { fontFamily: FONTS.family.body, fontSize: 13, color: COLORS.error },
  source: {
    marginTop: SPACING.sm,
    paddingTop: SPACING.sm,
    borderTopWidth: 1,
    borderTopColor: COLORS.border,
    gap: 2,
  },
  sourceTitle: { fontFamily: FONTS.family.bodySemibold, fontSize: 14, color: COLORS.textPrimary },
  sourceMeta: { fontFamily: FONTS.family.body, fontSize: 12, color: COLORS.textSecondary },
  sourceButton: { paddingVertical: 10, alignSelf: 'flex-start' },
  sourceButtonText: { fontFamily: FONTS.family.bodySemibold, fontSize: 14, color: COLORS.primary },
  secondaryButton: {
    marginTop: SPACING.sm,
    paddingVertical: 12,
    paddingHorizontal: SPACING.md,
    borderRadius: RADIUS.md,
    borderWidth: 1,
    borderColor: COLORS.primary,
    alignSelf: 'flex-start',
  },
  secondaryText: { fontFamily: FONTS.family.bodySemibold, fontSize: 14, color: COLORS.primary },
});
