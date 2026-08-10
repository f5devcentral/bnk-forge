/**
 * MSW Server Instance
 *
 * Creates a mock server for intercepting HTTP requests during tests.
 * Uses the default handlers from handlers.ts.
 */
import { setupServer } from 'msw/node';
import { handlers } from './handlers';

export const server = setupServer(...handlers);
