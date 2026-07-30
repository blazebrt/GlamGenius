import { create } from 'zustand';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { api } from '../services/api';

const USER_ID_KEY = 'glamgenius_user_id';

interface UserProfile {
  id: string;
  name: string;
  email: string;
  age?: number;
  budget_range?: string;
  hair_type?: string;
  skin_type?: string;
  face_shape?: string;
  skin_concerns: string[];
  hair_concerns: string[];
  preferences: Record<string, any>;
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
  createUser: (name: string, email?: string) => Promise<UserProfile | null>;
  updateUser: (data: Partial<UserProfile>) => Promise<void>;
  updateUserProfile: (data: Partial<UserProfile>) => void;
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
      const storedUserId = await AsyncStorage.getItem(USER_ID_KEY);
      if (storedUserId) {
        set({ userId: storedUserId });
        try {
          const response = await api.get(`/users/${storedUserId}`);
          set({ user: response.data });
        } catch (error) {
          console.log('User not found in DB, clearing stored ID');
          await AsyncStorage.removeItem(USER_ID_KEY);
          set({ userId: '', user: null });
        }
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
      const response = await api.get(`/users/${userId}`);
      set({ user: response.data });
    } catch (error) {
      console.error('Error fetching user:', error);
    } finally {
      set({ loading: false });
    }
  },

  createUser: async (name: string, email?: string) => {
    set({ loading: true });
    try {
      const response = await api.post('/users', { name, email: email || '' });
      const user = response.data;
      set({ user, userId: user.id });
      await AsyncStorage.setItem(USER_ID_KEY, user.id);
      return user;
    } catch (error) {
      console.error('Error creating user:', error);
      return null;
    } finally {
      set({ loading: false });
    }
  },

  updateUser: async (data: Partial<UserProfile>) => {
    const { userId } = get();
    if (!userId) return;

    set({ loading: true });
    try {
      const response = await api.put(`/users/${userId}`, data);
      set({ user: response.data });
    } catch (error) {
      console.error('Error updating user:', error);
    } finally {
      set({ loading: false });
    }
  },

  updateUserProfile: (data: Partial<UserProfile>) => {
    const { user } = get();
    if (user) {
      set({ user: { ...user, ...data } as UserProfile });
    }
  },

  logout: async () => {
    try {
      await AsyncStorage.multiRemove([USER_ID_KEY, 'glamgenius_cart']);
    } catch (error) {
      console.error('Error clearing session:', error);
    }
    set({ userId: '', user: null });
  },
}));
