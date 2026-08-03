/**
 * Sentry setup for the mobile app.
 *
 * Called once from the root layout. If EXPO_PUBLIC_SENTRY_DSN is empty
 * or missing, this module is a no-op and the app boots without opening
 * any Sentry-related socket.
 *
 * PII scrubbing here is a mirror of backend/app/shared/observability/
 * sentry_privacy.py. Both layers apply because Sentry events can
 * originate on either side of the wire.
 */
import * as Sentry from '@sentry/react-native';
// React type import must live at the top of the module so lint stops
// warning about `import/first`; it is only referenced by the
// `wrapRoot<T extends React.ComponentType<any>>` type signature below.
import type * as React from 'react';

const REDACTED = '[Redacted by application]';

const EMAIL_RE = /\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/gi;
const PHONE_RE = /(?<!\w)(?:\+?\d[\d ().-]{7,}\d)(?!\w)/g;
const JWT_RE = /\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b/g;
const BASE64_RE = /^[A-Za-z0-9+/=_-]{80,}$/;
const SENSITIVE_KEY =
  /(image|photo|bytes|base64|ingredient|memory|jwt|token|authorization|payment|order_id|receipt|email|phone|mobile|tel|password|secret|api_key)/i;

function scrub(value: unknown, key = ''): unknown {
  if (SENSITIVE_KEY.test(key)) return REDACTED;
  if (typeof value === 'string') {
    if (BASE64_RE.test(value.trim())) return REDACTED;
    return value
      .replace(JWT_RE, REDACTED)
      .replace(EMAIL_RE, REDACTED)
      .replace(PHONE_RE, REDACTED);
  }
  if (Array.isArray(value)) return value.map((v) => scrub(v, key));
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).map(([k, v]) => [k, scrub(v, k)]),
    );
  }
  return value;
}

let initialised = false;

/**
 * Initialise Sentry. Safe to call more than once — the second and later
 * calls are ignored.
 *
 * Returns true if Sentry was actually initialised, false if it was
 * skipped because the DSN was not configured.
 */
export function initSentry(): boolean {
  if (initialised) return true;
  const dsn = (process.env.EXPO_PUBLIC_SENTRY_DSN ?? '').trim();
  if (!dsn) return false;

  const environment = (process.env.EXPO_PUBLIC_SENTRY_ENVIRONMENT ?? 'development').trim();

  Sentry.init({
    dsn,
    environment,
    sendDefaultPii: false,
    beforeSend(event) {
      return scrub(event) as Sentry.ErrorEvent;
    },
    // Crash-only monitoring in this phase. Do NOT enable performance,
    // profiling, replay or logs without a separate product decision.
  });
  initialised = true;
  return true;
}

/**
 * Wrap the root component so React rendering errors reach Sentry when
 * initialised, or pass through unchanged when it is not.
 */
export function wrapRoot<T extends React.ComponentType<any>>(Component: T): T {
  const dsn = (process.env.EXPO_PUBLIC_SENTRY_DSN ?? '').trim();
  if (!dsn) return Component;
  return Sentry.wrap(Component) as T;
}

// Re-export the primitives the app uses.
export { Sentry };
