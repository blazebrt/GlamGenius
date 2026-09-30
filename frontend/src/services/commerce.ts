/**
 * Step 16: one disclosed outbound link, read after the decision and checked
 * again here before anything is shown or opened.
 *
 * The server's ``commerce-handoff-v1`` answer is the only authority on whether
 * a link exists and for which product. This module never decides that. It
 * refuses an answer it cannot vouch for, so a malformed, stale, mismatched or
 * tampered answer shows nothing:
 *
 *   - the contract, state, target, decision and pack notice must be exactly
 *     the ones the server can produce, and consistent with each other;
 *   - the partner must be one this app knows by key, and its address must be
 *     exactly that partner's search page for the target's own barcode — https,
 *     the registered host and path, the barcode and the partner's affiliate tag,
 *     nothing else;
 *   - the answer must describe the same pack, the same label version and the
 *     same canonical decision the screen already shows, and an alternative
 *     link must name the very alternative on screen.
 *
 * It opens the address with the platform's own external link handling (no
 * merchant SDK, no in-app browser, no WebView), after checking it once more.
 *
 * Telemetry is one whitelisted event, sent only for a signed-in person, only
 * after the link actually opened. It carries no barcode, product, address,
 * affiliate tag, device, account or free text, and it shares nothing with
 * Step 15's growth telemetry.
 */
import { api } from './api';
import type { PurchaseOsCheck } from './apiV2';
import { openExternalUrl, isSafeExternalUrl } from './externalLinks';
import { readScanCommerceHandoff } from './productScan';

export const COMMERCE_HANDOFF_CONTRACT = 'commerce-handoff-v1';
const EVENT_TIMEOUT_MS = 4000;

export type CommerceTarget = 'current_product' | 'alternative';
export type CommerceDecision = 'buy' | 'wait' | 'skip';

/**
 * The partners this app will open, by the server's registry key. Each is the
 * complete shape of an address: https, one exact host, one exact path, the
 * barcode, and the affiliate tag in the partner's own shape (Amazon India: a
 * tracking ID ending ``-21``). V1 has no untagged address: every partner is an
 * affiliate relationship, disclosed as one. A partner the server enables and
 * this table does not know simply shows no link.
 */
const DESTINATIONS = {
  amazon_in: /^https:\/\/www\.amazon\.in\/s\?k=([0-9]{8}|[0-9]{12,14})&tag=[a-z0-9](?:[a-z0-9-]{0,58}[a-z0-9])?-21$/,
} as const;
export type CommercePartnerKey = keyof typeof DESTINATIONS;
export const COMMERCE_PARTNER_KEYS = Object.keys(DESTINATIONS) as CommercePartnerKey[];
const MAX_URL_LENGTH = 256;

/** The (target, decision) pairs the server can produce. */
const CONSISTENT: readonly (readonly [CommerceTarget, CommerceDecision])[] = [
  ['current_product', 'buy'], ['alternative', 'wait'], ['alternative', 'skip'],
];

export interface CommerceHandoff {
  target: CommerceTarget;
  decision: CommerceDecision;
  targetBarcode: string;
  identity: { barcode: string; labelVersion: number; contentFingerprint: string };
  /** Always ``true`` in V1: an untagged or non-affiliate answer is refused. */
  partner: { key: CommercePartnerKey; url: string; affiliate: true };
}

/** A GS1 GTIN-8, 12, 13 or 14 with a valid check digit. ASCII digits only. */
export function isExactGtin(value: unknown): value is string {
  if (typeof value !== 'string' || !/^(?:[0-9]{8}|[0-9]{12,14})$/.test(value) || /^0+$/.test(value)) return false;
  const digits = value.split('').map(Number);
  const check = digits.pop() as number;
  const sum = digits.reverse().reduce((total, digit, index) => total + digit * (index % 2 === 0 ? 3 : 1), 0);
  return (10 - (sum % 10)) % 10 === check;
}

