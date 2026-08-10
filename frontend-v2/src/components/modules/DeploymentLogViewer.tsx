import { useEffect, useState, useRef, useCallback } from 'react';
import { SectionCard } from '@/components/ui/section-card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Alert, AlertDescription } from '@/components/ui/alert';
import {
  Terminal,
  Play,
  Square,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  Loader2,
  Download,
} from 'lucide-react';
import type { ProjectModule } from '@/types';
import { logger } from '@/lib/logger';

interface LogEntry {
  timestamp: string;
  level: 'info' | 'success' | 'warning' | 'error';
  message: string;
}

interface DeploymentLogViewerProps {
  module: ProjectModule;
  autoConnect?: boolean;
}

export function DeploymentLogViewer({ module, autoConnect = false }: DeploymentLogViewerProps) {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const [connectionStatus, setConnectionStatus] = useState<string>('disconnected');
  const [moduleStatus, setModuleStatus] = useState<string>(module.status || 'unknown');
  const [currentStage, setCurrentStage] = useState<string>('');
  const wsRef = useRef<WebSocket | null>(null);
  const pingIntervalRef = useRef<number | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [autoScroll, setAutoScroll] = useState(true);

  // Dynamically determine WebSocket URL based on current page location for portability
  // This allows the app to work when accessed from any host
  const getWsUrl = () => {
    if (import.meta.env.VITE_WS_URL) return import.meta.env.VITE_WS_URL;
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${protocol}//${window.location.host}`;
  };
  const wsUrl = getWsUrl();

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      return; // Already connected
    }

    const ws = new WebSocket(`${wsUrl}/api/ws/deployment/${module.id}`);

    ws.onopen = () => {
      setIsConnected(true);
      setConnectionStatus('connected');
      addLog('info', 'Connected to deployment stream');

      // Send ping to keep connection alive
      pingIntervalRef.current = window.setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ command: 'ping' }));
        }
      }, 30000); // Ping every 30 seconds
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);

        // Handle different message types
        switch (data.type) {
          case 'connected':
            setModuleStatus(data.data.current_status);
            addLog('success', `Connected to module: ${data.data.module_name}`);
            break;

          case 'status':
            setConnectionStatus(data.data.status);
            if (data.data.message) {
              addLog('info', data.data.message);
            }
            break;

          case 'output': {
            const line = data.data.line || '';
            addLog(data.data.level || 'info', line);
            // Extract stage from SSH engine output prefixes
            const stageMatch = line.match(
              /^\[ssh(?::exec:(\d+\/\d+)|:pre-stage|:validate)?\]\s*(.+)/
            );
            if (stageMatch) {
              if (stageMatch[1]) {
                setCurrentStage(`Executing ${stageMatch[1]}...`);
              } else if (line.includes(':pre-stage')) {
                setCurrentStage('Pre-staging...');
              } else if (line.includes(':validate')) {
                setCurrentStage('Validating...');
              } else if (line.includes('Reconnect')) {
                setCurrentStage(stageMatch[2].substring(0, 60));
              } else if (line.includes('Applying')) {
                setCurrentStage('Starting...');
              }
            }
            break;
          }

          case 'progress': {
            const { current, total, message } = data.data;
            addLog('info', message || `Progress: ${current}/${total}`);
            break;
          }

          case 'error':
            addLog('error', data.data.error);
            break;

          case 'complete': {
            const success = data.data.success;
            addLog(success ? 'success' : 'error', data.data.message || 'Operation complete');
            setConnectionStatus('idle');
            setCurrentStage('');
            break;
          }

          case 'pong':
            // Heartbeat response, no action needed
            break;

          default:
            // Handle Phase 4 UX format (flat structure)
            if (data.timestamp && data.level && data.message) {
              addLog(data.level, data.message);
            }
        }
      } catch (error) {
        logger.error('Error parsing WebSocket message:', error);
      }
    };

    ws.onerror = (error) => {
      logger.error('WebSocket error:', error);
      setConnectionStatus('error');
      addLog('error', 'Connection error occurred');
    };

    ws.onclose = () => {
      setIsConnected(false);
      setConnectionStatus('disconnected');
      addLog('warning', 'Disconnected from deployment stream');

      if (pingIntervalRef.current) {
        clearInterval(pingIntervalRef.current);
        pingIntervalRef.current = null;
      }
    };

    wsRef.current = ws;
  }, [module.id, wsUrl]);

  const disconnect = () => {
    if (wsRef.current) {
      wsRef.current.send(JSON.stringify({ command: 'close' }));
      wsRef.current.close();
      wsRef.current = null;
    }
  };

  useEffect(() => {
    if (autoConnect) {
      connect();
    }

    return () => {
      disconnect();
    };
  }, [module.id, autoConnect, connect]);

  // Auto-scroll to bottom when new logs arrive
  useEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [logs, autoScroll]);

  const addLog = (level: LogEntry['level'], message: string) => {
    const timestamp = new Date().toLocaleTimeString();
    setLogs((prev) => [...prev, { timestamp, level, message }]);
  };

  const clearLogs = () => {
    setLogs([]);
  };

  const downloadLogs = () => {
    const logText = logs.map((log) => `[${log.timestamp}] [${log.level.toUpperCase()}] ${log.message}`).join('\n');
    const blob = new Blob([logText], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `deployment-logs-${module.id}-${Date.now()}.txt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const getStatusColor = (status: string): 'success' | 'info' | 'outline' | 'destructive' => {
    switch (status) {
      case 'connected':
      case 'running':
        return 'success';
      case 'starting':
        return 'info';
      case 'idle':
      case 'disconnected':
        return 'outline';
      case 'error':
      case 'cancelled':
        return 'destructive';
      default:
        return 'outline';
    }
  };

  const getLevelIcon = (level: string) => {
    switch (level) {
      case 'success':
        return <CheckCircle2 className="h-4 w-4 text-success" />;
      case 'error':
        return <XCircle className="h-4 w-4 text-destructive" />;
      case 'warning':
        return <AlertTriangle className="h-4 w-4 text-warning" />;
      default:
        return <Terminal className="h-4 w-4 text-info" />;
    }
  };

  const getLevelColor = (level: string) => {
    switch (level) {
      case 'success':
        return 'text-success';
      case 'error':
        return 'text-destructive';
      case 'warning':
        return 'text-warning';
      default:
        return 'text-foreground';
    }
  };

  return (
    <SectionCard title={`Deployment logs · ${module.module_name}`}>
      <div className="flex items-center justify-between -mt-1 mb-4">
        <p className="text-sm text-muted-foreground">
          Real-time streaming of OpenTofu operations.
        </p>
        <div className="flex items-center gap-2">
          <Badge variant={getStatusColor(connectionStatus)}>
            {connectionStatus}
          </Badge>
          <Badge variant="outline">{moduleStatus}</Badge>
        </div>
      </div>
      <div className="space-y-4">
        {/* Connection Controls */}
        <div className="flex items-center gap-2">
          {!isConnected ? (
            <Button size="sm" onClick={connect}>
              <Play className="h-4 w-4 mr-2" />
              Connect
            </Button>
          ) : (
            <Button size="sm" variant="outline" onClick={disconnect}>
              <Square className="h-4 w-4 mr-2" />
              Disconnect
            </Button>
          )}

          <Button size="sm" variant="outline" onClick={clearLogs}>
            Clear
          </Button>

          <Button size="sm" variant="outline" onClick={downloadLogs} disabled={logs.length === 0}>
            <Download className="h-4 w-4 mr-2" />
            Download
          </Button>

          <div className="flex-1" />

          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={autoScroll}
              onChange={(e) => setAutoScroll(e.target.checked)}
              className="rounded"
            />
            Auto-scroll
          </label>
        </div>

        {/* Connection Info */}
        {!isConnected && logs.length === 0 && (
          <Alert>
            <Terminal className="h-4 w-4" />
            <AlertDescription>
              Click "Connect" to stream real-time logs from OpenTofu operations
            </AlertDescription>
          </Alert>
        )}

        {/* Log Output — console emulator surface */}
        <div className="border border-border rounded-lg bg-muted">
          <ScrollArea className="h-[400px]">
            <div ref={scrollRef} className="p-4 font-mono text-sm space-y-1 text-foreground">
              {logs.length === 0 && isConnected && (
                <div className="text-muted-foreground">Waiting for logs...</div>
              )}
              {logs.map((log, index) => (
                <div key={index} className="flex items-start gap-2 hover:bg-muted/50 px-2 py-1 rounded">
                  {getLevelIcon(log.level)}
                  <span className="text-muted-foreground text-xs">[{log.timestamp}]</span>
                  <span className={getLevelColor(log.level)}>{log.message}</span>
                </div>
              ))}
            </div>
          </ScrollArea>
        </div>

        {/* Progress Indicator */}
        {(connectionStatus === 'running' || currentStage) && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            <span>{currentStage || 'Operation in progress...'}</span>
          </div>
        )}
      </div>
    </SectionCard>
  );
}
