import { useEffect, useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Label } from '@/components/ui/label';
import { useUpdateCluster } from '@/hooks/k8s/useClusterCRUD';
import type { K8sCluster } from '@/types';

// Mirrors backend KNOWN_PREREQUISITE_IDS / DEFAULT_ENABLED_PREREQUISITES /
// LOCKED_PREREQUISITE_IDS in services/scanner/recommendations.py.
const KNOWN_PREREQUISITES: { id: string; label: string; description: string }[] = [
  {
    id: 'cert-manager',
    label: 'cert-manager',
    description: 'TLS certificate management. Required for BNK internal OTEL certs and ClusterIssuers.',
  },
  {
    id: 'multus',
    label: 'Multus CNI',
    description: 'Multi-network pod attachments. Required for any BNK deployment.',
  },
  {
    id: 'sriov',
    label: 'SR-IOV device plugin',
    description: 'Bare-metal/dedicated-node feature for high-performance data-plane networking. Off by default.',
  },
  {
    id: 'hugepages',
    label: 'HugePages',
    description: 'Bare-metal/dedicated-node feature for low-latency workloads. Off by default.',
  },
  {
    id: 'storage',
    label: 'Default StorageClass',
    description: 'A default StorageClass for persistent volumes.',
  },
  {
    id: 'gateway-api',
    label: 'Gateway API',
    description: 'Required for BNK gateway routing.',
  },
];

const DEFAULT_ENABLED = new Set(['cert-manager', 'multus', 'storage', 'gateway-api']);
const LOCKED = new Set(['multus']);

interface ClusterPrerequisitesDialogProps {
  open: boolean;
  cluster: K8sCluster | null;
  onOpenChange: (open: boolean) => void;
}

export function ClusterPrerequisitesDialog({ open, cluster, onOpenChange }: ClusterPrerequisitesDialogProps) {
  const updateCluster = useUpdateCluster();
  const [enabled, setEnabled] = useState<Set<string>>(new Set());

  // When the dialog opens for a cluster, hydrate the checkbox state from the
  // cluster's stored selection (or fall back to the defaults).
  useEffect(() => {
    if (!open || !cluster) return;
    const stored = cluster.enabled_prerequisites;
    const initial = stored && stored.length > 0 ? new Set(stored) : new Set(DEFAULT_ENABLED);
    LOCKED.forEach((id) => initial.add(id));
    setEnabled(initial);
  }, [open, cluster]);

  const toggle = (id: string, checked: boolean) => {
    if (LOCKED.has(id)) return;
    setEnabled((prev) => {
      const next = new Set(prev);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  };

  const handleSave = () => {
    if (!cluster) return;
    updateCluster.mutate(
      { clusterId: cluster.id, data: { enabled_prerequisites: Array.from(enabled).sort() } },
      { onSuccess: () => onOpenChange(false) },
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Prerequisite checks</DialogTitle>
          <DialogDescription>
            Choose which prerequisite checks the scanner runs against{' '}
            <span className="font-mono">{cluster?.name}</span>. Leave SR-IOV and HugePages off
            unless this cluster runs on bare metal with dedicated data-plane hardware.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3 py-2">
          {KNOWN_PREREQUISITES.map((p) => {
            const isLocked = LOCKED.has(p.id);
            const isChecked = enabled.has(p.id);
            return (
              <div key={p.id} className="flex items-start gap-3">
                <Checkbox
                  id={`prereq-${p.id}`}
                  checked={isChecked}
                  disabled={isLocked || updateCluster.isPending}
                  onCheckedChange={(c) => toggle(p.id, c === true)}
                  className="mt-0.5"
                />
                <div className="flex-1 min-w-0">
                  <Label htmlFor={`prereq-${p.id}`} className="cursor-pointer">
                    {p.label}
                    {isLocked && (
                      <span className="ml-2 text-[10px] uppercase tracking-wide text-muted-foreground">
                        always required
                      </span>
                    )}
                  </Label>
                  <p className="text-xs text-muted-foreground mt-0.5">{p.description}</p>
                </div>
              </div>
            );
          })}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={updateCluster.isPending}>
            Cancel
          </Button>
          <Button onClick={handleSave} disabled={updateCluster.isPending}>
            {updateCluster.isPending ? 'Saving...' : 'Save'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
