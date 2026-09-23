/**
 * Every word the Step 14 purchase surfaces say, keyed.
 *
 * The Purchase Operating System answers in codes: a state, a reason, the
 * authority it came from. This file is the only place those codes become
 * words, so the copy can be reviewed against LEGAL_RULES.md without reading a
 * component, and a static test fails if a Step 14 component grows its own.
 *
 * Writing rules, on top of the verdict screen's six:
 *   - Context is context. A changed label, a changed record, something you
 *     own or chose before, a comparable option: each is stated, and none of
 *     them is described as changing the answer, because none of them does.
 *   - The official-record ceiling is the one exception, and it says exactly
 *     what it does: we do not say BUY while an official record matches the
 *     pack in your hand. It never promises when that stops.
 *   - "Not enough information" is a truth state, never softened into WAIT.
 *   - A decision about an earlier label version is never worded as current.
 *   - Supplements: never buy, wait or skip, and never advice.
 *   - No free text. The customer picks; they never type. A wrong photo read is
 *     answered with another photo, never an editor.
 */

/** Bump whenever any sentence below changes, so a screenshot traces to its wording. */
export const PURCHASE_OS_COPY_VERSION = 'purchase-os-copy.v2';

/** Interpolation, the same shape as the verdict strings' `t`. */
export const fill = (template: string, values: Record<string, string | number> = {}): string =>
  template.replace(/\{(\w+)\}/g, (_, key) => String(values[key] ?? `{${key}}`));

export const PURCHASE_OS = {
  verdictWord: {
    buy: 'BUY',
    wait: 'WAIT',
    skip: 'SKIP',
  },
  context: {
    title: 'Your purchase context',
    why: {
      title: 'Why this decision',
      officialRecord: 'An official record matches this exact pack.',
      productResult: 'From the confirmed label of this exact pack.',
      identityInsufficient: 'There is no confirmed label for this exact version yet, so there is no purchase answer.',
      notEnoughInformation: 'There is not enough information for a purchase answer yet.',
      referenceView: 'You are reading about this product, not holding it. What applies to the pack in your hand appears when you scan it.',
    },
    official: {
      title: 'Official record',
      ceiling: 'We do not say BUY while an official record matches the pack in your hand.',
      consistent: 'An official record also matches this exact pack.',
      openSource: 'Open the source: {name}',
    },
    owned: {
      title: 'What you already own',
      exactVersion: 'This exact version is on your shelf.',
    },
    prior: {
      title: 'Your prior decision',
      exactVersion: 'You chose {decision} for this exact version.',
      earlierVersion: 'You chose {decision} for label version {version}. This pack is a different version, so that is not shown as your decision here.',
      historyIncomplete: 'Some earlier decisions are not linked to a person, so they are not shown here.',
    },
    changes: {
      title: 'Verified product changes',
      label: 'The confirmed label differs from an earlier confirmed version of this product.',
      regulatory: 'The official record has changed since it was first seen.',
      contextOnly: 'Changes are shown for context. They do not change the answer on their own.',
    },
    better: {
      title: 'Better option',
      shownBelow: 'One comparable option is shown further down.',
    },
    a11y: {
      answer: 'Purchase answer: {verdict}. {reason}',
      noAnswer: 'No purchase answer. {reason}',
    },
  },
  scanEntry: {
    action: "Can't scan it? Check a product you're considering",
    hint: 'For skin care, hair care or fragrance',
  },
  candidate: {
    title: "Check a product you're considering",
    scanFirst: 'Holding the product? Scanning it gives the most exact answer.',
    scanAction: 'Scan instead',
    back: 'Back',
    categoryTitle: 'What kind of product is it?',
    category: {
      beauty: 'Skin care',
      hair: 'Hair care',
      perfumes: 'Fragrance',
      supplements: 'Supplement',
    },
    photo: {
      action: 'Use a photo',
      hint: 'A photo or screenshot of the product or its label.',
      another: 'Read another photo',
      working: 'Reading the photo…',
    },
    context: {
      occasions: 'Where you would use it (optional)',
      seasons: 'When you would use it (optional)',
    },
    notInInventory: 'This is a product you are considering. Checking it does not add it to your shelf.',
    confirmationRequired: 'Check what we read from the photo. Confirm it, or read another photo if it is wrong.',
    unsupported: 'GlamGenius cannot check this kind of product before you buy it.',
    failed: 'We could not check that just now.',
    retry: 'Try again',
    startOver: 'Check something else',
  },
  supplementBoundary: {
    title: 'Supplements',
    body: 'GlamGenius does not give buy, wait or skip answers for supplements, and does not advise on taking them. You can keep a record of a supplement you own and what its label says.',
    action: 'Record a supplement you own',
  },
} as const;

export type PurchaseVerdict = keyof typeof PURCHASE_OS.verdictWord;
