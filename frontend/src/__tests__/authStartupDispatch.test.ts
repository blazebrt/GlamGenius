/**
 * Requests made at start-up, before the app has decided who is signed in.
 *
 * Supabase reads the stored session asynchronously, so for a moment after
 * launch the store has made no decision at all (auth generation 0). A request
 * made in that moment has no identity to keep yet. It belongs to the first
 * identity the app settles on, signed in or signed out, and to no later one.
 *
 * Every case loads fresh modules, so it really starts before any decision.
 * The real API client, store and ``services/supabase`` run here. The network
 * is answered by hand, and Supabase is an in-memory client whose
 * ``getSession`` can be paused. There are no timing sleeps.
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
    signOut: jest.fn(async () => ({ error: null })),
    signUp: jest.fn(),
    signInWithPassword: jest.fn(),
  };
  return { createClient: jest.fn(() => ({ auth })), __auth: auth };
});

const URL = '/api/v2/notifications';

function session(id: string): Session {
  return {
    access_token: `token-${id}`,
    refresh_token: `refresh-${id}`,
    token_type: 'bearer',
    expires_in: 3600,
    expires_at: Math.floor(Date.now() / 1000) + 3600,
    user: { id, email: `${id}@example.com`, aud: 'authenticated', app_metadata: {}, user_metadata: {}, created_at: '2026-01-01T00:00:00Z' },
  } as unknown as Session;
}

const nextMacrotask = () => new Promise<void>((resolve) => setTimeout(resolve, 0));
async function settle() {
  for (let i = 0; i < 10; i += 1) await nextMacrotask();
}

function outcome<T>(request: Promise<T>): Promise<T | unknown> {
  return request.then((value) => value, (error: unknown) => error);
}

/**
 * A freshly launched app: new modules, no decision yet. Supabase holds
 * ``stored`` and hands it out from ``getSession``, after ``gate`` if one is set.
 */
function launch(stored: Session | null) {
  jest.resetModules();
  mockSent.length = 0;
  const state = { session: stored, gate: null as Deferred<void> | null };
  /* eslint-disable @typescript-eslint/no-require-imports */
  const { api, AccountMismatchError } = require('../services/api');
  const { useUserStore } = require('../store/userStore');
  /* eslint-enable @typescript-eslint/no-require-imports */
  const auth = jest.requireMock('@supabase/supabase-js').__auth as Record<string, jest.Mock>;
  auth.getSession.mockImplementation(async () => {
    const gate = state.gate;
    state.gate = null;
    if (gate) await gate.promise;
    return { data: { session: state.session }, error: null };
  });
  const authCallback = auth.onAuthStateChange.mock.calls[0][0] as (event: string, s: Session | null) => void;
  return { api, AccountMismatchError, useUserStore, authCallback, state };
}

beforeEach(() => {
  jest.spyOn(console, 'error').mockImplementation(() => undefined);
  jest.spyOn(console, 'warn').mockImplementation(() => undefined);
});

describe('a request made before the app has decided who is signed in', () => {
  it('goes out as the stored account the app then adopts', async () => {
    const app = launch(session('account-a'));
    app.state.gate = mockDeferred<void>();
    const gate = app.state.gate;
    const request = outcome(app.api.get(URL));
    await settle();

    // Supabase announces the stored session: the app's first decision.
    app.authCallback('INITIAL_SESSION', session('account-a'));
    gate.resolve();
    await settle();

    const sent = mockSent.filter((r) => r.url === URL);
    expect(sent).toHaveLength(1);
    expect(sent[0].authorization).toBe('Bearer token-account-a');
    sent[0].answer(200, {});
    expect(await request).toMatchObject({ status: 200 });
  });

  it('goes out with the stored session\'s token while nothing has been decided yet, as before', async () => {
    const app = launch(session('account-a'));
    const request = outcome(app.api.get(URL));
    await settle();

    const sent = mockSent.filter((r) => r.url === URL);
    expect(sent).toHaveLength(1);
    expect(sent[0].authorization).toBe('Bearer token-account-a');
    sent[0].answer(200, {});
    expect(await request).toMatchObject({ status: 200 });
  });

  it('stays anonymous when the first decision is "signed out", even if a session then appears', async () => {
    const app = launch(null);
    app.state.gate = mockDeferred<void>();
    const gate = app.state.gate;
    const request = outcome(app.api.get(URL));
    await settle();

    app.authCallback('INITIAL_SESSION', null);
    expect(app.useUserStore.getState().registrationState).toBe('signed_out');
    // B's session reaches Supabase before this request reads its token.
    app.state.session = session('account-b');
    gate.resolve();
    await settle();

    const sent = mockSent.filter((r) => r.url === URL);
    expect(sent).toHaveLength(1);
    expect(sent[0].authorization).toBeUndefined();
    sent[0].answer(200, {});
    expect(await request).toMatchObject({ status: 200 });
  });

  it('is refused when a second identity has taken over before it is sent', async () => {
    const app = launch(session('account-a'));
    app.state.gate = mockDeferred<void>();
    const gate = app.state.gate;
    const request = outcome(app.api.get(URL));
    await settle();

    app.authCallback('INITIAL_SESSION', session('account-a'));
    app.state.session = session('account-b');
    app.authCallback('SIGNED_IN', session('account-b'));
    gate.resolve();
    await settle();

    expect(mockSent.filter((r) => r.url === URL)).toHaveLength(0);
    expect(await request).toBeInstanceOf(app.AccountMismatchError);
  });
});
