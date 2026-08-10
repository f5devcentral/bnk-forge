import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Loader2, RefreshCw, Download, Search, Terminal, Box, Play, Square, Wifi, WifiOff } from 'lucide-react';
import type { K8sResource, K8sPodContainer } from '@/types';
import { usePodContainers, usePodLogs } from '@/hooks/useK8s';
import { logger } from '@/lib/logger';

type ConnectionStatus = 'disconnected' | 'connecting' | 'connected' | 'error';

/** Max number of streamed log lines to keep in memory to prevent unbounded growth. */
const MAX_STREAMED_LINES = 50_000;

/** Interval (ms) for batching incoming WebSocket log lines into state. */
const LOG_BATCH_INTERVAL_MS = 100;

interface PodLogsViewerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  pod: K8sResource | null;
  clusterId: number;
  namespace: string;
}

/**
 * Get a JWT token for WebSocket authentication.
 * Reads from localStorage where the auth system stores it.
 */
function getAuthToken(): string | null {
  try {
    const token = localStorage.getItem('auth_token');
    return token || null;
  } catch {
    return null;
  }
}

export function PodLogsViewer({
  open,
  onOpenChange,
  pod,
  clusterId,
  namespace,
}: PodLogsViewerProps) {
  const [searchQuery, setSearchQuery] = useState('');
  const [autoScroll, setAutoScroll] = useState(true);
  const [selectedContainer, setSelectedContainer] = useState<string>('');
  const [isFollowing, setIsFollowing] = useState(false);
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>('disconnected');
  const [streamedLogs, setStreamedLogs] = useState<string[]>([]);

  // Native scroll container ref
  const scrollRef = useRef<HTMLDivElement>(null);
  const wsRef = useRef<WebSocket | null>(null);

  // Batch buffer for incoming WebSocket log lines to avoid per-line re-renders
  const logBatchRef = useRef<string[]>([]);
  const batchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Fetch containers
  const { data: containersData } = usePodContainers(
    clusterId,
    pod?.metadata?.name || '',
    namespace,
    { enabled: open && !!pod }
  );

  const containers = useMemo(() => containersData?.containers || [], [containersData?.containers]);
  const initContainers = useMemo(() => containersData?.init_containers || [], [containersData?.init_containers]);

  // Auto-select first container
  useEffect(() => {
    if (containers.length > 0 && !selectedContainer) {
      setSelectedContainer(containers[0].name);
    }
  }, [containers, selectedContainer]);

  // Fetch logs for selected container (used when not following)
  const { data: logsData, isLoading, refetch } = usePodLogs(
    clusterId,
    pod?.metadata?.name || '',
    namespace,
    {
      container: selectedContainer,
      tail_lines: 1000,
      enabled: open && !!pod && !!selectedContainer && !isFollowing,
    }
  );

  const httpLogs = logsData?.logs || null;
  
  // Use streamed logs when following, HTTP logs otherwise.
  // Each streamed line is a single log entry from the WebSocket — join with newlines.
  const logs = isFollowing ? streamedLogs.join('\n') : httpLogs;

  /**
   * Flush buffered log lines into state in a single batch.
   * Caps total lines at MAX_STREAMED_LINES to prevent unbounded memory growth.
   */
  const flushLogBatch = useCallback(() => {
    const batch = logBatchRef.current;
    if (batch.length === 0) return;
    logBatchRef.current = [];

    setStreamedLogs(prev => {
      const combined = [...prev, ...batch];
      // Trim from the front if we exceed the cap
      if (combined.length > MAX_STREAMED_LINES) {
        return combined.slice(combined.length - MAX_STREAMED_LINES);
      }
      return combined;
    });
  }, []);

  // Disconnect WebSocket
  const disconnectWebSocket = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    // Flush any remaining buffered lines
    flushLogBatch();
    if (batchTimerRef.current) {
      clearInterval(batchTimerRef.current);
      batchTimerRef.current = null;
    }
    setConnectionStatus('disconnected');
  }, [flushLogBatch]);

  // WebSocket connection for real-time log streaming
  const connectWebSocket = useCallback(() => {
    if (!pod?.metadata?.name || !selectedContainer) return;

    // Close existing connection
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }

    setConnectionStatus('connecting');
    setStreamedLogs([]);
    logBatchRef.current = [];

    // Determine WebSocket protocol (ws or wss)
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;

    // Build WebSocket URL — include JWT token for authentication
    const params = new URLSearchParams({
      namespace,
      container: selectedContainer,
      tail_lines: '100',
    });
    const token = getAuthToken();
    if (token) {
      params.set('token', token);
    }
    const wsUrl = `${protocol}//${host}/ws/k8s/clusters/${clusterId}/pods/${encodeURIComponent(pod.metadata.name)}/logs/follow?${params.toString()}`;

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnectionStatus('connected');
      // Start batch flush timer
      if (batchTimerRef.current) clearInterval(batchTimerRef.current);
      batchTimerRef.current = setInterval(flushLogBatch, LOG_BATCH_INTERVAL_MS);
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);

        switch (msg.type) {
          case 'connected':
            // Connection acknowledged
            break;

          case 'log':
            // Buffer the line — it will be flushed in batches.
            // Strip trailing newline since we join with '\n' later.
            logBatchRef.current.push(
              typeof msg.data === 'string' ? msg.data.replace(/\n$/, '') : msg.data
            );
            break;

          case 'error':
            setConnectionStatus('error');
            logger.error('Log stream error:', msg.message);
            break;
        }
      } catch (_e) {
        // Plain text log line (shouldn't happen with our protocol, but handle it)
        logBatchRef.current.push(
          typeof event.data === 'string' ? event.data.replace(/\n$/, '') : event.data
        );
      }
    };

    ws.onerror = (error) => {
      logger.error('WebSocket error:', error);
      setConnectionStatus('error');
    };

    ws.onclose = () => {
      // Flush any remaining buffered lines on close
      flushLogBatch();
      if (batchTimerRef.current) {
        clearInterval(batchTimerRef.current);
        batchTimerRef.current = null;
      }
      setConnectionStatus('disconnected');
    };
  }, [pod?.metadata?.name, selectedContainer, clusterId, namespace, flushLogBatch]);

  // Handle follow toggle — single entry point, no duplicate useEffect
  const handleFollowToggle = useCallback(() => {
    if (isFollowing) {
      // Stop following
      disconnectWebSocket();
      setIsFollowing(false);
      // Refetch HTTP logs
      refetch();
    } else {
      // Start following
      setIsFollowing(true);
      connectWebSocket();
    }
  }, [isFollowing, connectWebSocket, disconnectWebSocket, refetch]);

  // Cleanup: stop following and disconnect when dialog closes
  useEffect(() => {
    if (!open) {
      setIsFollowing(false);
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
      if (batchTimerRef.current) {
        clearInterval(batchTimerRef.current);
        batchTimerRef.current = null;
      }
      setStreamedLogs([]);
      logBatchRef.current = [];
    }
  }, [open]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
      if (batchTimerRef.current) {
        clearInterval(batchTimerRef.current);
        batchTimerRef.current = null;
      }
    };
  }, []);

  // Reset streamed logs when container changes while following
  useEffect(() => {
    if (isFollowing) {
      setStreamedLogs([]);
      logBatchRef.current = [];
      connectWebSocket();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps -- intentionally only fires on container change
  }, [selectedContainer]);

  // Auto-scroll: set scrollTop directly on the scroll container.
  // scrollIntoView can misbehave inside fixed/transformed Radix dialogs.
  useEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [logs, autoScroll]);

  // Detect manual scroll: disable auto-scroll when user scrolls up,
  // re-enable when user scrolls back to the bottom.
  const handleScroll = useCallback(() => {
    if (!scrollRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = scrollRef.current;
    const isAtBottom = scrollHeight - scrollTop - clientHeight < 50;
    if (isAtBottom && !autoScroll) {
      setAutoScroll(true);
    } else if (!isAtBottom && autoScroll) {
      setAutoScroll(false);
    }
  }, [autoScroll]);

  if (!pod) return null;

  const filteredLogs = logs
    ? logs
        .split('\n')
        .filter((line) =>
          !searchQuery || line.toLowerCase().includes(searchQuery.toLowerCase())
        )
        .join('\n')
    : '';

  const handleDownload = () => {
    if (!logs) return;

    const blob = new Blob([logs], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${pod.metadata.name}-logs.txt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-6xl h-[90vh] overflow-hidden flex flex-col" aria-describedby={undefined}>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Terminal className="h-5 w-5" />
            Pod Logs: {pod.metadata.name}
          </DialogTitle>
        </DialogHeader>

        <div className="flex flex-col gap-2 py-2">
          {/* Container Selector - only show if multiple containers */}
          {(containers.length > 1 || initContainers.length > 0) && (
            <div className="flex items-center gap-2">
              <Box className="h-4 w-4 text-muted-foreground" />
              <Label className="text-sm font-medium">Container:</Label>
              <Select value={selectedContainer} onValueChange={setSelectedContainer}>
                <SelectTrigger className="w-[250px]">
                  <SelectValue placeholder="Select container" />
                </SelectTrigger>
                <SelectContent>
                  {containers.map((container: K8sPodContainer) => (
                    <SelectItem key={container.name} value={container.name}>
                      <div className="flex items-center gap-2">
                        <span>{container.name}</span>
                        {container.ready && (
                          <Badge variant="default" className="text-xs">Ready</Badge>
                        )}
                        {!container.ready && (
                          <Badge variant="secondary" className="text-xs">{container.state}</Badge>
                        )}
                      </div>
                    </SelectItem>
                  ))}
                  {initContainers.length > 0 && (
                    <>
                      <SelectItem disabled value="init-separator">
                        -- Init Containers --
                      </SelectItem>
                      {initContainers.map((container: K8sPodContainer) => (
                        <SelectItem key={container.name} value={container.name}>
                          <div className="flex items-center gap-2">
                            <span>{container.name}</span>
                            <Badge variant="outline" className="text-xs">Init</Badge>
                          </div>
                        </SelectItem>
                      ))}
                    </>
                  )}
                </SelectContent>
              </Select>
            </div>
          )}

          <div className="flex items-center gap-2">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
              <Input
                placeholder="Search logs..."
                aria-label="Search pod logs"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="pl-9"
              />
            </div>

            {/* Follow Toggle Button */}
            <Button
              variant={isFollowing ? "default" : "outline"}
              size="sm"
              onClick={handleFollowToggle}
              aria-label={isFollowing ? "Stop following logs" : "Follow logs in real-time"}
            >
              {isFollowing ? (
                <>
                  <Square className="h-4 w-4 mr-1" />
                  Stop
                </>
              ) : (
                <>
                  <Play className="h-4 w-4 mr-1" />
                  Follow
                </>
              )}
            </Button>

            <Button
              variant="outline"
              size="sm"
              onClick={() => refetch()}
              disabled={isLoading || isFollowing}
              aria-label="Refresh logs"
            >
              <RefreshCw className={`h-4 w-4 ${isLoading ? 'animate-spin' : ''}`} />
            </Button>

            <Button
              variant="outline"
              size="sm"
              onClick={handleDownload}
              disabled={!logs}
              aria-label="Download logs"
            >
              <Download className="h-4 w-4" />
            </Button>
          </div>
        </div>

        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <Label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={autoScroll}
              onChange={(e) => setAutoScroll(e.target.checked)}
              className="rounded"
            />
            Auto-scroll
          </Label>

          {/* Connection Status Indicator */}
          {isFollowing && (
            <div className="flex items-center gap-2">
              {connectionStatus === 'connected' && (
                <Badge variant="success">
                  <Wifi className="h-3 w-3 mr-1" />
                  Live
                </Badge>
              )}
              {connectionStatus === 'connecting' && (
                <Badge variant="warning">
                  <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                  Connecting...
                </Badge>
              )}
              {connectionStatus === 'error' && (
                <Badge variant="destructive">
                  <WifiOff className="h-3 w-3 mr-1" />
                  Connection Error
                </Badge>
              )}
              {connectionStatus === 'disconnected' && isFollowing && (
                <Badge variant="secondary">
                  <WifiOff className="h-3 w-3 mr-1" />
                  Disconnected
                </Badge>
              )}
            </div>
          )}
        </div>

        {/* Log display — relative/absolute pattern guarantees the scroll
            container is constrained regardless of parent flex/grid quirks. */}
        <Card className="flex-1 min-h-0 relative">
          {isLoading && !logs && !isFollowing ? (
            <div className="absolute inset-0 flex items-center justify-center">
              <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            </div>
          ) : isFollowing && connectionStatus === 'connecting' && streamedLogs.length === 0 ? (
            <div className="absolute inset-0 flex items-center justify-center">
              <div className="flex flex-col items-center gap-2 text-muted-foreground">
                <Loader2 className="h-8 w-8 animate-spin" />
                <p>Connecting to log stream...</p>
              </div>
            </div>
          ) : logs ? (
            <div
              ref={scrollRef}
              onScroll={handleScroll}
              className="absolute inset-0 overflow-auto p-4"
            >
              <pre className="text-xs font-mono whitespace-pre-wrap break-words">
                {filteredLogs || 'No logs found matching your search.'}
              </pre>
            </div>
          ) : (
            <div className="absolute inset-0 flex items-center justify-center text-muted-foreground">
              <p>{isFollowing ? 'Waiting for logs...' : 'No logs available'}</p>
            </div>
          )}
        </Card>

        {logs && (
          <div className="text-xs text-muted-foreground flex items-center justify-between">
            <span>
              {filteredLogs.split('\n').length} lines
              {searchQuery && ` (filtered from ${logs.split('\n').length} total)`}
            </span>
            {isFollowing && connectionStatus === 'connected' && (
              <span className="text-success flex items-center gap-1">
                <span className="w-2 h-2 bg-success rounded-full animate-pulse" />
                Streaming live logs
              </span>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
