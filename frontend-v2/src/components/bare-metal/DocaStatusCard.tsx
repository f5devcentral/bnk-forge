/**
 * DocaStatusCard — compact checklist of DOCA host package readiness.
 *
 * Rendered in the bare-metal discovery result panel and in the DPU
 * inventory detail. Shows whether the DOCA toolchain is properly
 * installed on the host so BNK deployment can proceed.
 */
import { CheckCircle2, XCircle, Package } from 'lucide-react';
import { cn } from '@/lib/utils';

export interface DocaStatus {
  repo_configured: boolean;
  profile_installed: boolean;
  doca_version: string | null;
  mst_present: boolean;
  mlxconfig_present: boolean;
  rshim_present: boolean;
  bfb_install_present: boolean;
  openibd_loaded: boolean;
  mst_devices: string[];
  ready: boolean;
}

function StatusIcon({ ok }: { ok: boolean }) {
  return ok
    ? <CheckCircle2 className="h-3 w-3 text-success flex-shrink-0" />
    : <XCircle className="h-3 w-3 text-destructive flex-shrink-0" />;
}

export function DocaStatusCard({ doca }: { doca: DocaStatus }) {
  const title = doca.doca_version
    ? `DOCA Host (doca-all ${doca.doca_version})`
    : 'DOCA Host';

  // Only show binary rows when repo is configured (otherwise overwhelming)
  const showDetails = doca.repo_configured;

  return (
    <div className="space-y-2">
      <h4 className="text-sm font-semibold flex items-center gap-1.5">
        <Package className="h-3.5 w-3.5" />
        {title}
        <span className={cn(
          'ml-auto text-xs font-normal',
          doca.ready ? 'text-success' : 'text-destructive',
        )}>
          {doca.ready ? '✅ Ready' : '❌ Not ready'}
        </span>
      </h4>
      <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm pl-5">
        <div className="flex items-center gap-2 py-0.5">
          <StatusIcon ok={doca.repo_configured} />
          <span className="text-muted-foreground w-28 flex-shrink-0">Repo configured</span>
        </div>
        <div className="flex items-center gap-2 py-0.5">
          <StatusIcon ok={doca.profile_installed} />
          <span className="text-muted-foreground w-28 flex-shrink-0">doca-all installed</span>
        </div>
        {showDetails && (
          <>
            <div className="flex items-center gap-2 py-0.5">
              <StatusIcon ok={doca.mst_present} />
              <span className="text-muted-foreground w-28 flex-shrink-0">mst</span>
            </div>
            <div className="flex items-center gap-2 py-0.5">
              <StatusIcon ok={doca.mlxconfig_present} />
              <span className="text-muted-foreground w-28 flex-shrink-0">mlxconfig</span>
            </div>
            <div className="flex items-center gap-2 py-0.5">
              <StatusIcon ok={doca.rshim_present} />
              <span className="text-muted-foreground w-28 flex-shrink-0">rshim</span>
            </div>
            <div className="flex items-center gap-2 py-0.5">
              <StatusIcon ok={doca.bfb_install_present} />
              <span className="text-muted-foreground w-28 flex-shrink-0">bfb-install</span>
            </div>
            <div className="flex items-center gap-2 py-0.5">
              <StatusIcon ok={doca.openibd_loaded} />
              <span className="text-muted-foreground w-28 flex-shrink-0">openibd</span>
              <span className="font-mono text-xs">{doca.openibd_loaded ? 'active' : 'inactive'}</span>
            </div>
            {doca.mst_devices.length > 0 && (
              <div className="flex items-center gap-2 py-0.5 col-span-2">
                <CheckCircle2 className="h-3 w-3 text-success flex-shrink-0" />
                <span className="text-muted-foreground w-28 flex-shrink-0">MST devices</span>
                <span className="font-mono text-xs">{doca.mst_devices.join(', ')}</span>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
