/**
 * The API client's side of the auth generation: a response about an identity
 * that is no longer signed in changes nothing, and an account-bound request is
 * sent as that account or not at all.
 *
 * The real API client and axios run here; only the transport is replaced,
 * with an adapter the test answers by hand.
 */
import { AxiosError, type InternalAxiosRequestConfig } from 'axios';

jest.mock('../services/supabase', () => ({
  getAccessToken: jest.fn(async () => 'token-current'),
  getAccessIdentity: jest.fn(async () => ({ token: 'token-b', userId: 'account-b' })),
  signOut: jest.fn(async () => undefined),
}));

/* eslint-disable import/first */
import {
  AccountMismatchError,
  api,
  setAuthResponseAuthority,
  setRegistrationRequiredHandler,
  setUnauthorizedHandler,
} from '../services/api';
import { claimScanDevice } from '../services/apiV2';
import { router } from 'expo-router';
import { signOut } from '../services/supabase';
/* eslint-enable import/first */

type Pending = { config: InternalAxiosRequestConfig; answer: (status: number, code?: string) => void };
const sent: Pending[] = [];

api.defaults.adapter = (config: InternalAxiosRequestConfig) =>
  new Promise((resolve, reject) => {
    sent.push({
      config,
      answer: (status, code) => {
        const response = {
          data: code ? { detail: { code } } : {},
          status,
          statusText: String(status),
          headers: {},
          config,
        };
        if (status < 400) resolve(response);
        else reject(new AxiosError(`status ${status}`, 'ERR_BAD_RESPONSE', config, null, response as never));
      },
    });
  });

let generation = 1;
const onUnauthorized = jest.fn();
const onRegistrationRequired = jest.fn();

beforeEach(() => {
  sent.length = 0;
  generation = 1;
  jest.clearAllMocks();
  jest.spyOn(console, 'error').mockImplementation(() => undefined);
  setUnauthorizedHandler(onUnauthorized);
  setRegistrationRequiredHandler(onRegistrationRequired);
  setAuthResponseAuthority({
    stamp: () => generation,
    // The stand-in identity is always signed in: a request still in its own
    // generation goes out with the session's token, any other is refused.
    dispatchAs: (stamp, sessionAccountId) =>
      stamp !== generation ? 'refuse' : sessionAccountId ? 'session' : 'anonymous',
    acceptsUnauthorized: (stamp) => stamp === generation,
    acceptsRegistrationRequired: (stamp) => stamp === generation,
  });
});

const turn = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

describe('account-bound requests', () => {
  it('are never sent with another account\'s token', async () => {
    await expect(
      claimScanDevice('device-token', { expectedAccountId: 'account-a' }),
    ).rejects.toBeInstanceOf(AccountMismatchError);
    expect(sent).toHaveLength(0);
    expect(signOut).not.toHaveBeenCalled();
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it('go out with the expected account\'s own token', async () => {
    const claim = claimScanDevice('device-token', { expectedAccountId: 'account-b' });
    await turn();
    expect(sent).toHaveLength(1);
    expect(sent[0].config.headers.Authorization).toBe('Bearer token-b');
    expect(sent[0].config.headers['X-Device-Token']).toBe('device-token');
    sent[0].answer(200);
    await expect(claim).resolves.toEqual({});
  });
});

describe('401 from a request made for an earlier identity', () => {
  it('reaches its caller but signs no one out and navigates nowhere', async () => {
    const request = api.get('/api/v2/me');
    await turn();
    generation += 1; // the identity changed while the request was out
    sent[0].answer(401);
    await expect(request).rejects.toBeInstanceOf(AxiosError);
    expect(signOut).not.toHaveBeenCalled();
    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(router.replace).not.toHaveBeenCalled();
  });

  it('a 401 for the current identity still ends the session, as before', async () => {
    const request = api.get('/api/v2/me');
    await turn();
    sent[0].answer(401);
    await expect(request).rejects.toBeInstanceOf(AxiosError);
    expect(signOut).toHaveBeenCalledTimes(1);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
    expect(router.replace).toHaveBeenCalledWith('/(auth)/welcome');
  });
});

describe('REGISTRATION_REQUIRED from a request made for an earlier identity', () => {
  it('changes no state and navigates nowhere', async () => {
    const request = api.get('/api/v2/me');
    await turn();
    generation += 1;
    sent[0].answer(403, 'REGISTRATION_REQUIRED');
    await expect(request).rejects.toBeInstanceOf(AxiosError);
    expect(onRegistrationRequired).not.toHaveBeenCalled();
    expect(router.replace).not.toHaveBeenCalled();
  });

  it('for the current identity it is handed the request stamp and routes as before', async () => {
    const request = api.get('/api/v2/me');
    await turn();
    sent[0].answer(403, 'REGISTRATION_REQUIRED');
    await expect(request).rejects.toBeInstanceOf(AxiosError);
    expect(onRegistrationRequired).toHaveBeenCalledWith(1);
    expect(router.replace).toHaveBeenCalledWith('/(auth)/registration-incomplete');
  });
});
