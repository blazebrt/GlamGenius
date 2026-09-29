/**
 * A session the app has ended cannot come back on its own.
 *
 * When the backend's 401 for the current identity is accepted, the app signs
 * out locally at once and then asks Supabase to sign out. Supabase can keep
 * the session while that runs, or for good if it fails; its sign-out reports
 * such a failure as ``{ error }`` and keeps the stored session. Meanwhile its
 * own machinery keeps going: a token refresh, a recovered session (which
 * Supabase announces as SIGNED_IN), a user update. None of that is the person
 * signing in, and none of it may bring the rejected account back.
 *
 * A new session is different. An explicit sign-in, or a session Supabase
 * establishes from a new link, has a new ``session_id`` and is adopted
 * normally.
 *
 * The real store, the real API client and the real ``services/supabase`` run
 * here. Only the network and the Supabase client are replaced: the network is
 * answered by hand, and the client is in memory, with a ``signOut`` that can
 * be paused or made to fail. Access tokens are JWT-shaped and carry a
 * ``session_id``, as Supabase's do. Supabase's events go through the
 * subscriber the store registered. There are no timing sleeps.
 */
import { AxiosError, type InternalAxiosRequestConfig } from 'axios';
import type { Session } from '@supabase/supabase-js';

type Deferred<T> = { promise: Promise<T>; resolve: (value: T) => void };

function mockDeferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

interface MockSent {
  url: string;
  authorization: string | undefined;
  answered: boolean;
  answer: (status: number, data?: unknown) => void;
}
const mockSent: MockSent[] = [];

function mockTransport(config: InternalAxiosRequestConfig): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const entry: MockSent = {
      url: String(config.url ?? ''),
      authorization: (config.headers ?? {}).Authorization as string | undefined,
      answered: false,
      answer: (status, data = {}) => {
        entry.answered = true;
        const response = { data, status, statusText: String(status), headers: {}, config };
        if (status < 400) resolve(response);
        else reject(new AxiosError(`status ${status}`, 'ERR_BAD_RESPONSE', config, null, response as never));
      },
    };
    mockSent.push(entry);
  });
}

jest.mock('axios', () => {
  const actual = jest.requireActual('axios');
  const realCreate = actual.create;
  const create = (config: Record<string, unknown> = {}) =>
    realCreate({ ...config, adapter: (request: InternalAxiosRequestConfig) => mockTransport(request) });
  actual.create = create;
  if (actual.default) actual.default.create = create;
  return actual;
});

jest.mock('@supabase/supabase-js', () => {
  const auth = {
    onAuthStateChange: jest.fn(() => ({ data: { subscription: { unsubscribe: jest.fn() } } })),
    getSession: jest.fn(),
    signOut: jest.fn(),
    signUp: jest.fn(),
    signInWithPassword: jest.fn(),
  };
  return { createClient: jest.fn(() => ({ auth })), __auth: auth };
});

/* eslint-disable import/first */
import { router } from 'expo-router';
import { AccountMismatchError, api } from '../services/api';
import { signOut as supabaseSignOut } from '../services/supabase';
import { useUserStore } from '../store/userStore';
/* eslint-enable import/first */

const mockAuth = jest.requireMock('@supabase/supabase-js').__auth as Record<string, jest.Mock>;
const authCallback = mockAuth.onAuthStateChange.mock.calls[0][0] as (
  event: string,
  session: Session | null,
) => unknown;

const ME = '/api/v2/me';
const URL = '/api/v2/notifications';

// What Supabase itself holds, and how its sign-out behaves.
let supabaseSession: Session | null = null;
let signOutGate: Deferred<void> | null = null;
let signOutFails = false;
let sessionGate: Deferred<void> | null = null;

mockAuth.getSession.mockImplementation(async () => {
  const gate = sessionGate;
  sessionGate = null;
  if (gate) await gate.promise;
  return { data: { session: supabaseSession }, error: null };
});

