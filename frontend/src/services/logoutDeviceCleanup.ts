/**
 * Logout's best-effort removal of this installation's notification device.
 *
 * On a shared, borrowed or sold phone, signing out must not leave the phone
 * registered to receive the signed-out account's notifications. Logout ends
 * the session locally first and at once (the Lane B rule: nothing waits on the
 * network before that). This then asks the server to remove this
 * installation's device, for that account only.
 *
 * It is deliberately narrow. It is not a general API client and it does not go
 * through one:
 *
 * - it sends one request, the production ``DELETE`` the Notifications screen
 *   uses (``notificationDevicePath``), which the server scopes to the account
 *   the bearer token belongs to and the exact device key. A late request for a
 *   signed-out account can remove only that account's row, never the row of
 *   whoever signed in on this phone since;
 * - it uses the bearer token logout captured before the session ended, and
 *   never reads or changes the current auth state. The shared client's
 *   interceptors would, correctly, refuse to send a request for an account
 *   that is no longer signed in;
 * - the token lives only in this call. It is never stored, queued or logged;
 * - it never creates an installation id: an installation that never had one
 *   never registered a device;
 * - it never throws, and nothing waits for it. Offline, rejected (401),
 *   failing or slow, logout has already happened; the device stays registered
 *   on the server until a later removal succeeds or the token handoff moves it.
 */
import { readInstallationId } from './deviceIdentity';
import { notificationDevicePath } from './notificationRoutes';

/** A bound on a request nobody waits for, so it cannot linger. */
export const DEVICE_CLEANUP_TIMEOUT_MS = 10000;

function backendBaseUrl(): string {
  return (process.env.EXPO_PUBLIC_BACKEND_URL || '').replace(/\/$/, '');
}

async function removeDevice(accessToken: string, deviceKey: string): Promise<void> {
  const controller = typeof AbortController === 'function' ? new AbortController() : null;
  const timer = controller ? setTimeout(() => controller.abort(), DEVICE_CLEANUP_TIMEOUT_MS) : null;
  try {
    await fetch(`${backendBaseUrl()}${notificationDevicePath(deviceKey)}`, {
      method: 'DELETE',
      headers: { Authorization: `Bearer ${accessToken}` },
      signal: controller?.signal,
    });
  } catch {
    // Best effort. Nothing about the failure is recorded: the request carried
    // a credential, and logout is already complete.
  } finally {
    if (timer) clearTimeout(timer);
  }
}

/**
 * Start removing this installation's device for the account that just signed out.
 *
 * Resolves as soon as the request has been handed to the network, or skipped
 * because there is no token or no installation id. Only a local read happens
 * before that. ``settled`` resolves when the request finishes, fails or times
 * out; it never rejects.
 */
export async function beginDeviceRemovalForEndedSession(
  accessToken: string | null,
): Promise<{ settled: Promise<void> }> {
  if (!accessToken) return { settled: Promise.resolve() };
  const deviceKey = await readInstallationId();
  if (!deviceKey) return { settled: Promise.resolve() };
  return { settled: removeDevice(accessToken, deviceKey) };
}
