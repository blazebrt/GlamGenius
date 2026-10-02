/**
 * F08: a credential is never deleted on the strength of a write that did not
 * land, and a credential the server has just issued is never left to die with
 * the process.
 *
 * The device token is the only proof this phone has of its scan identity. The
 * server never re-registers a known ``device_key`` without the current token,
 * so a token that is lost is gone for good: every later registration is
 * refused and the scanner never works again. Two ways to lose it, both fixed:
 *
 * 1. Migration deleted the plain copy after a keychain write whose failure had
 *    been swallowed. Now the plain copy goes only after the keychain is proven
 *    to hold the same bytes: written, read back, compared.
 * 2. A first registration whose keychain write failed kept the token only in
 *    memory. Now it is kept in an explicit, migration-only recovery copy and
 *    moved into the keychain at the next chance.
 *
 * Every fault here is followed by a restart: modules reloaded, storage kept,
 * exactly as a phone that is closed and opened again. The two stores live
 * outside the mocks so they survive ``jest.resetModules``.
 */

type Behaviour = 'ok' | 'throw' | 'drop';
const mockKeychain = new Map<string, string>();
const mockPlain = new Map<string, string>();
const mockFaults = {
  /** What each successive keychain write does, consumed in order; then 'ok'. */
  writes: [] as Behaviour[],
  /** How many read-backs throw: the first read of a key just written. */
  readBackThrows: 0,
  /** A keychain read that answers with something else, once. */
  readSubstitute: null as string | null,
  plainRemoveThrows: false,
  plainSetThrows: false,
};

const mockJustWritten = new Set<string>();

jest.mock('expo-secure-store', () => ({
  // The project setup clears this before every test; it is the same map.
  __store: mockKeychain,
  getItemAsync: jest.fn(async (key: string) => {
    if (mockJustWritten.delete(key) && mockFaults.readBackThrows > 0) {
      mockFaults.readBackThrows -= 1;
      throw new Error('keychain read failed');
    }
    if (mockFaults.readSubstitute !== null && mockKeychain.has(key)) {
      const substitute = mockFaults.readSubstitute;
      mockFaults.readSubstitute = null;
      return substitute;
    }
    return mockKeychain.get(key) ?? null;
  }),
  setItemAsync: jest.fn(async (key: string, value: string) => {
    const behaviour = mockFaults.writes.shift() ?? 'ok';
    if (behaviour === 'throw') throw new Error('keychain write failed');
    if (behaviour === 'drop') return; // accepted, never stored
    if (value.length > 2048) throw new Error('value too large for SecureStore');
    mockKeychain.set(key, value);
    mockJustWritten.add(key);
  }),
  deleteItemAsync: jest.fn(async (key: string) => {
    mockKeychain.delete(key);
  }),
}));

jest.mock('@react-native-async-storage/async-storage', () => ({
  __esModule: true,
  default: {
    getItem: jest.fn(async (key: string) => mockPlain.get(key) ?? null),
    setItem: jest.fn(async (key: string, value: string) => {
      if (mockFaults.plainSetThrows) throw new Error('disk full');
      mockPlain.set(key, value);
    }),
    removeItem: jest.fn(async (key: string) => {
      if (mockFaults.plainRemoveThrows) throw new Error('remove failed');
      mockPlain.delete(key);
    }),
    multiRemove: jest.fn(async () => undefined),
  },
}));

const mockHttp = {
  get: jest.fn(),
  post: jest.fn(),
  interceptors: { request: { use: jest.fn() }, response: { use: jest.fn() } },
};
jest.mock('axios', () => ({
  __esModule: true,
  default: { create: jest.fn(() => mockHttp) },
  create: jest.fn(() => mockHttp),
}));

type ScanModule = typeof import('../services/productScan');
type StorageModule = typeof import('../services/secureSessionStorage');

const DEVICE_KEY = 'glamgenius_scan_device_v1';
const RECOVERY_KEY = 'glamgenius_scan_device_recovery_v1';
const LEGACY = { device_key: '__TEST_LEGACY_DEVICE_KEY__', token: '__TEST_LEGACY_DEVICE_TOKEN__' };

/** A phone closed and opened again: fresh modules, the same storage. */
function launch(): ScanModule {
  jest.resetModules();
  // Loaded after the reset on purpose: a fresh module is the restart.
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  return require('../services/productScan') as ScanModule;
}

