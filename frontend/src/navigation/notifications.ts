export type NotificationTarget = { destination: string; params?: Record<string, string> };

const ALLOWED = new Set([
  '/(tabs)/today', '/(tabs)/style', '/(tabs)/care', '/(tabs)/plan',
  '/event-ready', '/improve', '/(tabs)/services', '/(tabs)/inventory',
]);

/** Convert untrusted push data into a safe, server-owned navigation target. */
export function notificationTarget(data: unknown): NotificationTarget {
  const value = (data && typeof data === 'object' ? data : {}) as Record<string, unknown>;
  const destination = typeof value.destination === 'string' ? value.destination : '';
  if (!ALLOWED.has(destination)) return { destination: '/(tabs)/today' };
  if (destination === '/event-ready') {
    const eventId = typeof value.eventId === 'string' && value.eventId.trim() ? value.eventId : null;
    return eventId ? { destination, params: { eventId } } : { destination: '/(tabs)/plan' };
  }
  return { destination };
}

export const allowedNotificationDestinations = ALLOWED;
