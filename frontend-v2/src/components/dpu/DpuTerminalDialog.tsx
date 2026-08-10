/**
 * Generic interactive terminal dialog for a DPU.
 *
 * Three modes, all sharing the same xterm.js + WebSocket bridge:
 *
 *   • 'rshim-console' — BMC serial console via rshim (was the original
 *      DpuSerialConsoleDialog).
 *   • 'ssh-bmc'       — interactive SSH shell into the BMC (OpenBMC).
 *   • 'ssh-os'        — interactive SSH shell into the DPU OS (Ubuntu).
 *
 * The only differences between modes are the WebSocket URL suffix, the
 * dialog title, and the footer hints. Everything else (xterm setup,
 * base64 stdin/stdout, resize plumbing) is shared.
 */
import { useEffect, useRef, useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Badge } from '@/components/ui/badge';
import { Terminal as TerminalIcon, AlertCircle } from 'lucide-react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';
// D-020: isDark kept for xterm.js theme (data-viz allowlist).
import { useTheme } from '@/context/ThemeContext';
import { STORAGE_KEYS } from '@/lib/storage-keys';
import type { Dpu } from '@/lib/api/dpus';

export type DpuTerminalMode = 'rshim-console' | 'ssh-bmc' | 'ssh-os';

interface DpuTerminalDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: number;
  dpu: Dpu | null;
  mode: DpuTerminalMode;
}

type Status = 'disconnected' | 'connecting' | 'connected' | 'error';

const MODE_CONFIG: Record<
  DpuTerminalMode,
  { path: string; titlePrefix: string; targetLabel: (dpu: Dpu) => string }
> = {
  'rshim-console': {
    path: 'serial-console',
    titlePrefix: 'Serial Console',
    targetLabel: (dpu) => dpu.bmc_ip ?? dpu.host_node_ip ?? '—',
  },
  'ssh-bmc': {
    path: 'ssh-bmc',
    titlePrefix: 'SSH to BMC',
    targetLabel: (dpu) => dpu.bmc_ip ?? '—',
  },
  'ssh-os': {
    path: 'ssh-os',
    titlePrefix: 'SSH to DPU',
    targetLabel: (dpu) => dpu.dpu_os_ip ?? dpu.bmc_ip ?? '—',
  },
};

