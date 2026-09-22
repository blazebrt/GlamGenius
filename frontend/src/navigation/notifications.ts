export type NotificationTarget = { destination: string; params?: Record<string, string> };

const ALLOWED = new Set([
  '/scan', '/(tabs)/you', '/improve', '/shelf'
]);

/**
 * The one destination that carries a parameter: a watched product's verdict.
 * The same rule as the server's ``VERDICT_BARCODE`` — a GTIN of 8 to 14 digits
 * and nothing else — because push data is untrusted by the time it arrives.
 */
const VERDICT_DESTINATION = '/verdict';
const VERDICT_BARCODE = /^[0-9]{8,14}$/;

/** Convert untrusted push data into a safe, server-owned navigation target. */
export function notificationTarget(data: unknown): NotificationTarget {
  const value = (data && typeof data === 'object' ? data : {}) as Record<string, unknown>;
  const destination = typeof value.destination === 'string' ? value.destination : '';
  if (destination === VERDICT_DESTINATION) {
    // Only the barcode rides along. Anything else in the payload — a source
    // link, an id, a reference flag — is dropped, and a missing or malformed
    // barcode falls back to the scanner rather than routing on a guess.
    const barcode = value.barcode;
    if (typeof barcode === 'string' && VERDICT_BARCODE.test(barcode)) {
      return { destination: VERDICT_DESTINATION, params: { barcode } };
    }
    return { destination: '/scan' };
  }
  if (!ALLOWED.has(destination)) return { destination: '/scan' };
  return { destination };
}

export const allowedNotificationDestinations = ALLOWED;
