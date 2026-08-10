import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { User } from '@/types';
import { STORAGE_KEYS } from '@/lib/storage-keys';

interface AuthStore {
  user: User | null;
  /** Token kept in-memory for test access; canonical source is STORAGE_KEYS.AUTH_TOKEN */
  token: string | null;
  /** Derived from !!user — not stored independently */
  isAuthenticated: boolean;
  login: (token: string, user: User) => void;
  logout: () => void;
}

export const useAuthStore = create<AuthStore>()(
  persist(
    (set) => ({
      user: null,
      token: null,
      isAuthenticated: false,
      login: (token, user) => {
        // Canonical token location — read by API client interceptor
        localStorage.setItem(STORAGE_KEYS.AUTH_TOKEN, token);
        set({ token, user, isAuthenticated: true });
      },
      logout: () => {
        localStorage.removeItem(STORAGE_KEYS.AUTH_TOKEN);
        set({ token: null, user: null, isAuthenticated: false });
      },
    }),
    {
      name: 'auth-storage',
      // Only persist user — token lives in STORAGE_KEYS.AUTH_TOKEN (avoids dual-write desync)
      // isAuthenticated is derived from !!user on rehydration
      partialize: (state) => ({ user: state.user }),
      // Rehydrate derived state from persisted user
      onRehydrateStorage: () => (state) => {
        if (state) {
          const hasToken = !!localStorage.getItem(STORAGE_KEYS.AUTH_TOKEN);
          state.isAuthenticated = !!state.user && hasToken;
          // If user exists but token is gone (or vice versa), clean up
          if (state.user && !hasToken) {
            state.user = null;
            state.isAuthenticated = false;
          }
          if (!state.user && hasToken) {
            localStorage.removeItem(STORAGE_KEYS.AUTH_TOKEN);
            state.isAuthenticated = false;
          }
        }
      },
    }
  )
);
