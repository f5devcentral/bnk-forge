/**
 * Container Registries access-method panel (D-020).
 *
 * Mirrors SSHCredentials: a SectionCard with a list table and create / edit /
 * delete / test dialogs. Container registries are a first-class OCI registry
 * access method, orthogonal to cloud provider.
 *
 * Two type families drive the form:
 *   * Standalone (ghcr | quay) — username + token.
 *   * Standalone (far)         — F5 Artifact Registry; ingests a base64 *.tgz
 *                                auth key or raw service-account JSON.
 *   * Derived (ecr|acr|icr|gar) — reference a cloud credential template; the
 *                                short-lived registry token is exchanged at
 *                                pull time.
 */
import { useState } from 'react';
import { SectionCard } from '@/components/ui/section-card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
  Boxes,
  Container,
  Edit,
  ExternalLink,
  HelpCircle,
  Info,
  Loader2,
  MoreVertical,
  Plus,
  TestTube2,
  Trash2,
  Upload,
} from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { cn } from '@/lib/utils';
import { notify } from '@/lib/notify';
import { api } from '@/lib/api';
import type { CloudCredentialTemplate } from '@/types';
import {
  useContainerRegistries,
  useCreateContainerRegistry,
  useDeleteContainerRegistry,
  useTestContainerRegistry,
  useUpdateContainerRegistry,
  type ContainerRegistry,
} from '@/hooks/useContainerRegistries';

// ============================================================================
// Registry type metadata
// ============================================================================

type RegistryType =
  | 'ghcr'
  | 'quay'
  | 'dockerhub'
  | 'far'
  | 'artifactory'
  | 'harbor'
  | 'distribution'
  | 'oci'
  | 'ecr'
  | 'icr';

const STANDALONE_TYPES: RegistryType[] = [
  'ghcr',
  'quay',
  'dockerhub',
  'far',
  'artifactory',
  'harbor',
  'distribution',
  'oci',
];
// Only derived types with a real cloud token exchange (ecr → AWS, icr → IBM IAM).
const DERIVED_TYPES: RegistryType[] = ['ecr', 'icr'];

const TYPE_LABELS: Record<RegistryType, string> = {
  ghcr: 'GitHub Container Registry',
  quay: 'Quay',
  dockerhub: 'Docker Hub',
  far: 'F5 Artifact Registry',
  artifactory: 'JFrog Artifactory',
  harbor: 'Harbor',
  distribution: 'Docker Distribution',
  oci: 'OCI Compliant Registry',
  ecr: 'Amazon ECR',
  icr: 'IBM Cloud ICR',
};

// Default Registry Host per type, applied when the type is selected. SaaS types
// get their canonical host; the region/name-scoped clouds (ecr/acr/gar) seed an
// editable canonical value. Self-hostable registries (artifactory/harbor/
// distribution/oci) have no canonical host, so their suggestion is left blank.
const DEFAULT_HOSTS: Record<RegistryType, string> = {
  ghcr: 'ghcr.io',
  quay: 'quay.io',
  dockerhub: 'docker.io',
  far: 'repo.f5.com',
  artifactory: '',
  harbor: '',
  distribution: '',
  oci: '',
  icr: 'icr.io',
  ecr: 'public.ecr.aws',
};

// Per-type guidance on creating the right pull credential. credential = what to
// make; steps = how; scope = least privilege; docUrl = provider's token page.
interface RegistryHelpInfo {
  credential: string;
  steps: string[];
  scope: string;
  docUrl: string;
}

