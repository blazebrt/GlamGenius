import { create } from 'zustand';
import AsyncStorage from '@react-native-async-storage/async-storage';

interface CartItem {
  id: string;
  type: 'service' | 'product' | 'addon';
  name: string;
  price: number;
  quantity: number;
  duration?: number;
}

interface CartStore {
  items: CartItem[];
  addItem: (item: Omit<CartItem, 'quantity'>) => void;
  removeItem: (id: string) => void;
  updateQuantity: (id: string, quantity: number) => void;
  clearCart: () => void;
  getTotal: () => number;
  getItemCount: () => number;
  loadCart: () => Promise<void>;
  saveCart: () => Promise<void>;
}

export const useCartStore = create<CartStore>((set, get) => ({
  items: [],

  addItem: (item) => {
    const { items, saveCart } = get();
    const existingItem = items.find((i) => i.id === item.id);
    
    if (existingItem) {
      set({
        items: items.map((i) =>
          i.id === item.id ? { ...i, quantity: i.quantity + 1 } : i
        ),
      });
    } else {
      set({ items: [...items, { ...item, quantity: 1 }] });
    }
    saveCart();
  },

  removeItem: (id) => {
    const { items, saveCart } = get();
    set({ items: items.filter((i) => i.id !== id) });
    saveCart();
  },

  updateQuantity: (id, quantity) => {
    const { items, saveCart } = get();
    if (quantity <= 0) {
      set({ items: items.filter((i) => i.id !== id) });
    } else {
      set({
        items: items.map((i) => (i.id === id ? { ...i, quantity } : i)),
      });
    }
    saveCart();
  },

  clearCart: () => {
    set({ items: [] });
    AsyncStorage.removeItem('glamgenius_cart');
  },

  getTotal: () => {
    const { items } = get();
    return items.reduce((total, item) => total + item.price * item.quantity, 0);
  },

  getItemCount: () => {
    const { items } = get();
    return items.reduce((count, item) => count + item.quantity, 0);
  },

  loadCart: async () => {
    try {
      const cartData = await AsyncStorage.getItem('glamgenius_cart');
      if (cartData) {
        set({ items: JSON.parse(cartData) });
      }
    } catch (error) {
      console.error('Error loading cart:', error);
    }
  },

  saveCart: async () => {
    try {
      const { items } = get();
      await AsyncStorage.setItem('glamgenius_cart', JSON.stringify(items));
    } catch (error) {
      console.error('Error saving cart:', error);
    }
  },
}));
