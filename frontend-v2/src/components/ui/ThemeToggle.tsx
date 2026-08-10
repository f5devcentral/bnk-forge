/**
 * Theme Toggle Component
 *
 * Floating button to switch between light and dark themes
 */

import { Moon, Sun } from 'lucide-react';
import { useTheme } from '@/context/ThemeContext';

export function ThemeToggle() {
  const { theme, toggleTheme, isDark } = useTheme();

  return (
    <button
      onClick={toggleTheme}
      className="fixed bottom-6 right-6 z-50 p-3 rounded-full shadow-lg transition-all bg-card hover:bg-muted text-foreground border border-border"
      title={`Switch to ${theme === 'light' ? 'dark' : 'light'} theme`}
    >
      {isDark ? (
        <Sun className="h-5 w-5" />
      ) : (
        <Moon className="h-5 w-5" />
      )}
    </button>
  );
}
