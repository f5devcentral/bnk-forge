import { useMemo, useState } from 'react';
import { Cloud, Loader2 } from 'lucide-react';
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { useImportRegistryTFC } from '@/hooks/useRegistry';
import { credentialsApi } from '@/lib/api/credentials';
import { useQuery } from '@tanstack/react-query';
import { isAxiosError } from 'axios';
import { notify } from '@/lib/notify';
import type { CloudCredentialTemplate, ModuleLibrary } from '@/types';

interface TFCImportDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onImported?: (module: ModuleLibrary) => void;
}

export function TFCImportDialog({ open, onOpenChange, onImported }: TFCImportDialogProps) {
  const [credentialId, setCredentialId] = useState<string>('');
  const [namespace, setNamespace] = useState('');
  const [name, setName] = useState('');
  const [provider, setProvider] = useState('');
  const [version, setVersion] = useState('');

  const { data: templates = [] } = useQuery({
    queryKey: ['credential-templates', 'all'],
    queryFn: () => credentialsApi.listCredentialTemplates(),
    enabled: open,
  });

  const tfcTemplates = useMemo(
    () => (templates as CloudCredentialTemplate[]).filter((t) => Boolean(t.has_tfc_api_token)),
    [templates],
  );

  const selectedTemplate = useMemo(
    () => tfcTemplates.find((t) => String(t.id) === credentialId),
    [tfcTemplates, credentialId],
  );

  const importMutation = useImportRegistryTFC();

  const reset = () => {
    setCredentialId('');
    setNamespace('');
    setName('');
    setProvider('');
    setVersion('');
  };

  const handleImport = async () => {
    if (!selectedTemplate || !namespace || !name || !provider || !version) return;
    const hostname = selectedTemplate.tfc_hostname || 'app.terraform.io';
    try {
      const result = await importMutation.mutateAsync({
        hostname,
        namespace: namespace.trim(),
        name: name.trim(),
        provider: provider.trim(),
        version: version.trim(),
        credential_template_id: selectedTemplate.id,
      });
      notify.success(`Imported ${namespace}/${name}/${provider}@${version} from ${hostname}`, undefined, { category: 'system' });
      reset();
      onOpenChange(false);
      onImported?.(result.module);
    } catch (err: unknown) {
      const msg = isAxiosError(err)
        ? err.response?.data?.detail || err.message
        : err instanceof Error ? err.message : 'Unknown error';
      notify.error(`Failed to import from TFC: ${msg}`);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => { onOpenChange(o); if (!o) reset(); }}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Cloud className="h-5 w-5" />
            Import from Terraform Cloud
          </DialogTitle>
          <DialogDescription>
            Import a private module from Terraform Cloud or Terraform Enterprise using a stored API token.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          <div>
            <Label>Credential template *</Label>
            {tfcTemplates.length === 0 ? (
              <p className="text-sm text-muted-foreground mt-2">
                No credential templates with a TFC API token are configured. Create one in Settings → Credentials.
              </p>
            ) : (
              <Select value={credentialId} onValueChange={setCredentialId}>
                <SelectTrigger>
                  <SelectValue placeholder="Select a template" />
                </SelectTrigger>
                <SelectContent>
                  {tfcTemplates.map((t) => (
                    <SelectItem key={t.id} value={String(t.id)}>
                      {t.name} {t.tfc_hostname ? `(${t.tfc_hostname})` : '(app.terraform.io)'}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label htmlFor="tfc-namespace">Organization / namespace *</Label>
              <Input id="tfc-namespace" value={namespace} onChange={(e) => setNamespace(e.target.value)} />
            </div>
            <div>
              <Label htmlFor="tfc-name">Module name *</Label>
              <Input id="tfc-name" value={name} onChange={(e) => setName(e.target.value)} />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label htmlFor="tfc-provider">Provider *</Label>
              <Input id="tfc-provider" placeholder="aws" value={provider} onChange={(e) => setProvider(e.target.value)} />
            </div>
            <div>
              <Label htmlFor="tfc-version">Version *</Label>
              <Input id="tfc-version" placeholder="1.2.3" value={version} onChange={(e) => setVersion(e.target.value)} />
            </div>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button
            onClick={handleImport}
            disabled={
              !selectedTemplate || !namespace || !name || !provider || !version || importMutation.isPending
            }
          >
            {importMutation.isPending ? (
              <><Loader2 className="h-4 w-4 mr-2 animate-spin" /> Importing…</>
            ) : (
              'Import Module'
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
