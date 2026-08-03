/**
 * Neutral session + registration store (§2 hardening spec).
 *
 * Registration state is a first-class value here: a Supabase session on its
 * own is **not** enough to enter the product. The store distinguishes:
 *
 *   'signed_out'             — no Supabase session
 *   'registration_pending'   — Supabase session exists but /api/v2/me says
 *                              REGISTRATION_REQUIRED (typically because the
 *                              user has not presented a reservation challenge
 *                              yet, or their email is unconfirmed)
 *   'registered'             — /api/v2/me returned 200
 *
 * Screens read ``registrationState`` and route accordingly. Screens must
 * never assume that ``session != null`` implies ``registered``.
 *
 * The reservation challenge earned by ``/access/reserve`` is held in
 * AsyncStorage under a namespaced key so it survives an app kill between
 * Supabase sign-up and email confirmation.
 */
import { create } from 'zustand';
import type { AuthChangeEvent, Session } from '@supabase/supabase-js';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { supabase } from '../services/supabase';
import {
  api,
  isRegistrationRequired,
  setRegistrationRequiredHandler,
  setUnauthorizedHandler,
} from '../services/api';
import {
  finalizeRegistration,
  reserveInvite,
} from '../services/apiV2';

const CHALLENGE_STORAGE_KEY = '@glamgenius/registration_challenge_v2';

export type RegistrationState =
  | 'signed_out'
  | 'registration_pending'
  | 'registered';

export interface UserProfile {
  id: string;
  name?: string;
  email?: string;
  phone?: string;
  age?: number;
  city?: string;
  diet?: string;
  budget_range?: string;
  height_cm?: number;
  body_type?: string;
  style_vibe?: string;
  hair_type?: string;
  skin_type?: string;
  face_shape?: string;
  skin_tone?: string;
  undertone?: string;
  skin_concerns: string[];
  hair_concerns: string[];
  preferences: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
}

export interface AuthResult {
  ok: boolean;
  code?:
    | 'invalid_credentials'
    | 'email_in_use'
    | 'invite_required'
    | 'invite_invalid'
    | 'reservation_expired'
    | 'email_confirmation_required'
    | 'network'
    | 'unknown';
  message?: string;
  /** True when the Supabase sign-up succeeded but requires email confirmation. */
  needsEmailConfirmation?: boolean;
}

interface UserStore {
  session: Session | null;
  user: UserProfile | null;
  userId: string;
  loading: boolean;
  initialized: boolean;
  registrationState: RegistrationState;
  pendingChallenge: string | null;

  initializeUser: () => Promise<void>;
  hydrateRegistration: () => Promise<void>;
  fetchUser: () => Promise<void>;
  reserveAndRegister: (
    name: string,
    email: string,
    password: string,
    inviteCode: string
  ) => Promise<AuthResult>;
  finishPendingRegistration: () => Promise<AuthResult>;
  login: (email: string, password: string) => Promise<AuthResult>;
  updateUser: (data: Partial<UserProfile>) => Promise<void>;
  updateUserProfile: (data: Partial<UserProfile>) => void;
  logout: () => Promise<void>;
}

const emptyProfile = (id: string, email?: string): UserProfile => ({
  id,
  email,
  skin_concerns: [],
  hair_concerns: [],
  preferences: {},
});

async function readStoredChallenge(): Promise<string | null> {
  try {
    return await AsyncStorage.getItem(CHALLENGE_STORAGE_KEY);
  } catch {
    return null;
  }
}

async function writeStoredChallenge(value: string | null): Promise<void> {
  try {
    if (value) await AsyncStorage.setItem(CHALLENGE_STORAGE_KEY, value);
    else await AsyncStorage.removeItem(CHALLENGE_STORAGE_KEY);
  } catch {
    // Non-fatal: worst case the user has to re-reserve.
  }
}

