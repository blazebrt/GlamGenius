/**
 * F08, for the reservation challenge: the old copy goes only once the keychain
 * is proven to hold it.
 *
 * Someone who signed up and is waiting on a confirmation email may have their
 * only copy of the challenge in the pre-keychain location. Migration used to
 * write it to the keychain with any failure swallowed, then delete the old
 * copy regardless, which on a failed write destroyed the only copy and cost
 * the person their invite.
 *
 * Finalisation is made to fail with a network error in every case, so the
 * success path does not clear the challenge before where it ended up can be
 * observed. "Next launch" is a second call with the in-memory copy cleared.
 */
import { handleAuthStateChange, useUserStore } from '../store/userStore';
import { secureSessionStorage } from '../services/secureSessionStorage';
import { finalizeRegistration } from '../services/apiV2';

const CHALLENGE_KEY = '@glamgenius/registration_challenge_v2';

type Behaviour = 'ok' | 'throw' | 'drop';
const mockFaults = { writes: [] as Behaviour[], readBackThrows: 0, plainRemoveThrows: false };
const mockJustWritten = new Set<string>();

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
        if (mockFaults.plainRemoveThrows) throw new Error('remove failed');
        store.delete(key);
      }),
      multiRemove: jest.fn(async () => undefined),
    },
  };
});

jest.mock('expo-secure-store', () => {
  const store = new Map<string, string>();
  return {
    __store: store,
    getItemAsync: jest.fn(async (key: string) => {
      if (mockJustWritten.delete(key) && mockFaults.readBackThrows > 0) {
        mockFaults.readBackThrows -= 1;
        throw new Error('keychain read failed');
      }
      return store.get(key) ?? null;
    }),
    setItemAsync: jest.fn(async (key: string, value: string) => {
      const behaviour = mockFaults.writes.shift() ?? 'ok';
      if (behaviour === 'throw') throw new Error('keychain write failed');
      if (behaviour === 'drop') return;
      store.set(key, value);
      mockJustWritten.add(key);
    }),
    deleteItemAsync: jest.fn(async (key: string) => {
      store.delete(key);
    }),
  };
});

const legacyStore: Map<string, string> = jest.requireMock('@react-native-async-storage/async-storage').__store;
const keychain: Map<string, string> = jest.requireMock('expo-secure-store').__store;

jest.mock('../services/apiV2', () => ({
  finalizeRegistration: jest.fn(),
  reserveInvite: jest.fn(),
  claimScanDevice: jest.fn(),
  getMe: jest.fn(async () => ({})),
  patchAppearanceProfile: jest.fn(),
}));

jest.mock('../services/api', () => ({
  isRegistrationRequired: jest.fn(() => false),
  setRegistrationRequiredHandler: jest.fn(),
  setUnauthorizedHandler: jest.fn(),
  setAuthResponseAuthority: jest.fn(),
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

const asAccount = { expectedAccountId: 'account-1' };

beforeEach(() => {
  legacyStore.clear();
  keychain.clear();
  mockJustWritten.clear();
  Object.assign(mockFaults, { writes: [], readBackThrows: 0, plainRemoveThrows: false });
  jest.clearAllMocks();
  (finalizeRegistration as jest.Mock).mockRejectedValue(Object.assign(new Error('network'), { response: undefined }));
  useUserStore.setState({ pendingChallenge: null });
  handleAuthStateChange('SIGNED_IN', {
    access_token: 'token-1',
    user: { id: 'account-1', email: 'account-1@example.com' },
  } as never);
});

async function nextLaunch(): Promise<void> {
  useUserStore.setState({ pendingChallenge: null });
  await useUserStore.getState().finishPendingRegistration();
}

it.each<[string, () => void]>([
  ['the keychain write throws', () => { mockFaults.writes = ['throw']; }],
  ['the keychain accepts the write and stores nothing', () => { mockFaults.writes = ['drop']; }],
  ['the read-back throws', () => { mockFaults.readBackThrows = 1; }],
])('%s: the old copy survives, is used, and the next launch migrates it', async (_label, fault) => {
  legacyStore.set(CHALLENGE_KEY, 'only-copy');
  fault();

  await useUserStore.getState().finishPendingRegistration();
  expect(finalizeRegistration).toHaveBeenCalledWith('only-copy', asAccount);
  expect(legacyStore.get(CHALLENGE_KEY)).toBe('only-copy');

  await nextLaunch();
  expect(finalizeRegistration).toHaveBeenLastCalledWith('only-copy', asAccount);
  expect(await secureSessionStorage.getItem(CHALLENGE_KEY)).toBe('only-copy');
  expect(legacyStore.has(CHALLENGE_KEY)).toBe(false);
});

it('the old copy cannot be deleted: the keychain copy is used, and the stale one goes later', async () => {
  legacyStore.set(CHALLENGE_KEY, 'only-copy');
  mockFaults.plainRemoveThrows = true;

  await useUserStore.getState().finishPendingRegistration();
  expect(await secureSessionStorage.getItem(CHALLENGE_KEY)).toBe('only-copy');
  expect(legacyStore.get(CHALLENGE_KEY)).toBe('only-copy');

  mockFaults.plainRemoveThrows = false;
  await nextLaunch();
  expect(finalizeRegistration).toHaveBeenLastCalledWith('only-copy', asAccount);
  expect(legacyStore.has(CHALLENGE_KEY)).toBe(false);
});
