import React from 'react'
import ReactDOM from 'react-dom/client'
import { RouterProvider } from 'react-router-dom'
import { QueryClientProvider } from '@tanstack/react-query'

// Detect post-restore reload and force all queries to refetch.
// The _restored param is added by BackupPanel's reload logic.
const isPostRestore = new URL(window.location.href).searchParams.has('_restored')
if (isPostRestore) {
  const url = new URL(window.location.href)
  url.searchParams.delete('_restored')
  window.history.replaceState({}, '', url.toString())
}
import { queryClient } from './lib/queryClient'
import { router } from './router'
import { NotificationProvider } from './components/providers/NotificationProvider'
import { WebSocketProvider } from './components/providers/WebSocketProvider'
import { ThemeProvider } from './context/ThemeContext'
import { useUIStore } from './stores/uiStore'
import './styles.css'

// ---------------------------------------------------------------------------
// Monaco Editor: use locally bundled package instead of CDN.
// Without this, @monaco-editor/react fetches from jsDelivr which fails
// in air-gapped environments or behind firewalls (dialog shows "Loading..." forever).
// ---------------------------------------------------------------------------
import * as monaco from 'monaco-editor'
import editorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker'

self.MonacoEnvironment = {
  getWorker() {
    return new editorWorker()
  },
}

import { loader } from '@monaco-editor/react'
loader.config({ monaco })

// After a restore reload, nuke the entire query cache so no hook
// (even those with their own staleTime overrides) can serve pre-restore data.
if (isPostRestore) {
  queryClient.clear()
}

// Initialize theme from persisted state
const storedTheme = useUIStore.getState().theme
if (storedTheme === 'dark') {
  document.documentElement.classList.add('dark')
} else {
  document.documentElement.classList.remove('dark')
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <NotificationProvider>
          <WebSocketProvider>
            <RouterProvider
              router={router}
              future={{
                v7_startTransition: true,
              }}
            />
          </WebSocketProvider>
        </NotificationProvider>
      </ThemeProvider>
    </QueryClientProvider>
  </React.StrictMode>
)
