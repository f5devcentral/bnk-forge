/**
 * Project-level DPU settings card.
 *
 * Applied as defaults to all DPUs in the project: BFB image, high-speed
 * interface MTU/LAG, DNS list, and default credentials (OOB + OS).
 */
import { useEffect, useRef, useState } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { ChevronDown, ChevronRight, Eye, EyeOff, KeyRound, Loader2, Plus, Save, Trash2, Upload } from 'lucide-react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { dpusApi } from '@/lib/api/dpus';
import { sshCredentialsApi } from '@/lib/api/ssh-credentials';
import { useDpuSettings, useUpdateDpuSettings } from '@/hooks/useDpus';
import { useBluefieldImages } from '@/hooks/useBluefieldImages';
import { useBfConfTemplates } from '@/hooks/useBfConfTemplates';
import { queryKeys } from '@/lib/queryKeys';
import { notify } from '@/lib/notify';

type UplinkChoice = '' | 'bond0' | 'p0' | 'p1';
const UPLINK_CHOICES: UplinkChoice[] = ['', 'bond0', 'p0', 'p1'];
// Radix Select rejects empty-string values, so map '' ↔ this sentinel for the Select control.
const UPLINK_EMPTY = '__empty__';

/**
 * Filter the Uplink port dropdown to the choices the selected bf.conf
 * template actually exposes. LAG templates build bond0 and consume p0/p1
 * to do so, so VLANs only attach to bond0; non-LAG templates leave p0/p1
 * directly attachable and never define bond0. Custom templates ("unknown"
 * mode) keep all choices so the operator stays in control.
 */
function uplinkChoicesForMode(mode: string | null | undefined): UplinkChoice[] {
  if (mode === 'lag') return ['', 'bond0'];
  if (mode === 'p0p1') return ['', 'p0', 'p1'];
  return UPLINK_CHOICES;
}

// Editable extra VLAN row (anything beyond external + internal).
interface ExtraVlanRow {
  name: string;
  vlan_id: string;
  ipv4_subnet: string;
  ipv6_subnet: string;
  start_host: string;
  uplink: UplinkChoice;
}

const RESERVED_VLAN_NAMES = new Set(['external', 'internal']);

interface DpuProjectSettingsCardProps {
  projectId: number;
  disabled?: boolean;
}

const UNSET = '__unset__';

