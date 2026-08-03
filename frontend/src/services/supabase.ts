/**
 * Supabase client singleton for the Expo app.
 *
 * Only the anon key is shipped in the bundle — that key can create sessions
 * against Supabase Auth and nothing else. Every product data call goes through
 * FastAPI, which validates the JWT independently.
 *
 * Session persistence uses AsyncStorage on native and localStorage on web
 * (Supabase's own defaults handle web; we plug AsyncStorage in for native).
 */
import { Platform } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import 'react-native-url-polyfill/auto';
import { createClient, SupabaseClient } from '@supabase/supabase-js';

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
 */
const nativeStorage = {
  getItem: (key: string) => AsyncStorage.getItem(key),
  setItem: (key: string, value: string) => AsyncStorage.setItem(key, value),
  removeItem: (key: string) => AsyncStorage.removeItem(key),
};

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
