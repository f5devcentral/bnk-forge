/**
 * DpuPanel — the "DPU" tab on Existing Kubernetes projects.
 *
 * Phase 3 (storage) + Phase 4 (Discover button, NicMode / HostPrivilegeLevel
 * columns, expanded inventory view). Per-DPU polling auto-refreshes the table
 * while any discovery is running.
 */
import { useState, Fragment } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import {
  ArrowLeftRight,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  HardDriveDownload,
  Link2,
  Loader2,
  MonitorPlay,
  Pencil,
  Power,
  Server,
  Search,
  TerminalSquare,
  Terminal,
  Trash2,
  X,
  XCircle,
  Zap,
} from 'lucide-react';
import { notify, notifyError } from '@/lib/notify';
import { cn } from '@/lib/utils';
import {
  useCancelFlash,
  useCreateDpu,
  useDeleteDpu,
  useConvertToEthernet,
  useDisableRshim,
  useRegisterDpuHost,
  useDiscoverAllDpus,
  useDiscoverDpu,
  useDpusWithPolling,
  useEnableRshimOnBmc,
  useFlashAllDpus,
  useFlashDpu,
  useProbeDpuOs,
  useProbeDpuOsAll,
  useRenderBfConf,
  useResetDpu,
  useResetDpuNic,
  useResetDpuToDefaults,
  useUpdateDpu,
} from '@/hooks/useDpus';
import type { DpuResetType } from '@/lib/api/dpus';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuPortal,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import type { Dpu, DpuCreate, DpuUpdate } from '@/lib/api/dpus';
import { Checkbox } from '@/components/ui/checkbox';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { DpuProjectSettingsCard } from './DpuProjectSettingsCard';
import { DpuFormDialog } from './DpuFormDialog';
import { DpuInventoryDetail } from './DpuInventoryDetail';
import { DpuInterfaceTable } from './DpuInterfaceTable';
import { DpuConnectivityTables } from './DpuConnectivityTables';
import { DpuConnectivityMatrixCard } from './DpuConnectivityMatrixCard';
import { DpuTerminalDialog, type DpuTerminalMode } from './DpuTerminalDialog';
import { RshimInstallDialog } from './RshimInstallDialog';
import { dpuRowLabel, dpuShortLabel } from './dpu-label';

interface DpuPanelProps {
  projectId: number;
  isOwner: boolean;
}

function extractRenderError(err: unknown): string {
  const e = err as {
    response?: {
      data?: { error?: { message?: string }; detail?: string; message?: string };
    };
  };
  return (
    e.response?.data?.error?.message
    ?? e.response?.data?.detail
    ?? e.response?.data?.message
    ?? (err instanceof Error ? err.message : 'Render failed')
  );
}

