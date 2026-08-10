/**
 * Read-only discovery presentation for project detail.
 *
 * DISCOVERY-PORT-005 scope: bounded trigger/refresh UX on top of the
 * existing read surfaces.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, Cpu, HardDrive, Loader2, Network, Plus, RefreshCw, Search, Server, Trash2, XCircle } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Textarea } from '@/components/ui/textarea';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  useDeleteDiscoveryJob,
  useDiscoveryJob,
  useDiscoveryJobs,
  useRegisterAllHostsInJob,
  useRegisterNodeAsHost,
  useRerunDiscoveryJob,
  useTriggerDiscovery,
} from '@/hooks/useDiscovery';
import { queryKeys } from '@/lib/queryKeys';
import type { DiscoveredNode, DiscoveryJob, NodeKubeconfigProbeResult } from '@/lib/api/discovery';
import { discoveryApi } from '@/lib/api/discovery';
import { sshCredentialsApi } from '@/lib/api/ssh-credentials';
import { notify, notifyError } from '@/lib/notify';
import { parseIpInput } from '@/lib/ip-range';
import { ClusterConfigDialog } from '@/components/k8s/ClusterConfigDialog';
import { ConnectivityMatrix } from './ConnectivityMatrix';

interface NodeDiscoveryPanelProps {
  projectId: number;
  canTrigger?: boolean;
  jumphost?: { name: string; username: string; host: string } | null;
  projectSshCredentialId?: number;
}

// True when the node was identified as a BlueField BMC but no rung of
// the auth ladder produced a usable password — backend leaves
// `bmc_redfish_payload` as the empty dict it initialises. Without a
// working password registration cannot succeed, so the UI suppresses
// the Register button and shows an "Invalid password" chip instead.
function bmcAuthFailed(node: DiscoveredNode): boolean {
  if (!node.is_dpu_bmc) return false;
  const payload = node.bmc_redfish_payload as Record<string, unknown> | null | undefined;
  return !payload || Object.keys(payload).length === 0;
}

function nodeStatusBadge(status: DiscoveredNode['status']) {
  // The completed-row "Discovered" pill is redundant with the Arch /
  // OS / DPU detail badges and the per-row Register buttons that
  // appear once SSH succeeds — drop it. Still surface in-progress
  // and failure states because those carry information the rest of
  // the row doesn't.
  switch (status) {
    case 'completed':
      return null;
    case 'failed':
      return <Badge variant="destructive"><XCircle className="h-3 w-3 mr-1" />Failed</Badge>;
    case 'connecting':
      return <Badge variant="info"><Loader2 className="h-3 w-3 mr-1 animate-spin" />Connecting</Badge>;
    case 'discovering':
      return <Badge variant="info"><Loader2 className="h-3 w-3 mr-1 animate-spin" />Discovering</Badge>;
    default:
      return <Badge variant="outline" className="text-muted-foreground">Pending</Badge>;
  }
}

function jobStatusBadge(status: 'pending' | 'in_progress' | 'completed' | 'failed' | 'cancelled') {
  switch (status) {
    case 'completed':
      return <Badge variant="success"><CheckCircle2 className="h-3 w-3 mr-1" />Completed</Badge>;
    case 'failed':
      return <Badge variant="destructive"><XCircle className="h-3 w-3 mr-1" />Failed</Badge>;
    case 'in_progress':
      return <Badge variant="info"><Loader2 className="h-3 w-3 mr-1 animate-spin" />Running</Badge>;
    case 'cancelled':
      return <Badge variant="outline">Cancelled</Badge>;
    default:
      return <Badge variant="outline">Pending</Badge>;
  }
}

/**
 * BMC-specific section for the expanded node panel. Replaces the
 * OS/Kernel/Arch/Container-Runtime grid that doesn't apply to a BMC.
 *
 * Renders whatever fields the Redfish probe could pull
 * (`bmc_redfish_payload` is populated by `_probe_bluefield_bmc`); each
 * is optional, so the panel quietly hides anything not present rather
 * than showing "Unknown" placeholders. Surfaces a violet pill when
 * `UsesDefaultPassword` is true — the BMC still has the factory
 * `0penBmc` and the operator should rotate it.
 */
