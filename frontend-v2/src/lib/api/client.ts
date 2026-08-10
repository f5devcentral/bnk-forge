/**
 * Shared API client configuration
 * All domain-specific API modules import this client
 */
import axios, { type AxiosError, type InternalAxiosRequestConfig } from 'axios';
import type { ApiError } from '@/types';
import { STORAGE_KEYS } from '../storage-keys';

// Use relative URL for portability - requests go through the proxy which forwards /api/ to backend
// This allows the app to work when accessed from any host (not just localhost)
const API_BASE_URL = import.meta.env.VITE_API_URL || '';

/** Maximum retries for transient server errors */
const MAX_RETRIES = 2;

/** Base delay for exponential backoff (ms) */
const RETRY_BASE_DELAY = 1000;

/** Status codes worth retrying (transient server/gateway errors) */
const RETRYABLE_STATUS_CODES = new Set([502, 503, 504]);

/** Guard against concurrent logout redirects from multiple failing queries */
let isRedirectingToLogin = false;

export const apiClient = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Request interceptor (add auth token)
apiClient.interceptors.request.use((config) => {
  const token = localStorage.getItem(STORAGE_KEYS.AUTH_TOKEN);
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Response interceptor (handle errors with retry for transient failures)
apiClient.interceptors.response.use(
  (response) => response,
  async (error: AxiosError<{ error: ApiError }>) => {
    const config = error.config as InternalAxiosRequestConfig & { _retryCount?: number };
    const status = error.response?.status;

    // Retry transient server errors with exponential backoff, but NOT when the
    // response body carries a structured domain-error envelope. A 502/503/504
    // with `{ error: { code, message } }` means the backend deliberately mapped
    // a downstream failure to that status — retrying just delays the user from
    // seeing the actionable error message.
    // Structured ApiError envelopes are surfaced immediately; transport/proxy HTML errors still retry.
    const hasStructuredError = typeof error.response?.data?.error?.code === 'string' && error.response.data.error.code.length > 0;
    if (config && status && RETRYABLE_STATUS_CODES.has(status) && !hasStructuredError) {
      const retryCount = config._retryCount ?? 0;
      if (retryCount < MAX_RETRIES) {
        config._retryCount = retryCount + 1;
        const delay = RETRY_BASE_DELAY * Math.pow(2, retryCount);
        await new Promise(resolve => setTimeout(resolve, delay));
        return apiClient(config);
      }
    }

    const code = error.response?.data?.error?.code;

    // Handle authentication failures only.
    // Important: some domain endpoints legitimately return HTTP 401 from
    // downstream systems (e.g. Kubernetes API Unauthorized) while the user
    // session is still valid. Only force logout when backend explicitly marks
    // the error as auth-layer UNAUTHORIZED.
    if (code === 'UNAUTHORIZED') {
      // Don't redirect if we're already on the login page or this is a login attempt
      const isLoginRequest = config?.url?.includes('/api/auth/login');
      if (!isLoginRequest && !isRedirectingToLogin) {
        isRedirectingToLogin = true;
        localStorage.removeItem(STORAGE_KEYS.AUTH_TOKEN);
        // Also clear the zustand persisted auth store to prevent
        // stale isAuthenticated=true on next page load
        localStorage.removeItem('auth-storage');
        window.location.href = '/login';
      }
    }

    // Don't show toast here - let individual hooks handle error display
    // This prevents duplicate error messages and allows for context-specific error handling

    return Promise.reject(error);
  }
);
