import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { Cpu, MemoryStick, ArrowDownUp } from 'lucide-react';
import { systemApi } from '@/lib/api/system';
import { queryKeys } from '@/lib/queryKeys';
import type { ProcessMetrics } from '@/types/system';

// Visibility gate — mirrors the local helper in useSystem.ts / useFleet.ts.
// Stops polling when the tab is hidden.
function useDocumentVisibility(): boolean {
  const [isVisible, setIsVisible] = useState(() =>
    typeof document !== 'undefined' ? !document.hidden : true
  );
  useEffect(() => {
    const handler = () => setIsVisible(!document.hidden);
    document.addEventListener('visibilitychange', handler);
    return () => document.removeEventListener('visibilitychange', handler);
  }, []);
  return isVisible;
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(0)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(1)} GB`;
}

function formatRate(bytesPerSec: number): string {
  if (bytesPerSec < 1024) return `${bytesPerSec.toFixed(0)} B/s`;
  if (bytesPerSec < 1024 * 1024) return `${(bytesPerSec / 1024).toFixed(0)} KB/s`;
  return `${(bytesPerSec / 1024 / 1024).toFixed(1)} MB/s`;
}

export function ProcessMetricsBar() {
  const isVisible = useDocumentVisibility();

  const { data } = useQuery<ProcessMetrics>({
    queryKey: queryKeys.system.processMetrics(),
    queryFn: systemApi.getProcessMetrics,
    refetchInterval: isVisible ? 5_000 : false,
    staleTime: 2_000,
  });

  // Diff consecutive samples to compute network rate. Keep the previous
  // sample in a ref so we don't trigger re-renders on every update.
  const prevSampleRef = useRef<ProcessMetrics | null>(null);
  const [rxRate, setRxRate] = useState(0);
  const [txRate, setTxRate] = useState(0);

  useEffect(() => {
    if (!data) return;
    const prev = prevSampleRef.current;
    if (prev && data.sampled_at > prev.sampled_at) {
      const dt = data.sampled_at - prev.sampled_at;
      if (dt > 0) {
        setRxRate(Math.max(0, (data.net_rx_bytes - prev.net_rx_bytes) / dt));
        setTxRate(Math.max(0, (data.net_tx_bytes - prev.net_tx_bytes) / dt));
      }
    }
    prevSampleRef.current = data;
  }, [data]);

  if (!data) {
    return null;
  }

  // CPU percent is per-core-sum on Linux (e.g. 200% = 2 cores pegged). Normalize
  // to total capacity so the bar fills from 0 → 100% across all cores.
  const cpuPctOfTotal = Math.min(100, data.cpu_percent / data.cpu_count);
  const cpuColor =
    cpuPctOfTotal > 80 ? 'bg-destructive' : cpuPctOfTotal > 50 ? 'bg-warning' : 'bg-success';

  return (
    <div
      className="hidden lg:flex items-center gap-4 px-3 py-1.5 rounded-md bg-muted/40 border text-xs font-mono"
      title={`Backend process • ${data.num_threads} threads • ${data.open_fds ?? '?'} fds • up ${Math.floor(data.uptime_seconds / 60)}m`}
    >
      {/* CPU */}
      <div className="flex items-center gap-2">
        <Cpu className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
        <div className="flex items-center gap-1.5">
          <div className="w-16 h-1.5 rounded-full bg-muted overflow-hidden">
            <div
              className={`h-full ${cpuColor} transition-all duration-500`}
              style={{ width: `${cpuPctOfTotal}%` }}
            />
          </div>
          <span className="tabular-nums w-10 text-right">{cpuPctOfTotal.toFixed(0)}%</span>
        </div>
      </div>

      {/* RAM */}
      <div className="flex items-center gap-1.5">
        <MemoryStick className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
        <span className="tabular-nums">{formatBytes(data.rss_bytes)}</span>
      </div>

      {/* Network rate */}
      <div className="flex items-center gap-1.5">
        <ArrowDownUp className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
        <span className="tabular-nums">
          <span className="text-success">↓{formatRate(rxRate)}</span>
          <span className="text-muted-foreground mx-1">/</span>
          <span className="text-primary">↑{formatRate(txRate)}</span>
        </span>
      </div>
    </div>
  );
}
