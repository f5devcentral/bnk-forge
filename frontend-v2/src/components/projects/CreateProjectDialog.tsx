import { useEffect, useCallback, useMemo, useState } from 'react';
import { z } from 'zod';
import { useForm, Controller } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { LoadingButton } from '@/components/ui/loading-button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { useCreateProject } from '@/hooks/useProjects';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { settingsApi } from '@/lib/api/settings';
import { sshCredentialsApi } from '@/lib/api/ssh-credentials';
import { Loader2, CheckCircle, AlertTriangle, Terminal, Palette, Server, Key } from 'lucide-react';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import type { CloudRegionOption, ProjectCreateData, ProjectType } from '@/types';
import { RegionSelector } from '@/components/aws/RegionSelector';
import { CloudRegionSelector } from '@/components/cloud/CloudRegionSelector';
import { getRegionInfo } from '@/lib/aws-regions';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { SectionCard } from '@/components/ui/section-card';
import { cn } from '@/lib/utils';
import { queryKeys } from '@/lib/queryKeys';
import { useNavigate } from 'react-router-dom';

function slugifyAwsSafeProjectName(value: string): string {
  const cleaned = value.trim().replace(/[^A-Za-z0-9+=,.@_-]+/g, '-').replace(/-+/g, '-').replace(/^[-_.]+|[-_.]+$/g, '');
  return cleaned || 'bnk-forge';
}

const IBM_NAME_PATTERN = /^[a-z0-9][a-z0-9-]{0,34}$/;

function slugifyIbmSafeProjectName(value: string): string {
  const cleaned = value.trim().toLowerCase()
    .replace(/[^a-z0-9-]+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-+|-+$/g, '');
  return (cleaned || 'bnk-forge').slice(0, 35).replace(/-+$/g, '');
}

// ---------------------------------------------------------------------------
// Zod schema
// ---------------------------------------------------------------------------
const projectSchema = z.object({
  name: z.string().min(1, 'Project name is required').max(255, 'Project name must be 255 characters or less'),
  description: z.string().optional().default(''),
  project_type: z.enum(['cloud-aws', 'cloud-azure', 'cloud-gcp', 'cloud-ibm', 'kubernetes', 'bare-metal'], {
    required_error: 'Please select a project type',
  }),
  environment: z.string().default('dev'),
  region: z.string().optional().default(''),
  backend_type: z.string().default('local'),
  state_storage_provider: z.string().optional().default(''),
  s3_state_bucket: z.string().optional(),
  s3_state_key_prefix: z.string().optional(),
  s3_state_region: z.string().optional(),
  s3_use_native_locking: z.boolean().default(true),
  s3_endpoint: z.string().optional(),
  color: z.string().default('#2563eb'),
  icon: z.string().optional().default(''),
  credential_template_id: z.number().optional(),
  ssh_credential_id: z.number().optional(),
  target_platform_profile: z.string().optional(),
}).superRefine((data, ctx) => {
  // Cloud projects need a region
  if (data.project_type?.startsWith('cloud-') && !data.region) {
    ctx.addIssue({
      code: z.ZodIssueCode.custom,
      message: 'Region is required for cloud projects',
      path: ['region'],
    });
  }
  // S3 backend needs bucket name
  if (data.backend_type === 's3' && !data.s3_state_bucket) {
    ctx.addIssue({
      code: z.ZodIssueCode.custom,
      message: 'S3 bucket name is required',
      path: ['s3_state_bucket'],
    });
  }
  if (data.project_type === 'cloud-aws' && data.name && !/^[A-Za-z0-9+=,.@_-]+$/.test(data.name)) {
    ctx.addIssue({
      code: z.ZodIssueCode.custom,
      message: `AWS projects derive IAM/resource names from the project name. Use only letters, numbers, '+', '=', ',', '.', '@', '-', '_' or rename to something like '${slugifyAwsSafeProjectName(data.name)}'.`,
      path: ['name'],
    });
  }
  if (data.project_type === 'cloud-ibm' && data.name && !IBM_NAME_PATTERN.test(data.name)) {
    ctx.addIssue({
      code: z.ZodIssueCode.custom,
      message: `IBM Cloud resource names must use lowercase letters, digits, and '-' only (max 35 chars, must start with a letter or digit). Try '${slugifyIbmSafeProjectName(data.name)}'.`,
      path: ['name'],
    });
  }
});