const REGISTRY_HELP: Record<RegistryType, RegistryHelpInfo> = {
  ghcr: {
    credential: 'Personal Access Token (classic)',
    steps: [
      'GitHub → Settings → Developer settings → Personal access tokens (classic).',
      'Generate a token with the read:packages scope.',
      'Use your GitHub username + the token below.',
    ],
    scope: 'read:packages',
    docUrl: 'https://github.com/settings/tokens',
  },
  quay: {
    credential: 'Robot account',
    steps: [
      'Quay → your org or user → Robot Accounts → Create.',
      'Grant the robot Read on the target repositories.',
      'Use the robot name + token below.',
    ],
    scope: 'Read (pull)',
    docUrl: 'https://docs.quay.io/',
  },
  dockerhub: {
    credential: 'Access Token (PAT)',
    steps: [
      'Docker Hub → Account Settings → Security → Personal access tokens.',
      'Create a token with Read-only access.',
      'Use your Docker ID + the token below.',
    ],
    scope: 'Read-only',
    docUrl: 'https://docs.docker.com/security/for-developers/access-tokens/',
  },
  far: {
    credential: 'F5 CNE auth key (f5-cne-far-auth-key.tgz)',
    steps: [
      'Obtain f5-cne-far-auth-key.tgz from MyF5 / your F5 representative.',
      'Drag & drop the .tgz (or its service-account .json) into the FAR Auth Key field.',
      'No username needed — auth uses the _json_key_base64 scheme.',
    ],
    scope: 'Pull from the FAR host',
    docUrl: 'https://my.f5.com/',
  },
  artifactory: {
    credential: 'Identity / Access token (or API key)',
    steps: [
      'Artifactory → Edit Profile → Generate an Identity Token.',
      'Grant read on the target Docker repository.',
      'Use your username + the token below.',
    ],
    scope: 'Read (pull)',
    docUrl: 'https://jfrog.com/help/',
  },
  harbor: {
    credential: 'Robot account',
    steps: [
      'Harbor → Project → Robot Accounts → New Robot Account.',
      'Grant Pull permission on the project.',
      'Use robot$<name> + the secret below.',
    ],
    scope: 'pull',
    docUrl: 'https://goharbor.io/docs/latest/',
  },
  distribution: {
    credential: 'Basic-auth user (htpasswd)',
    steps: [
      'Ask the registry operator for a username + password.',
      'Self-hosted registry:2 typically authenticates from an htpasswd file.',
      'Enter the host (e.g. registry.example.com:5000) + username + password.',
    ],
    scope: 'Pull',
    docUrl: 'https://distribution.github.io/distribution/',
  },
  oci: {
    credential: 'Registry username + token / password',
    steps: [
      'Create a pull credential in your OCI-compliant registry.',
      'Enter its host + username + token below.',
      'Auth is HTTP Basic / Bearer against the /v2/ API.',
    ],
    scope: 'Pull',
    docUrl: 'https://github.com/opencontainers/distribution-spec',
  },
  ecr: {
    credential: 'AWS Cloud Credential Template (not a registry token)',
    steps: [
      'Create or select an AWS Cloud Credential Template under Access Methods.',
      'The IAM principal needs ecr:GetAuthorizationToken + image pull (e.g. AmazonEC2ContainerRegistryReadOnly).',
      'bnk-forge exchanges it for a short-lived ECR token at pull time.',
    ],
    scope: 'ecr:GetAuthorizationToken + read',
    docUrl: 'https://docs.aws.amazon.com/AmazonECR/latest/userguide/security-iam-awsmanpol.html',
  },
  icr: {
    credential: 'IBM Cloud Credential Template (not a registry token)',
    steps: [
      'Create or select an IBM Cloud Credential Template under Access Methods.',
      'Its API key needs the Container Registry Reader role.',
      'bnk-forge exchanges it for an IAM token at pull time.',
    ],
    scope: 'Container Registry Reader',
    docUrl: 'https://cloud.ibm.com/docs/Registry?topic=Registry-registry_access',
  },
};

const isDerived = (t: string): t is RegistryType =>
  DERIVED_TYPES.includes(t as RegistryType);
const isFar = (t: string) => t === 'far';

// ============================================================================
// Form state
// ============================================================================

