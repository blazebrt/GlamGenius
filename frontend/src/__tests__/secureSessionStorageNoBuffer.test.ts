/**
 * F01: the keychain adapter must work where there is no Node ``Buffer``.
 *
 * Jest runs on Node, which has a global ``Buffer``; the app runs on
 * JavaScriptCore, which does not, and nothing in the shipped bundle installs
 * one. A test suite that leaves the global in place cannot tell the two apart:
 * an unimported ``Buffer.from`` passes here and throws on a phone.
 *
 * So this suite removes the global *before* loading the storage module, checks
 * that it is gone, and drives the real module end to end through the mocked
 * keychain. It also keeps the real ``Buffer`` aside, taken before removal, so it
 * can prove the new encoding writes exactly the bytes the old one did: values a
 * phone already stored must read back unchanged.
 */

jest.mock('expo-secure-store', () => {
  const store = new Map<string, string>();
  return {
    __store: store,
    getItemAsync: jest.fn(async (key: string) => store.get(key) ?? null),
    setItemAsync: jest.fn(async (key: string, value: string) => {
      if (value.length > 2048) throw new Error(`value too large for SecureStore: ${value.length}`);
      store.set(key, value);
    }),
    deleteItemAsync: jest.fn(async (key: string) => {
      store.delete(key);
    }),
  };
});

// Taken before the global is removed, and used only to produce reference
// values the old implementation would have written. Never handed to the
// module under test.
const NodeBuffer: typeof Buffer = globalThis.Buffer;
const bufferEncode = (value: string) => NodeBuffer.from(value, 'utf8').toString('base64');

type Storage = typeof import('../services/secureSessionStorage');
type Codec = typeof import('../services/utf8Base64');
let storage: Storage['secureSessionStorage'];
let CHUNK_SIZE: number;
let codec: Codec;

const rawStore = (): Map<string, string> => jest.requireMock('expo-secure-store').__store;
const KEY = 'sb-realproject-auth-token';

beforeAll(() => {
  (globalThis as { Buffer?: unknown }).Buffer = undefined;
  // Loaded only now, with no Buffer anywhere in this realm.
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const loaded = require('../services/secureSessionStorage') as Storage;
  storage = loaded.secureSessionStorage;
  CHUNK_SIZE = loaded.CHUNK_SIZE;
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  codec = require('../services/utf8Base64') as Codec;
});

afterAll(() => {
  (globalThis as { Buffer?: unknown }).Buffer = NodeBuffer;
});

beforeEach(() => {
  rawStore().clear();
});

it('runs with no Buffer global at all', () => {
  expect((globalThis as { Buffer?: unknown }).Buffer).toBeUndefined();
  expect(typeof Buffer).toBe('undefined');
});

const SAMPLES: [string, string][] = [
  ['ASCII', 'plain ascii {"a":1} ~!@#$%^&*()'],
  ['Hindi / Devanagari', 'सौरभ वर्मा — नमस्ते, आप कैसे हैं?'],
  ['emoji', '🙂🙏🏽👩‍👩‍👧‍👦🇮🇳'],
  ['mixed Unicode', 'Ünïcødé ಸೌರಭ್ সৌরভ 汉字 🙏 तमिழ் a'],
  ['empty', ''],
];

describe('round trips through the real module', () => {
  it.each(SAMPLES)('%s, small enough for one entry', async (_label, text) => {
    const value = JSON.stringify({ user: { name: text } });
    await storage.setItem(KEY, value);
    expect(await storage.getItem(KEY)).toBe(value);
  });

  it.each(SAMPLES)('%s, inside a multi-chunk value', async (_label, text) => {
    const value = JSON.stringify({ pad: 'x'.repeat(9000), user: { name: text }, tail: text.repeat(40) });
    await storage.setItem(KEY, value);
    const head = rawStore().get(KEY) as string;
    expect(head.startsWith('__gg_chunks__:')).toBe(true);
    expect(await storage.getItem(KEY)).toBe(value);
  });

  it('a value made entirely of four-byte characters, across many chunks', async () => {
    const value = '🙏'.repeat(4000);
    await storage.setItem(KEY, value);
    expect(await storage.getItem(KEY)).toBe(value);
  });

  it('a boundary that would cut a character in half', async () => {
    const value = 'x'.repeat(CHUNK_SIZE - 1) + 'नम🙏' + 'y'.repeat(CHUNK_SIZE);
    await storage.setItem(KEY, value);
    expect(await storage.getItem(KEY)).toBe(value);
  });
});

