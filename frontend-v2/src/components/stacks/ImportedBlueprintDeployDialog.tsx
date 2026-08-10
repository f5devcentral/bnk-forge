import { useCallback, useEffect, useMemo, useState } from 'react';
import { AlertCircle, ArrowRight, CheckCircle2, ChevronDown, Clock, DollarSign, Key, Loader2, Package } from 'lucide-react';
import { SectionCard } from '@/components/ui/section-card';
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog';
import { VisuallyHidden } from '@radix-ui/react-visually-hidden';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Separator } from '@/components/ui/separator';
import { useStackRequiredInputs, useStackTemplate } from '@/hooks/useStacks';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { cn } from '@/lib/utils';
import { api } from '@/lib/api';
import { notify } from '@/lib/notify';
import { parseApiError } from '@/lib/error-handler';
import { CloudRegionSelector } from '@/components/cloud/CloudRegionSelector';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import type { StackInputDefinition, StackTemplateModule, StackPrerequisite } from '@/types';
import type { Project } from '@/types';

type DeployMode = 'new' | 'existing';

interface ImportedBlueprintDeployDialogProps {
  slug: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSuccess?: (projectId: number) => void;
}

export function ImportedBlueprintDeployDialog({ slug, open, onOpenChange, onSuccess }: ImportedBlueprintDeployDialogProps) {
  const [deployMode, setDeployMode] = useState<DeployMode>('new');
  const [selectedProjectId, setSelectedProjectId] = useState<number | undefined>(undefined);
  const [projectName, setProjectName] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [values, setValues] = useState<Record<string, string>>({});
  const [credentialTemplateId, setCredentialTemplateId] = useState<number | undefined>(undefined);
  const [region, setRegion] = useState('');
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const queryClient = useQueryClient();

  // Clear any prior deploy error when the dialog is closed/reopened.
  useEffect(() => {
    if (!open) setSubmitError(null);
  }, [open]);

  const { data: template, isLoading: templateLoading } = useStackTemplate(slug);
  const { data: requiredInputs, isLoading: inputsLoading } = useStackRequiredInputs(slug);
  const { data: templates } = useQuery({
    queryKey: ['credential-templates', template?.cloud_provider || 'all'],
    queryFn: () => api.listCredentialTemplates(template?.cloud_provider || undefined),
    enabled: open,
  });

  const { data: ibmRegionsResponse } = useQuery({
    queryKey: ['imported-blueprint-ibm-regions', credentialTemplateId ?? 'none'],
    queryFn: () => api.listIBMRegions(credentialTemplateId),
    enabled: open && template?.cloud_provider === 'ibm',
  });

  const { data: projects } = useQuery({
    queryKey: ['projects'],
    queryFn: () => api.getProjects(),
    enabled: open,
  });

  const releaseId = useMemo(() => Number(slug.replace('release-', '')), [slug]);
  const allInputs = requiredInputs?.all_inputs || [];
  const requiredFields = allInputs.filter((input) => input.required && !input.hidden);
  const optionalFields = allInputs.filter((input) => !input.required && !input.hidden);
  const missingModuleEntries = useMemo(() => requiredInputs?.missing_modules ?? [], [requiredInputs?.missing_modules]);
  const missingModuleMessageByPath = useMemo(() => {
    const entries = new Map<string, string>();
    for (const item of missingModuleEntries) {
      if (item.path) {
        entries.set(item.path, item.message);
      }
    }
    return entries;
  }, [missingModuleEntries]);
  const missingRequiredModulePaths = useMemo(() => {
    // Backend contract: missing_modules only contains REQUIRED modules.
    const paths = new Set<string>(missingModuleEntries.map((item) => item.path));
    for (const module of template?.modules || []) {
      if (module.required && module.module_catalog_status === 'missing') {
        paths.add(module.path);
      }
    }
    return Array.from(paths);
  }, [missingModuleEntries, template?.modules]);
  const hasMissingRequiredModules = missingRequiredModulePaths.length > 0;
  const selectedTemplate = templates?.find((item) => item.id === credentialTemplateId);

  // Does this blueprint expect a credential template to be bound?
  // Check both resolved_from (backend-computed) and source (manifest-declared).
  const needsCredentialTemplate = allInputs.some(
    (input) => input.resolved_from === 'credential_template' || input.source === 'credential_template'
  );
  const credentialTemplateMissing = needsCredentialTemplate && !credentialTemplateId;
  const ibmRegions = ibmRegionsResponse?.regions || [];

  // Detect whether the blueprint requires a kubernetes_cluster prerequisite.
  const requiresCluster = useMemo(
    () => (template?.prerequisites || []).some((p: StackPrerequisite) => p.type === 'kubernetes_cluster'),
    [template],
  );

  // Projects that satisfy the cluster prerequisite.
  const eligibleProjects = useMemo<Project[]>(() => {
    if (!projects) return [];
    if (requiresCluster) return projects.filter((p) => (p.cluster_count ?? 0) > 0);
    return projects;
  }, [projects, requiresCluster]);

  // Reset mode and project selection when the dialog closes.
  useEffect(() => {
    if (!open) {
      setDeployMode('new');
      setSelectedProjectId(undefined);
    }
  }, [open]);

  // When the template loads and requires a cluster, default to existing mode.
  useEffect(() => {
    if (!open || !template) return;
    if (requiresCluster) {
      setDeployMode('existing');
    }
  }, [open, template, requiresCluster]);

  const resolveMappedValue = useCallback((input: StackInputDefinition): string | undefined => {
    if (template?.cloud_provider !== 'ibm') {
      return undefined;
    }

    if (input.source === 'project' && input.source_field === 'region') {
      return region || selectedTemplate?.region || undefined;
    }

    if (input.source === 'credential_template' && selectedTemplate) {
      if (input.source_field === 'ibmcloud_resource_group') {
        return selectedTemplate.ibmcloud_resource_group || undefined;
      }

      if (input.source_field === 'ibmcloud_api_key' && selectedTemplate.has_ibmcloud_api_key) {
        return '__inherited_from_template__';
      }

      if (input.source_field === 'region') {
        return selectedTemplate.region || undefined;
      }
    }

    return undefined;
  }, [region, selectedTemplate, template?.cloud_provider]);

  useEffect(() => {
    if (!open || !template?.cloud_provider || !['aws', 'gcp', 'azure', 'ibm'].includes(template.cloud_provider)) {
      return;
    }

    if (!credentialTemplateId) {
      const providerTemplates = (templates || []).filter((item) => item.provider === template.cloud_provider);
      const preferredTemplate = providerTemplates.find((item) => item.is_default) || providerTemplates[0];
      if (preferredTemplate) {
        setCredentialTemplateId(preferredTemplate.id);
        if (preferredTemplate.region) {
          setRegion(preferredTemplate.region);
        }
      }
      return;
    }

    if (selectedTemplate?.region && !region) {
      setRegion(selectedTemplate.region);
    }
  }, [credentialTemplateId, open, region, selectedTemplate, template?.cloud_provider, templates]);

  useEffect(() => {
    if (!open || template?.cloud_provider !== 'ibm') {
      return;
    }

    setValues((prev) => {
      const next = { ...prev };
      let changed = false;

      for (const input of allInputs) {
        const mappedValue = resolveMappedValue(input);
        const currentValue = next[input.name];
        const isDefaultValue = currentValue === undefined || currentValue === '' || currentValue === String(input.default ?? '');
        if (mappedValue !== undefined && isDefaultValue) {
          next[input.name] = mappedValue;
          changed = true;
        }
      }

      return changed ? next : prev;
    });
  }, [allInputs, open, resolveMappedValue, template?.cloud_provider]);

  // Auto-expand optional fields when a credential template is selected and any
  // optional field is sourced from it (e.g. ibmcloud_resource_group).
  useEffect(() => {
    if (!open || !credentialTemplateId) return;
    const hasCredentialOptional = optionalFields.some(
      (input) => input.source === 'credential_template' || input.resolved_from === 'credential_template'
    );
    if (hasCredentialOptional) {
      setAdvancedOpen(true);
    }
  }, [open, credentialTemplateId, optionalFields]);

  const formatInputLabel = (input: StackInputDefinition) => {
    if (input.description) {
      return input.description;
    }
    return input.name.replace(/_/g, ' ');
  };

  const renderInputField = (input: StackInputDefinition) => {
    const currentValue = values[input.name] ?? String(input.default ?? '');
    const label = formatInputLabel(input);
    const hasValidation = !!input.validation?.pattern;
    const inheritedFromTemplate = currentValue === '__inherited_from_template__';
    const hasOptions = input.options && input.options.length > 0;

    return (
      <div key={input.name} className="space-y-1">
        <Label htmlFor={`imported-input-${input.name}`} className="text-xs flex items-center gap-2">
          {label}
          {input.required && <span className="text-destructive">*</span>}
          {input.sensitive && (
            <Badge variant="outline" className="text-[10px] py-0">sensitive</Badge>
          )}
        </Label>
        {hasOptions ? (
          <Select
            value={inheritedFromTemplate ? '' : currentValue}
            onValueChange={(value) => setValues((prev) => ({ ...prev, [input.name]: value }))}
          >
            <SelectTrigger id={`imported-input-${input.name}`} className="h-8 text-sm">
              <SelectValue placeholder={`Select ${label}`} />
            </SelectTrigger>
            <SelectContent>
              {input.options?.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : (
          <Input
            id={`imported-input-${input.name}`}
            value={inheritedFromTemplate ? '' : currentValue}
            onChange={(e) => setValues((prev) => ({ ...prev, [input.name]: e.target.value }))}
            placeholder={inheritedFromTemplate ? 'Inherited from credential template' : input.example ? `Example: ${input.example}` : input.description}
            type={input.sensitive ? 'password' : 'text'}
            className="h-8 text-sm"
          />
        )}
        {inheritedFromTemplate ? <p className="text-xs text-muted-foreground">Inherited from the selected IBM Cloud Credential Template.</p> : null}
        {hasOptions && input.options?.find((opt) => opt.value === currentValue)?.description ? (
          <p className="text-xs text-muted-foreground">{input.options?.find((opt) => opt.value === currentValue)?.description}</p>
        ) : null}
        {hasValidation && input.example ? <p className="text-xs text-warning">Format: {input.example}</p> : null}
        {input.description && !hasValidation && !hasOptions ? <p className="text-xs text-muted-foreground">{input.description}</p> : null}
      </div>
    );
  };

  const handleSubmit = async () => {
    if (!template) return;

    if (hasMissingRequiredModules) {
      notify.error(
        'Blueprint modules missing from catalog',
        'Sync the catalog source that contains the missing modules, then retry.',
        { category: 'deployment' },
      );
      return;
    }

    if (!canSubmit) return;

    setSubmitting(true);
    setSubmitError(null);
    try {
      let result: { project_id: number; project_name: string; module_count: number; created_module_ids: number[] };

      if (deployMode === 'existing') {
        if (!selectedProjectId) return;
        result = await api.addImportedBlueprintToProject(selectedProjectId, releaseId, {
          variables: values,
        });
        // Invalidate the project list and the specific project detail so new modules appear.
        void queryClient.invalidateQueries({ queryKey: ['projects'] });
        void queryClient.invalidateQueries({ queryKey: ['project', selectedProjectId] });
        notify.success(
          `Blueprint added to project`,
          `${result.module_count} modules added from imported blueprint release.`,
          { category: 'deployment' },
        );
      } else {
        result = await api.createProjectFromImportedBlueprint(releaseId, {
          name: projectName.trim() || `${template.name} Project`,
          description: `${template.name} deployment from imported blueprint release`,
          project_type: ['aws', 'gcp', 'azure', 'ibm'].includes(template.cloud_provider ?? '')
            ? `cloud-${template.cloud_provider}`
            : 'kubernetes',
          cloud_provider: template.cloud_provider || undefined,
          environment: 'production',
          region: region || selectedTemplate?.region,
          credential_template_id: credentialTemplateId,
          backend_type: 'local',
          color: '#2563eb',
          icon: '',
          variables: values,
        });
        notify.success(
          `Project "${result.project_name}" created`,
          `${result.module_count} modules added from imported blueprint release.`,
          { category: 'deployment' },
        );
      }

      // Auto-run a dry-run plan for cli-bnkctl modules immediately (no real
      // provisioning — this is the "Zero cost (dry-run only)" path advertised
      // by the blueprint).
      // Check if any of the created modules are cli-bnkctl execution_engine
      const isClibnkctlRelease = template.source_kind === 'blueprint_release' &&
        template.modules?.some((m: StackTemplateModule) =>
          m.path && m.path.startsWith('cli-bnkctl/')
        );

      if (isClibnkctlRelease && result.created_module_ids.length > 0) {
        // Trigger a dry-run plan on the first (and typically only) cli-bnkctl module
        try {
          const moduleId = result.created_module_ids[0];
          await api.planModule(moduleId);
          notify.success(
            'Dry-run plan started',
            'The CLI module dry-run plan has been initiated. Check the project for progress.',
            { category: 'deployment' },
          );
        } catch (deployError) {
          const parsed = parseApiError(deployError);
          notify.error('Dry-run plan failed to start', parsed.message || String(deployError));
        }
      }

      onOpenChange(false);
      setProjectName('');
      setValues({});
      onSuccess?.(result.project_id);
    } catch (error) {
      // Use the structured backend error (e.g. "A project with this name
      // already exists.") rather than the bare axios "Request failed with
      // status code 409".
      const parsed = parseApiError(error);
      // Surface inline in the dialog (the bell alone is easy to miss while the
      // user is focused on the form), and still record it in the bell.
      setSubmitError(parsed.message || parsed.title || 'Imported blueprint deployment failed');
      notify.error(parsed.title || 'Imported blueprint deployment failed', parsed.message, { category: 'deployment' });
    } finally {
      setSubmitting(false);
    }
  };

  const canSubmit =
    deployMode === 'existing'
      ? !!selectedProjectId
      : !!projectName.trim() && !requiresCluster;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-4xl max-h-[85vh] p-0 gap-0 flex flex-col bg-card border-border">
        {templateLoading || inputsLoading ? (
          <>
            <VisuallyHidden>
              <DialogTitle>Loading Imported Blueprint</DialogTitle>
              <DialogDescription>Please wait while we load the imported blueprint details</DialogDescription>
            </VisuallyHidden>
            <div className="p-12 flex items-center justify-center">
              <Loader2 className="h-12 w-12 animate-spin text-primary" />
            </div>
          </>
        ) : template ? (
          <>
            <div className="p-6 border-b border-border">
              <div className="flex items-start gap-4">
                <div className="flex-1">
                  <div className="flex items-center gap-2 mb-2">
                    <DialogTitle className="text-xl font-bold text-foreground">{template.name}</DialogTitle>
                    <Badge variant="outline" className="text-[10px] uppercase tracking-wide">
                      Imported Blueprint
                    </Badge>
                    {template.maturity && (
                      <Badge variant="outline" className="text-[10px]">
                        {template.maturity}
                      </Badge>
                    )}
                  </div>
                  <DialogDescription className="text-sm text-muted-foreground">
                    {template.description}
                  </DialogDescription>
                  {template.source_path ? (
                    <p className="text-[11px] text-muted-foreground mt-1">Source: {template.source_path}</p>
                  ) : null}
                </div>
                <div className="flex items-center gap-3 text-muted-foreground text-xs">
                  {template.estimated_time && (
                    <div className="flex items-center gap-1">
                      <Clock className="h-3 w-3" />
                      <span>{template.estimated_time}</span>
                    </div>
                  )}
                  {template.estimated_cost && (
                    <div className="flex items-center gap-1">
                      <DollarSign className="h-3 w-3" />
                      <span>{template.estimated_cost}</span>
                    </div>
                  )}
                </div>
              </div>
            </div>

            <ScrollArea className="flex-1 overflow-auto">
              <div className="p-6 space-y-6">
                {template.outcomes && template.outcomes.length > 0 && (
                  <SectionCard title="What You Get">
                    <div className="space-y-2">
                      {template.outcomes.map((outcome, idx) => (
                        <div key={idx} className="flex items-start gap-2">
                          <CheckCircle2 className="h-4 w-4 mt-0.5 flex-shrink-0 text-success" />
                          <span className="text-sm text-foreground/80">{outcome}</span>
                        </div>
                      ))}
                    </div>
                  </SectionCard>
                )}

                {template.prerequisites && template.prerequisites.length > 0 && (
                  <SectionCard title="Prerequisites">
                    <div className="space-y-2">
                      {template.prerequisites.map((prereq: StackPrerequisite, idx: number) => {
                        const isSecret = prereq.type === 'project_secret';
                        return (
                          <div key={idx} className="p-4 rounded-lg border border-border bg-muted/30">
                            <div className="flex items-center gap-2 mb-1">
                              {isSecret ? <Key className="h-4 w-4 text-muted-foreground" /> : null}
                              <p className="text-sm font-medium text-foreground">
                                {isSecret && prereq.name ? prereq.name : prereq.type}
                              </p>
                            </div>
                            <p className="text-sm text-muted-foreground">
                              {prereq.description}
                            </p>
                          </div>
                        );
                      })}
                    </div>
                  </SectionCard>
                )}

                {requiredInputs?.summary && requiredInputs.summary.length > 0 && (
                  <SectionCard title="Input Notes">
                    <div className="space-y-2">
                      {requiredInputs.summary.map((item, idx) => (
                        <div key={`${item.label}-${idx}`} className="p-4 rounded-lg border border-border bg-muted/30">
                          <p className="text-sm font-medium mb-1 text-foreground">{item.label}</p>
                          <p className="text-sm text-muted-foreground">{item.value}</p>
                        </div>
                      ))}
                    </div>
                  </SectionCard>
                )}

                <SectionCard title={`Modules (${template.modules?.length || 0})`}>
                  {hasMissingRequiredModules ? (
                    <Alert className="mb-3 border-warning/50 bg-warning/5">
                      <AlertCircle className="h-4 w-4 text-warning" />
                      <AlertDescription>
                        Required modules are missing from the active catalog. Sync the catalog source, then retry deployment.{' '}
                        Missing paths: {missingRequiredModulePaths.join(', ')}.
                      </AlertDescription>
                    </Alert>
                  ) : null}
                  <div className="space-y-2">
                    {template.modules?.map((module: StackTemplateModule, idx: number) => {
                      const isMissing = missingModuleMessageByPath.has(module.path) || module.module_catalog_status === 'missing';
                      const missingMessage = missingModuleMessageByPath.get(module.path) || module.module_catalog_message;

                      return (
                      <div
                        key={idx}
                        className={cn(
                          'flex items-start gap-4 p-4 rounded-lg border transition-colors',
                          isMissing ? 'border-warning/50 bg-warning/5' : 'border-border bg-muted/30',
                        )}
                      >
                        <div className="flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold bg-primary/10 text-primary border border-primary/20">
                          {idx + 1}
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-start justify-between gap-2 mb-1">
                            <h4 className="font-semibold text-foreground">{module.name}</h4>
                            {isMissing ? (
                              <Badge variant="warning" className="text-xs">Missing from catalog</Badge>
                            ) : (
                              <Badge variant={module.required ? 'success' : 'outline'} className="text-xs">
                                {module.required ? 'Required' : 'Optional'}
                              </Badge>
                            )}
                          </div>
                          <p className="text-sm mb-2 text-muted-foreground">
                            {module.path}
                            {module.version ? (
                              <span className="ml-2 font-mono text-xs text-foreground/70">@{module.version}</span>
                            ) : null}
                          </p>
                          {isMissing && missingMessage ? (
                            <p className="text-sm mb-2 text-warning">{missingMessage}</p>
                          ) : null}
                          {module.description ? <p className="text-sm text-muted-foreground">{module.description}</p> : null}
                        </div>
                        <Package className="h-5 w-5 flex-shrink-0 text-muted-foreground" />
                      </div>
                      );
                    })}
                  </div>
                </SectionCard>

                <Separator />

                <form
                  className="p-1 space-y-4"
                  onSubmit={(event) => {
                    event.preventDefault();
                    void handleSubmit();
                  }}
                >
                  {/* Deploy mode selector */}
                  <div className="space-y-1.5">
                    <Label className="text-xs font-semibold">Deployment Target</Label>
                    <div className="flex gap-2">
                      <Button
                        type="button"
                        variant={deployMode === 'new' ? 'default' : 'outline'}
                        size="sm"
                        className="h-8 text-xs"
                        onClick={() => setDeployMode('new')}
                      >
                        New Project
                      </Button>
                      <Button
                        type="button"
                        variant={deployMode === 'existing' ? 'default' : 'outline'}
                        size="sm"
                        className="h-8 text-xs"
                        onClick={() => setDeployMode('existing')}
                      >
                        Existing Project
                      </Button>
                    </div>
                    {requiresCluster && deployMode === 'new' && (
                      <p className="text-xs text-warning">
                        This blueprint requires a registered Kubernetes cluster. Add it to an existing project that already has a cluster registered.
                      </p>
                    )}
                  </div>

                  {deployMode === 'existing' ? (
                    <div className="space-y-1.5">
                      <Label htmlFor="imported-blueprint-existing-project" className="text-xs">Target Project *</Label>
                      <Select
                        value={selectedProjectId ? String(selectedProjectId) : ''}
                        onValueChange={(value) => setSelectedProjectId(Number(value))}
                      >
                        <SelectTrigger id="imported-blueprint-existing-project" className="h-9">
                          <SelectValue placeholder={
                            requiresCluster
                              ? 'Select a project with a registered cluster'
                              : 'Select an existing project'
                          } />
                        </SelectTrigger>
                        <SelectContent>
                          {eligibleProjects.map((project) => (
                            <SelectItem key={project.id} value={String(project.id)}>
                              {project.name}
                              {project.cluster_count ? ` (${project.cluster_count} cluster${project.cluster_count !== 1 ? 's' : ''})` : ''}
                            </SelectItem>
                          ))}
                          {eligibleProjects.length === 0 && (
                            <div className="py-2 px-3 text-sm text-muted-foreground">
                              {requiresCluster ? 'No projects with registered clusters' : 'No projects available'}
                            </div>
                          )}
                        </SelectContent>
                      </Select>
                      {requiresCluster && (
                        <p className="text-xs text-muted-foreground">
                          Only projects with at least one registered Kubernetes cluster are shown.
                        </p>
                      )}
                    </div>
                  ) : (
                    <div className="space-y-1.5">
                      <Label htmlFor="imported-blueprint-project-name" className="text-xs">Project Name *</Label>
                      <Input
                        id="imported-blueprint-project-name"
                        value={projectName}
                        onChange={(e) => setProjectName(e.target.value)}
                        placeholder={template ? `${template.name} Project` : 'Project name'}
                        className="h-9"
                      />
                    </div>
                  )}

                  {deployMode === 'new' && needsCredentialTemplate ? (
                    <div className="grid gap-4 pt-2 border-t border-dashed md:grid-cols-2">
                      <div className="space-y-1.5">
                        <Label htmlFor="imported-blueprint-credential-template" className="text-xs">
                          Access Method (Credential Template)
                        </Label>
                        <Select
                          value={credentialTemplateId ? String(credentialTemplateId) : 'none'}
                          onValueChange={(value) => {
                            const nextId = value === 'none' ? undefined : Number(value);
                            const nextTemplate = templates?.find((item) => item.id === nextId);
                            setCredentialTemplateId(nextId);
                            if (nextTemplate?.region) {
                              setRegion(nextTemplate.region);
                            }
                          }}
                        >
                          <SelectTrigger id="imported-blueprint-credential-template" className="h-9">
                            <SelectValue placeholder="Select credential template" />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="none">No template</SelectItem>
                            {(templates || [])
                              .filter((item) => !template.cloud_provider || item.provider === template.cloud_provider)
                              .map((item) => (
                                <SelectItem key={item.id} value={String(item.id)}>
                                  {item.name}
                                </SelectItem>
                              ))}
                          </SelectContent>
                        </Select>
                        {credentialTemplateMissing && (templates || []).filter((item) => !template.cloud_provider || item.provider === template.cloud_provider).length === 0 && (
                          <p className="text-xs text-warning">
                            No {template.cloud_provider?.toUpperCase() || 'cloud'} credential templates found.{' '}
                            <a href="/settings/credential-templates" className="underline">Create one in Settings</a> before deploying.
                          </p>
                        )}
                      </div>

                      {(['aws', 'gcp', 'azure', 'ibm'].includes(template.cloud_provider ?? '')) && (
                        <div className="space-y-1.5">
                          <Label className="text-xs">
                            {template.cloud_provider === 'ibm' ? 'IBM Cloud Region' : 'Region'}
                          </Label>
                          <CloudRegionSelector
                            provider={template.cloud_provider ?? ''}
                            value={region}
                            onValueChange={setRegion}
                            options={ibmRegions}
                          />
                        </div>
                      )}
                    </div>
                  ) : null}

                  {credentialTemplateMissing && (
                    <Alert className="border-warning">
                      <AlertCircle className="h-4 w-4 text-warning" />
                      <AlertDescription>
                        This blueprint requires an access method (credential template) to supply cloud credentials automatically.
                        Select or create one above before deploying.
                      </AlertDescription>
                    </Alert>
                  )}

                  {(requiredFields.length > 0 || optionalFields.length > 0) && (
                    <div className="space-y-3 pt-2 border-t border-dashed">
                      <Label className="text-xs font-semibold">Input Variables</Label>
                      <div className="p-3 rounded-lg border border-border bg-muted/50 space-y-4">
                        {requiredFields.length > 0 && (
                          <div className="space-y-3">
                            <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                              Required
                            </h4>
                            {requiredFields.map((input) => renderInputField(input))}
                          </div>
                        )}
                        {optionalFields.length > 0 && (
                          <div className="space-y-3">
                            <button
                              type="button"
                              onClick={() => setAdvancedOpen((prev) => !prev)}
                              className="flex items-center gap-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground hover:text-foreground transition-colors"
                            >
                              <ChevronDown className={cn('h-3 w-3 transition-transform', advancedOpen && 'rotate-180')} />
                              Advanced options ({optionalFields.length})
                            </button>
                            {advancedOpen && (
                              <div className="space-y-3">
                                {optionalFields.map((input) => renderInputField(input))}
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    </div>
                  )}

                  {submitError && (
                    <Alert variant="destructive">
                      <AlertCircle className="h-4 w-4" />
                      <AlertDescription>{submitError}</AlertDescription>
                    </Alert>
                  )}

                  <div className="flex gap-2 pt-2">
                    <Button
                      type="submit"
                      disabled={submitting || templateLoading || inputsLoading || !canSubmit || credentialTemplateMissing || hasMissingRequiredModules}
                      className={cn('flex-1 h-9 font-semibold text-sm')}
                    >
                      {submitting ? (
                        <>
                          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                          {deployMode === 'existing' ? 'Adding...' : 'Deploying...'}
                        </>
                      ) : (
                        <>
                          {deployMode === 'existing' ? 'Add to Project' : 'Deploy Blueprint'}
                          <ArrowRight className="ml-2 h-3 w-3" />
                        </>
                      )}
                    </Button>
                    <Button type="button" variant="outline" size="sm" onClick={() => onOpenChange(false)} disabled={submitting} className="h-9">
                      Cancel
                    </Button>
                  </div>
                </form>
              </div>
            </ScrollArea>
          </>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
