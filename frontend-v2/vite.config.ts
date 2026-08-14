import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'
import fs from 'fs'

const devProxyTarget = process.env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000'
const devProxySecure = devProxyTarget.startsWith('https://')

// Read version from VERSION file (check multiple locations for Docker compatibility)
let version = '2.0.0' // fallback
const versionPaths = [
  path.resolve(__dirname, '../VERSION'),  // Local dev (repo root)
  path.resolve(__dirname, 'VERSION'),     // Docker build (copied to frontend-v2)
  '/VERSION'                              // Docker absolute path
]
for (const versionPath of versionPaths) {
  if (fs.existsSync(versionPath)) {
    version = fs.readFileSync(versionPath, 'utf-8').trim()
    break
  }
}

export default defineConfig({
  plugins: [react()],
  define: {
    __APP_VERSION__: JSON.stringify(version)
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src')
    }
  },
  // Required for monaco-editor ?worker imports
  worker: {
    format: 'es',
  },
  server: {
    port: 5173,
    // WSL2 + Windows-mounted /mnt/* drives don't propagate inotify events,
    // so chokidar misses edits made from outside Vite's process. Polling
    // is the only reliable way to pick up changes on this filesystem.
    watch: {
      // Polling is only needed for WSL2/mnt filesystems (per comment above).
      // On macOS it causes spurious page-reload loops when HMR falls back to
      // full reload (e.g. Fast Refresh incompatible export); FSEvents is fine.
      usePolling: process.platform !== 'darwin',
      interval: 300,
    },
    proxy: {
      '/api': {
        target: devProxyTarget,
        changeOrigin: true,
        ws: true,
        secure: devProxySecure,
      }
    }
  },
  build: {
    outDir: 'dist',
    // DEVOPS-005: Disable source maps in production to avoid exposing source code
    // Set VITE_SOURCEMAP=true during build to enable for debugging
    sourcemap: process.env.VITE_SOURCEMAP === 'true',
    rollupOptions: {
      // monaco-editor ?worker imports are resolved at runtime via MonacoEnvironment.getWorker
      onwarn(warning, defaultHandler) {
        if (warning.code === 'UNRESOLVED_IMPORT' && warning.message?.includes('monaco-editor')) return;
        defaultHandler(warning);
      },
      output: {
        manualChunks: {
          // React core - rarely changes
          'vendor-react': ['react', 'react-dom', 'react-router-dom'],

          // React Query - data fetching library
          'vendor-query': ['@tanstack/react-query'],

          // UI components - large but stable (all Radix primitives in one chunk)
          'vendor-ui': [
            '@radix-ui/react-alert-dialog',
            '@radix-ui/react-avatar',
            '@radix-ui/react-checkbox',
            '@radix-ui/react-collapsible',
            '@radix-ui/react-dialog',
            '@radix-ui/react-dropdown-menu',
            '@radix-ui/react-label',
            '@radix-ui/react-popover',
            '@radix-ui/react-progress',
            '@radix-ui/react-radio-group',
            '@radix-ui/react-scroll-area',
            '@radix-ui/react-select',
            '@radix-ui/react-separator',
            '@radix-ui/react-slider',
            '@radix-ui/react-slot',
            '@radix-ui/react-switch',
            '@radix-ui/react-tabs',
            '@radix-ui/react-toast',
            '@radix-ui/react-tooltip',
            '@radix-ui/react-visually-hidden',
            'lucide-react',
            'cmdk'
          ],

          // Monaco Editor - very large, only used in K8s resource management
          // Includes both the core editor and the React wrapper
          'vendor-monaco': ['@monaco-editor/react', 'monaco-editor'],

          // React Flow - only used in dependency graphs
          'vendor-flow': ['reactflow'],

          // Terminal - only used in K8s pods
          'vendor-terminal': ['@xterm/xterm', '@xterm/addon-fit'],

          // Forms - used across multiple pages
          'vendor-forms': [
            'react-hook-form',
            '@hookform/resolvers',
            'zod'
          ],

          // Utilities - small but shared
          'vendor-utils': [
            'axios',
            'clsx',
            'tailwind-merge',
            'class-variance-authority',
            'js-yaml',
            'zustand'
          ]
        }
      }
    }
  }
})