interface FormState {
  name: string;
  description: string;
  type: RegistryType;
  registry_host: string;
  username: string;
  token: string;
  far_service_account: string;
  credential_template_id: number | null;
}

const emptyForm: FormState = {
  name: '',
  description: '',
  type: 'ghcr',
  registry_host: 'ghcr.io',
  username: '',
  token: '',
  far_service_account: '',
  credential_template_id: null,
};

// ============================================================================
// Credential file dropzone — drag-and-drop or click-to-upload for credentials
// delivered as a file: a FAR *.tgz auth key (base64-encoded) or a service-account
// *.json (raw text). Both forms are accepted by the backend FAR ingest.
// ============================================================================

function CredentialFileDropzone({
  inputId,
  onLoad,
  hint,
}: {
  inputId: string;
  onLoad: (value: string, filename: string) => void;
  hint: string;
}) {
  const [dragging, setDragging] = useState(false);
  const [filename, setFilename] = useState<string | null>(null);

  const ingest = (file: File) => {
    const isJson =
      file.name.toLowerCase().endsWith('.json') || file.type === 'application/json';
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result;
      if (typeof result !== 'string') return;
      // readAsText → raw JSON text; readAsDataURL → "data:...;base64,XXXX".
      const value = isJson ? result : (result.split(',')[1] ?? '');
      onLoad(value, file.name);
      setFilename(file.name);
    };
    if (isJson) reader.readAsText(file);
    else reader.readAsDataURL(file);
  };

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        const file = e.dataTransfer.files?.[0];
        if (file) ingest(file);
      }}
      className={cn(
        'flex flex-col items-center justify-center gap-1 rounded-md border border-dashed border-border p-4 text-center text-xs text-muted-foreground transition-colors',
        dragging && 'border-primary bg-primary/5 text-foreground',
      )}
    >
      <Upload className="h-4 w-4" />
      <span>{filename ? `Loaded ${filename}` : hint}</span>
      <label htmlFor={inputId} className="cursor-pointer text-primary hover:underline">
        Choose file
      </label>
      <input
        id={inputId}
        type="file"
        accept=".tgz,.gz,.json,application/gzip,application/json"
        className="sr-only"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) ingest(file);
          e.target.value = '';
        }}
      />
    </div>
  );
}

// ============================================================================
// Per-type credential help — contextual block that swaps with the selected type.
// ============================================================================

