/**
 * Auth session integrity: no asynchronous result may outlive the identity
 * that started it.
 *
 * Every race here is built from deferred promises the test resolves by hand,
 * so the interleaving is the one written down, not whatever the scheduler
 * happened to do. The only yielding is to the event loop (one macrotask),
 * which is how the store runs its post-callback reconciliation. There are no
 * timing sleeps.
 *
 * The auth callback is driven through the subscriber the store registered
 * with ``supabase.auth.onAuthStateChange``, exactly as Supabase would call it.
 */
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

// --- The backend --------------------------------------------------------------
const mockMeCalls: Deferred<unknown>[] = [];
const mockFinalizeCalls: Deferred<unknown>[] = [];

jest.mock('../services/apiV2', () => ({
  getMe: jest.fn(() => {
    const call = mockDeferred<unknown>();
    mockMeCalls.push(call);
    return call.promise;
  }),
  finalizeRegistration: jest.fn(() => {
    const call = mockDeferred<unknown>();
    mockFinalizeCalls.push(call);
    return call.promise;
  }),
  reserveInvite: jest.fn(async () => ({ challenge: 'challenge-1' })),
  claimScanDevice: jest.fn(async () => ({ claimed: true, scans_attached: 1 })),
  patchAppearanceProfile: jest.fn(async () => ({})),
}));

// --- The API client boundary: the store registers its handlers here ----------
jest.mock('../services/api', () => ({
  isRegistrationRequired: (err: any) =>
    err?.response?.status === 403 && err?.response?.data?.detail?.code === 'REGISTRATION_REQUIRED',
  setUnauthorizedHandler: jest.fn(),
  setRegistrationRequiredHandler: jest.fn(),
  setAuthResponseAuthority: jest.fn(),
}));

// --- Supabase ------------------------------------------------------------------
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
  tokenToClaimFor: jest.fn(async () => 'device-token'),
  markDeviceClaimed: jest.fn(async () => undefined),
}));

/* eslint-disable import/first */
import { useUserStore } from '../store/userStore';
import { claimScanDevice, finalizeRegistration, getMe } from '../services/apiV2';
import { tokenToClaimFor, markDeviceClaimed } from '../services/productScan';
import { supabase } from '../services/supabase';
import {
  setAuthResponseAuthority,
  setRegistrationRequiredHandler,
  setUnauthorizedHandler,
} from '../services/api';
/* eslint-enable import/first */

const authCallback = (supabase.auth.onAuthStateChange as jest.Mock).mock.calls[0][0] as (
  event: string,
  session: Session | null,
) => unknown;
const unauthorized = (setUnauthorizedHandler as jest.Mock).mock.calls[0][0] as () => void;
const registrationRequired = (setRegistrationRequiredHandler as jest.Mock).mock.calls[0][0] as (
  stamp: unknown,
) => void;
// Optional so the behavioural cases above it still run against a store that
// registers no authority (the pre-fix store did not).
const responseAuthority = (setAuthResponseAuthority as jest.Mock).mock.calls[0]?.[0] as {
  stamp: () => unknown;
  acceptsUnauthorized: (stamp: unknown) => boolean;
  acceptsRegistrationRequired: (stamp: unknown) => boolean;
};

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

/** One trip round the event loop: the store's post-callback reconciliation runs here. */
const nextMacrotask = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

/** Let every promise chain started so far run to completion. */
async function settle() {
  for (let i = 0; i < 5; i += 1) await nextMacrotask();
}

const registered = (name: string, isAdmin = false) => ({
  profile: { name },
  account: { is_admin: isAdmin },
});
const registrationRequiredError = () => ({
  response: { status: 403, data: { detail: { code: 'REGISTRATION_REQUIRED' } } },
});

const state = () => useUserStore.getState();

beforeEach(async () => {
  // Start every case signed out, in a fresh generation of its own.
  authCallback('SIGNED_OUT', null);
  await settle();
  mockMeCalls.length = 0;
  mockFinalizeCalls.length = 0;
  jest.clearAllMocks();
  useUserStore.setState({ loading: false, pendingChallenge: null });
});

