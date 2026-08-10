/**
 * Production-safe logger (IMP-022).
 *
 * In development mode, delegates to the browser console.
 * In production builds, all calls are no-ops to avoid leaking
 * internal details into end-user browser consoles.
 */

const isDev = import.meta.env.DEV;

/* eslint-disable @typescript-eslint/no-explicit-any */
function noop(..._args: any[]): void {}

export const logger = {
  error: isDev ? console.error.bind(console) : noop,
  warn: isDev ? console.warn.bind(console) : noop,
  info: isDev ? console.info.bind(console) : noop,
  debug: isDev ? console.debug.bind(console) : noop,
  log: isDev ? console.log.bind(console) : noop,
};