describe('the same bytes the Buffer version wrote', () => {
  it.each(SAMPLES)('%s encodes exactly as Buffer did', (_label, text) => {
    expect(codec.encodeUtf8Base64(text)).toBe(bufferEncode(text));
  });

  it('agrees with Buffer across every length and a spread of code points', () => {
    const points = [0x00, 0x24, 0x7f, 0x80, 0xa2, 0x7ff, 0x800, 0x939, 0xffff - 2, 0x10000, 0x1f64f, 0x10ffff];
    for (let length = 0; length < 12; length += 1) {
      for (const point of points) {
        const text = String.fromCodePoint(point).repeat(length) + 'z'.repeat(length % 3);
        expect(codec.encodeUtf8Base64(text)).toBe(bufferEncode(text));
        expect(codec.decodeUtf8Base64(bufferEncode(text))).toBe(text);
      }
    }
  });

  it('writes a lone surrogate as U+FFFD, as Buffer did', () => {
    const lone = 'a\ud800b\udc00';
    expect(codec.encodeUtf8Base64(lone)).toBe(bufferEncode(lone));
    expect(codec.decodeUtf8Base64(codec.encodeUtf8Base64(lone))).toBe('a�b�');
  });

  it('reads a single-entry value an older build stored', async () => {
    const value = JSON.stringify({ device_key: 'k', token: 't', name: 'सौरभ 🙏' });
    rawStore().set(KEY, bufferEncode(value));
    expect(await storage.getItem(KEY)).toBe(value);
  });

  it('reads a chunked value an older build stored under a count-only manifest', async () => {
    const value = JSON.stringify({ pad: 'p'.repeat(5000), name: 'ಸೌರಭ್' });
    const encoded = bufferEncode(value);
    const parts = [];
    for (let at = 0; at < encoded.length; at += CHUNK_SIZE) parts.push(encoded.slice(at, at + CHUNK_SIZE));
    parts.forEach((part, index) => rawStore().set(`${KEY}__${index}`, part));
    rawStore().set(KEY, `__gg_chunks__:${parts.length}`);
    expect(await storage.getItem(KEY)).toBe(value);
  });
});

describe('shrinking and deleting', () => {
  it('a shorter value removes the old tail chunks', async () => {
    await storage.setItem(KEY, 'L'.repeat(9000));
    const before = [...rawStore().keys()].filter((key) => key.startsWith(`${KEY}__`)).length;
    await storage.setItem(KEY, 'S'.repeat(2500));
    const after = [...rawStore().keys()].filter((key) => key.startsWith(`${KEY}__`)).length;
    expect(after).toBeLessThan(before);
    expect(await storage.getItem(KEY)).toBe('S'.repeat(2500));
  });

  it('chunked down to one entry leaves no chunk behind', async () => {
    await storage.setItem(KEY, 'L'.repeat(9000));
    await storage.setItem(KEY, 'short नमस्ते');
    expect([...rawStore().keys()]).toEqual([KEY]);
    expect(await storage.getItem(KEY)).toBe('short नमस्ते');
  });

  it('delete removes the manifest and every chunk', async () => {
    await storage.setItem(KEY, '🙏'.repeat(3000));
    await storage.removeItem(KEY);
    expect(rawStore().size).toBe(0);
    expect(await storage.getItem(KEY)).toBeNull();
  });
});

describe('a damaged value reads as nothing, never as part of a value', () => {
  it('a missing chunk', async () => {
    await storage.setItem(KEY, 'm'.repeat(9000));
    rawStore().delete(`${KEY}__1`);
    expect(await storage.getItem(KEY)).toBeNull();
  });

  it.each([
    ['not a number', '__gg_chunks__:abc'],
    ['zero chunks', '__gg_chunks__:0'],
    ['above the ceiling', '__gg_chunks__:65'],
    ['negative', '__gg_chunks__:-1'],
    ['a malformed checksum', '__gg_chunks__:3:4000:xyz'],
    ['an extra field', '__gg_chunks__:3:4000:0123abcd:9'],
  ])('a corrupt manifest: %s', async (_label, manifest) => {
    await storage.setItem(KEY, 'c'.repeat(9000));
    rawStore().set(KEY, manifest);
    expect(await storage.getItem(KEY)).toBeNull();
  });

  it('new chunks beside old ones under the old manifest, as a write that failed part-way leaves them', async () => {
    // Same length on purpose: the count and the length agree, so only the
    // checksum can tell the splice from a value.
    await storage.setItem(KEY, JSON.stringify({ v: 'o'.repeat(5000) }));
    const before = new Map(rawStore());
    await storage.setItem(KEY, JSON.stringify({ v: 'n'.repeat(5000) }));
    // The new write got through chunks 0 and 1, then failed: chunks 2.. and the
    // manifest are still the old ones.
    for (const [key, value] of before) {
      if (key === KEY || !/__[01]$/.test(key)) rawStore().set(key, value);
    }
    expect(rawStore().get(KEY)).toBe(before.get(KEY));
    expect(rawStore().get(`${KEY}__0`)).not.toBe(before.get(`${KEY}__0`));
    expect(await storage.getItem(KEY)).toBeNull();
  });

  it.each([
    ['a character outside the alphabet', 'ab*d'],
    ['a length that is not a multiple of four', 'abc'],
    ['padding in the middle', 'ab=dabcd'],
    ['non-canonical padding bits', 'QR=='],
    ['bytes that are not UTF-8', bufferEncodeBytes([0xff, 0xfe, 0x41])],
    ['an overlong encoding', bufferEncodeBytes([0xc0, 0xaf])],
    ['an encoded surrogate', bufferEncodeBytes([0xed, 0xa0, 0x80])],
    ['a truncated sequence', bufferEncodeBytes([0xe0, 0xa4])],
  ])('a single entry that is %s', async (_label, stored) => {
    rawStore().set(KEY, stored);
    expect(await storage.getItem(KEY)).toBeNull();
  });
});

function bufferEncodeBytes(bytes: number[]): string {
  return NodeBuffer.from(bytes).toString('base64');
}