function storage(): StorageModule['secureSessionStorage'] {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  return (require('../services/secureSessionStorage') as StorageModule).secureSessionStorage;
}

async function keychainDevice(): Promise<{ token?: string } | null> {
  const raw = await storage().getItem(DEVICE_KEY);
  return raw ? JSON.parse(raw) : null;
}

const registrations = () => mockHttp.post.mock.calls.filter(([path]) => path === '/api/v2/scan/device').length;

beforeEach(() => {
  mockKeychain.clear();
  mockPlain.clear();
  mockJustWritten.clear();
  Object.assign(mockFaults, {
    writes: [], readBackThrows: 0, readSubstitute: null, plainRemoveThrows: false, plainSetThrows: false,
  });
  mockHttp.get.mockReset();
  mockHttp.post.mockReset();
  mockHttp.post.mockImplementation(async (path: string) => {
    if (path === '/api/v2/scan/device') return { data: { device_id: 'd1', token: 'issued-token' } };
    throw new Error(`unexpected POST ${path}`);
  });
});

describe('migrating a pre-keychain device token', () => {
  beforeEach(() => {
    mockPlain.set(DEVICE_KEY, JSON.stringify(LEGACY));
  });

  it.each<[string, () => void]>([
    ['the keychain write throws', () => { mockFaults.writes = ['throw']; }],
    ['the keychain accepts the write and stores nothing', () => { mockFaults.writes = ['drop']; }],
    ['the read-back throws', () => { mockFaults.readBackThrows = 1; }],
    ['the read-back answers with something else', () => { mockFaults.readSubstitute = '{"token":"other"}'; }],
  ])('%s: the plain copy survives, the token keeps working, and the next launch migrates it', async (_label, fault) => {
    fault();
    let scan = launch();
    const device = await scan.ensureDevice();
    expect(device?.token).toBe(LEGACY.token);
    expect(JSON.parse(mockPlain.get(DEVICE_KEY) as string).token).toBe(LEGACY.token);

    scan = launch();
    expect((await scan.ensureDevice())?.token).toBe(LEGACY.token);
    expect((await keychainDevice())?.token).toBe(LEGACY.token);
    expect(mockPlain.has(DEVICE_KEY)).toBe(false);
    // Never re-registered: that would be refused, and would orphan the scans.
    expect(registrations()).toBe(0);
  });

  it('the plain copy cannot be deleted: the keychain copy is used, and the stale one goes later', async () => {
    mockFaults.plainRemoveThrows = true;
    let scan = launch();
    expect((await scan.ensureDevice())?.token).toBe(LEGACY.token);
    expect((await keychainDevice())?.token).toBe(LEGACY.token);
    expect(mockPlain.has(DEVICE_KEY)).toBe(true);

    mockFaults.plainRemoveThrows = false;
    scan = launch();
    expect((await scan.ensureDevice())?.token).toBe(LEGACY.token);
    expect(mockPlain.has(DEVICE_KEY)).toBe(false);
    expect(registrations()).toBe(0);
  });
});

