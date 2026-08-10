/* eslint-disable react-refresh/only-export-components */
/**
 * Theme Context
 *
 * Provides a React context wrapper around the uiStore theme state.
 * This allows components to easily access theme information via useTheme() hook.
 */

import { createContext, useContext, ReactNode } from 'react';
import { useUIStore } from '@/stores/uiStore';

interface ThemeContextType {
  theme: 'light' | 'dark';
  toggleTheme: () => void;
  isDark: boolean;
}

const ThemeContext = createContext<ThemeContextType | undefined>(undefined);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const theme = useUIStore((state) => state.theme);
  const toggleTheme = useUIStore((state) => state.toggleTheme);

  return (
    <ThemeContext.Provider value={{ theme, toggleTheme, isDark: theme === 'dark' }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  const context = useContext(ThemeContext);
  if (context === undefined) {
    throw new Error('useTheme must be used within a ThemeProvider');
  }
  return context;
}

/**
 * Theme-aware class helper.
 *
 * D-020 — all returned strings are token-only (`bg-card`, `border-border`,
 * `text-muted-foreground`, etc.) so callers don't accidentally re-introduce
 * raw `zinc-*` palette. Themes flip via CSS variable overrides at the root.
 */
export function useThemeClasses() {
  return {
    // Backgrounds
    bgPage: 'bg-background',
    bgCard: 'bg-card',
    bgCardHover: 'hover:bg-muted/50',
    bgSidebar: 'bg-card',
    bgInput: 'bg-background',
    bgMuted: 'bg-muted/50',
    bgHeader: 'bg-background/80',

    // Borders
    borderDefault: 'border-border',
    borderMuted: 'border-border',
    borderHover: 'hover:border-border',

    // Text
    textPrimary: 'text-foreground',
    textSecondary: 'text-muted-foreground',
    textMuted: 'text-muted-foreground',
    textHover: 'hover:text-foreground',

    // Interactive
    hoverBg: 'hover:bg-muted/50',
    activeBg: 'bg-muted',

    // Special elements
    codeBlock: 'bg-muted text-foreground',
    ring: 'ring-border',
  };
}

/**
 * Dynamic theme class generator
 * Use this for inline conditional classes
 */
export function tc(lightClass: string, darkClass: string, isDark: boolean): string {
  return isDark ? darkClass : lightClass;
}
