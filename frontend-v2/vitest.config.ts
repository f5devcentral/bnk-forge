/// <reference types="vitest" />
import { defineConfig } from 'vitest/config';
import path from 'path';

export default defineConfig({
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  define: {
    __APP_VERSION__: JSON.stringify('2.10.10-test'),
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json', 'html'],
      include: ['src/**/*.{ts,tsx}'],
      exclude: [
        'src/test/**',
        'src/**/*.d.ts',
        'src/vite-env.d.ts',
        'src/main.tsx',
      ],
      // Coverage thresholds — raised incrementally:
      //   Phase 1: 15% (baseline)
      //   Phase 2: 25% (current — 1,581 tests across 216 files)
      //   Phase 3: 40% (after hook/component tests)
      //   Phase 4: 60% (after full coverage sprint)
      thresholds: {
        statements: 25,
        branches: 15,
        functions: 15,
        lines: 25,
      },
    },
  },
});
