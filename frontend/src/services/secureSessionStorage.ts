/**
 * Where the signed-in session lives on a phone.
 *
 * It used to be AsyncStorage, which is a plain unencrypted file inside the app
 * sandbox. The sandbox is the whole protection: on a rooted or jailbroken
 * device, through some backup paths, or with physical access and the right
 * tools, the file is readable — and what it holds is the refresh token, which
 * is a long-lived key to the account rather than a password one can change.
 *
 * SecureStore puts it in the iOS keychain and Android Keystore instead, where
 * the operating system holds the key.
 *
 * The catch, and the reason this file is not three lines: SecureStore stores at
 * most 2048 bytes per value, and a Supabase session is routinely larger — two
 * JWTs and a user object. Writing one straight in fails, and it fails at
 * sign-in, which is the worst place for a silent failure. So a large value is
 * split across numbered keys and reassembled on read.
 *
 * Three things this has to get right, and each has a test:
 *
 * 1. **Shrinking.** A session that needed four chunks and now needs two must
 *    not leave chunks three and four behind, or the next read reassembles a
 *    session that is part new and part old — invalid, and unrecoverable
 *    without clearing storage by hand. A manifest records the count.
 * 2. **Unicode.** Chunking splits bytes, not characters. A naive split can cut
 *    a multi-byte character in half and corrupt the whole value, and the JWTs
 *    are base64 so this only shows up for names and emails with non-ASCII
 *    characters — which, for an India-only product, is a normal Tuesday.
 *    Values are base64-encoded before splitting, so every chunk is ASCII.
 * 3. **Failing to a signed-out state, never a broken one.** If the keychain is
 *    unavailable, reading returns null: the person signs in again. Returning a
 *    partial value would be worse than returning nothing.
 */
import * as SecureStore from 'expo-secure-store';

/** SecureStore's documented per-value ceiling. */
const SECURE_STORE_VALUE_LIMIT = 2048;

/**
 * Bytes per chunk. Comfortably inside the ceiling: the limit applies to the
 * stored value and we would rather leave room than discover the edge of it in
 * production, at sign-in, on somebody's phone.
 */
export const CHUNK_SIZE = 1536;

/** Marks a manifest so a single-chunk value is told apart from a chunked one. */
const MANIFEST_PREFIX = '__gg_chunks__:';

const chunkKey = (key: string, index: number) => `${key}__${index}`;

const encode = (value: string): string =>
  Buffer.from(value, 'utf8').toString('base64');

const decode = (value: string): string =>
  Buffer.from(value, 'base64').toString('utf8');

const split = (value: string): string[] => {
  const parts: string[] = [];
  for (let at = 0; at < value.length; at += CHUNK_SIZE) {
    parts.push(value.slice(at, at + CHUNK_SIZE));
  }
  return parts;
};

/**
 * The most chunks a value may occupy, and therefore the most this will ever
 * scan. A session runs to a few chunks; this ceiling is far above anything
 * real and exists so the loop below cannot run forever.
 */
const MAX_CHUNKS = 64;

/**
 * Remove chunks from `from` upwards until one is missing.
 *
 * Used both when a value shrinks and when it is deleted. It stops at the first
 * gap, because the chunks were written consecutively — but it stops at
 * `MAX_CHUNKS` regardless.
 *
 * The bound is not defensive tidiness. Written as an unbounded loop that ended
 * only on a strict `null`, this spun forever against any backend that reports a
 * missing key as `undefined` — no exception to catch, no progress, just a
 * process climbing to an out-of-memory kill. A loop whose exit depends on a
 * value some other layer decides needs both a nullish check and a ceiling.
 */
const removeChunksFrom = async (key: string, from: number): Promise<void> => {
  for (let index = from; index < MAX_CHUNKS; index += 1) {
    const existing = await SecureStore.getItemAsync(chunkKey(key, index));
    // Nullish, not strictly null: a backend that has no value may say either.
    if (existing == null) return;
    await SecureStore.deleteItemAsync(chunkKey(key, index));
  }
};

export const secureSessionStorage = {
  async getItem(key: string): Promise<string | null> {
    try {
      const head = await SecureStore.getItemAsync(key);
      if (head == null) return null;
      if (!head.startsWith(MANIFEST_PREFIX)) return decode(head);

      const count = Number.parseInt(head.slice(MANIFEST_PREFIX.length), 10);
      if (!Number.isInteger(count) || count < 1 || count > MAX_CHUNKS) return null;

      const parts: string[] = [];
      for (let index = 0; index < count; index += 1) {
        const part = await SecureStore.getItemAsync(chunkKey(key, index));
        // A missing chunk means the value is incomplete. Half a session is
        // not a session: report signed out and let them sign in again.
        if (part == null) return null;
        parts.push(part);
      }
      return decode(parts.join(''));
    } catch {
      return null;
    }
  },

  async setItem(key: string, value: string): Promise<void> {
    const encoded = encode(value);

    if (encoded.length <= SECURE_STORE_VALUE_LIMIT - MANIFEST_PREFIX.length) {
      await SecureStore.setItemAsync(key, encoded);
      // The previous value may have been chunked. Those chunks are not ours
      // to leave behind.
      await removeChunksFrom(key, 0);
      return;
    }

    const parts = split(encoded);
    if (parts.length > MAX_CHUNKS) {
      // Far larger than any session. Storing it would write chunks that
      // ``getItem`` refuses to reassemble, which is worse than not storing it.
      throw new Error(`value needs ${parts.length} chunks, above the ${MAX_CHUNKS} ceiling`);
    }
    for (let index = 0; index < parts.length; index += 1) {
      await SecureStore.setItemAsync(chunkKey(key, index), parts[index]);
    }
    // Written after the chunks, so a manifest never promises a chunk that is
    // not there yet.
    await SecureStore.setItemAsync(key, `${MANIFEST_PREFIX}${parts.length}`);
    // A shorter session than last time leaves the tail behind otherwise, and
    // the next read would splice old bytes onto new ones.
    await removeChunksFrom(key, parts.length);
  },

  async removeItem(key: string): Promise<void> {
    try {
      await SecureStore.deleteItemAsync(key);
      await removeChunksFrom(key, 0);
    } catch {
      // Signing out must not fail because storage did.
    }
  },
};

export default secureSessionStorage;
