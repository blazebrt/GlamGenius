/**
 * D. A token refresh cannot deadlock on the store's auth subscriber.
 *
 * This runs the installed Supabase auth implementation (``GoTrueClient`` from
 * ``@supabase/auth-js``) and the real API client, not a model of either. Only
 * the network is replaced: Supabase's fetch answers the refresh-token grant,
 * and the API client's adapter answers ``/api/v2/me``.
 *
 * The contract being proved: Supabase awaits its subscribers while it holds
 * its auth lock, and the API client's request interceptor asks
 * ``supabase.auth.getSession()``, which waits for that lock. A subscriber that
 * awaits ``/me`` therefore waits on the lock that is waiting on the
 * subscriber, and the refresh never completes. The store's subscriber must
 * return first and reconcile afterwards.
 *
 * "Never completes" is observed without a clock. A promise caught in a lock
 * cycle stays pending however many turns of the event loop are allowed, while
 * a healthy refresh settles within a handful. The bound below is a count of
 * event-loop turns, not a duration.
 */
import { AxiosError, type InternalAxiosRequestConfig } from 'axios';

jest.mock('../services/supabase', () => {
  const { GoTrueClient } = jest.requireActual('@supabase/auth-js');
  const storageKey = 'sb-auth-integrity-token';
  const memory = new Map<string, string>();
  const storage = {
    getItem: async (key: string) => memory.get(key) ?? null,
    setItem: async (key: string, value: string) => {
      memory.set(key, value);
    },
    removeItem: async (key: string) => {
      memory.delete(key);
    },
  };
  const sessionFor = (tag: string) => ({
    access_token: `access-${tag}`,
    refresh_token: `refresh-${tag}`,
    token_type: 'bearer',
    expires_in: 3600,
    expires_at: Math.floor(Date.now() / 1000) + 3600,
    user: {
      id: 'account-a',
      aud: 'authenticated',
      email: 'account-a@example.com',
      app_metadata: {},
      user_metadata: {},
      created_at: '2026-01-01T00:00:00Z',
    },
  });
  memory.set(storageKey, JSON.stringify(sessionFor('initial')));
  const refreshFetch = async (url: string) => {
    if (String(url).includes('grant_type=refresh_token')) {
      return new Response(JSON.stringify(sessionFor('rotated')), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }
    return new Response(JSON.stringify({ message: 'not handled' }), { status: 404 });
  };
  const client = new GoTrueClient({
    url: 'https://auth-integrity.invalid/auth/v1',
    storageKey,
    storage,
    autoRefreshToken: false,
    persistSession: true,
    detectSessionInUrl: false,
    fetch: refreshFetch,
  });
  return {
    supabase: { auth: client },
    getAccessToken: async () => {
      const { data } = await client.getSession();
      return data.session?.access_token ?? null;
    },
    getAccessIdentity: async () => {
      const { data } = await client.getSession();
      return data.session ? { token: data.session.access_token, userId: data.session.user.id } : null;
    },
    signOut: async () => {
      await client.signOut();
    },
  };
});

jest.mock('../services/productScan', () => ({
  tokenToClaimFor: jest.fn(async () => null),
  markDeviceClaimed: jest.fn(async () => undefined),
}));

/* eslint-disable import/first */
import { api } from '../services/api';
import { supabase } from '../services/supabase';
import { useUserStore } from '../store/userStore';
/* eslint-enable import/first */

const client = supabase.auth;
const meAuthorizations: string[] = [];
let meAnswers: 'offline' | 'registered' = 'offline';

api.defaults.adapter = async (config: InternalAxiosRequestConfig) => {
  if (config.url === '/api/v2/me') {
    meAuthorizations.push(String(config.headers.Authorization));
    if (meAnswers === 'offline') {
      throw new AxiosError('offline', 'ERR_NETWORK', config);
    }
    return {
      data: { profile: { name: 'Alice' }, account: { is_admin: false } },
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    };
  }
  throw new AxiosError(`unexpected request ${config.url}`, 'ERR_BAD_REQUEST', config);
};

/** Whether ``promise`` settles within ``turns`` trips round the event loop. */
async function settlesWithin(promise: Promise<unknown>, turns = 200): Promise<boolean> {
  let settled = false;
  promise.then(
    () => {
      settled = true;
    },
    () => {
      settled = true;
    },
  );
  for (let turn = 0; turn < turns && !settled; turn += 1) {
    await new Promise<void>((resolve) => setTimeout(resolve, 0));
  }
  return settled;
}

describe('D. TOKEN_REFRESHED against the real Supabase auth client', () => {
  it('completes the refresh and reconciles afterwards with the rotated token', async () => {
    jest.spyOn(console, 'warn').mockImplementation(() => undefined);
    jest.spyOn(console, 'error').mockImplementation(() => undefined);

    // Start signed in with the account's registration still undecided: the
    // first /me is offline, so the store stays ``resolving`` and the refresh
    // below has a reconciliation to schedule.
    await useUserStore.getState().initializeUser();
    expect(useUserStore.getState().session?.user.id).toBe('account-a');
    expect(useUserStore.getState().registrationState).not.toBe('registered');

    meAnswers = 'registered';
    const refresh = client.refreshSession();
    expect(await settlesWithin(refresh)).toBe(true);
    const refreshed = await refresh;
    expect(refreshed.error).toBeNull();
    expect(refreshed.data.session?.access_token).toBe('access-rotated');

    // Subsequent session retrieval completes, and returns the rotated token.
    const read = client.getSession();
    expect(await settlesWithin(read)).toBe(true);
    expect((await read).data.session?.access_token).toBe('access-rotated');

    // The reconciliation ran after the subscriber returned, outside the lock,
    // and asked /me as the refreshed session.
    expect(await settlesWithin(
      new Promise<void>((resolve) => {
        const check = () =>
          useUserStore.getState().registrationState === 'registered'
            ? resolve()
            : setTimeout(check, 0);
        check();
      }),
    )).toBe(true);
    expect(meAuthorizations[meAuthorizations.length - 1]).toBe('Bearer access-rotated');
    expect(useUserStore.getState().session?.access_token).toBe('access-rotated');
    expect(useUserStore.getState().user?.name).toBe('Alice');
  });

  it('a same-account refresh of a settled account neither blocks nor refetches', async () => {
    const before = meAuthorizations.length;
    const profile = useUserStore.getState().user;

    const refresh = client.refreshSession();
    expect(await settlesWithin(refresh)).toBe(true);
    expect(await settlesWithin(client.getSession())).toBe(true);

    expect(meAuthorizations.length).toBe(before);
    expect(useUserStore.getState().user).toBe(profile);
    expect(useUserStore.getState().registrationState).toBe('registered');
  });
});
