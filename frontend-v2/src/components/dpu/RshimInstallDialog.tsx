/**
 * Dialog: Install / probe rshim on an in-band DPU's host.
 *
 * Probes status on open (using whatever credentials the DPU has — inline
 * fields take precedence, FK is the fallback). When the probe fails
 * because no credential is set yet, the operator can either pick an
 * existing saved credential or create a new one inline, pre-filled from
 * the DPU's already-stored host_ssh_username / port / auth_type.
 */
import { useEffect, useState } from 'react';

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
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
import { api } from '@/lib/api';
import { queryKeys } from '@/lib/queryKeys';

import type { Dpu, RshimStatus } from '@/lib/api/dpus';
import { dpusApi } from '@/lib/api/dpus';
import { notify } from '@/lib/notify';

interface RshimInstallDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: number;
  dpu: Dpu | null;
}

type CredChoice = 'pick' | 'create';

function extractError(err: unknown): string {
  const e = err as { response?: { data?: { error?: { message?: string } } } };
  return (
    e.response?.data?.error?.message
    ?? (err instanceof Error ? err.message : 'Request failed')
  );
}

function isMissingCredentialError(msg: string): boolean {
  return /no host SSH credential/i.test(msg);
}

export function RshimInstallDialog({ open, onOpenChange, projectId, dpu }: RshimInstallDialogProps) {
  const qc = useQueryClient();

  const [status, setStatus] = useState<RshimStatus | null>(null);
  const [statusError, setStatusError] = useState<string>('');
  const [statusLoading, setStatusLoading] = useState(false);
  const [needsCred, setNeedsCred] = useState(false);

  const [installing, setInstalling] = useState(false);

  const [credChoice, setCredChoice] = useState<CredChoice>('pick');
  const [pickedCredId, setPickedCredId] = useState<string>('');
  const [savingCred, setSavingCred] = useState(false);

  // Create-new fields
  const [newCredName, setNewCredName] = useState('');
  const [newCredAuthType, setNewCredAuthType] = useState<'password' | 'key'>('password');
  const [newCredPassword, setNewCredPassword] = useState('');
  const [newCredPrivateKey, setNewCredPrivateKey] = useState('');
  const [newCredPassphrase, setNewCredPassphrase] = useState('');

  const { data: sshCredentials } = useQuery({
    queryKey: queryKeys.sshCredentials.list(),
    queryFn: () => api.listSSHCredentials(),
    enabled: open,
  });

  const probeStatus = async () => {
    if (!dpu) return;
    setStatusLoading(true);
    try {
      const s = await dpusApi.rshimStatus(projectId, dpu.id);
      setStatus(s);
      setStatusError('');
      setNeedsCred(false);
    } catch (err) {
      const msg = extractError(err);
      setStatus(null);
      setStatusError(msg);
      setNeedsCred(isMissingCredentialError(msg));
    } finally {
      setStatusLoading(false);
    }
  };

  useEffect(() => {
    if (!open || !dpu) {
      setStatus(null);
      setStatusError('');
      setPickedCredId('');
      setNeedsCred(false);
      return;
    }
    setPickedCredId(
      dpu.host_ssh_credential_id != null ? String(dpu.host_ssh_credential_id) : '',
    );
    // Seed "create" form from whatever's on the DPU (non-sensitive only —
    // the operator still has to type the actual password/key).
    setCredChoice('pick');
    setNewCredName(dpu.host_node_ip ? `host-${dpu.host_node_ip}` : '');
    setNewCredAuthType((dpu.host_ssh_auth_type as 'password' | 'key') ?? 'password');
    setNewCredPassword('');
    setNewCredPrivateKey('');
    setNewCredPassphrase('');
    // Probe immediately — backend decides whether we have usable credentials.
    probeStatus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, projectId, dpu]);

  const savePickedCredentialAndProbe = async () => {
    if (!dpu || pickedCredId === '') return;
    setSavingCred(true);
    try {
      await dpusApi.update(projectId, dpu.id, {
        host_ssh_credential_id: Number(pickedCredId),
      });
      await qc.invalidateQueries({ queryKey: queryKeys.dpuProvisioning.dpus.byProject(projectId) });
      notify.success('Host SSH credential saved.', undefined, { category: 'credentials' });
      await probeStatus();
    } catch (err) {
      notify.error(extractError(err));
    } finally {
      setSavingCred(false);
    }
  };

  const createCredentialAndProbe = async () => {
    if (!dpu) return;
    if (!newCredName.trim()) {
      notify.error('Credential name is required.');
      return;
    }
    if (newCredAuthType === 'password' && !newCredPassword) {
      notify.error('Password is required.');
      return;
    }
    if (newCredAuthType === 'key' && !newCredPrivateKey) {
      notify.error('Private key is required.');
      return;
    }
    setSavingCred(true);
    try {
      const created = await api.createSSHCredential({
        name: newCredName.trim(),
        host: dpu.host_node_ip ?? '',
        port: dpu.host_ssh_port ?? 22,
        username: dpu.host_ssh_username ?? 'ubuntu',
        auth_type: newCredAuthType,
        password: newCredAuthType === 'password' ? newCredPassword : null,
        private_key: newCredAuthType === 'key' ? newCredPrivateKey : null,
        key_passphrase: newCredAuthType === 'key' ? (newCredPassphrase || null) : null,
      });
      await dpusApi.update(projectId, dpu.id, {
        host_ssh_credential_id: created.id,
      });
      await qc.invalidateQueries({ queryKey: queryKeys.sshCredentials.list() });
      await qc.invalidateQueries({ queryKey: queryKeys.dpuProvisioning.dpus.byProject(projectId) });
      notify.success(`Created credential "${created.name}" and linked it to this DPU.`, undefined, { category: 'credentials' });
      await probeStatus();
    } catch (err) {
      notify.error(extractError(err));
    } finally {
      setSavingCred(false);
    }
  };

  const runInstall = async () => {
    if (!dpu) return;
    // Fire-and-forget: kick off the async install on the backend, close
    // the dialog immediately, let polling show progress on the DPU row.
    setInstalling(true);
    try {
      await dpusApi.installRshim(projectId, dpu.id);
      notify.success(`Installing rshim + MFT on ${dpu.host_node_ip}… watch the DPU row for status.`, undefined, { category: 'deployment' });
      qc.invalidateQueries({ queryKey: queryKeys.dpuProvisioning.dpus.byProject(projectId) });
      onOpenChange(false);
    } catch (err) {
      notify.error(extractError(err));
    } finally {
      setInstalling(false);
    }
  };

  const statusBadge = (s: RshimStatus) => {
    if (s.device_present && s.mft_installed && s.mst_running) {
      return <Badge variant="success">Ready (rshim + MFT)</Badge>;
    }
    if (s.device_present) {
      return <Badge variant="warning">rshim running; MFT missing</Badge>;
    }
    if (s.installed && !s.active) {
      return <Badge variant="warning">rshim installed, not active</Badge>;
    }
    if (s.installed) {
      return <Badge variant="warning">rshim active, device missing</Badge>;
    }
    return <Badge variant="muted">Not installed</Badge>;
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>rshim + MFT on {dpu?.host_node_ip ?? 'host'}</DialogTitle>
          <DialogDescription>
            In-band DPUs are managed through the host they sit in. This installs
            rshim (register access + boot), MFT (<span className="font-mono">mlxfwmanager</span>,{' '}
            <span className="font-mono">mst</span>, etc. for FW identity), and starts both.
            Runs via SSH — through the project jumphost if one is configured.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3 text-sm">
          {needsCred && dpu && (
            <div className="rounded border border-warning/20 bg-warning/10 p-3 space-y-3">
              <p className="text-xs">
                No SSH credential is set for this DPU's host yet. Either pick an
                existing saved credential, or create a new one (pre-filled with
                the username and port already stored on this DPU).
              </p>

              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant={credChoice === 'pick' ? 'default' : 'outline'}
                  onClick={() => setCredChoice('pick')}
                >
                  Pick existing
                </Button>
                <Button
                  size="sm"
                  variant={credChoice === 'create' ? 'default' : 'outline'}
                  onClick={() => setCredChoice('create')}
                >
                  Create new
                </Button>
              </div>

              {credChoice === 'pick' && (
                <div className="space-y-2">
                  <div className="space-y-1">
                    <Label>Saved SSH credential</Label>
                    <Select value={pickedCredId} onValueChange={setPickedCredId}>
                      <SelectTrigger className="h-8">
                        <SelectValue placeholder="Select saved credential" />
                      </SelectTrigger>
                      <SelectContent>
                        {sshCredentials?.map((cred) => (
                          <SelectItem key={cred.id} value={String(cred.id)}>
                            {cred.name} — {cred.username}@{cred.host}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <Button
                    size="sm"
                    disabled={pickedCredId === '' || savingCred}
                    onClick={savePickedCredentialAndProbe}
                  >
                    {savingCred && <Loader2 className="h-3 w-3 mr-1 animate-spin" />}
                    Save & probe
                  </Button>
                </div>
              )}

              {credChoice === 'create' && (
                <div className="space-y-2">
                  <div className="grid grid-cols-2 gap-2">
                    <div className="space-y-1">
                      <Label>Name</Label>
                      <Input
                        className="h-8"
                        value={newCredName}
                        onChange={(e) => setNewCredName(e.target.value)}
                      />
                    </div>
                    <div className="space-y-1">
                      <Label>Host</Label>
                      <Input
                        className="h-8 font-mono text-xs"
                        value={dpu.host_node_ip ?? ''}
                        disabled
                      />
                    </div>
                    <div className="space-y-1">
                      <Label>Username</Label>
                      <Input
                        className="h-8 font-mono text-xs"
                        value={dpu.host_ssh_username ?? 'ubuntu'}
                        disabled
                      />
                    </div>
                    <div className="space-y-1">
                      <Label>Port</Label>
                      <Input
                        className="h-8 font-mono text-xs"
                        value={String(dpu.host_ssh_port ?? 22)}
                        disabled
                      />
                    </div>
                  </div>
                  <p className="text-[11px] text-muted-foreground">
                    Username/port come from the DPU row. Edit them on the DPU first if wrong.
                  </p>

                  <div className="space-y-1">
                    <Label>Authentication</Label>
                    <Select
                      value={newCredAuthType}
                      onValueChange={(v) => setNewCredAuthType(v as 'password' | 'key')}
                    >
                      <SelectTrigger className="h-8">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="password">Password</SelectItem>
                        <SelectItem value="key">SSH private key</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  {newCredAuthType === 'password' && (
                    <div className="space-y-1">
                      <Label>Password</Label>
                      <Input
                        className="h-8"
                        type="password"
                        value={newCredPassword}
                        onChange={(e) => setNewCredPassword(e.target.value)}
                      />
                    </div>
                  )}
                  {newCredAuthType === 'key' && (
                    <>
                      <div className="space-y-1">
                        <Label>Private key (PEM/OpenSSH)</Label>
                        <Textarea
                          rows={4}
                          className="font-mono text-xs"
                          value={newCredPrivateKey}
                          onChange={(e) => setNewCredPrivateKey(e.target.value)}
                          placeholder={'-----BEGIN OPENSSH PRIVATE KEY-----\n...\n-----END OPENSSH PRIVATE KEY-----'}
                        />
                      </div>
                      <div className="space-y-1">
                        <Label>Key passphrase (optional)</Label>
                        <Input
                          className="h-8"
                          type="password"
                          value={newCredPassphrase}
                          onChange={(e) => setNewCredPassphrase(e.target.value)}
                        />
                      </div>
                    </>
                  )}
                  <Button
                    size="sm"
                    disabled={savingCred}
                    onClick={createCredentialAndProbe}
                  >
                    {savingCred && <Loader2 className="h-3 w-3 mr-1 animate-spin" />}
                    Create, save & probe
                  </Button>
                </div>
              )}
            </div>
          )}

          <div className="flex items-center gap-2">
            <span className="text-muted-foreground">Current status:</span>
            {statusLoading && <Loader2 className="h-4 w-4 animate-spin" />}
            {!statusLoading && status && statusBadge(status)}
            {!statusLoading && statusError && !needsCred && (
              <span className="text-destructive text-xs">{statusError}</span>
            )}
            {!statusLoading && !status && needsCred && (
              <span className="text-muted-foreground text-xs">— fill in credential above</span>
            )}
          </div>
          {status && <p className="text-muted-foreground text-xs">{status.message}</p>}
        </div>

        <DialogFooter>
          {status?.device_present ? (
            <>
              <Button
                variant="ghost"
                onClick={runInstall}
                disabled={installing || !dpu}
              >
                {installing && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                Re-run install
              </Button>
              <Button onClick={() => onOpenChange(false)} disabled={installing}>
                Close
              </Button>
            </>
          ) : (
            <>
              <Button variant="outline" onClick={() => onOpenChange(false)} disabled={installing}>
                Close
              </Button>
              <Button
                onClick={runInstall}
                disabled={installing || !dpu || needsCred || Boolean(statusError)}
              >
                {installing && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                Install rshim + MFT
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
