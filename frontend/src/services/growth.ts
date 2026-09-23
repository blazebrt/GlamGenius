/**
 * Step 15's two calls, both of which may fail without anybody noticing.
 *
 * - `ensureShareReferralCode` asks for the inviter's own usable referral code
 *   so a share can carry it. Anonymous, not yet activated, exhausted, slow or
 *   broken: every one of those is `null`, and the share goes out without a
 *   code. Sharing a Product Result never waits on growth for long and never
 *   fails because of it.
 * - `recordGrowthEvent` sends one whitelisted event. Fire and forget: its
 *   types admit only the enums the server accepts, it carries a random
 *   operation id (never a device or advertising id) so a retry is counted
 *   once, and a failure is swallowed.
 *
 * Neither call carries a barcode, a product name, a recipient, the app a
 * share went to, or any free text.
 */
import { api } from './api';

/** How long a share waits for a referral code before going without one. */
export const REFERRAL_TIMEOUT_MS = 4000;
const EVENT_TIMEOUT_MS = 4000;
const REFERRAL_CODE = /^[A-Z0-9]{6,64}$/;

export type ReferralState = 'not_activated' | 'ready' | 'available' | 'exhausted' | 'withdrawn' | 'unavailable';

export interface ReferralWire {
  program_version: string;
  state: ReferralState;
  referral: { code: string; expires_at: string | null; remaining_uses: number | null } | null;
}

export type ShareResult = 'shared' | 'dismissed' | 'failed';

export type GrowthEvent =
  | {
    name: 'growth.product_result_share';
    properties: { surface: 'product_result'; result: ShareResult; referral_included: boolean };
  }
  | { name: 'growth.scan_again'; properties: { surface: 'product_result' } };

/** The code a share may carry, or `null`. Never throws. */
export async function ensureShareReferralCode(): Promise<string | null> {
  try {
    const { data } = await api.post<ReferralWire>('/api/v2/growth/referral', undefined, {
      timeout: REFERRAL_TIMEOUT_MS,
    });
    const code = data?.state === 'available' ? data.referral?.code : null;
    return typeof code === 'string' && REFERRAL_CODE.test(code) ? code : null;
  } catch {
    return null;
  }
}

/** A random RFC 4122 version-4 id for one interaction. Not an identifier of anything. */
export function newClientEventId(): string {
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
 * Send one event. At most one retry, with the same operation id, and only when
 * the first attempt never reached the server. Never throws.
 */
export async function recordGrowthEvent(event: GrowthEvent, clientEventId: string = newClientEventId()): Promise<void> {
  const body = { name: event.name, client_event_id: clientEventId, properties: event.properties };
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      await api.post('/api/v2/growth/events', body, { timeout: EVENT_TIMEOUT_MS });
      return;
    } catch (error) {
      // Answered (refused, limited, or failed on the server): not retried.
      if ((error as { response?: unknown })?.response) return;
    }
  }
}
