import { create } from 'zustand';
import type { AuthChangeEvent, Session } from '@supabase/supabase-js';
import { supabase } from '../services/supabase';
import { api, setUnauthorizedHandler } from '../services/api';

/**
 * Neutral session + profile store.
 *
 * Supabase Auth is the source of truth for the session. This store simply
 * mirrors the Supabase session and layers the FastAPI profile on top so
 * screens can read `useUserStore(s => s.user)` without knowing about auth
 * mechanics.
 *
 * Fields removed from the pre-cutover shape (deliberately, do not add back):
 *   - plan
 *   - plan_expires_at
 *   - scans_remaining_free
 *   - free_scans_per_month
 *   - refreshSubscription
 *
 * Beta usage counters, if a screen needs them, come from `/api/v2/me` under
 * `beta_usage`, not from a user profile field.
 */
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
    | 'network'
    | 'unknown';
  message?: string;
}

interface UserStore {
  session: Session | null;
  user: UserProfile | null;
  userId: string;
  loading: boolean;
  initialized: boolean;

  initializeUser: () => Promise<void>;
  fetchUser: () => Promise<void>;
  createUser: (
    name: string,
    email: string,
    password: string,
    inviteCode: string
  ) => Promise<AuthResult>;
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

export const useUserStore = create<UserStore>((set, get) => ({
  session: null,
  user: null,
  userId: '',
  loading: false,
  initialized: false,

  initializeUser: async () => {
    try {
      const { data } = await supabase.auth.getSession();
      const session = data.session;
      if (!session) {
        set({ session: null, user: null, userId: '' });
        return;
      }
      set({
        session,
        userId: session.user.id,
        user: emptyProfile(session.user.id, session.user.email ?? undefined),
      });
      // Best-effort profile hydrate; failure just leaves the empty profile.
      try {
        const res = await api.get('/api/v2/me');
        const me = res.data?.profile ?? res.data;
        if (me && typeof me === 'object') {
          set({ user: { ...emptyProfile(session.user.id), ...me } });
        }
      } catch {
        // Profile row may not exist yet on very first login; ignore.
      }
    } catch (error) {
       
      console.error('Error initializing user:', error);
    } finally {
      set({ initialized: true });
    }
  },

  fetchUser: async () => {
    if (!get().userId) return;
    set({ loading: true });
    try {
      const res = await api.get('/api/v2/me');
      const me = res.data?.profile ?? res.data;
      if (me) set({ user: { ...emptyProfile(get().userId), ...me } });
    } catch (error) {
       
      console.error('Error fetching user:', error);
    } finally {
      set({ loading: false });
    }
  },

  createUser: async (name, email, password, inviteCode) => {
    set({ loading: true });
    try {
      const { data, error } = await supabase.auth.signUp({
        email,
        password,
        options: {
          data: { name, invite_code: inviteCode },
        },
      });
      if (error) {
        const msg = error.message || 'Registration failed.';
        const code = /registered|exists/i.test(msg)
          ? 'email_in_use'
          : 'unknown';
        return { ok: false, code, message: msg };
      }
      if (!data.session || !data.user) {
        return {
          ok: false,
          code: 'unknown',
          message:
            'Please confirm your email to finish creating your account.',
        };
      }
      // Redeem the invite server-side. FastAPI validates the code, marks it
      // used, and creates the `accounts`/`profiles` rows tied to the
      // authenticated Supabase UUID.
      try {
        await api.post('/api/v2/access/register', {
          name,
          invite_code: inviteCode,
        });
      } catch (err) {
        // Roll the Supabase session back so the user isn't left half-created.
        await supabase.auth.signOut().catch(() => {});
        const detail = (err as { response?: { data?: { detail?: { code?: string; message?: string } } } })
          ?.response?.data?.detail;
        const code = detail?.code === 'INVITE_INVALID' ? 'invite_invalid' : 'invite_required';
        return {
          ok: false,
          code,
          message: detail?.message ?? 'This invite code cannot be used.',
        };
      }
      set({
        session: data.session,
        userId: data.user.id,
        user: emptyProfile(data.user.id, data.user.email ?? undefined),
      });
      await get().fetchUser();
      return { ok: true };
    } catch (error) {
       
      console.error('createUser error:', error);
      return { ok: false, code: 'network', message: 'Network error.' };
    } finally {
      set({ loading: false });
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
        return {
          ok: false,
          code: 'invalid_credentials',
          message: error.message,
        };
      }
      if (!data.session || !data.user) {
        return { ok: false, code: 'unknown', message: 'Sign-in failed.' };
      }
      set({
        session: data.session,
        userId: data.user.id,
        user: emptyProfile(data.user.id, data.user.email ?? undefined),
      });
      await get().fetchUser();
      return { ok: true };
    } catch (error) {
       
      console.error('login error:', error);
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
    } catch (error) {
       
      console.error('updateUser error:', error);
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
    } catch (error) {
       
      console.error('signOut error:', error);
    }
    set({ session: null, user: null, userId: '' });
  },
}));

// Sync Zustand with Supabase's own session change events so refresh, remote
// sign-out and social sign-in all update the app.
supabase.auth.onAuthStateChange((_event: AuthChangeEvent, session) => {
  if (session) {
    useUserStore.setState({
      session,
      userId: session.user.id,
      user:
        useUserStore.getState().user ??
        emptyProfile(session.user.id, session.user.email ?? undefined),
    });
  } else {
    useUserStore.setState({ session: null, user: null, userId: '' });
  }
});

// When FastAPI rejects our token (rare — Supabase refreshes proactively),
// clear the in-app session as well.
setUnauthorizedHandler(() => {
  useUserStore.setState({ session: null, user: null, userId: '' });
});
