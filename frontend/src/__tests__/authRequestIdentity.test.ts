/**
 * Request identity and registration-flow ownership, through the real client.
 *
 * The real user store, the real API client with both of its interceptors, the
 * real ``apiV2`` calls and the real scanner claim run here. Two edges are
 * replaced, and nothing else:
 *
 * - the network: every axios instance's transport is answered by hand, so a
 *   test sees exactly which requests went out, with which token;
 * - Supabase: an in-memory auth client whose ``getSession`` and ``signOut``
 *   can be paused. That is how the dispatch race is built step by step: an
 *   operation belongs to A, and the identity changes while its request is
 *   still reading its token.
 *
 * Two races are kept apart on purpose. In the response race a request really
 * went out as A and its answer arrives after B became current; the answer must
 * change nothing. In the dispatch race a request that belongs to A has not
 * been sent yet when B becomes current; an account-specific mutation must then
 * not be sent at all. Checking the response cannot fix the second: by then the
 * server has already acted as B.
 *
 * Every interleaving is built from deferred promises the test resolves by
 * hand. The only yielding is to the event loop; there are no timing sleeps.
 */
import { AxiosError, type InternalAxiosRequestConfig } from 'axios';
import type { Session } from '@supabase/supabase-js';

type Deferred<T> = {
  promise: Promise<T>;
  resolve: (value: T) => void;
};

function mockDeferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

// --- The network: every request is recorded and answered by the test --------
interface MockSent {
  method: string;
  url: string;
  authorization: string | undefined;
  deviceToken: string | undefined;
  body: unknown;
  answered: boolean;
  answer: (status: number, data?: unknown) => void;
}
const mockSent: MockSent[] = [];

