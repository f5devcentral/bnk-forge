import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface UIStore {
  sidebarOpen: boolean;
  theme: 'light' | 'dark';
  _themeMigrated?: boolean;
  setSidebarOpen: (open: boolean) => void;
  toggleSidebar: () => void;
  setTheme: (theme: 'light' | 'dark') => void;
  toggleTheme: () => void;
}

export const useUIStore = create<UIStore>()(
  persist(
    (set) => ({
      sidebarOpen: true,
      theme: 'light',
      _themeMigrated: true,
      setSidebarOpen: (open) => set({ sidebarOpen: open }),
      toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),
      setTheme: (theme) => {
        set({ theme });
        if (theme === 'dark') {
          document.documentElement.classList.add('dark');
        } else {
          document.documentElement.classList.remove('dark');
        }
      },
      toggleTheme: () =>
        set((state) => {
          const newTheme = state.theme === 'light' ? 'dark' : 'light';
          if (newTheme === 'dark') {
            document.documentElement.classList.add('dark');
          } else {
            document.documentElement.classList.remove('dark');
          }
          return { theme: newTheme };
        }),
    }),
    {
      name: 'ui-storage',
      version: 2,
      migrate: (persistedState: unknown, _version: number) => {
        // D-020: default theme is LIGHT; dark is opt-in.
        // Earlier builds (v1) force-defaulted unset users to dark — undo that.
        // Preserve a theme the user explicitly toggled to (persisted as
        // state.theme === 'dark'); only resolve the unset/invalid case to light.
        const state = persistedState as Record<string, unknown> | null;
        if (state && state.theme !== 'dark') {
          state.theme = 'light';
        }
        return state;
      },
    }
  )
);
