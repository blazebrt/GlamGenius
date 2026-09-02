/* eslint-disable import/first */
let mockReject: ((error: unknown) => Promise<unknown>) | undefined;

jest.mock('../services/supabase', () => ({
  getAccessToken: jest.fn(() => Promise.resolve('account-token')),
  signOut: jest.fn(() => Promise.resolve()),
}));

jest.mock('expo-router', () => ({ router: { replace: jest.fn() } }));

jest.mock('axios', () => {
  const instance = {
    get: jest.fn(),
    post: jest.fn(),
    interceptors: {
      request: { use: jest.fn() },
      response: { use: jest.fn((_resolve: unknown, reject: (error: unknown) => Promise<unknown>) => { mockReject = reject; }) },
    },
  };
  return { __esModule: true, default: { create: jest.fn(() => instance) }, create: jest.fn(() => instance) };
});

import { setUnauthorizedHandler } from '../services/api';

const mockSignOut = jest.requireMock('../services/supabase').signOut as jest.Mock;
const mockReplace = jest.requireMock('expo-router').router.replace as jest.Mock;

describe('shared API authentication boundary', () => {
  beforeEach(() => {
    mockSignOut.mockClear();
    mockReplace.mockClear();
    setUnauthorizedHandler(null);
  });

  it('does not sign out a valid account when the scan device is unknown', async () => {
    const unauthorized = jest.fn();
    setUnauthorizedHandler(unauthorized);
    const error = { response: { status: 401, data: { detail: { code: 'DEVICE_UNKNOWN' } } } };

    await expect(mockReject?.(error)).rejects.toBe(error);
    expect(mockSignOut).not.toHaveBeenCalled();
    expect(unauthorized).not.toHaveBeenCalled();
    expect(mockReplace).not.toHaveBeenCalled();
  });

  it('keeps genuine account 401 handling unchanged', async () => {
    const unauthorized = jest.fn();
    setUnauthorizedHandler(unauthorized);
    const error = { response: { status: 401, data: { detail: { code: 'ACCOUNT_UNAUTHORIZED' } } } };

    await expect(mockReject?.(error)).rejects.toBe(error);
    expect(mockSignOut).toHaveBeenCalledTimes(1);
    expect(unauthorized).toHaveBeenCalledTimes(1);
    expect(mockReplace).toHaveBeenCalledWith('/(auth)/welcome');
  });
});
