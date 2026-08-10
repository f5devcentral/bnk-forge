/**
 * FU-015: stores/uiStore — UI state management
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { useUIStore } from '../uiStore';

describe('useUIStore', () => {
  beforeEach(() => {
    // Reset store to defaults (D-020: default theme is light, dark is opt-in)
    useUIStore.setState({
      sidebarOpen: true,
      theme: 'light',
    });
  });

  it('has initial sidebar state = true', () => {
    expect(useUIStore.getState().sidebarOpen).toBe(true);
  });

  it('has initial theme = light', () => {
    expect(useUIStore.getState().theme).toBe('light');
  });

  it('setSidebarOpen changes sidebar state', () => {
    useUIStore.getState().setSidebarOpen(false);
    expect(useUIStore.getState().sidebarOpen).toBe(false);

    useUIStore.getState().setSidebarOpen(true);
    expect(useUIStore.getState().sidebarOpen).toBe(true);
  });

  it('toggleSidebar flips sidebar state', () => {
    expect(useUIStore.getState().sidebarOpen).toBe(true);

    useUIStore.getState().toggleSidebar();
    expect(useUIStore.getState().sidebarOpen).toBe(false);

    useUIStore.getState().toggleSidebar();
    expect(useUIStore.getState().sidebarOpen).toBe(true);
  });

  it('setTheme changes theme', () => {
    useUIStore.getState().setTheme('light');
    expect(useUIStore.getState().theme).toBe('light');

    useUIStore.getState().setTheme('dark');
    expect(useUIStore.getState().theme).toBe('dark');
  });

  it('toggleTheme flips between light and dark', () => {
    useUIStore.getState().setTheme('dark');
    useUIStore.getState().toggleTheme();
    expect(useUIStore.getState().theme).toBe('light');

    useUIStore.getState().toggleTheme();
    expect(useUIStore.getState().theme).toBe('dark');
  });
});
