import { useEffect, useState, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type { Task } from '@/types';

interface TaskUpdate {
  type: 'task_update' | 'ping' | 'pong';
  task_id?: number;
  status?: 'queued' | 'in_progress' | 'completed' | 'failed' | 'cancelled';
  project_id?: number;
  module_id?: number;
  task_type?: 'init' | 'plan' | 'apply' | 'destroy';
  timestamp?: string;
  exit_code?: number;
  error?: string;
  duration_seconds?: number;
  metadata?: Record<string, unknown>;
}

/**
 * WebSocket hook for real-time task updates.
 *
 * Connects to the backend WebSocket endpoint and updates React Query cache
 * when task status changes are received.
 *
 * Benefits:
 * - Instant feedback (no 5s polling delay)
 * - Reduced server load (90% fewer requests)
 * - Better UX (real-time progress)
 *
 * Fallback: If WebSocket fails, polling still works via useTasks hook
 */
export function useTaskWebSocket() {
  const queryClient = useQueryClient();
  const [isConnected, setIsConnected] = useState(false);
  const [gaveUp, setGaveUp] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<number | null>(null);
  const initialConnectTimeoutRef = useRef<number | null>(null);
  const reconnectAttemptsRef = useRef(0);

  useEffect(() => {
    const connect = () => {
      // Clear any pending reconnect
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }

      // Determine WebSocket URL based on current location
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${protocol}//${window.location.host}/api/tasks/ws/tasks`;

      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        setIsConnected(true);
        reconnectAttemptsRef.current = 0;

        // Send initial ping
        ws.send('ping');
      };

      ws.onmessage = (event) => {
        try {
          const update: TaskUpdate = JSON.parse(event.data);

          if (update.type === 'task_update' && update.task_id) {
            // Update specific task in cache
            queryClient.setQueryData(
              ['tasks', update.task_id],
              (oldData: Task | undefined) => {
                if (!oldData) return oldData;

                return {
                  ...oldData,
                  status: update.status ?? oldData.status,
                  exit_code: update.exit_code ?? oldData.exit_code,
                  error: update.error ?? oldData.error,
                  duration_seconds: update.duration_seconds ?? oldData.duration_seconds,
                  // Don't update completed_at here - let the API provide the full data
                };
              }
            );

            // Invalidate task list to refresh it (don't refetch immediately)
            queryClient.invalidateQueries({
              queryKey: ['tasks'],
              exact: false,
              refetchType: 'none',
            });

            // If task completed, trigger a refetch after a short delay
            // This ensures we get the full updated task data from the API
            if (
              update.status === 'completed' ||
              update.status === 'failed' ||
              update.status === 'cancelled'
            ) {
              setTimeout(() => {
                queryClient.invalidateQueries({
                  queryKey: ['tasks', update.task_id],
                });
                queryClient.invalidateQueries({
                  queryKey: ['tasks'],
                  exact: false,
                });
                // Project Detail / Modules pages key off these — without
                // invalidation the user has to manually refresh to see status
                // updates after a deploy completes.
                queryClient.invalidateQueries({ queryKey: ['project-modules'] });
                queryClient.invalidateQueries({ queryKey: ['projects'] });
                // ['parallel-executions'] key removed — no component subscribes to it
                // anymore (all consumers migrated to ['run-progress', ...])
              }, 500);
            }
          } else if (update.type === 'ping') {
            // Respond to server ping
            ws.send('ping');
          }
          // Ignore 'pong' messages (keepalive response)
        } catch {
          // Ignore malformed WebSocket messages
        }
      };

      ws.onclose = () => {
        setIsConnected(false);
        wsRef.current = null;

        // Reconnect with exponential backoff
        const maxAttempts = 5;
        if (reconnectAttemptsRef.current < maxAttempts) {
          const delay = Math.min(
            1000 * Math.pow(2, reconnectAttemptsRef.current),
            30000 // Max 30 seconds
          );

          reconnectTimeoutRef.current = setTimeout(() => {
            reconnectAttemptsRef.current += 1;
            connect();
          }, delay);
        } else {
          setGaveUp(true);
        }
      };

      ws.onerror = () => {
        // onclose will be called after onerror, which handles reconnection
      };
    };

    // Delay initial connect by one tick so React.StrictMode dev remount cleanup
    // does not create and immediately tear down a connecting socket.
    initialConnectTimeoutRef.current = window.setTimeout(() => {
      connect();
    }, 0);

    // Cleanup on unmount
    return () => {
      if (initialConnectTimeoutRef.current) {
        clearTimeout(initialConnectTimeoutRef.current);
        initialConnectTimeoutRef.current = null;
      }
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      if (wsRef.current) {
        wsRef.current.onopen = null;
        wsRef.current.onmessage = null;
        wsRef.current.onerror = null;
        wsRef.current.onclose = null;
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [queryClient]);

  return { isConnected, gaveUp };
}
