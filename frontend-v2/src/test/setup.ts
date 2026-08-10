/**
 * Test setup file for Vitest
 *
 * Configures:
 * - @testing-library/jest-dom matchers
 * - MSW server for API mocking
 * - Global browser API mocks (matchMedia, ResizeObserver, IntersectionObserver)
 */
import '@testing-library/jest-dom';
import { afterAll, afterEach, beforeAll } from 'vitest';
import { cleanup } from '@testing-library/react';
import { server } from './mocks/server';

// Start MSW server before all tests
beforeAll(() => server.listen({ onUnhandledRequest: 'warn' }));

// Reset handlers after each test (so individual tests can override)
afterEach(() => {
  cleanup();
  server.resetHandlers();
});

// Clean up after all tests
afterAll(() => server.close());

// ============================================================================
// Global Browser API Mocks
// ============================================================================

// Mock window.matchMedia (used by Radix UI, Tailwind, dark mode detection)
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});

// Mock ResizeObserver (used by Radix UI popovers, dialogs)
class MockResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
window.ResizeObserver = MockResizeObserver as unknown as typeof ResizeObserver;

// Mock IntersectionObserver (used by lazy loading, infinite scroll)
class MockIntersectionObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
window.IntersectionObserver = MockIntersectionObserver as unknown as typeof IntersectionObserver;

// Mock WebSocket. jsdom implements WebSocket but actually attempts to dial the URL,
// and any component constructing `new WebSocket(...)` (e.g. SystemUpgrade) leaks an
// async connection error after the test unmounts — surfacing as a worker-killing
// AggregateError from MSW's MockHttpSocket. A no-op stub keeps the constructor
// signature intact without doing any real I/O.
class MockWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  readonly CONNECTING = 0;
  readonly OPEN = 1;
  readonly CLOSING = 2;
  readonly CLOSED = 3;
  readyState = MockWebSocket.CLOSED;
  url = '';
  onopen: ((ev: Event) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  constructor(url: string | URL) { this.url = String(url); }
  send() {}
  close() {}
  addEventListener() {}
  removeEventListener() {}
  dispatchEvent() { return false; }
}
window.WebSocket = MockWebSocket as unknown as typeof WebSocket;

// Mock scrollIntoView (used by Radix UI Select to scroll to selected item;
// not implemented in jsdom and throws "candidate?.scrollIntoView is not a function")
window.HTMLElement.prototype.scrollIntoView = () => {};

// Mock localStorage / sessionStorage. Node 25 + jsdom combo leaves these
// exposed but with no working Storage prototype, so `getItem` / `setItem`
// fail as "not a function". Provide a minimal in-memory Storage shim.
function makeStorage(): Storage {
  let store: Record<string, string> = {};
  return {
    get length() { return Object.keys(store).length; },
    key: (i: number) => Object.keys(store)[i] ?? null,
    getItem: (k: string) => (k in store ? store[k] : null),
    setItem: (k: string, v: string) => { store[k] = String(v); },
    removeItem: (k: string) => { delete store[k]; },
    clear: () => { store = {}; },
  };
}
Object.defineProperty(window, 'localStorage', { value: makeStorage(), writable: true, configurable: true });
Object.defineProperty(window, 'sessionStorage', { value: makeStorage(), writable: true, configurable: true });

// Guard EventTarget.dispatchEvent against late, post-teardown events. Radix UI's
// focus-scope schedules a `setTimeout(0)` on unmount that does
// `container.dispatchEvent(new CustomEvent(...))` (react-focus-scope index.mjs:90-92).
// When that timer fires after a test has finished and jsdom is tearing the realm
// down, the CustomEvent is no longer a valid Event in the dying realm and
// dispatchEvent throws "parameter 1 is not of type 'Event'" — a worker-killing
// unhandled error that fails the whole run even though every test passed. Drop any
// dispatch whose argument isn't a real Event; legitimate dispatches always pass one.
const realDispatchEvent = EventTarget.prototype.dispatchEvent;
EventTarget.prototype.dispatchEvent = function (event: Event) {
  if (!(event instanceof Event)) return false;
  return realDispatchEvent.call(this, event);
};

// Suppress console.error/warn noise in tests (optional, but keeps output clean)
// Uncomment if you want silent tests:
// const originalError = console.error;
// console.error = (...args: unknown[]) => {
//   if (typeof args[0] === 'string' && args[0].includes('Warning:')) return;
//   originalError.call(console, ...args);
// };