export const useUserStore = create<UserStore>((set, get) => ({
  session: null,
  user: null,
  userId: '',
  loading: false,
  initialized: false,
  registrationState: 'signed_out',
  pendingChallenge: null,

  initializeUser: async () => {
    try {
      const { data } = await supabase.auth.getSession();
      const session = data.session;
      const stored = await readStoredChallenge();
      set({ pendingChallenge: stored });

      if (!session) {
        set({
          session: null,
          user: null,
          userId: '',
          registrationState: 'signed_out',
        });
        return;
      }
      set({
        session,
        userId: session.user.id,
        user: emptyProfile(session.user.id, session.user.email ?? undefined),
      });
      await get().hydrateRegistration();
    } catch (error) {
       
      console.error('Error initializing user:', error);
    } finally {
      set({ initialized: true });
    }
  },

  hydrateRegistration: async () => {
    try {
      const res = await api.get('/api/v2/me');
      const me = res.data?.profile ?? res.data;
      if (me && typeof me === 'object') {
        set({
          user: { ...emptyProfile(get().userId), ...me },
          registrationState: 'registered',
        });
      } else {
        set({ registrationState: 'registered' });
      }
    } catch (err) {
      if (isRegistrationRequired(err)) {
        set({ registrationState: 'registration_pending' });
      } else {
         
        console.warn('hydrate registration error', err);
      }
    }
  },

  fetchUser: async () => {
    if (!get().userId) return;
    set({ loading: true });
    try {
      const res = await api.get('/api/v2/me');
      const me = res.data?.profile ?? res.data;
      if (me) {
        set({
          user: { ...emptyProfile(get().userId), ...me },
          registrationState: 'registered',
        });
      }
    } catch (err) {
      if (isRegistrationRequired(err)) {
        set({ registrationState: 'registration_pending' });
      }
    } finally {
      set({ loading: false });
    }
  },

  reserveAndRegister: async (name, email, password, inviteCode) => {
    set({ loading: true });
    try {
      // Step 1: reserve the invite BEFORE creating a Supabase identity.
      try {
        const reservation = await reserveInvite(inviteCode.trim(), email.trim());
        await writeStoredChallenge(reservation.challenge);
        set({ pendingChallenge: reservation.challenge });
      } catch (err: any) {
        const detail = err?.response?.data?.detail;
        const code = detail?.code === 'rate_limited' ? 'unknown' : 'invite_invalid';
        return {
          ok: false,
          code,
          message: detail?.message ?? 'This invite cannot be used.',
        };
      }

      // Step 2: Supabase sign-up. The Supabase project may require email
      // confirmation, in which case ``data.session`` will be null and the
      // caller must complete registration after the confirmation deep-link
      // returns.
      const { data, error } = await supabase.auth.signUp({
        email: email.trim(),
        password,
        options: { data: { name } },
      });
      if (error) {
        const msg = error.message || 'Registration failed.';
        return {
          ok: false,
          code: /registered|exists/i.test(msg) ? 'email_in_use' : 'unknown',
          message: msg,
        };
      }
      if (!data.session || !data.user) {
        // Email confirmation required. Keep the reservation challenge in
        // storage so we can finalise once the user completes confirmation.
        return {
          ok: false,
          code: 'email_confirmation_required',
          needsEmailConfirmation: true,
          message:
            'Please confirm your email to finish creating your account. Your invite is held for 30 minutes.',
        };
      }

      // Step 3: finalise. Since Supabase returned a session immediately we
      // can call ``/access/register`` right now.
      set({
        session: data.session,
        userId: data.user.id,
        user: emptyProfile(data.user.id, data.user.email ?? undefined),
      });
      return await get().finishPendingRegistration();
    } catch (err) {
       
      console.error('reserveAndRegister error:', err);
      return { ok: false, code: 'network', message: 'Network error.' };
    } finally {
      set({ loading: false });
    }
  },

  finishPendingRegistration: async () => {
    const challenge =
      get().pendingChallenge ?? (await readStoredChallenge());
    if (!challenge) {
      return {
        ok: false,
        code: 'invite_required',
        message:
          'Your invite reservation has been lost. Please start again from the sign-up screen.',
      };
    }
    try {
      const result = await finalizeRegistration(challenge);
      await writeStoredChallenge(null);
      set({ pendingChallenge: null, registrationState: 'registered' });
      await get().fetchUser();
      return { ok: true };
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      const code = detail?.code as string | undefined;
      if (code === 'reservation_expired' || code === 'reservation_invalid') {
        await writeStoredChallenge(null);
        set({ pendingChallenge: null });
        return {
          ok: false,
          code: 'reservation_expired',
          message:
            'Your invite reservation has expired. Please start again with your invite code.',
        };
      }
      return {
        ok: false,
        code: 'unknown',
        message: detail?.message ?? 'Could not finish registration.',
      };
    }
  },

  login: async (email, password) => {
    set({ loading: true });
    try {
      const { data, error } = await supabase.auth.signInWithPassword({
        email,
        password,
      });
      if (error) {
        return { ok: false, code: 'invalid_credentials', message: error.message };
      }
      if (!data.session || !data.user) {
        return { ok: false, code: 'unknown', message: 'Sign-in failed.' };
      }
      set({
        session: data.session,
        userId: data.user.id,
        user: emptyProfile(data.user.id, data.user.email ?? undefined),
      });
      await get().hydrateRegistration();
      // If the account still isn't registered (rare — a former user who
      // signed up without finishing invite redemption), stay on the
      // registration-pending track. The interceptor already routed the
      // 403 REGISTRATION_REQUIRED response to the correct screen.
      if (get().registrationState !== 'registered') {
        return {
          ok: false,
          code: 'invite_required',
          message:
            'Your Supabase account is signed in, but your GlamGenius registration is not complete.',
        };
      }
      return { ok: true };
    } catch (err) {
       
      console.error('login error:', err);
      return { ok: false, code: 'network', message: 'Network error.' };
    } finally {
      set({ loading: false });
    }
  },

  updateUser: async (data) => {
    if (!get().userId) return;
    set({ loading: true });
    try {
      const res = await api.patch('/api/v2/profile', { attributes: data });
      const updated = res.data?.profile ?? res.data;
      if (updated) set({ user: { ...emptyProfile(get().userId), ...updated } });
    } catch (err) {
       
      console.error('updateUser error:', err);
    } finally {
      set({ loading: false });
    }
  },

  updateUserProfile: (data) => {
    const { user } = get();
    if (user) set({ user: { ...user, ...data } });
  },

  logout: async () => {
    try {
      await supabase.auth.signOut();
    } catch (err) {
       
      console.error('signOut error:', err);
    }
    await writeStoredChallenge(null);
    set({
      session: null,
      user: null,
      userId: '',
      pendingChallenge: null,
      registrationState: 'signed_out',
    });
  },
}));

// Sync Zustand with Supabase's own session change events.
supabase.auth.onAuthStateChange(async (_event: AuthChangeEvent, session) => {
  if (session) {
    useUserStore.setState({
      session,
      userId: session.user.id,
      user:
        useUserStore.getState().user ??
        emptyProfile(session.user.id, session.user.email ?? undefined),
    });
    // Freshly-confirmed email flow: SIGNED_IN fires when Supabase completes
    // email confirmation. Re-hydrate so we discover ``registered`` vs
    // ``registration_pending``.
    await useUserStore.getState().hydrateRegistration();
  } else {
    useUserStore.setState({
      session: null,
      user: null,
      userId: '',
      registrationState: 'signed_out',
    });
  }
});

setUnauthorizedHandler(() => {
  useUserStore.setState({
    session: null,
    user: null,
    userId: '',
    registrationState: 'signed_out',
    pendingChallenge: null,
  });
  void writeStoredChallenge(null);
});

setRegistrationRequiredHandler(() => {
  useUserStore.setState({ registrationState: 'registration_pending' });
});
