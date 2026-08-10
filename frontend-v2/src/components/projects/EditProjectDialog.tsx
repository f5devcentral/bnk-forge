import { useEffect } from 'react';
import { z } from 'zod';
import { useForm } from 'react-hook-form';
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
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { useUpdateProject } from '@/hooks/useProjects';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { queryKeys } from '@/lib/queryKeys';
import type { Project, ProjectUpdateData, ProjectType } from '@/types';
import { Loader2, Terminal } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { CloudRegionSelector } from '@/components/cloud/CloudRegionSelector';
import type { CloudRegionOption } from '@/types';

interface EditProjectDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  project: Project | null;
}

const PROJECT_TYPE_LABELS: Record<string, { label: string; icon: string }> = {
  'cloud-aws': { label: 'AWS Cloud', icon: '☁️' },
  'cloud-azure': { label: 'Azure Cloud', icon: '⛅' },
  'cloud-gcp': { label: 'GCP Cloud', icon: '🌤️' },
  'cloud-ibm': { label: 'IBM Cloud', icon: '🟦' },
  'kubernetes': { label: 'On-Premises (Bare Metal / VM)', icon: '🖥️' },
  'bare-metal': { label: 'Bare Metal', icon: '🔧' },
};

const editProjectSchema = z.object({
  description: z.string().optional().default(''),
  color: z.string().default('#2563eb'),
  icon: z.string().optional().default(''),
  credential_template_id: z.number().optional(),
  ssh_credential_id: z.number().optional(),
  project_type: z
    .enum(['cloud-aws', 'cloud-azure', 'cloud-gcp', 'cloud-ibm', 'kubernetes', 'bare-metal'])
    .optional(),
  region: z.string().optional(),
  backend_type: z.string().optional(),
});

type EditProjectFormData = z.infer<typeof editProjectSchema>;