function mockTransport(config: InternalAxiosRequestConfig): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const headers = config.headers ?? {};
    const entry: MockSent = {
      method: String(config.method ?? 'get').toUpperCase(),
      url: String(config.url ?? ''),
      authorization: headers.Authorization as string | undefined,
      deviceToken: headers['X-Device-Token'] as string | undefined,
      body: typeof config.data === 'string' ? JSON.parse(config.data) : config.data,
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

// Every axios instance (the account client and the scanner's device client)
// is created with the hand-answered transport.
jest.mock('axios', () => {
  const actual = jest.requireActual('axios');
  const realCreate = actual.create;
  const create = (config: Record<string, unknown> = {}) =>
    realCreate({ ...config, adapter: (request: InternalAxiosRequestConfig) => mockTransport(request) });
  actual.create = create;
  if (actual.default) actual.default.create = create;
  return actual;
});

// --- Supabase: the real ``services/supabase`` module over an in-memory client --
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
import { api } from '../services/api';
import { ensureDeviceClaimed } from '../services/productScan';
import { secureSessionStorage } from '../services/secureSessionStorage';
import { useUserStore } from '../store/userStore';
/* eslint-enable import/first */

const mockAuth = jest.requireMock('@supabase/supabase-js').__auth as Record<string, jest.Mock>;
const authCallback = mockAuth.onAuthStateChange.mock.calls[0][0] as (
  event: string,
  session: Session | null,
) => unknown;

const ME = '/api/v2/me';
const REGISTER = '/api/v2/access/register';
const RESERVE = '/api/v2/access/reserve';
const PROFILE = '/api/v2/profile';
const CLAIM = '/api/v2/scan/device/claim';
const DEVICE = '/api/v2/scan/device';
const CHALLENGE_KEY = '@glamgenius/registration_challenge_v2';
const DEVICE_KEY = 'glamgenius_scan_device_v1';

// What Supabase itself holds. ``getSession`` reads it after any pause, which is
// how "the identity changed before the token was read" is modelled.
let supabaseSession: Session | null = null;
const sessionGates: Deferred<void>[] = [];
const signOutGates: Deferred<void>[] = [];

mockAuth.getSession.mockImplementation(async () => {
  const gate = sessionGates.shift();
  if (gate) await gate.promise;
  return { data: { session: supabaseSession }, error: null };
});
mockAuth.signOut.mockImplementation(async () => {
  const gate = signOutGates.shift();
  if (gate) await gate.promise;
  supabaseSession = null;
  authCallback('SIGNED_OUT', null);
  return { error: null };
});

/** The next ``getSession`` call waits until the returned gate is opened. */
function pauseNextGetSession(): Deferred<void> {
  const gate = mockDeferred<void>();
  sessionGates.push(gate);
  return gate;
}

function pauseNextSignOut(): Deferred<void> {
  const gate = mockDeferred<void>();
  signOutGates.push(gate);
  return gate;
}

function session(id: string, email = `${id}@example.com`): Session {
  return {
    access_token: `token-${id}`,
    refresh_token: `refresh-${id}`,
    token_type: 'bearer',
    expires_in: 3600,
    expires_at: Math.floor(Date.now() / 1000) + 3600,
    user: { id, email, aud: 'authenticated', app_metadata: {}, user_metadata: {}, created_at: '2026-01-01T00:00:00Z' },
  } as unknown as Session;
}

/** Supabase signs ``id`` in and announces it, as it would. */
function signIn(id: string, email?: string): void {
  supabaseSession = session(id, email);
  authCallback('SIGNED_IN', supabaseSession);
}

const nextMacrotask = () => new Promise<void>((resolve) => setTimeout(resolve, 0));
async function settle() {
  for (let i = 0; i < 10; i += 1) await nextMacrotask();
}

const sentTo = (url: string) => mockSent.filter((request) => request.url === url);
const waitingOn = (url: string) => sentTo(url).filter((request) => !request.answered);
function only(url: string): MockSent {
  const waiting = waitingOn(url);
  expect(waiting).toHaveLength(1);
  return waiting[0];
}

const state = () => useUserStore.getState();
const registered = (name: string, isAdmin = false) => ({ profile: { name }, account: { is_admin: isAdmin } });
const REGISTRATION_REQUIRED = { detail: { code: 'REGISTRATION_REQUIRED' } };

async function signInRegistered(id: string, name = id, isAdmin = false) {
  signIn(id);
  await settle();
  only(ME).answer(200, registered(name, isAdmin));
  await settle();
  expect(state()).toMatchObject({ userId: id, registrationState: 'registered' });
}

async function signInPending(id: string) {
  signIn(id);
  await settle();
  only(ME).answer(403, REGISTRATION_REQUIRED);
  await settle();
  expect(state()).toMatchObject({ userId: id, registrationState: 'registration_pending' });
}

async function storeDevice(token: string) {
  await secureSessionStorage.setItem(DEVICE_KEY, JSON.stringify({ device_key: 'device-key-1', token }));
}

async function storedDevice(): Promise<{ token: string; claimed_for?: string } | null> {
  const raw = await secureSessionStorage.getItem(DEVICE_KEY);
  return raw ? JSON.parse(raw) : null;
}

beforeEach(async () => {
  supabaseSession = null;
  sessionGates.length = 0;
  signOutGates.length = 0;
  authCallback('SIGNED_OUT', null);
  await settle();
  mockSent.length = 0;
  jest.clearAllMocks();
  jest.spyOn(console, 'error').mockImplementation(() => undefined);
  jest.spyOn(console, 'warn').mockImplementation(() => undefined);
  useUserStore.setState({ pendingChallenge: null, loading: false });
});

// ===========================================================================
// 1. A registration flow holds authority over its own identity only
// ===========================================================================
describe('1. a registration flow cannot hold back another identity', () => {
  /** A is registration_pending and its finalisation is out, paused indefinitely. */
  async function pausedFinalisationForA(): Promise<{ finishA: Promise<unknown>; registerA: MockSent }> {
    await signInPending('account-a');
    useUserStore.setState({ pendingChallenge: 'challenge-a' });
    const finishA = state().finishPendingRegistration();
    await settle();
    const registerA = only(REGISTER);
    expect(registerA.authorization).toBe('Bearer token-account-a');
    return { finishA, registerA };
  }

  it('A. B\'s /me starts, as B, and resolves while A\'s finalisation is still paused', async () => {
    const { registerA } = await pausedFinalisationForA();

    signIn('account-b');
    await settle();
    const meB = only(ME);
    expect(meB.authorization).toBe('Bearer token-account-b');
    meB.answer(200, registered('Bob'));
    await settle();

    expect(registerA.answered).toBe(false);
    expect(state()).toMatchObject({ userId: 'account-b', registrationState: 'registered' });
    expect(state().user).toMatchObject({ id: 'account-b', name: 'Bob' });
  });

  it('B. B\'s own REGISTRATION_REQUIRED is not suppressed by A\'s flow: B is pending and routed', async () => {
    const { registerA } = await pausedFinalisationForA();
    (router.replace as jest.Mock).mockClear();

    signIn('account-b');
    await settle();
    only(ME).answer(403, REGISTRATION_REQUIRED);
    await settle();

    expect(registerA.answered).toBe(false);
    expect(state()).toMatchObject({ userId: 'account-b', registrationState: 'registration_pending' });
    expect(router.replace).toHaveBeenCalledWith('/(auth)/registration-incomplete');
  });

  it('C. A\'s finalisation, succeeding after B has settled, changes nothing about B', async () => {
    const { finishA, registerA } = await pausedFinalisationForA();
    await signInRegistered('account-b', 'Bob', true);
    const profileB = state().user;
    const requestsBefore = mockSent.length;
    (router.replace as jest.Mock).mockClear();

    registerA.answer(200, { account: { id: 'account-a', status: 'active', created_at: null }, invite_redeemed: true });
    await expect(finishA).resolves.toMatchObject({ ok: false });
    await settle();

    expect(state()).toMatchObject({ userId: 'account-b', registrationState: 'registered', isAdmin: true });
    expect(state().user).toBe(profileB);
    expect(mockSent.length).toBe(requestsBefore);
    expect(router.replace).not.toHaveBeenCalled();
  });

  it('C. nor can it spend or wipe the challenge B is holding for its own registration', async () => {
    const { finishA, registerA } = await pausedFinalisationForA();
    await signInPending('account-b');
    useUserStore.setState({ pendingChallenge: 'challenge-b' });
    await secureSessionStorage.setItem(CHALLENGE_KEY, 'challenge-b');

    registerA.answer(200, { account: { id: 'account-a', status: 'active', created_at: null }, invite_redeemed: true });
    await expect(finishA).resolves.toMatchObject({ ok: false });
    await settle();

    expect(state()).toMatchObject({ userId: 'account-b', registrationState: 'registration_pending' });
    expect(state().pendingChallenge).toBe('challenge-b');
    expect(await secureSessionStorage.getItem(CHALLENGE_KEY)).toBe('challenge-b');
  });

  it('C. a late failure of A\'s finalisation is not reported as B\'s expired reservation', async () => {
    // registration-incomplete signs the person out on reservation_expired, so
    // A's stale failure must not carry that verdict to B.
    const { finishA, registerA } = await pausedFinalisationForA();
    await signInPending('account-b');
    useUserStore.setState({ pendingChallenge: 'challenge-b' });
    await secureSessionStorage.setItem(CHALLENGE_KEY, 'challenge-b');

    registerA.answer(410, { detail: { code: 'reservation_expired' } });
    const result = await finishA;
    await settle();

    expect(result).toEqual({ ok: false, code: 'unknown' });
    expect(state().pendingChallenge).toBe('challenge-b');
    expect(await secureSessionStorage.getItem(CHALLENGE_KEY)).toBe('challenge-b');
    expect(state()).toMatchObject({ userId: 'account-b', registrationState: 'registration_pending' });
  });

  it('D. an immediate sign-up holds back its own /me until finalisation lands, whatever order timers run in', async () => {
    const signUpReturns = mockDeferred<void>();
    mockAuth.signUp.mockImplementationOnce(async () => {
      // Supabase announces the new account before signUp() returns, and here
      // it does not return until the test says so: the SIGNED_IN check's timer
      // fires first.
      signIn('account-new', 'new@example.com');
      await signUpReturns.promise;
      return { data: { session: supabaseSession, user: supabaseSession!.user }, error: null };
    });

    const register = state().reserveAndRegister('New', ' New@Example.com ', 'secret', 'INVITE1');
    await settle();
    only(RESERVE).answer(200, { challenge: 'challenge-new', expires_at: '2026-09-24T15:00:00Z' });
    await settle();

    expect(state().userId).toBe('account-new');
    expect(sentTo(ME)).toHaveLength(0);

    signUpReturns.resolve();
    await settle();
    const registerNew = only(REGISTER);
    expect(registerNew.authorization).toBe('Bearer token-account-new');
    expect(registerNew.body).toEqual({ registration_challenge: 'challenge-new' });
    expect(sentTo(ME)).toHaveLength(0);

    registerNew.answer(200, { account: { id: 'account-new', status: 'active', created_at: null }, invite_redeemed: true });
    await settle();
    only(ME).answer(200, registered('New'));
    await expect(register).resolves.toEqual({ ok: true });
    await settle();

    expect(sentTo(ME)).toHaveLength(1);
    expect(state()).toMatchObject({ userId: 'account-new', registrationState: 'registered' });
  });

  it('D. a sign-up still waiting for its account holds back nobody else', async () => {
    const register = state().reserveAndRegister('New', 'new@example.com', 'secret', 'INVITE1');
    await settle();
    const reserve = only(RESERVE);

    // An unrelated account signs in while the invite is being reserved.
    signIn('account-b');
    await settle();
    const meB = only(ME);
    expect(meB.authorization).toBe('Bearer token-account-b');
    meB.answer(403, REGISTRATION_REQUIRED);
    await settle();
    expect(state()).toMatchObject({ userId: 'account-b', registrationState: 'registration_pending' });

    reserve.answer(422, { detail: { code: 'invite_invalid' } });
    await expect(register).resolves.toMatchObject({ ok: false, code: 'invite_invalid' });
  });

  it('D. a sign-up never binds to an identity created for a different email', async () => {
    const signUpReturns = mockDeferred<void>();
    mockAuth.signUp.mockImplementationOnce(async () => {
      // Somebody else's SIGNED_IN lands while this sign-up is still out.
      signIn('account-other', 'other@example.com');
      await signUpReturns.promise;
      return { data: { session: null, user: null }, error: null };
    });

    const register = state().reserveAndRegister('New', 'new@example.com', 'secret', 'INVITE1');
    await settle();
    only(RESERVE).answer(200, { challenge: 'challenge-new', expires_at: '2026-09-24T15:00:00Z' });
    await settle();

    // While the sign-up is still out, the other identity's own check has
    // already gone out, as that identity.
    expect(only(ME).authorization).toBe('Bearer token-account-other');

    signUpReturns.resolve();
    await expect(register).resolves.toMatchObject({ needsEmailConfirmation: true });
  });

  it('E. a stale flow ending cannot clear the current identity\'s flow, and vice versa', async () => {
    const { finishA, registerA } = await pausedFinalisationForA();
    await signInPending('account-b');

    // B starts its own finalisation, also paused.
    useUserStore.setState({ pendingChallenge: 'challenge-b' });
    const finishB = state().finishPendingRegistration();
    await settle();
    const registerB = waitingOn(REGISTER).find((request) => request !== registerA)!;
    expect(registerB.authorization).toBe('Bearer token-account-b');

    // A's stale flow ends first. B's flow must still hold B: a
    // REGISTRATION_REQUIRED raced by B's finalisation cannot reroute B.
    registerA.answer(200, { account: { id: 'account-a', status: 'active', created_at: null }, invite_redeemed: true });
    await expect(finishA).resolves.toMatchObject({ ok: false });
    (router.replace as jest.Mock).mockClear();
    const racing = api.get('/api/v2/notifications');
    await settle();
    only('/api/v2/notifications').answer(403, REGISTRATION_REQUIRED);
    await expect(racing).rejects.toBeInstanceOf(AxiosError);
    expect(router.replace).not.toHaveBeenCalled();

    // B's flow completes on its own terms.
    registerB.answer(200, { account: { id: 'account-b', status: 'active', created_at: null }, invite_redeemed: true });
    await settle();
    only(ME).answer(200, registered('Bob'));
    await expect(finishB).resolves.toEqual({ ok: true });
    expect(state()).toMatchObject({ userId: 'account-b', registrationState: 'registered' });
  });

  it('E. the current identity\'s flow ending does not leave B held by A\'s stale flow', async () => {
    const { registerA } = await pausedFinalisationForA();
    await signInPending('account-b');
    useUserStore.setState({ pendingChallenge: 'challenge-b' });
    const finishB = state().finishPendingRegistration();
    await settle();

    const registerB = waitingOn(REGISTER).find((request) => request !== registerA)!;
    expect(registerB.authorization).toBe('Bearer token-account-b');
    registerB.answer(500, {});
    await expect(finishB).resolves.toMatchObject({ ok: false });
    await settle();

    // A's flow is still out, and has no say over B.
    expect(registerA.answered).toBe(false);
    (router.replace as jest.Mock).mockClear();
    const request = api.get('/api/v2/notifications');
    await settle();
    only('/api/v2/notifications').answer(403, REGISTRATION_REQUIRED);
    await expect(request).rejects.toBeInstanceOf(AxiosError);
    expect(router.replace).toHaveBeenCalledWith('/(auth)/registration-incomplete');
  });
});

// ===========================================================================
// 2. Account-specific mutations are bound before they are sent
// ===========================================================================
describe('2. the scanner\'s device claim is sent as its account or not at all', () => {
  it('5. the first claim goes out as the scanning account, with its device token', async () => {
    await signInRegistered('account-a');
    await storeDevice('device-1');

    const claim = ensureDeviceClaimed('account-a');
    await settle();
    const request = only(CLAIM);
    expect(request.authorization).toBe('Bearer token-account-a');
    expect(request.deviceToken).toBe('device-1');
    request.answer(200, { claimed: true, scans_attached: 2 });

    await expect(claim).resolves.toBe(true);
    expect((await storedDevice())?.claimed_for).toBe('account-a');
  });

  it('5. the first claim is not sent when the identity changes while it reads its token', async () => {
    await signInRegistered('account-a');
    await storeDevice('device-1');

    const gate = pauseNextGetSession();
    const claim = ensureDeviceClaimed('account-a');
    await settle();
    expect(sentTo(CLAIM)).toHaveLength(0);

    signIn('account-b');
    gate.resolve();
    await settle();

    expect(sentTo(CLAIM)).toHaveLength(0);
    await expect(claim).resolves.toBe(false);
    expect((await storedDevice())?.claimed_for).toBeUndefined();
  });

  it('6. the stale-device retry goes out as the same account, with the fresh device', async () => {
    await signInRegistered('account-a');
    await storeDevice('device-1');

    const claim = ensureDeviceClaimed('account-a');
    await settle();
    only(CLAIM).answer(401, { detail: { code: 'DEVICE_UNKNOWN' } });
    await settle();
    only(DEVICE).answer(200, { device_id: 'device-2-id', token: 'device-2' });
    await settle();

    const retry = only(CLAIM);
    expect(retry.authorization).toBe('Bearer token-account-a');
    expect(retry.deviceToken).toBe('device-2');
    retry.answer(200, { claimed: true, scans_attached: 0 });
    await expect(claim).resolves.toBe(true);
    expect(await storedDevice()).toMatchObject({ token: 'device-2', claimed_for: 'account-a' });
    // A stale device is not an account failure: nobody was signed out.
    expect(mockAuth.signOut).not.toHaveBeenCalled();
  });

  it('6. the stale-device retry is not sent as B when the identity changes before it goes out', async () => {
    await signInRegistered('account-a');
    await storeDevice('device-1');

    const claim = ensureDeviceClaimed('account-a');
    await settle();
    only(CLAIM).answer(401, { detail: { code: 'DEVICE_UNKNOWN' } });
    await settle();
    // The retry's token read is the next one; hold it, then let the device
    // re-register.
    const gate = pauseNextGetSession();
    only(DEVICE).answer(200, { device_id: 'device-2-id', token: 'device-2' });
    await settle();
    expect(sentTo(CLAIM)).toHaveLength(1);

    signIn('account-b');
    gate.resolve();
    await settle();

    const claims = sentTo(CLAIM);
    expect(claims).toHaveLength(1);
    expect(claims[0].authorization).toBe('Bearer token-account-a');
    await expect(claim).resolves.toBe(false);
    expect(await storedDevice()).toMatchObject({ token: 'device-2' });
    expect((await storedDevice())?.claimed_for).toBeUndefined();
  });

  it('7. a scanner still holding A cannot claim the phone while B is signed in', async () => {
    await signInRegistered('account-b', 'Bob');
    await storeDevice('device-1');

    // The scanner captured A's id before the switch.
    const claim = ensureDeviceClaimed('account-a');
    await settle();

    expect(sentTo(CLAIM)).toHaveLength(0);
    await expect(claim).resolves.toBe(false);
    expect((await storedDevice())?.claimed_for).toBeUndefined();
    expect(state()).toMatchObject({ userId: 'account-b', registrationState: 'registered' });
  });
});

describe('2. registration finalisation is sent as its own account or not at all', () => {
  it('8. A\'s finalisation is not dispatched as B when the identity changes before it is sent', async () => {
    await signInPending('account-a');
    useUserStore.setState({ pendingChallenge: 'challenge-a' });

    const gate = pauseNextGetSession();
    const finish = state().finishPendingRegistration();
    await settle();
    expect(sentTo(REGISTER)).toHaveLength(0);

    signIn('account-b');
    gate.resolve();
    await settle();

    expect(sentTo(REGISTER)).toHaveLength(0);
    expect((await finish).ok).toBe(false);

    // B is untouched: it gets its own check, as B, and its own answer.
    const meB = only(ME);
    expect(meB.authorization).toBe('Bearer token-account-b');
    meB.answer(403, REGISTRATION_REQUIRED);
    await settle();
    expect(state()).toMatchObject({ userId: 'account-b', registrationState: 'registration_pending' });
  });

  it('8. with no signed-in account, finalisation is refused before anything is sent', async () => {
    useUserStore.setState({ pendingChallenge: 'challenge-orphan' });

    const finish = state().finishPendingRegistration();
    await settle();

    expect(sentTo(REGISTER)).toHaveLength(0);
    expect((await finish).ok).toBe(false);
    expect(state().registrationState).toBe('signed_out');
  });

  it('8. with the owner still signed in, it goes out as the owner', async () => {
    await signInPending('account-a');
    useUserStore.setState({ pendingChallenge: 'challenge-a' });

    const finish = state().finishPendingRegistration();
    await settle();
    const request = only(REGISTER);
    expect(request.authorization).toBe('Bearer token-account-a');
    request.answer(200, { account: { id: 'account-a', status: 'active', created_at: null }, invite_redeemed: true });
    await settle();
    only(ME).answer(200, registered('Alice'));
    await expect(finish).resolves.toEqual({ ok: true });
  });
});

describe('2. a profile change is sent as its own account or not at all', () => {
  it('9. A\'s PATCH is never sent as B when the identity changes before it is sent', async () => {
    await signInRegistered('account-a', 'Alice');

    const gate = pauseNextGetSession();
    const update = state().updateUser({ skin_type: 'dry' });
    await settle();
    expect(sentTo(PROFILE)).toHaveLength(0);

    signIn('account-b');
    gate.resolve();
    await settle();

    expect(sentTo(PROFILE)).toHaveLength(0);
    await update;
    // B's own check goes out as B, and B's profile is B's own.
    only(ME).answer(200, registered('Bob'));
    await settle();
    expect(state().user).toMatchObject({ id: 'account-b', name: 'Bob' });
    expect(state().user?.skin_type).toBeUndefined();
  });

  it('9. with A still signed in, the PATCH goes out as A with A\'s attributes', async () => {
    await signInRegistered('account-a', 'Alice');

    const update = state().updateUser({ skin_type: 'dry' });
    await settle();
    const request = only(PROFILE);
    expect(request.method).toBe('PATCH');
    expect(request.authorization).toBe('Bearer token-account-a');
    expect(request.body).toEqual({ attributes: [{ key: 'skin_type', value: 'dry' }] });
    request.answer(200, {});
    await settle();
    only(ME).answer(200, { profile: { name: 'Alice', skin_type: 'dry' }, account: { is_admin: false } });
    await update;
    expect(state().user).toMatchObject({ id: 'account-a', skin_type: 'dry' });
  });
});

// ===========================================================================
// 3. An accepted 401 ends the session locally before Supabase signs out
// ===========================================================================
describe('3. the 401 boundary', () => {
  it('10. a current 401 invalidates at once; an old /me landing during a paused signOut restores nothing', async () => {
    signIn('account-a');
    await settle();
    const meA = only(ME);

    let stateWhenSignOutStarted: string | null = null;
    const signOutGate = pauseNextSignOut();
    mockAuth.signOut.mockImplementationOnce(async () => {
      stateWhenSignOutStarted = state().registrationState;
      await signOutGate.promise;
      supabaseSession = null;
      authCallback('SIGNED_OUT', null);
      return { error: null };
    });

    const other = api.get('/api/v2/notifications');
    await settle();
    only('/api/v2/notifications').answer(401, { detail: { code: 'ACCOUNT_UNAUTHORIZED' } });
    await settle();

    // Supabase's sign-out has started and is paused; locally it is already over.
    expect(mockAuth.signOut).toHaveBeenCalledTimes(1);
    expect(stateWhenSignOutStarted).toBe('signed_out');
    expect(state()).toMatchObject({
      session: null,
      user: null,
      userId: '',
      registrationState: 'signed_out',
      isAdmin: false,
    });

    meA.answer(200, registered('Alice', true));
    await settle();
    expect(state()).toMatchObject({
      session: null,
      user: null,
      userId: '',
      registrationState: 'signed_out',
      isAdmin: false,
    });
    expect(router.replace).not.toHaveBeenCalledWith('/(auth)/welcome');

    signOutGate.resolve();
    await expect(other).rejects.toBeInstanceOf(AxiosError);
    await settle();
    expect(router.replace).toHaveBeenCalledWith('/(auth)/welcome');
    expect(state().registrationState).toBe('signed_out');
    expect(mockAuth.signOut).toHaveBeenCalledTimes(1);
  });

  it('11. A\'s 401 arriving after B became current neither clears B nor signs anyone out', async () => {
    await signInRegistered('account-a', 'Alice');
    const requestA = api.get('/api/v2/notifications');
    await settle();
    const sentAsA = only('/api/v2/notifications');
    expect(sentAsA.authorization).toBe('Bearer token-account-a');

    await signInRegistered('account-b', 'Bob');
    (router.replace as jest.Mock).mockClear();
    sentAsA.answer(401, { detail: { code: 'ACCOUNT_UNAUTHORIZED' } });
    await expect(requestA).rejects.toBeInstanceOf(AxiosError);
    await settle();

    expect(mockAuth.signOut).not.toHaveBeenCalled();
    expect(router.replace).not.toHaveBeenCalled();
    expect(state()).toMatchObject({ userId: 'account-b', registrationState: 'registered' });
    expect(state().session?.user.id).toBe('account-b');
    expect(state().user).toMatchObject({ id: 'account-b', name: 'Bob' });
  });
});

// ===========================================================================
// 4. The response race, kept apart from the dispatch race above
// ===========================================================================
describe('4. the response race: a request that really went out as A answers after B', () => {
  it('a /me that went out as A cannot apply to B', async () => {
    signIn('account-a');
    await settle();
    const meA = only(ME);
    expect(meA.authorization).toBe('Bearer token-account-a');

    signIn('account-b');
    await settle();
    const meB = waitingOn(ME).find((request) => request !== meA)!;
    expect(meB.authorization).toBe('Bearer token-account-b');

    meA.answer(200, registered('Alice', true));
    await settle();
    expect(state()).toMatchObject({ userId: 'account-b', registrationState: 'resolving', isAdmin: false });
    expect(state().user?.name).toBeUndefined();

    meB.answer(200, registered('Bob'));
    await settle();
    expect(state().user).toMatchObject({ id: 'account-b', name: 'Bob' });
    expect(state().isAdmin).toBe(false);
  });

  it('a REGISTRATION_REQUIRED that answered A neither marks nor routes B', async () => {
    await signInRegistered('account-a', 'Alice');
    const requestA = api.get('/api/v2/notifications');
    await settle();
    const sentAsA = only('/api/v2/notifications');

    await signInRegistered('account-b', 'Bob');
    (router.replace as jest.Mock).mockClear();
    sentAsA.answer(403, REGISTRATION_REQUIRED);
    await expect(requestA).rejects.toBeInstanceOf(AxiosError);
    await settle();

    expect(router.replace).not.toHaveBeenCalled();
    expect(state()).toMatchObject({ userId: 'account-b', registrationState: 'registered' });
  });
});
