/**
 * Admin table for DOCA Releases — unified BFB + DOCA catalog.
 *
 * Each row pairs a BFB bootstream image with its DOCA release metadata.
 * The standalone DOCA catalog has been merged into this single view.
 */
import { useState } from 'react';
import {
  useBluefieldImages,
  useCreateBluefieldImage,
  useUpdateBluefieldImage,
  useDeleteBluefieldImage,
} from '@/hooks/useBluefieldImages';
import type { BluefieldImage } from '@/lib/api/bluefield-images';
import { SectionCard } from '@/components/ui/section-card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
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
import { Loader2, Pencil, Plus, Star, Trash2 } from 'lucide-react';

interface FormState {
  image_filename: string;
  base_url: string;
  doca_version: string;
  doca_build: string;
  doca_package: string;
  host_os: string;
  host_os_version: string;
  host_arch: string;
  doca_host_url: string;
  is_default: boolean;
  notes: string;
}

/** Known DOCA releases — selecting a version auto-fills everything. */
interface DocaReleaseMeta {
  build: string;
  bfb_filename: string;
  bfb_base: string;  // base URL without OS suffix — OS version is appended
  package: string;
}

const KNOWN_RELEASES: Record<string, DocaReleaseMeta> = {
  '2.9.2': {
    build: '012101-24.10',
    bfb_filename: 'bf-bundle-2.9.2-32_25.02_ubuntu-22.04_prod.bfb',
    bfb_base: 'https://content.mellanox.com/BlueField/BFBs/',
    package: 'doca-all',
  },
  '3.2.1': {
    build: '044000-25.10',
    bfb_filename: 'bf-bundle-3.2.1_ubuntu-24.04_prod.bfb',
    bfb_base: 'https://content.mellanox.com/BlueField/BFBs/',
    package: 'doca-all',
  },
  '3.2.2': {
    build: '035000-25.10',
    bfb_filename: 'bf-bundle-3.2.2_ubuntu-24.04_prod.bfb',
    bfb_base: 'https://content.mellanox.com/BlueField/BFBs/',
    package: 'doca-all',
  },
};

const KNOWN_VERSIONS = Object.keys(KNOWN_RELEASES);

const EMPTY_FORM: FormState = {
  image_filename: '',
  base_url: '',
  doca_version: '',
  doca_build: '',
  doca_package: 'doca-all',
  host_os: 'ubuntu',
  host_os_version: '22.04',
  host_arch: 'arm64',
  doca_host_url: '',
  is_default: false,
  notes: '',
};

/** OS versions provided by NVIDIA for DOCA host packages. */
const OS_VERSIONS: Record<string, string[]> = {
  ubuntu: ['22.04', '24.04', '20.04'],
  debian: ['12', '11'],
  rhel: ['9.4', '9.3', '9.2', '8.10', '8.9'],
  rocky: ['9.4', '9.3', '9.2', '8.10', '8.9'],
  centos: ['9', '8'],
  almalinux: ['9.4', '9.3', '8.10'],
};

/** Map deb arch names (ubuntu/debian) vs rpm arch names (rhel/rocky/centos/alma). */
const DEB_DISTROS = new Set(['ubuntu', 'debian']);

/** Build a suggested DOCA host package URL from form values. */
function suggestDocaHostUrl(f: FormState): string {
  if (!f.doca_version || !f.host_os || !f.host_os_version || !f.host_arch || !f.doca_build) return '';
  const ver = f.doca_version;
  const build = f.doca_build;
  const osVer = f.host_os_version.replace(/\./g, '');
  const isDeb = DEB_DISTROS.has(f.host_os);
  if (isDeb) {
    return `https://www.mellanox.com/downloads/DOCA/DOCA_v${ver}/host/doca-host_${ver}-${build}-${f.host_os}${osVer}_${f.host_arch}.deb`;
  }
  const rpmArch = f.host_arch === 'arm64' ? 'aarch64' : f.host_arch === 'amd64' ? 'x86_64' : f.host_arch;
  return `https://www.mellanox.com/downloads/DOCA/DOCA_v${ver}/host/doca-host-${ver}-${build}_${f.host_os}${osVer}.${rpmArch}.rpm`;
}

