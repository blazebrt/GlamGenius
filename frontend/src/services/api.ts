import axios from 'axios';

const BACKEND_URL = process.env.EXPO_PUBLIC_BACKEND_URL || '';

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

// Add response interceptor for error handling
api.interceptors.response.use(
  (response) => response,
  (error) => {
    console.error('API Error:', error.response?.data || error.message);
    return Promise.reject(error);
  }
);
