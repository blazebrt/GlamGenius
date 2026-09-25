/**
 * The one notification-device route, shared by every caller.
 *
 * The Notifications screen removes this installation's device through it, and
 * so does logout. Both must reach the same production route on the server,
 * which removes the device only for the account the request is signed in as.
 * Kept here, with no imports, so the path cannot drift between the two.
 */
export const NOTIFICATION_DEVICES_PATH = '/api/v2/today/notifications/devices';

export const notificationDevicePath = (deviceKey: string): string =>
  `${NOTIFICATION_DEVICES_PATH}/${encodeURIComponent(deviceKey)}`;
