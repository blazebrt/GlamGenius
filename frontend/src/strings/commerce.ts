/**
 * Every word Step 16 says, keyed.
 *
 * Step 16 adds one quiet outbound link to a Product Result, and only after the
 * decision: "Find this product" beneath a BUY, or "Find this alternative"
 * inside the existing comparable-alternative card beneath a WAIT or SKIP. Both
 * read their words from here, so the copy can be checked against
 * LEGAL_RULES.md without opening a component, and a static test fails if a
 * Step 16 file grows its own sentence.
 *
 * Writing rules, on top of the verdict screen's six:
 *   - The disclosure is upfront and travels with the link. It says three
 *     things, every time: this is an affiliate link, GlamGenius may earn a
 *     commission, and that does not affect our decisions.
 *   - A link opens a partner's search for one barcode. It is not a listing we
 *     checked, so nothing here says the product was found, is the same, is in
 *     stock, is available, is the best or cheapest, or is sold by a
 *     recommended seller.
 *   - A listing is not the pack that was scanned. The person is told to scan
 *     the pack they receive.
 *   - No urgency, no scarcity, no price, no offer, no reward.
 */

/** Bump whenever any sentence below changes. */
export const COMMERCE_COPY_VERSION = 'commerce-copy.v1';

/** Interpolation, the same shape as the verdict strings' `t`. */
export const fill = (template: string, values: Record<string, string | number> = {}): string =>
  template.replace(/\{(\w+)\}/g, (_, key) => String(values[key] ?? `{${key}}`));

export const COMMERCE = {
  disclosure: 'Affiliate · GlamGenius may earn a commission. This does not affect our decisions.',
  action: {
    current_product: 'Find this product',
    alternative: 'Find this alternative',
  },
  destination: 'Opens a search for this barcode on {partner}. GlamGenius does not check what it lists.',
  packNotice: 'Re-scan the pack you receive before you use it.',
  openFailed: 'The link could not be opened.',
  /** Partner names, keyed by the server's closed registry key. */
  partners: {
    amazon_in: 'Amazon.in',
  },
  a11y: {
    action: '{action}. Opens a search on {partner} in your browser. Affiliate link.',
  },
} as const;
