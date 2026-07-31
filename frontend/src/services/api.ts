import axios from 'axios';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { router } from 'expo-router';

const BACKEND_URL = process.env.EXPO_PUBLIC_BACKEND_URL || '';

const TOKEN_KEY = 'glamgenius_token';

if (!BACKEND_URL) {
  console.warn(
    'EXPO_PUBLIC_BACKEND_URL is not set. API requests will fail until the backend URL is configured.'
  );
}

export const api = axios.create({
  baseURL: BACKEND_URL ? `${BACKEND_URL.replace(/\/$/, '')}/api` : undefined,
  timeout: 60000, // 60 seconds for AI requests
  headers: {
    'Content-Type': 'application/json',
  },
});

// Kept in memory so every request doesn't have to wait on storage.
let authToken: string | null = null;

// Lets the user store clear its own state when the session ends.
let onUnauthorized: (() => void) | null = null;

export const setUnauthorizedHandler = (handler: (() => void) | null) => {
  onUnauthorized = handler;
};

export const setAuthToken = async (token: string) => {
  authToken = token;
  await AsyncStorage.setItem(TOKEN_KEY, token);
};

export const loadAuthToken = async (): Promise<string | null> => {
  if (authToken) return authToken;
  const stored = await AsyncStorage.getItem(TOKEN_KEY);
  authToken = stored;
  return stored;
};

export const clearAuthToken = async () => {
  authToken = null;
  await AsyncStorage.removeItem(TOKEN_KEY);
};

export const getAuthToken = () => authToken;

// Attach the token to every request automatically.
api.interceptors.request.use(async (config) => {
  const token = authToken ?? (await loadAuthToken());
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Handle expired / invalid sessions: drop the token and go back to welcome.
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    if (error.response?.status === 401) {
      await clearAuthToken();
      onUnauthorized?.();
      router.replace('/(auth)/welcome');
      return Promise.reject(error);
    }
    console.error('API Error:', error.response?.data || error.message);
    return Promise.reject(error);
  }
);