export function DpuTerminalDialog({
  open,
  onOpenChange,
  projectId,
  dpu,
  mode,
}: DpuTerminalDialogProps) {
  const { isDark } = useTheme();
  const [status, setStatus] = useState<Status>('disconnected');
  const [errorMessage, setErrorMessage] = useState<string>('');
  const [hints, setHints] = useState<string>('');
  const termContainerRef = useRef<HTMLDivElement>(null);
  const xtermRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!open || !dpu) return;

    setStatus('connecting');
    setErrorMessage('');
    setHints('');

    const term = new Terminal({
      cursorBlink: true,
      fontFamily:
        'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace',
      fontSize: 13,
      theme: isDark
        ? { background: '#0a0a0a', foreground: '#e4e4e7' }
        : { background: '#ffffff', foreground: '#18181b' },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    xtermRef.current = term;
    fitRef.current = fit;

    // Fit, then shrink by one row. fit-addon's row count rounds to fit
    // the container exactly, which leaves the bottom row flush against
    // the container edge — at some zoom levels sub-pixel rounding clips
    // the cursor row's last 1-2 px and the operator can't see what
    // they're typing. Reserving one row of headroom is immune to
    // rounding and trivially cheap (one fewer row of scrollback view).
    const safeFit = () => {
      try {
        const dims = fitRef.current?.proposeDimensions();
        if (dims && xtermRef.current) {
          xtermRef.current.resize(
            Math.max(20, dims.cols),
            Math.max(2, dims.rows - 1),
          );
        }
      } catch { /* noop */ }
    };

    const attachTimer = window.setTimeout(() => {
      if (termContainerRef.current && xtermRef.current) {
        xtermRef.current.open(termContainerRef.current);
        safeFit();
      }
    }, 20);

    // Re-fit whenever the dialog/container actually reaches its final size.
    // Radix dialogs animate open over ~200 ms, so the initial fit at 20 ms
    // is calculated against an intermediate height and the bottom row gets
    // clipped. ResizeObserver fires once the CSS transition settles.
    let resizeObserver: ResizeObserver | null = null;
    if (typeof ResizeObserver !== 'undefined' && termContainerRef.current) {
      resizeObserver = new ResizeObserver(() => {
        safeFit();
      });
      resizeObserver.observe(termContainerRef.current);
    }

    // Force Ctrl+C to send SIGINT (0x03) to the remote when no selection
    // exists. Without this, xterm.js / the browser swallow Ctrl+C for the
    // "copy" keyboard shortcut even with no selection — so the user can
    // Ctrl+Z a running job but not Ctrl+C it. With a selection we still let
    // the default copy-to-clipboard behaviour run.
    term.attachCustomKeyEventHandler((ev) => {
      if (
        ev.type === 'keydown'
        && ev.ctrlKey
        && !ev.shiftKey
        && !ev.metaKey
        && !ev.altKey
        && (ev.key === 'c' || ev.key === 'C')
      ) {
        if (!term.hasSelection()) {
          ev.preventDefault();
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'stdin', data: btoa('\x03') }));
          }
          return false; // tell xterm we've handled it
        }
        // A selection exists — let the normal copy path run.
      }
      return true;
    });

    const token = localStorage.getItem(STORAGE_KEYS.AUTH_TOKEN) ?? '';
    const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const { path } = MODE_CONFIG[mode];
    const url =
      `${scheme}://${window.location.host}` +
      `/ws/projects/${projectId}/dpus/${dpu.id}/${path}` +
      (token ? `?token=${encodeURIComponent(token)}` : '');
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onmessage = (ev) => {
      let msg: { type: string; data?: string; message?: string; hints?: string; code?: number };
      try {
        msg = JSON.parse(typeof ev.data === 'string' ? ev.data : '');
      } catch {
        return;
      }
      if (msg.type === 'connected') {
        setStatus('connected');
        if (msg.hints) setHints(msg.hints);
      } else if (msg.type === 'stdout' || msg.type === 'stderr') {
        if (msg.data) {
          try {
            const decoded = atob(msg.data);
            xtermRef.current?.write(decoded, () => {
              // Force viewport to follow the cursor on every chunk.
              // xterm's default is to follow only when the user is
              // already at the bottom; an accidental mousewheel scroll
              // sticks the view and subsequent output (and the user's
              // own keystrokes) end up below the visible area.
              xtermRef.current?.scrollToBottom();
            });
          } catch {
            /* noop */
          }
        }
      } else if (msg.type === 'error') {
        setStatus('error');
        setErrorMessage(msg.message || 'Terminal error');
      } else if (msg.type === 'exit') {
        setStatus('disconnected');
      }
    };

    ws.onerror = () => {
      setStatus('error');
      setErrorMessage('WebSocket connection failed.');
    };
    ws.onclose = () => {
      setStatus((s) => (s === 'error' ? s : 'disconnected'));
    };

    const dataDisposer = term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'stdin', data: btoa(data) }));
      }
      // Snap back to the cursor on every keystroke. xterm's
      // `scrollOnUserInput` default should do this, but in this dialog
      // the layout sometimes reports a viewport slightly larger than
      // the rendered rows and the auto-scroll misses by a line. Belt-
      // and-braces: explicit call.
      term.scrollToBottom();
    });
    const resizeDisposer = term.onResize(({ rows, cols }) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'resize', rows, cols }));
      }
    });

    const onWindowResize = () => {
      try { fit.fit(); } catch { /* noop */ }
    };
    window.addEventListener('resize', onWindowResize);

    return () => {
      window.clearTimeout(attachTimer);
      window.removeEventListener('resize', onWindowResize);
      if (resizeObserver) resizeObserver.disconnect();
      dataDisposer.dispose();
      resizeDisposer.dispose();
      try { ws.close(); } catch { /* noop */ }
      try { term.dispose(); } catch { /* noop */ }
      xtermRef.current = null;
      fitRef.current = null;
      wsRef.current = null;
    };
  }, [open, dpu, projectId, isDark, mode]);

  const cfg = MODE_CONFIG[mode];
  const title = dpu
    ? `${cfg.titlePrefix} — ${cfg.targetLabel(dpu)}${dpu.serial_number ? ` (${dpu.serial_number})` : ''}`
    : cfg.titlePrefix;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-5xl h-[80vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <TerminalIcon className="h-4 w-4" />
            {title}
            <StatusBadge status={status} />
          </DialogTitle>
        </DialogHeader>

        {status === 'error' ? (
          <div className="flex-1 overflow-auto p-4 space-y-2">
            <div className="flex items-center gap-2 text-destructive">
              <AlertCircle className="h-4 w-4" />
              <span className="font-medium">Could not open terminal</span>
            </div>
            <pre className="text-xs whitespace-pre-wrap font-mono opacity-80">
              {errorMessage}
            </pre>
          </div>
        ) : (
          <>
            <div
              ref={termContainerRef}
              // `pb-2` gives the bottom line a few pixels of headroom so the
              // cursor on the last row isn't clipped by sub-pixel rounding
              // in the fit-addon row calculation.
              className="flex-1 min-h-[300px] overflow-hidden rounded border border-border bg-black pb-2"
            />
            <ModeFooter mode={mode} hints={hints} />
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

function ModeFooter({ mode, hints }: { mode: DpuTerminalMode; hints: string }) {
  if (mode === 'rshim-console') {
    return (
      <div className="text-xs opacity-70 pt-1 space-y-0.5">
        <div>
          <span className="font-semibold">Raw tty:</span> keystrokes are
          forwarded as-is to the DPU. Ctrl-C / Ctrl-Z / etc. go to the DPU
          (they do not close this window).
        </div>
        <div>
          <span className="font-semibold">Note:</span> at the login /
          password prompt, typed chars echo back only after Enter — the
          rshim-over-USB path coalesces getty's per-char echo. Keys still
          reach the DPU immediately. After login, the shell's own echo
          shows per-keystroke as expected.
        </div>
        <div>
          <span className="font-semibold">Disconnect:</span> close this
          window — the BMC-side reader/writer will terminate when the
          WebSocket drops.
        </div>
        {hints && <div className="italic">{hints}</div>}
      </div>
    );
  }
  const target = mode === 'ssh-bmc' ? 'BMC' : 'DPU OS';
  return (
    <div className="text-xs opacity-70 pt-1 space-y-0.5">
      <div>
        <span className="font-semibold">Interactive SSH:</span> standard
        shell on the {target}. Ctrl-C / Ctrl-Z are interpreted by the
        remote shell.
      </div>
      <div>
        <span className="font-semibold">Disconnect:</span> type{' '}
        <span className="font-mono">exit</span> or close this window.
      </div>
      {hints && <div className="italic">{hints}</div>}
    </div>
  );
}

function StatusBadge({ status }: { status: Status }) {
  if (status === 'connected') return <Badge variant="success" className="ml-2">Connected</Badge>;
  if (status === 'connecting') return <Badge variant="info" className="ml-2">Connecting…</Badge>;
  if (status === 'error')      return <Badge variant="destructive" className="ml-2">Error</Badge>;
  return <Badge variant="outline" className="ml-2">Idle</Badge>;
}
