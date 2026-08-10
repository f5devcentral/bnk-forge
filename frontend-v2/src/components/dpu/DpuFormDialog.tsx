/**
 * Add/edit DPU dialog — shared form for creating and editing DPU entries.
 *
 * Supports two access modes:
 *   - "bmc": DPU is reached via its own BMC/Redfish. Fields: BMC IP + creds.
 *   - "in-band": DPU is managed via rshim on the host it's plugged into.
 *     Fields: host IP + host SSH user/port + password or private key.
 *
 * For edit mode the VLAN block is shown; access-mode is locked (it's a
 * deep attribute of how the DPU was registered, changing it here would
 * be nonsense).
 */
import { useEffect, useState } from 'react';

import { Eye, EyeOff, Loader2 } from 'lucide-react';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import type { Dpu, DpuCreate, DpuUpdate } from '@/lib/api/dpus';
import { dpusApi } from '@/lib/api/dpus';
import { notify } from '@/lib/notify';

type AccessMode = 'bmc' | 'in-band';
type AuthType = 'password' | 'key';

// Per-DPU extra VLAN IP assignment. `name` matches an entry in the project's
// extra_vlan_interfaces list; the backend joins the two at render time.
export interface ExtraVlanFormRow {
  name: string;
  vlan_id: string;
  ipv4: string;
  ipv6: string;
}

export interface DpuFormValues {
  name: string;
  access_mode: AccessMode;
  // BMC-mode
  bmc_ip: string;
  bmc_username: string;
  bmc_password: string;
  // In-band mode
  host_node_ip: string;
  pci_address: string;
  host_ssh_username: string;
  host_ssh_port: string;
  host_ssh_auth_type: AuthType;
  host_ssh_password: string;
  host_ssh_private_key: string;
  host_ssh_key_passphrase: string;
  // VLAN plan (edit-only) — fixed rows only; extras live on their own list.
  external_vlan_id: string;
  external_vlan_ipv4: string;
  external_vlan_ipv6: string;
  internal_vlan_id: string;
  internal_vlan_ipv4: string;
  internal_vlan_ipv6: string;
  extras: ExtraVlanFormRow[];
}

const EMPTY: DpuFormValues = {
  name: '',
  access_mode: 'bmc',
  bmc_ip: '',
  bmc_username: '',
  bmc_password: '',
  host_node_ip: '',
  pci_address: '',
  host_ssh_username: 'ubuntu',
  host_ssh_port: '22',
  host_ssh_auth_type: 'password',
  host_ssh_password: '',
  host_ssh_private_key: '',
  host_ssh_key_passphrase: '',
  external_vlan_id: '',
  external_vlan_ipv4: '',
  external_vlan_ipv6: '',
  internal_vlan_id: '',
  internal_vlan_ipv4: '',
  internal_vlan_ipv6: '',
  extras: [],
};

function toCreatePayload(f: DpuFormValues): DpuCreate {
  const base: DpuCreate = {
    name: f.name || null,
    access_mode: f.access_mode,
    bmc_ip: null,
    bmc_username: null,
    bmc_password: null,
    host_node_ip: null,
    host_ssh_username: null,
    host_ssh_port: null,
    host_ssh_auth_type: null,
    host_ssh_password: null,
    host_ssh_private_key: null,
    host_ssh_key_passphrase: null,
    oob0_ipv4: 'dhcp',
    pci_address: f.pci_address.trim() || null,
    external_vlan_id: f.external_vlan_id === '' ? null : Number(f.external_vlan_id),
    external_vlan_ipv4: f.external_vlan_ipv4 || null,
    external_vlan_ipv6: f.external_vlan_ipv6 || null,
    internal_vlan_id: f.internal_vlan_id === '' ? null : Number(f.internal_vlan_id),
    internal_vlan_ipv4: f.internal_vlan_ipv4 || null,
    internal_vlan_ipv6: f.internal_vlan_ipv6 || null,
    extra_vlan_interfaces: f.extras.map((e) => ({
      name: e.name.trim().toLowerCase(),
      vlan_id: e.vlan_id === '' ? null : Number(e.vlan_id),
      ipv4: e.ipv4 || null,
      ipv6: e.ipv6 || null,
    })),
  };
  if (f.access_mode === 'bmc') {
    base.bmc_ip = f.bmc_ip.trim();
    base.bmc_username = f.bmc_username || null;
    base.bmc_password = f.bmc_password || null;
  } else {
    base.host_node_ip = f.host_node_ip.trim();
    base.host_ssh_username = f.host_ssh_username.trim() || null;
    base.host_ssh_port = f.host_ssh_port === '' ? null : Number(f.host_ssh_port);
    base.host_ssh_auth_type = f.host_ssh_auth_type;
    if (f.host_ssh_auth_type === 'password') {
      base.host_ssh_password = f.host_ssh_password || null;
    } else {
      base.host_ssh_private_key = f.host_ssh_private_key || null;
      base.host_ssh_key_passphrase = f.host_ssh_key_passphrase || null;
    }
  }
  return base;
}