function RegistryHelp({ type }: { type: RegistryType }) {
  const help = REGISTRY_HELP[type];
  if (!help) return null;
  return (
    <div className="rounded-md border border-border bg-card border-l-2 border-l-info p-3 text-xs">
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 font-medium text-foreground">
          <Info className="h-3.5 w-3.5 text-info" />
          Credential: {help.credential}
        </span>
        <Popover>
          <PopoverTrigger asChild>
            <Button variant="ghost" size="sm" className="h-6 px-2 text-xs">
              <HelpCircle className="mr-1 h-3.5 w-3.5" />
              Steps
            </Button>
          </PopoverTrigger>
          <PopoverContent className="w-80 text-xs">
            <ol className="list-decimal space-y-1 pl-4">
              {help.steps.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ol>
          </PopoverContent>
        </Popover>
      </div>
      <p className="mt-1 text-muted-foreground">Permission: {help.scope}</p>
      <a
        href={help.docUrl}
        target="_blank"
        rel="noreferrer"
        className="mt-1 inline-flex items-center gap-1 text-primary hover:underline"
      >
        How to create this credential
        <ExternalLink className="h-3 w-3" />
      </a>
    </div>
  );
}

// ============================================================================
// Component
// ============================================================================

export default function ContainerRegistries() {
  const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false);
  const [isEditDialogOpen, setIsEditDialogOpen] = useState(false);
  const [editingRegistry, setEditingRegistry] = useState<ContainerRegistry | null>(null);
  const [testingRegistryId, setTestingRegistryId] = useState<number | null>(null);
  const [formData, setFormData] = useState<FormState>({ ...emptyForm });

  const { data: registries, isLoading } = useContainerRegistries();

  // Cloud credential templates feed the derived-type dropdown.
  const { data: templates } = useQuery({
    queryKey: ['credential-templates'],
    queryFn: () => api.listCredentialTemplates(),
  });

  const createMutation = useCreateContainerRegistry();
  const updateMutation = useUpdateContainerRegistry();
  const deleteMutation = useDeleteContainerRegistry();
  const testMutation = useTestContainerRegistry();

  const resetForm = () => setFormData({ ...emptyForm });

  const handleTypeChange = (type: RegistryType) => {
    setFormData((prev) => ({
      ...prev,
      type,
      registry_host: DEFAULT_HOSTS[type] ?? prev.registry_host,
    }));
  };

  const handleCreate = () => {
    createMutation.mutate(
      {
        name: formData.name,
        description: formData.description || null,
        type: formData.type,
        registry_host: formData.registry_host,
        username: isDerived(formData.type) || isFar(formData.type) ? null : formData.username || null,
        token: isDerived(formData.type) || isFar(formData.type) ? null : formData.token || null,
        far_service_account: isFar(formData.type) ? formData.far_service_account || null : null,
        credential_template_id: isDerived(formData.type) ? formData.credential_template_id : null,
      },
      {
        onSuccess: (reg) => {
          notify.success('Container registry added', undefined, { category: 'credentials' });
          setIsCreateDialogOpen(false);
          resetForm();
          if (reg?.id) runTest(reg.id);
        },
      },
    );
  };

  const handleEdit = (registry: ContainerRegistry) => {
    setEditingRegistry(registry);
    setFormData({
      name: registry.name,
      description: registry.description || '',
      type: registry.type as RegistryType,
      registry_host: registry.registry_host,
      username: registry.username || '',
      token: '',
      far_service_account: '',
      credential_template_id: registry.credential_template_id ?? null,
    });
    setIsEditDialogOpen(true);
  };

  const handleUpdate = () => {
    if (!editingRegistry) return;
    const data: Parameters<typeof updateMutation.mutate>[0]['data'] = {
      name: formData.name,
      description: formData.description,
      type: formData.type,
      registry_host: formData.registry_host,
    };
    if (!isDerived(formData.type) && !isFar(formData.type)) {
      data.username = formData.username || null;
      if (formData.token) data.token = formData.token;
    }
    if (isFar(formData.type) && formData.far_service_account) {
      data.far_service_account = formData.far_service_account;
    }
    if (isDerived(formData.type)) {
      data.credential_template_id = formData.credential_template_id;
    }

    updateMutation.mutate(
      { id: editingRegistry.id, data },
      {
        onSuccess: (reg) => {
          notify.success('Container registry updated', undefined, { category: 'credentials' });
          setIsEditDialogOpen(false);
          setEditingRegistry(null);
          resetForm();
          if (reg?.id) runTest(reg.id);
        },
      },
    );
  };

  const handleDelete = (registry: ContainerRegistry) => {
    if (confirm(`Delete container registry "${registry.name}"? This action cannot be undone.`)) {
      deleteMutation.mutate(registry.id, {
        onSuccess: () =>
          notify.success('Container registry deleted', undefined, { category: 'credentials' }),
      });
    }
  };

  const runTest = (id: number) => {
    setTestingRegistryId(id);
    testMutation.mutate(id, {
      onSuccess: (result) => {
        setTestingRegistryId(null);
        if (result.not_implemented) {
          notify.warning(result.message || 'Connectivity test not yet implemented for this type.');
        } else if (result.success) {
          notify.success(
            `Registry test passed${result.message ? `: ${result.message}` : ''}`,
            undefined,
            { category: 'credentials' },
          );
        } else {
          notify.error(`Registry test failed: ${result.error || result.message || 'Unknown error'}`);
        }
      },
      onError: () => setTestingRegistryId(null),
    });
  };

  const derivedTemplates = (templates ?? []) as CloudCredentialTemplate[];

  // ==========================================================================
  // Shared form fields (used by both create + edit dialogs)
  // ==========================================================================

  const renderTypeAwareFields = (mode: 'create' | 'edit') => {
    const prefix = mode === 'edit' ? 'edit-' : '';
    const derived = isDerived(formData.type);
    const far = isFar(formData.type);
    return (
      <>
        {derived ? (
          <div>
            <Label htmlFor={`${prefix}credential-template`}>Cloud Credential Template *</Label>
            <Select
              value={formData.credential_template_id ? String(formData.credential_template_id) : ''}
              onValueChange={(v) =>
                setFormData({ ...formData, credential_template_id: Number(v) })
              }
            >
              <SelectTrigger id={`${prefix}credential-template`}>
                <SelectValue placeholder="Select a credential template" />
              </SelectTrigger>
              <SelectContent>
                {derivedTemplates.map((t) => (
                  <SelectItem key={t.id} value={String(t.id)}>
                    {t.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground mt-1">
              The short-lived registry token is exchanged from this cloud credential at pull time.
            </p>
          </div>
        ) : far ? (
          <div>
            <Label htmlFor={`${prefix}far-sa`}>FAR Auth Key *</Label>
            <CredentialFileDropzone
              inputId={`${prefix}far-file`}
              onLoad={(value) => setFormData((p) => ({ ...p, far_service_account: value }))}
              hint="Drag & drop f5-cne-far-auth-key.tgz or a service-account .json — or paste below"
            />
            <Textarea
              id={`${prefix}far-sa`}
              value={formData.far_service_account}
              onChange={(e) => setFormData({ ...formData, far_service_account: e.target.value })}
              placeholder={
                mode === 'edit'
                  ? '(existing auth key hidden — drop a file above or paste to replace)'
                  : 'Base64-encoded *.tgz auth key, or raw service-account JSON'
              }
              rows={4}
              className="font-mono text-xs mt-2"
            />
            <p className="text-xs text-muted-foreground mt-1">
              Paste the base64 <code>f5-cne-far-auth-key.tgz</code> contents or the raw service-account
              JSON. Stored encrypted; never displayed again.
            </p>
          </div>
        ) : (
          <>
            <div>
              <Label htmlFor={`${prefix}username`}>Username</Label>
              <Input
                id={`${prefix}username`}
                value={formData.username}
                onChange={(e) => setFormData({ ...formData, username: e.target.value })}
                placeholder="Registry username or robot account"
              />
            </div>
            <div>
              <Label htmlFor={`${prefix}token`}>Token / Password {mode === 'create' ? '*' : ''}</Label>
              <Input
                id={`${prefix}token`}
                type="password"
                value={formData.token}
                onChange={(e) => setFormData({ ...formData, token: e.target.value })}
                placeholder={mode === 'edit' ? 'Leave blank to keep existing' : 'Personal access token'}
              />
            </div>
          </>
        )}
        <RegistryHelp type={formData.type} />
      </>
    );
  };

  const renderForm = (mode: 'create' | 'edit') => {
    const prefix = mode === 'edit' ? 'edit-' : '';
    return (
      <div className="space-y-4">
        <div>
          <Label htmlFor={`${prefix}name`}>Registry Name *</Label>
          <Input
            id={`${prefix}name`}
            value={formData.name}
            onChange={(e) => setFormData({ ...formData, name: e.target.value })}
            placeholder="e.g., GHCR (jgruberf5)"
          />
        </div>

        <div>
          <Label htmlFor={`${prefix}description`}>Description</Label>
          <Input
            id={`${prefix}description`}
            value={formData.description}
            onChange={(e) => setFormData({ ...formData, description: e.target.value })}
            placeholder="Optional description"
          />
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <Label htmlFor={`${prefix}type`}>Registry Type *</Label>
            <Select value={formData.type} onValueChange={(v) => handleTypeChange(v as RegistryType)}>
              <SelectTrigger id={`${prefix}type`}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {[...STANDALONE_TYPES, ...DERIVED_TYPES].map((t) => (
                  <SelectItem key={t} value={t}>
                    {TYPE_LABELS[t]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label htmlFor={`${prefix}registry-host`}>Registry Host *</Label>
            <Input
              id={`${prefix}registry-host`}
              value={formData.registry_host}
              onChange={(e) => setFormData({ ...formData, registry_host: e.target.value })}
              placeholder="ghcr.io"
            />
          </div>
        </div>

        {renderTypeAwareFields(mode)}
      </div>
    );
  };

  // ==========================================================================
  // Render
  // ==========================================================================

  return (
    <SectionCard title="Container registries">
      <p className="text-sm text-muted-foreground -mt-1 mb-4">
        OCI registry access for pulling container images, Helm charts, and manifests used by
        container-engine deployments. Standalone types (GHCR, Quay, FAR) carry their own secret;
        derived types (ECR, ACR, ICR, GAR) reference a cloud credential template for short-lived
        token exchange.
      </p>
      <div className="space-y-6">
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              resetForm();
              setIsCreateDialogOpen(true);
            }}
          >
            <Plus className="h-4 w-4 mr-2" />
            Add Registry
          </Button>
        </div>

        {isLoading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : registries && registries.length > 0 ? (
          <div className="border border-border rounded-md overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="text-xs uppercase tracking-wider text-muted-foreground">
                    Registry
                  </TableHead>
                  <TableHead className="text-xs uppercase tracking-wider text-muted-foreground">
                    Type
                  </TableHead>
                  <TableHead className="text-xs uppercase tracking-wider text-muted-foreground">
                    Host
                  </TableHead>
                  <TableHead className="text-xs uppercase tracking-wider text-muted-foreground">
                    Status
                  </TableHead>
                  <TableHead className="text-xs uppercase tracking-wider text-muted-foreground text-right w-16">
                    {/* actions */}
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {registries.map((registry) => {
                  const isRowTesting =
                    testingRegistryId === registry.id && testMutation.isPending;
                  return (
                    <TableRow key={registry.id}>
                      <TableCell>
                        <div className="flex flex-col">
                          <span className="font-medium text-foreground">{registry.name}</span>
                          {registry.description && (
                            <span className="text-xs text-muted-foreground line-clamp-1 mt-0.5">
                              {registry.description}
                            </span>
                          )}
                        </div>
                      </TableCell>
                      <TableCell>
                        <Badge
                          variant={isDerived(registry.type) ? 'info' : 'success'}
                          className="text-xs"
                        >
                          {TYPE_LABELS[registry.type as RegistryType] ?? registry.type}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <span className="font-mono text-xs text-foreground/80">
                          {registry.registry_host}
                        </span>
                      </TableCell>
                      <TableCell>
                        <RegistryStatusIcon
                          status={registry.last_test_status}
                          when={registry.last_test_at}
                          message={registry.last_test_message}
                          pending={isRowTesting}
                        />
                      </TableCell>
                      <TableCell className="text-right">
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-8 w-8 p-0"
                              aria-label="Container registry actions"
                            >
                              <MoreVertical className="h-4 w-4" />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            <DropdownMenuItem
                              onClick={() => runTest(registry.id)}
                              disabled={testMutation.isPending}
                              title="Test registry connectivity"
                            >
                              {isRowTesting ? (
                                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                              ) : (
                                <TestTube2 className="h-4 w-4 mr-2" />
                              )}
                              Test connection
                            </DropdownMenuItem>
                            <DropdownMenuItem onClick={() => handleEdit(registry)}>
                              <Edit className="h-4 w-4 mr-2" />
                              Edit
                            </DropdownMenuItem>
                            <DropdownMenuItem onClick={() => handleDelete(registry)}>
                              <Trash2 className="h-4 w-4 mr-2 text-destructive" />
                              Delete
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        ) : (
          <div className="text-center py-8 text-muted-foreground">
            <Boxes className="h-12 w-12 mx-auto mb-4 opacity-50" />
            <p>No container registries configured</p>
            <p className="text-xs mt-2">
              Add a registry to pull images, charts, and manifests for container-engine deployments
            </p>
          </div>
        )}

        {/* Create Dialog */}
        <Dialog
          open={isCreateDialogOpen}
          onOpenChange={(open) => {
            setIsCreateDialogOpen(open);
            if (!open) resetForm();
          }}
        >
          <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
            <DialogHeader>
              <DialogTitle>Add Container Registry</DialogTitle>
              <DialogDescription>
                Configure an OCI registry. Secrets are stored encrypted and never displayed again.
              </DialogDescription>
            </DialogHeader>
            {renderForm('create')}
            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => {
                  setIsCreateDialogOpen(false);
                  resetForm();
                }}
              >
                Cancel
              </Button>
              <Button onClick={handleCreate} disabled={createMutation.isPending}>
                {createMutation.isPending ? (
                  <>
                    <Loader2 className="h-4 w-4 mr-2 animate-spin" /> Adding...
                  </>
                ) : (
                  'Add Registry'
                )}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* Edit Dialog */}
        <Dialog open={isEditDialogOpen} onOpenChange={setIsEditDialogOpen}>
          <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
            <DialogHeader>
              <DialogTitle>Edit Container Registry</DialogTitle>
              <DialogDescription>
                Update registry settings. Leave secret fields blank to keep existing values.
              </DialogDescription>
            </DialogHeader>
            {renderForm('edit')}
            <DialogFooter>
              <Button variant="outline" onClick={() => setIsEditDialogOpen(false)}>
                Cancel
              </Button>
              <Button onClick={handleUpdate} disabled={updateMutation.isPending}>
                {updateMutation.isPending ? (
                  <>
                    <Loader2 className="h-4 w-4 mr-2 animate-spin" /> Updating...
                  </>
                ) : (
                  'Update Registry'
                )}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
    </SectionCard>
  );
}

// ============================================================================
// Helpers
// ============================================================================

/**
 * Registry reachability indicator — a container icon coloured by the last
 * connectivity-test result:
 *   green   — authenticated (ok)
 *   red     — failed
 *   amber   — testing in progress / not yet implemented
 *   zinc    — never tested yet
 */
function RegistryStatusIcon({
  status,
  when,
  message,
  pending,
}: {
  status?: string | null;
  when?: string | null;
  message?: string | null;
  pending?: boolean;
}) {
  const ageLabel = when ? `last tested ${new Date(when).toLocaleString()}` : 'never tested';
  const effective = pending ? 'running' : (status ?? 'unknown');
  const tooltip =
    effective === 'running'
      ? 'Testing…'
      : effective === 'ok'
        ? `Authenticated — ${ageLabel}`
        : effective === 'failed'
          ? `Failed — ${ageLabel}${message ? `\n${message}` : ''}`
          : effective === 'not_implemented'
            ? `Test not yet implemented — ${ageLabel}`
            : `Not tested yet — ${ageLabel}`;
  const colour =
    effective === 'running'
      ? 'text-warning animate-pulse'
      : effective === 'ok'
        ? 'text-success'
        : effective === 'failed'
          ? 'text-destructive'
          : effective === 'not_implemented'
            ? 'text-warning'
            : 'text-muted-foreground';
  return (
    <span className={`inline-flex items-center ${colour}`} title={tooltip} aria-label={tooltip}>
      <Container className="h-4 w-4" />
    </span>
  );
}
