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
 * or been replaced is passed back to its caller and changes nothing else.
 *
 * That is the response side. The request side is separate: a request that
 * belongs to one particular account carries ``expectedAccountId`` and is sent
 * with that account's own token or not at all. Checking a response afterwards
 * can protect local state, but it cannot take back a change the server has
 * already made as the wrong account.
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
    /** Opaque auth-generation stamp, taken when the request was prepared. */
    authStamp?: unknown;
  }
}

/** Raised, before anything is sent, when an account-bound request finds another account signed in. */
export class AccountMismatchError extends Error {
  readonly code = 'ACCOUNT_MISMATCH';

  constructor() {
    super('The signed-in account changed before this request was sent.');
    this.name = 'AccountMismatchError';
  }
}

/** Decides whether a response may still change the signed-in state. */
export interface AuthResponseAuthority {
  stamp: () => unknown;
  acceptsUnauthorized: (stamp: unknown) => boolean;
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

// Lets the user store clear its own state when the session ends.
let onUnauthorized: (() => void) | null = null;
let onRegistrationRequired: ((stamp: unknown) => void) | null = null;

export const setUnauthorizedHandler = (handler: (() => void) | null) => {
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

api.interceptors.request.use(async (config) => {
  // Stamped before the token is read. If the identity changes while it is
  // read, a response is treated as stale; the other way round would let an old
  // account's 401 sign out the new one.
  config.authStamp = authAuthority?.stamp();
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
});

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const status = error?.response?.status;
    const code = error?.response?.data?.detail?.code;
    const stamp = error?.config?.authStamp;

    // Registration-incomplete: KEEP the Supabase session — the user is
    // authenticated, just not yet a GlamGenius account.
    if (status === 403 && code === 'REGISTRATION_REQUIRED') {
      if (!authAuthority || authAuthority.acceptsRegistrationRequired(stamp)) {
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
      if (!authAuthority || authAuthority.acceptsUnauthorized(stamp)) {
        // Locally first, and synchronously: the session is over the moment
        // this 401 is accepted, and every result still in flight for it is
        // stale from here. Waiting for Supabase first would leave that window
        // open for as long as the sign-out takes.
        onUnauthorized?.();
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