describe('the first registration, when the keychain will not keep the token', () => {
  it('keeps an explicit recovery copy, and the next launch moves it into the keychain without registering again', async () => {
    mockFaults.writes = ['throw'];
    let scan = launch();
    expect((await scan.ensureDevice())?.token).toBe('issued-token');
    expect(registrations()).toBe(1);
    const copy = JSON.parse(mockPlain.get(RECOVERY_KEY) as string);
    expect(copy).toEqual({
      state: 'secure_write_failed', written_at: expect.any(String),
      device: { device_key: expect.any(String), token: 'issued-token' },
    });
    expect(mockKeychain.has(DEVICE_KEY)).toBe(false);

    scan = launch();
    expect((await scan.ensureDevice())?.token).toBe('issued-token');
    expect((await keychainDevice())?.token).toBe('issued-token');
    expect(mockPlain.has(RECOVERY_KEY)).toBe(false);
    expect(registrations()).toBe(1);
  });

  it('a keychain that keeps failing: the recovery copy is used, kept, and never re-registered', async () => {
    mockFaults.writes = ['throw', 'throw', 'throw', 'throw'];
    let scan = launch();
    await scan.ensureDevice();
    scan = launch();
    expect((await scan.ensureDevice())?.token).toBe('issued-token');
    scan = launch();
    expect((await scan.ensureDevice())?.token).toBe('issued-token');
    expect(mockPlain.has(RECOVERY_KEY)).toBe(true);
    expect(registrations()).toBe(1);
  });

  it('the write lands but the read-back fails: still a recovery copy, never a lost token', async () => {
    mockFaults.readBackThrows = 1; // the read-back inside the verified write
    let scan = launch();
    expect((await scan.ensureDevice())?.token).toBe('issued-token');
    expect(mockPlain.has(RECOVERY_KEY)).toBe(true);

    scan = launch();
    expect((await scan.ensureDevice())?.token).toBe('issued-token');
    expect(mockPlain.has(RECOVERY_KEY)).toBe(false);
    expect(registrations()).toBe(1);
  });

  it('neither store will keep it: this process keeps it in memory rather than registering again', async () => {
    // The installation id was written on an earlier launch; only this
    // launch's writes fail.
    mockPlain.set('glamgenius_installation_id_v1', '01234567-89ab-4cde-8f01-23456789abcd');
    mockFaults.writes = ['throw', 'throw', 'throw'];
    mockFaults.plainSetThrows = true;
    const scan = launch();
    expect((await scan.ensureDevice())?.token).toBe('issued-token');
    expect((await scan.ensureDevice())?.token).toBe('issued-token');
    expect(registrations()).toBe(1);
  });

  it('a recovery copy is newer than any keychain value and wins over it', async () => {
    // A keychain that was unreadable when the server issued a new token, and
    // readable again now with the dead one.
    await storage().setItem(DEVICE_KEY, JSON.stringify({ device_key: LEGACY.device_key, token: 'dead-token' }));
    mockPlain.set(RECOVERY_KEY, JSON.stringify({
      state: 'secure_write_failed', written_at: '2026-10-02T00:00:00.000Z',
      device: { device_key: LEGACY.device_key, token: 'fresh-token' },
    }));
    const scan = launch();
    expect((await scan.ensureDevice())?.token).toBe('fresh-token');
    expect((await keychainDevice())?.token).toBe('fresh-token');
    expect(mockPlain.has(RECOVERY_KEY)).toBe(false);
  });

  it('a recovery copy in any other shape is not a credential', async () => {
    mockPlain.set(RECOVERY_KEY, JSON.stringify({ state: 'something_else', device: { device_key: 'k', token: 't' } }));
    const scan = launch();
    expect((await scan.ensureDevice())?.token).toBe('issued-token');
    expect(registrations()).toBe(1);
  });
});

describe('the verified write itself, at every point a chunked write can fail', () => {
  const KEY = 'sb-project-auth-token';
  const previous = JSON.stringify({ v: 'p'.repeat(5000), name: 'सौरभ' });
  const next = JSON.stringify({ v: 'n'.repeat(5000), name: 'वर्मा' });

  beforeEach(async () => {
    jest.resetModules();
    await storage().setItem(KEY, previous);
    mockJustWritten.clear();
  });

  // previous and next both need four chunks: writes 1-4 are chunks, 5 the manifest.
  it.each<[string, () => void]>([
    ['the first chunk', () => { mockFaults.writes = ['throw']; }],
    ['a later chunk', () => { mockFaults.writes = ['ok', 'ok', 'throw']; }],
    ['the manifest', () => { mockFaults.writes = ['ok', 'ok', 'ok', 'ok', 'throw']; }],
    ['a chunk the keychain silently dropped', () => { mockFaults.writes = ['ok', 'drop']; }],
    ['the read-back', () => { mockFaults.readBackThrows = 1; }],
  ])('%s: reports failure, and what reads back is a whole value or nothing, never a mixture', async (_label, fault) => {
    fault();
    const ok = await storage().setItemVerified(KEY, next);
    expect(ok).toBe(false);
    jest.resetModules();
    const after = await storage().getItem(KEY);
    // A failure reported for a write that did land in full is harmless: the
    // caller keeps its other copy. A spliced value is what must never appear.
    expect([previous, next, null]).toContain(after);
  });

  it('the read-back answers with a different value: reports failure', async () => {
    mockFaults.readSubstitute = '__gg_chunks__:1';
    expect(await storage().setItemVerified(KEY, next)).toBe(false);
  });

  it('a write that lands is reported, and reads back exactly', async () => {
    expect(await storage().setItemVerified(KEY, next)).toBe(true);
    jest.resetModules();
    expect(await storage().getItem(KEY)).toBe(next);
  });
});