function BmcDetailPanel({ node }: { node: DiscoveredNode }) {
  const rf = (node.bmc_redfish_payload as Record<string, unknown> | null | undefined) ?? {};
  // The "Default password" chip lives on the collapsed row now so the
  // operator sees it without expanding. Just track PCR here so the
  // empty-state hint can explain *why* the rich details are missing.
  const passwordChangeRequired = rf.PasswordChangeRequired === true;

  // Pairs surface in the order most operators want to see them.
  const rows: Array<[string, React.ReactNode]> = [];
  if (node.bmc_product) rows.push(['Product', node.bmc_product]);
  if (node.bmc_serial_number) rows.push(['Serial #', node.bmc_serial_number]);
  if (typeof rf.Manufacturer === 'string') rows.push(['Manufacturer', rf.Manufacturer]);
  if (typeof rf.Model === 'string') rows.push(['Model', rf.Model]);
  if (typeof rf.BiosVersion === 'string' && rf.BiosVersion) rows.push(['BIOS', rf.BiosVersion]);
  if (typeof rf.UUID === 'string') rows.push(['UUID', rf.UUID]);
  if (typeof rf.BaseGUID === 'string') rows.push(['BaseGUID', rf.BaseGUID]);
  if (typeof rf.BaseMAC === 'string') rows.push(['BaseMAC', rf.BaseMAC]);
  if (typeof rf.HostRshim === 'string') rows.push(['HostRshim', rf.HostRshim]);
  if (typeof rf.NicMode === 'string') rows.push(['NicMode', rf.NicMode]);
  if (typeof rf.HostPrivilegeLevel === 'string') {
    rows.push(['HostPrivilegeLevel', rf.HostPrivilegeLevel]);
  }
  if (typeof rf.InternalCPUModel === 'string') rows.push(['CPU mode', rf.InternalCPUModel]);
  if (typeof rf.PowerState === 'string') rows.push(['Power', rf.PowerState]);
  if (typeof rf.MemoryGiB === 'number') rows.push(['Memory', `${rf.MemoryGiB} GiB`]);
  // ProcessorSummary may live as an object — extract the most useful fields.
  const ps = rf.ProcessorSummary as Record<string, unknown> | undefined;
  if (ps && typeof ps === 'object') {
    if (typeof ps.Count === 'number') rows.push(['CPU count', ps.Count]);
    if (typeof ps.CoreCount === 'number') rows.push(['CPU cores', ps.CoreCount]);
    if (typeof ps.Model === 'string') rows.push(['CPU model', ps.Model]);
  }
  // BootProgress timestamp — epoch zero is a useful "SoC never reported back" signal.
  const bp = rf.BootProgress as Record<string, unknown> | undefined;
  if (bp && typeof bp === 'object') {
    if (typeof bp.OemLastState === 'string') rows.push(['Boot state', bp.OemLastState]);
    if (typeof bp.LastStateTime === 'string') rows.push(['Boot time', bp.LastStateTime]);
  }

  // Per-component firmware versions (Phase 3 diagnostic capture). Two
  // same-model DPUs commonly diverge on UEFI/BSP/OS bundle versions —
  // surfacing them helps operators see at a glance why otherwise
  // identical cards behave differently.
  const fwInv = rf.FirmwareInventory as Record<string, unknown> | undefined;
  const fwRows: Array<[string, string]> = [];
  if (fwInv && typeof fwInv === 'object') {
    for (const k of ['DPU_UEFI', 'DPU_ATF', 'DPU_BSP', 'DPU_NIC', 'DPU_OS', 'DPU_OFED', 'DPU_NODE']) {
      const v = fwInv[k];
      if (typeof v === 'string') fwRows.push([k, v]);
    }
  }
  if (typeof rf.BmcFirmwareVersion === 'string') fwRows.push(['BMC_Firmware', rf.BmcFirmwareVersion]);

  // SoC enumeration starvation: when the BMC reports zero processors AND
  // zero memory, the SoC has not pushed SMBIOS / boot data up to the BMC.
  // Common with bf-bundle 3.1.0 / DPU-UEFI 4.12+ — the legacy Bios
  // Attributes table is also empty in that state.
  const socStarved =
    !!ps && typeof ps === 'object' &&
    (ps.Count === 0) &&
    typeof rf.MemoryGiB === 'number' && rf.MemoryGiB === 0;

  return (
    <div className="col-span-full">
      <p className="text-muted-foreground text-xs font-medium mb-1 flex items-center gap-2">
        <Cpu className="h-3 w-3 inline" />
        BlueField BMC (via Redfish)
      </p>
      {rows.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          {passwordChangeRequired ? (
            <>
              The BMC accepts the Nvidia factory default password but
              refuses to serve any resource until it's changed
              (<span className="font-mono">PasswordChangeRequired</span>).
              Rotate the password via Redfish (PATCH{' '}
              <span className="font-mono">/redfish/v1/AccountService/Accounts/root</span>),
              then re-run discovery to populate the rich details.
            </>
          ) : (
            <>
              No Redfish details captured — the auth ladder didn't
              authenticate (BMC password is neither the operator's nor
              the Nvidia default).
            </>
          )}
        </p>
      ) : (
        <>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-x-4 gap-y-1 text-xs mt-1">
            {rows.map(([k, v]) => (
              <div key={k} className="flex flex-col">
                <span className="text-muted-foreground text-[10px] uppercase tracking-wide">{k}</span>
                <span className="font-mono break-all">{v}</span>
              </div>
            ))}
          </div>
          {socStarved && (
            <p className="text-[11px] text-muted-foreground italic mt-2">
              The SoC has not pushed CPU/memory enumeration to the BMC —
              common with newer bf-bundle BSPs (3.1.0 / DPU-UEFI 4.12+).
              The BMC, identity, and firmware versions below are still
              valid; the legacy BIOS Attributes view is unavailable.
            </p>
          )}
          {fwRows.length > 0 && (
            <>
              <p className="text-muted-foreground text-xs font-medium mt-3 mb-1">
                Firmware versions
              </p>
              <div className="grid grid-cols-2 md:grid-cols-3 gap-x-4 gap-y-1 text-xs">
                {fwRows.map(([k, v]) => (
                  <div key={k} className="flex flex-col">
                    <span className="text-muted-foreground text-[10px] uppercase tracking-wide">{k}</span>
                    <span className="font-mono break-all">{v}</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}


function NodeRow({
  node,
  onRegisterK8s,
  isRegisterLoading,
  onRegisterAsDpu,
  isRegisterAsDpuLoading,
  onRegisterAsHost,
  isRegisterAsHostLoading,
}: {
  node: DiscoveredNode;
  onRegisterK8s?: (nodeId: number) => void;
  isRegisterLoading?: boolean;
  onRegisterAsDpu?: (node: DiscoveredNode) => void;
  isRegisterAsDpuLoading?: boolean;
  onRegisterAsHost?: (node: DiscoveredNode) => void;
  isRegisterAsHostLoading?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="border rounded-lg">
      <div className="flex items-center gap-3 p-3 cursor-pointer hover:bg-muted/50 transition-colors" onClick={() => setExpanded(!expanded)}>
        <button className="text-muted-foreground" aria-label={`toggle-node-${node.id}`}>
          {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        </button>

        <Server className="h-4 w-4 text-muted-foreground flex-shrink-0" />
        <span className="font-mono text-sm min-w-[120px]">{node.ip_address}</span>

        {node.hostname && (
          <span className="text-sm text-muted-foreground truncate max-w-[180px] flex-shrink-0">{node.hostname}</span>
        )}

        <div className="flex items-center gap-2 ml-auto flex-shrink-0">
          {node.status === 'completed' && (
            <>
              {node.os_pretty_name && (
                <Badge
                  variant="outline"
                  className="text-xs"
                  title={node.os_pretty_name}
                >
                  <HardDrive className="h-3 w-3 mr-1" />
                  {node.os_type}
                </Badge>
              )}
              {node.architecture && (
                <Badge variant="outline" className="text-xs">{node.architecture}</Badge>
              )}
              {node.is_dpu_host && (
                <>
                  <Badge variant="outline" className="bg-primary/10 text-primary border-primary/20 text-xs">
                    <Cpu className="h-3 w-3 mr-1" />
                    {node.dpu_count} DPU{node.dpu_count !== 1 ? 's' : ''}
                  </Badge>
                  {node.dpu_details?.some((d) => d.soc_mgmt_available === false) && (
                    <Badge variant="destructive" className="text-xs">
                      <AlertTriangle className="h-3 w-3 mr-1" />
                      Zero-Trust
                    </Badge>
                  )}
                </>
              )}
              {node.is_dpu_node && (
                <Badge variant="outline" className="bg-primary/10 text-primary border-primary/20 text-xs">
                  <Cpu className="h-3 w-3 mr-1" />
                  DPU Node
                </Badge>
              )}
              {node.k8s_running && (
                <>
                  <Badge variant="info" className="text-xs">
                    K8s {node.k8s_version}
                  </Badge>
                  {onRegisterK8s && (
                    <Button
                      size="sm"
                      variant="outline"
                      className="h-6 text-xs px-2"
                      onClick={(e) => { e.stopPropagation(); onRegisterK8s(node.id); }}
                      disabled={isRegisterLoading}
                      title="Register this Kubernetes cluster in the project"
                    >
                      {isRegisterLoading
                        ? <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                        : <Plus className="h-3 w-3 mr-1" />}
                      Register K8s
                    </Button>
                  )}
                </>
              )}
              {node.k8s_installed && !node.k8s_running && (
                <Badge variant="warning" className="text-xs">
                  <AlertTriangle className="h-3 w-3 mr-1" />
                  K8s stopped
                </Badge>
              )}
            </>
          )}
          {node.is_dpu_bmc && (
            <Badge variant="outline" className="bg-primary/10 text-primary border-primary/20 text-xs">
              <Cpu className="h-3 w-3 mr-1" />
              BlueField BMC
            </Badge>
          )}
          {/* BMC was identified (Redfish service-root spoke) but no rung
              of the auth ladder unlocked anything — `bmc_redfish_payload`
              stays the empty dict the backend initialises. Without a
              working password we can't register the DPU, so flag it and
              suppress the Register button below. */}
          {node.is_dpu_bmc && bmcAuthFailed(node) && (
            <Badge
              variant="destructive"
              className="text-xs px-1.5 py-0"
              title="No supplied or factory-default BMC password authenticated. Provide a working password and re-run discovery before registering."
            >
              Invalid password
            </Badge>
          )}
          {/* Default-password warning chip on the collapsed row so the
              operator sees it without expanding. The flag is set
              whenever the auth that authenticated was Nvidia's factory
              `0penBmc` OR Redfish returned PasswordChangeRequired. */}
          {node.is_dpu_bmc
            && (node.bmc_redfish_payload as Record<string, unknown> | null | undefined)
            && (
              ((node.bmc_redfish_payload as Record<string, unknown>).UsesDefaultPassword === true)
              || ((node.bmc_redfish_payload as Record<string, unknown>).PasswordChangeRequired === true)
            ) && (
            <Badge
              variant="warning"
              className="text-xs px-1.5 py-0"
              title={
                (node.bmc_redfish_payload as Record<string, unknown>).PasswordChangeRequired === true
                  ? 'BMC has the Nvidia factory default password and refuses to serve resources until rotated. PATCH /redfish/v1/AccountService/Accounts/root.'
                  : 'BMC accepts the Nvidia factory default password (root / 0penBmc). Rotate it before exposing this BMC to a shared network.'
              }
            >
              Default password (0penBmc)
            </Badge>
          )}
          {((node.is_dpu_bmc && !bmcAuthFailed(node)) || node.is_dpu_node
            || (node.is_dpu_host && node.dpu_count > 0)) && onRegisterAsDpu && (
            <Button
              size="sm"
              variant="outline"
              className="h-6 text-xs px-2"
              onClick={(e) => { e.stopPropagation(); onRegisterAsDpu(node); }}
              disabled={isRegisterAsDpuLoading}
              title={
                node.is_dpu_host && !node.is_dpu_bmc && !node.is_dpu_node
                  ? `Register ${node.dpu_count} in-band DPU${node.dpu_count === 1 ? '' : 's'} — managed via rshim on this host`
                  : "Register this DPU in the project's DPU tab"
              }
            >
              {isRegisterAsDpuLoading
                ? <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                : <Plus className="h-3 w-3 mr-1" />}
              {node.is_dpu_host && !node.is_dpu_bmc && !node.is_dpu_node
                ? `Register ${node.dpu_count} DPU${node.dpu_count === 1 ? '' : 's'}`
                : 'Register DPU'}
            </Button>
          )}
          {/* "Register Host" — eligible only for DPU-host nodes (the
              x86 server carrying the DPUs). Adds a row to the Bare
              Metal tab; idempotent on (project, host_ip), so re-clicking
              after a fresh discovery is safe. BMC-only / DPU-OS-only
              rows don't expose this — we don't have a usable host IP
              for them. */}
          {node.is_dpu_host && node.dpu_count > 0 && onRegisterAsHost && (
            <Button
              size="sm"
              variant="outline"
              className="h-6 text-xs px-2"
              onClick={(e) => { e.stopPropagation(); onRegisterAsHost(node); }}
              disabled={isRegisterAsHostLoading}
              title="Add this host to the Bare Metal tab — already-registered hosts are silently accepted"
            >
              {isRegisterAsHostLoading
                ? <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                : <Plus className="h-3 w-3 mr-1" />}
              Register Host
            </Button>
          )}
          {nodeStatusBadge(node.status)}
        </div>
      </div>

      {expanded && node.status === 'completed' && (
        <div className="px-3 pb-3 pt-1 border-t bg-muted/30">
          <div className="grid grid-cols-2 md:grid-cols-3 gap-4 text-sm">
            {node.is_dpu_bmc ? (
              <BmcDetailPanel node={node} />
            ) : (
              <>
                <div>
                  <p className="text-muted-foreground text-xs font-medium mb-1">Operating System</p>
                  <p>{node.os_pretty_name || 'Unknown'}</p>
                  <p className="text-xs text-muted-foreground">Kernel: {node.kernel_version || 'N/A'}</p>
                </div>
                <div>
                  <p className="text-muted-foreground text-xs font-medium mb-1">Architecture</p>
                  <p>{node.architecture || 'Unknown'}</p>
                </div>
                <div>
                  <p className="text-muted-foreground text-xs font-medium mb-1">Container Runtime</p>
                  <p className="truncate">{node.container_runtime || 'Not detected'}</p>
                </div>
              </>
            )}

            {(node.is_dpu_host || node.is_dpu_node) && (
              <div className="col-span-full">
                <p className="text-muted-foreground text-xs font-medium mb-1">
                  <Cpu className="h-3 w-3 inline mr-1" />
                  BlueField-3 DPU
                </p>
                {node.is_dpu_node && <p>This node IS a BlueField-3 DPU (ARM/aarch64)</p>}
                {node.is_dpu_host && (
                  <div>
                    <p>{node.dpu_count} DPU(s) detected via PCIe</p>
                    {node.dpu_details?.map((detail, index) => (
                      <div key={index} className="ml-2 mt-1">
                        <p className="text-xs text-muted-foreground">
                          [{String(detail.pci_address || 'unknown')}] {String(detail.description || 'unknown')}
                        </p>
                        {detail.soc_mgmt_available === true && (
                          <p className="text-xs text-success ml-4">✓ SoC Management Interface available</p>
                        )}
                        {detail.soc_mgmt_available === false && (
                          <p className="text-xs text-destructive ml-4">
                            <AlertTriangle className="h-3 w-3 inline mr-1" />
                            SoC Management Interface missing — likely Zero-Trust mode, imaging blocked
                          </p>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {node.k8s_installed && (
              <div>
                <p className="text-muted-foreground text-xs font-medium mb-1">Kubernetes</p>
                <p>
                  {node.k8s_distribution || 'unknown'} {node.k8s_version}
                  {node.k8s_role && <span className="text-muted-foreground"> ({node.k8s_role})</span>}
                </p>
                <p className="text-xs">
                  {node.k8s_running
                    ? <span className="text-success">Running</span>
                    : <span className="text-warning">Installed but not running</span>
                  }
                </p>
              </div>
            )}

            {node.network_interfaces && node.network_interfaces.length > 0 && (
              <div className="col-span-full">
                <p className="text-muted-foreground text-xs font-medium mb-1">
                  <Network className="h-3 w-3 inline mr-1" />
                  Network Interfaces ({node.network_interfaces.length})
                </p>
                <table className="text-xs w-full border-collapse mt-1">
                  <thead>
                    <tr className="text-muted-foreground text-[10px] uppercase">
                      <th className="text-left font-medium pr-3 py-0.5">Name</th>
                      <th className="text-left font-medium pr-3 py-0.5">Kind</th>
                      <th className="text-left font-medium pr-3 py-0.5">State</th>
                      <th className="text-left font-medium pr-3 py-0.5">IPv4</th>
                      <th className="text-left font-medium pr-3 py-0.5">IPv6</th>
                      <th className="text-left font-medium pr-3 py-0.5">PCI</th>
                    </tr>
                  </thead>
                  <tbody>
                    {node.network_interfaces.map((iface, index) => {
                      const v4 = (iface.addresses || []).filter((a) => a.family === 'inet');
                      const v6 = (iface.addresses || []).filter((a) => a.family === 'inet6');
                      const kindColor =
                        iface.kind === 'bluefield'
                          ? 'text-primary'
                          : iface.kind === 'builtin'
                            ? 'text-info'
                            : 'text-muted-foreground';
                      return (
                        <tr key={index} className="border-t border-border/50">
                          <td className="font-mono pr-3 py-0.5">{iface.name}</td>
                          <td className={`pr-3 py-0.5 ${kindColor}`}>{iface.kind}</td>
                          <td className="pr-3 py-0.5">
                            {iface.state === 'UP' ? (
                              <span className="text-success">UP</span>
                            ) : (
                              <span className="text-muted-foreground">{iface.state}</span>
                            )}
                          </td>
                          <td className="font-mono pr-3 py-0.5">
                            {v4.length === 0 ? <span className="text-muted-foreground">—</span> : v4.map((a) => `${a.address}/${a.prefix ?? '?'}`).join(', ')}
                          </td>
                          <td className="font-mono pr-3 py-0.5">
                            {v6.length === 0 ? <span className="text-muted-foreground">—</span> : v6.map((a) => `${a.address}/${a.prefix ?? '?'}`).join(', ')}
                          </td>
                          <td className="font-mono pr-3 py-0.5 text-muted-foreground">
                            {iface.pci || '—'}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      {expanded && node.status === 'failed' && node.error_message && (
        <div className="px-3 pb-3 pt-1 border-t border-l-2 border-l-destructive">
          <p className="text-sm text-destructive">{node.error_message}</p>
        </div>
      )}
    </div>
  );
}

function DiscoveryJobList({
  jobs,
  selectedJobId,
  onSelect,
  onRerun,
  onDelete,
  rerunningJobId,
  deletingJobId,
}: {
  jobs: DiscoveryJob[];
  selectedJobId: number | null;
  onSelect: (jobId: number) => void;
  onRerun: (jobId: number) => void;
  onDelete: (jobId: number) => void;
  rerunningJobId: number | null;
  deletingJobId: number | null;
}) {
  if (jobs.length === 0) {
    return (
      <Card className="p-6 text-center">
        <Server className="h-10 w-10 mx-auto text-muted-foreground mb-3" />
        <p className="text-sm text-muted-foreground">No discovery jobs found for this project yet.</p>
      </Card>
    );
  }

  return (
    <div className="space-y-2">
      {jobs.map((job) => {
        const canRerun = job.status === 'completed' || job.status === 'failed';
        const canDelete = job.status !== 'in_progress' && job.status !== 'pending';
        return (
          <Card
            key={job.id}
            className={`p-3 cursor-pointer transition-colors ${selectedJobId === job.id ? 'ring-1 ring-primary' : 'hover:bg-muted/50'}`}
            onClick={() => onSelect(job.id)}
          >
            <div className="flex items-center justify-between gap-3">
              <div>
                <div className="flex items-center gap-2">
                  <span className="font-medium text-sm">Job #{job.id}</span>
                  {jobStatusBadge(job.status)}
                </div>
                <p className="text-xs text-muted-foreground mt-1">
                  {job.total_nodes} host{job.total_nodes !== 1 ? 's' : ''}
                  {job.completed_nodes > 0 ? ` · ${job.completed_nodes} discovered` : ''}
                  {job.failed_nodes > 0 ? ` · ${job.failed_nodes} failed` : ''}
                </p>
              </div>
              <div className="flex items-center gap-1">
                {canRerun && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 w-7 p-0"
                    title="Re-run discovery"
                    disabled={rerunningJobId !== null}
                    onClick={(e) => { e.stopPropagation(); onRerun(job.id); }}
                  >
                    {rerunningJobId === job.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                  </Button>
                )}
                {canDelete && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 w-7 p-0 text-destructive hover:text-destructive"
                    title="Delete job"
                    disabled={deletingJobId !== null}
                    onClick={(e) => { e.stopPropagation(); onDelete(job.id); }}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                )}
                <ChevronRight className="h-4 w-4 text-muted-foreground" />
              </div>
            </div>
          </Card>
        );
      })}
    </div>
  );
}

type DiscoveryFeedback = {
  level: 'info' | 'success' | 'error';
  message: string;
};

function NewDiscoveryTriggerCard({
  projectId,
  canTrigger,
  hasActiveJob,
  activeJobId,
  onTriggered,
}: {
  projectId: number;
  canTrigger: boolean;
  hasActiveJob: boolean;
  activeJobId: number | null;
  onTriggered: (job: DiscoveryJob, feedback: DiscoveryFeedback) => void;
}) {
  const triggerDiscovery = useTriggerDiscovery(projectId);
  const [ipText, setIpText] = useState('');
  const [authMethod, setAuthMethod] = useState<'password' | 'key' | 'credential'>('password');
  const [username, setUsername] = useState('root');
  const [port, setPort] = useState('22');
  const [password, setPassword] = useState('');
  const [privateKey, setPrivateKey] = useState('');
  const [credentialId, setCredentialId] = useState('');

  const { data: sshCredentials } = useQuery({
    queryKey: ['ssh-credentials', 'list'],
    queryFn: sshCredentialsApi.listSSHCredentials,
    enabled: canTrigger,
  });

  const { ips: parsedIps, errors: parseErrors } = useMemo(() => parseIpInput(ipText), [ipText]);

  const canSubmit = useMemo(() => {
    if (
      !canTrigger ||
      hasActiveJob ||
      triggerDiscovery.isPending ||
      parsedIps.length === 0 ||
      parseErrors.length > 0
    ) {
      return false;
    }

    if (authMethod === 'credential') {
      return credentialId !== '';
    }

    if (!username.trim()) return false;
    if (authMethod === 'password') return password.trim().length > 0;
    return privateKey.trim().length > 0;
  }, [
    authMethod,
    canTrigger,
    credentialId,
    hasActiveJob,
    parseErrors.length,
    parsedIps.length,
    password,
    privateKey,
    triggerDiscovery.isPending,
    username,
  ]);

  const handleTrigger = () => {
    if (!canSubmit) return;

    const payload = {
      nodes: parsedIps.map((ip) => ({ ip_address: ip })),
      ...(authMethod !== 'credential' && {
        ssh_username: username.trim(),
        ssh_port: Number.parseInt(port, 10) || 22,
        ssh_auth_type: authMethod,
      }),
      ...(authMethod === 'password' && { ssh_password: password }),
      ...(authMethod === 'key' && { ssh_private_key: privateKey }),
      ...(authMethod === 'credential' && { ssh_credential_id: Number.parseInt(credentialId, 10) }),
    };

    notify.info('Discovery run started', `Scanning ${parsedIps.length} node${parsedIps.length !== 1 ? 's' : ''}...`, { category: 'cluster' });

    triggerDiscovery.mutate(payload, {
      onSuccess: (job) => {
        // If the backend completed inline (very fast or test mode), fire the terminal
        // notification directly — the status-transition effect won't see a transition.
        if (job.status === 'completed') {
          const summary = `${job.completed_nodes}/${job.total_nodes} discovered${job.failed_nodes > 0 ? `, ${job.failed_nodes} failed` : ''}`;
          notify.success('Discovery completed', summary, { category: 'cluster' });
          onTriggered(job, { level: 'success', message: `Discovery completed (${summary})` });
          return;
        }
        if (job.status === 'failed') {
          const summary = `${job.completed_nodes}/${job.total_nodes} discovered${job.failed_nodes > 0 ? `, ${job.failed_nodes} failed` : ''}`;
          notify.error('Discovery failed', job.error_message || summary, { category: 'cluster' });
          onTriggered(job, {
            level: 'error',
            message: `Discovery finished with failures (${summary})${job.error_message ? ` — ${job.error_message}` : ''}`,
          });
          return;
        }
        onTriggered(job, {
          level: 'info',
          message: `Discovery job #${job.id} is running (${job.total_nodes} host${job.total_nodes !== 1 ? 's' : ''})`,
        });
      },
      onError: (error) => {
        notifyError(error, 'starting discovery');
      },
    });
  };

  if (!canTrigger) {
    return (
      <Card className="p-4 border-l-2 border-l-warning">
        <p className="text-sm text-warning">
          Discovery runs are owner-only for this project. You can view existing results but cannot start new runs.
        </p>
      </Card>
    );
  }

  return (
    <Card className="p-4 space-y-4">
      {hasActiveJob && (
        <div className="rounded-md border border-l-2 border-l-info px-3 py-2">
          <p className="text-xs text-info">
            Discovery job #{activeJobId} is currently active. Starting another run is disabled until it finishes.
          </p>
        </div>
      )}

      <div className="space-y-2">
        <Label htmlFor="discovery-node-ips">Host IP addresses</Label>
        <Textarea
          id="discovery-node-ips"
          rows={4}
          className="font-mono text-xs"
          placeholder={'10.0.0.10\n10.0.0.11\n198.18.0.19-22\n198.18.0.30-198.18.0.34'}
          value={ipText}
          onChange={(event) => setIpText(event.target.value)}
        />
        <p className="text-xs text-muted-foreground">
          {parsedIps.length} node{parsedIps.length !== 1 ? 's' : ''} ready — one per line, comma separated, or as a range
          (<span className="font-mono">198.18.0.19-22</span> or <span className="font-mono">198.18.0.19-198.18.0.22</span>)
        </p>
        {parseErrors.length > 0 && (
          <ul className="text-xs text-destructive list-disc list-inside space-y-0.5">
            {parseErrors.map((error, index) => (
              <li key={index}>{error}</li>
            ))}
          </ul>
        )}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <div className="space-y-2">
          <Label>Authentication method</Label>
          <Select value={authMethod} onValueChange={(value) => setAuthMethod(value as 'password' | 'key' | 'credential')}>
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="password">Password</SelectItem>
              <SelectItem value="key">SSH private key</SelectItem>
              {sshCredentials && sshCredentials.length > 0 && (
                <SelectItem value="credential">Saved SSH credential</SelectItem>
              )}
            </SelectContent>
          </Select>
        </div>

        {authMethod !== 'credential' && (
          <>
            <div className="space-y-2">
              <Label htmlFor="discovery-username">Username</Label>
              <Input
                id="discovery-username"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                placeholder="root"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="discovery-port">SSH port</Label>
              <Input
                id="discovery-port"
                type="number"
                value={port}
                onChange={(event) => setPort(event.target.value)}
                placeholder="22"
              />
            </div>
          </>
        )}
      </div>

      {authMethod === 'password' && (
        <div className="space-y-2">
          <Label htmlFor="discovery-password">Password</Label>
          <Input
            id="discovery-password"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="SSH password"
          />
        </div>
      )}

      {authMethod === 'key' && (
        <div className="space-y-2">
          <Label htmlFor="discovery-private-key">Private key (PEM/OpenSSH)</Label>
          <Textarea
            id="discovery-private-key"
            rows={5}
            className="font-mono text-xs"
            value={privateKey}
            onChange={(event) => setPrivateKey(event.target.value)}
            placeholder={'-----BEGIN OPENSSH PRIVATE KEY-----\n...\n-----END OPENSSH PRIVATE KEY-----'}
          />
        </div>
      )}

      {authMethod === 'credential' && (
        <div className="space-y-2">
          <Label>Saved SSH credential</Label>
          <Select value={credentialId} onValueChange={setCredentialId}>
            <SelectTrigger>
              <SelectValue placeholder="Select credential" />
            </SelectTrigger>
            <SelectContent>
              {sshCredentials?.map((credential) => (
                <SelectItem key={credential.id} value={String(credential.id)}>
                  {credential.name} — {credential.username}@{credential.host}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}

      <div className="flex justify-end pt-2">
        <Button
          onClick={handleTrigger}
          disabled={!canSubmit}
          title={hasActiveJob ? `Discovery job #${activeJobId} is already running` : undefined}
        >
          {triggerDiscovery.isPending ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Plus className="h-4 w-4 mr-2" />}
          Run discovery
        </Button>
      </div>
    </Card>
  );
}

export function NodeDiscoveryPanel({ projectId, canTrigger = true, jumphost, projectSshCredentialId }: NodeDiscoveryPanelProps) {
  const qc = useQueryClient();
  const [selectedJobId, setSelectedJobId] = useState<number | null>(null);
  const [triggerFeedback, setTriggerFeedback] = useState<DiscoveryFeedback | null>(null);
  const [registerOpen, setRegisterOpen] = useState(false);
  const [registerProbeResult, setRegisterProbeResult] = useState<NodeKubeconfigProbeResult | null>(null);
  const [registerLoadingNodeId, setRegisterLoadingNodeId] = useState<number | null>(null);
  // Register-as-DPU state (separate from K8s register flow)
  const [dpuRegisterTargetNode, setDpuRegisterTargetNode] = useState<DiscoveredNode | null>(null);
  const [dpuRegisterBmcIp, setDpuRegisterBmcIp] = useState('');
  const [dpuRegisterLoadingNodeId, setDpuRegisterLoadingNodeId] = useState<number | null>(null);
  // Register-as-Host state — drives the per-row + bulk Bare Metal hooks.
  // Per-row tracks one nodeId at a time; bulk uses the mutation's
  // `isPending` directly (the action is one HTTP call, not N).
  const [hostRegisterLoadingNodeId, setHostRegisterLoadingNodeId] = useState<number | null>(null);
  const registerNodeAsHostMut = useRegisterNodeAsHost(projectId);
  const registerAllHostsMut = useRegisterAllHostsInJob(projectId);
  const previousSelectedStatusRef = useRef<DiscoveryJob['status'] | null>(null);
  const { data: jobs, isLoading: jobsLoading } = useDiscoveryJobs(projectId, 20);
  const { data: job, isLoading, isError } = useDiscoveryJob(projectId, selectedJobId);

  const activeJob = useMemo(
    () => (jobs || []).find((entry) => entry.status === 'pending' || entry.status === 'in_progress') || null,
    [jobs],
  );

  const selectedJob = useMemo(
    () => (jobs || []).find((item) => item.id === selectedJobId) || null,
    [jobs, selectedJobId],
  );

  const stats = useMemo(() => {
    if (!job) return null;
    const dpuHosts = job.nodes.filter((node) => node.is_dpu_host).length;
    const dpuNodes = job.nodes.filter((node) => node.is_dpu_node).length;
    const totalDpus = job.nodes.reduce((sum, node) => sum + node.dpu_count, 0);
    const k8sNodes = job.nodes.filter((node) => node.k8s_running).length;
    return { dpuHosts, dpuNodes, totalDpus, k8sNodes };
  }, [job]);

  useEffect(() => {
    if (!job || !selectedJobId) {
      previousSelectedStatusRef.current = null;
      return;
    }

    const previous = previousSelectedStatusRef.current;
    const current = job.status;

    if (
      previous &&
      (previous === 'pending' || previous === 'in_progress') &&
      (current === 'completed' || current === 'failed')
    ) {
      if (current === 'completed') {
        const message = `Discovery job #${job.id} completed (${job.completed_nodes}/${job.total_nodes} discovered)`;
        notify.success('Discovery completed', message, { category: 'cluster' });
        setTriggerFeedback({ level: 'success', message });
      } else {
        const message = `Discovery job #${job.id} failed${job.error_message ? ` — ${job.error_message}` : ''}`;
        notify.error('Discovery failed', message, { category: 'cluster' });
        setTriggerFeedback({ level: 'error', message });
      }
    }

    previousSelectedStatusRef.current = current;
  }, [job, selectedJobId]);

  const rerunJob = useRerunDiscoveryJob(projectId);
  const deleteJob = useDeleteDiscoveryJob(projectId);
  const [rerunningJobId, setRerunningJobId] = useState<number | null>(null);
  const [deletingJobId, setDeletingJobId] = useState<number | null>(null);

  const handleRerun = (jobId: number) => {
    setRerunningJobId(jobId);
    notify.info('Re-running discovery...', undefined, { category: 'cluster' });
    rerunJob.mutate(jobId, {
      onSuccess: (updatedJob) => {
        setRerunningJobId(null);
        setSelectedJobId(updatedJob.id);
        previousSelectedStatusRef.current = updatedJob.status;
        setTriggerFeedback({
          level: 'info',
          message: `Discovery job #${updatedJob.id} is running (${updatedJob.total_nodes} host${updatedJob.total_nodes !== 1 ? 's' : ''})`,
        });
      },
      onError: (error) => {
        setRerunningJobId(null);
        notifyError(error, 're-running discovery');
      },
    });
  };

  const handleDelete = (jobId: number) => {
    if (!window.confirm(`Delete discovery job #${jobId} and all its results?`)) return;
    setDeletingJobId(jobId);
    deleteJob.mutate(jobId, {
      onSuccess: () => {
        setDeletingJobId(null);
        notify.success(`Discovery job #${jobId} deleted`, undefined, { category: 'cluster' });
        if (selectedJobId === jobId) setSelectedJobId(null);
        setTriggerFeedback(null);
      },
      onError: (error) => {
        setDeletingJobId(null);
        notifyError(error, 'deleting discovery job');
      },
    });
  };

  const handleRegisterK8s = async (nodeId: number) => {
    setRegisterLoadingNodeId(nodeId);
    try {
      const result = await discoveryApi.probeNodeKubeconfig(projectId, nodeId);
      setRegisterProbeResult(result);
      setRegisterOpen(true);
    } catch (error) {
      notifyError(error, 'probing kubeconfig from node');
    } finally {
      setRegisterLoadingNodeId(null);
    }
  };

  const handleRegisterAllDpus = async () => {
    if (!job) return;
    // BMC nodes register directly; host nodes register N in-band DPUs each;
    // DPU-OS-only nodes need interactive BMC IP input → skipped with note.
    const bmcNodes = job.nodes.filter((n) => n.is_dpu_bmc);
    const hostNodes = job.nodes.filter(
      (n) => n.is_dpu_host && !n.is_dpu_bmc && !n.is_dpu_node && n.dpu_count > 0,
    );
    const skippedOsOnly = job.nodes.filter(
      (n) => n.is_dpu_node && !n.is_dpu_bmc && !n.is_dpu_host,
    ).length;
    let bmcOk = 0;
    let inbandOk = 0;
    let failed = 0;
    for (const node of [...bmcNodes, ...hostNodes]) {
      setDpuRegisterLoadingNodeId(node.id);
      try {
        const result = await discoveryApi.registerNodeAsDpu(projectId, node.id, {});
        for (const d of result.dpus) {
          if (d.access_mode === 'in-band') inbandOk += 1;
          else bmcOk += 1;
        }
      } catch {
        failed += 1;
      }
    }
    setDpuRegisterLoadingNodeId(null);
    if (bmcOk + inbandOk > 0) {
      qc.invalidateQueries({ queryKey: queryKeys.dpuProvisioning.dpus.byProject(projectId) });
    }
    const parts: string[] = [];
    if (bmcOk > 0) parts.push(`${bmcOk} BMC DPU${bmcOk === 1 ? '' : 's'}`);
    if (inbandOk > 0) parts.push(`${inbandOk} in-band DPU${inbandOk === 1 ? '' : 's'}`);
    const registered = parts.join(' + ');
    const summary: string[] = [];
    if (registered) summary.push(`${registered} registered`);
    if (failed > 0) summary.push(`${failed} failed`);
    if (skippedOsOnly > 0) {
      summary.push(
        `${skippedOsOnly} DPU-OS node${skippedOsOnly === 1 ? '' : 's'} skipped — use '+ Register DPU' per row to supply BMC IP`,
      );
    }
    const msg = summary.join('; ') || 'No DPUs to register';
    if (failed > 0) notify.error(msg, undefined, { category: 'cluster' });
    else notify.success(msg, undefined, { category: 'cluster' });
  };

  const [k8sRegisterQueue, setK8sRegisterQueue] = useState<DiscoveredNode[]>([]);

  const handleRegisterAsHost = async (node: DiscoveredNode) => {
    setHostRegisterLoadingNodeId(node.id);
    try {
      const result = await registerNodeAsHostMut.mutateAsync(node.id);
      const label = result.name || result.host_ip;
      if (result.status === 'created') {
        notify.success(`Registered host ${label} in Bare Metal tab`, undefined, { category: 'cluster' });
      } else {
        notify.info(`Host ${label} was already registered — no change`, undefined, { category: 'cluster' });
      }
    } catch (error) {
      notifyError(error, 'registering host');
    } finally {
      setHostRegisterLoadingNodeId(null);
    }
  };

  const handleRegisterAllHosts = async () => {
    if (!job) return;
    try {
      const result = await registerAllHostsMut.mutateAsync(job.id);
      const parts: string[] = [];
      if (result.created > 0) {
        parts.push(`${result.created} new host${result.created === 1 ? '' : 's'}`);
      }
      if (result.existing > 0) {
        parts.push(
          `${result.existing} already registered`,
        );
      }
      const summary = parts.length ? parts.join(', ') : 'no eligible hosts';
      notify.success(`Bare Metal: ${summary}`, undefined, { category: 'cluster' });
    } catch (error) {
      notifyError(error, 'registering all hosts');
    }
  };

  const handleRegisterAllK8s = async () => {
    if (!job) return;
    const k8sNodes = job.nodes.filter((n) => n.k8s_running);
    if (k8sNodes.length === 0) return;
    // Queue the rest; open the first one via the single-node handler.
    setK8sRegisterQueue(k8sNodes.slice(1));
    await handleRegisterK8s(k8sNodes[0].id);
  };

  const handleRegisterAsDpu = async (node: DiscoveredNode) => {
    // BMC case: register immediately. Host-only case (is_dpu_host without
    // BMC/node flags): register N in-band DPUs non-interactively. DPU-OS
    // case: open dialog to collect BMC IP.
    if (node.is_dpu_bmc) {
      setDpuRegisterLoadingNodeId(node.id);
      try {
        const result = await discoveryApi.registerNodeAsDpu(projectId, node.id, {});
        const d = result.dpus[0];
        notify.success(`Registered DPU (BMC ${d?.bmc_ip ?? '?'})`, undefined, { category: 'cluster' });
        qc.invalidateQueries({ queryKey: queryKeys.dpuProvisioning.dpus.byProject(projectId) });
      } catch (error) {
        notifyError(error, 'registering as DPU');
      } finally {
        setDpuRegisterLoadingNodeId(null);
      }
    } else if (node.is_dpu_host && !node.is_dpu_node) {
      setDpuRegisterLoadingNodeId(node.id);
      try {
        const result = await discoveryApi.registerNodeAsDpu(projectId, node.id, {});
        notify.success(
          `Registered ${result.dpus.length} in-band DPU${result.dpus.length === 1 ? '' : 's'} on host ${node.ip_address}`,
          undefined,
          { category: 'cluster' },
        );
        qc.invalidateQueries({ queryKey: queryKeys.dpuProvisioning.dpus.byProject(projectId) });
      } catch (error) {
        notifyError(error, 'registering in-band DPUs');
      } finally {
        setDpuRegisterLoadingNodeId(null);
      }
    } else if (node.is_dpu_node) {
      setDpuRegisterTargetNode(node);
      setDpuRegisterBmcIp('');
    }
  };

  const submitDpuOsRegistration = async () => {
    if (!dpuRegisterTargetNode) return;
    const trimmed = dpuRegisterBmcIp.trim();
    if (!trimmed) {
      notify.error('BMC IP is required');
      return;
    }
    setDpuRegisterLoadingNodeId(dpuRegisterTargetNode.id);
    try {
      const result = await discoveryApi.registerNodeAsDpu(projectId, dpuRegisterTargetNode.id, {
        bmc_ip: trimmed,
      });
      const d = result.dpus[0];
      notify.success(`Registered DPU (BMC ${d?.bmc_ip ?? '?'}, DPU OS ${d?.dpu_os_ip ?? '?'})`, undefined, { category: 'cluster' });
      qc.invalidateQueries({ queryKey: queryKeys.dpuProvisioning.dpus.byProject(projectId) });
      setDpuRegisterTargetNode(null);
    } catch (error) {
      notifyError(error, 'registering as DPU');
    } finally {
      setDpuRegisterLoadingNodeId(null);
    }
  };

  const onTriggered = (triggeredJob: DiscoveryJob, feedback: DiscoveryFeedback) => {
    setSelectedJobId(triggeredJob.id);
    setTriggerFeedback(feedback);
    previousSelectedStatusRef.current = triggeredJob.status;
  };

  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-lg font-semibold flex items-center gap-2">
          <Search className="h-5 w-5" />
          Node Discovery
        </h3>
        <p className="text-sm text-muted-foreground mt-1">
          Trigger on-demand discovery runs and review project discovery results.
        </p>
        <p className="text-sm text-muted-foreground mt-2">
          <span className="font-medium">Discovery is read-only.</span>{' '}
          bnk-forge SSHes into each target and probes Redfish on port 443 to
          capture OS, <span className="font-mono text-xs">lspci</span>, and
          BMC facts. Nothing on the targets is changed — no config writes,
          no reboots, no package installs. Safe to run on production hosts.
        </p>
        {jumphost && (
          <div className="flex items-center gap-1.5 mt-2 text-xs text-muted-foreground">
            <Network className="h-3.5 w-3.5 flex-shrink-0" />
            <span>Via jumphost: {jumphost.name}</span>
            <span>({jumphost.username}@{jumphost.host})</span>
          </div>
        )}
      </div>

      <NewDiscoveryTriggerCard
        projectId={projectId}
        canTrigger={canTrigger}
        hasActiveJob={Boolean(activeJob)}
        activeJobId={activeJob?.id ?? null}
        onTriggered={onTriggered}
      />

      {triggerFeedback && (
        <Card
          className={`p-3 border-l-2 ${
            triggerFeedback.level === 'success'
              ? 'border-l-success'
              : triggerFeedback.level === 'error'
                ? 'border-l-destructive'
                : 'border-l-info'
          }`}
        >
          <p
            className={`text-sm ${
              triggerFeedback.level === 'success'
                ? 'text-success'
                : triggerFeedback.level === 'error'
                  ? 'text-destructive'
                  : 'text-info'
            }`}
          >
            {triggerFeedback.message}
          </p>
        </Card>
      )}

      <Card className="p-4 space-y-3">
        <div className="flex items-center justify-between">
          <p className="text-sm font-medium">Recent discovery jobs</p>
          <Button
            variant="outline"
            size="sm"
            disabled={selectedJobId === null}
            onClick={() => setSelectedJobId(null)}
          >
            Clear selection
          </Button>
        </div>
        {jobsLoading ? (
          <div className="flex items-center justify-center py-6">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <DiscoveryJobList
            jobs={jobs || []}
            selectedJobId={selectedJobId}
            onSelect={setSelectedJobId}
            onRerun={handleRerun}
            onDelete={handleDelete}
            rerunningJobId={rerunningJobId}
            deletingJobId={deletingJobId}
          />
        )}
      </Card>

      {isLoading ? (
        <div className="flex items-center justify-center p-8">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : null}

      {isError && selectedJobId ? (
        <Card className="p-4 border-l-2 border-l-destructive">
          <p className="text-sm text-destructive">
            Failed to load discovery job #{selectedJobId}. Verify project scope.
          </p>
        </Card>
      ) : null}

      {job && selectedJob && (
        <JobDetail
          job={job}
          selectedJob={selectedJob}
          stats={stats}
          onRerun={() => handleRerun(job.id)}
          onDelete={() => handleDelete(job.id)}
          isRerunning={rerunningJobId === job.id}
          isDeleting={deletingJobId === job.id}
          onRegisterK8s={handleRegisterK8s}
          registerLoadingNodeId={registerLoadingNodeId}
          onRegisterAsDpu={handleRegisterAsDpu}
          dpuRegisterLoadingNodeId={dpuRegisterLoadingNodeId}
          onRegisterAllDpus={handleRegisterAllDpus}
          onRegisterAllK8s={handleRegisterAllK8s}
          onRegisterAsHost={handleRegisterAsHost}
          hostRegisterLoadingNodeId={hostRegisterLoadingNodeId}
          onRegisterAllHosts={handleRegisterAllHosts}
          isRegisterAllHostsLoading={registerAllHostsMut.isPending}
        />
      )}

      <ClusterConfigDialog
        open={registerOpen}
        onOpenChange={async (open) => {
          setRegisterOpen(open);
          if (!open) {
            setRegisterProbeResult(null);
            // Advance the batch K8s queue, if any.
            if (k8sRegisterQueue.length > 0) {
              const [next, ...rest] = k8sRegisterQueue;
              setK8sRegisterQueue(rest);
              await handleRegisterK8s(next.id);
            }
          }
        }}
        projectId={projectId}
        projectSshCredentialId={projectSshCredentialId}
        preFilledProbe={registerProbeResult}
      />

      <Dialog
        open={dpuRegisterTargetNode !== null}
        onOpenChange={(open) => {
          if (!open) setDpuRegisterTargetNode(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Register DPU OS</DialogTitle>
            <DialogDescription>
              {dpuRegisterTargetNode && (
                <>
                  The discovered IP{' '}
                  <span className="font-mono">{dpuRegisterTargetNode.ip_address}</span> is a DPU
                  DPU OS. Enter the DPU's BMC IP to complete registration.
                </>
              )}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2 py-2">
            <Label htmlFor="dpu-bmc-ip">BMC IP</Label>
            <Input
              id="dpu-bmc-ip"
              placeholder="192.168.68.100"
              value={dpuRegisterBmcIp}
              onChange={(e) => setDpuRegisterBmcIp(e.target.value)}
            />
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDpuRegisterTargetNode(null)}
              disabled={dpuRegisterLoadingNodeId !== null}
            >
              Cancel
            </Button>
            <Button
              onClick={submitDpuOsRegistration}
              disabled={dpuRegisterBmcIp.trim() === '' || dpuRegisterLoadingNodeId !== null}
            >
              {dpuRegisterLoadingNodeId !== null && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              Register
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function JobDetail({
  job,
  selectedJob,
  stats,
  onRerun,
  onDelete,
  isRerunning,
  isDeleting,
  onRegisterK8s,
  registerLoadingNodeId,
  onRegisterAsDpu,
  dpuRegisterLoadingNodeId,
  onRegisterAllDpus,
  onRegisterAllK8s,
  onRegisterAsHost,
  hostRegisterLoadingNodeId,
  onRegisterAllHosts,
  isRegisterAllHostsLoading,
}: {
  job: DiscoveryJob;
  selectedJob: DiscoveryJob;
  stats: { dpuHosts: number; dpuNodes: number; totalDpus: number; k8sNodes: number } | null;
  onRerun: () => void;
  onDelete: () => void;
  isRerunning: boolean;
  isDeleting: boolean;
  onRegisterK8s?: (nodeId: number) => void;
  registerLoadingNodeId?: number | null;
  onRegisterAsDpu?: (node: DiscoveredNode) => void;
  dpuRegisterLoadingNodeId?: number | null;
  onRegisterAllDpus?: () => void;
  onRegisterAllK8s?: () => void;
  onRegisterAsHost?: (node: DiscoveredNode) => void;
  hostRegisterLoadingNodeId?: number | null;
  onRegisterAllHosts?: () => void;
  isRegisterAllHostsLoading?: boolean;
}) {
  const canRerun = job.status === 'completed' || job.status === 'failed';
  const canDelete = job.status !== 'in_progress' && job.status !== 'pending';

  // Per-column sort for the discovered-node list. Cycle: asc → desc → none.
  const [sortKey, setSortKey] = useState<NodeSortKey | null>(null);
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');
  const setSortState = (nextKey: NodeSortKey) => {
    if (nextKey !== sortKey) {
      setSortKey(nextKey);
      setSortDir('asc');
    } else if (sortDir === 'asc') {
      setSortDir('desc');
    } else {
      setSortKey(null);
      setSortDir('asc');
    }
  };

  const sortedNodes = useMemo(
    () => (sortKey ? sortNodes(job.nodes, sortKey, sortDir) : job.nodes),
    [job.nodes, sortKey, sortDir],
  );

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h4 className="text-base font-semibold">Discovery Job #{job.id}</h4>
          {jobStatusBadge(job.status)}
          <span className="text-xs text-muted-foreground">
            {selectedJob.created_at ? new Date(selectedJob.created_at).toLocaleString() : 'Unknown time'}
          </span>
          {job.error_message && (
            <span className="text-sm text-warning">{job.error_message}</span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {canRerun && (
            <Button
              variant="outline"
              size="sm"
              onClick={onRerun}
              disabled={isRerunning}
            >
              {isRerunning ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <RefreshCw className="h-4 w-4 mr-1" />}
              Re-run
            </Button>
          )}
          {canDelete && (
            <Button
              variant="ghost"
              size="sm"
              className="text-destructive hover:text-destructive"
              onClick={onDelete}
              disabled={isDeleting}
            >
              <Trash2 className="h-4 w-4 mr-1" />
              Delete
            </Button>
          )}
        </div>
      </div>

          {stats && job.status === 'completed' && (
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
              <Card className="p-3 text-center">
                <p className="text-2xl font-bold">{job.total_nodes}</p>
                <p className="text-xs text-muted-foreground">Total Hosts</p>
              </Card>
              <Card className="p-3 text-center">
                <p className="text-2xl font-bold text-success">{job.completed_nodes}</p>
                <p className="text-xs text-muted-foreground">Discovered</p>
              </Card>
              <Card className="p-3 text-center">
                <p className={`text-2xl font-bold ${stats.dpuHosts + stats.dpuNodes > 0 ? 'text-primary' : ''}`}>{stats.totalDpus}</p>
                <p className="text-xs text-muted-foreground">DPUs Found</p>
              </Card>
              <Card className="p-3 text-center">
                <p className={`text-2xl font-bold ${stats.k8sNodes > 0 ? 'text-info' : ''}`}>{stats.k8sNodes}</p>
                <p className="text-xs text-muted-foreground">K8s Running</p>
              </Card>
              <Card className="p-3 text-center">
                <p className={`text-2xl font-bold ${job.failed_nodes > 0 ? 'text-destructive' : ''}`}>{job.failed_nodes}</p>
                <p className="text-xs text-muted-foreground">Failed</p>
              </Card>
            </div>
          )}

          {(job.status === 'in_progress' || job.status === 'pending') && (
            <div className="space-y-2">
              <div className="flex justify-between text-sm text-muted-foreground">
                <span>{job.status === 'pending' ? 'Starting discovery...' : 'Discovering nodes...'}</span>
                <span>{job.completed_nodes + job.failed_nodes} / {job.total_nodes}</span>
              </div>
              <div className="h-2 bg-muted rounded-full overflow-hidden">
                <div
                  className="h-full bg-primary transition-all duration-500"
                  style={{ width: `${((job.completed_nodes + job.failed_nodes) / Math.max(job.total_nodes, 1)) * 100}%` }}
                />
              </div>
            </div>
          )}

          <div className="flex items-center justify-between gap-3 flex-wrap">
            <NodeSortBar sortKey={sortKey} sortDir={sortDir} onChange={setSortState} />
            <div className="flex items-center gap-2">
              {onRegisterAllDpus
                && job.nodes.some(
                  (n) =>
                    n.is_dpu_bmc
                    || n.is_dpu_node
                    || (n.is_dpu_host && n.dpu_count > 0),
                ) && (
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-7 text-xs px-2"
                    onClick={onRegisterAllDpus}
                    disabled={dpuRegisterLoadingNodeId !== null}
                    title="Register every discovered DPU (BMC + in-band; DPU-OS-only nodes still need per-row registration)"
                  >
                    {dpuRegisterLoadingNodeId !== null
                      ? <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                      : <Plus className="h-3 w-3 mr-1" />}
                    Register all DPUs
                  </Button>
              )}
              {/* Add every DPU-host (the x86 server carrying DPUs)
                  as a Bare Metal row. Idempotent — operators can re-run
                  this on a refreshed discovery without producing
                  duplicates or errors. */}
              {onRegisterAllHosts
                && job.nodes.some((n) => n.is_dpu_host && n.dpu_count > 0) && (
                <Button
                  size="sm"
                  variant="outline"
                  className="h-7 text-xs px-2"
                  onClick={onRegisterAllHosts}
                  disabled={isRegisterAllHostsLoading}
                  title="Add every DPU-host to the Bare Metal tab. Already-registered hosts are silently accepted."
                >
                  {isRegisterAllHostsLoading
                    ? <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                    : <Plus className="h-3 w-3 mr-1" />}
                  Register all Hosts
                </Button>
              )}
              {onRegisterAllK8s && job.nodes.some((n) => n.k8s_running) && (
                <Button
                  size="sm"
                  variant="outline"
                  className="h-7 text-xs px-2"
                  onClick={onRegisterAllK8s}
                  disabled={registerLoadingNodeId !== null}
                  title="Register every discovered Kubernetes cluster (opens the config dialog for each in turn)"
                >
                  {registerLoadingNodeId !== null
                    ? <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                    : <Plus className="h-3 w-3 mr-1" />}
                  Register all K8s
                </Button>
              )}
            </div>
          </div>
          <div className="space-y-2">
            {sortedNodes.map((node) => (
              <NodeRow
                key={node.id}
                node={node}
                onRegisterK8s={onRegisterK8s}
                isRegisterLoading={registerLoadingNodeId === node.id}
                onRegisterAsDpu={onRegisterAsDpu}
                isRegisterAsDpuLoading={dpuRegisterLoadingNodeId === node.id}
                onRegisterAsHost={onRegisterAsHost}
                isRegisterAsHostLoading={hostRegisterLoadingNodeId === node.id}
              />
            ))}
          </div>

          <ConnectivityMatrix
            status={job.connectivity_status}
            matrix={job.connectivity_matrix}
          />
    </div>
  );
}


// ─── Per-column sort for the discovered-node list ─────────────────────────

type NodeSortKey = 'ip' | 'hostname' | 'status' | 'os' | 'arch' | 'dpu' | 'k8s';

const SORT_LABELS: Record<NodeSortKey, string> = {
  ip: 'IP',
  hostname: 'Hostname',
  status: 'Status',
  os: 'OS',
  arch: 'Arch',
  dpu: 'DPU',
  k8s: 'K8s',
};

const STATUS_ORDER: Record<string, number> = {
  in_progress: 0,
  connecting: 1,
  discovering: 2,
  pending: 3,
  completed: 4,
  failed: 5,
};

function ipKey(ip: string | null | undefined): number {
  if (!ip) return Number.MAX_SAFE_INTEGER;
  const parts = ip.split('.').map((p) => parseInt(p, 10));
  if (parts.length !== 4 || parts.some((p) => Number.isNaN(p))) return Number.MAX_SAFE_INTEGER;
  return (parts[0] << 24) + (parts[1] << 16) + (parts[2] << 8) + parts[3];
}

function cmpNullable(
  a: string | number | boolean | null | undefined,
  b: string | number | boolean | null | undefined,
  dir: 'asc' | 'desc',
): number {
  const aNil = a == null || a === '';
  const bNil = b == null || b === '';
  if (aNil && bNil) return 0;
  if (aNil) return 1;   // nulls always at the end
  if (bNil) return -1;
  const mul = dir === 'asc' ? 1 : -1;
  if (typeof a === 'number' && typeof b === 'number') return (a - b) * mul;
  if (typeof a === 'boolean' && typeof b === 'boolean') {
    return ((a === b) ? 0 : (a ? -1 : 1)) * mul;
  }
  return String(a).localeCompare(String(b)) * mul;
}

function sortNodes(
  nodes: readonly DiscoveredNode[],
  key: NodeSortKey,
  dir: 'asc' | 'desc',
): DiscoveredNode[] {
  const copy = [...nodes];
  copy.sort((a, b) => {
    switch (key) {
      case 'ip':
        return cmpNullable(ipKey(a.ip_address), ipKey(b.ip_address), dir);
      case 'hostname':
        return cmpNullable(a.hostname, b.hostname, dir);
      case 'status': {
        const ax = STATUS_ORDER[a.status] ?? 99;
        const bx = STATUS_ORDER[b.status] ?? 99;
        return cmpNullable(ax, bx, dir);
      }
      case 'os':
        return cmpNullable(a.os_pretty_name ?? a.os_type, b.os_pretty_name ?? b.os_type, dir);
      case 'arch':
        return cmpNullable(a.architecture, b.architecture, dir);
      case 'dpu': {
        // DPU host > DPU node > BMC > none. Present rows come first.
        const rank = (n: DiscoveredNode): number =>
          n.is_dpu_host ? 0 : n.is_dpu_node ? 1 : n.is_dpu_bmc ? 2 : 3;
        return cmpNullable(rank(a), rank(b), dir);
      }
      case 'k8s': {
        const rank = (n: DiscoveredNode): number =>
          n.k8s_running ? 0 : n.k8s_installed ? 1 : 2;
        return cmpNullable(rank(a), rank(b), dir);
      }
    }
  });
  return copy;
}

function NodeSortBar({
  sortKey,
  sortDir,
  onChange,
}: {
  sortKey: NodeSortKey | null;
  sortDir: 'asc' | 'desc';
  onChange: (k: NodeSortKey) => void;
}) {
  const keys: NodeSortKey[] = ['ip', 'hostname', 'status', 'os', 'arch', 'dpu', 'k8s'];
  return (
    <div className="flex items-center gap-1 text-xs flex-wrap">
      <span className="text-muted-foreground mr-1">Sort by:</span>
      {keys.map((k) => {
        const active = sortKey === k;
        return (
          <button
            key={k}
            type="button"
            onClick={() => onChange(k)}
            className={
              'h-6 px-2 rounded border transition-colors ' +
              (active
                ? 'border-primary text-primary bg-primary/10'
                : 'border-border text-muted-foreground hover:bg-muted/50')
            }
            title={active ? `Sorted ${sortDir === 'asc' ? 'ascending' : 'descending'} — click to ${sortDir === 'asc' ? 'sort descending' : 'clear'}` : `Sort by ${SORT_LABELS[k]}`}
          >
            {SORT_LABELS[k]}
            {active && (
              <span className="ml-1 opacity-70">
                {sortDir === 'asc' ? '▲' : '▼'}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
