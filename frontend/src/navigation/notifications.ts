export type NotificationTarget = { destination: string; params?: Record<string, string> };

const ALLOWED = new Set([
  '/scan', '/(tabs)/you', '/improve', '/shelf'
]);

/** Convert untrusted push data into a safe, server-owned navigation target. */
export function notificationTarget(data: unknown): NotificationTarget {
  const value = (data && typeof data === 'object' ? data : {}) as Record<string, unknown>;
  const destination = typeof value.destination === 'string' ? value.destination : '';
  if (!ALLOWED.has(destination)) return { destination: '/scan' };
  return { destination };
}

export const allowedNotificationDestinations = ALLOWED;
