/**
 * UTF-8 text to base64 and back, in plain JavaScript.
 *
 * The keychain adapter (``secureSessionStorage``) stores every value as base64
 * so that a chunk boundary can never cut a multi-byte character in half. It
 * used to do that with an unimported ``Buffer``, which is a Node global. Jest
 * runs on Node, so the tests always had one. The app runs on JavaScriptCore,
 * which has no such global, and nothing in the shipped bundle installs one:
 * the ``buffer`` package is bundled, but only as a module other packages
 * import, never assigned to the global object. On an engine without the
 * global, ``Buffer.from`` is a ReferenceError: every keychain write throws
 * and every read comes back as nothing.
 *
 * So this depends on nothing outside the language: no ``Buffer``, no
 * ``TextEncoder``, no ``btoa``. Each of those may or may not exist depending on
 * the engine and the React Native version, and a credential store is the
 * wrong place to find out.
 *
 * Two properties matter, and the tests hold both:
 *
 * - **Same bytes as before.** The output is the standard alphabet with ``=``
 *   padding: exactly what ``Buffer.from(value, 'utf8').toString('base64')``
 *   produced. Values written by the old code therefore read back unchanged.
 *   That includes a lone surrogate, which is written as U+FFFD, as Buffer did.
 * - **Strict on the way back.** Anything that is not canonical base64 of
 *   well-formed UTF-8 throws. ``Buffer`` quietly skipped bad characters and
 *   replaced bad bytes. For a credential, a value that decodes to something
 *   slightly different is worse than one that does not decode, so the caller
 *   reads it as signed out.
 */

const ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';

const LOOKUP: Record<string, number> = {};
for (let index = 0; index < ALPHABET.length; index += 1) {
  LOOKUP[ALPHABET[index]] = index;
}

const REPLACEMENT = 0xfffd;

function utf8Bytes(value: string): number[] {
  const bytes: number[] = [];
  for (let index = 0; index < value.length; index += 1) {
    let code = value.charCodeAt(index);
    if (code >= 0xd800 && code <= 0xdbff) {
      const next = index + 1 < value.length ? value.charCodeAt(index + 1) : -1;
      if (next >= 0xdc00 && next <= 0xdfff) {
        code = 0x10000 + ((code - 0xd800) << 10) + (next - 0xdc00);
        index += 1;
      } else {
        code = REPLACEMENT;
      }
    } else if (code >= 0xdc00 && code <= 0xdfff) {
      code = REPLACEMENT;
    }
    if (code < 0x80) {
      bytes.push(code);
    } else if (code < 0x800) {
      bytes.push(0xc0 | (code >> 6), 0x80 | (code & 0x3f));
    } else if (code < 0x10000) {
      bytes.push(0xe0 | (code >> 12), 0x80 | ((code >> 6) & 0x3f), 0x80 | (code & 0x3f));
    } else {
      bytes.push(
        0xf0 | (code >> 18),
        0x80 | ((code >> 12) & 0x3f),
        0x80 | ((code >> 6) & 0x3f),
        0x80 | (code & 0x3f),
      );
    }
  }
  return bytes;
}

/** Encode a string as base64 of its UTF-8 bytes, padded, standard alphabet. */
export function encodeUtf8Base64(value: string): string {
  const bytes = utf8Bytes(value);
  let out = '';
  let index = 0;
  for (; index + 2 < bytes.length; index += 3) {
    const triple = (bytes[index] << 16) | (bytes[index + 1] << 8) | bytes[index + 2];
    out += ALPHABET[(triple >> 18) & 63] + ALPHABET[(triple >> 12) & 63]
      + ALPHABET[(triple >> 6) & 63] + ALPHABET[triple & 63];
  }
  const rest = bytes.length - index;
  if (rest === 1) {
    const single = bytes[index] << 16;
    out += `${ALPHABET[(single >> 18) & 63]}${ALPHABET[(single >> 12) & 63]}==`;
  } else if (rest === 2) {
    const double = (bytes[index] << 16) | (bytes[index + 1] << 8);
    out += `${ALPHABET[(double >> 18) & 63]}${ALPHABET[(double >> 12) & 63]}${ALPHABET[(double >> 6) & 63]}=`;
  }
  return out;
}

function base64Bytes(value: string): number[] {
  if (value.length % 4 !== 0) throw new Error('base64 length is not a multiple of four');
  const padding = value.endsWith('==') ? 2 : value.endsWith('=') ? 1 : 0;
  const body = value.length - padding;
  const bytes: number[] = [];
  for (let index = 0; index < value.length; index += 4) {
    const digits: number[] = [];
    for (let offset = 0; offset < 4; offset += 1) {
      const at = index + offset;
      if (at >= body) {
        digits.push(0);
        continue;
      }
      const digit = LOOKUP[value[at]];
      if (digit === undefined) throw new Error('not a base64 character');
      digits.push(digit);
    }
    const quad = (digits[0] << 18) | (digits[1] << 12) | (digits[2] << 6) | digits[3];
    const isLast = index + 4 === value.length;
    const count = isLast ? 3 - padding : 3;
    // Canonical only: the bits a padded group does not use must be zero, so one
    // stored value has exactly one spelling.
    if (isLast && padding === 2 && (quad & 0xffff) !== 0) throw new Error('non-canonical base64');
    if (isLast && padding === 1 && (quad & 0xff) !== 0) throw new Error('non-canonical base64');
    if (count >= 1) bytes.push((quad >> 16) & 0xff);
    if (count >= 2) bytes.push((quad >> 8) & 0xff);
    if (count >= 3) bytes.push(quad & 0xff);
  }
  return bytes;
}

function utf8Text(bytes: number[]): string {
  let out = '';
  let index = 0;
  while (index < bytes.length) {
    const first = bytes[index];
    let code: number;
    let width: number;
    let minimum: number;
    if (first < 0x80) {
      code = first; width = 1; minimum = 0;
    } else if (first >= 0xc2 && first <= 0xdf) {
      code = first & 0x1f; width = 2; minimum = 0x80;
    } else if (first >= 0xe0 && first <= 0xef) {
      code = first & 0x0f; width = 3; minimum = 0x800;
    } else if (first >= 0xf0 && first <= 0xf4) {
      code = first & 0x07; width = 4; minimum = 0x10000;
    } else {
      throw new Error('invalid UTF-8 lead byte');
    }
    if (index + width > bytes.length) throw new Error('truncated UTF-8 sequence');
    for (let offset = 1; offset < width; offset += 1) {
      const next = bytes[index + offset];
      if ((next & 0xc0) !== 0x80) throw new Error('invalid UTF-8 continuation byte');
      code = (code << 6) | (next & 0x3f);
    }
    if (code < minimum) throw new Error('overlong UTF-8 sequence');
    if (code > 0x10ffff || (code >= 0xd800 && code <= 0xdfff)) throw new Error('invalid code point');
    if (code >= 0x10000) {
      const offset = code - 0x10000;
      out += String.fromCharCode(0xd800 + (offset >> 10), 0xdc00 + (offset & 0x3ff));
    } else {
      out += String.fromCharCode(code);
    }
    index += width;
  }
  return out;
}

/** Decode canonical base64 of well-formed UTF-8. Throws on anything else. */
export function decodeUtf8Base64(value: string): string {
  return utf8Text(base64Bytes(value));
}
