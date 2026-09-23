/**
 * Every word Step 15 says, keyed.
 *
 * Step 15 adds two customer surfaces and no more: the text a Product Result
 * share hands to the system share sheet, and one quiet "scan another product"
 * action beneath the result. Both read their words from here, so the copy can
 * be checked against LEGAL_RULES.md without opening a component, and a static
 * test fails if a Step 15 file grows its own sentence.
 *
 * Writing rules, on top of the verdict screen's six:
 *   - A share leaves the app, so it carries only what is true for whoever
 *     reads it: the product, the canonical Product Result decision, and a
 *     reason only when its named, openable source travels with it.
 *   - A WAIT or SKIP is never shared without its sourced reason. The share
 *     says where to see it instead. A negative statement carries its source,
 *     outside the app as much as in it.
 *   - Nothing about the sender's own pack: not a recall that matched it, not
 *     their shelf, their history or their household. The recipient is told to
 *     scan their own pack.
 *   - No scarcity, no urgency, no reward. The invite code is stated, not sold.
 *   - No mockery, no characterisation of a brand.
 */
import { ODBL_ATTRIBUTION_TEXT, ODBL_LICENSE_URL, OFF_SOURCE_URL } from '../components/common/OpenFoodFactsAttribution';

/** Bump whenever any sentence below changes, so a shared message traces to its wording. */
export const GROWTH_COPY_VERSION = 'growth-copy.v1';

/** Interpolation, the same shape as the verdict strings' `t`. */
export const fill = (template: string, values: Record<string, string | number> = {}): string =>
  template.replace(/\{(\w+)\}/g, (_, key) => String(values[key] ?? `{${key}}`));

export const GROWTH = {
  share: {
    intro: 'I checked this with GlamGenius before buying.',
    unnamedProduct: 'A product without a recorded name',
    decision: 'GlamGenius result: {decision}',
    decisionWord: {
      buy: 'BUY',
      wait: 'WAIT',
      skip: 'SKIP',
    },
    notGraded: 'GlamGenius does not grade this kind of product.',
    notEnoughInformation: 'Not enough information for GlamGenius to decide on this product.',
    resultInApp: 'The result and the sources behind it are in the GlamGenius app.',
    reasonSource: 'Source: {name} — {url}',
    ownPack: 'Scan your own pack before you decide. Packs and labels can differ.',
    // A licence condition, not copy: the fixed ODbL wording and its links.
    odblAttribution: ODBL_ATTRIBUTION_TEXT,
    odblLinks: `${OFF_SOURCE_URL} · ${ODBL_LICENSE_URL}`,
    privateBeta: 'GlamGenius is in private beta.',
    inviteCode: 'Invite code: {code}',
  },
  scanAgain: {
    action: 'Scan another product',
    hint: 'Goes back to the scanner.',
  },
} as const;
