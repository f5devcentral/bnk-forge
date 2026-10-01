import { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { queryKeys } from '@/lib/queryKeys';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Checkbox } from '@/components/ui/checkbox';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { Loader2, Server, Cpu, Network, ShieldCheck } from 'lucide-react';
import { useBareMetalHosts } from '@/hooks/useBareMetal';
import { useDpus } from '@/hooks/useDpus';
import { useAssignBnkClusterMembers } from '@/hooks/useK8sClusters';
import type { Dpu } from '@/lib/api/dpus';
import type { BnkClusterConfigSummary, BareMetalHost } from '@/types';
import { useThemeClasses } from '@/context/ThemeContext';
import { cn } from '@/lib/utils';
import { notify } from '@/lib/notify';

interface BnkClusterMemberDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  clusterId: number;
  projectId: number;
  clusterName: string;
  currentConfig?: BnkClusterConfigSummary | null;
}

export function BnkClusterMemberDialog({
  open,
  onOpenChange,
  clusterId,
  projectId,
  clusterName,
  currentConfig,
}: BnkClusterMemberDialogProps) {
  const tc = useThemeClasses();
  const queryClient = useQueryClient();
  const { data: hosts, isLoading: loadingHosts } = useBareMetalHosts(projectId);
  const { data: dpuResponse, isLoading: loadingDpus } = useDpus(projectId);
  const assignMembers = useAssignBnkClusterMembers();

  const dpuList = useMemo(() => dpuResponse?.dpus ?? [], [dpuResponse]);

  const [controlPlaneHostId, setControlPlaneHostId] = useState<number | null>(null);
  const [selectedHostIds, setSelectedHostIds] = useState<number[]>([]);
  const [selectedDpuIds, setSelectedDpuIds] = useState<number[]>([]);
  const [tmfifoPoolCidr, setTmfifoPoolCidr] = useState('192.168.100.0/22');

  // A host/DPU is "foreign" when it already belongs to a DIFFERENT cluster.
  // Foreign members must never be silently re-homed: they are shown disabled,
  // excluded from the B-all default, and left out of Select-All.
  const isForeignHost = useCallback(
    (h: BareMetalHost) => h.kubernetes_cluster_id != null && h.kubernetes_cluster_id !== clusterId,
    [clusterId]
  );
  const isForeignDpu = useCallback(
    (d: Dpu) => d.kubernetes_cluster_id != null && d.kubernetes_cluster_id !== clusterId,
    [clusterId]
  );

  const selectableHosts = useMemo(() => (hosts ?? []).filter((h) => !isForeignHost(h)), [hosts, isForeignHost]);
  const selectableDpus = useMemo(() => dpuList.filter((d) => !isForeignDpu(d)), [dpuList, isForeignDpu]);

  // Map each DPU id to its owner host (the BareMetalHost whose host_ip matches dpu.host_node_ip).
  const dpuOwnerHostMap = useMemo(() => {
    const map = new Map<number, BareMetalHost | undefined>();
    for (const dpu of dpuList) {
      const ownerHost = (hosts ?? []).find((h) => h.host_ip === dpu.host_node_ip);
      map.set(dpu.id, ownerHost);
    }
    return map;
  }, [dpuList, hosts]);

  // Guard: initialize selections only once per open session so background data
  // refetches do not reset the user's in-progress selections mid-edit.
  // We defer the latch until both queries have resolved — on a cold open both
  // useBareMetalHosts and useDpus may return undefined/[] before their first
  // fetch completes, so latching immediately would leave every selection empty.
  const hasInitializedRef = useRef(false);

  useEffect(() => {
    if (!open) {
      hasInitializedRef.current = false;
      return;
    }
    if (hasInitializedRef.current) return;
    // Wait until the hosts query has resolved (undefined → array) before latching.
    // loadingDpus covers the DPU query; dpuList stays [] while loading so we
    // cannot distinguish "still loading" from "genuinely empty" without the flag.
    if (hosts === undefined || loadingDpus) return;

    hasInitializedRef.current = true;

    if (currentConfig?.control_plane_host_id) {
      setControlPlaneHostId(currentConfig.control_plane_host_id);
    } else if (selectableHosts.length > 0) {
      setControlPlaneHostId(selectableHosts[0].id);
    }

    if (currentConfig?.tmfifo_pool_cidr) {
      setTmfifoPoolCidr(currentConfig.tmfifo_pool_cidr);
    }

    // When a config already exists, seed from the cluster's REAL membership
    // (host_ids/dpu_ids). Only fall back to the B-all default on first
    // configuration (no bnk_config) — and even then exclude members owned by
    // another cluster. Re-applying B-all on every open is what silently stole
    // sibling clusters' members.
    if (currentConfig) {
      setSelectedHostIds(currentConfig.host_ids ?? []);
      setSelectedDpuIds(currentConfig.dpu_ids ?? []);
    } else {
      setSelectedHostIds(selectableHosts.map((h: BareMetalHost) => h.id));
      setSelectedDpuIds(selectableDpus.map((d: Dpu) => d.id));
    }
  }, [open, loadingDpus, currentConfig, hosts, dpuList, selectableHosts, selectableDpus]);

  const toggleHost = useCallback((hostId: number) => {
    setSelectedHostIds((prev) => {
      const isRemoving = prev.includes(hostId);
      if (isRemoving) {
        // Auto-uncheck DPUs whose owner host is the host being removed.
        const host = (hosts ?? []).find((h) => h.id === hostId);
        if (host) {
          setSelectedDpuIds((dpuPrev) =>
            dpuPrev.filter((dpuId) => {
              const dpu = dpuList.find((d) => d.id === dpuId);
              return dpu?.host_node_ip !== host.host_ip;
            })
          );
        }
        return prev.filter((id) => id !== hostId);
      }
      return [...prev, hostId];
    });
  }, [hosts, dpuList]);

  const toggleDpu = useCallback((dpuId: number) => {
    setSelectedDpuIds((prev) =>
      prev.includes(dpuId) ? prev.filter((id) => id !== dpuId) : [...prev, dpuId]
    );
  }, []);

  const handleSelectAllHosts = useCallback(() => {
    if (selectedHostIds.length === selectableHosts.length) {
      setSelectedHostIds([]);
    } else {
      setSelectedHostIds(selectableHosts.map((h: BareMetalHost) => h.id));
    }
  }, [selectableHosts, selectedHostIds]);

  const handleSelectAllDpus = useCallback(() => {
    if (selectedDpuIds.length === selectableDpus.length) {
      setSelectedDpuIds([]);
    } else {
      setSelectedDpuIds(selectableDpus.map((d: Dpu) => d.id));
    }
  }, [selectableDpus, selectedDpuIds]);

  const totalAllocations = useMemo(() => {
    return selectedDpuIds.length;
  }, [selectedDpuIds.length]);

  const handleSubmit = useCallback(async () => {
    if (!controlPlaneHostId) {
      notify.error('Control Plane host required', 'Please select a designated Control Plane host.');
      return;
    }

    // Ensure CP host is included in host_ids
    const hostIdsWithCp = Array.from(new Set([controlPlaneHostId, ...selectedHostIds]));

    // Confirm before removing hosts or DPUs that are currently in the cluster
    // (ADR-424 minor): stale cache or empty host_ids would otherwise silently
    // unassign every member with no warning.
    const currentHostIds = currentConfig?.host_ids ?? [];
    const currentDpuIds = currentConfig?.dpu_ids ?? [];
    if (currentHostIds.length > 0 || currentDpuIds.length > 0) {
      const hostsToRemove = currentHostIds.filter(id => !hostIdsWithCp.includes(id));
      const dpusToRemove = currentDpuIds.filter(id => !selectedDpuIds.includes(id));
      if (hostsToRemove.length > 0 || dpusToRemove.length > 0) {
        const hostWord = hostsToRemove.length === 1 ? 'host' : 'hosts';
        const dpuWord = dpusToRemove.length === 1 ? 'DPU' : 'DPUs';
        const parts: string[] = [];
        if (hostsToRemove.length > 0) parts.push(`${hostsToRemove.length} ${hostWord}`);
        if (dpusToRemove.length > 0) parts.push(`${dpusToRemove.length} ${dpuWord}`);
        if (!confirm(`This will remove ${parts.join(' and ')} from the cluster. Continue?`)) return;
      }
    }

    try {
      await assignMembers.mutateAsync({
        clusterId,
        data: {
          control_plane_host_id: controlPlaneHostId,
          host_ids: hostIdsWithCp,
          dpu_ids: selectedDpuIds,
          tmfifo_pool_cidr: tmfifoPoolCidr,
        },
      });
      // Refetch before closing so the dialog re-seeds from fresh membership on next open,
      // rather than from the stale placeholderData row (ADR-424 F3).
      await queryClient.refetchQueries({ queryKey: queryKeys.k8s.clusters.byProject(projectId) });
      onOpenChange(false);
    } catch {
      // Error handled by mutation
    }
  }, [controlPlaneHostId, selectedHostIds, selectedDpuIds, tmfifoPoolCidr, clusterId, assignMembers, onOpenChange, currentConfig, queryClient, projectId]);

  const isLoading = loadingHosts || loadingDpus;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className={cn('sm:max-w-xl max-h-[85vh] flex flex-col', tc.bgCard)}>
        <DialogHeader>
          <DialogTitle className={cn('text-xl font-bold flex items-center gap-2', tc.textPrimary)}>
            <Network className="h-5 w-5 text-primary" />
            Multi-Host & DPU Topology
          </DialogTitle>
          <DialogDescription className={tc.textMuted}>
            Configure cluster membership, select the Control Plane host, and set the tmfifo IPAM pool for cluster{' '}
            <strong className={tc.textPrimary}>{clusterName}</strong>.
          </DialogDescription>
        </DialogHeader>

        <div className="flex-1 overflow-y-auto space-y-6 py-2 pr-1">
          {/* IPAM Configuration */}
          <div className={cn('rounded-lg border p-4 space-y-3', tc.borderDefault)}>
            <div className="flex items-center justify-between">
              <Label htmlFor="tmfifoCidr" className="font-semibold text-sm flex items-center gap-2">
                <Network className="h-4 w-4 text-primary" />
                tmfifo IPAM Pool CIDR
              </Label>
              <Badge variant="outline" className="text-xs">
                Cluster-Scoped /30 Allocator
              </Badge>
            </div>
            <Input
              id="tmfifoCidr"
              value={tmfifoPoolCidr}
              onChange={(e) => setTmfifoPoolCidr(e.target.value)}
              placeholder="192.168.100.0/22"
              className="font-mono text-xs"
            />
            <p className={cn('text-xs', tc.textMuted)}>
              Subnet allocated deterministically in /30 blocks per member host and DPU. Currently allocating{' '}
              <strong>{totalAllocations}</strong> /30 block(s).
            </p>
          </div>

          {/* Bare-Metal Hosts & Control Plane Selection */}
          <div className={cn('rounded-lg border p-4 space-y-3', tc.borderDefault)}>
            <div className="flex items-center justify-between">
              <Label className="font-semibold text-sm flex items-center gap-2">
                <Server className="h-4 w-4 text-primary" />
                Bare-Metal Hosts & Control Plane (B-select / B-all)
              </Label>
              {selectableHosts.length > 0 && (
                <Button variant="ghost" size="sm" onClick={handleSelectAllHosts} className="text-xs h-7">
                  {selectedHostIds.length === selectableHosts.length ? 'Deselect All' : 'Select All'}
                </Button>
              )}
            </div>

            {isLoading ? (
              <div className="flex items-center justify-center py-6">
                <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
              </div>
            ) : !hosts || hosts.length === 0 ? (
              <p className={cn('text-xs py-2', tc.textMuted)}>No bare-metal hosts discovered in this project.</p>
            ) : (
              <RadioGroup
                value={controlPlaneHostId ? String(controlPlaneHostId) : ''}
                onValueChange={(val) => setControlPlaneHostId(Number(val))}
                className="space-y-2"
              >
                {hosts.map((host: BareMetalHost) => {
                  const isCp = controlPlaneHostId === host.id;
                  const foreign = isForeignHost(host);
                  const isMember = !foreign && (selectedHostIds.includes(host.id) || isCp);

                  return (
                    <div
                      key={host.id}
                      className={cn(
                        'flex items-center justify-between p-3 rounded-md border text-xs transition-colors',
                        foreign ? 'opacity-50' : isCp ? 'border-primary/60 bg-primary/5' : isMember ? 'border-border' : 'opacity-60',
                        tc.borderDefault
                      )}
                    >
                      <div className="flex items-center gap-3">
                        <Checkbox
                          checked={isMember}
                          disabled={isCp || foreign}
                          onCheckedChange={() => toggleHost(host.id)}
                          id={`host-member-${host.id}`}
                        />
                        <div className="flex flex-col">
                          <label htmlFor={`host-member-${host.id}`} className="font-medium cursor-pointer">
                            {host.hostname || host.host_ip}
                          </label>
                          <span className={cn('text-[11px]', tc.textMuted)}>
                            IP: {host.host_ip} · Status: {host.last_discovery_status || 'active'}
                          </span>
                        </div>
                      </div>

                      <div className="flex items-center gap-2">
                        {foreign && (
                          <Badge variant="outline" className="text-[10px] border-warning/40 text-warning">
                            In cluster #{host.kubernetes_cluster_id}
                          </Badge>
                        )}
                        {isCp && (
                          <Badge variant="default" className="text-[10px] bg-primary flex items-center gap-1">
                            <ShieldCheck className="h-3 w-3" /> Control Plane
                          </Badge>
                        )}
                        {!foreign && (
                          <>
                            <RadioGroupItem
                              value={String(host.id)}
                              id={`cp-host-${host.id}`}
                              className="sr-only"
                            />
                            <Label
                              htmlFor={`cp-host-${host.id}`}
                              className={cn(
                                'cursor-pointer px-2 py-1 rounded text-[10px] font-medium border transition-colors',
                                isCp
                                  ? 'bg-primary text-primary-foreground border-primary'
                                  : 'bg-muted hover:bg-accent border-border',
                              )}
                              onClick={() => setControlPlaneHostId(host.id)}
                            >
                              Set as CP
                            </Label>
                          </>
                        )}
                      </div>
                    </div>
                  );
                })}
              </RadioGroup>
            )}
          </div>

          {/* Member DPUs */}
          <div className={cn('rounded-lg border p-4 space-y-3', tc.borderDefault)}>
            <div className="flex items-center justify-between">
              <Label className="font-semibold text-sm flex items-center gap-2">
                <Cpu className="h-4 w-4 text-primary" />
                Member DPUs
              </Label>
              {selectableDpus.length > 0 && (
                <Button variant="ghost" size="sm" onClick={handleSelectAllDpus} className="text-xs h-7">
                  {selectedDpuIds.length === selectableDpus.length ? 'Deselect All' : 'Select All'}
                </Button>
              )}
            </div>

            {isLoading ? (
              <div className="flex items-center justify-center py-6">
                <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
              </div>
            ) : dpuList.length === 0 ? (
              <p className={cn('text-xs py-2', tc.textMuted)}>No DPUs registered in this project.</p>
            ) : (
              <div className="space-y-2">
                {dpuList.map((dpu: Dpu) => {
                  const foreign = isForeignDpu(dpu);
                  const isMember = !foreign && selectedDpuIds.includes(dpu.id);
                  const ownerHost = dpuOwnerHostMap.get(dpu.id);

                  return (
                    <div
                      key={dpu.id}
                      className={cn(
                        'flex items-center justify-between p-3 rounded-md border text-xs transition-colors',
                        foreign ? 'opacity-50' : isMember ? 'border-border' : 'opacity-60',
                        tc.borderDefault
                      )}
                    >
                      <div className="flex items-center gap-3">
                        <Checkbox
                          checked={isMember}
                          disabled={foreign}
                          onCheckedChange={() => toggleDpu(dpu.id)}
                          id={`dpu-member-${dpu.id}`}
                        />
                        <div className="flex flex-col">
                          <label htmlFor={`dpu-member-${dpu.id}`} className="font-medium cursor-pointer">
                            {dpu.name || dpu.serial_number || dpu.host_hostname || `DPU #${dpu.id}`}
                          </label>
                          <span className={cn('text-[11px]', tc.textMuted)}>
                            Mode: {dpu.nic_mode || 'nic'} · Host: {ownerHost?.hostname || ownerHost?.host_ip || 'Unknown'} · IP: {dpu.oob0_ipv4 || dpu.dpu_os_ip || 'Unassigned'} · Status: {dpu.last_discovery_status || 'active'}
                          </span>
                        </div>
                      </div>
                      {foreign ? (
                        <Badge variant="outline" className="text-[10px] border-warning/40 text-warning">
                          In cluster #{dpu.kubernetes_cluster_id}
                        </Badge>
                      ) : (
                        <Badge variant="outline" className="text-[10px]">
                          {dpu.last_discovery_status || 'active'}
                        </Badge>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>

        <DialogFooter className="pt-2 border-t">
          <Button variant="outline" size="sm" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            size="sm"
            onClick={handleSubmit}
            disabled={assignMembers.isPending || !controlPlaneHostId}
          >
            {assignMembers.isPending && <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />}
            Save & Orchestrate Cluster Members
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
