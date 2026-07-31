import { create } from 'zustand';
import AsyncStorage from '@react-native-async-storage/async-storage';
import {
  api,
  setAuthToken,
  loadAuthToken,
  clearAuthToken,
  setUnauthorizedHandler,
} from '../services/api';

const USER_ID_KEY = 'glamgenius_user_id';

export interface UserProfile {
  id: string;
  name: string;
  email: string;
  phone?: string;
  age?: number;
  city?: string;
  diet?: string;
  budget_range?: string;
  height_cm?: number;
  weight_kg?: number;
  body_type?: string;
  style_vibe?: string;
  hair_type?: string;
  skin_type?: string;
  face_shape?: string;
  skin_tone?: string;
  undertone?: string;
  skin_concerns: string[];
  hair_concerns: string[];
  preferences: Record<string, any>;
  plan?: string;
  plan_expires_at?: string;
  scans_used_this_month?: number;
  scans_remaining_free?: number | null;
  free_scans_per_month?: number;
  created_at: string;
  updated_at: string;
}

interface UserStore {
  userId: string;
  user: UserProfile | null;
  loading: boolean;
  initialized: boolean;
  setUserId: (id: string) => void;
  setUser: (user: UserProfile | null) => void;
  initializeUser: () => Promise<void>;
  fetchUser: () => Promise<void>;
  createUser: (name: string, email?: string, extra?: Partial<UserProfile> & { password?: string }) => Promise<UserProfile | null>;
  login: (email: string, password: string) => Promise<UserProfile | null>;
  updateUser: (data: Partial<UserProfile>) => Promise<void>;
  updateUserProfile: (data: Partial<UserProfile>) => void;
  refreshSubscription: () => Promise<void>;
  logout: () => Promise<void>;
}

export const useUserStore = create<UserStore>((set, get) => ({
  userId: '',
  user: null,
  loading: false,
  initialized: false,

  setUserId: (id: string) => set({ userId: id }),
  setUser: (user: UserProfile | null) => set({ user }),

  initializeUser: async () => {
    try {
      // Session is only usable if we still hold a token.
      const token = await loadAuthToken();
      const storedUserId = await AsyncStorage.getItem(USER_ID_KEY);

      if (!token) {
        if (storedUserId) await AsyncStorage.removeItem(USER_ID_KEY);
        set({ userId: '', user: null });
        return;
      }

      try {
        const response = await api.get('/users/me');
        set({ user: response.data, userId: response.data.id });
        await AsyncStorage.setItem(USER_ID_KEY, response.data.id);
      } catch {
        // A 401 is already handled by the api interceptor.
        console.log('Stored session is no longer valid, clearing it');
        await AsyncStorage.removeItem(USER_ID_KEY);
        set({ userId: '', user: null });
      }
    } catch (error) {
      console.error('Error initializing user:', error);
    } finally {
      set({ initialized: true });
    }
  },

  fetchUser: async () => {
    const { userId } = get();
    if (!userId) return;
    set({ loading: true });
    try {
      const response = await api.get('/users/me');
      set({ user: response.data });
    } catch (error) {
      console.error('Error fetching user:', error);
    } finally {
      set({ loading: false });
    }
  },

  createUser: async (name, email, extra) => {
    set({ loading: true });
    try {
      const response = await api.post('/users', {
        name,
        email: email || '',
        phone: extra?.phone || '',
        password: (extra as any)?.password || '',
        age: extra?.age,
        city: extra?.city,
        diet: extra?.diet,
        height_cm: extra?.height_cm,
        weight_kg: extra?.weight_kg,
        body_type: extra?.body_type,
        style_vibe: extra?.style_vibe,
      });
      const { token, ...user } = response.data;
      if (token) await setAuthToken(token);
      await AsyncStorage.setItem(USER_ID_KEY, user.id);
      set({ user: user as UserProfile, userId: user.id });
      return user as UserProfile;
    } catch (error) {
      console.error('Error creating user:', error);
      return null;
    } finally {
      set({ loading: false });
    }
  },

  login: async (email, password) => {
    set({ loading: true });
    try {
      const response = await api.post('/auth/login', { email, password });
      const user = response.data.user;
      const token = response.data.token;
      if (token) await setAuthToken(token);
      await AsyncStorage.setItem(USER_ID_KEY, user.id);
      set({ user, userId: user.id });
      return user;
    } catch (error) {
      console.error('Login error:', error);
      return null;
    } finally {
      set({ loading: false });
    }
  },

  updateUser: async (data) => {
    const { userId } = get();
    if (!userId) return;
    set({ loading: true });
    try {
      const response = await api.put('/users/me', data);
      set({ user: response.data });
    } catch (error) {
      console.error('Error updating user:', error);
    } finally {
      set({ loading: false });
    }
  },

  updateUserProfile: (data) => {
    const { user } = get();
    if (user) set({ user: { ...user, ...data } as UserProfile });
  },

  refreshSubscription: async () => {
    const { userId } = get();
    if (!userId) return;
    try {
      const response = await api.get('/subscription/status');
      const { user } = get();
      if (user) {
        set({
          user: {
            ...user,
            plan: response.data.plan,
            plan_expires_at: response.data.expires_at,
            scans_used_this_month: response.data.scans_used_this_month,
            scans_remaining_free: response.data.scans_remaining,
            free_scans_per_month: response.data.free_scans_per_month,
          },
        });
      }
    } catch (error) {
      console.error('Subscription refresh error:', error);
    }
  },

  logout: async () => {
    try {
      await clearAuthToken();
      await AsyncStorage.multiRemove([USER_ID_KEY, 'glamgenius_cart']);
    } catch (error) {
      console.error('Error clearing session:', error);
    }
    set({ userId: '', user: null });
  },
}));

// When the server rejects our token, drop the in-app session too. The api
// layer clears the token and navigates back to welcome.
setUnauthorizedHandler(() => {
  useUserStore.setState({ userId: '', user: null });
  AsyncStorage.removeItem(USER_ID_KEY).catch(() => {});
});
