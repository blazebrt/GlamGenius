import { create } from 'zustand';
import { api } from '../services/api';

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
  setUserId: (id: string) => void;
  setUser: (user: UserProfile | null) => void;
  initializeUser: () => Promise<void>;
  fetchUser: () => Promise<void>;
  createUser: (name: string, email?: string) => Promise<UserProfile | null>;
  updateUser: (data: Partial<UserProfile>) => Promise<void>;
  updateUserProfile: (data: Partial<UserProfile>) => void;
}

export const useUserStore = create<UserStore>((set, get) => ({
  userId: '',
  user: null,
  loading: false,

  setUserId: (id: string) => set({ userId: id }),

  setUser: (user: UserProfile | null) => set({ user }),

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

  // Direct profile update without API call (for immediate state updates)
  updateUserProfile: (data: Partial<UserProfile>) => {
    const { user } = get();
    if (user) {
      set({ user: { ...user, ...data } as UserProfile });
    }
  },
}));
