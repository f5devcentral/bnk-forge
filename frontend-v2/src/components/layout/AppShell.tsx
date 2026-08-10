import { useState, Suspense } from 'react';
import { Outlet, useNavigate } from 'react-router-dom';
import { Sidebar } from './Sidebar';
import { Header } from './Header';

import { CommandPalette } from '@/components/CommandPalette';
import { KeyboardShortcutsModal } from '@/components/KeyboardShortcutsModal';
import { ThemeToggle } from '@/components/ui/ThemeToggle';
import { useKeyboardShortcuts, useNavigationShortcuts } from '@/hooks/useKeyboardShortcuts';
import { useRouteAnnouncer, useScrollToTop } from '@/hooks/useAccessibility';
import { useConnectivitySession } from '@/hooks/useConnectivity';
import { Loader2 } from 'lucide-react';

export function AppShell() {
  const navigate = useNavigate();
  const [commandPaletteOpen, setCommandPaletteOpen] = useState(false);
  const [shortcutsModalOpen, setShortcutsModalOpen] = useState(false);

  // Global keyboard shortcuts
  useKeyboardShortcuts([
    {
      key: 'k',
      meta: true,
      action: () => setCommandPaletteOpen(true),
      description: 'Open command palette',
    },
    {
      key: '/',
      meta: true,
      action: () => setShortcutsModalOpen((prev) => !prev),
      description: 'Show keyboard shortcuts',
    },
    {
      key: '?',
      shift: true,
      action: () => setShortcutsModalOpen((prev) => !prev),
      description: 'Show keyboard shortcuts',
    },
    {
      key: 'n',
      meta: true,
      action: () => navigate('/'),
      description: 'Go to Dashboard',
    },
  ]);

  // Navigation shortcuts
  useNavigationShortcuts();

  // Accessibility: announce route changes to screen readers & scroll to top
  useRouteAnnouncer();
  useScrollToTop();

  // Single SSE subscription for the whole authenticated session.
  useConnectivitySession();

  return (
    <div className="flex h-screen bg-background">
      {/* B5: Skip navigation link for keyboard/screen reader users */}
      <a href="#main-content" className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:p-4 focus:bg-background focus:text-foreground focus:border focus:rounded-md focus:top-2 focus:left-2">
        Skip to main content
      </a>
      <Sidebar />
      <div className="flex flex-col flex-1 overflow-hidden">
        <Header />
        <main id="main-content" className="flex-1 overflow-y-auto p-6">
          <Suspense
            fallback={
              <div className="flex items-center justify-center h-full">
                <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
              </div>
            }
          >
            <div className="motion-safe:animate-page-enter">
              <Outlet />
            </div>
          </Suspense>
        </main>
      </div>

      {/* Command Palette */}
      <CommandPalette
        open={commandPaletteOpen}
        onOpenChange={setCommandPaletteOpen}
      />

      {/* Keyboard Shortcuts Modal */}
      <KeyboardShortcutsModal
        open={shortcutsModalOpen}
        onOpenChange={setShortcutsModalOpen}
      />

      {/* Theme Toggle */}
      <ThemeToggle />
    </div>
  );
}
