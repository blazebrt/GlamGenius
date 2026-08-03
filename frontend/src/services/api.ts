/**
 * Shared HTTP client for the FastAPI backend.
 *
 * The Bearer token comes from the current Supabase session. It is fetched on
 * every request (Supabase caches it in-memory, so this is cheap) so a rotated
 * or refreshed token is picked up automatically.
 *
 * The baseURL is the raw backend URL, and callers pass full paths starting
 * with `/api/v2/...`. There are no active V1 paths — the static test at
 * `src/__tests__/no_v1_paths.test.ts` fails the build if one appears.
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

export const setUnauthorizedHandler = (handler: (() => void) | null) => {
  onUnauthorized = handler;
};

// eslint-disable-next-line import/no-named-as-default-member
export const api = axios.create({
  baseURL: BACKEND_URL ? BACKEND_URL.replace(/\/$/, '') : undefined,
  timeout: 60000, // 60 seconds — some AI endpoints legitimately take that long.
  headers: {
    'Content-Type': 'application/json',
  },
});

/**
 * Pull a message worth showing out of a failed request. The backend writes
 * refusals for a person to read (hourly limit, monthly limit, etc.); screens
 * should show that text rather than replacing it with a generic apology.
 */
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

/** True when the caller has hit a rate limit. */
export const isRateLimited = (err: unknown): boolean => {
  const anyErr = err as {
    response?: { status?: number; data?: { detail?: { code?: string } } };
  };
  return (
    anyErr?.response?.status === 429 ||
    anyErr?.response?.data?.detail?.code === 'AI_RATE_LIMIT'
  );
};

// Attach the Supabase access token to every request.
api.interceptors.request.use(async (config) => {
  const token = await getAccessToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Handle expired / invalid sessions: sign out of Supabase and go to welcome.
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    if (error.response?.status === 401) {
      await signOut().catch(() => {});
      onUnauthorized?.();
      try {
        router.replace('/(auth)/welcome');
      } catch {
        // Router may not be mounted yet during boot; ignore.
      }
      return Promise.reject(error);
    }
     
    console.error('API Error:', error.response?.data || error.message);
    return Promise.reject(error);
  }
);
