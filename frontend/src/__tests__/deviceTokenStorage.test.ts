/**
 * Where the device token lives, and the migration that must not lose it.
 *
 * ``X-Device-Token`` is a credential: it authenticates anonymous scanning and
 * ties a phone's scan history to that phone. It sat in AsyncStorage, an
 * unencrypted file protected only by the app sandbox, and has moved to the
 * keychain alongside the signed-in session.
 *
 * The migration matters more than the move. The server refuses to re-register a
 * known ``device_key`` without presenting its current token, so an install that
 * simply lost this blob could never recover that identity — it would mint a new
 * key and orphan every scan already made under the old one. So the old location
 * is read, its contents written to the keychain, and only then cleared.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';

import {
  ensureDevice,
  markDeviceClaimed,
  tokenToClaimFor,
} from '../services/productScan';
import { secureSessionStorage } from '../services/secureSessionStorage';

// The project-wide mock is a stub that always reads null. Migration is the
// whole subject here, so the old store has to actually hold something.
jest.mock('@react-native-async-storage/async-storage', () => {
  const store = new Map<string, string>();
  return {
    __esModule: true,
    __store: store,
    default: {
      getItem: jest.fn(async (key: string) => store.get(key) ?? null),
      setItem: jest.fn(async (key: string, value: string) => {
        store.set(key, value);
      }),
      removeItem: jest.fn(async (key: string) => {
        store.delete(key);
      }),
      multiRemove: jest.fn(async () => undefined),
    },
  };
});

const legacyStore: Map<string, string> = jest.requireMock(
  '@react-native-async-storage/async-storage',
).__store;

jest.mock('expo-secure-store', () => {
  const store = new Map<string, string>();
  return {
    __store: store,
    getItemAsync: jest.fn(async (key: string) => store.get(key) ?? null),
    setItemAsync: jest.fn(async (key: string, value: string) => {
      if (value.length > 2048) throw new Error('value too large for SecureStore');
      store.set(key, value);
    }),
    deleteItemAsync: jest.fn(async (key: string) => {
      store.delete(key);
    }),
  };
});

jest.mock('axios', () => {
  const instance = {
    get: jest.fn(),
    post: jest.fn(),
    interceptors: { request: { use: jest.fn() }, response: { use: jest.fn() } },
  };
  return {
    __esModule: true,
    default: { create: jest.fn(() => instance) },
    create: jest.fn(() => instance),
  };
});

const DEVICE_KEY = 'glamgenius_scan_device_v1';


const LEGACY = { device_key: 'abc123def456', token: 'device-token-value', claimed_for: undefined };

beforeEach(() => {
  jest.clearAllMocks();
  const secureStore = jest.requireMock('expo-secure-store');
  (secureStore.__store as Map<string, string>).clear();
  legacyStore.clear();
});

describe('an install that already has a token in the old place', () => {
  it('keeps working — the identity is not lost', async () => {
    await AsyncStorage.setItem(DEVICE_KEY, JSON.stringify(LEGACY));

    const device = await ensureDevice();

    expect(device?.token).toBe(LEGACY.token);
    expect(device?.device_key).toBe(LEGACY.device_key);
  });

  it('moves it into the keychain', async () => {
    await AsyncStorage.setItem(DEVICE_KEY, JSON.stringify(LEGACY));

    await ensureDevice();

    const secure = await secureSessionStorage.getItem(DEVICE_KEY);
    expect(secure).toBeTruthy();
    expect(JSON.parse(secure as string).token).toBe(LEGACY.token);
  });

  it('clears the unencrypted copy once the keychain has it', async () => {
    await AsyncStorage.setItem(DEVICE_KEY, JSON.stringify(LEGACY));

    await ensureDevice();

    expect(await AsyncStorage.getItem(DEVICE_KEY)).toBeNull();
  });

  it('does not register again — that would orphan the existing scans', async () => {
    await AsyncStorage.setItem(DEVICE_KEY, JSON.stringify(LEGACY));
    const axios = jest.requireMock('axios');
    const instance = axios.default.create();

    await ensureDevice();

    expect(instance.post).not.toHaveBeenCalled();
  });
});

describe('an install that has already migrated', () => {
  it('reads from the keychain and leaves the old place alone', async () => {
    await secureSessionStorage.setItem(DEVICE_KEY, JSON.stringify(LEGACY));

    const device = await ensureDevice();

    expect(device?.token).toBe(LEGACY.token);
    expect(await AsyncStorage.getItem(DEVICE_KEY)).toBeNull();
  });
});

describe('claiming a phone for an account', () => {
  it('reads the token through the keychain', async () => {
    await secureSessionStorage.setItem(DEVICE_KEY, JSON.stringify(LEGACY));

    expect(await tokenToClaimFor('account-1')).toBe(LEGACY.token);
  });

  it('records the claim in the keychain, not the old place', async () => {
    await secureSessionStorage.setItem(DEVICE_KEY, JSON.stringify(LEGACY));

    await markDeviceClaimed('account-1');

    expect(await tokenToClaimFor('account-1')).toBeNull();
    expect(await AsyncStorage.getItem(DEVICE_KEY)).toBeNull();
    const secure = JSON.parse((await secureSessionStorage.getItem(DEVICE_KEY)) as string);
    expect(secure.claimed_for).toBe('account-1');
  });
});

describe('a fresh install', () => {
  it('registers and stores the token in the keychain', async () => {
    const axios = jest.requireMock('axios');
    const instance = axios.default.create();
    instance.post.mockResolvedValueOnce({ data: { token: 'brand-new-token' } });

    const device = await ensureDevice();

    expect(device?.token).toBe('brand-new-token');
    const secure = JSON.parse((await secureSessionStorage.getItem(DEVICE_KEY)) as string);
    expect(secure.token).toBe('brand-new-token');
    expect(await AsyncStorage.getItem(DEVICE_KEY)).toBeNull();
  });
});