// ---------------------------------------------------------------------------
// A. Logout beats an old hydration
// ---------------------------------------------------------------------------
describe('A. logout beats old hydration', () => {
  it('leaves the store signed out when a /me started before logout resolves afterwards', async () => {
    authCallback('SIGNED_IN', session('account-a'));
    await nextMacrotask();
    expect(getMe).toHaveBeenCalledTimes(1);
    const meA = mockMeCalls[0];

    await state().logout();
    meA.resolve(registered('Alice', true));
    await settle();

    expect(state().session).toBeNull();
    expect(state().user).toBeNull();
    expect(state().userId).toBe('');
    expect(state().registrationState).toBe('signed_out');
    expect(state().isAdmin).toBe(false);
    expect(tokenToClaimFor).not.toHaveBeenCalled();
    expect(claimScanDevice).not.toHaveBeenCalled();
    expect(markDeviceClaimed).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// B. Account B beats a stale account A
// ---------------------------------------------------------------------------
describe('B. account B beats stale account A', () => {
  it('clears A the moment B arrives, and never combines B with A', async () => {
    authCallback('SIGNED_IN', session('account-a'));
    await nextMacrotask();
    mockMeCalls[0].resolve(registered('Alice', true));
    await settle();
    expect(state().user?.name).toBe('Alice');
    expect(state().isAdmin).toBe(true);

    // Account A refreshes its profile again, and that request is out when B signs in.
    void state().hydrateRegistration();
    await nextMacrotask();
    const staleA = mockMeCalls[mockMeCalls.length - 1];

    authCallback('SIGNED_IN', session('account-b'));
    // Synchronously, before any network answer for B: nothing of A remains.
    expect(state().userId).toBe('account-b');
    expect(state().session?.user.id).toBe('account-b');
    expect(state().user?.id).toBe('account-b');
    expect(state().user?.name).toBeUndefined();
    expect(state().isAdmin).toBe(false);
    expect(state().registrationState).toBe('resolving');

    await nextMacrotask();
    const meB = mockMeCalls[mockMeCalls.length - 1];
    expect(meB).not.toBe(staleA);

    staleA.resolve(registered('Alice', true));
    await settle();
    expect(state().user?.name).toBeUndefined();
    expect(state().isAdmin).toBe(false);
    expect(state().registrationState).toBe('resolving');

    meB.resolve(registered('Bob'));
    await settle();
    expect(state().userId).toBe('account-b');
    expect(state().user).toMatchObject({ id: 'account-b', name: 'Bob' });
    expect(state().registrationState).toBe('registered');
    expect(state().isAdmin).toBe(false);
  });

  it('keeps B when B answers first and the stale A answer lands afterwards', async () => {
    authCallback('SIGNED_IN', session('account-a'));
    await nextMacrotask();
    const meA = mockMeCalls[0];

    authCallback('SIGNED_IN', session('account-b'));
    await nextMacrotask();
    const meB = mockMeCalls[1];

    meB.resolve(registered('Bob'));
    await settle();
    meA.resolve(registered('Alice', true));
    await settle();

    expect(state().user).toMatchObject({ id: 'account-b', name: 'Bob' });
    expect(state().isAdmin).toBe(false);
    expect(state().registrationState).toBe('registered');
  });

  it('never lets a stale REGISTRATION_REQUIRED for A mark B as unregistered', async () => {
    authCallback('SIGNED_IN', session('account-a'));
    await nextMacrotask();
    const meA = mockMeCalls[0];

    authCallback('SIGNED_IN', session('account-b'));
    await nextMacrotask();
    mockMeCalls[1].resolve(registered('Bob'));
    await settle();

    meA.reject(registrationRequiredError());
    await settle();
    expect(state().registrationState).toBe('registered');
    expect(state().user?.name).toBe('Bob');
  });
});

// ---------------------------------------------------------------------------
// C. Unauthorized beats hydration
// ---------------------------------------------------------------------------
describe('C. unauthorized beats hydration', () => {
  it('a /me that started before a 401 reset cannot restore the session', async () => {
    authCallback('SIGNED_IN', session('account-a'));
    await nextMacrotask();
    const meA = mockMeCalls[0];

    unauthorized();
    expect(state().registrationState).toBe('signed_out');

    meA.resolve(registered('Alice', true));
    await settle();

    expect(state().session).toBeNull();
    expect(state().user).toBeNull();
    expect(state().userId).toBe('');
    expect(state().registrationState).toBe('signed_out');
    expect(state().isAdmin).toBe(false);
    expect(tokenToClaimFor).not.toHaveBeenCalled();
    expect(claimScanDevice).not.toHaveBeenCalled();
  });

  it('a fetchUser that started before a 401 reset cannot restore the profile', async () => {
    authCallback('SIGNED_IN', session('account-a'));
    await nextMacrotask();
    mockMeCalls[0].resolve(registered('Alice'));
    await settle();

    const refresh = state().fetchUser();
    const pending = mockMeCalls[mockMeCalls.length - 1];
    unauthorized();
    pending.resolve(registered('Alice', true));
    await refresh;
    await settle();

    expect(state().user).toBeNull();
    expect(state().registrationState).toBe('signed_out');
    expect(state().isAdmin).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// E. No cross-account device claim
// ---------------------------------------------------------------------------
describe('E. the device claim belongs to the account that was accepted', () => {
  it('starts no claim for stale A and claims for B only, bound to B', async () => {
    authCallback('SIGNED_IN', session('account-a'));
    await nextMacrotask();
    const meA = mockMeCalls[0];
    authCallback('SIGNED_IN', session('account-b'));
    await nextMacrotask();
    const meB = mockMeCalls[1];

    meA.resolve(registered('Alice'));
    await settle();
    expect(tokenToClaimFor).not.toHaveBeenCalled();
    expect(claimScanDevice).not.toHaveBeenCalled();

    meB.resolve(registered('Bob'));
    await settle();
    expect(tokenToClaimFor).toHaveBeenCalledTimes(1);
    expect(tokenToClaimFor).toHaveBeenCalledWith('account-b');
    expect(claimScanDevice).toHaveBeenCalledTimes(1);
    expect(claimScanDevice).toHaveBeenCalledWith('device-token', { expectedAccountId: 'account-b' });
    expect(markDeviceClaimed).toHaveBeenCalledWith('account-b');
  });

  it('abandons a claim whose account signed out before the claim request', async () => {
    const token = mockDeferred<string | null>();
    (tokenToClaimFor as jest.Mock).mockImplementationOnce(() => token.promise);
    authCallback('SIGNED_IN', session('account-a'));
    await nextMacrotask();
    mockMeCalls[0].resolve(registered('Alice'));
    await settle();
    expect(tokenToClaimFor).toHaveBeenCalledWith('account-a');

    await state().logout();
    token.resolve('device-token');
    await settle();
    expect(claimScanDevice).not.toHaveBeenCalled();
    expect(markDeviceClaimed).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// The auth callback boundary and token refresh
// ---------------------------------------------------------------------------
describe('the Supabase auth callback', () => {
  it('returns synchronously and starts no network work before it has returned', () => {
    const result = authCallback('SIGNED_IN', session('account-a'));
    expect(result).toBeUndefined();
    expect(getMe).not.toHaveBeenCalled();
    expect(supabase.auth.getSession).not.toHaveBeenCalled();
    expect(tokenToClaimFor).not.toHaveBeenCalled();
  });

  it('marks a new identity as resolving, neither registered nor pending', () => {
    authCallback('SIGNED_IN', session('account-a'));
    expect(state().registrationState).toBe('resolving');
  });

  it('a same-account TOKEN_REFRESHED updates the token and keeps the profile', async () => {
    authCallback('SIGNED_IN', session('account-a'));
    await nextMacrotask();
    mockMeCalls[0].resolve(registered('Alice', true));
    await settle();
    const profile = state().user;

    authCallback('TOKEN_REFRESHED', session('account-a', 'token-rotated'));
    expect(state().session?.access_token).toBe('token-rotated');
    await settle();

    expect(state().user).toBe(profile);
    expect(state().isAdmin).toBe(true);
    expect(state().registrationState).toBe('registered');
    expect(getMe).toHaveBeenCalledTimes(1);
  });

  it('a same-account TOKEN_REFRESHED does not discard a check already in flight', async () => {
    authCallback('SIGNED_IN', session('account-a'));
    await nextMacrotask();
    authCallback('TOKEN_REFRESHED', session('account-a', 'token-rotated'));
    await nextMacrotask();
    expect(getMe).toHaveBeenCalledTimes(1);

    mockMeCalls[0].resolve(registered('Alice'));
    await settle();
    expect(state().registrationState).toBe('registered');
    expect(state().user?.name).toBe('Alice');
  });

  it('coalesces repeated events for one identity into one /me', async () => {
    authCallback('SIGNED_IN', session('account-a'));
    authCallback('INITIAL_SESSION', session('account-a'));
    authCallback('TOKEN_REFRESHED', session('account-a', 'token-rotated'));
    await settle();
    expect(getMe).toHaveBeenCalledTimes(1);
  });

  it('SIGNED_OUT clears everything, including the admin flag', async () => {
    authCallback('SIGNED_IN', session('account-a'));
    await nextMacrotask();
    mockMeCalls[0].resolve(registered('Alice', true));
    await settle();

    authCallback('SIGNED_OUT', null);
    expect(state()).toMatchObject({
      session: null,
      user: null,
      userId: '',
      registrationState: 'signed_out',
      isAdmin: false,
    });
  });
});

// ---------------------------------------------------------------------------
// Password login and the auth event it causes
// ---------------------------------------------------------------------------
describe('password login', () => {
  it('shares one /me with the SIGNED_IN event it causes and resolves registered', async () => {
    (supabase.auth.signInWithPassword as jest.Mock).mockImplementationOnce(async () => {
      authCallback('SIGNED_IN', session('account-a'));
      return { data: { session: session('account-a'), user: session('account-a').user }, error: null };
    });

    const login = state().login('a@example.com', 'secret');
    await settle();
    expect(getMe).toHaveBeenCalledTimes(1);
    mockMeCalls[0].resolve(registered('Alice'));

    await expect(login).resolves.toEqual({ ok: true });
    await settle();
    expect(getMe).toHaveBeenCalledTimes(1);
    expect(state().registrationState).toBe('registered');
  });

  it('reports registration required for an identity with no GlamGenius account', async () => {
    (supabase.auth.signInWithPassword as jest.Mock).mockImplementationOnce(async () => ({
      data: { session: session('account-a'), user: session('account-a').user },
      error: null,
    }));
    const login = state().login('a@example.com', 'secret');
    await settle();
    mockMeCalls[0].reject(registrationRequiredError());
    const result = await login;
    expect(result.ok).toBe(false);
    expect(result.code).toBe('invite_required');
    expect(state().registrationState).toBe('registration_pending');
  });

  it('is not reported as successful when logout happened during the check', async () => {
    (supabase.auth.signInWithPassword as jest.Mock).mockImplementationOnce(async () => ({
      data: { session: session('account-a'), user: session('account-a').user },
      error: null,
    }));
    const login = state().login('a@example.com', 'secret');
    await settle();
    await state().logout();
    mockMeCalls[0].resolve(registered('Alice'));
    const result = await login;
    expect(result.ok).toBe(false);
    expect(state().registrationState).toBe('signed_out');
  });
});

// ---------------------------------------------------------------------------
// Registration finalisation owns its account's state
// ---------------------------------------------------------------------------
describe('registration finalisation', () => {
  it('holds back the SIGNED_IN check while sign-up finalises, then lands registered', async () => {
    (supabase.auth.signUp as jest.Mock).mockImplementationOnce(async () => {
      authCallback('SIGNED_IN', session('account-new'));
      return { data: { session: session('account-new'), user: session('account-new').user }, error: null };
    });

    const register = state().reserveAndRegister('New', 'new@example.com', 'secret', 'INVITE1');
    await settle();
    // The event's scheduled check has fired, and must not have asked /me yet.
    expect(finalizeRegistration).toHaveBeenCalledTimes(1);
    expect(getMe).not.toHaveBeenCalled();
    // A REGISTRATION_REQUIRED from a request that raced the finalisation
    // cannot move this person off the path to onboarding.
    expect(responseAuthority.acceptsRegistrationRequired(responseAuthority.stamp())).toBe(false);

    mockFinalizeCalls[0].resolve({});
    await settle();
    mockMeCalls[0].resolve(registered('New'));
    await expect(register).resolves.toEqual({ ok: true });
    expect(state().registrationState).toBe('registered');
  });

  it('an older REGISTRATION_REQUIRED cannot overwrite a completed finalisation', async () => {
    authCallback('SIGNED_IN', session('account-a'));
    await nextMacrotask();
    mockMeCalls[0].reject(registrationRequiredError());
    await settle();
    expect(state().registrationState).toBe('registration_pending');

    // A product request is sent (and stamped) before the finalisation.
    const olderStamp = responseAuthority.stamp();
    useUserStore.setState({ pendingChallenge: 'challenge-1' });
    const finish = state().finishPendingRegistration();
    await settle();
    mockFinalizeCalls[0].resolve({});
    await settle();
    mockMeCalls[mockMeCalls.length - 1].resolve(registered('Alice'));
    await expect(finish).resolves.toEqual({ ok: true });

    // Its REGISTRATION_REQUIRED arrives only now.
    expect(responseAuthority.acceptsRegistrationRequired(olderStamp)).toBe(false);
    registrationRequired(olderStamp);
    expect(state().registrationState).toBe('registered');
  });

  it('does not report success, or register anyone, when the identity changed while finalising', async () => {
    authCallback('SIGNED_IN', session('account-a'));
    await nextMacrotask();
    mockMeCalls[0].reject(registrationRequiredError());
    await settle();
    useUserStore.setState({ pendingChallenge: 'challenge-1' });

    const finish = state().finishPendingRegistration();
    await settle();
    authCallback('SIGNED_IN', session('account-b'));
    mockFinalizeCalls[0].resolve({});
    const result = await finish;

    expect(result.ok).toBe(false);
    expect(state().userId).toBe('account-b');
    expect(state().registrationState).not.toBe('registered');
  });
});

// ---------------------------------------------------------------------------
// The response authority the API client consults
// ---------------------------------------------------------------------------
describe('the response authority', () => {
  it('lets only the current identity\'s 401 end the session', async () => {
    authCallback('SIGNED_IN', session('account-a'));
    const stampA = responseAuthority.stamp();
    authCallback('SIGNED_IN', session('account-b'));
    const stampB = responseAuthority.stamp();

    expect(responseAuthority.acceptsUnauthorized(stampA)).toBe(false);
    expect(responseAuthority.acceptsUnauthorized(stampB)).toBe(true);
    expect(responseAuthority.acceptsRegistrationRequired(stampA)).toBe(false);
    expect(responseAuthority.acceptsRegistrationRequired(stampB)).toBe(true);
  });

  it('ignores a REGISTRATION_REQUIRED stamped for an account that has signed out', async () => {
    authCallback('SIGNED_IN', session('account-a'));
    await nextMacrotask();
    mockMeCalls[0].resolve(registered('Alice'));
    await settle();
    const stampA = responseAuthority.stamp();
    await state().logout();

    registrationRequired(stampA);
    expect(state().registrationState).toBe('signed_out');
  });
});
