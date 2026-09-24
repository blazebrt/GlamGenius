/**
 * Shared HTTP client for the FastAPI backend.
 *
 * The Bearer token comes from the current Supabase session. It is fetched on
 * every request (Supabase caches it in-memory, so this is cheap) so a rotated
 * or refreshed token is picked up automatically.
 *
 * Two response codes trigger client-side navigation (§2 hardening spec):
 *
 *   401 Unauthorized              → end the session locally at once, then sign
 *                                    out of Supabase, go to /(auth)/welcome
 *   403 REGISTRATION_REQUIRED     → keep the Supabase session, route to
 *                                    /(auth)/registration-incomplete so the
 *                                    user can finish invite redemption.
 *
 * A plain 403 without ``code === "REGISTRATION_REQUIRED"`` (e.g. an admin-only
 * route) is left to the calling screen to render, exactly as before.
 *
 * Both of those change who the app thinks is signed in, so they must only act
 * on a response about the identity that is signed in now. Every request is
 * stamped when it is prepared, and the auth authority (the user store)
 * decides whether a response still speaks for the current identity. A 401 or
 * REGISTRATION_REQUIRED that belonged to an account which has since signed out
 * or been replaced, or to an earlier session of the same account, is passed
 * back to its caller and changes nothing else. A 401 is judged by the
 * credential it rejected (``authSentAs``), not by whoever is current when it
 * lands.
 *
 * That is the response side. The request side is separate, and it applies to
 * every request, not only account-specific ones. Checking a response afterwards
 * can protect local state. It cannot take back a GET, PATCH or POST the server
 * has already served, or applied, as the wrong account.
 *
 * - Each request is stamped with the identity it began under at the moment it
 *   is made (the ``runWhen`` hook runs synchronously inside the axios call,
 *   before any await).
 * - When its token is ready, the auth authority decides how it may go out
 *   (``dispatchAs``):
 *   - with the session's token, when that session still belongs to the
 *     account it began as (a refreshed token for the same account is fine);
 *   - anonymously, when it began signed out;
 *   - or not at all, when the identity changed in between: AccountMismatchError,
 *     and nothing reaches the network.
 * - ``expectedAccountId`` adds an explicit contract on top: this account's
 *   token, or nothing.
 *
 * A request sent anonymously presented no credentials, so its 401 says nothing
 * about any session and has no auth side effects.
 */
import axios from 'axios';
import { router } from 'expo-router';
import { getAccessIdentity, getAccessToken, signOut } from './supabase';

declare module 'axios' {
  interface AxiosRequestConfig {
    /**
     * Send this request only with this account's token. If the signed-in
     * account is anyone else, or no one, the request is not sent at all.
     */
    expectedAccountId?: string;
    /** Opaque auth-generation stamp, taken the moment the request was made. */
    authStamp?: unknown;
    /** How the request actually went out: with the session's token, or anonymously. */
    authDispatch?: 'session' | 'anonymous';
    /** The account and token a ``session`` request was sent with. */
    authSentAs?: SentCredential;
  }
}

/** The credential a request was sent with, handed to the unauthorized handler. */
export interface SentCredential {
  accountId: string;
  accessToken: string;
}

/**
 * Raised, before anything is sent, when a request would otherwise go out as an
 * identity other than the one it began under, or for an account-bound request,
 * as anyone but its account.
 */
export class AccountMismatchError extends Error {
  readonly code = 'ACCOUNT_MISMATCH';

  constructor() {
    super('The signed-in account changed before this request was sent.');
    this.name = 'AccountMismatchError';
  }
}

/**
 * The auth authority (the user store). It stamps requests with an opaque
 * identity snapshot and interprets those stamps. The API client never looks
 * inside a stamp, so it does not depend on the store.
 */
export interface AuthResponseAuthority {
  /** The current identity, as an opaque stamp. */
  stamp: () => unknown;
  /**
   * How a request stamped ``stamp`` may be sent, now that the session it would
   * use belongs to ``sessionAccountId`` (null: no session).
   */
  dispatchAs: (stamp: unknown, sessionAccountId: string | null) => 'session' | 'anonymous' | 'refuse';
  /**
   * Whether a 401 may end the current session. ``sentAs`` is the credential
   * the request actually presented, when it carried one: a 401 rejects that
   * credential, not whichever session happens to be current when it lands.
   */
  acceptsUnauthorized: (stamp: unknown, sentAs?: SentCredential | null) => boolean;
  acceptsRegistrationRequired: (stamp: unknown) => boolean;
}

let authAuthority: AuthResponseAuthority | null = null;

export const setAuthResponseAuthority = (authority: AuthResponseAuthority | null) => {
  authAuthority = authority;
};

const BACKEND_URL = process.env.EXPO_PUBLIC_BACKEND_URL || '';

if (!BACKEND_URL) {
   
  console.warn(
    'EXPO_PUBLIC_BACKEND_URL is not set. API requests will fail until the backend URL is configured.'
  );
}

// Lets the user store clear its own state when the session ends. It is told
// which credential the server rejected, when the request carried one.
type UnauthorizedHandler = (rejected?: SentCredential | null) => void;
let onUnauthorized: UnauthorizedHandler | null = null;
let onRegistrationRequired: ((stamp: unknown) => void) | null = null;

