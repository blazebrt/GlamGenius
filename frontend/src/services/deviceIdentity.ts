import AsyncStorage from '@react-native-async-storage/async-storage';

const KEY = 'glamgenius_installation_id_v1';
const INSTALLATION_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function randomUuid(): string {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (char) => {
    const value = Math.floor(Math.random() * 16);
    const nibble = char === 'x' ? value : (value & 0x3) | 0x8;
    return nibble.toString(16);
  });
}

/**
 * This installation's id if it already has one, otherwise ``null``.
 *
 * Reads only: nothing is created or written, and a storage failure is
 * ``null``. For a caller that must not mint an identity it has no use for:
 * logout removes this installation's notification device only if the
 * installation ever had an id to register one with.
 */
export async function readInstallationId(): Promise<string | null> {
  try {
    const existing = await AsyncStorage.getItem(KEY);
    return existing && INSTALLATION_ID.test(existing) ? existing : null;
  } catch {
    return null;
  }
}

export async function getInstallationId(): Promise<string> {
  const existing = await AsyncStorage.getItem(KEY);
  if (existing && INSTALLATION_ID.test(existing)) return existing;
  const created = randomUuid();
  await AsyncStorage.setItem(KEY, created);
  return created;
}
