/**
 * The session store, and the three ways a chunked keychain write goes wrong.
 *
 * The session moved from AsyncStorage — a plain unencrypted file — to the
 * device keychain, because what it holds is a refresh token: a long-lived key
 * to the account rather than a password somebody can change.
 *
 * The keychain stores at most 2048 bytes per value and a Supabase session is
 * routinely larger, so values are split. Splitting is where the risk is, and
 * every failure lands at sign-in, which is the worst place for one.
 */
import { secureSessionStorage, CHUNK_SIZE } from '../services/secureSessionStorage';

jest.mock('expo-secure-store', () => {
  const store = new Map<string, string>();
  return {
    __store: store,
    getItemAsync: jest.fn(async (key: string) => store.get(key) ?? null),
    setItemAsync: jest.fn(async (key: string, value: string) => {
      // The real thing refuses an oversized value. If it did not, none of this
      // file would need to exist, so the mock has to refuse one too.
      if (value.length > 2048) {
        throw new Error(`value too large for SecureStore: ${value.length}`);
      }
      store.set(key, value);
    }),
    deleteItemAsync: jest.fn(async (key: string) => {
      store.delete(key);
    }),
  };
});

const secureStore = jest.requireMock('expo-secure-store');
const rawStore: Map<string, string> = secureStore.__store;

const KEY = 'sb-realproject-auth-token';

/** A session of roughly the shape Supabase persists. */
const session = (bytes: number) =>
  JSON.stringify({
    access_token: 'a'.repeat(Math.max(bytes - 200, 10)),
    refresh_token: 'r'.repeat(64),
    user: { id: '11111111-1111-1111-1111-111111111111', email: 'a@example.com' },
  });

beforeEach(() => {
  rawStore.clear();
  jest.clearAllMocks();
});

describe('a session that fits', () => {
  it('is stored and read back unchanged', async () => {
    const value = session(300);
    await secureSessionStorage.setItem(KEY, value);
    expect(await secureSessionStorage.getItem(KEY)).toBe(value);
  });

  it('reads back as null when nothing was stored', async () => {
    expect(await secureSessionStorage.getItem(KEY)).toBeNull();
  });

  it('is removed completely', async () => {
    await secureSessionStorage.setItem(KEY, session(300));
    await secureSessionStorage.removeItem(KEY);
    expect(await secureSessionStorage.getItem(KEY)).toBeNull();
    expect(rawStore.size).toBe(0);
  });
});

describe('a session too large for one keychain entry', () => {
  it('round-trips a value several times the limit', async () => {
    const value = session(9000);
    expect(value.length).toBeGreaterThan(2048);

    await secureSessionStorage.setItem(KEY, value);

    expect(await secureSessionStorage.getItem(KEY)).toBe(value);
  });

  it('never writes an entry the keychain would refuse', async () => {
    await secureSessionStorage.setItem(KEY, session(20000));
    for (const [key, stored] of rawStore) {
      expect(stored.length).toBeLessThanOrEqual(2048);
      expect(key).toBeTruthy();
    }
  });

  it('is removed completely, chunks and all', async () => {
    await secureSessionStorage.setItem(KEY, session(9000));
    expect(rawStore.size).toBeGreaterThan(1);

    await secureSessionStorage.removeItem(KEY);

    expect(rawStore.size).toBe(0);
    expect(await secureSessionStorage.getItem(KEY)).toBeNull();
  });
});

describe('a session that shrinks', () => {
  it('does not splice the tail of the old one onto the new one', async () => {
    // The failure this guards against: four chunks written, then two, with
    // chunks three and four left behind. The next read reassembles a value
    // that is part new and part old — invalid, and not fixable from inside
    // the app.
    const large = session(20000);
    const small = session(6000);

    await secureSessionStorage.setItem(KEY, large);
    const chunkCountAfterLarge = rawStore.size;
    await secureSessionStorage.setItem(KEY, small);

    expect(rawStore.size).toBeLessThan(chunkCountAfterLarge);
    expect(await secureSessionStorage.getItem(KEY)).toBe(small);
  });

  it('handles chunked down to unchunked', async () => {
    await secureSessionStorage.setItem(KEY, session(20000));
    const tiny = session(200);

    await secureSessionStorage.setItem(KEY, tiny);

    expect(await secureSessionStorage.getItem(KEY)).toBe(tiny);
    expect(rawStore.size).toBe(1);
  });

  it('handles unchunked up to chunked', async () => {
    await secureSessionStorage.setItem(KEY, session(200));
    const large = session(20000);

    await secureSessionStorage.setItem(KEY, large);

    expect(await secureSessionStorage.getItem(KEY)).toBe(large);
  });
});