export const setUnauthorizedHandler = (handler: UnauthorizedHandler | null) => {
  onUnauthorized = handler;
};

export const setRegistrationRequiredHandler = (
  handler: ((stamp: unknown) => void) | null
) => {
  onRegistrationRequired = handler;
};

// eslint-disable-next-line import/no-named-as-default-member
export const api = axios.create({
  baseURL: BACKEND_URL ? BACKEND_URL.replace(/\/$/, '') : undefined,
  timeout: 60000,
  headers: {
    'Content-Type': 'application/json',
  },
});

export const errorMessage = (err: unknown, fallback: string): string => {
  const anyErr = err as { response?: { data?: { detail?: unknown } } };
  const detail = anyErr?.response?.data?.detail;
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (detail && typeof detail === 'object' && 'message' in detail) {
    const m = (detail as { message?: unknown }).message;
    if (typeof m === 'string') return m;
  }
  return fallback;
};

export const isRateLimited = (err: unknown): boolean => {
  const anyErr = err as {
    response?: { status?: number; data?: { detail?: { code?: string } } };
  };
  return (
    anyErr?.response?.status === 429 ||
    anyErr?.response?.data?.detail?.code === 'AI_RATE_LIMITED'
  );
};

export const isRegistrationRequired = (err: unknown): boolean => {
  const anyErr = err as {
    response?: { status?: number; data?: { detail?: { code?: string } } };
  };
  return (
    anyErr?.response?.status === 403 &&
    anyErr?.response?.data?.detail?.code === 'REGISTRATION_REQUIRED'
  );
};

/**
 * Stamp a request with the identity it begins under.
 *
 * axios calls ``runWhen`` synchronously inside ``api.get()``, ``api.post()`` and
 * the rest, before its own promise chain starts. So the stamp is taken while
 * the caller's code is still running, not after some other event has had a
 * chance to change who is signed in. It always lets the interceptor run.
 */
function stampWhenMade(config: { authStamp?: unknown }): boolean {
  if (config.authStamp === undefined) config.authStamp = authAuthority?.stamp();
  return true;
}

api.interceptors.request.use(
  async (config) => {
    if (!authAuthority) {
      // No auth authority is registered (only in tests that exercise the
      // client on its own): the plain token lookup, as before.
      if (config.expectedAccountId !== undefined) {
        const identity = await getAccessIdentity();
        if (!identity || identity.userId !== config.expectedAccountId) {
          throw new AccountMismatchError();
        }
        config.headers.Authorization = `Bearer ${identity.token}`;
        return config;
      }
      const token = await getAccessToken();
      if (token) {
        config.headers.Authorization = `Bearer ${token}`;
      }
      return config;
    }
    // The token together with the account it belongs to, read now. It may be a
    // refreshed token; what matters is whose it is.
    const identity = await getAccessIdentity();
    const route = authAuthority.dispatchAs(config.authStamp, identity?.userId ?? null);
    if (route === 'refuse') throw new AccountMismatchError();
    if (
      config.expectedAccountId !== undefined
      && (route !== 'session' || identity?.userId !== config.expectedAccountId)
    ) {
      throw new AccountMismatchError();
    }
    if (route === 'session' && identity) {
      config.headers.Authorization = `Bearer ${identity.token}`;
      config.authDispatch = 'session';
      config.authSentAs = { accountId: identity.userId, accessToken: identity.token };
    } else {
      config.authDispatch = 'anonymous';
    }
    return config;
  },
  undefined,
  { runWhen: stampWhenMade },
);

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const status = error?.response?.status;
    const code = error?.response?.data?.detail?.code;
    const stamp = error?.config?.authStamp;
    // Sent without credentials: its answer says nothing about any session.
    const sentAnonymously = error?.config?.authDispatch === 'anonymous';

    // Registration-incomplete: KEEP the Supabase session — the user is
    // authenticated, just not yet a GlamGenius account.
    if (status === 403 && code === 'REGISTRATION_REQUIRED') {
      if (!sentAnonymously && (!authAuthority || authAuthority.acceptsRegistrationRequired(stamp))) {
        onRegistrationRequired?.(stamp);
        try {
          router.replace('/(auth)/registration-incomplete');
        } catch {
          // Router not mounted yet.
        }
      }
      return Promise.reject(error);
    }

    // A stale device token is a scan-identity failure, not an account-auth
    // failure. Let the caller re-register the device without signing out.
    if (status === 401 && code === 'DEVICE_UNKNOWN') {
      return Promise.reject(error);
    }

    if (status === 401) {
      const sentAs = error?.config?.authSentAs ?? null;
      if (!sentAnonymously && (!authAuthority || authAuthority.acceptsUnauthorized(stamp, sentAs))) {
        // Locally first, and synchronously: the session is over the moment
        // this 401 is accepted, and every result still in flight for it is
        // stale from here. The store also quarantines the rejected session, so
        // Supabase's own events cannot bring it back while (or if) the
        // provider sign-out below is slow or fails. That sign-out is best
        // effort; nothing here depends on it succeeding.
        onUnauthorized?.(sentAs);
        await signOut().catch(() => {});
        try {
          router.replace('/(auth)/welcome');
        } catch {
          // Router not mounted yet.
        }
      }
      return Promise.reject(error);
    }
     
    console.error('API Error:', error.response?.data || error.message);
    return Promise.reject(error);
  }
);
