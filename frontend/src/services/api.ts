/**
 * Shared HTTP client for the FastAPI backend.
 *
 * The Bearer token comes from the current Supabase session. It is fetched on
 * every request (Supabase caches it in-memory, so this is cheap) so a rotated
 * or refreshed token is picked up automatically.
 *
 * Two response codes trigger client-side navigation (§2 hardening spec):
 *
 *   401 Unauthorized              → sign out of Supabase, go to /(auth)/welcome
 *   403 REGISTRATION_REQUIRED     → keep the Supabase session, route to
 *                                    /(auth)/registration-incomplete so the
 *                                    user can finish invite redemption.
 *
 * A plain 403 without ``code === "REGISTRATION_REQUIRED"`` (e.g. an admin-only
 * route) is left to the calling screen to render, exactly as before.
 */
import axios from 'axios';
import { router } from 'expo-router';
import { getAccessToken, signOut } from './supabase';

const BACKEND_URL = process.env.EXPO_PUBLIC_BACKEND_URL || '';

if (!BACKEND_URL) {
   
  console.warn(
    'EXPO_PUBLIC_BACKEND_URL is not set. API requests will fail until the backend URL is configured.'
  );
}

// Lets the user store clear its own state when the session ends.
let onUnauthorized: (() => void) | null = null;
let onRegistrationRequired: (() => void) | null = null;

export const setUnauthorizedHandler = (handler: (() => void) | null) => {
  onUnauthorized = handler;
};

export const setRegistrationRequiredHandler = (
  handler: (() => void) | null
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
    anyErr?.response?.data?.detail?.code === 'AI_RATE_LIMIT'
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

    // Registration-incomplete: KEEP the Supabase session — the user is
    // authenticated, just not yet a GlamGenius account.
    if (status === 403 && code === 'REGISTRATION_REQUIRED') {
      onRegistrationRequired?.();
      try {
        router.replace('/(auth)/registration-incomplete');
      } catch {
        // Router not mounted yet.
      }
      return Promise.reject(error);
    }

    // A stale device token is a scan-identity failure, not an account-auth
    // failure. Let the caller re-register the device without signing out.
    if (status === 401 && code === 'DEVICE_UNKNOWN') {
      return Promise.reject(error);
    }

    if (status === 401) {
      await signOut().catch(() => {});
      onUnauthorized?.();
      try {
        router.replace('/(auth)/welcome');
      } catch {
        // Router not mounted yet.
      }
      return Promise.reject(error);
    }
     
    console.error('API Error:', error.response?.data || error.message);
    return Promise.reject(error);
  }
);
