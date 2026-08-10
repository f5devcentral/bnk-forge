/**
 * Tests for useAccessibility hooks
 *
 * Covers: useReducedMotion, useRouteAnnouncer, useScrollToTop.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useReducedMotion, useRouteAnnouncer, useScrollToTop } from '@/hooks/useAccessibility';
import React from 'react';
import { MemoryRouter } from 'react-router-dom';

// ========================================================================
// useReducedMotion
// ========================================================================

describe('useReducedMotion', () => {
  let matchMediaListeners: Map<string, (event: MediaQueryListEvent) => void>;
  let matchMediaMatches: boolean;

  beforeEach(() => {
    matchMediaListeners = new Map();
    matchMediaMatches = false;

    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        matches: matchMediaMatches,
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn((_event: string, handler: (event: MediaQueryListEvent) => void) => {
          matchMediaListeners.set(query, handler);
        }),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });
  });

  it('returns false when reduced motion is not preferred', () => {
    matchMediaMatches = false;
    const { result } = renderHook(() => useReducedMotion());
    expect(result.current).toBe(false);
  });

  it('returns true when reduced motion is preferred', () => {
    matchMediaMatches = true;
    const { result } = renderHook(() => useReducedMotion());
    expect(result.current).toBe(true);
  });

  it('reacts to preference changes', () => {
    matchMediaMatches = false;
    const { result } = renderHook(() => useReducedMotion());
    expect(result.current).toBe(false);

    // Simulate user toggling reduced motion
    const handler = matchMediaListeners.get('(prefers-reduced-motion: reduce)');
    expect(handler).toBeDefined();

    act(() => {
      handler!({ matches: true } as MediaQueryListEvent);
    });

    expect(result.current).toBe(true);
  });
});

// ========================================================================
// useRouteAnnouncer
// ========================================================================

describe('useRouteAnnouncer', () => {
  beforeEach(() => {
    // Reset matchMedia to default no-op mock for non-reduced-motion tests
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });
  });

  afterEach(() => {
    // Clean up any created route announcer elements
    const announcer = document.getElementById('route-announcer');
    if (announcer) announcer.remove();
  });

  it('creates an aria-live region on mount', () => {
    const wrapper = ({ children }: { children: React.ReactNode }) =>
      React.createElement(MemoryRouter, { initialEntries: ['/'] }, children);

    renderHook(() => useRouteAnnouncer(), { wrapper });

    const region = document.getElementById('route-announcer');
    expect(region).not.toBeNull();
    expect(region!.getAttribute('role')).toBe('status');
    expect(region!.getAttribute('aria-live')).toBe('polite');
    expect(region!.getAttribute('aria-atomic')).toBe('true');
    expect(region!.className).toBe('sr-only');
  });

  it('announces known route titles', async () => {
    vi.useFakeTimers();

    const wrapper = ({ children }: { children: React.ReactNode }) =>
      React.createElement(MemoryRouter, { initialEntries: ['/projects'] }, children);

    renderHook(() => useRouteAnnouncer(), { wrapper });

    act(() => {
      vi.advanceTimersByTime(200);
    });

    const region = document.getElementById('route-announcer');
    expect(region!.textContent).toBe('Navigated to Projects');

    vi.useRealTimers();
  });

  it('announces dynamic project detail routes', async () => {
    vi.useFakeTimers();

    const wrapper = ({ children }: { children: React.ReactNode }) =>
      React.createElement(MemoryRouter, { initialEntries: ['/projects/42'] }, children);

    renderHook(() => useRouteAnnouncer(), { wrapper });

    act(() => {
      vi.advanceTimersByTime(200);
    });

    const region = document.getElementById('route-announcer');
    expect(region!.textContent).toBe('Navigated to Project Detail');

    vi.useRealTimers();
  });

  it('falls back to "Page" for unknown routes', async () => {
    vi.useFakeTimers();

    const wrapper = ({ children }: { children: React.ReactNode }) =>
      React.createElement(MemoryRouter, { initialEntries: ['/unknown-route'] }, children);

    renderHook(() => useRouteAnnouncer(), { wrapper });

    act(() => {
      vi.advanceTimersByTime(200);
    });

    const region = document.getElementById('route-announcer');
    expect(region!.textContent).toBe('Navigated to Page');

    vi.useRealTimers();
  });

  it('removes the live region on unmount', () => {
    const wrapper = ({ children }: { children: React.ReactNode }) =>
      React.createElement(MemoryRouter, { initialEntries: ['/'] }, children);

    const { unmount } = renderHook(() => useRouteAnnouncer(), { wrapper });

    expect(document.getElementById('route-announcer')).not.toBeNull();

    unmount();

    expect(document.getElementById('route-announcer')).toBeNull();
  });
});

// ========================================================================
// useScrollToTop
// ========================================================================

describe('useScrollToTop', () => {
  beforeEach(() => {
    // Reset matchMedia
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });
  });

  it('scrolls main content area to top on route change', () => {
    const mockScrollTo = vi.fn();
    const mainEl = document.createElement('div');
    mainEl.id = 'main-content';
    mainEl.scrollTo = mockScrollTo;
    document.body.appendChild(mainEl);

    const wrapper = ({ children }: { children: React.ReactNode }) =>
      React.createElement(MemoryRouter, { initialEntries: ['/projects'] }, children);

    renderHook(() => useScrollToTop(), { wrapper });

    expect(mockScrollTo).toHaveBeenCalledWith({ top: 0, behavior: 'smooth' });

    mainEl.remove();
  });
});
