/**
 * Opening a link that came from somewhere else.
 *
 * Every URL the app opens arrives as data: an evidence source, an official
 * record, a citation, a brand's reply page, an Open Food Facts entry.
 * ``Linking.openURL`` hands whatever it is given to the operating system, and
 * the operating system will act on more than web addresses — ``file://`` reads
 * local storage, Android's ``intent://`` reaches other installed apps and
 * their exported components, ``javascript:`` runs in some in-app browsers, and
 * a custom scheme opens whatever app claims it.
 *
 * None of that is something a row in a database should be able to make a phone
 * do. So one place decides, and it decides by allowlist: http and https,
 * nothing else. A URL that is not one of those is not opened and not
 * "corrected" into one — a link the app cannot vouch for is a link it does not
 * follow.
 */
import { Linking } from 'react-native';

/** The only two schemes worth following from data. */
const SAFE_SCHEME = /^https?:\/\/[^\s]/i;

/**
 * Whitespace and control characters, which let a crafted string read as one
 * thing in a log or a review and resolve as another on the device.
 */
const SUSPICIOUS = /[\s\u0000-\u001f\u007f]/;

export const isSafeExternalUrl = (value: unknown): value is string => {
  if (typeof value !== 'string') return false;
  const trimmed = value.trim();
  if (!trimmed || SUSPICIOUS.test(trimmed)) return false;
  return SAFE_SCHEME.test(trimmed);
};

/**
 * Open a link, if it is one we are willing to open.
 *
 * Resolves to whether it was opened, so a caller can show its own "could not
 * open that" state. It never throws: a link failing to open must not take a
 * screen down with it.
 */
export const openExternalUrl = async (value: unknown): Promise<boolean> => {
  if (!isSafeExternalUrl(value)) return false;
  try {
    await Linking.openURL(value.trim());
    return true;
  } catch {
    return false;
  }
};

export default openExternalUrl;
