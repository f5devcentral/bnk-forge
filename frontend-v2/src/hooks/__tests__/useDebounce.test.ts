/**
 * Tests for useDebounce hook
 *
 * Covers: initial value return, delayed updates after timeout,
 * no premature updates, timer reset on rapid value changes,
 * and default 300ms delay behavior.
 *
 * This is a pure React hook — no React Query or MSW needed.
 * Uses vi.useFakeTimers() for deterministic timing control.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useDebounce } from '@/hooks/useDebounce';

describe('useDebounce', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('returns initial value immediately', () => {
    const { result } = renderHook(() => useDebounce('hello', 500));

    expect(result.current).toBe('hello');
  });

  it('updates value after delay', () => {
    const { result, rerender } = renderHook(
      ({ value }) => useDebounce(value, 500),
      { initialProps: { value: 'hello' } }
    );

    rerender({ value: 'world' });
    expect(result.current).toBe('hello');

    act(() => {
      vi.advanceTimersByTime(500);
    });

    expect(result.current).toBe('world');
  });

  it('does not update before delay elapses', () => {
    const { result, rerender } = renderHook(
      ({ value }) => useDebounce(value, 500),
      { initialProps: { value: 'initial' } }
    );

    rerender({ value: 'updated' });

    // Advance by less than the delay
    act(() => {
      vi.advanceTimersByTime(300);
    });

    expect(result.current).toBe('initial');

    // Advance the remaining time
    act(() => {
      vi.advanceTimersByTime(200);
    });

    expect(result.current).toBe('updated');
  });

  it('resets timer on rapid changes and only applies the last value', () => {
    const { result, rerender } = renderHook(
      ({ value }) => useDebounce(value, 500),
      { initialProps: { value: 'a' } }
    );

    // Rapid changes before the delay expires
    rerender({ value: 'b' });
    act(() => {
      vi.advanceTimersByTime(200);
    });
    expect(result.current).toBe('a');

    rerender({ value: 'c' });
    act(() => {
      vi.advanceTimersByTime(200);
    });
    expect(result.current).toBe('a');

    rerender({ value: 'd' });
    act(() => {
      vi.advanceTimersByTime(200);
    });
    // Still the original — each rerender resets the timer
    expect(result.current).toBe('a');

    // Now let the full delay elapse from the last change
    act(() => {
      vi.advanceTimersByTime(300);
    });

    // Only the final value should be applied
    expect(result.current).toBe('d');
  });

  it('uses default 300ms delay when none is specified', () => {
    const { result, rerender } = renderHook(
      ({ value }) => useDebounce(value),
      { initialProps: { value: 'start' } }
    );

    rerender({ value: 'end' });
    expect(result.current).toBe('start');

    // Just before default delay
    act(() => {
      vi.advanceTimersByTime(299);
    });
    expect(result.current).toBe('start');

    // At exactly the default delay
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(result.current).toBe('end');
  });
});