export function DpuProjectSettingsCard({ projectId, disabled = false }: DpuProjectSettingsCardProps) {
  const { data: settings, isLoading } = useDpuSettings(projectId);
  const { data: images } = useBluefieldImages();
  const { data: bfTemplates } = useBfConfTemplates();
  const updateMut = useUpdateDpuSettings(projectId);
  const queryClient = useQueryClient();
  const { data: sshCredentials } = useQuery({
    queryKey: queryKeys.sshCredentials.all,
    queryFn: sshCredentialsApi.listSSHCredentials,
  });
  const [generatingKey, setGeneratingKey] = useState(false);
  const [uploadDialogOpen, setUploadDialogOpen] = useState(false);
  const [uploadForm, setUploadForm] = useState({
    name: '',
    private_key: '',
    key_passphrase: '',
  });
  const [uploading, setUploading] = useState(false);
  const uploadFileRef = useRef<HTMLInputElement>(null);

  const [expanded, setExpanded] = useState(false);
  const [oobPasswordRevealed, setOobPasswordRevealed] = useState(false);
  const [osPasswordRevealed, setOsPasswordRevealed] = useState(false);
  const [form, setForm] = useState({
    bluefield_image_id: '' as string,
    bf_template_id: '' as string,
    high_speed_mtu: '9000',
    dns_servers: '1.1.1.1,8.8.8.8',
    ntp_servers: 'ntp.ubuntu.com',
    default_oob_username: 'root',
    default_oob_password: '0penBmc',
    default_os_username: 'ubuntu',
    default_os_password: '',
    default_os_ssh_credential_id: '' as string,
    external_vlan_id: '0',
    external_vlan_ipv4_subnet: '10.10.20.0/24',
    external_vlan_ipv6_subnet: '',
    external_vlan_start_host: '100',
    external_vlan_uplink: 'bond0' as UplinkChoice,
    internal_vlan_id: '100',
    internal_vlan_ipv4_subnet: '10.10.10.0/24',
    internal_vlan_ipv6_subnet: '',
    internal_vlan_start_host: '100',
    internal_vlan_uplink: 'bond0' as UplinkChoice,
  });
  const [extras, setExtras] = useState<ExtraVlanRow[]>([]);

  // The bf.conf template selected in the form decides which uplink ports
  // are valid VLAN attachment points: LAG → bond0 only, non-LAG → p0/p1
  // only. We derive the choices once per render and reuse them on every
  // dropdown so the table stays in sync with the template selection.
  const selectedTemplate = bfTemplates?.find(
    (t) => String(t.id) === form.bf_template_id,
  );
  const availableUplinks = uplinkChoicesForMode(selectedTemplate?.uplink_mode);
  const isUplinkAllowed = (u: UplinkChoice): boolean =>
    availableUplinks.includes(u);

  // Clear any stale uplink selections when the operator switches the
  // template to a mode that no longer offers the previously-chosen port
  // (e.g. LAG → non-LAG should drop bond0). For the non-LAG path we
  // also auto-fill external→p0, internal→p1, and pin both VLAN ids to
  // 0 (untagged): non-LAG only supports those two uplinks with no
  // shared port and no extra VLANs, so guessing here saves a click and
  // documents intent. Operators can still edit, but the save-time
  // validator will reject deviations.
  useEffect(() => {
    if (!selectedTemplate) return;
    const mode = selectedTemplate.uplink_mode;
    setForm((f) => {
      if (mode === 'p0p1') {
        return {
          ...f,
          external_vlan_uplink: 'p0',
          external_vlan_id: '0',
          internal_vlan_uplink: 'p1',
          internal_vlan_id: '0',
        };
      }
      if (mode === 'lag') {
        // Switching to LAG: bond0 is the only valid uplink, so auto-
        // pin both VLANs there. Without this, a previous p0/p1 value
        // is `isUplinkAllowed = false` → reset to '' → operator would
        // see two blank uplink dropdowns and have to re-pick.
        return {
          ...f,
          external_vlan_uplink: 'bond0',
          internal_vlan_uplink: 'bond0',
        };
      }
      return {
        ...f,
        external_vlan_uplink: isUplinkAllowed(f.external_vlan_uplink)
          ? f.external_vlan_uplink
          : '',
        internal_vlan_uplink: isUplinkAllowed(f.internal_vlan_uplink)
          ? f.internal_vlan_uplink
          : '',
      };
    });
    if (mode === 'p0p1') {
      // Non-LAG forbids extras entirely — drop them rather than carry
      // a config the operator can't save.
      setExtras([]);
    } else if (mode === 'lag') {
      // LAG: every extra row also needs bond0 (the only valid uplink
      // in this mode), not whatever stale p0/p1 value it carried.
      setExtras((rows) => rows.map((r) => ({ ...r, uplink: 'bond0' })));
    } else {
      setExtras((rows) =>
        rows.map((r) =>
          isUplinkAllowed(r.uplink) ? r : { ...r, uplink: '' },
        ),
      );
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedTemplate?.uplink_mode]);

  useEffect(() => {
    if (!settings) return;
    // Auto-select first BFB image / bf.conf template if none saved.
    const fallbackImageId =
      images && images.length > 0 ? String(images[0].id) : '';
    const fallbackTemplateId =
      bfTemplates && bfTemplates.length > 0 ? String(bfTemplates[0].id) : '';

    // A project is considered "pristine" if no DPU-specific field has been
    // saved yet. In that case we apply the recommended defaults across the
    // board (root/0penBmc for BMC, ubuntu for DPU OS) so the operator
    // only needs to click "Save defaults". Once ANY field has been saved, we
    // respect the saved values exactly.
    const isPristine =
      settings.bluefield_image_id == null
      && settings.high_speed_mtu == null
      && !settings.default_oob_username
      && !settings.has_default_oob_password
      && !settings.default_os_username
      && !settings.has_default_os_password;

    const uplinkOf = (
      v: string | null | undefined,
      { emptyIfUnset = false }: { emptyIfUnset?: boolean } = {},
    ): UplinkChoice => {
      if (v === 'bond0' || v === 'p0' || v === 'p1') return v;
      return emptyIfUnset ? '' : 'bond0';
    };

    setForm({
      bluefield_image_id:
        settings.bluefield_image_id != null
          ? String(settings.bluefield_image_id)
          : fallbackImageId,
      bf_template_id:
        settings.bf_template_id != null
          ? String(settings.bf_template_id)
          : fallbackTemplateId,
      high_speed_mtu: settings.high_speed_mtu != null ? String(settings.high_speed_mtu) : '9000',
      dns_servers: (settings.dns_servers ?? ['1.1.1.1', '8.8.8.8']).join(','),
      ntp_servers: (settings.ntp_servers ?? ['ntp.ubuntu.com']).join(','),
      default_oob_username: settings.default_oob_username ?? (isPristine ? 'root' : ''),
      default_oob_password:
        !settings.has_default_oob_password && isPristine ? '0penBmc' : '',
      default_os_username: settings.default_os_username ?? (isPristine ? 'ubuntu' : ''),
      default_os_password: '',
      default_os_ssh_credential_id:
        settings.default_os_ssh_credential_id != null
          ? String(settings.default_os_ssh_credential_id)
          : '',
      // VLAN defaults apply per-field when nothing's been saved for that field yet.
      external_vlan_id:
        settings.external_vlan_id != null ? String(settings.external_vlan_id) : '0',
      external_vlan_ipv4_subnet: settings.external_vlan_ipv4_subnet ?? '10.10.20.0/24',
      external_vlan_ipv6_subnet: settings.external_vlan_ipv6_subnet ?? '',
      external_vlan_start_host:
        settings.external_vlan_start_host != null
          ? String(settings.external_vlan_start_host)
          : '100',
      external_vlan_uplink: uplinkOf(settings.external_vlan_uplink),
      internal_vlan_id:
        settings.internal_vlan_id != null ? String(settings.internal_vlan_id) : '100',
      internal_vlan_ipv4_subnet: settings.internal_vlan_ipv4_subnet ?? '10.10.10.0/24',
      internal_vlan_ipv6_subnet: settings.internal_vlan_ipv6_subnet ?? '',
      internal_vlan_start_host:
        settings.internal_vlan_start_host != null
          ? String(settings.internal_vlan_start_host)
          : '100',
      internal_vlan_uplink: uplinkOf(settings.internal_vlan_uplink),
    });
    setExtras(
      (settings.extra_vlan_interfaces ?? []).map((e) => ({
        name: e.name ?? '',
        vlan_id: e.vlan_id != null ? String(e.vlan_id) : '',
        ipv4_subnet: e.ipv4_subnet ?? '',
        ipv6_subnet: e.ipv6_subnet ?? '',
        start_host: e.start_host != null ? String(e.start_host) : '100',
        uplink: uplinkOf(e.uplink, { emptyIfUnset: true }),
      })),
    );
  }, [settings, images, bfTemplates]);

  const toggleOobReveal = async () => {
    if (oobPasswordRevealed) {
      // Hide — reset the form input back to empty so "unchanged" semantics hold.
      setForm((f) => ({ ...f, default_oob_password: '' }));
      setOobPasswordRevealed(false);
      return;
    }
    try {
      const pw = await dpusApi.revealDefaultOobPassword(projectId);
      setForm((f) => ({ ...f, default_oob_password: pw }));
      setOobPasswordRevealed(true);
    } catch (err) {
      notify.error(err instanceof Error ? err.message : 'Could not reveal password');
    }
  };

  const toggleOsReveal = async () => {
    if (osPasswordRevealed) {
      setForm((f) => ({ ...f, default_os_password: '' }));
      setOsPasswordRevealed(false);
      return;
    }
    try {
      const pw = await dpusApi.revealDefaultOsPassword(projectId);
      setForm((f) => ({ ...f, default_os_password: pw }));
      setOsPasswordRevealed(true);
    } catch (err) {
      notify.error(err instanceof Error ? err.message : 'Could not reveal password');
    }
  };

  const generateAndSelectSshKey = async () => {
    setGeneratingKey(true);
    try {
      const pair = await sshCredentialsApi.generateKeyPair('ed25519');
      // Ensure a unique name — the catalog enforces unique `name`.
      const baseName = `dpu-os-project-${projectId}`;
      let name = baseName;
      let suffix = 1;
      const existingNames = new Set((sshCredentials ?? []).map((c) => c.name));
      while (existingNames.has(name)) {
        suffix += 1;
        name = `${baseName}-${suffix}`;
      }
      const created = await sshCredentialsApi.createSSHCredential({
        name,
        description: `Generated for DPU OS access (project #${projectId})`,
        host: 'dpu-os',
        port: 22,
        username: form.default_os_username || 'ubuntu',
        auth_type: 'key',
        private_key: pair.private_key,
      });
      queryClient.invalidateQueries({ queryKey: queryKeys.sshCredentials.all });
      setForm((f) => ({ ...f, default_os_ssh_credential_id: String(created.id) }));
      notify.success(`Generated SSH key and saved as "${created.name}"`, undefined, { category: 'credentials' });
    } catch (err) {
      const axiosErr = err as { response?: { data?: { detail?: string; message?: string } } };
      const backendMsg = axiosErr.response?.data?.detail ?? axiosErr.response?.data?.message;
      notify.error(backendMsg ?? (err instanceof Error ? err.message : 'Key generation failed'));
    } finally {
      setGeneratingKey(false);
    }
  };

  const openUploadDialog = () => {
    const base = `dpu-os-project-${projectId}`;
    let name = base;
    let suffix = 1;
    const existing = new Set((sshCredentials ?? []).map((c) => c.name));
    while (existing.has(name)) {
      suffix += 1;
      name = `${base}-${suffix}`;
    }
    setUploadForm({ name, private_key: '', key_passphrase: '' });
    setUploadDialogOpen(true);
  };

  const handleUploadFile = async (ev: React.ChangeEvent<HTMLInputElement>) => {
    const file = ev.target.files?.[0];
    ev.target.value = '';
    if (!file) return;
    const text = await file.text();
    setUploadForm((f) => ({ ...f, private_key: text }));
  };

  const submitUpload = async () => {
    if (!uploadForm.name.trim() || !uploadForm.private_key.trim()) {
      notify.error('Name and private key are required');
      return;
    }
    setUploading(true);
    try {
      const created = await sshCredentialsApi.createSSHCredential({
        name: uploadForm.name.trim(),
        description: `Uploaded for DPU OS access (project #${projectId})`,
        host: 'dpu-os',
        port: 22,
        username: form.default_os_username || 'ubuntu',
        auth_type: 'key',
        private_key: uploadForm.private_key,
        key_passphrase: uploadForm.key_passphrase || undefined,
      });
      queryClient.invalidateQueries({ queryKey: queryKeys.sshCredentials.all });
      setForm((f) => ({ ...f, default_os_ssh_credential_id: String(created.id) }));
      notify.success(`Uploaded SSH key and saved as "${created.name}"`, undefined, { category: 'credentials' });
      setUploadDialogOpen(false);
    } catch (err) {
      const axiosErr = err as { response?: { data?: { detail?: string; message?: string } } };
      const backendMsg = axiosErr.response?.data?.detail ?? axiosErr.response?.data?.message;
      notify.error(backendMsg ?? (err instanceof Error ? err.message : 'Upload failed'));
    } finally {
      setUploading(false);
    }
  };

  const save = async () => {
    // Validate extras: non-empty unique names, not reserved.
    const cleanedExtras: {
      name: string;
      vlan_id: number | null;
      ipv4_subnet: string | null;
      ipv6_subnet: string | null;
      start_host: number | null;
      uplink: string | null;
    }[] = [];
    const seenNames = new Set<string>();
    for (const row of extras) {
      const name = row.name.trim().toLowerCase();
      if (!name) {
        notify.error('Every extra VLAN row needs a name');
        return;
      }
      if (RESERVED_VLAN_NAMES.has(name)) {
        notify.error(`"${row.name}" is reserved — external and internal are fixed rows`);
        return;
      }
      if (seenNames.has(name)) {
        notify.error(`Duplicate VLAN interface name: ${name}`);
        return;
      }
      seenNames.add(name);
      const idStr = row.vlan_id.trim();
      const startStr = row.start_host.trim();
      cleanedExtras.push({
        name,
        vlan_id: idStr === '' ? null : Number(idStr),
        ipv4_subnet: row.ipv4_subnet.trim() || null,
        ipv6_subnet: row.ipv6_subnet.trim() || null,
        start_host: startStr === '' ? null : Number(startStr),
        uplink: row.uplink === '' ? null : row.uplink,
      });
    }

    // Validate uplink consistency client-side for a friendly error.
    const uplinks = [
      form.external_vlan_uplink,
      form.internal_vlan_uplink,
      ...extras.map((e) => e.uplink),
    ];
    const hasBond = uplinks.some((u) => u === 'bond0');
    const hasPhys = uplinks.some((u) => u === 'p0' || u === 'p1');
    if (hasBond && hasPhys) {
      notify.error(
        "Uplink ports must all be 'bond0' (LAG path) or all be 'p0'/'p1' (non-LAG path); mixing is not allowed.",
      );
      return;
    }
    // Both fixed VLANs must have an uplink — surface the same rule the
    // backend enforces, so the operator gets an inline error before the
    // round-trip to the API.
    if (!form.external_vlan_uplink || !form.internal_vlan_uplink) {
      notify.error(
        'Both External and Internal VLANs must have an uplink port assigned.',
      );
      return;
    }

    const payload: Record<string, unknown> = {
      bluefield_image_id:
        form.bluefield_image_id === '' ? null : Number(form.bluefield_image_id),
      bf_template_id:
        form.bf_template_id === '' ? null : Number(form.bf_template_id),
      high_speed_mtu: form.high_speed_mtu === '' ? null : Number(form.high_speed_mtu),
      dns_servers: form.dns_servers
        .split(',')
        .map((s) => s.trim())
        .filter((s) => s !== ''),
      ntp_servers: form.ntp_servers
        .split(',')
        .map((s) => s.trim())
        .filter((s) => s !== ''),
      default_oob_username: form.default_oob_username || null,
      default_os_username: form.default_os_username || null,
      default_os_ssh_credential_id:
        form.default_os_ssh_credential_id === ''
          ? null
          : Number(form.default_os_ssh_credential_id),
      extra_vlan_interfaces: cleanedExtras,
    };
    // Only send passwords if the user typed something; empty stays unchanged.
    if (form.default_oob_password !== '') payload.default_oob_password = form.default_oob_password;
    if (form.default_os_password !== '') payload.default_os_password = form.default_os_password;

    // Fixed VLAN rows — external + internal only.
    for (const iface of ['external', 'internal'] as const) {
      const idKey = `${iface}_vlan_id` as const;
      const v4Key = `${iface}_vlan_ipv4_subnet` as const;
      const v6Key = `${iface}_vlan_ipv6_subnet` as const;
      const startKey = `${iface}_vlan_start_host` as const;
      const uplinkKey = `${iface}_vlan_uplink` as const;
      const idStr = form[idKey].trim();
      const startStr = form[startKey].trim();
      payload[idKey] = idStr === '' ? null : Number(idStr);
      payload[v4Key] = form[v4Key].trim() || null;
      payload[v6Key] = form[v6Key].trim() || null;
      payload[startKey] = startStr === '' ? null : Number(startStr);
      payload[uplinkKey] = form[uplinkKey] === '' ? null : form[uplinkKey];
    }

    try {
      await updateMut.mutateAsync(payload);
      setForm((f) => ({ ...f, default_oob_password: '', default_os_password: '' }));
    } catch (err) {
      // Axios wraps the backend's structured error body under
      // `response.data` — surface the `detail` / `message` the
      // service-layer raised so operators see *why* the save failed
      // (e.g. "Both External and Internal VLANs must have…") instead
      // of a useless "Request failed with status code 400".
      const axiosErr = err as { response?: { data?: { detail?: string; message?: string } } };
      const backendMsg = axiosErr.response?.data?.detail ?? axiosErr.response?.data?.message;
      notify.error(backendMsg ?? (err instanceof Error ? err.message : 'Failed to save'));
    }
  };

  if (isLoading) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center py-12">
          <Loader2 className="h-6 w-6 animate-spin text-primary" />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader
        className="cursor-pointer select-none"
        onClick={() => setExpanded((v) => !v)}
        role="button"
        aria-expanded={expanded}
        aria-controls="dpu-project-defaults-body"
      >
        <div className="flex items-center gap-2">
          {expanded ? (
            <ChevronDown className="h-4 w-4" />
          ) : (
            <ChevronRight className="h-4 w-4" />
          )}
          <CardTitle className="text-lg">
            DPU Project Defaults
          </CardTitle>
        </div>
        <CardDescription>
          Applied to every DPU in this project. Per-DPU credentials and fields override these.
        </CardDescription>
      </CardHeader>
      {expanded && (
      <CardContent id="dpu-project-defaults-body" className="space-y-4">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="space-y-1">
            <Label htmlFor="bfb-image">BFB image</Label>
            <Select
              value={form.bluefield_image_id === '' ? UNSET : form.bluefield_image_id}
              onValueChange={(v) =>
                setForm({ ...form, bluefield_image_id: v === UNSET ? '' : v })
              }
              disabled={disabled}
            >
              <SelectTrigger id="bfb-image">
                <SelectValue placeholder="Select a BFB image" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={UNSET}>— none —</SelectItem>
                {(images ?? []).map((img) => (
                  <SelectItem key={img.id} value={String(img.id)}>
                    {img.doca_version ? `DOCA ${img.doca_version}` : img.image_filename ?? `#${img.id}`}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-1">
            <Label htmlFor="bf-template">bf.conf template</Label>
            <Select
              value={form.bf_template_id === '' ? UNSET : form.bf_template_id}
              onValueChange={(v) =>
                setForm({ ...form, bf_template_id: v === UNSET ? '' : v })
              }
              disabled={disabled}
            >
              <SelectTrigger id="bf-template">
                <SelectValue placeholder="Select a bf.conf template" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={UNSET}>— none —</SelectItem>
                {(bfTemplates ?? []).map((tpl) => (
                  <SelectItem key={tpl.id} value={String(tpl.id)}>
                    {tpl.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-1">
            <Label htmlFor="mtu">Uplink ports MTU</Label>
            <Input
              id="mtu"
              type="number"
              value={form.high_speed_mtu}
              onChange={(e) => setForm({ ...form, high_speed_mtu: e.target.value })}
              disabled={disabled}
            />
          </div>

          <div className="space-y-1">
            <Label htmlFor="dns">DNS servers (comma-separated)</Label>
            <Input
              id="dns"
              placeholder="1.1.1.1,8.8.8.8"
              value={form.dns_servers}
              onChange={(e) => setForm({ ...form, dns_servers: e.target.value })}
              disabled={disabled}
            />
          </div>

          <div className="space-y-1">
            <Label htmlFor="ntp">NTP servers (comma-separated)</Label>
            <Input
              id="ntp"
              placeholder="ntp.ubuntu.com"
              value={form.ntp_servers}
              onChange={(e) => setForm({ ...form, ntp_servers: e.target.value })}
              disabled={disabled}
            />
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-2">
          <div className="space-y-1">
            <Label htmlFor="oob-user">DPU BMC username</Label>
            <Input
              id="oob-user"
              placeholder="root"
              value={form.default_oob_username}
              onChange={(e) => setForm({ ...form, default_oob_username: e.target.value })}
              disabled={disabled}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="oob-pw">DPU BMC password</Label>
            <div className="relative">
              <Input
                id="oob-pw"
                type={oobPasswordRevealed ? 'text' : 'password'}
                placeholder={
                  settings?.has_default_oob_password
                    ? '(saved — reveal to view, or type to replace)'
                    : ''
                }
                value={form.default_oob_password}
                onChange={(e) => setForm({ ...form, default_oob_password: e.target.value })}
                disabled={disabled}
                className="pr-10"
              />
              {settings?.has_default_oob_password && (
                <button
                  type="button"
                  onClick={toggleOobReveal}
                  disabled={disabled}
                  aria-label={oobPasswordRevealed ? 'Hide BMC password' : 'Show BMC password'}
                  className="absolute right-2 top-1/2 -translate-y-1/2 opacity-60 hover:opacity-100"
                >
                  {oobPasswordRevealed ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              )}
            </div>
          </div>
          <div className="space-y-1">
            <Label htmlFor="os-user">DPU OS username</Label>
            <Input
              id="os-user"
              placeholder="ubuntu"
              value={form.default_os_username}
              onChange={(e) => setForm({ ...form, default_os_username: e.target.value })}
              disabled={disabled}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="os-pw">DPU OS password</Label>
            <div className="relative">
              <Input
                id="os-pw"
                type={osPasswordRevealed ? 'text' : 'password'}
                placeholder={
                  settings?.has_default_os_password
                    ? '(saved — reveal to view, or type to replace)'
                    : ''
                }
                value={form.default_os_password}
                onChange={(e) => setForm({ ...form, default_os_password: e.target.value })}
                disabled={disabled}
                className="pr-10"
              />
              {settings?.has_default_os_password && (
                <button
                  type="button"
                  onClick={toggleOsReveal}
                  disabled={disabled}
                  aria-label={osPasswordRevealed ? 'Hide OS password' : 'Show OS password'}
                  className="absolute right-2 top-1/2 -translate-y-1/2 opacity-60 hover:opacity-100"
                >
                  {osPasswordRevealed ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              )}
            </div>
          </div>
        </div>

        <div className="pt-2">
          <Label htmlFor="os-ssh-cred">DPU OS SSH key</Label>
          <div className="text-xs mb-1 text-muted-foreground">
            Public key is injected into bf.cfg at flash time so DPUs trust Forge
            immediately. Generate a new key or pick an existing one from the
            project-independent SSH credentials catalog.
          </div>
          <div className="flex items-center gap-2">
            <div className="flex-1">
              <Select
                value={
                  form.default_os_ssh_credential_id === ''
                    ? UNSET
                    : form.default_os_ssh_credential_id
                }
                onValueChange={(v) =>
                  setForm({
                    ...form,
                    default_os_ssh_credential_id: v === UNSET ? '' : v,
                  })
                }
                disabled={disabled}
              >
                <SelectTrigger id="os-ssh-cred">
                  <SelectValue placeholder="Select an SSH credential" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={UNSET}>— none —</SelectItem>
                  {(sshCredentials ?? []).map((c) => (
                    <SelectItem key={c.id} value={String(c.id)}>
                      {c.name}{' '}
                      <span className="opacity-60">
                        ({c.auth_type === 'key' ? 'key' : 'password'}, {c.username})
                      </span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <Button
              onClick={generateAndSelectSshKey}
              disabled={disabled || generatingKey}
              variant="outline"
              size="sm"
              className="gap-2"
              title="Generate a fresh Ed25519 key pair and save it as a new SSH credential"
            >
              {generatingKey ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <KeyRound className="h-4 w-4" />
              )}
              Generate new
            </Button>
            <Button
              onClick={openUploadDialog}
              disabled={disabled}
              variant="outline"
              size="sm"
              className="gap-2"
              title="Paste or upload an existing private SSH key"
            >
              <Upload className="h-4 w-4" />
              Upload
            </Button>
          </div>
        </div>

        <div className="pt-4">
          <div className="text-sm font-semibold mb-2">VLAN Interfaces</div>
          <div className="text-xs mb-2 text-muted-foreground">
            VLAN IDs apply to every DPU in the project. Use VLAN ID 0 for
            untagged. The "Uplink port" column decides where each VLAN
            attaches.
            {selectedTemplate?.uplink_mode === 'lag' && (
              <>
                {' '}The selected bf.conf template builds a LAG, so VLANs
                attach to the LACP bond (<span className="font-mono">bond0</span>);
                <span className="font-mono"> p0</span> /
                <span className="font-mono"> p1</span> are consumed to build it.
              </>
            )}
            {selectedTemplate?.uplink_mode === 'p0p1' && (
              <>
                {' '}The selected bf.conf template doesn't build a LAG, so VLANs
                attach directly to a physical uplink
                (<span className="font-mono">p0</span> / <span className="font-mono">p1</span>).
              </>
            )}
            {(!selectedTemplate || selectedTemplate.uplink_mode === 'unknown') && (
              <>
                {' '}Custom template — all uplink choices remain available.
              </>
            )}
            <br />
            <span className="font-medium">External</span> and
            <span className="font-medium"> Internal</span> are required and cannot
            be removed; other rows are editable.
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs border-collapse">
              <thead>
                <tr className="text-muted-foreground">
                  <th className="text-left font-medium pb-2 w-32">Interface</th>
                  <th className="text-left font-medium pb-2 w-24">VLAN ID</th>
                  <th className="text-left font-medium pb-2">IPv4 subnet/mask</th>
                  <th className="text-left font-medium pb-2">IPv6 subnet/mask</th>
                  <th className="text-left font-medium pb-2 w-28">Start host</th>
                  <th className="text-left font-medium pb-2 w-28">Uplink port</th>
                  <th className="w-8" />
                </tr>
              </thead>
              <tbody>
                {(['external', 'internal'] as const).map((iface) => (
                  <tr key={iface} className="align-top">
                    <td className="py-1 pr-3 capitalize font-medium">{iface}</td>
                    <td className="py-1 pr-2">
                      <Input
                        aria-label={`${iface} VLAN ID`}
                        type="number"
                        value={form[`${iface}_vlan_id`]}
                        onChange={(e) =>
                          setForm({ ...form, [`${iface}_vlan_id`]: e.target.value })
                        }
                        disabled={disabled}
                        className="h-8"
                      />
                    </td>
                    <td className="py-1 pr-2">
                      <Input
                        aria-label={`${iface} IPv4 subnet`}
                        placeholder="10.1.0.0/24"
                        value={form[`${iface}_vlan_ipv4_subnet`]}
                        onChange={(e) =>
                          setForm({ ...form, [`${iface}_vlan_ipv4_subnet`]: e.target.value })
                        }
                        disabled={disabled}
                        className="h-8 font-mono"
                      />
                    </td>
                    <td className="py-1 pr-2">
                      <Input
                        aria-label={`${iface} IPv6 subnet`}
                        placeholder="fd00:1::/64"
                        value={form[`${iface}_vlan_ipv6_subnet`]}
                        onChange={(e) =>
                          setForm({ ...form, [`${iface}_vlan_ipv6_subnet`]: e.target.value })
                        }
                        disabled={disabled}
                        className="h-8 font-mono"
                      />
                    </td>
                    <td className="py-1 pr-2">
                      <Input
                        aria-label={`${iface} start host`}
                        type="number"
                        placeholder="100"
                        value={form[`${iface}_vlan_start_host`]}
                        onChange={(e) =>
                          setForm({ ...form, [`${iface}_vlan_start_host`]: e.target.value })
                        }
                        disabled={disabled}
                        className="h-8"
                      />
                    </td>
                    <td className="py-1">
                      <Select
                        value={
                          form[`${iface}_vlan_uplink`] === ''
                            ? UPLINK_EMPTY
                            : form[`${iface}_vlan_uplink`]
                        }
                        onValueChange={(v) =>
                          setForm({
                            ...form,
                            [`${iface}_vlan_uplink`]: (v === UPLINK_EMPTY ? '' : v) as UplinkChoice,
                          })
                        }
                        disabled={disabled}
                      >
                        <SelectTrigger
                          aria-label={`${iface} uplink port`}
                          className="h-8"
                        >
                          <SelectValue placeholder="—" />
                        </SelectTrigger>
                        <SelectContent>
                          {availableUplinks.map((u) => (
                            <SelectItem key={u || UPLINK_EMPTY} value={u || UPLINK_EMPTY}>
                              {u || '—'}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </td>
                    <td />
                  </tr>
                ))}
                {extras.map((row, idx) => (
                  <tr key={`extra-${idx}`} className="align-top">
                    <td className="py-1 pr-3">
                      <Input
                        aria-label={`extra ${idx} name`}
                        placeholder="storage"
                        value={row.name}
                        onChange={(e) => {
                          const next = [...extras];
                          next[idx] = { ...next[idx], name: e.target.value };
                          setExtras(next);
                        }}
                        disabled={disabled}
                        className="h-8"
                      />
                    </td>
                    <td className="py-1 pr-2">
                      <Input
                        aria-label={`extra ${idx} VLAN ID`}
                        type="number"
                        value={row.vlan_id}
                        onChange={(e) => {
                          const next = [...extras];
                          next[idx] = { ...next[idx], vlan_id: e.target.value };
                          setExtras(next);
                        }}
                        disabled={disabled}
                        className="h-8"
                      />
                    </td>
                    <td className="py-1 pr-2">
                      <Input
                        aria-label={`extra ${idx} IPv4 subnet`}
                        placeholder="10.3.0.0/24"
                        value={row.ipv4_subnet}
                        onChange={(e) => {
                          const next = [...extras];
                          next[idx] = { ...next[idx], ipv4_subnet: e.target.value };
                          setExtras(next);
                        }}
                        disabled={disabled}
                        className="h-8 font-mono"
                      />
                    </td>
                    <td className="py-1 pr-2">
                      <Input
                        aria-label={`extra ${idx} IPv6 subnet`}
                        placeholder="fd00:3::/64"
                        value={row.ipv6_subnet}
                        onChange={(e) => {
                          const next = [...extras];
                          next[idx] = { ...next[idx], ipv6_subnet: e.target.value };
                          setExtras(next);
                        }}
                        disabled={disabled}
                        className="h-8 font-mono"
                      />
                    </td>
                    <td className="py-1 pr-2">
                      <Input
                        aria-label={`extra ${idx} start host`}
                        type="number"
                        placeholder="100"
                        value={row.start_host}
                        onChange={(e) => {
                          const next = [...extras];
                          next[idx] = { ...next[idx], start_host: e.target.value };
                          setExtras(next);
                        }}
                        disabled={disabled}
                        className="h-8"
                      />
                    </td>
                    <td className="py-1">
                      <Select
                        value={row.uplink === '' ? UPLINK_EMPTY : row.uplink}
                        onValueChange={(v) => {
                          const next = [...extras];
                          next[idx] = {
                            ...next[idx],
                            uplink: (v === UPLINK_EMPTY ? '' : v) as UplinkChoice,
                          };
                          setExtras(next);
                        }}
                        disabled={disabled}
                      >
                        <SelectTrigger
                          aria-label={`extra ${idx} uplink port`}
                          className="h-8"
                        >
                          <SelectValue placeholder="—" />
                        </SelectTrigger>
                        <SelectContent>
                          {availableUplinks.map((u) => (
                            <SelectItem key={u || UPLINK_EMPTY} value={u || UPLINK_EMPTY}>
                              {u || '—'}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </td>
                    <td className="py-1">
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="h-8 w-8 text-destructive hover:text-destructive/80"
                        onClick={() => setExtras(extras.filter((_, i) => i !== idx))}
                        disabled={disabled}
                        aria-label={`remove extra VLAN ${row.name || idx + 1}`}
                        title="Remove row"
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex justify-start mt-2 items-center gap-3">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => {
                // Default the new row's uplink to whatever the selected
                // template exposes — bond0 for LAG, p0 for non-LAG, ""
                // for custom/unknown templates so the operator picks.
                const mode = selectedTemplate?.uplink_mode;
                const defaultUplink: UplinkChoice =
                  mode === 'lag' ? 'bond0'
                  : mode === 'p0p1' ? 'p0'
                  : '';
                setExtras([
                  ...extras,
                  {
                    name: '',
                    vlan_id: '',
                    ipv4_subnet: '',
                    ipv6_subnet: '',
                    start_host: '100',
                    uplink: defaultUplink,
                  },
                ]);
              }}
              // Non-LAG path doesn't support extras — each uplink owns
              // its own OVS bridge with no trunk in between, so an
              // additional VLAN has nowhere to attach. Use the LAG
              // template if you need more.
              disabled={disabled || selectedTemplate?.uplink_mode === 'p0p1'}
              className="gap-2"
            >
              <Plus className="h-4 w-4" />
              Add VLAN interface
            </Button>
            {selectedTemplate?.uplink_mode === 'p0p1' && (
              <span className="text-xs text-muted-foreground italic">
                Non-LAG mode supports exactly one VLAN per uplink
                (external on p0, internal on p1). Switch to a LAG
                template to add more.
              </span>
            )}
          </div>
        </div>

        <div className="flex justify-end pt-2">
          <Button onClick={save} disabled={disabled || updateMut.isPending} className="gap-2">
            {updateMut.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Save className="h-4 w-4" />
            )}
            Save defaults
          </Button>
        </div>
      </CardContent>
      )}

      <Dialog
        open={uploadDialogOpen}
        onOpenChange={(open) => {
          if (!open) setUploadDialogOpen(false);
        }}
      >
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>Upload SSH private key</DialogTitle>
            <DialogDescription>
              Paste the key or upload a file. The key is encrypted at rest and
              available to all projects via the SSH credentials catalog. The
              matching public key will be injected into bf.cfg at DPU flash
              time.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <div className="space-y-1">
              <Label htmlFor="upload-name">Credential name</Label>
              <Input
                id="upload-name"
                value={uploadForm.name}
                onChange={(e) => setUploadForm({ ...uploadForm, name: e.target.value })}
              />
            </div>
            <div className="space-y-1">
              <div className="flex items-center justify-between">
                <Label htmlFor="upload-key">Private key (OpenSSH or PEM)</Label>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="gap-1 h-7"
                  onClick={() => uploadFileRef.current?.click()}
                >
                  <Upload className="h-3.5 w-3.5" />
                  From file
                </Button>
                <input
                  ref={uploadFileRef}
                  type="file"
                  className="hidden"
                  accept=".pem,.key,.pub,text/plain,*/*"
                  onChange={handleUploadFile}
                />
              </div>
              <Textarea
                id="upload-key"
                className="font-mono text-xs h-[220px]"
                placeholder="-----BEGIN OPENSSH PRIVATE KEY-----&#10;...&#10;-----END OPENSSH PRIVATE KEY-----"
                value={uploadForm.private_key}
                onChange={(e) =>
                  setUploadForm({ ...uploadForm, private_key: e.target.value })
                }
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="upload-passphrase">Passphrase (optional)</Label>
              <Input
                id="upload-passphrase"
                type="password"
                value={uploadForm.key_passphrase}
                onChange={(e) =>
                  setUploadForm({ ...uploadForm, key_passphrase: e.target.value })
                }
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setUploadDialogOpen(false)} disabled={uploading}>
              Cancel
            </Button>
            <Button onClick={submitUpload} disabled={uploading}>
              {uploading && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
              Save key
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