/** The next ``getSession`` call waits until the returned gate is opened. */
function pauseNextGetSession(): Deferred<void> {
  sessionGate = mockDeferred<void>();
  return sessionGate;
}
mockAuth.signOut.mockImplementation(async () => {
  const gate = signOutGate;
  signOutGate = null;
  if (gate) await gate.promise;
  if (signOutFails) {
    // What Supabase does when its logout call fails: report it, keep the session.
    return { error: { name: 'AuthRetryableFetchError', message: 'Failed to fetch' } };
  }
  supabaseSession = null;
  authCallback('SIGNED_OUT', null);
  return { error: null };
});

const base64Url = (value: string) =>
  Buffer.from(value).toString('base64').replace(/=+$/, '').replace(/\+/g, '-').replace(/\//g, '_');

/** A JWT-shaped access token carrying the claims Supabase puts there. */
function jwt(accountId: string, sessionId: string, tag: string): string {
  const header = base64Url(JSON.stringify({ alg: 'HS256', typ: 'JWT' }));
  const payload = base64Url(JSON.stringify({ sub: accountId, session_id: sessionId, tag }));
  return `${header}.${payload}.signature-${tag}`;
}

/** A Supabase session for ``accountId``; ``sessionId`` null gives an unreadable token. */
function session(accountId: string, sessionId: string | null, tag = 'initial'): Session {
  return {
    access_token: sessionId ? jwt(accountId, sessionId, tag) : `opaque-${accountId}-${tag}`,
    refresh_token: `refresh-${accountId}-${tag}`,
    token_type: 'bearer',
    expires_in: 3600,
    expires_at: Math.floor(Date.now() / 1000) + 3600,
    user: {
      id: accountId,
      email: `${accountId}@example.com`,
      aud: 'authenticated',
      app_metadata: {},
      user_metadata: {},
      created_at: '2026-01-01T00:00:00Z',
    },
  } as unknown as Session;
}

/** Supabase announces ``next`` as its current session, as its own machinery would. */
function supabaseEmits(event: string, next: Session) {
  supabaseSession = next;
  authCallback(event, next);
}

const nextMacrotask = () => new Promise<void>((resolve) => setTimeout(resolve, 0));
async function settle() {
  for (let i = 0; i < 10; i += 1) await nextMacrotask();
}

function outcome<T>(request: Promise<T>): Promise<T | unknown> {
  return request.then((value) => value, (error: unknown) => error);
}

const sentTo = (url: string) => mockSent.filter((request) => request.url === url);
function only(url: string): MockSent {
  const waiting = sentTo(url).filter((request) => !request.answered);
  expect(waiting).toHaveLength(1);
  return waiting[0];
}

const state = () => useUserStore.getState();
const registered = (name: string) => ({ profile: { name }, account: { is_admin: false } });
const SIGNED_OUT = {
  session: null,
  user: null,
  userId: '',
  registrationState: 'signed_out',
  isAdmin: false,
};

async function signInRegistered(next: Session, name = next.user.id) {
  supabaseEmits('SIGNED_IN', next);
  await settle();
  only(ME).answer(200, registered(name));
  await settle();
  expect(state()).toMatchObject({ userId: next.user.id, registrationState: 'registered' });
}

/**
 * A current request for the signed-in account is answered 401, and the app
 * accepts it. The request is handed back unawaited: it settles only once the
 * interceptor's Supabase sign-out has, and a test may be holding that.
 */
async function acceptedUnauthorized(): Promise<{ request: Promise<unknown> }> {
  const request = outcome(api.get(URL));
  await settle();
  only(URL).answer(401, { detail: { code: 'ACCOUNT_UNAUTHORIZED' } });
  await settle();
  return { request };
}

beforeEach(async () => {
  signOutGate = null;
  signOutFails = false;
  sessionGate = null;
  supabaseSession = null;
  authCallback('SIGNED_OUT', null);
  await settle();
  mockSent.length = 0;
  jest.clearAllMocks();
  jest.spyOn(console, 'error').mockImplementation(() => undefined);
  jest.spyOn(console, 'warn').mockImplementation(() => undefined);
  useUserStore.setState({ pendingChallenge: null, loading: false });
});

describe('an account rejected by an accepted 401 is not re-adopted by its own session', () => {
  it('TOKEN_REFRESHED for the rejected session while Supabase\'s sign-out is paused does not bring A back', async () => {
    await signInRegistered(session('account-a', 'session-a1'));
    signOutGate = mockDeferred<void>();
    const gate = signOutGate;

    const { request } = await acceptedUnauthorized();
    expect(mockAuth.signOut).toHaveBeenCalledTimes(1);
    expect(state()).toMatchObject(SIGNED_OUT);

    // Supabase's refresh of the rejected session completes while its sign-out waits.
    const meBefore = sentTo(ME).length;
    supabaseEmits('TOKEN_REFRESHED', session('account-a', 'session-a1', 'rotated'));
    await settle();
    expect(state()).toMatchObject(SIGNED_OUT);
    expect(sentTo(ME)).toHaveLength(meBefore);

    gate.resolve();
    expect(await request).toBeInstanceOf(AxiosError);
    await settle();
    expect(state()).toMatchObject(SIGNED_OUT);
    expect(router.replace).toHaveBeenCalledWith('/(auth)/welcome');
  });

  it('stays signed out when Supabase\'s sign-out fails and keeps the session', async () => {
    await signInRegistered(session('account-a', 'session-a1'));
    signOutFails = true;

    await acceptedUnauthorized();
    await settle();
    // Supabase still holds the rejected session.
    expect(supabaseSession?.user.id).toBe('account-a');
    expect(state()).toMatchObject(SIGNED_OUT);

    supabaseEmits('TOKEN_REFRESHED', session('account-a', 'session-a1', 'rotated'));
    await settle();
    expect(state()).toMatchObject(SIGNED_OUT);

    // Nothing the app sends now uses the rejected session's token.
    const later = outcome(api.get('/api/v2/public/catalogue'));
    await settle();
    expect(only('/api/v2/public/catalogue').authorization).toBeUndefined();
    only('/api/v2/public/catalogue').answer(200, {});
    await later;
  });

  it('repeated automatic events for the rejected session never reopen it', async () => {
    await signInRegistered(session('account-a', 'session-a1'));
    signOutFails = true;
    await acceptedUnauthorized();
    await settle();
    const meBefore = sentTo(ME).length;

    for (const [event, tag] of [
      ['TOKEN_REFRESHED', 'rotated-1'],
      ['TOKEN_REFRESHED', 'rotated-2'],
      // Supabase announces a recovered stored session as SIGNED_IN.
      ['SIGNED_IN', 'recovered'],
      ['INITIAL_SESSION', 'initial-again'],
      ['USER_UPDATED', 'updated'],
      ['TOKEN_REFRESHED', 'rotated-3'],
    ]) {
      supabaseEmits(event, session('account-a', 'session-a1', tag));
      await settle();
      expect(state()).toMatchObject(SIGNED_OUT);
    }
    expect(sentTo(ME)).toHaveLength(meBefore);
  });

  it('an old session\'s 401, landing after the same account moved to a new session, leaves the new session signed in', async () => {
    await signInRegistered(session('account-a', 'session-a1'), 'Alice');
    const profile = state().user;
    const request = outcome(api.get(URL));
    await settle();
    const sentWithA1 = only(URL);
    expect(sentWithA1.authorization).toBe(`Bearer ${session('account-a', 'session-a1').access_token}`);

    // The same account signs in again: a genuinely new Supabase session.
    const a2 = session('account-a', 'session-a2');
    supabaseEmits('SIGNED_IN', a2);
    await settle();
    expect(state().session?.access_token).toBe(a2.access_token);
    (router.replace as jest.Mock).mockClear();

    // The 401 rejects A1's token. It still reaches its caller...
    sentWithA1.answer(401, { detail: { code: 'ACCOUNT_UNAUTHORIZED' } });
    expect(await request).toBeInstanceOf(AxiosError);
    await settle();

    // ...but has no say over A2.
    expect(mockAuth.signOut).not.toHaveBeenCalled();
    expect(router.replace).not.toHaveBeenCalled();
    expect(state()).toMatchObject({ userId: 'account-a', registrationState: 'registered' });
    expect(state().session?.access_token).toBe(a2.access_token);
    expect(state().user).toBe(profile);

    // A2 was not quarantined: its own refresh is adopted as usual.
    const rotated = session('account-a', 'session-a2', 'rotated');
    supabaseEmits('TOKEN_REFRESHED', rotated);
    await settle();
    expect(state().session?.access_token).toBe(rotated.access_token);
  });

  it('a stale 401 for A after B became current leaves B, and B\'s own token refresh, untouched', async () => {
    await signInRegistered(session('account-a', 'session-a1'), 'Alice');
    const requestA = outcome(api.get(URL));
    await settle();
    const sentAsA = only(URL);

    await signInRegistered(session('account-b', 'session-b1'), 'Bob');
    const profileB = state().user;
    (router.replace as jest.Mock).mockClear();
    sentAsA.answer(401, { detail: { code: 'ACCOUNT_UNAUTHORIZED' } });
    expect(await requestA).toBeInstanceOf(AxiosError);
    await settle();

    expect(mockAuth.signOut).not.toHaveBeenCalled();
    expect(router.replace).not.toHaveBeenCalled();
    expect(state()).toMatchObject({ userId: 'account-b', registrationState: 'registered' });

    const rotated = session('account-b', 'session-b1', 'rotated');
    supabaseEmits('TOKEN_REFRESHED', rotated);
    await settle();
    expect(state().session?.access_token).toBe(rotated.access_token);
    expect(state().user).toBe(profileB);
  });

  it('a later explicit sign-in establishes A again, with its new session', async () => {
    await signInRegistered(session('account-a', 'session-a1'));
    signOutFails = true;
    await acceptedUnauthorized();
    await settle();

    const fresh = session('account-a', 'session-a2');
    mockAuth.signInWithPassword.mockImplementationOnce(async () => {
      supabaseEmits('SIGNED_IN', fresh);
      return { data: { session: fresh, user: fresh.user }, error: null };
    });
    const login = state().login('account-a@example.com', 'secret');
    await settle();
    const me = only(ME);
    expect(me.authorization).toBe(`Bearer ${fresh.access_token}`);
    me.answer(200, registered('Alice'));

    await expect(login).resolves.toEqual({ ok: true });
    expect(state()).toMatchObject({ userId: 'account-a', registrationState: 'registered' });
    expect(state().session?.access_token).toBe(fresh.access_token);
  });

  it('an explicit sign-in establishes A again even when sessions cannot be told apart', async () => {
    // Tokens that do not decode: the quarantine falls back to the account.
    await signInRegistered(session('account-a', null));
    signOutFails = true;
    await acceptedUnauthorized();
    await settle();

    // Supabase's automatic events for A are held back...
    supabaseEmits('TOKEN_REFRESHED', session('account-a', null, 'rotated'));
    await settle();
    expect(state()).toMatchObject(SIGNED_OUT);

    // ...but a sign-in the person makes is theirs to make.
    const fresh = session('account-a', null, 'relogin');
    mockAuth.signInWithPassword.mockImplementationOnce(async () => {
      supabaseEmits('SIGNED_IN', fresh);
      return { data: { session: fresh, user: fresh.user }, error: null };
    });
    const login = state().login('account-a@example.com', 'secret');
    await settle();
    only(ME).answer(200, registered('Alice'));

    await expect(login).resolves.toEqual({ ok: true });
    expect(state()).toMatchObject({ userId: 'account-a', registrationState: 'registered' });
  });

  it('a new session Supabase establishes on its own, such as from a confirmation link, is adopted', async () => {
    await signInRegistered(session('account-a', 'session-a1'));
    signOutFails = true;
    await acceptedUnauthorized();
    await settle();

    supabaseEmits('SIGNED_IN', session('account-a', 'session-a3', 'link'));
    await settle();
    expect(state()).toMatchObject({ userId: 'account-a', registrationState: 'resolving' });
    expect(only(ME).authorization).toBe(`Bearer ${session('account-a', 'session-a3', 'link').access_token}`);
  });

  it('once Supabase reports it signed out, the next session for A is adopted as usual', async () => {
    await signInRegistered(session('account-a', 'session-a1'));
    await acceptedUnauthorized();
    await settle();
    expect(supabaseSession).toBeNull();

    supabaseEmits('SIGNED_IN', session('account-a', 'session-a4'));
    await settle();
    expect(state()).toMatchObject({ userId: 'account-a', registrationState: 'resolving' });
  });
});

describe('a new Supabase session for the same account is a new authority', () => {
  it('an old session\'s REGISTRATION_REQUIRED neither rewrites nor reroutes the new session', async () => {
    await signInRegistered(session('account-a', 'session-a1'), 'Alice');
    const request = outcome(api.get(URL));
    await settle();
    const sentWithA1 = only(URL);

    supabaseEmits('SIGNED_IN', session('account-a', 'session-a2'));
    await settle();
    (router.replace as jest.Mock).mockClear();
    sentWithA1.answer(403, { detail: { code: 'REGISTRATION_REQUIRED' } });
    expect(await request).toBeInstanceOf(AxiosError);
    await settle();

    expect(router.replace).not.toHaveBeenCalled();
    expect(state()).toMatchObject({ userId: 'account-a', registrationState: 'registered' });
  });

  it('a /me the old session sent cannot settle the new one; the new session gets its own check', async () => {
    supabaseEmits('SIGNED_IN', session('account-a', 'session-a1'));
    await settle();
    const meA1 = only(ME);

    const a2 = session('account-a', 'session-a2');
    supabaseEmits('SIGNED_IN', a2);
    await settle();
    const meA2 = sentTo(ME).find((request) => !request.answered && request !== meA1);
    expect(meA2?.authorization).toBe(`Bearer ${a2.access_token}`);

    meA1.answer(403, { detail: { code: 'REGISTRATION_REQUIRED' } });
    await settle();
    expect(state()).toMatchObject({ userId: 'account-a', registrationState: 'resolving' });

    meA2!.answer(200, registered('Alice'));
    await settle();
    expect(state()).toMatchObject({ userId: 'account-a', registrationState: 'registered' });
    expect(state().user?.name).toBe('Alice');
  });

  it('a request begun under the old session is not sent once the new session is authoritative', async () => {
    await signInRegistered(session('account-a', 'session-a1'));

    const gate = pauseNextGetSession();
    const request = outcome(api.get(URL));
    await settle();
    supabaseEmits('SIGNED_IN', session('account-a', 'session-a2'));
    gate.resolve();
    await settle();

    expect(sentTo(URL)).toHaveLength(0);
    expect(await request).toBeInstanceOf(AccountMismatchError);
    expect(state()).toMatchObject({ userId: 'account-a', registrationState: 'registered' });
  });

  it('a same-session token refresh keeps the same authority', async () => {
    await signInRegistered(session('account-a', 'session-a1'), 'Alice');
    const profile = state().user;
    const checksBefore = sentTo(ME).length;

    // One request already sent with the pre-refresh token...
    const sentEarlier = outcome(api.get(URL));
    await settle();
    const withOldToken = only(URL);
    // ...and one begun before the refresh whose token is read after it.
    const gate = pauseNextGetSession();
    const readsAfter = outcome(api.get('/api/v2/public/after-refresh'));
    await settle();

    const rotated = session('account-a', 'session-a1', 'rotated');
    supabaseEmits('TOKEN_REFRESHED', rotated);
    gate.resolve();
    await settle();

    // Nothing was invalidated: it goes out with the fresh token, the profile is
    // the same object, and no /me was needed.
    const fresh = only('/api/v2/public/after-refresh');
    expect(fresh.authorization).toBe(`Bearer ${rotated.access_token}`);
    fresh.answer(200, {});
    expect(await readsAfter).toMatchObject({ status: 200 });
    expect(state().user).toBe(profile);
    expect(sentTo(ME)).toHaveLength(checksBefore);

    // And a 401 for the same session, even for its earlier token, still speaks for it.
    withOldToken.answer(401, { detail: { code: 'ACCOUNT_UNAUTHORIZED' } });
    expect(await sentEarlier).toBeInstanceOf(AxiosError);
    await settle();
    expect(state()).toMatchObject(SIGNED_OUT);
    expect(mockAuth.signOut).toHaveBeenCalledTimes(1);
  });

  it('a 401 for the current new session still ends it: locally first, quarantined, signed out, routed', async () => {
    await signInRegistered(session('account-a', 'session-a1'));
    const a2 = session('account-a', 'session-a2');
    supabaseEmits('SIGNED_IN', a2);
    await settle();
    signOutGate = mockDeferred<void>();
    const gate = signOutGate;

    const request = outcome(api.get(URL));
    await settle();
    const sentWithA2 = only(URL);
    expect(sentWithA2.authorization).toBe(`Bearer ${a2.access_token}`);
    sentWithA2.answer(401, { detail: { code: 'ACCOUNT_UNAUTHORIZED' } });
    await settle();

    // Supabase's sign-out has started and waits; locally it is already over.
    expect(mockAuth.signOut).toHaveBeenCalledTimes(1);
    expect(state()).toMatchObject(SIGNED_OUT);
    // A2 is quarantined: its own refresh cannot bring it back.
    supabaseEmits('TOKEN_REFRESHED', session('account-a', 'session-a2', 'rotated'));
    await settle();
    expect(state()).toMatchObject(SIGNED_OUT);

    gate.resolve();
    expect(await request).toBeInstanceOf(AxiosError);
    await settle();
    expect(router.replace).toHaveBeenCalledWith('/(auth)/welcome');
  });

  it.each([
    ['opaque sessions', null, null],
    ['readable sessions', 'session-a1', 'session-a2'],
  ])('an explicit re-login is not undone by a 401 for the earlier session answered after it (%s)', async (_kind, oldId, newId) => {
    await signInRegistered(session('account-a', oldId, 'old'), 'Alice');
    const request = outcome(api.get(URL));
    await settle();
    const sentWithOld = only(URL);

    const fresh = session('account-a', newId, 'relogin');
    mockAuth.signInWithPassword.mockImplementationOnce(async () => {
      supabaseEmits('SIGNED_IN', fresh);
      return { data: { session: fresh, user: fresh.user }, error: null };
    });
    const login = state().login('account-a@example.com', 'secret');
    await settle();
    only(ME).answer(200, registered('Alice'));
    await expect(login).resolves.toEqual({ ok: true });
    expect(state().session?.access_token).toBe(fresh.access_token);

    (router.replace as jest.Mock).mockClear();
    sentWithOld.answer(401, { detail: { code: 'ACCOUNT_UNAUTHORIZED' } });
    expect(await request).toBeInstanceOf(AxiosError);
    await settle();

    expect(mockAuth.signOut).not.toHaveBeenCalled();
    expect(router.replace).not.toHaveBeenCalled();
    expect(state()).toMatchObject({ userId: 'account-a', registrationState: 'registered' });
    expect(state().session?.access_token).toBe(fresh.access_token);
  });

  it.each([
    ['opaque sessions', null, null],
    ['readable sessions', 'session-a1', 'session-a2'],
  ])('nor by one answered while that sign-in is still in progress (%s)', async (_kind, oldId, newId) => {
    await signInRegistered(session('account-a', oldId, 'old'), 'Alice');
    const request = outcome(api.get(URL));
    await settle();
    const sentWithOld = only(URL);

    const fresh = session('account-a', newId, 'relogin');
    const signInGate = mockDeferred<void>();
    mockAuth.signInWithPassword.mockImplementationOnce(async () => {
      await signInGate.promise;
      supabaseEmits('SIGNED_IN', fresh);
      return { data: { session: fresh, user: fresh.user }, error: null };
    });
    const login = state().login('account-a@example.com', 'secret');
    await settle();

    // The old request's 401 lands while Supabase is still signing in.
    sentWithOld.answer(401, { detail: { code: 'ACCOUNT_UNAUTHORIZED' } });
    expect(await request).toBeInstanceOf(AxiosError);
    await settle();
    expect(mockAuth.signOut).not.toHaveBeenCalled();

    signInGate.resolve();
    await settle();
    only(ME).answer(200, registered('Alice'));
    await expect(login).resolves.toEqual({ ok: true });
    expect(state()).toMatchObject({ userId: 'account-a', registrationState: 'registered' });
    expect(state().session?.access_token).toBe(fresh.access_token);
    expect(mockAuth.signOut).not.toHaveBeenCalled();
  });
});

describe('a sign-in that establishes nothing', () => {
  it('leaves the current identity as it was, and an undecided one still gets its check', async () => {
    supabaseEmits('SIGNED_IN', session('account-a', 'session-a1'));
    await settle();
    const meBefore = only(ME);

    mockAuth.signInWithPassword.mockImplementationOnce(async () => ({
      data: { session: null, user: null },
      error: { message: 'Invalid login credentials' },
    }));
    await expect(state().login('account-a@example.com', 'wrong')).resolves.toMatchObject({
      ok: false,
      code: 'invalid_credentials',
    });
    await settle();

    // The check that was out when the sign-in began was set aside; a new one went out.
    const meAfter = sentTo(ME).find((request) => !request.answered && request !== meBefore);
    expect(meAfter?.authorization).toBe(`Bearer ${session('account-a', 'session-a1').access_token}`);
    meBefore.answer(200, registered('Stale'));
    await settle();
    expect(state()).toMatchObject({ userId: 'account-a', registrationState: 'resolving' });
    meAfter!.answer(200, registered('Alice'));
    await settle();
    expect(state()).toMatchObject({ userId: 'account-a', registrationState: 'registered' });
    expect(state().user?.name).toBe('Alice');
  });
});

describe('sign-out is held to the same rule', () => {
  it('a token refresh cannot undo a logout whose Supabase sign-out failed', async () => {
    await signInRegistered(session('account-a', 'session-a1'));
    signOutFails = true;

    await state().logout();
    expect(state()).toMatchObject(SIGNED_OUT);

    supabaseEmits('TOKEN_REFRESHED', session('account-a', 'session-a1', 'rotated'));
    await settle();
    expect(state()).toMatchObject(SIGNED_OUT);
  });
});

describe('same-account refresh with nothing rejected', () => {
  it('updates the token and keeps everything else, exactly as before', async () => {
    await signInRegistered(session('account-a', 'session-a1'), 'Alice');
    const profile = state().user;
    const meBefore = sentTo(ME).length;

    const rotated = session('account-a', 'session-a1', 'rotated');
    supabaseEmits('TOKEN_REFRESHED', rotated);
    await settle();

    expect(state().session?.access_token).toBe(rotated.access_token);
    expect(state().user).toBe(profile);
    expect(state().registrationState).toBe('registered');
    expect(sentTo(ME)).toHaveLength(meBefore);
  });
});

describe('the Supabase sign-out wrapper', () => {
  it('does not treat Supabase\'s returned { error } as a completed sign-out', async () => {
    signOutFails = true;
    await expect(supabaseSignOut()).rejects.toMatchObject({ message: 'Failed to fetch' });
  });

  it('resolves when Supabase really signed out', async () => {
    await expect(supabaseSignOut()).resolves.toBeUndefined();
  });
});
