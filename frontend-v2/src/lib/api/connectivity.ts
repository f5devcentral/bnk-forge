/**
 * Connectivity / reachability API client.
 *
 * Three surfaces:
 * - getInitialState()  : one-shot snapshot for first-paint
 * - watchConnectivity(): SSE subscription using @microsoft/fetch-event-source
 *   (native EventSource can't set Authorization headers — required for
 *   our Bearer-token apiClient)
 * - forceProbe()       : retry button — bypass breaker, immediate probe
 */
import {
  fetchEventSource,
  type EventSourceMessage,
} from '@microsoft/fetch-event-source';
import { apiClient } from './client';
import { STORAGE_KEYS } from '../storage-keys';

export type ReachabilityStateValue = 'unknown' | 'reachable' | 'unreachable';

export type ErrorCategory =
  | 'network'
  | 'auth'
  | 'authz'
  | 'target'
  | 'timeout'
  | 'slow';

export interface ReachabilityState {
  target_type: string;
  target_id: number;
  target_name: string | null;
  state: ReachabilityStateValue;
  latency_ms: number | null;
  error_category: ErrorCategory | null;
  error_context: {
    target_name?: string;
    suggested_action?: string;
    last_success_at?: string | null;
    [k: string]: unknown;
  };
  checked_at: string | null;
}

export const reachabilityKey = (type: string, id: number) => `${type}:${id}`;

export async function getInitialState(): Promise<ReachabilityState[]> {
  const resp = await apiClient.get<{ states: ReachabilityState[] }>(
    '/api/connectivity/state',
  );
  return resp.data.states;
}

export async function forceProbe(
  targetType: string,
  targetId: number,
): Promise<ReachabilityState> {
  const resp = await apiClient.post<ReachabilityState>(
    `/api/connectivity/probe/${encodeURIComponent(targetType)}/${targetId}`,
  );
  return resp.data;
}

export interface WatchOptions {
  onState: (state: ReachabilityState) => void;
  onError?: (err: unknown) => void;
  signal?: AbortSignal;
}

export function watchConnectivity({
  onState,
  onError,
  signal,
}: WatchOptions): () => void {
  // Local controller chained to caller's signal so we can always abort cleanly.
  const controller = new AbortController();
  if (signal) {
    if (signal.aborted) controller.abort();
    else signal.addEventListener('abort', () => controller.abort(), { once: true });
  }

  const start = async () => {
    try {
      await fetchEventSource('/api/connectivity/watch', {
        signal: controller.signal,
        // Keep the connection alive when the tab is hidden — the whole point
        // is the user comes back to a screen that's already up to date.
        openWhenHidden: true,
        headers: authHeader(),
        onmessage(ev: EventSourceMessage) {
          if (!ev.data) return;
          try {
            onState(JSON.parse(ev.data) as ReachabilityState);
          } catch (err) {
            onError?.(err);
          }
        },
        onerror(err) {
          // Returning here lets the lib retry with backoff; throwing aborts.
          onError?.(err);
        },
      });
    } catch (err) {
      if (!controller.signal.aborted) onError?.(err);
    }
  };

  void start();
  return () => controller.abort();
}

function authHeader(): Record<string, string> {
  const token = localStorage.getItem(STORAGE_KEYS.AUTH_TOKEN);
  return token ? { Authorization: `Bearer ${token}` } : {};
}