type ProjectFormData = z.infer<typeof projectSchema>;

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------
interface CreateProjectDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const PROJECT_TYPES: { value: ProjectType; label: string; icon: string; description: string }[] = [
  { value: 'cloud-aws', label: 'AWS Cloud', icon: '☁️', description: 'Deploy on Amazon EKS with cloud-managed infrastructure' },
  { value: 'cloud-azure', label: 'Azure Cloud', icon: '⛅', description: 'Deploy on Azure AKS with cloud-managed infrastructure' },
  { value: 'cloud-gcp', label: 'GCP Cloud', icon: '🌤️', description: 'Deploy on Google GKE with cloud-managed infrastructure' },
  { value: 'cloud-ibm', label: 'IBM Cloud', icon: '🟦', description: 'Deploy on IBM ROKs with cloud-managed infrastructure' },
  { value: 'kubernetes', label: 'Existing Kubernetes', icon: '🖥️', description: 'On-prem, bare-metal, OpenShift, or any existing K8s cluster' },
  { value: 'bare-metal', label: 'Bare Metal DPU', icon: '🔧', description: 'Deploy BNK on bare-metal servers with NVIDIA BlueField DPUs' },
];

const K8S_PLATFORM_OPTIONS = [
  { value: 'generic_onprem', label: 'Generic (kind, microk8s, bare-metal)' },
  { value: 'ocp', label: 'OpenShift / OKD' },
];

