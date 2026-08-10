import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Terminal as TerminalIcon, Box, AlertCircle } from 'lucide-react';
import type { K8sResource, K8sPodContainer } from '@/types';
import { usePodContainers } from '@/hooks/useK8s';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';
import { useTheme } from '@/context/ThemeContext';
import { logger } from '@/lib/logger';

interface PodExecTerminalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  pod: K8sResource | null;
  clusterId: number;
  namespace: string;
}

export function PodExecTerminal({
  open,
  onOpenChange,
  pod,
  clusterId,
  namespace,
}: PodExecTerminalProps) {
  // D-020: isDark kept for xterm.js theme (data-viz allowlist)
  const { isDark } = useTheme();
  const [selectedContainer, setSelectedContainer] = useState<string>('');
  const [selectedShell, setSelectedShell] = useState('/bin/sh');
  const [connectionStatus, setConnectionStatus] = useState<'disconnected' | 'connecting' | 'connected' | 'error'>('disconnected');
  const [errorMessage, setErrorMessage] = useState('');
  const terminalRef = useRef<HTMLDivElement>(null);
  const xtermRef = useRef<Terminal | null>(null);
  const fitAddonRef = useRef<FitAddon | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  // Track connection status in a ref so the WebSocket callbacks don't
  // need `connectionStatus` in the useCallback deps (which would cause
  // a reconnect loop: status change → new callback → useEffect re-fires).
  const connectionStatusRef = useRef(connectionStatus);
  connectionStatusRef.current = connectionStatus;

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

  const connectWebSocket = useCallback((terminal: Terminal) => {
    if (!pod?.metadata?.name) return;

    // Close any existing connection first
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }

    setConnectionStatus('connecting');
    setErrorMessage('');
    terminal.write('\r\n\x1b[33mConnecting to pod...\x1b[0m\r\n\n');

    // Determine WebSocket protocol (ws or wss)
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;

    // Build WebSocket URL with JWT token for authentication
    const authToken = localStorage.getItem('auth_token');
    let wsUrl = `${protocol}//${host}/ws/k8s/clusters/${clusterId}/pods/${pod.metadata.name}/exec?namespace=${encodeURIComponent(namespace)}&container=${encodeURIComponent(selectedContainer)}&command=${encodeURIComponent(selectedShell)}`;
    if (authToken) {
      wsUrl += `&token=${encodeURIComponent(authToken)}`;
    }

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnectionStatus('connected');
      setErrorMessage('');
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);

        switch (msg.type) {
          case 'connected':
            terminal.write(`\r\n\x1b[32m✓ ${msg.message}\x1b[0m\r\n`);
            terminal.write(`\x1b[90mShell: ${msg.command} | Container: ${msg.container}\x1b[0m\r\n\n`);
            break;

          case 'stdout': {
            // Decode base64
            const output = atob(msg.data);
            terminal.write(output);
            break;
          }

          case 'stderr': {
            // Decode base64 and write in red
            const error = atob(msg.data);
            terminal.write(`\x1b[31m${error}\x1b[0m`);
            break;
          }

          case 'exit':
            terminal.write(`\r\n\x1b[33m✗ Session ended: ${msg.message}\x1b[0m\r\n`);
            setConnectionStatus('disconnected');
            break;

          case 'error':
            terminal.write(`\r\n\x1b[31m✗ Error: ${msg.message}\x1b[0m\r\n`);
            setConnectionStatus('error');
            setErrorMessage(msg.message);
            break;
        }
      } catch (e) {
        logger.error('Failed to parse WebSocket message:', e);
      }
    };

    ws.onerror = (error) => {
      logger.error('WebSocket error:', error);
      terminal.write('\r\n\x1b[31m✗ WebSocket connection error\x1b[0m\r\n');
      setConnectionStatus('error');
      setErrorMessage('WebSocket connection failed');
    };

    ws.onclose = () => {
      // Read status from ref to avoid stale closure
      const status = connectionStatusRef.current;
      if (status === 'connected') {
        terminal.write('\r\n\x1b[33m✗ Connection closed\x1b[0m\r\n');
      }
      // Only transition to disconnected if we were connected — if we're
      // already in 'error' state (server sent an error message), keep it
      // so the user sees the error badge + message.
      if (status !== 'error') {
        setConnectionStatus('disconnected');
      }
    };

    // Send user input to server
    terminal.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({
          type: 'stdin',
          data: btoa(data),  // Base64 encode
        }));
      }
    });
  // NOTE: connectionStatus intentionally excluded — using ref instead
  // to break the reconnect loop (status change → new callback → useEffect)
  }, [pod?.metadata?.name, selectedContainer, selectedShell, clusterId, namespace]);

  // Initialize terminal
  useEffect(() => {
    if (!open || !terminalRef.current || !selectedContainer) return;

    // Create terminal instance
    const terminal = new Terminal({
      cursorBlink: true,
      fontSize: 13,
      fontFamily: 'Menlo, Monaco, "Courier New", monospace',
      theme: isDark ? {
        background: '#0f172a',
        foreground: '#e2e8f0',
        cursor: '#2563eb',
        black: '#000000',
        red: '#ef4444',
        green: '#10b981',
        yellow: '#f59e0b',
        blue: '#3b82f6',
        magenta: '#2563eb',
        cyan: '#06b6d4',
        white: '#e2e8f0',
        brightBlack: '#64748b',
        brightRed: '#f87171',
        brightGreen: '#34d399',
        brightYellow: '#fbbf24',
        brightBlue: '#60a5fa',
        brightMagenta: '#2563eb',
        brightCyan: '#22d3ee',
        brightWhite: '#f1f5f9',
      } : {
        background: '#ffffff',
        foreground: '#1e293b',
        cursor: '#2563eb',
      },
      allowProposedApi: true,
    });

    // Create fit addon
    const fitAddon = new FitAddon();
    terminal.loadAddon(fitAddon);

    // Open terminal in DOM
    terminal.open(terminalRef.current);
    fitAddon.fit();

    xtermRef.current = terminal;
    fitAddonRef.current = fitAddon;

    // Connect WebSocket
    connectWebSocket(terminal);

    // Handle resize
    const handleResize = () => {
      if (fitAddonRef.current && xtermRef.current) {
        fitAddonRef.current.fit();
        // Send resize to server
        if (wsRef.current?.readyState === WebSocket.OPEN) {
          wsRef.current.send(JSON.stringify({
            type: 'resize',
            rows: xtermRef.current.rows,
            cols: xtermRef.current.cols,
          }));
        }
      }
    };

    window.addEventListener('resize', handleResize);

    // Cleanup
    return () => {
      window.removeEventListener('resize', handleResize);
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
      if (xtermRef.current) {
        xtermRef.current.dispose();
        xtermRef.current = null;
      }
    };
  }, [open, selectedContainer, selectedShell, isDark, pod?.metadata?.name, clusterId, namespace, connectWebSocket]);

  const handleReconnect = () => {
    if (xtermRef.current) {
      xtermRef.current.clear();
      connectWebSocket(xtermRef.current);
    }
  };

  if (!pod) return null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-6xl h-[90vh] overflow-hidden flex flex-col" aria-describedby={undefined}>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <TerminalIcon className="h-5 w-5" />
            Terminal: {pod.metadata.name}
          </DialogTitle>
        </DialogHeader>

        <div className="flex flex-col gap-3 py-2">
          {/* Controls */}
          <div className="flex items-center gap-3 flex-wrap">
            {/* Container Selector */}
            {(containers.length > 1 || initContainers.length > 0) && (
              <div className="flex items-center gap-2">
                <Box className="h-4 w-4 text-muted-foreground" />
                <Label className="text-sm font-medium">Container:</Label>
                <Select
                  value={selectedContainer}
                  onValueChange={setSelectedContainer}
                  disabled={connectionStatus === 'connected'}
                >
                  <SelectTrigger className="w-[200px]">
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
                        </div>
                      </SelectItem>
                    ))}
                    {initContainers.length > 0 && (
                      <>
                        <SelectItem disabled value="init-separator">
                          ── Init Containers ──
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

            {/* Shell Selector */}
            <div className="flex items-center gap-2">
              <Label className="text-sm font-medium">Shell:</Label>
              <Select
                value={selectedShell}
                onValueChange={setSelectedShell}
                disabled={connectionStatus === 'connected'}
              >
                <SelectTrigger className="w-[140px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="/bin/bash">bash</SelectItem>
                  <SelectItem value="/bin/sh">sh</SelectItem>
                  <SelectItem value="/bin/zsh">zsh</SelectItem>
                  <SelectItem value="/bin/ash">ash</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {/* Status Badge */}
            <Badge
              variant={
                connectionStatus === 'connected' ? 'success' :
                connectionStatus === 'connecting' ? 'warning' :
                connectionStatus === 'error' ? 'destructive' :
                'muted'
              }
            >
              {connectionStatus === 'connected' && '● Connected'}
              {connectionStatus === 'connecting' && '◐ Connecting...'}
              {connectionStatus === 'disconnected' && '○ Disconnected'}
              {connectionStatus === 'error' && '✗ Error'}
            </Badge>

            {/* Reconnect Button */}
            {(connectionStatus === 'disconnected' || connectionStatus === 'error') && (
              <Button
                variant="outline"
                size="sm"
                onClick={handleReconnect}
                disabled={!selectedContainer}
              >
                Reconnect
              </Button>
            )}
          </div>

          {/* Error Message */}
          {errorMessage && (
            <div className="flex items-center gap-2 px-3 py-2 bg-destructive/10 border border-destructive/20 rounded text-sm text-destructive">
              <AlertCircle className="h-4 w-4" />
              <span>{errorMessage}</span>
            </div>
          )}
        </div>

        {/* Terminal */}
        <div className="flex-1 overflow-hidden rounded-lg border border-border bg-background">
          <div ref={terminalRef} className="h-full w-full p-2" />
        </div>

        <div className="text-xs text-muted-foreground">
          Tip: Use Ctrl+C to interrupt running commands, Ctrl+D to exit shell
        </div>
      </DialogContent>
    </Dialog>
  );
}