/** Build a suggested BFB base URL from OS selection. */
function suggestBaseUrl(bfbBase: string, os: string, osVer: string): string {
  if (!os || !osVer) return bfbBase || 'https://content.mellanox.com/BlueField/BFBs/';
  const cap = os.charAt(0).toUpperCase() + os.slice(1);
  const base = bfbBase || 'https://content.mellanox.com/BlueField/BFBs/';
  return `${base.replace(/\/$/, '')}/${cap}${osVer}`;
}

/** Apply all known release defaults to the form when a DOCA version is selected. */
function applyRelease(ver: string, prev: FormState): FormState {
  const meta = KNOWN_RELEASES[ver];
  if (!meta) {
    // Unknown version — just set the version, keep rest
    return { ...prev, doca_version: ver };
  }
  const updated: FormState = {
    ...prev,
    doca_version: ver,
    doca_build: meta.build,
    doca_package: meta.package,
    image_filename: meta.bfb_filename,
  };
  updated.base_url = suggestBaseUrl(meta.bfb_base, updated.host_os, updated.host_os_version);
  updated.doca_host_url = suggestDocaHostUrl(updated);
  return updated;
}

export function BluefieldImages() {
  const { data: images, isLoading, error } = useBluefieldImages();
  const createMut = useCreateBluefieldImage();
  const updateMut = useUpdateBluefieldImage();
  const deleteMut = useDeleteBluefieldImage();

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [confirmDelete, setConfirmDelete] = useState<BluefieldImage | null>(null);

  const openAdd = () => {
    setEditingId(null);
    setForm(EMPTY_FORM);
    setSubmitError(null);
    setDialogOpen(true);
  };

  const openEdit = (img: BluefieldImage) => {
    setEditingId(img.id);
    setSubmitError(null);
    setForm({
      image_filename: img.image_filename ?? '',
      base_url: img.base_url ?? '',
      doca_version: img.doca_version ?? '',
      doca_build: '',  // Not stored in DB — admin re-enters if regenerating URL
      doca_package: img.doca_package ?? '',
      host_os: img.host_os ?? '',
      host_os_version: img.host_os_version ?? '',
      host_arch: img.host_arch ?? '',
      doca_host_url: img.doca_host_url ?? '',
      is_default: img.is_default,
      notes: img.notes ?? '',
    });
    setDialogOpen(true);
  };

  const [submitError, setSubmitError] = useState<string | null>(null);

  const submit = async () => {
    setSubmitError(null);
    const payload = {
      image_filename: form.image_filename.trim() || null,
      base_url: form.base_url.trim() || null,
      doca_version: form.doca_version.trim(),
      doca_package: form.doca_package.trim() || null,
      host_os: form.host_os,
      host_os_version: form.host_os_version || null,
      host_arch: form.host_arch,
      doca_host_url: form.doca_host_url.trim() || null,
      is_default: form.is_default,
      notes: form.notes.trim() || null,
    };
    try {
      if (editingId == null) {
        await createMut.mutateAsync(payload);
      } else {
        await updateMut.mutateAsync({ id: editingId, data: payload });
      }
      setDialogOpen(false);
    } catch (err: unknown) {
      // Extract backend error detail if available (axios wraps it)
      const axiosErr = err as { response?: { data?: { error?: { message?: string } } } };
      const backendMsg = axiosErr?.response?.data?.error?.message;
      const msg = backendMsg || (err instanceof Error ? err.message : 'Failed to save');
      setSubmitError(msg);
    }
  };

  const handleSetDefault = async (img: BluefieldImage) => {
    if (img.is_default) return;
    await updateMut.mutateAsync({ id: img.id, data: { is_default: true } });
  };

  const saving = createMut.isPending || updateMut.isPending;
  const formValid = form.doca_version.trim() !== '' && form.host_os !== '' && form.host_arch !== '';

  return (
    <SectionCard>
      <div className="flex flex-row items-start justify-between gap-4 mb-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            DOCA releases
          </p>
          <p className="text-sm text-muted-foreground mt-1">
            Paired BFB bootstream images and DOCA packages for DPU provisioning and bare-metal blueprints.
          </p>
        </div>
        <Button onClick={openAdd} size="sm" className="gap-2">
          <Plus className="h-4 w-4" />
          Add Release
        </Button>
      </div>

      <div>
        {isLoading ? (
          <div className="flex justify-center p-8">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : error ? (
          <div className="text-destructive py-4">Failed to load DOCA releases.</div>
        ) : !images || images.length === 0 ? (
          <div className="py-6 text-center text-muted-foreground">
            No releases yet. Click &quot;Add Release&quot; to register one.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-muted-foreground">
                  <th className="text-left font-medium pb-2">DOCA Version</th>
                  <th className="text-left font-medium pb-2">BFB Image</th>
                  <th className="text-left font-medium pb-2">BFB URL</th>
                  <th className="text-left font-medium pb-2">DOCA Package</th>
                  <th className="text-left font-medium pb-2">Host OS / Version</th>
                  <th className="text-left font-medium pb-2">Architecture</th>
                  <th className="text-left font-medium pb-2">Host Package URL</th>
                  <th className="text-left font-medium pb-2">Default</th>
                  <th className="text-left font-medium pb-2">Notes</th>
                  <th className="text-right font-medium pb-2 w-36">Actions</th>
                </tr>
              </thead>
              <tbody>
                {images.map((img) => (
                  <tr
                    key={img.id}
                    className="border-t border-border"
                  >
                    <td className="py-2 font-mono font-semibold">
                      {img.doca_version ?? <span className="text-muted-foreground italic">—</span>}
                    </td>
                    <td className="py-2 font-mono text-xs max-w-[200px] truncate" title={img.image_filename ?? undefined}>
                      {img.image_filename ?? <span className="text-muted-foreground italic">—</span>}
                    </td>
                    <td className="py-2 font-mono text-xs max-w-[200px] truncate text-primary" title={img.base_url && img.image_filename ? `${img.base_url.replace(/\/$/, '')}/${img.image_filename}` : (img.base_url ?? undefined)}>
                      {img.base_url && img.image_filename ? `${img.base_url.replace(/\/$/, '')}/${img.image_filename}` : (img.base_url ?? <span className="text-muted-foreground italic">—</span>)}
                    </td>
                    <td className="py-2 font-mono text-xs">
                      {img.doca_package ?? <span className="text-muted-foreground">—</span>}
                    </td>
                    <td className="py-2 text-xs">
                      {img.host_os ? (
                        <>
                          {img.host_os}
                          {img.host_os_version && <span className="text-muted-foreground ml-1">{img.host_os_version}</span>}
                        </>
                      ) : <span className="text-muted-foreground italic">—</span>}
                    </td>
                    <td className="py-2 text-xs">
                      {img.host_arch ?? <span className="text-muted-foreground italic">—</span>}
                    </td>
                    <td className="py-2 font-mono text-xs max-w-[200px] truncate text-primary" title={img.doca_host_url ?? undefined}>
                      {img.doca_host_url ?? <span className="text-muted-foreground">—</span>}
                    </td>
                    <td className="py-2">
                      {img.is_default ? (
                        <Badge variant="default" className="text-xs">
                          Default
                        </Badge>
                      ) : (
                        <Button
                          variant="ghost"
                          size="sm"
                          className="text-xs h-6 px-2"
                          onClick={() => handleSetDefault(img)}
                          disabled={updateMut.isPending}
                        >
                          <Star className="h-3 w-3 mr-1" />
                          Set default
                        </Button>
                      )}
                    </td>
                    <td className="py-2 text-xs max-w-[120px] truncate" title={img.notes ?? undefined}>
                      {img.notes ?? ''}
                    </td>
                    <td className="py-2 text-right">
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => openEdit(img)}
                        aria-label={`Edit ${img.doca_version ?? img.image_filename}`}
                      >
                        <Pencil className="h-4 w-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => setConfirmDelete(img)}
                        aria-label={`Delete ${img.doca_version ?? img.image_filename}`}
                      >
                        <Trash2 className="h-4 w-4 text-destructive" />
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Add / Edit dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{editingId == null ? 'Add DOCA Release' : 'Edit DOCA Release'}</DialogTitle>
            <DialogDescription>
              Pair a BFB bootstream image with its DOCA release metadata.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-5 py-2">
            {/* --- Step 1: DOCA Release --- */}
            <div>
              <h4 className="text-xs font-semibold uppercase tracking-wide mb-3 text-muted-foreground">
                1. Select DOCA Release
              </h4>
              <div className="space-y-3">
                <div className="grid grid-cols-3 gap-3">
                  <div className="space-y-1">
                    <Label htmlFor="doca_version">DOCA version *</Label>
                    <Select
                      value={KNOWN_VERSIONS.includes(form.doca_version) ? form.doca_version : '__custom__'}
                      onValueChange={(val) => {
                        if (val === '__custom__') {
                          setForm({ ...form, doca_version: '', doca_build: '', image_filename: '' });
                        } else {
                          setForm(applyRelease(val, form));
                        }
                      }}
                    >
                      <SelectTrigger id="doca_version">
                        <SelectValue placeholder="Select version…" />
                      </SelectTrigger>
                      <SelectContent>
                        {KNOWN_VERSIONS.map((ver) => (
                          <SelectItem key={ver} value={ver}>DOCA {ver}</SelectItem>
                        ))}
                        <SelectItem value="__custom__">Custom version…</SelectItem>
                      </SelectContent>
                    </Select>
                    {!KNOWN_VERSIONS.includes(form.doca_version) && form.doca_version === '' && (
                      <Input
                        placeholder="e.g. 3.3.0"
                        className="mt-1"
                        onChange={(e) => setForm(applyRelease(e.target.value, form))}
                      />
                    )}
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor="doca_build">Build number</Label>
                    <Input
                      id="doca_build"
                      value={form.doca_build}
                      onChange={(e) => {
                        const updated = { ...form, doca_build: e.target.value };
                        updated.doca_host_url = suggestDocaHostUrl(updated);
                        setForm(updated);
                      }}
                    />
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor="doca_package">Package</Label>
                    <Input
                      id="doca_package"
                      value={form.doca_package}
                      onChange={(e) => setForm({ ...form, doca_package: e.target.value })}
                    />
                  </div>
                </div>

                {/* BFB details — auto-filled from release, editable */}
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1">
                    <Label htmlFor="image_filename" className="text-xs">BFB filename</Label>
                    <Input
                      id="image_filename"
                      className="text-xs"
                      value={form.image_filename}
                      onChange={(e) => setForm({ ...form, image_filename: e.target.value })}
                    />
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor="base_url" className="text-xs">BFB base URL</Label>
                    <Input
                      id="base_url"
                      className="text-xs"
                      value={form.base_url}
                      onChange={(e) => setForm({ ...form, base_url: e.target.value })}
                    />
                  </div>
                </div>
              </div>
            </div>

            {/* --- Step 2: Host Variant --- */}
            <div>
              <h4 className="text-xs font-semibold uppercase tracking-wide mb-3 text-muted-foreground">
                2. Host Variant
              </h4>
              <div className="space-y-3">
                <div className="grid grid-cols-3 gap-3">
                  <div className="space-y-1">
                    <Label htmlFor="host_os">Host OS *</Label>
                    <Select
                      value={form.host_os || undefined}
                      onValueChange={(val) => {
                        const meta = KNOWN_RELEASES[form.doca_version];
                        const updated = { ...form, host_os: val, host_os_version: '' };
                        updated.base_url = suggestBaseUrl(meta?.bfb_base || '', val, '');
                        updated.doca_host_url = suggestDocaHostUrl(updated);
                        setForm(updated);
                      }}
                    >
                      <SelectTrigger id="host_os">
                        <SelectValue placeholder="Select OS…" />
                      </SelectTrigger>
                      <SelectContent>
                        {Object.keys(OS_VERSIONS).map((os) => (
                          <SelectItem key={os} value={os}>{os}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor="host_os_version">OS Version</Label>
                    <Select
                      value={form.host_os_version || undefined}
                      onValueChange={(val) => {
                        const meta = KNOWN_RELEASES[form.doca_version];
                        const updated = { ...form, host_os_version: val };
                        updated.base_url = suggestBaseUrl(meta?.bfb_base || '', form.host_os, val);
                        updated.doca_host_url = suggestDocaHostUrl(updated);
                        setForm(updated);
                      }}
                      disabled={!form.host_os}
                    >
                      <SelectTrigger id="host_os_version">
                        <SelectValue placeholder="Select version…" />
                      </SelectTrigger>
                      <SelectContent>
                        {(OS_VERSIONS[form.host_os] ?? []).map((ver) => (
                          <SelectItem key={ver} value={ver}>{ver}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor="host_arch">Architecture *</Label>
                    <Select
                      value={form.host_arch || undefined}
                      onValueChange={(val) => {
                        const updated = { ...form, host_arch: val };
                        updated.doca_host_url = suggestDocaHostUrl(updated);
                        setForm(updated);
                      }}
                    >
                      <SelectTrigger id="host_arch">
                        <SelectValue placeholder="Select arch…" />
                      </SelectTrigger>
                      <SelectContent>
                        {['arm64', 'amd64', 'aarch64', 'x86_64'].map((arch) => (
                          <SelectItem key={arch} value={arch}>{arch}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </div>
                <div className="space-y-1">
                  <Label htmlFor="doca_host_url">Host package URL</Label>
                  <Input
                    id="doca_host_url"
                    className="text-xs font-mono"
                    value={form.doca_host_url}
                    onChange={(e) => setForm({ ...form, doca_host_url: e.target.value })}
                  />
                  <p className="text-xs text-muted-foreground">
                    Auto-generated. Edit directly if the URL differs.
                  </p>
                </div>
              </div>
            </div>

            {/* --- Metadata --- */}
            <div className="space-y-3">
              <div className="space-y-1">
                <Label htmlFor="notes">Notes (optional)</Label>
                <Input
                  id="notes"
                  placeholder="e.g. Latest 24.04 GA release"
                  value={form.notes}
                  onChange={(e) => setForm({ ...form, notes: e.target.value })}
                />
              </div>
              <div className="flex items-center gap-2">
                <input
                  type="checkbox"
                  id="is_default"
                  checked={form.is_default}
                  onChange={(e) => setForm({ ...form, is_default: e.target.checked })}
                  className="rounded border-border"
                />
                <Label htmlFor="is_default" className="cursor-pointer flex items-center gap-1">
                  <Star className="h-3 w-3" />
                  Set as default
                </Label>
              </div>
            </div>
          </div>
          {submitError && (
            <div className="text-sm text-destructive px-6 pb-2">{submitError}</div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)} disabled={saving}>
              Cancel
            </Button>
            <Button onClick={submit} disabled={!formValid || saving}>
              {saving && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
              {editingId == null ? 'Add' : 'Save'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete confirmation */}
      <AlertDialog
        open={confirmDelete !== null}
        onOpenChange={(open) => {
          if (!open) setConfirmDelete(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete DOCA release?</AlertDialogTitle>
            <AlertDialogDescription>
              {confirmDelete
                ? `${confirmDelete.doca_version ?? ''} ${confirmDelete.image_filename}`.trim()
                : ''}{' '}
              will be removed from the catalog. Existing projects that use it will need a new selection.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={async () => {
                if (confirmDelete) {
                  await deleteMut.mutateAsync(confirmDelete.id);
                  setConfirmDelete(null);
                }
              }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </SectionCard>
  );
}