const DEFAULT_VALUES: ProjectFormData = {
  name: '',
  description: '',
  project_type: undefined as unknown as ProjectFormData['project_type'],
  environment: 'dev',
  region: '',
  backend_type: 'local',
  state_storage_provider: '',
  s3_state_bucket: undefined,
  s3_state_key_prefix: undefined,
  s3_state_region: undefined,
  s3_use_native_locking: true,
  s3_endpoint: undefined,
  color: '#2563eb',
  icon: '',
  credential_template_id: undefined,
  ssh_credential_id: undefined,
  target_platform_profile: undefined,
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------
const NEW_SSH_CRED_DEFAULT = {
  name: '',
  host: '',
  username: '',
  auth_type: 'password' as 'key' | 'password',
  password: '',
  private_key: '',
};

export function CreateProjectDialog({ open, onOpenChange }: CreateProjectDialogProps) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const createMutation = useCreateProject();

  const form = useForm<ProjectFormData>({
    resolver: zodResolver(projectSchema),
    defaultValues: DEFAULT_VALUES,
  });

  const {
    register,
    handleSubmit,
    watch,
    setValue,
    reset,
    control,
    formState: { errors },
  } = form;

  const projectType = watch('project_type');
  const backendType = watch('backend_type');
  const region = watch('region');
  const credentialTemplateId = watch('credential_template_id');
  const color = watch('color');

  const isCloudProject = projectType?.startsWith('cloud-');
  const isK8sProject = projectType === 'kubernetes';
  const isBareMetalProject = projectType === 'bare-metal';

  // Inline SSH credential creation state
  const [sshCredMode, setSshCredMode] = useState<'existing' | 'new'>('existing');
  const [newSshCred, setNewSshCred] = useState(NEW_SSH_CRED_DEFAULT);
  const [newSshCredPort, setNewSshCredPort] = useState(22);

  // Fetch system defaults for pre-selecting project type
  const { data: defaults } = useQuery({
    queryKey: ['system-defaults'],
    queryFn: () => settingsApi.getSystemDefaults(),
  });

  // Fetch credential templates (for cloud projects)
  const { data: templates, isLoading: templatesLoading } = useQuery({
    queryKey: ['credential-templates'],
    queryFn: () => api.listCredentialTemplates(),
  });

  // Fetch first-class SSH credentials (for K8s projects)
  const { data: sshCredentials, isLoading: sshCredsLoading } = useQuery({
    queryKey: queryKeys.sshCredentials.list(),
    queryFn: () => api.listSSHCredentials(),
  });

  // Handle project type change — must be defined before useEffect that references it
  const handleProjectTypeChange = useCallback((type: ProjectType) => {
    const isCloud = type.startsWith('cloud-');

    setValue('project_type', type, { shouldValidate: true });
    // Reset region for kubernetes projects
    if (!isCloud) {
      setValue('region', '');
    }
    // Kubernetes projects always use local backend
    if (!isCloud) {
      setValue('backend_type', 'local');
    }
    // Clear credential selections and platform when switching types
    setValue('credential_template_id', undefined);
    setValue('ssh_credential_id', undefined);
    setValue('target_platform_profile', undefined);
  }, [setValue]);

  // Pre-select project type from system defaults on first open
  useEffect(() => {
    if (open && !projectType && defaults) {
      const defaultType = defaults?.project?.default_type?.value as ProjectType | undefined;
      if (defaultType) {
        handleProjectTypeChange(defaultType);
      }
    }
  }, [open, defaults, projectType, handleProjectTypeChange]);

  // Filter credential templates by project type (cloud only — K8s uses SSH Credentials)
  const filteredTemplates = templates?.filter(t => {
    if (isK8sProject) return false; // K8s projects don't use cloud credential templates
    if (projectType === 'cloud-aws') return t.provider === 'aws';
    if (projectType === 'cloud-azure') return t.provider === 'azure';
    if (projectType === 'cloud-gcp') return t.provider === 'gcp';
    if (projectType === 'cloud-ibm') return t.provider === 'ibm';
    return t.provider !== 'ssh'; // Exclude legacy SSH templates from cloud picker
  });

  const selectedTemplate = templates?.find(t => t.id === credentialTemplateId);

  const { data: ibmRegionsResponse, isLoading: ibmRegionsLoading, error: ibmRegionsError } = useQuery({
    queryKey: ['ibm-regions', credentialTemplateId ?? 'default'],
    queryFn: () => api.listIBMRegions(credentialTemplateId),
    enabled: open && projectType === 'cloud-ibm',
  });

  const ibmRegions = useMemo<CloudRegionOption[]>(() => ibmRegionsResponse?.regions || [], [ibmRegionsResponse]);

  // Handle credential template change -- auto-inherit region for cloud projects
  const handleTemplateChange = useCallback((templateId: string) => {
    const id = templateId === 'none' ? undefined : parseInt(templateId);
    const template = templates?.find(t => t.id === id);

    setValue('credential_template_id', id);
    // For cloud projects: inherit region from template
    if (isCloudProject && template?.region) {
      setValue('region', template.region, { shouldValidate: true });
    }
  }, [templates, isCloudProject, setValue]);

  // Check if region matches template region (cloud projects only)
  const regionMatchesTemplate = isCloudProject && selectedTemplate
    ? region === selectedTemplate.region
    : false;

  const regionInfo = region ? getRegionInfo(region) : null;
  const projectNameValue = watch('name');
  const awsSafeNameSuggestion = projectType === 'cloud-aws' ? slugifyAwsSafeProjectName(projectNameValue || '') : '';
  const ibmSafeNameSuggestion = projectType === 'cloud-ibm' ? slugifyIbmSafeProjectName(projectNameValue || '') : '';

  // Form submission
  const onSubmit = async (data: ProjectFormData) => {
    try {
      // Transform form data → API payload (wrap S3 fields into state_config)
      const { s3_state_bucket, s3_state_key_prefix, s3_state_region, s3_use_native_locking, s3_endpoint, state_storage_provider, target_platform_profile, ...rest } = data;
      const payload: ProjectCreateData = {
        ...rest,
        target_platform_profile: target_platform_profile || undefined,
        state_config: data.backend_type === 's3'
          ? { s3_state_bucket, s3_state_key_prefix, s3_state_region, s3_use_native_locking, s3_endpoint, state_storage_provider: state_storage_provider || (projectType === 'cloud-ibm' ? 'ibm' : 'aws') }
          : undefined,
      };

      // If user created a new SSH credential inline, create it first
      if (sshCredMode === 'new' && newSshCred.username && newSshCred.host) {
        const credName = newSshCred.name || `${data.name || 'project'}-ssh`;
        const newCred = await sshCredentialsApi.createSSHCredential({
          name: credName,
          host: newSshCred.host,
          port: newSshCredPort,
          username: newSshCred.username,
          auth_type: newSshCred.auth_type,
          password: newSshCred.auth_type === 'password' ? newSshCred.password : undefined,
          private_key: newSshCred.auth_type === 'key' ? newSshCred.private_key : undefined,
        });
        payload.ssh_credential_id = newCred.id;
        queryClient.invalidateQueries({ queryKey: queryKeys.sshCredentials.all });
      }

      const result = await createMutation.mutateAsync(payload);

      // Close dialog immediately
      onOpenChange(false);

      // K8s project with SSH → navigate to clusters tab
      if (data.project_type === 'kubernetes' && (data.ssh_credential_id ?? payload.ssh_credential_id) && result.project_id) {
        setTimeout(() => {
          navigate(`/projects/${result.project_id}?tab=clusters`);
        }, 300);
      }

      // Bare-metal project → navigate to project with bare-metal tab
      if (data.project_type === 'bare-metal' && result.project_id) {
        setTimeout(() => {
          navigate(`/projects/${result.project_id}?tab=bare-metal`);
        }, 300);
      }

      // Reset form and inline SSH state
      reset(DEFAULT_VALUES);
      setSshCredMode('existing');
      setNewSshCred(NEW_SSH_CRED_DEFAULT);
      setNewSshCredPort(22);
    } catch {
      // Error is handled by the mutation
    }
  };

  const handleOpenChange = (nextOpen: boolean) => {
    if (!nextOpen) {
      reset(DEFAULT_VALUES);
      setSshCredMode('existing');
      setNewSshCred(NEW_SSH_CRED_DEFAULT);
      setNewSshCredPort(22);
    }
    onOpenChange(nextOpen);
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-[600px] max-h-[90vh] overflow-y-auto">
        <form onSubmit={handleSubmit(onSubmit)}>
          <DialogHeader>
            <DialogTitle className="text-xl font-bold">Create New Project</DialogTitle>
            <DialogDescription>
              Projects group together modules deployed to a target environment.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-6 py-4">
            {/* Basics */}
            <SectionCard title="Basics" compact>
              <div className="space-y-4">
                {/* Project Name */}
                <div className="grid gap-2">
                  <Label htmlFor="name">Project Name *</Label>
                  <Input
                    id="name"
                    {...register('name')}
                    placeholder="my-infrastructure"
                  />
                  {errors.name && (
                    <p className="text-xs text-destructive mt-1">{errors.name.message}</p>
                  )}
                  {projectType === 'cloud-aws' && projectNameValue && !/^[A-Za-z0-9+=,.@_-]+$/.test(projectNameValue) && (
                    <p className="text-xs text-warning mt-1">
                      AWS-derived IAM/resource names cannot contain spaces or unsupported punctuation. Suggested AWS-safe name: <span className="font-mono">{awsSafeNameSuggestion}</span>
                    </p>
                  )}
                  {projectType === 'cloud-ibm' && projectNameValue && !IBM_NAME_PATTERN.test(projectNameValue) && (
                    <p className="text-xs text-warning mt-1">
                      IBM Cloud resource names use lowercase letters, digits, and '-' only (max 35 chars, must start with a letter or digit). Suggested IBM-safe name: <span className="font-mono">{ibmSafeNameSuggestion}</span>
                    </p>
                  )}
                </div>

                {/* Project Type -- the key new field */}
                <div className="grid gap-2">
                  <Label>Project Type *</Label>
                  <div className="grid grid-cols-2 gap-2">
                    {PROJECT_TYPES.map(pt => (
                      <button
                        key={pt.value}
                        type="button"
                        onClick={() => handleProjectTypeChange(pt.value)}
                        className={cn(
                          'flex items-start gap-3 rounded-lg border p-3 text-left transition-colors',
                          projectType === pt.value
                            ? 'border-primary bg-primary/5 ring-1 ring-primary'
                            : 'border-border hover:border-primary/50 hover:bg-muted/50'
                        )}
                      >
                        <span className="text-xl mt-0.5">{pt.icon}</span>
                        <div className="flex-1 min-w-0">
                          <div className="text-sm font-medium text-foreground">{pt.label}</div>
                          <div className="text-xs text-muted-foreground mt-1 line-clamp-2">
                            {pt.description}
                          </div>
                        </div>
                      </button>
                    ))}
                  </div>
                  {errors.project_type && (
                    <p className="text-xs text-destructive mt-1">{errors.project_type.message}</p>
                  )}
                </div>

                {/* K8s Platform Sub-selector */}
                {isK8sProject && (
                  <div className="grid gap-2">
                    <Label>Platform Type</Label>
                    <Controller
                      control={control}
                      name="target_platform_profile"
                      render={({ field }) => (
                        <Select
                          value={field.value || 'generic_onprem'}
                          onValueChange={field.onChange}
                        >
                          <SelectTrigger>
                            <SelectValue placeholder="Select platform" />
                          </SelectTrigger>
                          <SelectContent>
                            {K8S_PLATFORM_OPTIONS.map((opt) => (
                              <SelectItem key={opt.value} value={opt.value}>
                                {opt.label}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      )}
                    />
                    <p className="text-xs text-muted-foreground mt-1">
                      Helps BNK Forge select the right module configurations for your cluster
                    </p>
                  </div>
                )}

                {/* Description */}
                <div className="grid gap-2">
                  <Label htmlFor="description">Description</Label>
                  <Textarea
                    id="description"
                    {...register('description')}
                    placeholder="Brief description of this project"
                    rows={2}
                  />
                </div>

                {/* Environment */}
                <div className="grid gap-2">
                  <Label htmlFor="environment">Environment</Label>
                  <Controller
                    control={control}
                    name="environment"
                    render={({ field }) => (
                      <Select
                        value={field.value || 'dev'}
                        onValueChange={field.onChange}
                      >
                        <SelectTrigger>
                          <SelectValue placeholder="Select environment" />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="dev">Development</SelectItem>
                          <SelectItem value="staging">Staging</SelectItem>
                          <SelectItem value="prod">Production</SelectItem>
                        </SelectContent>
                      </Select>
                    )}
                  />
                </div>
              </div>
            </SectionCard>

            {/* Connectivity — SSH jumphost / bastion (K8s + bare-metal) */}
            {(isK8sProject || isBareMetalProject) && (
              <SectionCard title="Connectivity" compact>
                <div className="space-y-4">
                  <div className="grid gap-2">
                    <Label htmlFor="ssh_credential">SSH Jumphost / Bastion / Single Node</Label>
                    {sshCredsLoading ? (
                      <div className="flex items-center gap-2 text-sm text-muted-foreground">
                        <Loader2 className="h-4 w-4 animate-spin" />
                        Loading SSH credentials...
                      </div>
                    ) : sshCredMode === 'existing' ? (
                      <Controller
                        control={control}
                        name="ssh_credential_id"
                        render={({ field }) => (
                          <Select
                            value={field.value?.toString() || 'none'}
                            onValueChange={(val) => {
                              if (val === '__new__') {
                                setSshCredMode('new');
                                field.onChange(undefined);
                              } else {
                                field.onChange(val === 'none' ? undefined : parseInt(val));
                              }
                            }}
                          >
                            <SelectTrigger>
                              <SelectValue placeholder="Select SSH jumphost / bastion (optional)" />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="none">None — direct connectivity</SelectItem>
                              {sshCredentials?.map((cred) => (
                                <SelectItem key={cred.id} value={cred.id.toString()}>
                                  <span className="flex items-center gap-2">
                                    <Terminal className="h-3 w-3" />
                                    {cred.name}
                                    <span className="text-muted-foreground text-xs">
                                      ({cred.username}@{cred.host})
                                    </span>
                                  </span>
                                </SelectItem>
                              ))}
                              <SelectItem value="__new__">
                                <span className="flex items-center gap-2 text-primary">
                                  <Key className="h-3 w-3" />
                                  + Create new SSH connection...
                                </span>
                              </SelectItem>
                            </SelectContent>
                          </Select>
                        )}
                      />
                    ) : (
                      /* Inline SSH credential creation form */
                      <div className="rounded-lg border border-border bg-muted/50 p-4 space-y-3">
                        <div className="flex items-center justify-between">
                          <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground/70 flex items-center gap-1.5">
                            <Key className="h-3 w-3" />
                            New SSH Connection
                          </span>
                          <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            onClick={() => {
                              setSshCredMode('existing');
                              setNewSshCred(NEW_SSH_CRED_DEFAULT);
                              setNewSshCredPort(22);
                            }}
                          >
                            Cancel
                          </Button>
                        </div>
                        <div className="grid grid-cols-2 gap-3">
                          <div>
                            <Label className="text-xs">Credential Name</Label>
                            <Input
                              value={newSshCred.name}
                              onChange={(e) => setNewSshCred(prev => ({ ...prev, name: e.target.value }))}
                              placeholder="my-server-ssh"
                              className="h-9 text-sm mt-1"
                            />
                          </div>
                          <div>
                            <Label className="text-xs">Host / IP *</Label>
                            <Input
                              value={newSshCred.host}
                              onChange={(e) => setNewSshCred(prev => ({ ...prev, host: e.target.value }))}
                              placeholder="10.176.11.91"
                              className="h-9 text-sm mt-1"
                            />
                          </div>
                        </div>
                        <div className="grid grid-cols-2 gap-3">
                          <div>
                            <Label className="text-xs">Username *</Label>
                            <Input
                              value={newSshCred.username}
                              onChange={(e) => setNewSshCred(prev => ({ ...prev, username: e.target.value }))}
                              placeholder="ubuntu"
                              className="h-9 text-sm mt-1"
                            />
                          </div>
                          <div>
                            <Label className="text-xs">Port</Label>
                            <Input
                              type="number"
                              value={newSshCredPort}
                              onChange={(e) => setNewSshCredPort(parseInt(e.target.value) || 22)}
                              placeholder="22"
                              className="h-9 text-sm mt-1"
                            />
                          </div>
                        </div>
                        <div>
                          <div className="flex gap-2 mb-2">
                            <Button
                              type="button"
                              variant={newSshCred.auth_type === 'password' ? 'default' : 'outline'}
                              size="sm"
                              onClick={() => setNewSshCred(prev => ({ ...prev, auth_type: 'password' }))}
                            >
                              Password
                            </Button>
                            <Button
                              type="button"
                              variant={newSshCred.auth_type === 'key' ? 'default' : 'outline'}
                              size="sm"
                              onClick={() => setNewSshCred(prev => ({ ...prev, auth_type: 'key' }))}
                            >
                              SSH Key
                            </Button>
                          </div>
                          {newSshCred.auth_type === 'password' ? (
                            <Input
                              type="password"
                              value={newSshCred.password}
                              onChange={(e) => setNewSshCred(prev => ({ ...prev, password: e.target.value }))}
                              placeholder="Password"
                              className="h-9 text-sm"
                            />
                          ) : (
                            <Textarea
                              value={newSshCred.private_key}
                              onChange={(e) => setNewSshCred(prev => ({ ...prev, private_key: e.target.value }))}
                              placeholder="Paste private key (PEM format)"
                              className="text-xs font-mono h-20 resize-none"
                              rows={3}
                            />
                          )}
                        </div>
                        <p className="text-xs text-muted-foreground">
                          This credential will be created when you create the project.
                        </p>
                      </div>
                    )}
                    <p className="text-xs text-muted-foreground mt-1">
                      {isK8sProject
                        ? 'Optional. When set, all discovery and cluster API access will be tunneled through this host. Clusters will be detected automatically after project creation.'
                        : 'Optional. A project-level jumphost that all bare-metal hosts will inherit unless overridden per host.'}
                    </p>
                  </div>

                  {/* Bare Metal Host Configuration */}
                  {isBareMetalProject && (
                    <div className="rounded-lg border border-border bg-muted/50 p-4 space-y-2">
                      <div className="flex items-center gap-2 text-sm font-medium text-foreground">
                        <Server className="h-4 w-4" />
                        Bare Metal DPU Deployment
                      </div>
                      <p className="text-xs text-muted-foreground">
                        After creating the project, you'll register bare-metal hosts, run discovery probes,
                        and deploy BNK with DPU acceleration. Configure a project-level jumphost above.
                        Additional per-host SSH credentials can be configured on the Bare Metal tab.
                      </p>
                    </div>
                  )}

                  {/* SSH info banner for kubernetes projects with SSH credential */}
                  {isK8sProject && sshCredMode === 'existing' && watch('ssh_credential_id') && sshCredentials && (() => {
                    const selected = sshCredentials.find(c => c.id === watch('ssh_credential_id'));
                    return selected ? (
                      <Alert className="border-warning/30 bg-warning/10 py-2">
                        <Terminal className="h-4 w-4 text-warning" />
                        <AlertDescription className="text-xs text-foreground/80">
                          <strong>SSH Jumphost</strong> — All discovery and cluster access will be tunneled through <Badge variant="outline" className="text-[10px] px-1 py-0 mx-0.5">{selected.username}@{selected.host}</Badge>.
                          After creation, BNK Forge will auto-detect available clusters.
                        </AlertDescription>
                      </Alert>
                    ) : null;
                  })()}
                  {isK8sProject && sshCredMode === 'new' && newSshCred.username && newSshCred.host && (
                    <Alert className="border-warning/30 bg-warning/10 py-2">
                      <Terminal className="h-4 w-4 text-warning" />
                      <AlertDescription className="text-xs text-foreground/80">
                        <strong>SSH Jumphost</strong> — All discovery and cluster access will be tunneled through <Badge variant="outline" className="text-[10px] px-1 py-0 mx-0.5">{newSshCred.username}@{newSshCred.host}</Badge>.
                        After creation, BNK Forge will auto-detect available clusters.
                      </AlertDescription>
                    </Alert>
                  )}
                </div>
              </SectionCard>
            )}

            {/* Cloud — credentials + region (cloud projects only) */}
            {isCloudProject && (
              <SectionCard title="Cloud" compact>
                <div className="space-y-4">
                  <div className="grid gap-2">
                    <Label htmlFor="credential_template">Cloud Credentials</Label>
                    {templatesLoading ? (
                      <div className="flex items-center gap-2 text-sm text-muted-foreground">
                        <Loader2 className="h-4 w-4 animate-spin" />
                        Loading templates...
                      </div>
                    ) : (
                      <Select
                        value={credentialTemplateId?.toString() || 'none'}
                        onValueChange={handleTemplateChange}
                      >
                        <SelectTrigger>
                          <SelectValue placeholder="Select cloud credentials" />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="none">Use global credentials</SelectItem>
                          {filteredTemplates && filteredTemplates.length > 0 ? (
                            filteredTemplates.map((template) => (
                              <SelectItem key={template.id} value={template.id.toString()}>
                                {template.name} ({template.provider.toUpperCase()})
                              </SelectItem>
                            ))
                          ) : (
                            <SelectItem value="create-hint" disabled>
                              No {projectType?.replace('cloud-', '').toUpperCase()} templates — create one in Settings
                            </SelectItem>
                          )}
                        </SelectContent>
                      </Select>
                    )}
                    <p className="text-xs text-muted-foreground mt-1">
                      Select a credential template or use global credentials from Settings
                    </p>
                  </div>

                  {/* Cloud Region */}
                  <div className="grid gap-2">
                    <Label htmlFor="region">Region *</Label>
                    <Controller
                      control={control}
                      name="region"
                      render={({ field }) => (
                        projectType === 'cloud-ibm' ? (
                          <CloudRegionSelector
                            provider="ibm"
                            value={field.value || ''}
                            onValueChange={field.onChange}
                            options={ibmRegions}
                            disabled={ibmRegionsLoading}
                          />
                        ) : (
                          <RegionSelector
                            value={field.value || ''}
                            onValueChange={(value) => {
                              field.onChange(value);
                            }}
                          />
                        )
                      )}
                    />
                    {errors.region && (
                      <p className="text-xs text-destructive mt-1">{errors.region.message}</p>
                    )}

                    {projectType === 'cloud-ibm' && ibmRegionsLoading && (
                      <p className="text-xs text-muted-foreground flex items-center gap-2">
                        <Loader2 className="h-3 w-3 animate-spin" />
                        Loading IBM Cloud regions...
                      </p>
                    )}

                    {projectType === 'cloud-ibm' && ibmRegionsError && (
                      <p className="text-xs text-destructive mt-1">
                        {ibmRegionsError instanceof Error ? ibmRegionsError.message : 'Failed to load IBM Cloud regions'}
                      </p>
                    )}

                    {projectType === 'cloud-ibm' && !credentialTemplateId && ibmRegions.length === 0 && !ibmRegionsLoading && (
                      <p className="text-xs text-muted-foreground mt-1">
                        Using the default IBM credential template for region discovery. Set an IBM default template in Settings if none is available.
                      </p>
                    )}

                    {/* Smart inheritance indicator */}
                    {selectedTemplate && regionMatchesTemplate && (
                      <div className="flex items-center gap-1.5 text-xs text-success">
                        <CheckCircle className="h-3.5 w-3.5" />
                        <span>Inherited from {selectedTemplate.name}</span>
                      </div>
                    )}

                    {/* Region mismatch warning */}
                    {selectedTemplate && !regionMatchesTemplate && region && (
                      <Alert className="py-2">
                        <AlertTriangle className="h-4 w-4" />
                        <AlertDescription className="text-xs">
                          Template region is <strong>{selectedTemplate.region}</strong>, but project will deploy to{' '}
                          <strong>{region}</strong>. Ensure credentials support multi-region operations.
                        </AlertDescription>
                      </Alert>
                    )}

                    {/* No template selected -- show region info */}
                    {!selectedTemplate && regionInfo && (
                      <p className="text-xs text-muted-foreground mt-1">
                        {regionInfo.flag} {regionInfo.label}
                      </p>
                    )}
                  </div>
                </div>
              </SectionCard>
            )}

            {/* Location -- for kubernetes (on-prem) projects */}
            {isK8sProject && (
              <SectionCard title="Location" compact>
                <div className="grid gap-2">
                  <Label htmlFor="location">Location (optional)</Label>
                  <Input
                    id="location"
                    {...register('region')}
                    placeholder="e.g. singapore, lab-rack-3, datacenter-east"
                  />
                  <p className="text-xs text-muted-foreground mt-1">
                    A descriptive location label for this deployment
                  </p>
                </div>
              </SectionCard>
            )}

            {/* State Backend (+ S3 config) — conditional on project type */}
            {projectType && (
              <SectionCard title="State Backend" compact>
                <div className="space-y-4">
                  <div className="grid gap-2">
                    <Label htmlFor="backend_type">State Backend</Label>
                    {isK8sProject ? (
                      <>
                        <Input value="Local (Container Volume)" disabled className="text-muted-foreground" />
                        <p className="text-xs text-muted-foreground mt-1">
                          Kubernetes projects use local state storage
                        </p>
                      </>
                    ) : (
                      <>
                        <Controller
                          control={control}
                          name="backend_type"
                          render={({ field }) => (
                            <Select
                              value={field.value || 'local'}
                              onValueChange={(value) => {
                                field.onChange(value);
                                if (value === 'local') {
                                  setValue('state_storage_provider', '');
                                  setValue('s3_state_bucket', undefined);
                                  setValue('s3_state_key_prefix', undefined);
                                  setValue('s3_state_region', undefined);
                                  setValue('s3_endpoint', undefined);
                                } else if (projectType === 'cloud-ibm') {
                                  setValue('state_storage_provider', 'ibm');
                                } else {
                                  setValue('state_storage_provider', 'aws');
                                }
                              }}
                            >
                              <SelectTrigger>
                                <SelectValue placeholder="Select backend type" />
                              </SelectTrigger>
                              <SelectContent>
                                <SelectItem value="local">Local (Container Volume)</SelectItem>
                                <SelectItem value="s3">{projectType === 'cloud-ibm' ? 'IBM Cloud COS' : 'S3 (AWS Remote State)'}</SelectItem>
                              </SelectContent>
                            </Select>
                          )}
                        />
                        <p className="text-xs text-muted-foreground mt-1">
                          {backendType === 's3'
                            ? projectType === 'cloud-ibm'
                              ? 'State stored in IBM Cloud Object Storage using the COS instance selected in the credential template.'
                              : 'State stored in AWS S3 with native locking (no DynamoDB required)'
                            : 'State stored locally in container persistent volume (default)'}
                        </p>
                      </>
                    )}
                  </div>

                  {/* S3 Backend Configuration */}
                  {backendType === 's3' && isCloudProject && (
                    <>
                      <div className="grid gap-2">
                        <Label htmlFor="s3_state_bucket">{projectType === 'cloud-ibm' ? 'IBM COS Bucket Name *' : 'S3 Bucket Name *'}</Label>
                        <Input
                          id="s3_state_bucket"
                          {...register('s3_state_bucket')}
                          placeholder="my-terraform-state-bucket"
                        />
                        {errors.s3_state_bucket && (
                          <p className="text-xs text-destructive mt-1">{errors.s3_state_bucket.message}</p>
                        )}
                        <p className="text-xs text-muted-foreground mt-1">
                          {projectType === 'cloud-ibm'
                            ? 'IBM Cloud Object Storage bucket for storing state files. BNK-Forge can create the bucket in the selected COS instance if needed.'
                            : 'S3 bucket for storing state files (must exist)'}
                        </p>
                      </div>

                      <div className="grid gap-2">
                        <Label htmlFor="s3_state_region">{projectType === 'cloud-ibm' ? 'COS Bucket Region' : 'S3 Bucket Region'}</Label>
                        <Controller
                          control={control}
                          name="s3_state_region"
                          render={({ field }) => (
                            projectType === 'cloud-ibm' ? (
                              <CloudRegionSelector
                                provider="ibm"
                                value={field.value || region || ''}
                                onValueChange={field.onChange}
                                options={ibmRegions}
                              />
                            ) : (
                              <RegionSelector
                                value={field.value || region || ''}
                                onValueChange={field.onChange}
                              />
                            )
                          )}
                        />
                        <p className="text-xs text-muted-foreground mt-1">
                          {projectType === 'cloud-ibm'
                            ? 'Region where the COS bucket is located. Defaults to the project region.'
                            : 'Region where S3 bucket is located (defaults to project region)'}
                        </p>
                      </div>

                      {projectType === 'cloud-ibm' && (
                        <div className="grid gap-2">
                          <Label htmlFor="s3_endpoint">COS Endpoint (Optional Override)</Label>
                          <Input
                            id="s3_endpoint"
                            {...register('s3_endpoint')}
                            placeholder="https://s3.us-south.cloud-object-storage.appdomain.cloud"
                          />
                          <p className="text-xs text-muted-foreground mt-1">
                            Leave blank to use the standard IBM COS endpoint for the selected region.
                          </p>
                        </div>
                      )}

                      <div className="grid gap-2">
                        <Label htmlFor="s3_state_key_prefix">Key Prefix (Optional)</Label>
                        <Input
                          id="s3_state_key_prefix"
                          {...register('s3_state_key_prefix')}
                          placeholder="terraform-state/"
                        />
                        <p className="text-xs text-muted-foreground mt-1">
                          Optional prefix for state file keys (e.g., "prod/", "staging/")
                        </p>
                      </div>

                      <Alert className="border-info/30 bg-info/10">
                        <CheckCircle className="h-4 w-4 text-info" />
                        <AlertDescription className="text-xs text-foreground/80">
                          <strong>{projectType === 'cloud-ibm' ? 'IBM COS Remote State:' : 'Native S3 Locking Enabled:'}</strong>{' '}
                          {projectType === 'cloud-ibm'
                            ? 'Uses the OpenTofu S3-compatible backend against IBM Cloud Object Storage. BNK-Forge derives the required COS access from the selected IBM credential template and COS instance.'
                            : 'Uses OpenTofu 1.10+ native locking with S3 conditional writes. No DynamoDB table required!'}
                        </AlertDescription>
                      </Alert>
                    </>
                  )}
                </div>
              </SectionCard>
            )}

            {/* Appearance — color */}
            <SectionCard title="Appearance" compact>
              <div className="grid gap-2">
                <Label>Color</Label>
                <Popover>
                  <PopoverTrigger asChild>
                    <Button variant="outline" className="w-full justify-start gap-2 h-10">
                      <div
                        className="h-5 w-5 rounded-full border border-border shadow-sm"
                        style={{ backgroundColor: color }}
                      />
                      <span className="text-sm text-muted-foreground">{color}</span>
                      <Palette className="ml-auto h-4 w-4 text-muted-foreground" />
                    </Button>
                  </PopoverTrigger>
                  <PopoverContent className="w-64" align="start">
                    <div className="grid grid-cols-7 gap-1.5">
                      {[
                        '#ef4444', '#f97316', '#f59e0b', '#eab308', '#84cc16', '#22c55e', '#10b981',
                        '#14b8a6', '#06b6d4', '#0ea5e9', '#3b82f6', '#6366f1', '#8b5cf6', '#a855f7',
                        '#d946ef', '#ec4899', '#f43f5e', '#78716c', '#64748b', '#475569', '#1e293b',
                      ].map((preset) => (
                        <button
                          key={preset}
                          type="button"
                          className={cn(
                            'h-7 w-7 rounded-full border-2 transition-all hover:scale-110',
                            color === preset ? 'border-foreground scale-110' : 'border-transparent'
                          )}
                          style={{ backgroundColor: preset }}
                          onClick={() => setValue('color', preset)}
                        />
                      ))}
                    </div>
                    <div className="flex gap-2 mt-3 pt-3 border-t border-border">
                      <div
                        className="h-9 w-9 rounded-md border border-border shadow-sm shrink-0"
                        style={{ backgroundColor: color }}
                      />
                      <Input
                        value={color}
                        onChange={(e) => setValue('color', e.target.value)}
                        placeholder="#2563eb"
                        className="h-9 font-mono text-xs"
                        aria-label="Color hex code"
                      />
                    </div>
                  </PopoverContent>
                </Popover>
              </div>
            </SectionCard>
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => handleOpenChange(false)}
              disabled={createMutation.isPending}
            >
              Cancel
            </Button>
            <LoadingButton
              type="submit"
              loading={createMutation.isPending}
              loadingText="Creating..."
              disabled={!projectType}
            >
              Create Project
            </LoadingButton>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
