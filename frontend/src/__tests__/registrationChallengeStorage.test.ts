/**
 * Where the reservation challenge lives, and the migration that must not
 * lose it.
 *
 * The challenge is a bearer secret: presented with the matching email it
 * finalises a registration and spends an invite slot. It sat in AsyncStorage,
 * an unencrypted file protected only by the app sandbox, and has moved to the
 * keychain alongside the signed-in session and the device token.
 *
 * Its window is short — the reservation expires in thirty minutes and the
 * challenge is cleared the moment registration finishes — so this is defence
 * in depth rather than an open door. The install that leaves one lying around
 * is the one that signed up, closed the app, and is waiting on a confirmation
 * email. That is also the install the migration exists for: its only copy of
 * the challenge is in the old place, and dropping it would cost the invite.
 */

import { useUserStore } from '../store/userStore';
import { secureSessionStorage } from '../services/secureSessionStorage';
import { finalizeRegistration } from '../services/apiV2';

const CHALLENGE_KEY = '@glamgenius/registration_challenge_v2';

// The project-wide mock always reads null. Migration is the whole subject
// here, so the old store has to actually hold something.
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

const keychain: Map<string, string> = jest.requireMock('expo-secure-store').__store;

jest.mock('../services/apiV2', () => ({
  finalizeRegistration: jest.fn(async () => ({})),
  reserveInvite: jest.fn(),
  claimScanDevice: jest.fn(),
  getMe: jest.fn(async () => ({})),
  patchAppearanceProfile: jest.fn(),
}));

jest.mock('../services/api', () => ({
  isRegistrationRequired: jest.fn(() => false),
  setRegistrationRequiredHandler: jest.fn(),
  setUnauthorizedHandler: jest.fn(),
}));

jest.mock('../services/supabase', () => ({
  supabase: {
    auth: {
      signUp: jest.fn(),
      getSession: jest.fn(async () => ({ data: { session: null } })),
      onAuthStateChange: jest.fn(() => ({ data: { subscription: { unsubscribe: jest.fn() } } })),
    },
  },
}));

jest.mock('../services/productScan', () => ({
  markDeviceClaimed: jest.fn(),
  tokenToClaimFor: jest.fn(async () => null),
}));

beforeEach(() => {
  legacyStore.clear();
  keychain.clear();
  jest.clearAllMocks();
  useUserStore.setState({ pendingChallenge: null });
});

describe('the reservation challenge is kept in the keychain', () => {
  it('is read from the keychain when it is already there', async () => {
    await secureSessionStorage.setItem(CHALLENGE_KEY, 'challenge-from-keychain');

    await useUserStore.getState().finishPendingRegistration();

    expect(finalizeRegistration).toHaveBeenCalledWith('challenge-from-keychain');
  });

  it('is migrated out of the old unencrypted store rather than dropped', async () => {
    legacyStore.set(CHALLENGE_KEY, 'challenge-from-asyncstorage');

    const result = await useUserStore.getState().finishPendingRegistration();

    expect(result.ok).toBe(true);
    expect(finalizeRegistration).toHaveBeenCalledWith('challenge-from-asyncstorage');
  });

  it('actually writes the secret into the keychain, not just the old store', async () => {
    // The server call is made to fail so the success path does not clear the
    // challenge before it can be observed. What is being checked is where the
    // secret ended up, which is invisible on the happy path.
    (finalizeRegistration as jest.Mock).mockRejectedValueOnce(
      Object.assign(new Error('network'), { response: undefined }),
    );
    legacyStore.set(CHALLENGE_KEY, 'challenge-to-move');

    await useUserStore.getState().finishPendingRegistration();

    expect(await secureSessionStorage.getItem(CHALLENGE_KEY)).toBe('challenge-to-move');
    expect(legacyStore.has(CHALLENGE_KEY)).toBe(false);
  });

  it('never leaves a plaintext copy once the keychain holds it', async () => {
    (finalizeRegistration as jest.Mock).mockRejectedValueOnce(new Error('network'));
    legacyStore.set(CHALLENGE_KEY, 'challenge-to-move');

    await useUserStore.getState().finishPendingRegistration();

    const plaintext = [...legacyStore.values()];
    expect(plaintext).not.toContain('challenge-to-move');
  });

  it('clears both stores once registration has finished', async () => {
    legacyStore.set(CHALLENGE_KEY, 'challenge-to-move');
    await secureSessionStorage.setItem(CHALLENGE_KEY, 'challenge-to-move');

    await useUserStore.getState().finishPendingRegistration();

    expect(await secureSessionStorage.getItem(CHALLENGE_KEY)).toBeNull();
    expect(legacyStore.has(CHALLENGE_KEY)).toBe(false);
  });

  it('reports a missing reservation rather than calling the server', async () => {
    const result = await useUserStore.getState().finishPendingRegistration();

    expect(result.ok).toBe(false);
    expect(result.code).toBe('invite_required');
    expect(finalizeRegistration).not.toHaveBeenCalled();
  });

  it('survives an unavailable keychain by leaving the person able to retry', async () => {
    const secureStore = jest.requireMock('expo-secure-store');
    secureStore.setItemAsync.mockRejectedValueOnce(new Error('keychain locked'));
    legacyStore.set(CHALLENGE_KEY, 'challenge-to-move');

    // The migration write fails; the challenge must still reach the server
    // from the copy we just read rather than the attempt throwing.
    const result = await useUserStore.getState().finishPendingRegistration();

    expect(result.ok).toBe(true);
    expect(finalizeRegistration).toHaveBeenCalledWith('challenge-to-move');
  });
});