function toUpdatePayload(f: DpuFormValues, original: Dpu): DpuUpdate {
  const payload: DpuUpdate = {
    name: f.name || null,
    external_vlan_id: f.external_vlan_id === '' ? null : Number(f.external_vlan_id),
    external_vlan_ipv4: f.external_vlan_ipv4 || null,
    external_vlan_ipv6: f.external_vlan_ipv6 || null,
    internal_vlan_id: f.internal_vlan_id === '' ? null : Number(f.internal_vlan_id),
    internal_vlan_ipv4: f.internal_vlan_ipv4 || null,
    internal_vlan_ipv6: f.internal_vlan_ipv6 || null,
    extra_vlan_interfaces: f.extras.map((e) => ({
      name: e.name.trim().toLowerCase(),
      vlan_id: e.vlan_id === '' ? null : Number(e.vlan_id),
      ipv4: e.ipv4 || null,
      ipv6: e.ipv6 || null,
    })),
  };
  if (original.access_mode === 'bmc') {
    payload.bmc_ip = f.bmc_ip.trim();
    payload.bmc_username = f.bmc_username || null;
    if (f.bmc_password !== '') payload.bmc_password = f.bmc_password;
  } else {
    payload.host_node_ip = f.host_node_ip.trim();
    payload.host_ssh_username = f.host_ssh_username.trim() || null;
    payload.host_ssh_port = f.host_ssh_port === '' ? null : Number(f.host_ssh_port);
    payload.host_ssh_auth_type = f.host_ssh_auth_type;
    if (f.host_ssh_auth_type === 'password' && f.host_ssh_password !== '') {
      payload.host_ssh_password = f.host_ssh_password;
    }
    if (f.host_ssh_auth_type === 'key' && f.host_ssh_private_key !== '') {
      payload.host_ssh_private_key = f.host_ssh_private_key;
      payload.host_ssh_key_passphrase = f.host_ssh_key_passphrase || null;
    }
  }
  return payload;
}

export interface DpuFormDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  mode: 'create' | 'edit';
  editing?: Dpu | null;
  projectId: number;
  onSubmit: (payload: DpuCreate | DpuUpdate) => Promise<void>;
}