export function DpuPanel({ projectId, isOwner }: DpuPanelProps) {
  const { data: list, isLoading, error } = useDpusWithPolling(projectId);
  const createMut = useCreateDpu(projectId);
  const updateMut = useUpdateDpu(projectId);
  const deleteMut = useDeleteDpu(projectId);
  const discoverMut = useDiscoverDpu(projectId);
  const discoverAllMut = useDiscoverAllDpus(projectId);
  const probeOsMut = useProbeDpuOs(projectId);
  const probeOsAllMut = useProbeDpuOsAll(projectId);
  const resetMut = useResetDpu(projectId);
  const resetNicMut = useResetDpuNic(projectId);
  const resetDefaultsMut = useResetDpuToDefaults(projectId);
  const flashMut = useFlashDpu(projectId);
  const flashAllMut = useFlashAllDpus(projectId);
  const cancelFlashMut = useCancelFlash(projectId);
  const renderBfConfMut = useRenderBfConf(projectId);
  const enableRshimMut = useEnableRshimOnBmc(projectId);
  const disableRshimMut = useDisableRshim(projectId);
  const convertToEthernetMut = useConvertToEthernet(projectId);
  const registerHostMut = useRegisterDpuHost(projectId);

  const [dialogOpen, setDialogOpen] = useState(false);
  const [dialogMode, setDialogMode] = useState<'create' | 'edit'>('create');
  const [editing, setEditing] = useState<Dpu | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<Dpu | null>(null);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [confirmReset, setConfirmReset] = useState<{ dpu: Dpu; resetType: DpuResetType } | null>(null);
  const [confirmResetNic, setConfirmResetNic] = useState<Dpu | null>(null);
  const [terminalTarget, setTerminalTarget] = useState<{ dpu: Dpu; mode: DpuTerminalMode } | null>(null);
  const [confirmFlash, setConfirmFlash] = useState<Dpu | null>(null);
  const [confirmFlashAll, setConfirmFlashAll] = useState(false);
  const [factoryResetDpu, setFactoryResetDpu] = useState<Dpu | null>(null);
  const [rshimDpu, setRshimDpu] = useState<Dpu | null>(null);
  const [confirmDisableRshim, setConfirmDisableRshim] = useState<Dpu | null>(null);
  const [confirmConvertEth, setConfirmConvertEth] = useState<Dpu | null>(null);
  const [confirmDeleteAll, setConfirmDeleteAll] = useState(false);
  const [deletingAll, setDeletingAll] = useState(false);
  const [previewDpu, setPreviewDpu] = useState<Dpu | null>(null);
  const [previewContent, setPreviewContent] = useState<string>('');
  const [previewAt, setPreviewAt] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState<string>('');
  const [factoryOpts, setFactoryOpts] = useState<{
    bios: boolean; bmc: boolean; nic: boolean; bmc_deep: boolean;
  }>({
    bios: true, bmc: true, nic: true, bmc_deep: true,
  });

  const toggleExpand = (id: number) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const openEdit = (dpu: Dpu) => {
    setDialogMode('edit');
    setEditing(dpu);
    setDialogOpen(true);
  };

  const submit = async (payload: DpuCreate | DpuUpdate) => {
    try {
      if (dialogMode === 'create') {
        await createMut.mutateAsync(payload as DpuCreate);
      } else if (editing) {
        await updateMut.mutateAsync({ dpuId: editing.id, data: payload as DpuUpdate });
      }
    } catch (err) {
      notify.error(err instanceof Error ? err.message : 'Save failed');
    }
  };

  const discover = async (dpu: Dpu) => {
    try {
      await discoverMut.mutateAsync(dpu.id);
      const label =
        dpu.access_mode === 'in-band'
          ? `in-band via ${dpu.host_node_ip ?? dpu.name ?? `#${dpu.id}`}`
          : (dpu.bmc_ip ?? dpu.name ?? `#${dpu.id}`);
      notify.success(`Discovering ${label}…`, undefined, { category: 'deployment' });
    } catch (err) {
      notify.error(err instanceof Error ? err.message : 'Discover failed');
    }
  };

  const discoverAll = async () => {
    try {
      const res = await discoverAllMut.mutateAsync();
      notify.success(`Discovering ${res.enqueued} DPU(s), up to ${res.max_parallel} in parallel`, undefined, { category: 'deployment' });
    } catch (err) {
      notify.error(err instanceof Error ? err.message : 'Discover All failed');
    }
  };

  const enableRshimOnBmc = async (dpu: Dpu) => {
    try {
      await enableRshimMut.mutateAsync(dpu.id);
      notify.success(`Enabling rshim on BMC ${dpu.bmc_ip ?? `#${dpu.id}`}…`, undefined, { category: 'deployment' });
    } catch (err) {
      notify.error(err instanceof Error ? err.message : 'Enable rshim on BMC failed');
    }
  };

  const disableRshim = async (dpu: Dpu) => {
    const side = dpu.access_mode === 'in-band' ? 'host' : 'BMC';
    const target =
      dpu.access_mode === 'in-band'
        ? (dpu.host_hostname ?? dpu.host_node_ip ?? `#${dpu.id}`)
        : (dpu.bmc_ip ?? `#${dpu.id}`);
    try {
      await disableRshimMut.mutateAsync(dpu.id);
      notify.success(`Disabling rshim on ${side} ${target}…`, undefined, { category: 'deployment' });
    } catch (err) {
      notify.error(err instanceof Error ? err.message : `Disable rshim on ${side} failed`);
    }
  };

  const registerHost = async (dpu: Dpu) => {
    try {
      const result = await registerHostMut.mutateAsync(dpu.id);
      const label = result.name || result.host_ip;
      if (result.status === 'created') {
        notify.success(`Registered host ${label} in Bare Metal tab`, undefined, { category: 'fleet' });
      } else {
        notify.info(`Host ${label} was already registered — no change`, undefined, { category: 'fleet' });
      }
    } catch (err) {
      notify.error(err instanceof Error ? err.message : 'Register host failed');
    }
  };

  const convertToEthernet = async (dpu: Dpu) => {
    try {
      await convertToEthernetMut.mutateAsync(dpu.id);
      notify.success(
        `Converting ${dpuRowLabel(dpu)} to Ethernet — DPU is rebooting (~1-2 min). `
          + 'Click Discover after it comes back up.',
        undefined,
        { category: 'deployment' },
      );
    } catch (err) {
      notify.error(err instanceof Error ? err.message : 'Convert to Ethernet failed');
    }
  };

  const probeOs = async (dpu: Dpu) => {
    try {
      await probeOsMut.mutateAsync(dpu.id);
      const via =
        dpu.access_mode === 'in-band'
          ? `tmfifo on host ${dpu.host_node_ip ?? '?'}`
          : (dpu.bmc_ip ?? '?');
      notify.success(`Probing DPU OS via ${via}…`, undefined, { category: 'deployment' });
    } catch (err) {
      notify.error(err instanceof Error ? err.message : 'DPU OS probe failed');
    }
  };

  const probeOsAll = async () => {
    try {
      const res = await probeOsAllMut.mutateAsync();
      notify.success(`Probing ${res.enqueued} DPU(s), up to ${res.max_parallel} in parallel`, undefined, { category: 'deployment' });
    } catch (err) {
      notify.error(err instanceof Error ? err.message : 'Probe DPU OS All failed');
    }
  };

  const doFlash = async () => {
    if (!confirmFlash) return;
    try {
      await flashMut.mutateAsync(confirmFlash.id);
      notify.success(`Flashing ${dpuRowLabel(confirmFlash)}…`, undefined, { category: 'deployment' });
    } catch (err) {
      // Backend wraps errors as `{ error: { message: ... } }`; use the
      // shared parser so the bf.conf preflight detail (e.g. missing
      // VLAN IPs) actually shows up.
      notifyError(err, 'flashing DPU');
    } finally {
      setConfirmFlash(null);
    }
  };

  const doFlashAll = async () => {
    try {
      const res = await flashAllMut.mutateAsync();
      notify.success(`Flashing ${res.enqueued} DPU(s), up to ${res.max_parallel} in parallel`, undefined, { category: 'deployment' });
      if (res.skipped_render_failures && res.skipped_render_failures.length > 0) {
        const first = res.skipped_render_failures[0];
        const extra = res.skipped_render_failures.length - 1;
        notify.error(
          `Skipped ${res.skipped_render_failures.length} DPU(s) whose bf.conf wouldn't render.\n`
            + `DPU #${first.dpu_id}: ${String(first.error).split(':')[0]}`
            + (extra > 0 ? `\n(+ ${extra} more — check each DPU's status and retry)` : ''),
        );
      }
    } catch (err) {
      notifyError(err, 'flashing all DPUs');
    } finally {
      setConfirmFlashAll(false);
    }
  };

  const cancelFlash = async (dpu: Dpu) => {
    try {
      await cancelFlashMut.mutateAsync(dpu.id);
      notify.success(`Cancelled flash on ${dpuRowLabel(dpu)}`, undefined, { category: 'deployment' });
    } catch (err) {
      notify.error(err instanceof Error ? err.message : 'Cancel failed');
    }
  };

  const openPreviewBfConf = async (dpu: Dpu) => {
    setPreviewDpu(dpu);
    setPreviewContent('');
    setPreviewAt(null);
    setPreviewError('');
    // Always re-render on open so the preview reflects the current DPU +
    // project settings (and surfaces validation errors like missing uplink
    // IPs). Any previously-cached render is overwritten server-side.
    try {
      const res = await renderBfConfMut.mutateAsync(dpu.id);
      setPreviewContent(res.content);
      setPreviewAt(res.rendered_at);
    } catch (err) {
      setPreviewError(extractRenderError(err));
    }
  };

  const downloadBfConf = () => {
    if (!previewDpu || !previewContent) return;
    const blob = new Blob([previewContent], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const name = previewDpu.serial_number || `dpu-${previewDpu.id}`;
    a.download = `bf-conf-${name.replace(/[^a-z0-9._-]+/gi, '_')}.conf`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };

  const openFactoryReset = (dpu: Dpu) => {
    // NIC step requires either DPU OS reachable (BMC path) or host-side
    // mlxconfig (in-band path). For in-band the operator opts-in — it's
    // disruptive (flaps PCIe + reboots DPU), so leave it OFF by default.
    setFactoryOpts({
      bios: true,
      bmc: true,
      nic: dpu.access_mode === 'in-band' ? false : dpu.dpu_os_status === 'reachable',
      bmc_deep: true,
    });
    setFactoryResetDpu(dpu);
  };

  const doFactoryReset = async () => {
    if (!factoryResetDpu) return;
    try {
      const res = await resetDefaultsMut.mutateAsync({
        dpuId: factoryResetDpu.id,
        opts: factoryOpts,
      });
      const failed = res.steps.filter((s) => s.ran && !s.ok);
      if (failed.length === 0) {
        notify.success(
          `Reset to defaults issued (${res.steps.filter((s) => s.ran).length} step(s))`,
          undefined,
          { category: 'deployment' },
        );
      } else {
        // Surface each failing step's actual error message so the operator
        // can see what the BMC / DPU OS actually rejected.
        notify.error(
          failed.map((s) => `${s.name}: ${s.message}`).join(' | '),
        );
      }
    } catch (err) {
      const axiosErr = err as { response?: { data?: { detail?: string; message?: string } } };
      const msg = axiosErr.response?.data?.detail ?? axiosErr.response?.data?.message;
      notify.error(msg ?? (err instanceof Error ? err.message : 'Reset to defaults failed'));
    } finally {
      setFactoryResetDpu(null);
    }
  };

  const doReset = async () => {
    if (!confirmReset) return;
    const { dpu, resetType } = confirmReset;
    try {
      await resetMut.mutateAsync({ dpuId: dpu.id, resetType });
      notify.success(`${resetType} requested on ${dpuRowLabel(dpu)}`, undefined, { category: 'deployment' });
    } catch (err) {
      // Backend wraps errors as {error: {message: "..."}} — extractRenderError
      // unpacks that shape correctly; the old code read data.detail /
      // data.message which don't exist, so the toast only ever showed
      // the generic "Request failed with status code 400".
      notify.error(extractRenderError(err));
    } finally {
      setConfirmReset(null);
    }
  };

  const doResetNic = async () => {
    if (!confirmResetNic) return;
    const dpu = confirmResetNic;
    try {
      await resetNicMut.mutateAsync({ dpuId: dpu.id });
      notify.success(`NIC reset issued on ${dpuRowLabel(dpu)}`, undefined, { category: 'deployment' });
    } catch (err) {
      notify.error(extractRenderError(err));
    } finally {
      setConfirmResetNic(null);
    }
  };

  // Reset NIC silicon needs an SSH path that bypasses the wedged NIC.
  // In-band: host SSH (host_node_ip set + jumphost reachable upstream).
  // BMC mode: DPU OS SSH (dpu_os_status='reachable').
  const canResetNic = (dpu: Dpu): boolean =>
    (dpu.access_mode === 'in-band' && !!dpu.host_node_ip)
    || dpu.dpu_os_status === 'reachable';

  const doDelete = async () => {
    if (!confirmDelete) return;
    try {
      await deleteMut.mutateAsync(confirmDelete.id);
      notify.success('DPU removed', undefined, { category: 'fleet' });
    } catch (err) {
      notify.error(err instanceof Error ? err.message : 'Delete failed');
    } finally {
      setConfirmDelete(null);
    }
  };

  const doDeleteAll = async () => {
    if (!list || list.dpus.length === 0) return;
    setDeletingAll(true);
    let ok = 0;
    let failed = 0;
    for (const dpu of list.dpus) {
      try {
        await deleteMut.mutateAsync(dpu.id);
        ok += 1;
      } catch {
        failed += 1;
      }
    }
    setDeletingAll(false);
    setConfirmDeleteAll(false);
    if (failed > 0) {
      notify.error(`${ok} removed, ${failed} failed`);
    } else {
      notify.success(`${ok} DPU${ok === 1 ? '' : 's'} removed`, undefined, { category: 'fleet' });
    }
  };

  return (
    <div className="space-y-6">
      <DpuProjectSettingsCard projectId={projectId} disabled={!isOwner} />

      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0">
          <div>
            <CardTitle className="text-lg">DPUs</CardTitle>
            <CardDescription>
              BlueField DPUs registered for this project. Click a row to view the full
              Redfish inventory; use Discover to probe it. The DPU OS column shows
              <span className="font-mono"> oob_net0</span>'s DHCP-assigned IPv4 when
              available; if oob_net0 has no IP yet, the tmfifo loopback
              (<span className="font-mono">192.168.100.2</span>) is shown instead.
            </CardDescription>
          </div>
          <div className="flex items-center gap-2">
            <Button
              onClick={discoverAll}
              size="sm"
              variant="outline"
              disabled={!isOwner || !list || list.dpus.length === 0 || discoverAllMut.isPending}
              className="gap-2"
            >
              {discoverAllMut.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Search className="h-4 w-4" />
              )}
              Discover All
            </Button>
            <Button
              onClick={probeOsAll}
              size="sm"
              variant="outline"
              disabled={!isOwner || !list || list.dpus.length === 0 || probeOsAllMut.isPending}
              className="gap-2"
            >
              {probeOsAllMut.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Terminal className="h-4 w-4" />
              )}
              Probe DPU OS All
            </Button>
            <Button
              onClick={() => setConfirmFlashAll(true)}
              size="sm"
              variant="outline"
              disabled={!isOwner || !list || list.dpus.length === 0 || flashAllMut.isPending}
              className="gap-2"
            >
              {flashAllMut.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Zap className="h-4 w-4" />
              )}
              Flash All
            </Button>
            <Button
              onClick={() => setConfirmDeleteAll(true)}
              size="sm"
              variant="outline"
              disabled={!isOwner || !list || list.dpus.length === 0 || deletingAll}
              className="gap-2 text-destructive hover:text-destructive border-destructive/40 hover:border-destructive/60"
            >
              {deletingAll ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Trash2 className="h-4 w-4" />
              )}
              Delete All
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="flex justify-center py-8">
              <Loader2 className="h-6 w-6 animate-spin text-primary" />
            </div>
          ) : error ? (
            <div className="text-destructive py-4">Failed to load DPUs.</div>
          ) : !list || list.dpus.length === 0 ? (
            <div className="py-6 text-center text-muted-foreground">
              No DPUs yet. Use the Discovery tab to find DPUs and register them.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-muted-foreground">
                    <th className="w-6 pb-2" />
                    <th className="text-left font-medium pb-2">BMC IP / Host</th>
                    <th className="text-left font-medium pb-2">Serial # / PCI #</th>
                    <th className="text-left font-medium pb-2">NicMode</th>
                    <th className="text-left font-medium pb-2">HostPrivilegeLevel</th>
                    <th className="text-left font-medium pb-2">oob0 MAC</th>
                    <th className="text-left font-medium pb-2">oob_net0 IP</th>
                    <th className="text-left font-medium pb-2">Discovery</th>
                    <th className="text-left font-medium pb-2">Flash</th>
                    <th className="pb-2 w-28" />
                  </tr>
                </thead>
                <tbody>
                  {list.dpus.map((dpu) => {
                    const isExpanded = expanded.has(dpu.id);
                    // IB-mode DPUs render the body cells muted so the
                    // operator sees at a glance that flash/SSH-to-DPU
                    // actions are gated. The Actions cell (last <td>)
                    // and the InfiniBand chip stay at full opacity —
                    // they remain useful (Convert to Ethernet lives
                    // in the menu) and the chip needs to be readable.
                    const isIb = dpu.link_mode === 'ib' || dpu.link_mode === 'mixed';
                    return (
                      <Fragment key={dpu.id}>
                        <tr
                          className={cn(
                            'border-t border-border cursor-pointer hover:bg-muted/50',
                            // Mute every td when this DPU is in IB / mixed
                            // mode, EXCEPT the Discovery column (8th cell —
                            // hosts the InfiniBand chip, must stay readable)
                            // and the Actions column (last — has Convert to
                            // Ethernet so it stays interactive).
                            isIb
                              && '[&>td:not(:last-child):not(:nth-child(8))]:opacity-50'
                              + ' hover:[&>td:not(:last-child):not(:nth-child(8))]:opacity-80',
                          )}
                          onClick={() => toggleExpand(dpu.id)}
                        >
                          <td className="py-2">
                            {isExpanded ? (
                              <ChevronDown className="h-4 w-4" />
                            ) : (
                              <ChevronRight className="h-4 w-4" />
                            )}
                          </td>
                          <td className="py-2 font-mono">
                            {dpu.access_mode === 'in-band' ? (
                              <span className="flex items-center gap-1.5 whitespace-nowrap">
                                <Badge
                                  variant="warning"
                                  className="text-[10px] px-1 py-0 whitespace-nowrap"
                                  title={`Managed in-band via rshim on ${dpu.host_hostname ?? dpu.host_node_ip ?? '?'}${dpu.host_hostname && dpu.host_node_ip ? ` (${dpu.host_node_ip})` : ''} — PCI ${dpu.pci_address ?? '?'}`}
                                >
                                  in-band
                                </Badge>
                                <span className="text-muted-foreground">
                                  {dpu.bfb_hostname ?? dpu.host_hostname ?? dpu.host_node_ip ?? '—'}
                                </span>
                              </span>
                            ) : (
                              dpu.bmc_ip ?? '—'
                            )}
                          </td>
                          <td className="py-2 font-mono">{dpuShortLabel(dpu)}</td>
                          <td className="py-2">
                            <BiosFieldBadge dpu={dpu} field="nic_mode" />
                          </td>
                          <td className="py-2">
                            <BiosFieldBadge dpu={dpu} field="host_privilege_level" />
                          </td>
                          <td className="py-2 font-mono">{dpu.oob0_mac ?? '—'}</td>
                          <td className="py-2">
                            <DpuOsCell dpu={dpu} />
                          </td>
                          <td className="py-2">
                            <DiscoveryOrLinkModeCell
                              dpu={dpu}
                              onActivateRshim={() =>
                                dpu.access_mode === 'in-band'
                                  ? setRshimDpu(dpu)
                                  : enableRshimOnBmc(dpu)
                              }
                            />
                          </td>
                          <td className="py-2">
                            <FlashCell dpu={dpu} />
                          </td>
                          <td
                            className="py-2 pr-2 text-right whitespace-nowrap"
                            onClick={(e) => e.stopPropagation()}
                          >
                            <DropdownMenu>
                              <DropdownMenuTrigger asChild>
                                <Button
                                  variant="outline"
                                  size="sm"
                                  disabled={!isOwner}
                                  className="gap-1"
                                  aria-label={`Actions for ${dpu.bmc_ip}`}
                                >
                                  {dpu.last_discovery_status === 'running' ? (
                                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                  ) : null}
                                  Actions
                                  <ChevronDown className="h-3 w-3 opacity-60" />
                                </Button>
                              </DropdownMenuTrigger>
                              <DropdownMenuContent align="end" className="w-56">
                                <DropdownMenuItem
                                  disabled={dpu.last_discovery_status === 'running'}
                                  onClick={() => discover(dpu)}
                                >
                                  <Search className="h-3.5 w-3.5 mr-2" />
                                  Discover
                                </DropdownMenuItem>
                                {dpu.access_mode === 'in-band' && (
                                  <DropdownMenuItem onClick={() => setRshimDpu(dpu)}>
                                    <HardDriveDownload className="h-3.5 w-3.5 mr-2" />
                                    Install rshim + MFT…
                                  </DropdownMenuItem>
                                )}
                                {dpu.access_mode === 'bmc' && (
                                  <DropdownMenuItem
                                    disabled={dpu.last_discovery_status === 'running'}
                                    onClick={() => enableRshimOnBmc(dpu)}
                                  >
                                    <HardDriveDownload className="h-3.5 w-3.5 mr-2" />
                                    Enable rshim on BMC
                                  </DropdownMenuItem>
                                )}
                                <DropdownMenuItem
                                  disabled={dpu.last_discovery_status === 'running'}
                                  onClick={() => setConfirmDisableRshim(dpu)}
                                >
                                  <X className="h-3.5 w-3.5 mr-2" />
                                  Disable rshim on {dpu.access_mode === 'in-band' ? 'host' : 'BMC'}
                                </DropdownMenuItem>
                                {!isIb && (
                                  <DropdownMenuItem onClick={() => probeOs(dpu)}>
                                    <Terminal className="h-3.5 w-3.5 mr-2" />
                                    Probe DPU OS
                                  </DropdownMenuItem>
                                )}
                                <DropdownMenuItem
                                  onClick={() => setTerminalTarget({ dpu, mode: 'rshim-console' })}
                                >
                                  <MonitorPlay className="h-3.5 w-3.5 mr-2" />
                                  Serial Console
                                </DropdownMenuItem>
                                <DropdownMenuItem
                                  disabled={dpu.access_mode === 'in-band' || !dpu.bmc_ip}
                                  onClick={() => setTerminalTarget({ dpu, mode: 'ssh-bmc' })}
                                >
                                  <TerminalSquare className="h-3.5 w-3.5 mr-2" />
                                  SSH to BMC
                                </DropdownMenuItem>
                                {!isIb && (
                                  <DropdownMenuItem
                                    disabled={dpu.dpu_os_status !== 'reachable' || !dpu.dpu_os_ip}
                                    onClick={() => setTerminalTarget({ dpu, mode: 'ssh-os' })}
                                  >
                                    <TerminalSquare className="h-3.5 w-3.5 mr-2" />
                                    SSH to DPU
                                  </DropdownMenuItem>
                                )}
                                <DropdownMenuSeparator />
                                {!isIb && (
                                  <DropdownMenuItem onClick={() => openPreviewBfConf(dpu)}>
                                    <HardDriveDownload className="h-3.5 w-3.5 mr-2" />
                                    Preview bf.conf…
                                  </DropdownMenuItem>
                                )}
                                {!isIb && (
                                  <DropdownMenuItem
                                    disabled={dpu.flash_status === 'uploading'}
                                    onClick={() => setConfirmFlash(dpu)}
                                  >
                                    <HardDriveDownload className="h-3.5 w-3.5 mr-2" />
                                    Flash BFB+FW+CFG
                                  </DropdownMenuItem>
                                )}
                                {isIb && (
                                  <DropdownMenuItem
                                    disabled={
                                      dpu.access_mode === 'bmc'
                                      && dpu.dpu_os_status !== 'reachable'
                                    }
                                    onClick={() => setConfirmConvertEth(dpu)}
                                    title={
                                      dpu.access_mode === 'bmc'
                                      && dpu.dpu_os_status !== 'reachable'
                                        ? 'Probe DPU OS first — Convert needs an OS-side SSH session to call mlxconfig.'
                                        : undefined
                                    }
                                  >
                                    <ArrowLeftRight className="h-3.5 w-3.5 mr-2" />
                                    Convert to Ethernet…
                                  </DropdownMenuItem>
                                )}
                                {dpu.flash_status === 'uploading' && (
                                  <DropdownMenuItem
                                    onClick={() => cancelFlash(dpu)}
                                    className="text-destructive focus:text-destructive"
                                  >
                                    <X className="h-3.5 w-3.5 mr-2" />
                                    Cancel flash
                                  </DropdownMenuItem>
                                )}
                                <DropdownMenuSeparator />
                                <DropdownMenuSub>
                                  <DropdownMenuSubTrigger>
                                    <Power className="h-3.5 w-3.5 mr-2" />
                                    Reset
                                  </DropdownMenuSubTrigger>
                                  <DropdownMenuPortal>
                                    <DropdownMenuSubContent className="w-64">
                                      <DropdownMenuItem
                                        onClick={() =>
                                          setConfirmReset({ dpu, resetType: 'GracefulRestart' })
                                        }
                                      >
                                        <div className="flex flex-col">
                                          <span className="font-medium">Graceful Restart</span>
                                          <span className="text-xs opacity-60">
                                            Clean reboot — requires OS to respond
                                          </span>
                                        </div>
                                      </DropdownMenuItem>
                                      <DropdownMenuItem
                                        onClick={() =>
                                          setConfirmReset({ dpu, resetType: 'ForceRestart' })
                                        }
                                      >
                                        <div className="flex flex-col">
                                          <span className="font-medium">Force Restart</span>
                                          <span className="text-xs opacity-60">
                                            Hard reboot — use when OS is unresponsive
                                          </span>
                                        </div>
                                      </DropdownMenuItem>
                                      <DropdownMenuItem
                                        onClick={() =>
                                          setConfirmReset({ dpu, resetType: 'PowerCycle' })
                                        }
                                      >
                                        <div className="flex flex-col">
                                          <span className="font-medium">Power Cycle</span>
                                          <span className="text-xs opacity-60">
                                            Off → On — also flaps PCIe to host
                                          </span>
                                        </div>
                                      </DropdownMenuItem>
                                      <DropdownMenuSeparator />
                                      <DropdownMenuItem
                                        onClick={() => setConfirmResetNic(dpu)}
                                        disabled={!canResetNic(dpu)}
                                      >
                                        <div className="flex flex-col">
                                          <span className="font-medium">Reset NIC silicon</span>
                                          <span className="text-xs opacity-60">
                                            {canResetNic(dpu)
                                              ? 'mlxfwreset -l 4 — recover wedged NIC firmware'
                                              : 'Needs host SSH (in-band) or DPU OS reachable'}
                                          </span>
                                        </div>
                                      </DropdownMenuItem>
                                      <DropdownMenuSeparator />
                                      <DropdownMenuItem onClick={() => openFactoryReset(dpu)}>
                                        <div className="flex flex-col">
                                          <span className="font-medium">
                                            Reset to Defaults…
                                          </span>
                                          <span className="text-xs opacity-60">
                                            Factory-reset BIOS / BMC / NIC firmware
                                          </span>
                                        </div>
                                      </DropdownMenuItem>
                                    </DropdownMenuSubContent>
                                  </DropdownMenuPortal>
                                </DropdownMenuSub>
                                <DropdownMenuSeparator />
                                {/* Add the host carrying this in-band DPU
                                    to the Bare Metal tab. Idempotent on
                                    (project, host_ip) — re-clicking on
                                    multiple DPUs sharing the same host
                                    all point at the same row. */}
                                {dpu.access_mode === 'in-band'
                                  && dpu.host_node_ip && (
                                  <DropdownMenuItem
                                    onClick={() => registerHost(dpu)}
                                  >
                                    <Server className="h-3.5 w-3.5 mr-2" />
                                    Register Host
                                  </DropdownMenuItem>
                                )}
                                <DropdownMenuItem onClick={() => openEdit(dpu)}>
                                  <Pencil className="h-3.5 w-3.5 mr-2" />
                                  Edit
                                </DropdownMenuItem>
                                <DropdownMenuItem
                                  onClick={() => setConfirmDelete(dpu)}
                                  className="text-destructive focus:text-destructive"
                                >
                                  <Trash2 className="h-3.5 w-3.5 mr-2" />
                                  Delete
                                </DropdownMenuItem>
                              </DropdownMenuContent>
                            </DropdownMenu>
                          </td>
                        </tr>
                        {isExpanded && (
                          <tr className="bg-muted/30">
                            <td colSpan={10} className="py-3">
                              <DpuInventoryDetail projectId={projectId} dpu={dpu} />
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <DpuInterfaceTable
        projectId={projectId}
        dpus={list?.dpus ?? []}
        disabled={!isOwner}
      />

      <DpuConnectivityTables dpus={list?.dpus ?? []} />

      <DpuConnectivityMatrixCard projectId={projectId} disabled={!isOwner} />

      <DpuFormDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        mode={dialogMode}
        editing={editing}
        projectId={projectId}
        onSubmit={submit}
      />

      <DpuTerminalDialog
        open={terminalTarget !== null}
        onOpenChange={(open) => { if (!open) setTerminalTarget(null); }}
        projectId={projectId}
        dpu={terminalTarget?.dpu ?? null}
        mode={terminalTarget?.mode ?? 'rshim-console'}
      />

      <RshimInstallDialog
        open={rshimDpu !== null}
        onOpenChange={(open) => { if (!open) setRshimDpu(null); }}
        projectId={projectId}
        dpu={rshimDpu}
      />

      <AlertDialog
        open={confirmDisableRshim !== null}
        onOpenChange={(open) => {
          if (!open) setConfirmDisableRshim(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Disable rshim on {confirmDisableRshim?.access_mode === 'in-band' ? 'host' : 'BMC'}?
            </AlertDialogTitle>
            <AlertDialogDescription>
              {confirmDisableRshim && (
                <>
                  This will stop and disable the <span className="font-mono">rshim</span> service on{' '}
                  <span className="font-mono">
                    {confirmDisableRshim.access_mode === 'in-band'
                      ? (confirmDisableRshim.host_hostname ?? confirmDisableRshim.host_node_ip ?? '?')
                      : (confirmDisableRshim.bmc_ip ?? '?')}
                  </span>
                  . Any active Serial Console or Flash session on this DPU will be interrupted.
                  Releases rshim so the{' '}
                  {confirmDisableRshim.access_mode === 'in-band' ? 'BMC' : 'host'} can claim it.
                </>
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (confirmDisableRshim) {
                  const target = confirmDisableRshim;
                  setConfirmDisableRshim(null);
                  disableRshim(target);
                }
              }}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Disable rshim
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog
        open={confirmConvertEth !== null}
        onOpenChange={(open) => {
          if (!open) setConfirmConvertEth(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Convert to Ethernet?</AlertDialogTitle>
            <AlertDialogDescription>
              {confirmConvertEth && (
                <>
                  Set <span className="font-mono">LINK_TYPE_P1</span>{' '}
                  and <span className="font-mono">LINK_TYPE_P2</span> to{' '}
                  <span className="font-mono">ETH(2)</span> on{' '}
                  <span className="font-mono">{dpuRowLabel(confirmConvertEth)}</span>{' '}
                  and reboot the DPU to apply. The reset takes about 1-2 minutes
                  during which the DPU is offline. After it comes back up, click{' '}
                  <span className="font-mono">Discover</span> to refresh the link
                  mode.
                  {confirmConvertEth.access_mode === 'bmc' && (
                    <>
                      {' '}
                      <span className="font-medium">BMC mode:</span> mlxconfig
                      runs on the DPU OS, then{' '}
                      <span className="font-mono">mlxfwreset -l 3</span> applies
                      it (also flaps the PCIe link to the host).
                    </>
                  )}
                </>
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (confirmConvertEth) {
                  const target = confirmConvertEth;
                  setConfirmConvertEth(null);
                  convertToEthernet(target);
                }
              }}
              disabled={convertToEthernetMut.isPending}
            >
              {convertToEthernetMut.isPending && (
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              )}
              Convert
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog
        open={confirmReset !== null}
        onOpenChange={(open) => {
          if (!open) setConfirmReset(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {confirmReset ? resetDialogTitle(confirmReset.resetType) : ''}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {confirmReset && (
                <>
                  Trigger <span className="font-mono">{confirmReset.resetType}</span> on DPU{' '}
                  <span className="font-mono">{confirmReset.dpu.bmc_ip}</span>
                  {confirmReset.dpu.serial_number && (
                    <> ({confirmReset.dpu.serial_number})</>
                  )}
                  ? {resetDialogDetail(confirmReset.resetType)}
                </>
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={doReset} disabled={resetMut.isPending}>
              {resetMut.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              Confirm
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog
        open={confirmResetNic !== null}
        onOpenChange={(open) => {
          if (!open) setConfirmResetNic(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Reset NIC silicon?</AlertDialogTitle>
            <AlertDialogDescription>
              {confirmResetNic && (
                <>
                  Run <span className="font-mono">mlxfwreset -l 4 -t 0</span> on{' '}
                  <span className="font-mono">{dpuRowLabel(confirmResetNic)}</span>?
                  Use this to recover a NIC wedged in firmware pre-init —
                  PCIe link to the host will flap during the reset.
                </>
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={doResetNic} disabled={resetNicMut.isPending}>
              {resetNicMut.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              Confirm
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog
        open={confirmDelete !== null}
        onOpenChange={(open) => {
          if (!open) setConfirmDelete(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove DPU?</AlertDialogTitle>
            <AlertDialogDescription>
              DPU {confirmDelete ? `(BMC ${confirmDelete.bmc_ip})` : ''} will be removed from this
              project. Discovery snapshots and VLAN plan are lost.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={doDelete}>Remove</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog
        open={confirmDeleteAll}
        onOpenChange={(open) => {
          if (!open) setConfirmDeleteAll(false);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove all DPUs?</AlertDialogTitle>
            <AlertDialogDescription>
              All {list?.dpus.length ?? 0} DPU{list?.dpus.length === 1 ? '' : 's'} will be removed
              from this project. Discovery snapshots, flash history, and per-DPU VLAN plans are
              lost. This can't be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deletingAll}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={doDeleteAll}
              disabled={deletingAll}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {deletingAll && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              Remove all
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog
        open={confirmFlash !== null}
        onOpenChange={(open) => {
          if (!open) setConfirmFlash(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Flash BFB+FW+CFG?</AlertDialogTitle>
            <AlertDialogDescription>
              {confirmFlash && (
                <>
                  The BFB bundle will be pushed to DPU{' '}
                  <span className="font-mono">{confirmFlash.bmc_ip}</span>
                  {confirmFlash.serial_number && (
                    <> ({confirmFlash.serial_number})</>
                  )}
                  {' '}via rshim, which triggers a full reflash of the DPU OS
                  and firmware. This is destructive and takes 5-10 minutes.
                  <br />
                  <br />
                  Open the Serial Console to watch the installer output.
                </>
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={doFlash} disabled={flashMut.isPending}>
              {flashMut.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              Flash
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog
        open={confirmFlashAll}
        onOpenChange={(open) => {
          if (!open) setConfirmFlashAll(false);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Flash all DPUs?</AlertDialogTitle>
            <AlertDialogDescription>
              All {list?.dpus.length ?? 0} DPU(s) in this project will be
              reflashed in parallel (up to 10 at a time). Existing DPU OS
              state and firmware settings will be overwritten. You can cancel
              a single in-flight flash via the Actions menu.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={doFlashAll} disabled={flashAllMut.isPending}>
              {flashAllMut.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              Flash all
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <Dialog
        open={previewDpu !== null}
        onOpenChange={(open) => {
          if (!open) {
            setPreviewDpu(null);
            setPreviewContent('');
            setPreviewAt(null);
            setPreviewError('');
          }
        }}
      >
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>
              Rendered bf.conf — {previewDpu?.bmc_ip}
              {previewDpu?.serial_number && <> ({previewDpu.serial_number})</>}
            </DialogTitle>
            <DialogDescription>
              {previewAt
                ? `Rendered at ${new Date(previewAt).toLocaleString()}.`
                : 'Rendering…'}
            </DialogDescription>
          </DialogHeader>
          {previewError ? (
            <pre className="text-xs text-destructive whitespace-pre-wrap font-mono p-3 border rounded">
              {previewError}
            </pre>
          ) : previewContent ? (
            <pre className="font-mono text-xs whitespace-pre-wrap max-h-[60vh] overflow-auto border rounded p-3 bg-muted/30">
              {previewContent}
            </pre>
          ) : (
            <div className="flex justify-center p-8">
              <Loader2 className="h-6 w-6 animate-spin text-primary" />
            </div>
          )}
          <DialogFooter>
            <Button
              variant="outline"
              onClick={downloadBfConf}
              disabled={!previewContent}
              className="gap-2"
            >
              Download
            </Button>
            <Button
              onClick={() => {
                setPreviewDpu(null);
                setPreviewContent('');
                setPreviewAt(null);
                setPreviewError('');
              }}
            >
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={factoryResetDpu !== null}
        onOpenChange={(open) => {
          if (!open) setFactoryResetDpu(null);
        }}
      >
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>Reset to Defaults</DialogTitle>
            <DialogDescription>
              Layered factory reset for{' '}
              <span className="font-mono">
                {factoryResetDpu ? dpuRowLabel(factoryResetDpu) : ''}
              </span>
              . Select which layers to wipe:
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-2">
            {factoryResetDpu?.access_mode !== 'in-band' && (
              <label className="flex items-start gap-3 cursor-pointer">
                <Checkbox
                  checked={factoryOpts.bios}
                  onCheckedChange={(v) =>
                    setFactoryOpts((o) => ({ ...o, bios: v === true }))
                  }
                />
                <div className="text-sm">
                  <div className="font-medium">Reset BIOS attributes</div>
                  <div className="text-xs opacity-60">
                    Redfish <span className="font-mono">Bios.ResetBios</span>.
                    Restores NicMode, HostPrivilegeLevel, InternalCPUModel,
                    EnableSMMU, FieldMode, SPCR_UART, etc. to factory. Applied on
                    power cycle.
                  </div>
                </div>
              </label>
            )}

            {factoryResetDpu?.access_mode !== 'in-band' && (
              <label className="flex items-start gap-3 cursor-pointer">
                <Checkbox
                  checked={factoryOpts.bmc}
                  onCheckedChange={(v) =>
                    setFactoryOpts((o) => ({
                      ...o,
                      bmc: v === true,
                      // Deep only makes sense when BMC is on.
                      bmc_deep: v === true ? o.bmc_deep : false,
                    }))
                  }
                />
                <div className="text-sm flex-1">
                  <div className="font-medium">Reset BMC to defaults</div>
                  <div className="text-xs opacity-60">
                    Redfish{' '}
                    <span className="font-mono">Manager.ResetToDefaults</span>.
                    Wipes BMC users, network config, SSH host keys, certs;
                    reverts <span className="font-mono">root</span> to
                    <span className="font-mono"> 0penBmc</span>. Forge's
                    credential helper will re-PATCH your configured password on
                    next access.
                  </div>
                  <label
                    className={cn(
                      'mt-2 flex items-start gap-2 text-xs',
                      factoryOpts.bmc ? 'cursor-pointer' : 'cursor-not-allowed opacity-50',
                    )}
                  >
                    <Checkbox
                      checked={factoryOpts.bmc_deep}
                      disabled={!factoryOpts.bmc}
                      onCheckedChange={(v) =>
                        setFactoryOpts((o) => ({ ...o, bmc_deep: v === true }))
                      }
                    />
                    <div>
                      <div className="font-medium">
                        Use Nvidia OEM deep variant
                      </div>
                      <div className="opacity-60">
                        Calls{' '}
                        <span className="font-mono">
                          Oem/NvidiaManager.ResetToDefaults
                        </span>{' '}
                        instead. Clears more Nvidia-specific state than the
                        Redfish-standard action.
                      </div>
                    </div>
                  </label>
                </div>
              </label>
            )}

            {(() => {
              const nicEnabled =
                factoryResetDpu?.access_mode === 'in-band'
                || factoryResetDpu?.dpu_os_status === 'reachable';
              const isInband = factoryResetDpu?.access_mode === 'in-band';
              return (
                <label
                  className={cn(
                    'flex items-start gap-3',
                    nicEnabled ? 'cursor-pointer' : 'cursor-not-allowed opacity-60',
                  )}
                >
                  <Checkbox
                    checked={factoryOpts.nic}
                    disabled={!nicEnabled}
                    onCheckedChange={(v) =>
                      setFactoryOpts((o) => ({ ...o, nic: v === true }))
                    }
                  />
                  <div className="text-sm">
                    <div className="font-medium">Reset NIC firmware settings</div>
                    <div className="text-xs opacity-60">
                      {isInband ? (
                        <>
                          <span className="font-mono">sudo mlxconfig -d … -y reset</span> on
                          the host, then an rshim SW_RESET to reboot the DPU with factory
                          mlxconfig. Clears SRIOV_EN, NUM_OF_VFS, INTERNAL_CPU_MODEL,
                          LAG_RESOURCE_ALLOCATION, etc. Disruptive — flaps PCIe + reboots.
                        </>
                      ) : (
                        <>
                          <span className="font-mono">mlxconfig -d … reset</span> +
                          <span className="font-mono"> mlxprivhost -d … p</span> via SSH
                          to the DPU OS. Clears SRIOV_EN, NUM_OF_VFS, INTERNAL_CPU_MODEL,
                          LAG_RESOURCE_ALLOCATION, etc.
                          {!nicEnabled && (
                            <>
                              {' '}
                              <span className="text-warning">
                                Requires DPU OS reachable — run Flash BFB+FW+CFG to reset
                                NIC firmware instead.
                              </span>
                            </>
                          )}
                        </>
                      )}
                    </div>
                  </div>
                </label>
              );
            })()}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setFactoryResetDpu(null)}>
              Cancel
            </Button>
            <Button
              onClick={doFactoryReset}
              disabled={
                resetDefaultsMut.isPending
                || !(factoryOpts.bios || factoryOpts.bmc || factoryOpts.nic)
              }
              className="gap-2"
            >
              {resetDefaultsMut.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
              Reset to defaults
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function resetDialogTitle(t: DpuResetType): string {
  switch (t) {
    case 'GracefulRestart': return 'Graceful Restart?';
    case 'ForceRestart':    return 'Force Restart?';
    case 'PowerCycle':      return 'Power Cycle?';
  }
}

function resetDialogDetail(t: DpuResetType): string {
  switch (t) {
    case 'GracefulRestart':
      return 'Signals the DPU OS to shut down cleanly and reboot. Needs a responsive OS.';
    case 'ForceRestart':
      return 'Immediately reboots the ARM CPU without waiting for the OS. Use this when the OS is wedged.';
    case 'PowerCycle':
      return 'Powers the DPU OFF then back ON. Also flaps the PCIe link to the host — the host may log a NIC link event.';
  }
}

function DiscoveryBadge({
  dpu,
  onActivateRshim,
}: {
  dpu: Dpu;
  onActivateRshim: () => void;
}) {
  const status = dpu.last_discovery_status;
  const error = dpu.last_discovery_error;
  const rshim = dpu.rshim_state;

  if (!status) return <span className="text-muted-foreground">—</span>;

  // Running — keep the live progress breadcrumb (install-rshim / discover
  // write step-by-step updates into last_discovery_error so the operator
  // sees "Installing MFT + kernel-mft-dkms…" etc in real time).
  if (status === 'running') {
    if (error) {
      return (
        <div className="flex flex-col gap-0.5 max-w-[22rem]" title={error}>
          <Badge variant="info" className="w-fit">running…</Badge>
          <span className="text-[11px] text-muted-foreground truncate">{error}</span>
        </div>
      );
    }
    return <Badge variant="info">running…</Badge>;
  }

  // Failed — red badge + Details popover for the full remediation.
  // The badge label calls out rshim-specific failures so operators can
  // tell a conflict/install issue from a generic Redfish/creds failure.
  if (status === 'failed') {
    const errText = (error ?? '').trim();
    const isRshimFailure = errText.toLowerCase().includes('rshim');
    const badgeLabel = isRshimFailure ? 'rshim failed' : 'failed';
    return (
      <div className="flex flex-col gap-0.5 max-w-[22rem]">
        <Badge variant="destructive" className="w-fit">{badgeLabel}</Badge>
        {errText && (
          <Popover>
            <PopoverTrigger asChild>
              <button
                type="button"
                className="self-start text-[11px] text-primary hover:underline"
              >
                Details
              </button>
            </PopoverTrigger>
            <PopoverContent
              align="start"
              side="bottom"
              className="w-[520px] max-h-[420px] overflow-auto p-3 text-xs"
            >
              <pre className="whitespace-pre-wrap break-words font-mono leading-snug">
                {errText}
              </pre>
            </PopoverContent>
          </Popover>
        )}
      </div>
    );
  }

  // status === 'ok' — let rshim readiness downgrade the badge when it's
  // not actually flash/console-ready, so the operator sees the real
  // usable state instead of a misleading plain "OK".
  const rshimTooltip = [
    dpu.rshim_state_detail,
    dpu.rshim_checked_at
      ? `rshim checked: ${new Date(dpu.rshim_checked_at).toLocaleString()}`
      : null,
  ]
    .filter(Boolean)
    .join('\n');

  if (rshim === 'inactive' || rshim === 'unreachable') {
    const label = rshim === 'unreachable' ? 'rshim: unreachable' : 'rshim: inactive';
    const hint =
      dpu.access_mode === 'in-band'
        ? 'Click to install / activate rshim on the host'
        : 'Click to enable rshim on the BMC';
    const tooltip = rshimTooltip ? `${rshimTooltip}\n\n${hint}` : hint;

    return (
      <button
        type="button"
        onClick={onActivateRshim}
        title={tooltip}
        className="inline-flex w-fit"
      >
        <Badge variant="warning">
          {label}
        </Badge>
      </button>
    );
  }

  // Discovery OK + rshim ready (or not-yet-checked). Show OK; when we
  // have a rshim snapshot, surface it through the tooltip.
  const okTooltip =
    rshim === 'ready'
      ? rshimTooltip || 'Discovery OK — rshim ready'
      : 'Discovery OK';
  return (
    <Badge variant="success" title={okTooltip}>
      OK
    </Badge>
  );
}

function FlashCell({ dpu }: { dpu: Dpu }) {
  const status = dpu.flash_status;
  if (!status) return <span className="text-muted-foreground">—</span>;
  if (status === 'uploading') {
    return (
      <div className="flex items-center gap-1 text-xs">
        <Loader2 className="h-3.5 w-3.5 animate-spin text-info" />
        <span className="text-info" title={dpu.flash_stage_detail ?? ''}>
          {dpu.flash_stage_detail ?? 'uploading…'}
        </span>
      </div>
    );
  }
  if (status === 'waiting_reboot') {
    return (
      <div className="flex items-center gap-1 text-xs">
        <Loader2 className="h-3.5 w-3.5 animate-spin text-warning" />
        <span className="text-warning">waiting for reboot…</span>
      </div>
    );
  }
  if (status === 'os_online') {
    return (
      <div className="flex items-center gap-1 text-xs">
        <CheckCircle2 className="h-3.5 w-3.5 text-success" />
        <span className="text-success">flashed</span>
      </div>
    );
  }
  if (status === 'cancelled') {
    return (
      <div className="flex items-center gap-1 text-xs">
        <XCircle className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="text-muted-foreground">cancelled</span>
      </div>
    );
  }
  // Flash failed — show "failed" + a short headline (everything before
  // the first ":" in the error; backend wraps each failure as
  // "<stage> failed: <detail>"), and a "Details" popover with the full
  // multi-line error (often includes remediation commands).
  const errText = (dpu.flash_error ?? '').trim();
  const headline = errText.split(/[:\n]/)[0].trim();
  return (
    <div className="flex items-start gap-1 text-xs max-w-[280px]">
      <XCircle className="h-3.5 w-3.5 text-destructive mt-0.5 flex-shrink-0" />
      <div className="flex flex-col min-w-0 gap-0.5">
        <span className="text-destructive font-medium">failed</span>
        {headline && (
          <span className="text-[11px] text-muted-foreground truncate">
            {headline}
          </span>
        )}
        {errText && (
          <Popover>
            <PopoverTrigger asChild>
              <button
                type="button"
                className="self-start text-[11px] text-primary hover:underline"
              >
                Details
              </button>
            </PopoverTrigger>
            <PopoverContent
              align="start"
              side="bottom"
              className="w-[520px] max-h-[420px] overflow-auto p-3 text-xs"
            >
              <pre className="whitespace-pre-wrap break-words font-mono leading-snug">
                {errText}
              </pre>
            </PopoverContent>
          </Popover>
        )}
      </div>
    </div>
  );
}

function BiosFieldBadge({
  dpu,
  field,
}: {
  dpu: Dpu;
  field: 'nic_mode' | 'host_privilege_level';
}) {
  const value = dpu[field];
  if (!value) return <span className="text-muted-foreground">—</span>;
  return <span className="font-mono text-xs">{value}</span>;
}

/**
 * Discovery column content. For IB / mixed-mode DPUs we replace the
 * usual discovery badge with an "InfiniBand" pill — the link mode is
 * the most important thing to see at a glance (and the regular
 * discovery state is moot when bf.cfg can't be flashed). The pill is
 * rendered at full opacity even on a muted IB row because it carries
 * the row's identity.
 *
 * Ethernet (and unknown / null link_mode) DPUs render the existing
 * DiscoveryBadge unchanged.
 */
function DiscoveryOrLinkModeCell({
  dpu,
  onActivateRshim,
}: {
  dpu: Dpu;
  onActivateRshim: () => void;
}) {
  const mode = dpu.link_mode;
  if (mode === 'ib' || mode === 'mixed') {
    const label = mode === 'ib' ? 'InfiniBand' : 'Mixed link mode';
    const tooltip =
      mode === 'ib'
        ? 'InfiniBand mode (P1=IB, P2=IB). Use the Convert to Ethernet action to switch back; standard bf.cfg templates assume Ethernet.'
        : 'Mixed link mode: P1 and P2 differ. Inspect mlxconfig in the verbose panel.';
    return (
      <Badge
        variant="info"
        className="text-[10px] px-1.5 py-0 whitespace-nowrap"
        title={tooltip}
      >
        {label}
      </Badge>
    );
  }
  return <DiscoveryBadge dpu={dpu} onActivateRshim={onActivateRshim} />;
}

// Phase 3: roll-up health from connectivity + LACP for the inline row pill.
// Returns one of "ok" / "degraded" / "down" / null. Internet ping is
// the strongest signal — if that's failing the DPU is effectively
// isolated, so render red. DNS or any bond-member down counts as
// "degraded" (amber). All green ⇒ "ok".
function dpuConnectivityHealth(dpu: Dpu): 'ok' | 'degraded' | 'down' | null {
  const conn = dpu.dpu_os_connectivity as Record<string, unknown> | null | undefined;
  const lacp = dpu.dpu_os_lacp as Record<string, unknown> | null | undefined;
  if (!conn && !lacp) return null;
  const inet = (conn?.internet ?? null) as Record<string, unknown> | null;
  const dns = ((conn?.dns ?? null) as Record<string, unknown> | null)?.f5_com as Record<string, unknown> | null;
  if (inet && inet.ping_ok === false) return 'down';
  let degraded = false;
  if (dns && dns.ok === false) degraded = true;
  if (lacp?.bond_present === true) {
    const members = (lacp.members ?? []) as Array<{ mii_status?: string }>;
    if (members.some((m) => m.mii_status !== 'up')) degraded = true;
  } else if (lacp?.bond_present === false) {
    const uplinks = (lacp.uplinks ?? []) as Array<{ link?: string }>;
    if (uplinks.length > 0 && uplinks.some((u) => u.link !== 'yes')) degraded = true;
  }
  if (inet && inet.ping_ok !== true) degraded = true;
  return degraded ? 'degraded' : 'ok';
}

function ConnectivityHealthDot({ dpu }: { dpu: Dpu }) {
  const h = dpuConnectivityHealth(dpu);
  if (h === null) return null;
  const cls =
    h === 'ok'
      ? 'bg-success'
      : h === 'degraded'
        ? 'bg-warning'
        : 'bg-destructive';
  const label =
    h === 'ok'
      ? 'Internet reachable, DNS works, all uplinks up'
      : h === 'degraded'
        ? 'Internet/DNS or one uplink degraded — see tables below'
        : 'Internet unreachable from this DPU';
  return (
    <span
      className={`inline-block w-2 h-2 rounded-full flex-shrink-0 ${cls}`}
      title={label}
    />
  );
}

function DpuOsCell({ dpu }: { dpu: Dpu }) {
  if (dpu.dpu_os_status === 'probing') {
    return (
      <div className="flex items-center gap-1 text-xs text-info">
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        probing…
      </div>
    );
  }
  if (dpu.dpu_os_status === 'reachable' && dpu.dpu_os_ip) {
    return (
      <div className="flex items-center gap-1 text-xs">
        <CheckCircle2 className="h-3.5 w-3.5 text-success" />
        <span className="font-mono">{dpu.dpu_os_ip}</span>
        <ConnectivityHealthDot dpu={dpu} />
      </div>
    );
  }
  if (dpu.dpu_os_status === 'unreachable') {
    return (
      <div className="flex items-center gap-1 text-xs">
        <XCircle className="h-3.5 w-3.5 text-destructive" />
        <span className="text-destructive">unreachable</span>
      </div>
    );
  }
  if (dpu.dpu_os_status === 'error') {
    const err = (dpu.dpu_os_error ?? '').trim();
    // Extract a short headline (first segment before ':' or newline) so the
    // badge itself hints at the failure class — auth, timeout, etc. — and
    // put the full message behind a Details popover.
    const headline = err.split(/[:\n]/).pop()?.trim() || err.split(/[:\n]/)[0]?.trim() || '';
    const badgeLabel = headline
      ? (/auth/i.test(headline) ? 'auth failed' : headline.length > 28 ? 'probe error' : headline)
      : 'probe error';
    return (
      <div className="flex flex-col gap-0.5 max-w-[22rem]">
        <div className="flex items-center gap-1 text-xs">
          <XCircle className="h-3.5 w-3.5 text-warning flex-shrink-0" />
          <span className="text-warning truncate" title={err}>{badgeLabel}</span>
        </div>
        {err && (
          <Popover>
            <PopoverTrigger asChild>
              <button
                type="button"
                className="self-start text-[11px] text-primary hover:underline"
              >
                Details
              </button>
            </PopoverTrigger>
            <PopoverContent
              align="start"
              side="bottom"
              className="w-[520px] max-h-[420px] overflow-auto p-3 text-xs"
            >
              <pre className="whitespace-pre-wrap break-words font-mono leading-snug">
                {err}
              </pre>
            </PopoverContent>
          </Popover>
        )}
      </div>
    );
  }
  // Legacy rows pre-dating dpu_os_status: fall back to dpu_os_reachable.
  if (dpu.dpu_os_reachable === true && dpu.dpu_os_ip) {
    return (
      <div className="flex items-center gap-1 text-xs">
        <CheckCircle2 className="h-3.5 w-3.5 text-success" />
        <span className="font-mono">{dpu.dpu_os_ip}</span>
        <ConnectivityHealthDot dpu={dpu} />
      </div>
    );
  }
  if (dpu.dpu_os_reachable === false) {
    return (
      <div className="flex items-center gap-1 text-xs">
        <XCircle className="h-3.5 w-3.5 text-destructive" />
        <span className="text-destructive">unreachable</span>
      </div>
    );
  }
  return (
    <div className="flex items-center gap-1 text-xs text-muted-foreground">
      <Link2 className="h-3.5 w-3.5" />
      not probed
    </div>
  );
}