/** True only for exactly this partner's search page for exactly this barcode. */
export function isCommerceDestination(url: unknown, partner: unknown, barcode: unknown): url is string {
  if (typeof url !== 'string' || url.length > MAX_URL_LENGTH || !isSafeExternalUrl(url)) return false;
  if (typeof partner !== 'string' || !Object.prototype.hasOwnProperty.call(DESTINATIONS, partner)) return false;
  if (!isExactGtin(barcode)) return false;
  const match = DESTINATIONS[partner as CommercePartnerKey].exec(url);
  return match !== null && match[1] === barcode;
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

/** The validated handoff, or `null`. Never throws. */
export function parseCommerceHandoff(raw: unknown): CommerceHandoff | null {
  if (!isRecord(raw) || raw.contract_version !== COMMERCE_HANDOFF_CONTRACT || raw.state !== 'available') return null;
  if (raw.pack_notice !== 'rescan_received_pack') return null;
  const target = raw.target;
  const decision = raw.decision;
  if (!CONSISTENT.some(([t, d]) => t === target && d === decision)) return null;
  const identity = raw.identity;
  const partner = raw.partner;
  if (!isRecord(identity) || !isRecord(partner)) return null;
  if (typeof identity.barcode !== 'string' || typeof identity.label_version !== 'number'
      || typeof identity.content_fingerprint !== 'string') return null;
  const targetBarcode = raw.target_barcode;
  if (!isExactGtin(targetBarcode)) return null;
  // BUY links the scanned pack; WAIT and SKIP never do.
  if (target === 'current_product' ? targetBarcode !== identity.barcode : targetBarcode === identity.barcode) return null;
  // Every V1 partner is an affiliate relationship; an answer claiming otherwise
  // did not come from a V1 handoff. The address must carry the tag as well.
  if (partner.affiliate !== true) return null;
  if (!isCommerceDestination(partner.url, partner.key, targetBarcode)) return null;
  return {
    target: target as CommerceTarget,
    decision: decision as CommerceDecision,
    targetBarcode,
    identity: {
      barcode: identity.barcode,
      labelVersion: identity.label_version,
      contentFingerprint: identity.content_fingerprint,
    },
    partner: { key: partner.key as CommercePartnerKey, url: partner.url, affiliate: true },
  };
}

/**
 * What the screen is showing, which the handoff has to match exactly: the
 * purchase check already matched to the pack on screen, and the alternative
 * the card already shows, if any.
 */
export interface CommerceShown {
  barcode: string;
  purchaseCheck: PurchaseOsCheck | null;
  alternativeBarcode: string | null;
}

export function commerceHandoffMatches(handoff: CommerceHandoff | null, shown: CommerceShown): handoff is CommerceHandoff {
  const check = shown.purchaseCheck;
  if (!handoff || !check) return false;
  const identity = check.identity;
  if (identity.state !== 'exact' || identity.reference_view !== false || identity.physical_pack_context !== true) return false;
  if (handoff.identity.barcode !== shown.barcode || identity.barcode !== shown.barcode) return false;
  if (handoff.identity.labelVersion !== identity.label_version
      || handoff.identity.contentFingerprint !== identity.content_fingerprint) return false;
  // The link rests on the same canonical decision the screen shows, never another.
  if (check.decision.state !== 'decided' || check.decision.verdict !== handoff.decision) return false;
  if (handoff.target === 'alternative') return shown.alternativeBarcode === handoff.targetBarcode;
  return handoff.targetBarcode === shown.barcode;
}

/** The server's answer for this pack, validated, or `null`. Never throws. */
export async function readCommerceHandoff(barcode: string): Promise<CommerceHandoff | null> {
  try {
    return parseCommerceHandoff(await readScanCommerceHandoff(barcode, { physicalPackContext: true }));
  } catch {
    return null;
  }
}

/** Open the partner's search page, after checking the address once more. */
export async function openCommerceHandoff(handoff: CommerceHandoff): Promise<boolean> {
  if (!isCommerceDestination(handoff.partner.url, handoff.partner.key, handoff.targetBarcode)) return false;
  return openExternalUrl(handoff.partner.url);
}

export interface CommerceOpenEvent {
  name: 'commerce.outbound_open';
  properties: {
    surface: 'product_result';
    target: CommerceTarget;
    decision: CommerceDecision;
    partner: CommercePartnerKey;
    affiliate: true;
  };
}

/** The one event, built from the validated handoff and nothing else. */
export function commerceOpenEvent(handoff: CommerceHandoff): CommerceOpenEvent {
  return {
    name: 'commerce.outbound_open',
    properties: {
      surface: 'product_result',
      target: handoff.target,
      decision: handoff.decision,
      partner: handoff.partner.key,
      affiliate: handoff.partner.affiliate,
    },
  };
}

/** A random RFC 4122 version-4 id for one open. Not an identifier of anything. */
export function newCommerceEventId(): string {
  const bytes = new Uint8Array(16);
  const cryptoApi = (globalThis as { crypto?: { getRandomValues?: (array: Uint8Array) => Uint8Array } }).crypto;
  if (cryptoApi?.getRandomValues) {
    cryptoApi.getRandomValues(bytes);
  } else {
    for (let i = 0; i < bytes.length; i += 1) bytes[i] = Math.floor(Math.random() * 256);
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

/**
 * Send the one event. At most one retry, with the same operation id, and only
 * when the first attempt never reached the server. Never throws.
 */
export async function recordCommerceOpen(event: CommerceOpenEvent, clientEventId: string = newCommerceEventId()): Promise<void> {
  const body = { name: event.name, client_event_id: clientEventId, properties: event.properties };
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      await api.post('/api/v2/commerce/events', body, { timeout: EVENT_TIMEOUT_MS });
      return;
    } catch (error) {
      if ((error as { response?: unknown })?.response) return;
    }
  }
}
