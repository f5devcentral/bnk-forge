/**
 * Tests for useFocusTrap hook
 *
 * Covers: focus trap activation, Tab/Shift+Tab cycling,
 * inactive state, and cleanup.
 */
import { describe, it, expect, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useFocusTrap } from '@/hooks/useFocusTrap';

describe('useFocusTrap', () => {
  function createContainer() {
    const container = document.createElement('div');

    const button1 = document.createElement('button');
    button1.textContent = 'First';
    const input = document.createElement('input');
    input.placeholder = 'Middle';
    const button2 = document.createElement('button');
    button2.textContent = 'Last';

    container.appendChild(button1);
    container.appendChild(input);
    container.appendChild(button2);
    document.body.appendChild(container);

    return { container, button1, input, button2 };
  }

  it('returns a ref object', () => {
    const { result } = renderHook(() => useFocusTrap(false));
    expect(result.current).toBeDefined();
    expect(result.current.current).toBeNull();
  });

  it('focuses the first focusable element when activated', () => {
    const { container, button1 } = createContainer();
    const focusSpy = vi.spyOn(button1, 'focus');

    const { result, rerender } = renderHook(
      ({ isActive }) => useFocusTrap<HTMLDivElement>(isActive),
      { initialProps: { isActive: false } }
    );

    // Manually assign the container to the ref
    (result.current as unknown as { current: HTMLDivElement }).current = container;

    // Activate the trap
    rerender({ isActive: true });

    expect(focusSpy).toHaveBeenCalled();

    container.remove();
  });

  it('wraps focus from last to first on Tab', () => {
    const { container, button1, button2 } = createContainer();
    const focusSpy = vi.spyOn(button1, 'focus');

    const { result, rerender } = renderHook(
      ({ isActive }) => useFocusTrap<HTMLDivElement>(isActive),
      { initialProps: { isActive: false } }
    );

    (result.current as unknown as { current: HTMLDivElement }).current = container;
    rerender({ isActive: true });

    // Clear focus calls from activation
    focusSpy.mockClear();

    // Simulate: active element is the last button, user presses Tab
    Object.defineProperty(document, 'activeElement', {
      value: button2,
      writable: true,
      configurable: true,
    });

    const tabEvent = new KeyboardEvent('keydown', {
      key: 'Tab',
      shiftKey: false,
      bubbles: true,
    });
    const preventDefaultSpy = vi.spyOn(tabEvent, 'preventDefault');

    act(() => {
      container.dispatchEvent(tabEvent);
    });

    expect(preventDefaultSpy).toHaveBeenCalled();
    expect(focusSpy).toHaveBeenCalled();

    // Reset activeElement
    Object.defineProperty(document, 'activeElement', {
      value: document.body,
      writable: true,
      configurable: true,
    });

    container.remove();
  });

  it('wraps focus from first to last on Shift+Tab', () => {
    const { container, button1, button2 } = createContainer();
    const lastFocusSpy = vi.spyOn(button2, 'focus');

    const { result, rerender } = renderHook(
      ({ isActive }) => useFocusTrap<HTMLDivElement>(isActive),
      { initialProps: { isActive: false } }
    );

    (result.current as unknown as { current: HTMLDivElement }).current = container;
    rerender({ isActive: true });

    lastFocusSpy.mockClear();

    // Simulate: active element is the first button, user presses Shift+Tab
    Object.defineProperty(document, 'activeElement', {
      value: button1,
      writable: true,
      configurable: true,
    });

    const shiftTabEvent = new KeyboardEvent('keydown', {
      key: 'Tab',
      shiftKey: true,
      bubbles: true,
    });
    const preventDefaultSpy = vi.spyOn(shiftTabEvent, 'preventDefault');

    act(() => {
      container.dispatchEvent(shiftTabEvent);
    });

    expect(preventDefaultSpy).toHaveBeenCalled();
    expect(lastFocusSpy).toHaveBeenCalled();

    Object.defineProperty(document, 'activeElement', {
      value: document.body,
      writable: true,
      configurable: true,
    });

    container.remove();
  });

  it('does not trap focus when inactive', () => {
    const { container, button1 } = createContainer();
    const focusSpy = vi.spyOn(button1, 'focus');

    const { result } = renderHook(
      () => useFocusTrap<HTMLDivElement>(false),
    );

    (result.current as unknown as { current: HTMLDivElement }).current = container;

    // Focus should NOT be called since trap is inactive
    expect(focusSpy).not.toHaveBeenCalled();

    container.remove();
  });

  it('does nothing when container has no focusable elements', () => {
    const container = document.createElement('div');
    container.innerHTML = '<p>No focusable elements</p>';
    document.body.appendChild(container);

    const { result, rerender } = renderHook(
      ({ isActive }) => useFocusTrap<HTMLDivElement>(isActive),
      { initialProps: { isActive: false } }
    );

    (result.current as unknown as { current: HTMLDivElement }).current = container;

    // Should not throw
    expect(() => {
      rerender({ isActive: true });
    }).not.toThrow();

    container.remove();
  });
});
