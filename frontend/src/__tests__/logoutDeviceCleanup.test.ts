/**
 * Lane D, defect E — logout removes this phone's notification device, without
 * weakening Lane B.
 *
 * The real user store runs here, with its real logout. Three edges are replaced
 * and nothing else: the V2 backend calls the store makes itself (``/me``), the
 * Supabase auth client, and ``fetch``, which is the only transport the logout
 * cleanup uses. The installation id lives in the AsyncStorage stub.
 *
 * The rules under test, in order:
 *
 * - local auth ends synchronously and first; nothing waits on the network;
 * - the cleanup uses the bearer token captured before that, never the store;
 * - it reads an existing installation id and never creates one;
 * - it is the same production DELETE the Notifications screen uses;
 * - it is best effort: offline, 401, 5xx or stalled, logout completes and
 *   Supabase sign-out is still called, and nothing waits for it;
 * - the token is never written anywhere;
 * - the 401 reset path is untouched: it sends no cleanup.
 *
 * Every interleaving is built from deferred promises; the only yielding is to
 * the event loop. There are no timing sleeps.
 */
import fs from 'fs';
import path from 'path';
import type { Session } from '@supabase/supabase-js';

type Deferred<T> = {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (error: unknown) => void;
};

function mockDeferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const mockMeCalls: Deferred<unknown>[] = [];

jest.mock('../services/apiV2', () => ({
  getMe: jest.fn(() => {
    const call = mockDeferred<unknown>();
    mockMeCalls.push(call);
    return call.promise;
  }),
  finalizeRegistration: jest.fn(),
  reserveInvite: jest.fn(),
  claimScanDevice: jest.fn(async () => ({ claimed: true, scans_attached: 0 })),
  patchAppearanceProfile: jest.fn(async () => ({})),
}));

jest.mock('../services/api', () => ({
  isRegistrationRequired: () => false,
  setUnauthorizedHandler: jest.fn(),
  setRegistrationRequiredHandler: jest.fn(),
  setAuthResponseAuthority: jest.fn(),
}));

jest.mock('../services/supabase', () => ({
  supabase: {
    auth: {
      onAuthStateChange: jest.fn(() => ({ data: { subscription: { unsubscribe: jest.fn() } } })),
      getSession: jest.fn(async () => ({ data: { session: null }, error: null })),
      signInWithPassword: jest.fn(),
      signUp: jest.fn(),
      signOut: jest.fn(async () => ({ error: null })),
    },
  },
}));

jest.mock('../services/productScan', () => ({
  tokenToClaimFor: jest.fn(async () => null),
  markDeviceClaimed: jest.fn(async () => undefined),
}));

/* eslint-disable import/first */
import AsyncStorage from '@react-native-async-storage/async-storage';
import * as SecureStore from 'expo-secure-store';
import { useUserStore } from '../store/userStore';
import { supabase } from '../services/supabase';
import { setUnauthorizedHandler } from '../services/api';
import { getInstallationId, readInstallationId } from '../services/deviceIdentity';
import { notificationDevicePath } from '../services/notificationRoutes';
/* eslint-enable import/first */

const INSTALLATION_KEY = 'glamgenius_installation_id_v1';
const INSTALLATION_ID = '3f1c2b7a-9d4e-4a6b-8c1d-2e3f4a5b6c7d';

const authCallback = (supabase.auth.onAuthStateChange as jest.Mock).mock.calls[0][0] as (
  event: string,
  session: Session | null,
) => unknown;
const unauthorized = (setUnauthorizedHandler as jest.Mock).mock.calls[0][0] as (
  rejected?: { accountId: string; accessToken: string } | null,
) => void;

function session(id: string, token = `token-${id}`): Session {
  return {
    access_token: token,
    refresh_token: `refresh-${id}`,
    token_type: 'bearer',
    expires_in: 3600,
    expires_at: Math.floor(Date.now() / 1000) + 3600,
    user: {
      id,
      email: `${id}@example.com`,
      aud: 'authenticated',
      app_metadata: {},
      user_metadata: {},
      created_at: '2026-01-01T00:00:00Z',
    },
  } as unknown as Session;
}

const nextMacrotask = () => new Promise<void>((resolve) => setTimeout(resolve, 0));
async function settle() {
  for (let i = 0; i < 5; i += 1) await nextMacrotask();
}
const state = () => useUserStore.getState();

// --- fetch: the cleanup's only transport, answered by hand ---------------------
interface SentCleanup {
  url: string;
  method: string;
  authorization: string | undefined;
  answer: Deferred<Response>;
}
const sent: SentCleanup[] = [];
const fetchMock = jest.fn((url: string, init: { method?: string; headers?: Record<string, string> } = {}) => {
  const answer = mockDeferred<Response>();
  sent.push({ url, method: init.method ?? 'GET', authorization: init.headers?.Authorization, answer });
  return answer.promise;
});

