/**
 * Plain-text share card for the system share sheet (including WhatsApp).
 *
 * We intentionally do not render a brand logo here.  A product name is text
 * supplied by the person sharing it, and a negative result never becomes a
 * branded visual accusation.  Private-beta invites remain admin-issued, so a
 * verdict share cannot create or redeem an invite by itself.
 */
import type { VerdictSource, VerdictView } from './verdictModel';

export function buildVerdictShareText(source: VerdictSource, view: VerdictView): string {
  const product = source.productName.trim() || 'this product';
  return [
    `GlamGenius checked ${product}.`,
    `${view.verdict}. ${view.action}`.trim(),
    view.everydayNumber,
    'GlamGenius is currently invite-only. Ask the sender for an invite.',
  ].filter(Boolean).join('\n\n');
}
