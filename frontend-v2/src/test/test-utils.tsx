/* eslint-disable react-refresh/only-export-components */
/**
 * Custom render function for tests
 *
 * Wraps components with the same providers used in the real app:
 * - QueryClientProvider (fresh QueryClient per test)
 * - BrowserRouter (for useNavigate, useParams, etc.)
 * - ThemeProvider
 *
 * Usage:
 *   import { render, screen } from '@/test/test-utils';
 *   render(<MyComponent />);
 */
import React, { type ReactElement } from 'react';
import { render, type RenderOptions } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { ThemeProvider } from '@/context/ThemeContext';

/**
 * Create a fresh QueryClient for each test to avoid cross-test contamination.
 * Disables retries and refetchOnWindowFocus for deterministic tests.
 */
function createTestQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: 0,
        staleTime: 0,
        refetchOnWindowFocus: false,
        refetchOnMount: false,
        refetchOnReconnect: false,
      },
      mutations: {
        retry: false,
      },
    },
  });
}

interface TestWrapperProps {
  children: React.ReactNode;
  initialRoute?: string;
  queryClient?: QueryClient;
}

/**
 * All-providers wrapper for testing.
 * Intentionally omits NotificationProvider/WebSocketProvider to avoid side effects.
 */
function TestWrapper({ children, initialRoute = '/', queryClient }: TestWrapperProps) {
  const [client] = React.useState(() => queryClient || createTestQueryClient());

  return (
    <QueryClientProvider client={client}>
      <ThemeProvider>
        <MemoryRouter initialEntries={[initialRoute]}>
          {children}
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>
  );
}

/**
 * Custom render that wraps component in all test providers.
 * Accepts optional `initialRoute` for routing-aware tests.
 */
function customRender(
  ui: ReactElement,
  options?: Omit<RenderOptions, 'wrapper'> & { initialRoute?: string; queryClient?: QueryClient }
) {
  const { initialRoute, queryClient, ...renderOptions } = options || {};
  const client = queryClient || createTestQueryClient();

  return render(ui, {
    wrapper: ({ children }) => (
      <TestWrapper initialRoute={initialRoute} queryClient={client}>{children}</TestWrapper>
    ),
    ...renderOptions,
  });
}

/**
 * Create a QueryClient for hook testing with renderHook.
 */
export { createTestQueryClient };

// Re-export everything from @testing-library/react
export * from '@testing-library/react';

// Override render with our custom version
export { customRender as render };