function storedInstallationId(value: string | null) {
  (AsyncStorage.getItem as jest.Mock).mockImplementation(async (key: string) =>
    (key === INSTALLATION_KEY ? value : null));
}

async function signedInRegistered(id: string, admin = true) {
  authCallback('SIGNED_IN', session(id));
  await nextMacrotask();
  mockMeCalls[mockMeCalls.length - 1].resolve({ profile: { name: id }, account: { is_admin: admin } });
  await settle();
  expect(state()).toMatchObject({ userId: id, registrationState: 'registered', isAdmin: admin });
}

function expectSignedOut() {
  expect(state().session).toBeNull();
  expect(state().user).toBeNull();
  expect(state().userId).toBe('');
  expect(state().registrationState).toBe('signed_out');
  expect(state().isAdmin).toBe(false);
}

const signOut = supabase.auth.signOut as jest.Mock;
const originalFetch = global.fetch;

beforeEach(async () => {
  global.fetch = fetchMock as unknown as typeof fetch;
  authCallback('SIGNED_OUT', null);
  await settle();
  mockMeCalls.length = 0;
  sent.length = 0;
  jest.clearAllMocks();
  storedInstallationId(null);
  useUserStore.setState({ loading: false, pendingChallenge: null });
});

afterEach(() => {
  for (const request of sent) request.answer.resolve(new Response(null, { status: 200 }));
  (AsyncStorage.getItem as jest.Mock).mockImplementation(() => Promise.resolve(null));
  global.fetch = originalFetch;
});

describe('A. a normal logout removes this installation for the account signing out', () => {
  it('signs out locally first, then sends one DELETE with the captured token, then signs out of Supabase', async () => {
    await signedInRegistered('account-a');
    storedInstallationId(INSTALLATION_ID);

    const done = state().logout();
    expectSignedOut(); // synchronously, before any await
    await done;

    expect(sent).toHaveLength(1);
    expect(sent[0].method).toBe('DELETE');
    expect(sent[0].url).toBe(notificationDevicePath(INSTALLATION_ID));
    expect(sent[0].url).toBe(`/api/v2/today/notifications/devices/${INSTALLATION_ID}`);
    expect(sent[0].authorization).toBe('Bearer token-account-a');
    expect(signOut).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.invocationCallOrder[0]).toBeLessThan(signOut.mock.invocationCallOrder[0]);
    expect(AsyncStorage.setItem).not.toHaveBeenCalled();
  });
});

describe('B. local auth is cleared before the cleanup completes', () => {
  it('is signed out, and Supabase sign-out has run, while the DELETE is still unanswered', async () => {
    await signedInRegistered('account-a');
    storedInstallationId(INSTALLATION_ID);

    const done = state().logout();
    expectSignedOut();
    await done; // logout resolves without waiting for the network
    await settle();

    expect(sent).toHaveLength(1);
    expectSignedOut();
    expect(signOut).toHaveBeenCalledTimes(1);
    sent[0].answer.resolve(new Response(null, { status: 200 }));
    await settle();
    expectSignedOut();
  });
});

describe('C. no installation id', () => {
  it('mints nothing, sends nothing, and still logs out', async () => {
    await signedInRegistered('account-a');
    storedInstallationId(null);

    await state().logout();
    await settle();

    expect(sent).toHaveLength(0);
    expect(AsyncStorage.setItem).not.toHaveBeenCalled();
    expect(signOut).toHaveBeenCalledTimes(1);
    expectSignedOut();
  });

  it('treats a malformed stored id as none, and does not replace it', async () => {
    await signedInRegistered('account-a');
    storedInstallationId('not-an-installation-id');

    await state().logout();
    await settle();

    expect(sent).toHaveLength(0);
    expect(AsyncStorage.setItem).not.toHaveBeenCalled();
    expect(signOut).toHaveBeenCalledTimes(1);
  });

  it('readInstallationId only reads; getInstallationId still creates one when asked', async () => {
    storedInstallationId(null);
    expect(await readInstallationId()).toBeNull();
    storedInstallationId(INSTALLATION_ID);
    expect(await readInstallationId()).toBe(INSTALLATION_ID);
    (AsyncStorage.getItem as jest.Mock).mockImplementation(async () => {
      throw new Error('storage unavailable');
    });
    expect(await readInstallationId()).toBeNull();
    expect(AsyncStorage.setItem).not.toHaveBeenCalled();

    storedInstallationId(null);
    const created = await getInstallationId();
    expect(created).toMatch(/^[0-9a-f-]{36}$/);
    expect(AsyncStorage.setItem).toHaveBeenCalledWith(INSTALLATION_KEY, created);
  });
});