describe('characters that are more than one byte', () => {
  // Chunking splits bytes, not characters, and a naive split cuts a multi-byte
  // character in half. The tokens are base64 so this only surfaces through
  // names and emails — which, for an India-only product, is every day.
  const names = ['सौरभ वर्मा', 'ಸೌರಭ್', 'সৌরভ', '🙂 नमस्ते 🙏', 'Ünïcødé'];

  it.each(names)('round-trips %s inside a small value', async (name) => {
    const value = JSON.stringify({ user: { name } });
    await secureSessionStorage.setItem(KEY, value);
    expect(await secureSessionStorage.getItem(KEY)).toBe(value);
  });

  it.each(names)('round-trips %s inside a chunked value', async (name) => {
    const value = JSON.stringify({ pad: 'x'.repeat(20000), user: { name } });
    await secureSessionStorage.setItem(KEY, value);
    expect(await secureSessionStorage.getItem(KEY)).toBe(value);
  });

  it('round-trips a value made entirely of multi-byte characters', async () => {
    const value = '🙏'.repeat(3000);
    await secureSessionStorage.setItem(KEY, value);
    expect(await secureSessionStorage.getItem(KEY)).toBe(value);
  });

  it('splits on a boundary that would cut a character in half', async () => {
    // Positioned so a byte-naive split would land mid-character.
    const value = 'x'.repeat(CHUNK_SIZE - 1) + '🙏' + 'y'.repeat(CHUNK_SIZE);
    await secureSessionStorage.setItem(KEY, value);
    expect(await secureSessionStorage.getItem(KEY)).toBe(value);
  });
});

describe('when the keychain is not available', () => {
  it('reads as signed out rather than throwing', async () => {
    secureStore.getItemAsync.mockRejectedValueOnce(new Error('keychain locked'));
    expect(await secureSessionStorage.getItem(KEY)).toBeNull();
  });

  it('signing out does not fail because storage did', async () => {
    secureStore.deleteItemAsync.mockRejectedValueOnce(new Error('keychain locked'));
    await expect(secureSessionStorage.removeItem(KEY)).resolves.toBeUndefined();
  });

  it('a missing chunk reads as signed out, never as half a session', async () => {
    await secureSessionStorage.setItem(KEY, session(20000));
    const chunk = [...rawStore.keys()].find((key) => key.endsWith('__1'));
    rawStore.delete(chunk as string);

    expect(await secureSessionStorage.getItem(KEY)).toBeNull();
  });
});

describe('a backend that reports a missing key as undefined', () => {
  // Not hypothetical: with no mock in place, the real module's reads come back
  // undefined. The first version of this file ended its chunk-removal loop on a
  // strict `null`, so undefined meant it never ended — no exception, no
  // progress, just a process climbing until the OS killed it. Jest reported
  // that as a dead worker, which looks nothing like the bug it was.
  const asUndefined = () => {
    secureStore.getItemAsync.mockImplementation(async () => undefined);
  };

  it('reads as signed out rather than hanging', async () => {
    asUndefined();
    expect(await secureSessionStorage.getItem(KEY)).toBeNull();
  });

  it('removeItem finishes', async () => {
    asUndefined();
    await expect(secureSessionStorage.removeItem(KEY)).resolves.toBeUndefined();
  });

  it('setItem finishes', async () => {
    asUndefined();
    await expect(secureSessionStorage.setItem(KEY, session(300))).resolves.toBeUndefined();
  });

  it('never scans past the ceiling', async () => {
    asUndefined();
    await secureSessionStorage.removeItem(KEY);
    // A bounded loop makes a bounded number of reads. An unbounded one made
    // this number grow until the process died.
    expect(secureStore.getItemAsync.mock.calls.length).toBeLessThan(100);
  });
});

describe('a value too large to store', () => {
  it('is refused rather than written as chunks nothing can reassemble', async () => {
    await expect(
      secureSessionStorage.setItem(KEY, 'x'.repeat(CHUNK_SIZE * 200)),
    ).rejects.toThrow(/ceiling/);
  });
});