export function EditProjectDialog({ open, onOpenChange, project }: EditProjectDialogProps) {
  const updateMutation = useUpdateProject();

  const form = useForm<EditProjectFormData>({
    resolver: zodResolver(editProjectSchema),
    defaultValues: {
      description: '',
      color: '#2563eb',
      icon: '',
      credential_template_id: undefined,
      ssh_credential_id: undefined,
      project_type: undefined,
      region: undefined,
      backend_type: undefined,
    },
  });

  const isK8sProject = project?.project_type === 'kubernetes';
  const isBareMetalProject = project?.project_type === 'bare-metal';
  const isCloudProject = project?.project_type?.startsWith('cloud-');

  const { data: templates, isLoading: templatesLoading } = useQuery({
    queryKey: ['credential-templates'],
    queryFn: () => api.listCredentialTemplates(),
    enabled: open && isCloudProject,
  });

  const { data: sshCredentials, isLoading: sshCredsLoading } = useQuery({
    queryKey: queryKeys.sshCredentials.list(),
    queryFn: () => api.listSSHCredentials(),
    enabled: open && (isK8sProject || isBareMetalProject),
  });

  // Reset form whenever a new project is loaded into the dialog.
  useEffect(() => {
    if (project) {
      form.reset({
        description: project.description || '',
        color: project.color || '#2563eb',
        icon: project.icon || '',
        credential_template_id: project.credential_template_id,
        ssh_credential_id: project.ssh_credential_id,
        project_type: project.project_type as ProjectType | undefined,
        region: project.region,
        backend_type: project.backend_type,
      });
    }
  }, [project, form]);

  const credentialTemplateId = form.watch('credential_template_id');

  const { data: ibmRegionsResponse, isLoading: ibmRegionsLoading } = useQuery({
    queryKey: ['edit-ibm-regions', credentialTemplateId ?? 'default'],
    queryFn: () => api.listIBMRegions(credentialTemplateId),
    enabled: open && project?.project_type === 'cloud-ibm',
  });

  const ibmRegions: CloudRegionOption[] = ibmRegionsResponse?.regions || [];

  const onSubmit = async (values: EditProjectFormData) => {
    if (!project) return;

    const stateConfig =
      values.backend_type === 's3'
        ? {
            state_storage_provider: project.project_type === 'cloud-ibm' ? 'ibm' : 'aws',
            s3_state_bucket: project.s3_state_bucket,
            s3_state_key_prefix: project.s3_state_key_prefix,
            s3_state_region: project.s3_state_region,
            s3_use_native_locking: project.s3_use_native_locking,
            s3_endpoint: project.s3_endpoint,
          }
        : undefined;

    const payload: ProjectUpdateData = {
      description: values.description,
      color: values.color,
      icon: values.icon,
      credential_template_id: values.credential_template_id,
      ssh_credential_id: values.ssh_credential_id,
      project_type: values.project_type,
      region: values.region,
      backend_type: values.backend_type,
      state_config: stateConfig,
    };

    try {
      await updateMutation.mutateAsync({ id: project.id, data: payload });
      onOpenChange(false);
    } catch {
      // Error toast handled by useAppMutation default.
    }
  };

  if (!project) return null;

  const projectTypeInfo = project.project_type ? PROJECT_TYPE_LABELS[project.project_type] : null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[525px]">
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} noValidate>
            <DialogHeader>
              <DialogTitle>Edit Project</DialogTitle>
              <DialogDescription>
                Update project settings for {project.name}
              </DialogDescription>
            </DialogHeader>
            <div className="grid gap-4 py-4">
              {/* Project name is read-only — render as a plain disabled input */}
              <div className="grid gap-2">
                <Label htmlFor="project-name-readonly">Project Name</Label>
                <Input
                  id="project-name-readonly"
                  value={project.name}
                  disabled
                  className="bg-muted"
                />
                <p className="text-xs text-muted-foreground">
                  Project name cannot be changed
                </p>
              </div>

              {/* Project Type — editable if not already set, read-only display if set */}
              {projectTypeInfo ? (
                <div className="grid gap-2">
                  <Label>Project Type</Label>
                  <div className="flex items-center gap-2">
                    <Badge variant="outline" className="text-sm py-1 px-3">
                      {projectTypeInfo.icon} {projectTypeInfo.label}
                    </Badge>
                  </div>
                </div>
              ) : (
                <FormField
                  control={form.control}
                  name="project_type"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Project Type</FormLabel>
                      <Select
                        value={field.value || ''}
                        onValueChange={(value) => field.onChange(value as ProjectType)}
                      >
                        <FormControl>
                          <SelectTrigger>
                            <SelectValue placeholder="Select project type" />
                          </SelectTrigger>
                        </FormControl>
                        <SelectContent>
                          <SelectItem value="cloud-aws">☁️ AWS Cloud</SelectItem>
                          <SelectItem value="cloud-azure">⛅ Azure Cloud</SelectItem>
                          <SelectItem value="cloud-gcp">🌤️ GCP Cloud</SelectItem>
                          <SelectItem value="cloud-ibm">🟦 IBM Cloud</SelectItem>
                          <SelectItem value="kubernetes">🖥️ On-Premises (Bare Metal / VM)</SelectItem>
                        </SelectContent>
                      </Select>
                      <FormDescription>
                        Set the project type to enable type-specific features
                      </FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              )}

              <FormField
                control={form.control}
                name="description"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Description</FormLabel>
                    <FormControl>
                      <Textarea
                        placeholder="Brief description of this project"
                        rows={3}
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              {/* SSH Credentials — for K8s and bare-metal projects */}
              {(isK8sProject || isBareMetalProject) && (
                <FormField
                  control={form.control}
                  name="ssh_credential_id"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>SSH Credentials</FormLabel>
                      {sshCredsLoading ? (
                        <div className="flex items-center gap-2 text-sm text-muted-foreground">
                          <Loader2 className="h-4 w-4 animate-spin" />
                          Loading SSH credentials...
                        </div>
                      ) : (
                        <Select
                          value={field.value?.toString() || 'none'}
                          onValueChange={(value) =>
                            field.onChange(value === 'none' ? undefined : parseInt(value))
                          }
                        >
                          <FormControl>
                            <SelectTrigger>
                              <SelectValue placeholder="Select SSH credentials (optional)" />
                            </SelectTrigger>
                          </FormControl>
                          <SelectContent>
                            <SelectItem value="none">No SSH credentials (direct kubeconfig)</SelectItem>
                            {sshCredentials && sshCredentials.length > 0 ? (
                              sshCredentials.map((cred) => (
                                <SelectItem key={cred.id} value={cred.id.toString()}>
                                  <span className="flex items-center gap-2">
                                    <Terminal className="h-3 w-3" />
                                    {cred.name}
                                    <span className="text-muted-foreground text-xs">
                                      ({cred.username}@{cred.host})
                                    </span>
                                  </span>
                                </SelectItem>
                              ))
                            ) : (
                              <SelectItem value="create-hint" disabled>
                                No SSH credentials — create one in Settings
                              </SelectItem>
                            )}
                          </SelectContent>
                        </Select>
                      )}
                      <FormDescription>
                        {isK8sProject
                          ? 'SSH credentials for tunnel access to on-prem clusters'
                          : 'Project-level jumphost inherited by all bare-metal hosts (can be overridden per host)'}
                      </FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              )}

              {/* Cloud Credentials — for cloud projects */}
              {isCloudProject && (
                <FormField
                  control={form.control}
                  name="credential_template_id"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Cloud Credentials</FormLabel>
                      {templatesLoading ? (
                        <div className="flex items-center gap-2 text-sm text-muted-foreground">
                          <Loader2 className="h-4 w-4 animate-spin" />
                          Loading templates...
                        </div>
                      ) : (
                        <Select
                          value={field.value?.toString() || 'none'}
                          onValueChange={(value) =>
                            field.onChange(value === 'none' ? undefined : parseInt(value))
                          }
                        >
                          <FormControl>
                            <SelectTrigger>
                              <SelectValue placeholder="Use global credentials" />
                            </SelectTrigger>
                          </FormControl>
                          <SelectContent>
                            <SelectItem value="none">Use global credentials</SelectItem>
                            {templates && templates.length > 0 ? (
                              templates
                                .filter((t) => t.provider !== 'ssh')
                                .map((template) => (
                                  <SelectItem key={template.id} value={template.id.toString()}>
                                    ☁️ {template.name} ({template.provider.toUpperCase()})
                                    {template.is_default && ' (Default)'}
                                  </SelectItem>
                                ))
                            ) : (
                              <SelectItem value="none" disabled>
                                No templates available - create one in Settings
                              </SelectItem>
                            )}
                          </SelectContent>
                        </Select>
                      )}
                      <FormDescription>
                        Current: {project.credential_template?.name || 'Global credentials'}
                      </FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              )}

              {isCloudProject && (
                <FormField
                  control={form.control}
                  name="backend_type"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>State Backend</FormLabel>
                      <Select
                        value={field.value || 'local'}
                        onValueChange={field.onChange}
                      >
                        <FormControl>
                          <SelectTrigger>
                            <SelectValue placeholder="Select state backend" />
                          </SelectTrigger>
                        </FormControl>
                        <SelectContent>
                          <SelectItem value="local">Local (Container Volume)</SelectItem>
                          <SelectItem value="s3">
                            {project.project_type === 'cloud-ibm' ? 'IBM Cloud COS' : 'S3 (AWS Remote State)'}
                          </SelectItem>
                        </SelectContent>
                      </Select>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              )}

              {isCloudProject && (
                <FormField
                  control={form.control}
                  name="region"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Region</FormLabel>
                      {project.project_type === 'cloud-ibm' ? (
                        <CloudRegionSelector
                          provider="ibm"
                          value={field.value || ''}
                          onValueChange={field.onChange}
                          options={ibmRegions}
                          disabled={ibmRegionsLoading}
                        />
                      ) : (
                        <FormControl>
                          <Input placeholder="e.g., us-east-1" {...field} value={field.value || ''} />
                        </FormControl>
                      )}
                      <FormDescription>
                        {project.project_type === 'cloud-ibm'
                          ? 'Regions are loaded from the selected IBM credential template or the default IBM template.'
                          : 'Update the region used for this cloud project.'}
                      </FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              )}

              <FormField
                control={form.control}
                name="color"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Color</FormLabel>
                    <div className="flex gap-2">
                      <FormControl>
                        <Input
                          type="color"
                          className="w-20 h-10"
                          {...field}
                        />
                      </FormControl>
                      <Input
                        value={field.value}
                        onChange={(e) => field.onChange(e.target.value)}
                        placeholder="#2563eb"
                        className="flex-1"
                      />
                    </div>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => onOpenChange(false)}
                disabled={updateMutation.isPending}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={updateMutation.isPending}>
                {updateMutation.isPending ? (
                  <>
                    <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                    Updating...
                  </>
                ) : (
                  'Update Project'
                )}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