describe('D. a failing cleanup never blocks or undoes logout', () => {
  it.each([
    ['network failure', (request: SentCleanup) => request.answer.reject(new TypeError('Network request failed'))],
    ['401', (request: SentCleanup) => request.answer.resolve(new Response(null, { status: 401 }))],
    ['server error', (request: SentCleanup) => request.answer.resolve(new Response(null, { status: 500 }))],
    ['a stall that never answers', () => undefined],
  ])('%s', async (_name, answer) => {
    await signedInRegistered('account-a');
    storedInstallationId(INSTALLATION_ID);

    await state().logout();
    expect(sent).toHaveLength(1);
    answer(sent[0]);
    await settle();

    expectSignedOut();
    expect(signOut).toHaveBeenCalledTimes(1);
    expect(state().pendingChallenge).toBeNull();
  });

  it('a Supabase sign-out that fails still leaves the app signed out, cleanup or not', async () => {
    await signedInRegistered('account-a');
    storedInstallationId(INSTALLATION_ID);
    signOut.mockResolvedValueOnce({ error: { message: 'auth server unavailable' } });
    const quiet = jest.spyOn(console, 'error').mockImplementation(() => undefined);
    try {
      await state().logout();
    } finally {
      quiet.mockRestore();
    }
    expect(sent).toHaveLength(1);
    expectSignedOut();
  });
});

describe('E. A logs out, then B signs in on the same phone before A\'s cleanup lands', () => {
  it('A\'s late request still carries only A\'s credential, and B stays signed in', async () => {
    await signedInRegistered('account-a');
    storedInstallationId(INSTALLATION_ID);

    await state().logout();
    expect(sent).toHaveLength(1);
    const lateCleanupForA = sent[0];

    authCallback('SIGNED_IN', session('account-b'));
    await nextMacrotask();
    mockMeCalls[mockMeCalls.length - 1].resolve({ profile: { name: 'B' }, account: { is_admin: false } });
    await settle();
    expect(state().userId).toBe('account-b');

    lateCleanupForA.answer.resolve(new Response(null, { status: 200 }));
    await settle();

    // The server scopes the DELETE to the bearer's account and this exact device
    // key (backend: test_a_late_cleanup_by_a_signed_out_account_cannot_touch_...).
    expect(lateCleanupForA.authorization).toBe('Bearer token-account-a');
    expect(sent).toHaveLength(1);
    expect(state()).toMatchObject({ userId: 'account-b', registrationState: 'registered' });
    expect(state().session?.access_token).toBe('token-account-b');
  });
});

describe('F. the captured token is never persisted or logged', () => {
  it('writes the token nowhere, at any point of the logout and its cleanup', async () => {
    await signedInRegistered('account-a');
    storedInstallationId(INSTALLATION_ID);
    (AsyncStorage.setItem as jest.Mock).mockClear();
    (SecureStore.setItemAsync as jest.Mock).mockClear();
    const consoles = (['log', 'info', 'warn', 'error', 'debug'] as const).map((level) =>
      jest.spyOn(console, level).mockImplementation(() => undefined));
    try {
      await state().logout();
      sent[0].answer.reject(new TypeError('Network request failed'));
      await settle();
      const written = [
        ...(AsyncStorage.setItem as jest.Mock).mock.calls,
        ...(SecureStore.setItemAsync as jest.Mock).mock.calls,
        ...consoles.flatMap((spy) => spy.mock.calls),
      ];
      expect(JSON.stringify(written)).not.toContain('token-account-a');
    } finally {
      consoles.forEach((spy) => spy.mockRestore());
    }
  });

  it('the cleanup module holds no storage and no logging at all', () => {
    const source = fs.readFileSync(path.join(__dirname, '..', 'services', 'logoutDeviceCleanup.ts'), 'utf8');
    const code = source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');
    for (const forbidden of ['AsyncStorage', 'SecureStore', 'secureSessionStorage', 'console.', 'setItem']) {
      expect(code).not.toContain(forbidden);
    }
    // The one transport, with the one route, and no second API client.
    expect(code).toContain('notificationDevicePath(deviceKey)');
    expect(code).not.toMatch(/from ['"]axios['"]|from ['"]\.\/api['"]|from ['"]\.\/apiV2['"]/);
  });
});

describe('G. the 401 reset path is untouched', () => {
  it('signs out synchronously and sends no cleanup with the rejected credential', async () => {
    await signedInRegistered('account-a');
    storedInstallationId(INSTALLATION_ID);

    unauthorized({ accountId: 'account-a', accessToken: 'token-account-a' });
    expectSignedOut();
    await settle();

    expect(sent).toHaveLength(0);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe('one server authority for removing a device', () => {
  it('the Notifications screen and logout reach the same production route', () => {
    const apiV2 = fs.readFileSync(path.join(__dirname, '..', 'services', 'apiV2.ts'), 'utf8');
    expect(apiV2).toContain('api.delete(notificationDevicePath(deviceKey))');
    expect(apiV2).not.toMatch(/api\.delete\(`\$\{V2\}\/today\/notifications\/devices/);
    expect(notificationDevicePath('a b/c')).toBe('/api/v2/today/notifications/devices/a%20b%2Fc');
  });
});
