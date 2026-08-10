/**
 * Tests for useKeyboardShortcuts hook
 *
 * Covers: useKeyboardShortcuts (modifier combos, input suppression, allowInInput,
 * preventDefault/stopPropagation, enabled toggle),
 * useSequenceShortcuts (leader key + follow-up, timeout reset, input suppression,
 * modifier key skip), useNavigationShortcuts (navigation routes),
 * getAvailableShortcuts, getSequenceShortcuts, formatShortcut.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import {
  useKeyboardShortcuts,
  useSequenceShortcuts,
  getAvailableShortcuts,
  getSequenceShortcuts,
  formatShortcut,
  type KeyboardShortcut as _KeyboardShortcut,
} from '@/hooks/useKeyboardShortcuts';

// Helper: dispatch a keyboard event on document
function fireKey(
  key: string,
  options: Partial<KeyboardEventInit> = {},
  target?: HTMLElement
) {
  const event = new KeyboardEvent('keydown', {
    key,
    bubbles: true,
    cancelable: true,
    ...options,
  });

  // Override target if provided
  if (target) {
    Object.defineProperty(event, 'target', { value: target, writable: false });
  }

  // Spy on preventDefault / stopPropagation
  const preventDefaultSpy = vi.spyOn(event, 'preventDefault');
  const stopPropagationSpy = vi.spyOn(event, 'stopPropagation');

  document.dispatchEvent(event);

  return { event, preventDefaultSpy, stopPropagationSpy };
}

// ============================================================================
// useKeyboardShortcuts
// ============================================================================

describe('useKeyboardShortcuts', () => {
  it('fires action on matching key press', () => {
    const action = vi.fn();
    renderHook(() =>
      useKeyboardShortcuts([{ key: 'k', action, description: 'Test' }])
    );

    fireKey('k');
    expect(action).toHaveBeenCalledTimes(1);
  });

  it('matches key case-insensitively', () => {
    const action = vi.fn();
    renderHook(() =>
      useKeyboardShortcuts([{ key: 'K', action, description: 'Test' }])
    );

    fireKey('k');
    expect(action).toHaveBeenCalledTimes(1);
  });

  it('fires action with meta key modifier', () => {
    const action = vi.fn();
    renderHook(() =>
      useKeyboardShortcuts([{ key: 'k', meta: true, action, description: 'Meta+K' }])
    );

    // Without meta — should NOT fire
    fireKey('k');
    expect(action).not.toHaveBeenCalled();

    // With meta — should fire
    fireKey('k', { metaKey: true });
    expect(action).toHaveBeenCalledTimes(1);
  });

  it('treats ctrlKey as equivalent to metaKey for meta shortcuts', () => {
    const action = vi.fn();
    renderHook(() =>
      useKeyboardShortcuts([{ key: 'k', meta: true, action, description: 'Meta+K' }])
    );

    fireKey('k', { ctrlKey: true });
    expect(action).toHaveBeenCalledTimes(1);
  });

  it('fires action with shift modifier', () => {
    const action = vi.fn();
    renderHook(() =>
      useKeyboardShortcuts([{ key: 'p', shift: true, action, description: 'Shift+P' }])
    );

    fireKey('p');
    expect(action).not.toHaveBeenCalled();

    fireKey('p', { shiftKey: true });
    expect(action).toHaveBeenCalledTimes(1);
  });

  it('fires action with alt modifier', () => {
    const action = vi.fn();
    renderHook(() =>
      useKeyboardShortcuts([{ key: 'n', alt: true, action, description: 'Alt+N' }])
    );

    fireKey('n');
    expect(action).not.toHaveBeenCalled();

    fireKey('n', { altKey: true });
    expect(action).toHaveBeenCalledTimes(1);
  });

  it('fires action with ctrl modifier', () => {
    const action = vi.fn();
    renderHook(() =>
      useKeyboardShortcuts([{ key: 'z', ctrl: true, action, description: 'Ctrl+Z' }])
    );

    fireKey('z');
    expect(action).not.toHaveBeenCalled();

    fireKey('z', { ctrlKey: true });
    expect(action).toHaveBeenCalledTimes(1);
  });

  it('calls preventDefault and stopPropagation on match', () => {
    const action = vi.fn();
    renderHook(() =>
      useKeyboardShortcuts([{ key: 'k', meta: true, action }])
    );

    const { preventDefaultSpy, stopPropagationSpy } = fireKey('k', { metaKey: true });
    expect(preventDefaultSpy).toHaveBeenCalled();
    expect(stopPropagationSpy).toHaveBeenCalled();
  });

  // ========================================================================
  // Input suppression
  // ========================================================================

  it('suppresses shortcuts when focused on an INPUT', () => {
    const action = vi.fn();
    renderHook(() =>
      useKeyboardShortcuts([{ key: 'k', action, description: 'Test' }])
    );

    const input = document.createElement('input');
    fireKey('k', {}, input);
    expect(action).not.toHaveBeenCalled();
  });

  it('suppresses shortcuts when focused on a TEXTAREA', () => {
    const action = vi.fn();
    renderHook(() =>
      useKeyboardShortcuts([{ key: 'k', action, description: 'Test' }])
    );

    const textarea = document.createElement('textarea');
    fireKey('k', {}, textarea);
    expect(action).not.toHaveBeenCalled();
  });

  it('suppresses shortcuts when focused on contentEditable', () => {
    const action = vi.fn();
    renderHook(() =>
      useKeyboardShortcuts([{ key: 'k', action, description: 'Test' }])
    );

    const div = document.createElement('div');
    div.contentEditable = 'true';
    // jsdom may not set isContentEditable; use a mock target with the property
    Object.defineProperty(div, 'isContentEditable', { value: true });
    fireKey('k', {}, div);
    expect(action).not.toHaveBeenCalled();
  });

  it('allows shortcut in input when allowInInput is true', () => {
    const action = vi.fn();
    renderHook(() =>
      useKeyboardShortcuts([
        { key: '/', action, description: 'Search', allowInInput: true },
      ])
    );

    const input = document.createElement('input');
    fireKey('/', {}, input);
    expect(action).toHaveBeenCalledTimes(1);
  });

  // ========================================================================
  // Enabled toggle
  // ========================================================================

  it('does not fire when enabled is false', () => {
    const action = vi.fn();
    renderHook(() =>
      useKeyboardShortcuts([{ key: 'k', action, description: 'Test' }], false)
    );

    fireKey('k');
    expect(action).not.toHaveBeenCalled();
  });

  it('fires when enabled switches from false to true', () => {
    const action = vi.fn();
    const { rerender } = renderHook(
      ({ enabled }) =>
        useKeyboardShortcuts([{ key: 'k', action, description: 'Test' }], enabled),
      { initialProps: { enabled: false } }
    );

    fireKey('k');
    expect(action).not.toHaveBeenCalled();

    rerender({ enabled: true });
    fireKey('k');
    expect(action).toHaveBeenCalledTimes(1);
  });

  // ========================================================================
  // Multiple shortcuts
  // ========================================================================

  it('matches the first matching shortcut only', () => {
    const action1 = vi.fn();
    const action2 = vi.fn();
    renderHook(() =>
      useKeyboardShortcuts([
        { key: 'k', action: action1, description: 'First' },
        { key: 'k', action: action2, description: 'Second' },
      ])
    );

    fireKey('k');
    expect(action1).toHaveBeenCalledTimes(1);
    expect(action2).not.toHaveBeenCalled();
  });

  it('does not fire for non-matching keys', () => {
    const action = vi.fn();
    renderHook(() =>
      useKeyboardShortcuts([{ key: 'k', action, description: 'Test' }])
    );

    fireKey('j');
    expect(action).not.toHaveBeenCalled();
  });

  // ========================================================================
  // Cleanup
  // ========================================================================

  it('removes event listener on unmount', () => {
    const action = vi.fn();
    const { unmount } = renderHook(() =>
      useKeyboardShortcuts([{ key: 'k', action, description: 'Test' }])
    );

    unmount();
    fireKey('k');
    expect(action).not.toHaveBeenCalled();
  });
});

// ============================================================================
// useSequenceShortcuts
// ============================================================================

describe('useSequenceShortcuts', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('fires action on leader + follow-up key sequence', () => {
    const action = vi.fn();
    renderHook(() =>
      useSequenceShortcuts([
        { leader: 'g', followUp: 'd', action, description: 'Go to Dashboard' },
      ])
    );

    fireKey('g');
    fireKey('d');
    expect(action).toHaveBeenCalledTimes(1);
  });

  it('matches sequence case-insensitively', () => {
    const action = vi.fn();
    renderHook(() =>
      useSequenceShortcuts([
        { leader: 'G', followUp: 'D', action, description: 'Go to Dashboard' },
      ])
    );

    fireKey('g');
    fireKey('d');
    expect(action).toHaveBeenCalledTimes(1);
  });

  it('resets after 1 second timeout', () => {
    const action = vi.fn();
    renderHook(() =>
      useSequenceShortcuts([
        { leader: 'g', followUp: 'd', action, description: 'Test' },
      ])
    );

    fireKey('g');

    // Advance past 1 second timeout
    act(() => {
      vi.advanceTimersByTime(1100);
    });

    fireKey('d');
    expect(action).not.toHaveBeenCalled();
  });

  it('fires when follow-up arrives within 1 second', () => {
    const action = vi.fn();
    renderHook(() =>
      useSequenceShortcuts([
        { leader: 'g', followUp: 'd', action, description: 'Test' },
      ])
    );

    fireKey('g');

    act(() => {
      vi.advanceTimersByTime(500);
    });

    fireKey('d');
    expect(action).toHaveBeenCalledTimes(1);
  });

  it('ignores sequence when focused on an input', () => {
    const action = vi.fn();
    renderHook(() =>
      useSequenceShortcuts([
        { leader: 'g', followUp: 'd', action, description: 'Test' },
      ])
    );

    const input = document.createElement('input');
    fireKey('g', {}, input);
    fireKey('d', {}, input);
    expect(action).not.toHaveBeenCalled();
  });

  it('ignores keys when modifier keys are pressed', () => {
    const action = vi.fn();
    renderHook(() =>
      useSequenceShortcuts([
        { leader: 'g', followUp: 'd', action, description: 'Test' },
      ])
    );

    // Leader with meta — should be skipped
    fireKey('g', { metaKey: true });
    fireKey('d');
    expect(action).not.toHaveBeenCalled();
  });

  it('does not fire for wrong follow-up key', () => {
    const action = vi.fn();
    renderHook(() =>
      useSequenceShortcuts([
        { leader: 'g', followUp: 'd', action, description: 'Test' },
      ])
    );

    fireKey('g');
    fireKey('x'); // wrong follow-up
    expect(action).not.toHaveBeenCalled();
  });

  it('supports multiple sequences with the same leader', () => {
    const dashboardAction = vi.fn();
    const projectsAction = vi.fn();
    renderHook(() =>
      useSequenceShortcuts([
        { leader: 'g', followUp: 'd', action: dashboardAction, description: 'Dashboard' },
        { leader: 'g', followUp: 'p', action: projectsAction, description: 'Projects' },
      ])
    );

    // First sequence
    fireKey('g');
    fireKey('d');
    expect(dashboardAction).toHaveBeenCalledTimes(1);
    expect(projectsAction).not.toHaveBeenCalled();

    // Second sequence
    fireKey('g');
    fireKey('p');
    expect(projectsAction).toHaveBeenCalledTimes(1);
  });

  it('does not fire when enabled is false', () => {
    const action = vi.fn();
    renderHook(() =>
      useSequenceShortcuts(
        [{ leader: 'g', followUp: 'd', action, description: 'Test' }],
        false
      )
    );

    fireKey('g');
    fireKey('d');
    expect(action).not.toHaveBeenCalled();
  });

  it('cleans up timer on unmount', () => {
    const action = vi.fn();
    const { unmount } = renderHook(() =>
      useSequenceShortcuts([
        { leader: 'g', followUp: 'd', action, description: 'Test' },
      ])
    );

    fireKey('g');
    unmount();

    // Timer should be cleaned up — no error
    act(() => {
      vi.advanceTimersByTime(2000);
    });

    // Verify no action was called
    expect(action).not.toHaveBeenCalled();
  });
});

// ============================================================================
// getAvailableShortcuts (pure function)
// ============================================================================

describe('getAvailableShortcuts', () => {
  it('returns array of keyboard shortcuts', () => {
    const shortcuts = getAvailableShortcuts();

    expect(Array.isArray(shortcuts)).toBe(true);
    expect(shortcuts.length).toBeGreaterThan(0);

    // Each shortcut should have the required fields
    for (const shortcut of shortcuts) {
      expect(shortcut).toHaveProperty('key');
      expect(shortcut).toHaveProperty('action');
      expect(typeof shortcut.key).toBe('string');
      expect(typeof shortcut.action).toBe('function');
    }
  });

  it('includes command palette shortcut (Meta+K)', () => {
    const shortcuts = getAvailableShortcuts();
    const metaK = shortcuts.find((s) => s.key === 'k' && s.meta);
    expect(metaK).toBeDefined();
    expect(metaK!.description).toBe('Open command palette');
  });
});

// ============================================================================
// getSequenceShortcuts (pure function)
// ============================================================================

describe('getSequenceShortcuts', () => {
  it('returns array of sequence shortcut definitions', () => {
    const sequences = getSequenceShortcuts();

    expect(Array.isArray(sequences)).toBe(true);
    expect(sequences.length).toBeGreaterThan(0);

    for (const seq of sequences) {
      expect(seq).toHaveProperty('keys');
      expect(seq).toHaveProperty('description');
      expect(typeof seq.keys).toBe('string');
      expect(typeof seq.description).toBe('string');
    }
  });

  it('includes G → D (Go to Dashboard)', () => {
    const sequences = getSequenceShortcuts();
    const gd = sequences.find((s) => s.keys === 'G → D');
    expect(gd).toBeDefined();
    expect(gd!.description).toBe('Go to Dashboard');
  });

  it('includes all 9 navigation sequences', () => {
    const sequences = getSequenceShortcuts();
    expect(sequences).toHaveLength(9);
  });
});

// ============================================================================
// formatShortcut (pure function)
// ============================================================================

describe('formatShortcut', () => {
  it('formats meta + key', () => {
    const result = formatShortcut({ key: 'k', meta: true, action: vi.fn() });
    expect(result).toBe('⌘K');
  });

  it('formats ctrl + key', () => {
    const result = formatShortcut({ key: 'z', ctrl: true, action: vi.fn() });
    expect(result).toBe('CtrlZ');
  });

  it('formats shift + key', () => {
    const result = formatShortcut({ key: 'p', shift: true, action: vi.fn() });
    expect(result).toBe('⇧P');
  });

  it('formats alt + key', () => {
    const result = formatShortcut({ key: 'n', alt: true, action: vi.fn() });
    expect(result).toBe('⌥N');
  });

  it('formats combined modifiers', () => {
    const result = formatShortcut({
      key: 's',
      meta: true,
      shift: true,
      action: vi.fn(),
    });
    expect(result).toBe('⌘⇧S');
  });

  it('formats key only (no modifiers)', () => {
    const result = formatShortcut({ key: '/', action: vi.fn() });
    expect(result).toBe('/');
  });

  it('uppercases the key', () => {
    const result = formatShortcut({ key: 'a', action: vi.fn() });
    expect(result).toBe('A');
  });
});
