/**
 * Admin table for bf.conf templates.
 *
 * Admins can view / add / edit / upload / download / delete templates.
 * Each project's DPU settings can then pick one as the default at DPU
 * flash time (Phase 4). Deletion of a template referenced by a project
 * is rejected by the backend — the operator must unassign first.
 */
import { useRef, useState } from 'react';
import {
  useBfConfTemplates,
  useBfConfTemplate,
  useCreateBfConfTemplate,
  useUpdateBfConfTemplate,
  useDeleteBfConfTemplate,
} from '@/hooks/useBfConfTemplates';
import type {
  BfConfTemplate,
  BfConfTemplateSummary,
} from '@/lib/api/bf-conf-templates';
import { SectionCard } from '@/components/ui/section-card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
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
import { Download, Eye, Loader2, Pencil, Plus, Trash2, Upload } from 'lucide-react';
import { notify } from '@/lib/notify';

type FormState = {
  name: string;
  description: string;
  content: string;
  matching_bnk_version: string;
};

const EMPTY_FORM: FormState = {
  name: '',
  description: '',
  content: '',
  matching_bnk_version: '',
};

export function BfConfTemplates() {
  const { data: templates, isLoading, error } = useBfConfTemplates();
  const createMut = useCreateBfConfTemplate();
  const updateMut = useUpdateBfConfTemplate();
  const deleteMut = useDeleteBfConfTemplate();

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [viewingId, setViewingId] = useState<number | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<BfConfTemplateSummary | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const { data: editingTemplate } = useBfConfTemplate(editingId);
  const { data: viewingTemplate } = useBfConfTemplate(viewingId);

  // When we fetch the full template in edit mode, seed the form with it.
  // Triggered once per editingTemplate change to avoid clobbering local edits.
  const lastSeededRef = useRef<number | null>(null);
  if (editingTemplate && lastSeededRef.current !== editingTemplate.id) {
    lastSeededRef.current = editingTemplate.id;
    setForm({
      name: editingTemplate.name,
      description: editingTemplate.description ?? '',
      content: editingTemplate.content,
      matching_bnk_version: editingTemplate.matching_bnk_version ?? '',
    });
  }

  const openAdd = () => {
    setEditingId(null);
    lastSeededRef.current = null;
    setForm(EMPTY_FORM);
    setDialogOpen(true);
  };

  const openEdit = (id: number) => {
    setEditingId(id);
    lastSeededRef.current = null;
    setForm(EMPTY_FORM);
    setDialogOpen(true);
  };

  const closeDialog = () => {
    setDialogOpen(false);
    setEditingId(null);
    lastSeededRef.current = null;
    setForm(EMPTY_FORM);
  };

  const submit = async () => {
    const basePayload = {
      name: form.name.trim(),
      description: form.description.trim() || null,
      content: form.content,
      matching_bnk_version: form.matching_bnk_version.trim() || null,
    };
    try {
      if (editingId == null) {
        await createMut.mutateAsync(basePayload);
      } else {
        await updateMut.mutateAsync({ id: editingId, data: basePayload });
      }
      closeDialog();
    } catch (err) {
      const axiosErr = err as { response?: { data?: { detail?: string; message?: string } } };
      const backendMsg = axiosErr.response?.data?.detail ?? axiosErr.response?.data?.message;
      notify.error(backendMsg ?? (err instanceof Error ? err.message : 'Save failed'));
    }
  };

  const handleUpload = async (ev: React.ChangeEvent<HTMLInputElement>) => {
    const file = ev.target.files?.[0];
    ev.target.value = '';
    if (!file) return;
    const content = await file.text();
    setForm((f) => ({
      ...f,
      content,
      // Pre-fill the name from the filename stem if the name is still empty.
      name: f.name || file.name.replace(/\.(conf|cfg|bf)$/i, ''),
    }));
  };

  const handleDownload = (t: BfConfTemplate) => {
    const blob = new Blob([t.content], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${t.name.replace(/[^a-z0-9._-]+/gi, '_')}.conf`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };

  const saving = createMut.isPending || updateMut.isPending;
  const formValid = form.name.trim() !== '' && form.content.trim() !== '';

  return (
    <SectionCard>
      <div className="flex flex-row items-start justify-between gap-4 mb-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            bf.conf templates
          </p>
          <p className="text-sm text-muted-foreground mt-1">
            Jinja2 templates rendered per-DPU at flash time. The Project DPU
            Settings dropdown picks from this catalog.
          </p>
        </div>
        <Button onClick={openAdd} size="sm" className="gap-2">
          <Plus className="h-4 w-4" />
          Add Template
        </Button>
      </div>

      <div>
        {isLoading ? (
          <div className="flex justify-center p-8">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : error ? (
          <div className="text-destructive py-4">Failed to load bf.conf templates.</div>
        ) : !templates || templates.length === 0 ? (
          <div className="py-6 text-center text-muted-foreground">
            No templates yet. Click “Add Template” to register one.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-muted-foreground">
                  <th className="text-left font-medium pb-2">Name</th>
                  <th className="text-left font-medium pb-2">Description</th>
                  <th className="text-left font-medium pb-2 w-36">Matching BNK version</th>
                  <th className="text-right font-medium pb-2 w-40" />
                </tr>
              </thead>
              <tbody>
                {templates.map((t) => (
                  <tr
                    key={t.id}
                    className="border-t border-border"
                  >
                    <td className="py-2 font-mono">{t.name}</td>
                    <td className="py-2">{t.description ?? ''}</td>
                    <td className="py-2 font-mono text-xs">
                      {t.matching_bnk_version ?? (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </td>
                    <td className="py-2 text-right whitespace-nowrap">
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => setViewingId(t.id)}
                        aria-label={`View ${t.name}`}
                      >
                        <Eye className="h-4 w-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => openEdit(t.id)}
                        aria-label={`Edit ${t.name}`}
                      >
                        <Pencil className="h-4 w-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => setConfirmDelete(t)}
                        aria-label={`Delete ${t.name}`}
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

      {/* Create / edit dialog */}
      <Dialog
        open={dialogOpen}
        onOpenChange={(open) => {
          if (!open) closeDialog();
          else setDialogOpen(open);
        }}
      >
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>
              {editingId == null ? 'Add bf.conf template' : 'Edit bf.conf template'}
            </DialogTitle>
            <DialogDescription>
              Paste or upload the Jinja2 template. Placeholders such as{' '}
              <span className="font-mono">{'{{ DPU_UBUNTU_PASSWORD_HASH }}'}</span> and{' '}
              <span className="font-mono">{'{{ bfb_hostname }}'}</span> are resolved
              per-DPU at flash time.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label htmlFor="tpl-name">Name</Label>
                <Input
                  id="tpl-name"
                  placeholder="e.g. F5 RCP RA with LACP"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="tpl-version">Matching BNK version (optional)</Label>
                <Input
                  id="tpl-version"
                  placeholder="e.g. 1.4.x"
                  value={form.matching_bnk_version}
                  onChange={(e) =>
                    setForm({ ...form, matching_bnk_version: e.target.value })
                  }
                />
              </div>
            </div>
            <div className="space-y-1">
              <Label htmlFor="tpl-desc">Description</Label>
              <Input
                id="tpl-desc"
                placeholder="Short human-readable description"
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
              />
            </div>
            <div className="space-y-1">
              <div className="flex items-center justify-between">
                <Label htmlFor="tpl-content">Template content</Label>
                <Button
                  variant="outline"
                  size="sm"
                  className="gap-1 h-7"
                  onClick={() => fileInputRef.current?.click()}
                  type="button"
                >
                  <Upload className="h-3.5 w-3.5" />
                  Upload file
                </Button>
                <input
                  ref={fileInputRef}
                  type="file"
                  className="hidden"
                  accept=".conf,.cfg,.bf,text/plain"
                  onChange={handleUpload}
                />
              </div>
              <Textarea
                id="tpl-content"
                className="font-mono text-xs h-[320px]"
                placeholder='UPDATE_DPU_OS="yes"&#10;ubuntu_PASSWORD=&#39;{{ DPU_UBUNTU_PASSWORD_HASH }}&#39;&#10;...'
                value={form.content}
                onChange={(e) => setForm({ ...form, content: e.target.value })}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={closeDialog} disabled={saving}>
              Cancel
            </Button>
            <Button onClick={submit} disabled={!formValid || saving}>
              {saving && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
              {editingId == null ? 'Add' : 'Save'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Read-only view dialog */}
      <Dialog
        open={viewingId !== null}
        onOpenChange={(open) => {
          if (!open) setViewingId(null);
        }}
      >
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>{viewingTemplate?.name ?? 'Template'}</DialogTitle>
            <DialogDescription>
              {viewingTemplate?.description ?? 'bf.conf template content'}
            </DialogDescription>
          </DialogHeader>
          {viewingTemplate ? (
            <pre className="font-mono text-xs whitespace-pre-wrap max-h-[60vh] overflow-auto border border-border rounded p-3 bg-muted/30">
              {viewingTemplate.content}
            </pre>
          ) : (
            <div className="flex justify-center p-8">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          )}
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => viewingTemplate && handleDownload(viewingTemplate)}
              disabled={!viewingTemplate}
              className="gap-2"
            >
              <Download className="h-4 w-4" />
              Download
            </Button>
            <Button onClick={() => setViewingId(null)}>Close</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog
        open={confirmDelete !== null}
        onOpenChange={(open) => {
          if (!open) setConfirmDelete(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete bf.conf template?</AlertDialogTitle>
            <AlertDialogDescription>
              {confirmDelete ? confirmDelete.name : ''} will be removed from the
              catalog. Projects that have it selected must pick a different
              template first.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={async () => {
                if (!confirmDelete) return;
                try {
                  await deleteMut.mutateAsync(confirmDelete.id);
                  notify.success('Template deleted', undefined, { category: 'system' });
                } catch (err) {
                  const axiosErr = err as {
                    response?: { data?: { detail?: string; message?: string } };
                  };
                  const backendMsg =
                    axiosErr.response?.data?.detail ?? axiosErr.response?.data?.message;
                  notify.error(
                    backendMsg ?? (err instanceof Error ? err.message : 'Delete failed'),
                  );
                } finally {
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