export function DpuFormDialog({ open, onOpenChange, mode, editing, projectId, onSubmit }: DpuFormDialogProps) {
  const [form, setForm] = useState<DpuFormValues>(EMPTY);
  const [submitting, setSubmitting] = useState(false);
  const [bmcPasswordRevealed, setBmcPasswordRevealed] = useState(false);
  const [hostPasswordRevealed, setHostPasswordRevealed] = useState(false);

  const toggleBmcReveal = async () => {
    if (bmcPasswordRevealed) {
      setForm((f) => ({ ...f, bmc_password: '' }));
      setBmcPasswordRevealed(false);
      return;
    }
    if (!editing) return;
    try {
      const pw = await dpusApi.revealDpuBmcPassword(projectId, editing.id);
      setForm((f) => ({ ...f, bmc_password: pw }));
      setBmcPasswordRevealed(true);
    } catch (err) {
      notify.error(err instanceof Error ? err.message : 'Could not reveal password');
    }
  };

  useEffect(() => {
    if (!open) return;
    setBmcPasswordRevealed(false);
    setHostPasswordRevealed(false);
    if (mode === 'edit' && editing) {
      setForm({
        name: editing.name ?? '',
        access_mode: (editing.access_mode as AccessMode) ?? 'bmc',
        bmc_ip: editing.bmc_ip ?? '',
        bmc_username: editing.bmc_username ?? '',
        bmc_password: '',
        host_node_ip: editing.host_node_ip ?? '',
        pci_address: editing.pci_address ?? '',
        host_ssh_username: editing.host_ssh_username ?? 'ubuntu',
        host_ssh_port: editing.host_ssh_port != null ? String(editing.host_ssh_port) : '22',
        host_ssh_auth_type: (editing.host_ssh_auth_type as AuthType) ?? 'password',
        host_ssh_password: '',
        host_ssh_private_key: '',
        host_ssh_key_passphrase: '',
        external_vlan_id: editing.external_vlan_id != null ? String(editing.external_vlan_id) : '',
        external_vlan_ipv4: editing.external_vlan_ipv4 ?? '',
        external_vlan_ipv6: editing.external_vlan_ipv6 ?? '',
        internal_vlan_id: editing.internal_vlan_id != null ? String(editing.internal_vlan_id) : '',
        internal_vlan_ipv4: editing.internal_vlan_ipv4 ?? '',
        internal_vlan_ipv6: editing.internal_vlan_ipv6 ?? '',
        extras: (editing.extra_vlan_interfaces ?? []).map((e) => ({
          name: e.name ?? '',
          vlan_id: e.vlan_id != null ? String(e.vlan_id) : '',
          ipv4: e.ipv4 ?? '',
          ipv6: e.ipv6 ?? '',
        })),
      });
    } else {
      setForm(EMPTY);
    }
  }, [open, mode, editing]);

  const submit = async () => {
    setSubmitting(true);
    try {
      const payload =
        mode === 'create' ? toCreatePayload(form) : toUpdatePayload(form, editing as Dpu);
      await onSubmit(payload);
      onOpenChange(false);
    } finally {
      setSubmitting(false);
    }
  };

  const valid =
    form.access_mode === 'bmc'
      ? form.bmc_ip.trim() !== ''
      : form.host_node_ip.trim() !== '' && form.host_ssh_username.trim() !== '';

  const accessModeLocked = mode === 'edit';

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{mode === 'create' ? 'Add DPU' : 'Edit DPU'}</DialogTitle>
          <DialogDescription>
            {mode === 'create'
              ? 'Register a DPU. Pick "BMC" when it has its own management IP, or "In-band" to manage it through the host it sits in via rshim.'
              : 'Update DPU details. Leave password/key fields blank to keep the stored secret unchanged.'}
          </DialogDescription>
        </DialogHeader>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 py-2">
          <div className="space-y-1">
            <Label htmlFor="name">Name (optional)</Label>
            <Input
              id="name"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="dpu-node-1"
            />
          </div>
          <div className="space-y-1">
            <Label>Access mode</Label>
            <Select
              value={form.access_mode}
              onValueChange={(v) => setForm({ ...form, access_mode: v as AccessMode })}
              disabled={accessModeLocked}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="bmc">BMC (dedicated management IP)</SelectItem>
                <SelectItem value="in-band">In-band (via host + rshim)</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>

        {form.access_mode === 'bmc' && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 py-2">
            <div className="space-y-1">
              <Label htmlFor="bmc_ip">BMC IP *</Label>
              <Input
                id="bmc_ip"
                value={form.bmc_ip}
                onChange={(e) => setForm({ ...form, bmc_ip: e.target.value })}
                placeholder="192.168.68.100"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="bmc_username">BMC username (optional)</Label>
              <Input
                id="bmc_username"
                value={form.bmc_username}
                onChange={(e) => setForm({ ...form, bmc_username: e.target.value })}
                placeholder="root"
              />
            </div>
            <div className="md:col-span-2 space-y-1">
              <Label htmlFor="bmc_password">BMC password (optional)</Label>
              <div className="relative">
                <Input
                  id="bmc_password"
                  type={bmcPasswordRevealed ? 'text' : 'password'}
                  value={form.bmc_password}
                  onChange={(e) => setForm({ ...form, bmc_password: e.target.value })}
                  placeholder={
                    mode === 'edit' && editing?.has_bmc_password ? '(unchanged)' : 'project default'
                  }
                  className="pr-10"
                />
                {mode === 'edit' && editing?.has_bmc_password && (
                  <button
                    type="button"
                    onClick={toggleBmcReveal}
                    aria-label={bmcPasswordRevealed ? 'Hide BMC password' : 'Show BMC password'}
                    className="absolute right-2 top-1/2 -translate-y-1/2 opacity-60 hover:opacity-100"
                  >
                    {bmcPasswordRevealed ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </button>
                )}
              </div>
            </div>
          </div>
        )}

        {form.access_mode === 'in-band' && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 py-2">
            <div className="space-y-1">
              <Label htmlFor="host_node_ip">Host IP *</Label>
              <Input
                id="host_node_ip"
                value={form.host_node_ip}
                onChange={(e) => setForm({ ...form, host_node_ip: e.target.value })}
                placeholder="10.0.0.10"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="pci_address">PCI address (optional)</Label>
              <Input
                id="pci_address"
                value={form.pci_address}
                onChange={(e) => setForm({ ...form, pci_address: e.target.value })}
                placeholder="0000:01:00"
                className="font-mono"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="host_ssh_username">Host SSH username *</Label>
              <Input
                id="host_ssh_username"
                value={form.host_ssh_username}
                onChange={(e) => setForm({ ...form, host_ssh_username: e.target.value })}
                placeholder="ubuntu"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="host_ssh_port">Host SSH port</Label>
              <Input
                id="host_ssh_port"
                type="number"
                value={form.host_ssh_port}
                onChange={(e) => setForm({ ...form, host_ssh_port: e.target.value })}
                placeholder="22"
              />
            </div>
            <div className="space-y-1">
              <Label>Authentication</Label>
              <Select
                value={form.host_ssh_auth_type}
                onValueChange={(v) => setForm({ ...form, host_ssh_auth_type: v as AuthType })}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="password">Password</SelectItem>
                  <SelectItem value="key">SSH private key</SelectItem>
                </SelectContent>
              </Select>
            </div>
            {form.host_ssh_auth_type === 'password' && (
              <div className="md:col-span-2 space-y-1">
                <Label htmlFor="host_ssh_password">Password</Label>
                <div className="relative">
                  <Input
                    id="host_ssh_password"
                    type={hostPasswordRevealed ? 'text' : 'password'}
                    value={form.host_ssh_password}
                    onChange={(e) => setForm({ ...form, host_ssh_password: e.target.value })}
                    placeholder={
                      mode === 'edit' && editing?.has_host_ssh_password ? '(unchanged)' : 'SSH password'
                    }
                    className="pr-10"
                  />
                  <button
                    type="button"
                    onClick={() => setHostPasswordRevealed((v) => !v)}
                    aria-label={hostPasswordRevealed ? 'Hide password' : 'Show password'}
                    className="absolute right-2 top-1/2 -translate-y-1/2 opacity-60 hover:opacity-100"
                  >
                    {hostPasswordRevealed ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </button>
                </div>
              </div>
            )}
            {form.host_ssh_auth_type === 'key' && (
              <>
                <div className="md:col-span-2 space-y-1">
                  <Label htmlFor="host_ssh_private_key">Private key (PEM/OpenSSH)</Label>
                  <Textarea
                    id="host_ssh_private_key"
                    rows={5}
                    className="font-mono text-xs"
                    value={form.host_ssh_private_key}
                    onChange={(e) => setForm({ ...form, host_ssh_private_key: e.target.value })}
                    placeholder={
                      mode === 'edit' && editing?.has_host_ssh_private_key
                        ? '(unchanged — paste a new key to replace)'
                        : '-----BEGIN OPENSSH PRIVATE KEY-----\n...\n-----END OPENSSH PRIVATE KEY-----'
                    }
                  />
                </div>
                <div className="md:col-span-2 space-y-1">
                  <Label htmlFor="host_ssh_key_passphrase">Key passphrase (optional)</Label>
                  <Input
                    id="host_ssh_key_passphrase"
                    type="password"
                    value={form.host_ssh_key_passphrase}
                    onChange={(e) => setForm({ ...form, host_ssh_key_passphrase: e.target.value })}
                  />
                </div>
              </>
            )}
          </div>
        )}

        {mode === 'edit' && (
          <div className="space-y-2">
            <div className="text-sm font-semibold">Per-DPU VLAN IPs</div>
            <div className="text-xs text-muted-foreground">
              VLAN IDs come from project defaults. Edit each address's host
              part here. Extras (storage, …) come from the project's VLAN
              list; edit the Project Defaults card to add or remove rows.
            </div>
            {(
              [
                ['external', 'External'],
                ['internal', 'Internal'],
              ] as const
            ).map(([prefix, label]) => (
              <div key={prefix} className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <div className="space-y-1">
                  <Label htmlFor={`${prefix}_vlan_ipv4`}>{label} IPv4 / mask</Label>
                  <Input
                    id={`${prefix}_vlan_ipv4`}
                    value={form[`${prefix}_vlan_ipv4`]}
                    onChange={(e) => setForm({ ...form, [`${prefix}_vlan_ipv4`]: e.target.value })}
                    placeholder="10.1.0.10/24"
                    className="font-mono"
                  />
                </div>
                <div className="space-y-1">
                  <Label htmlFor={`${prefix}_vlan_ipv6`}>{label} IPv6 / mask</Label>
                  <Input
                    id={`${prefix}_vlan_ipv6`}
                    value={form[`${prefix}_vlan_ipv6`]}
                    onChange={(e) => setForm({ ...form, [`${prefix}_vlan_ipv6`]: e.target.value })}
                    placeholder="fd00:1::a/64"
                    className="font-mono"
                  />
                </div>
              </div>
            ))}
            {form.extras.map((row, idx) => (
              <div key={`extra-${idx}`} className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <div className="space-y-1">
                  <Label htmlFor={`extra_${idx}_ipv4`} className="capitalize">
                    {row.name || `Extra ${idx + 1}`} IPv4 / mask
                  </Label>
                  <Input
                    id={`extra_${idx}_ipv4`}
                    value={row.ipv4}
                    onChange={(e) => {
                      const next = [...form.extras];
                      next[idx] = { ...next[idx], ipv4: e.target.value };
                      setForm({ ...form, extras: next });
                    }}
                    placeholder="10.3.0.10/24"
                    className="font-mono"
                  />
                </div>
                <div className="space-y-1">
                  <Label htmlFor={`extra_${idx}_ipv6`} className="capitalize">
                    {row.name || `Extra ${idx + 1}`} IPv6 / mask
                  </Label>
                  <Input
                    id={`extra_${idx}_ipv6`}
                    value={row.ipv6}
                    onChange={(e) => {
                      const next = [...form.extras];
                      next[idx] = { ...next[idx], ipv6: e.target.value };
                      setForm({ ...form, extras: next });
                    }}
                    placeholder="fd00:3::a/64"
                    className="font-mono"
                  />
                </div>
              </div>
            ))}
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={!valid || submitting}>
            {submitting && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
            {mode === 'create' ? 'Add' : 'Save'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
