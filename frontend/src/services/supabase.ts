/**
 * Supabase client singleton for the Expo app.
 *
 * Only the anon key is shipped in the bundle — that key can create sessions
 * against Supabase Auth and nothing else. Every product data call goes through
 * FastAPI, which validates the JWT independently.
 *
 * Session persistence uses the device keychain on native (iOS Keychain,
 * Android Keystore) and localStorage on web, where Supabase's own defaults
 * apply.
 */
import { Platform } from 'react-native';
import 'react-native-url-polyfill/auto';
import { createClient, SupabaseClient } from '@supabase/supabase-js';

import { secureSessionStorage } from './secureSessionStorage';

const SUPABASE_URL = process.env.EXPO_PUBLIC_SUPABASE_URL || '';
const SUPABASE_ANON_KEY = process.env.EXPO_PUBLIC_SUPABASE_ANON_KEY || '';

if (!SUPABASE_URL || !SUPABASE_ANON_KEY) {
  // Do not throw — some tests import this file without env set. The client
  // will simply refuse to authenticate anyone, which is the safer default.
   
  console.warn(
    'EXPO_PUBLIC_SUPABASE_URL or EXPO_PUBLIC_SUPABASE_ANON_KEY is not set. Sign-in will fail until they are configured.'
  );
}

/**
 * Storage adapter that Supabase can use for session persistence. On web we
 * let Supabase use its own localStorage default (autoStorage is enabled),
 * so this only wires up native.
 *
 * Native uses the keychain rather than AsyncStorage. What is being stored is a
 * refresh token — a long-lived key to the account, not a password somebody can
 * change — and AsyncStorage is an unencrypted file whose only protection is
 * the app sandbox. See ``secureSessionStorage`` for why that adapter is more
 * than a passthrough: the keychain caps a value at 2048 bytes and a Supabase
 * session is usually larger.
 */
const nativeStorage = secureSessionStorage;

export const supabase: SupabaseClient = createClient(
  SUPABASE_URL || 'https://placeholder.supabase.co',
  SUPABASE_ANON_KEY || 'placeholder-anon-key',
  {
    auth: {
      storage: Platform.OS === 'web' ? undefined : (nativeStorage as never),
      autoRefreshToken: true,
      persistSession: true,
      detectSessionInUrl: Platform.OS === 'web',
    },
  }
);

/**
 * Read the current access token, refreshing the session if necessary.
 * Returns null if the caller is signed out or the refresh failed.
 */
export const getAccessToken = async (): Promise<string | null> => {
  const { data, error } = await supabase.auth.getSession();
  if (error || !data.session) return null;
  return data.session.access_token ?? null;
};

/** Sign out of Supabase Auth. Local session state is cleared. */
export const signOut = async (): Promise<void> => {
  await supabase.auth.signOut();
};
