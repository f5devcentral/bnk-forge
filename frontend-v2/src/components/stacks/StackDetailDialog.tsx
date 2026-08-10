/**
 * Stack Detail Dialog
 *
 * Shows full template details, prerequisites, modules, and deployment options.
 */

import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from '@/components/ui/dialog';
import { VisuallyHidden } from '@radix-ui/react-visually-hidden';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Separator } from '@/components/ui/separator';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { SectionCard } from '@/components/ui/section-card';
import { useStackTemplate, useStackPreview } from '@/hooks/useStacks';
import { useSyncModuleLibrary } from '@/hooks/useModules';
import { useBareMetalHosts } from '@/hooks/useBareMetal';
import { useQueryClient, useQuery, keepPreviousData } from '@tanstack/react-query';
import { stackKeys } from '@/hooks/useStacks';
import { isAxiosError } from 'axios';
import { api } from '@/lib/api';
import { cn } from '@/lib/utils';
import {
  Clock,
  DollarSign,
  AlertCircle,
  Package,
  ArrowRight,
  Server,
  Shield,
  Rocket,
  CheckCircle,
  CheckCircle2,
  AlertTriangle,
  FolderPlus,
  FolderOpen,
  Key,
  Target,
  Upload,
  ChevronDown,
  Loader2,
  RefreshCw,
} from 'lucide-react';
import type { StackTemplateModule, StackInputDefinition, StackPrerequisitesCheck, StackPrerequisite, Project, BareMetalHost } from '@/types';
import { notify } from '@/lib/notify';
import { startValueJourney } from '@/lib/value-journey';
import { CloudRegionSelector } from '@/components/cloud/CloudRegionSelector';
import { getRegionInfo } from '@/lib/aws-regions';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { getEnginePresentation, getLifecycleSummaryBadges } from '@/lib/module-engine';
import { getEligibleExistingClusterProjects } from './existing-cluster-projects';

interface StackDetailDialogProps {
  slug: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSuccess?: (projectId: number) => void;
}

const categoryConfig: Record<string, { icon: typeof Server }> = {
  infrastructure: { icon: Server },
  bnk: { icon: Shield },
  solution: { icon: Rocket },
  custom: { icon: Package },
  'bare-metal': { icon: Server },
};

const categoryLabels: Record<string, string> = {
  infrastructure: 'Infrastructure',
  bnk: 'Platform',
  solution: 'Solution',
  custom: 'Custom',
  'bare-metal': 'Bare Metal',
};

const categoryStageHelp: Record<string, string> = {
  infrastructure: 'Stage 1: create the target environment and cluster foundation.',
  bnk: 'Stage 2: install the BNK platform onto an existing cluster.',
  solution: 'Stage 3: deploy solution/application components on top of BNK.',
  custom: 'Custom blueprint composed from saved project modules.',
  'bare-metal': 'DPU & Bare Metal Infrastructure — deploys directly to physical hosts via SSH.',
};

const EXISTING_PROJECT_ONLY_TEMPLATE_SLUGS = new Set([
  'bnk-on-k8s',
  'f5-bnk-2.2',
  'ocp-connection',
  'ubuntu-kind-foundation',
  'bnk-bare-metal-full-poc',
  'bnk-bare-metal-dpu-infra',
]);

const EXISTING_CLUSTER_REQUIRED_TEMPLATE_SLUGS = new Set([
  'bnk-on-k8s',
  'f5-bnk-2.2',
  'ocp-connection',
]);

const CLOUD_PROVISIONING_TEMPLATE_PROVIDERS = new Set(['aws', 'azure', 'gcp', 'ibm']);

/** Returns true if the input should render the enhanced textarea+upload widget */
function isLargeContentInput(input: StackInputDefinition): boolean {
  const nameLower = input.name.toLowerCase();
  const typeLower = input.type.toLowerCase();
  if (typeLower === 'file' || typeLower === 'json') return true;
  const sensitiveNameMatch =
    nameLower.includes('secret') || nameLower.includes('key');
  if (input.sensitive === true && sensitiveNameMatch) return true;
  return (
    nameLower.includes('secret') ||
    nameLower.includes('pull_secret') ||
    nameLower.includes('json') ||
    nameLower.includes('key')
  );
}

/** Inline enhanced input: textarea + file upload for large/secret content */
function LargeContentInput({
  input,
  modulePath,
  currentValue,
  isAutoPopulated,
  error,
  hasValidation,
  onChange,
}: {
  input: StackInputDefinition;
  modulePath: string;
  currentValue: string;
  isAutoPopulated: boolean;
  error: string | undefined;
  hasValidation: boolean;
  onChange: (value: string) => void;
}) {
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (evt) => {
      const text = evt.target?.result;
      if (typeof text === 'string') {
        onChange(text);
      }
    };
    reader.readAsText(file);
    // Reset so the same file can be re-selected if needed
    e.target.value = '';
  };

  const charCount = currentValue.length;

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <Textarea
          id={`${modulePath}-${input.name}`}
          placeholder={input.example ? `Example: ${input.example}` : input.description}
          value={currentValue}
          rows={4}
          onChange={(e) => onChange(e.target.value)}
          className={cn(
            'flex-1 text-xs font-mono resize-y',
            isAutoPopulated && 'border-success/40',
            error && 'border-destructive'
          )}
        />
        <div className="flex flex-col gap-1 shrink-0">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => fileInputRef.current?.click()}
            className="h-8 px-2 text-xs"
          >
            <Upload className="h-3.5 w-3.5 mr-1" />
            Upload File
          </Button>
          {charCount > 0 && (
            <span className="text-[10px] text-muted-foreground text-right">
              {charCount.toLocaleString()} chars
            </span>
          )}
        </div>
      </div>
      {/* Hidden native file picker */}
      <input
        ref={fileInputRef}
        type="file"
        accept=".json,.txt,.pem,.key"
        className="hidden"
        onChange={handleFileChange}
      />
      {/* Validation hint */}
      {hasValidation && input.example && !error && (
        <p className="text-xs text-warning flex items-center gap-1">
          <AlertTriangle className="h-3 w-3" />
          Format: {input.example}
        </p>
      )}
      {/* Validation error */}
      {error && (
        <p className="text-xs text-destructive flex items-center gap-1">
          <AlertCircle className="h-3 w-3" />
          {error}
        </p>
      )}
      {/* Description (show if no error/hint) */}
      {input.description && !error && !hasValidation && (
        <p className="text-xs text-muted-foreground">{input.description}</p>
      )}
    </div>
  );
}

export function StackDetailDialog({ slug, open, onOpenChange, onSuccess }: StackDetailDialogProps) {
  // isDark removed — tokens handle theming (D-020)
  const navigate = useNavigate();
  const [deployMode, setDeployMode] = useState<'new' | 'existing'>('new');
  const [projectName, setProjectName] = useState('');
  const [selectedProjectId, setSelectedProjectId] = useState<number | undefined>(undefined);
  const [awsRegion, setAwsRegion] = useState('');
  const [credentialTemplateId, setCredentialTemplateId] = useState<number | undefined>(undefined);
  const [isDeploying, setIsDeploying] = useState(false);
  const [userInputs, setUserInputs] = useState<Record<string, Record<string, string>>>({});
  const [validationErrors, setValidationErrors] = useState<Record<string, string>>({});
  const [selectedBareMetalHostId, setSelectedBareMetalHostId] = useState<string>('');
  const [customInputMode, setCustomInputMode] = useState<Set<string>>(new Set());
  // Sections are expanded by default; this set holds paths the user has
  // explicitly collapsed. Defaulting to expanded means inherited values and
  // validation hints are visible on first open without requiring a click.
  const [collapsedModuleSections, setCollapsedModuleSections] = useState<Set<string>>(new Set());
  const [userPublicIpCidr, setUserPublicIpCidr] = useState<string>('');

  const toggleModuleSection = (path: string) => {
    setCollapsedModuleSections(prev => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };
  const queryClient = useQueryClient();
  const scrollAreaRef = useRef<HTMLDivElement>(null);

  // Radix ScrollArea preserves viewport scroll across re-mounts. Reset to the
  // top each time the dialog opens so the user sees the header first.
  useEffect(() => {
    if (!open) return;
    const id = requestAnimationFrame(() => {
      const viewport = scrollAreaRef.current?.querySelector<HTMLDivElement>(
        '[data-radix-scroll-area-viewport]'
      );
      if (viewport) viewport.scrollTop = 0;
    });
    return () => cancelAnimationFrame(id);
  }, [open]);

  // Auto-detect: fetch user's public IP when dialog opens (for `user_ip` /32 fields).
  // Falls back silently if the user's network blocks checkip.amazonaws.com.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    fetch('https://checkip.amazonaws.com', { signal: AbortSignal.timeout(3000) })
      .then((r) => (r.ok ? r.text() : Promise.reject()))
      .then((text) => {
        const ip = text.trim();
        if (!cancelled && /^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(ip)) {
          setUserPublicIpCidr(`${ip}/32`);
        }
      })
      .catch(() => {
        // Silent fallback — user types it themselves.
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  // Auto-detect: fetch AWS account_id via STS (cred-template /test endpoint) so
  // we can derive `ecr_registry` = <account_id>.dkr.ecr.<region>.amazonaws.com.
  // Cached per credential-template id; only fires when an AWS cred is picked.
  const { data: awsTestResult } = useQuery({
    queryKey: ['credential-template-test', credentialTemplateId],
    queryFn: () => api.testCredentialTemplate(credentialTemplateId!),
    enabled: !!credentialTemplateId && open,
    staleTime: 5 * 60 * 1000,
    retry: false,
  });
  const awsAccountId = awsTestResult?.success ? awsTestResult.account_id : undefined;
  const awsEcrRegistry = awsAccountId && awsRegion
    ? `${awsAccountId}.dkr.ecr.${awsRegion}.amazonaws.com`
    : undefined;

  // Validate a single input value against its validation rules
  const validateInput = (input: StackInputDefinition, value: string): string | null => {
    if (!value && input.required) {
      return 'This field is required';
    }
    if (!value) return null;
    
    if (input.validation?.pattern) {
      try {
        const regex = new RegExp(input.validation.pattern);
        if (!regex.test(value)) {
          return input.validation.error_message || 'Invalid format';
        }
      } catch {
        // Invalid regex pattern, skip validation
      }
    }
    return null;
  };

  // Handle input change with validation
  const handleInputChange = (modulePath: string, inputName: string, value: string, input: StackInputDefinition) => {
    setUserInputs(prev => ({
      ...prev,
      [modulePath]: {
        ...(prev[modulePath] || {}),
        [inputName]: value
      }
    }));
    
    // Validate and update error state
    const errorKey = `${modulePath}:${inputName}`;
    const error = validateInput(input, value);
    setValidationErrors(prev => {
      if (error) {
        return { ...prev, [errorKey]: error };
      }
      const { [errorKey]: __, ...rest } = prev;
      return rest;
    });
  };

  const { data: template, isLoading: loadingTemplate } = useStackTemplate(slug);
  const { data: preview } = useStackPreview(slug);
  
  // Fetch required inputs for this stack (with project context for pre-populating defaults)
  const { data: requiredInputs } = useQuery({
    queryKey: ['stack-required-inputs', slug, selectedProjectId, selectedBareMetalHostId],
    queryFn: () => api.getStackRequiredInputs(
      slug,
      deployMode === 'existing' ? selectedProjectId : undefined,
      selectedBareMetalHostId || undefined,
    ),
    enabled: !!slug && open,
    placeholderData: keepPreviousData,
  });
  
  // Fetch existing projects for "deploy to existing" option
  const { data: existingProjects } = useQuery({
    queryKey: ['projects'],
    queryFn: () => api.getProjects(),
    enabled: open,
  });
  
  // Check if this stack has prerequisites that suggest existing infrastructure
  const hasK8sPrerequisite = template?.prerequisites?.some(
    (p: StackPrerequisite) => p.type === 'kubernetes_cluster' || p.description?.toLowerCase().includes('eks')
  );
  
  const requiresRegisteredClusterTarget =
    !!template?.slug && EXISTING_CLUSTER_REQUIRED_TEMPLATE_SLUGS.has(template.slug);

  // Existing-cluster templates require projects with at least one registered cluster.
  // ubuntu-kind-foundation targets discovered blank hosts before cluster registration.
  const eligibleExistingProjects = requiresRegisteredClusterTarget
    ? getEligibleExistingClusterProjects(existingProjects)
    : (existingProjects || []);
  const missingCatalogModules = (template?.modules || []).filter(
    (module) => module.module_catalog_status === 'missing'
  );
  const hasMissingCatalogModules = missingCatalogModules.length > 0;

  const syncModuleCatalog = useSyncModuleLibrary();

  // Fetch selected project's modules to get outputs for auto-population
  const { data: selectedProjectModules } = useQuery({
    queryKey: ['project-modules', selectedProjectId],
    queryFn: () => api.getProjectModules(selectedProjectId!),
    enabled: deployMode === 'existing' && !!selectedProjectId,
  });
  
  // S19-001: Check prerequisites (secrets) when deploying to an existing project
  const { data: prerequisitesCheck } = useQuery<StackPrerequisitesCheck>({
    queryKey: ['stack-prerequisites-check', slug, selectedProjectId],
    queryFn: () => api.checkStackPrerequisites(slug, selectedProjectId!),
    enabled: deployMode === 'existing' && !!selectedProjectId && !!slug && open,
  });

  // S19-001: Extract project_secret prerequisites from template for "new project" mode
  const secretPrerequisites = template?.prerequisites?.filter(
    (p) => p.type === 'project_secret' && p.name
  ) || [];
  const hasSecretPrerequisites = secretPrerequisites.length > 0;

  // Auto-populate inputs from existing project's module outputs
  const getAutoPopulatedValue = (inputName: string): string | undefined => {
    if (!selectedProjectModules || selectedProjectModules.length === 0) return undefined;
    
    // Map of input names to module output keys
    const outputMappings: Record<string, string[]> = {
      'cluster_name': ['cluster_name'],
      'cluster_endpoint': ['cluster_endpoint'],
      'cluster_certificate_authority_data': ['cluster_certificate_authority_data'],
      'vpc_id': ['vpc_id'],
      'vpc_cidr_block': ['vpc_cidr_block'],
    };
    
    const outputKeys = outputMappings[inputName] || [inputName];
    
    // Search through all modules for the output
    for (const module of selectedProjectModules) {
      if (module.outputs) {
        for (const key of outputKeys) {
          const outputValue = module.outputs[key];
          if (outputValue !== undefined && outputValue !== null) {
            // Return as string if it's a primitive, otherwise stringify
            return typeof outputValue === 'string' ? outputValue : String(outputValue);
          }
        }
      }
    }
    return undefined;
  };

  // Single source of truth for auto-detect priority. Used by display, finalInputs build,
  // and required-input validation so they cannot disagree (regression from PR #92).
  //   1. Well-known names (user_ip, ecr_registry) resolve in BOTH 'new' and 'existing' modes.
  //   2. Existing-project module-output mapping resolves only in 'existing' mode.
  const resolveAutoValue = (inputName: string): string | undefined => {
    if (inputName === 'user_ip' && userPublicIpCidr) return userPublicIpCidr;
    if (inputName === 'ecr_registry' && awsEcrRegistry) return awsEcrRegistry;
    if (deployMode === 'existing') return getAutoPopulatedValue(inputName);
    return undefined;
  };

  // Fetch credential templates
  const { data: templates, isLoading: templatesLoading } = useQuery({
    queryKey: ['credential-templates'],
    queryFn: () => api.listCredentialTemplates(),
  });

  const blueprintProvider = (template?.cloud_provider || '').toLowerCase();

  const { data: ibmRegionsResponse } = useQuery({
    queryKey: ['stack-detail-ibm-regions', credentialTemplateId ?? 'none'],
    queryFn: () => api.listIBMRegions(credentialTemplateId),
    enabled: open && blueprintProvider === 'ibm',
  });
  const ibmRegions = ibmRegionsResponse?.regions || [];

  // Fetch bare-metal hosts for the selected project (bare-metal templates only)
  const isBareMetalTemplate = template?.category === 'bare-metal';
  const { data: bareMetalHosts } = useBareMetalHosts(
    isBareMetalTemplate && selectedProjectId ? selectedProjectId : 0
  );

  const category = categoryConfig[template?.category?.toLowerCase() || 'infrastructure'] || categoryConfig.infrastructure;
  const CategoryIcon = category.icon;
  const isExistingProjectOnlyTemplate = !!template?.slug && EXISTING_PROJECT_ONLY_TEMPLATE_SLUGS.has(template.slug);
  const allowsNewProjectMode = !isExistingProjectOnlyTemplate;
  const shouldShowCloudProvisioningInputs =
    deployMode === 'new' &&
    allowsNewProjectMode &&
    CLOUD_PROVISIONING_TEMPLATE_PROVIDERS.has((template?.cloud_provider || '').toLowerCase());

  useEffect(() => {
    if (isExistingProjectOnlyTemplate && deployMode !== 'existing') {
      setDeployMode('existing');
    }
  }, [deployMode, isExistingProjectOnlyTemplate]);

  // Inherit credential template + region from the selected existing project so
  // downstream logic (IBM region fetch, value inheritance, validation) sees the
  // project's cloud context without the user having to re-pick it.
  useEffect(() => {
    if (deployMode !== 'existing' || !selectedProjectId || !existingProjects) return;
    const project = existingProjects.find((p) => p.id === selectedProjectId);
    if (!project) return;
    if (project.credential_template_id) {
      setCredentialTemplateId(project.credential_template_id);
    }
    if (project.region) {
      setAwsRegion(project.region);
    }
  }, [deployMode, selectedProjectId, existingProjects]);

  // Reset all user selections when the dialog closes so the next open starts fresh
  useEffect(() => {
    if (!open) {
      setSelectedProjectId(undefined);
      setProjectName('');
      setUserInputs({});
      setValidationErrors({});
      setSelectedBareMetalHostId('');
      setCustomInputMode(new Set());
      setCollapsedModuleSections(new Set());
      setAwsRegion('');
      setCredentialTemplateId(undefined);
      setIsDeploying(false);
    }
  }, [open]);

  // Get selected credential template
  const selectedTemplate = templates?.find(t => t.id === credentialTemplateId);
  const selectedTemplateProvider = (selectedTemplate?.provider || '').toLowerCase();
  const isSelectedIbmTemplate = selectedTemplateProvider === 'ibm';

  // Check if region matches template region
  const regionMatchesTemplate = selectedTemplate
    ? awsRegion === selectedTemplate.region
    : false;

  const getInheritedTemplateValue = (inputName: string): string | undefined => {
    if (!selectedTemplate || !isSelectedIbmTemplate || deployMode !== 'new') {
      return undefined;
    }

    if (inputName === 'ibmcloud_cluster_region' && selectedTemplate.region) {
      return selectedTemplate.region;
    }

    if (inputName === 'ibmcloud_resource_group' && selectedTemplate.ibmcloud_resource_group) {
      return selectedTemplate.ibmcloud_resource_group;
    }

    if (inputName === 'ibmcloud_api_key' && selectedTemplate.has_ibmcloud_api_key) {
      return '__inherited_from_template__';
    }

    return undefined;
  };

  const regionInfo = getRegionInfo(awsRegion);

  // Handle credential template change - auto-inherit region
  const handleTemplateChange = (templateId: string) => {
    const id = templateId === 'none' ? undefined : parseInt(templateId);
    const template = templates?.find(t => t.id === id);

    setCredentialTemplateId(id);
    // Inherit region from template, or keep current if no template
    if (template?.region) {
      setAwsRegion(template.region);
    }
  };

  const handleDeploy = async () => {
    let targetProjectId: number | undefined;

    if (isExistingProjectOnlyTemplate && deployMode === 'new') {
      notify.error('This blueprint can only be deployed to an existing project with a registered cluster');
      return;
    }

    if (deployMode === 'new' && !projectName.trim()) {
      notify.error('Please enter a project name');
      return;
    }
    
    if (deployMode === 'existing' && !selectedProjectId) {
      notify.error('Please select an existing project');
      return;
    }

    if (deployMode === 'existing' && prerequisitesCheck && !prerequisitesCheck.all_satisfied) {
      notify.error(
        'Blueprint prerequisites not satisfied',
        'Fix missing or invalid secrets in Project Secrets before deploying.'
      );
      return;
    }

    if (!template) {
      notify.error('Template not loaded. Please try again.');
      return;
    }

    if (hasMissingCatalogModules) {
      notify.error(
        'Blueprint modules missing from catalog',
        'Sync/import required module sources before deploying this blueprint.'
      );
      return;
    }
    
    // Build final inputs including auto-populated values and catalog defaults
    const finalInputs: Record<string, Record<string, string>> = {};
    
    if (requiredInputs) {
      for (const input of requiredInputs.all_inputs) {
        const userValue = userInputs[input.module_path]?.[input.name];
        const autoValue = resolveAutoValue(input.name);
        const defaultValue = input.default != null ? String(input.default) : '';
        const value = userValue || autoValue || defaultValue;

        if (value) {
          if (!finalInputs[input.module_path]) {
            finalInputs[input.module_path] = {};
          }
          finalInputs[input.module_path][input.name] = value;
        }
      }
    }

    // Validate required inputs and format validation
    if (requiredInputs) {
      const missingInputs: string[] = [];
      const formatErrors: string[] = [];
      const newValidationErrors: Record<string, string> = {};

      for (const input of requiredInputs.all_inputs) {
        const userValue = userInputs[input.module_path]?.[input.name];
        const autoValue = resolveAutoValue(input.name);
        const inheritedValue = getInheritedTemplateValue(input.name);
        const defaultValue = input.default || '';
        const value = userValue || autoValue || inheritedValue || defaultValue;
        const errorKey = `${input.module_path}:${input.name}`;
        
        // Check required
        if (input.required && (!value || value.trim() === '')) {
          missingInputs.push(`${input.module_name}: ${input.name}`);
          newValidationErrors[errorKey] = 'This field is required';
        }
        // Check format validation
        else if (value && input.validation?.pattern) {
          try {
            const regex = new RegExp(input.validation.pattern);
            if (!regex.test(value)) {
              formatErrors.push(`${input.module_name}: ${input.name}`);
              newValidationErrors[errorKey] = input.validation.error_message || 'Invalid format';
            }
          } catch {
            // Invalid regex, skip
          }
        }
      }
      
      // Update validation errors state
      setValidationErrors(newValidationErrors);
      
      if (missingInputs.length > 0) {
        notify.error(
          'Missing required inputs',
          `Please fill in: ${missingInputs.slice(0, 3).join(', ')}${missingInputs.length > 3 ? ` and ${missingInputs.length - 3} more` : ''}`
        );
        return;
      }
      
      if (formatErrors.length > 0) {
        notify.error(
          'Invalid input format',
          `Please fix: ${formatErrors.slice(0, 3).join(', ')}${formatErrors.length > 3 ? ` and ${formatErrors.length - 3} more` : ''}`
        );
        return;
      }
    }

    // Validate bare-metal host selection
    if (isBareMetalTemplate && !selectedBareMetalHostId) {
      notify.error('Host required', 'Please select a bare-metal host for deployment.');
      return;
    }

    // Inject bare-metal host ID as a stack-level variable
    if (isBareMetalTemplate && selectedBareMetalHostId) {
      // Add to every bare-metal module path so variable_assembler can resolve the host
      const bareMetalModulePaths = (template?.modules || [])
        .filter((m: { path: string }) => m.path.startsWith('bare-metal/'))
        .map((m: { path: string }) => m.path);
      for (const modPath of bareMetalModulePaths) {
        if (!finalInputs[modPath]) {
          finalInputs[modPath] = {};
        }
        finalInputs[modPath]['bare_metal_host_id'] = selectedBareMetalHostId;
      }
    }

    setIsDeploying(true);

    try {
      let projectId: number;
      
      if (deployMode === 'new') {
        // Step 1a: Create new project
        // Derive project_type — bare-metal category takes priority over cloud_provider
        const stackProvider = template.cloud_provider?.toLowerCase();
        const projectType = template.category === 'bare-metal' ? 'bare-metal' as const
          : stackProvider === 'aws' ? 'cloud-aws' as const
          : stackProvider === 'azure' ? 'cloud-azure' as const
          : stackProvider === 'gcp' ? 'cloud-gcp' as const
          : stackProvider === 'ibm' ? 'cloud-ibm' as const
          : 'kubernetes' as const;

        const project = await api.createProject({
          name: projectName.trim(),
          description: `${template.name} deployment`,
          project_type: projectType,
          environment: 'production',
          region: awsRegion,
          credential_template_id: credentialTemplateId,
          backend_type: 'local',
          color: '#2563eb',
          icon: '',
        });
        projectId = project.project_id;
        targetProjectId = projectId;
      } else {
        // Step 1b: Use existing project
        projectId = selectedProjectId!;
        targetProjectId = projectId;
      }

      // Step 2: Create stack instance with user-provided variables (including auto-populated)
      // Variables are stored as { "module/path": { "var_name": "value" } }
      const stackInstance = await api.createStackInstance(projectId, {
        template_id: template.id,
        name: `${template.name} Blueprint`,
        variables: finalInputs,
      });

      // Step 3: Prepare the stack (creates modules in 'pending' status)
      await api.deployStack(projectId, stackInstance.id);

      notify.success(
        `Blueprint "${template.name}" ready!`,
        'Modules created. Run Init → Plan → Apply to deploy, then validate with Performance Benchmarks.',
        { category: 'deployment' },
      );

      // Start value journey for guided flow (GAP-004)
      startValueJourney({
        projectId,
        blueprintSlug: template.slug,
        blueprintName: template.name,
      });

      // Invalidate queries to refresh data
      queryClient.invalidateQueries({ queryKey: ['projects'] });
      queryClient.invalidateQueries({ queryKey: stackKeys.instances(projectId) });

      // Close dialog and reset form
      onOpenChange(false);
      setProjectName('');
      setSelectedProjectId(undefined);
      setDeployMode('new');
      setAwsRegion('');
      setCredentialTemplateId(undefined);
      setUserInputs({});
      setSelectedBareMetalHostId('');
      
      // Callback for navigation (handled by parent)
      onSuccess?.(projectId);
    } catch (error: unknown) {
      // Extract error message - check multiple possible locations
      const errorMsg = isAxiosError(error)
        ? (error.response?.data?.detail ||           // FastAPI HTTPException format
           error.response?.data?.error?.message ||   // Global error handler format
           error.response?.data?.message ||          // Alternative format
           error.message)                            // Axios error message
        : error instanceof Error ? error.message : 'Failed to deploy blueprint';

      // Show error notification - use alert as fallback to ensure user sees it
      const errorCode = isAxiosError(error) ? error.response?.data?.error?.code : undefined;
      
      const errorTitle = errorCode === 'MISSING_SECRETS'
        ? 'Missing Required Secrets'
        : errorMsg.includes('not found in library') || errorMsg.includes('Module library')
        ? 'Module Library Not Configured'
        : 'Blueprint Deployment Failed';
      
      const errorDescription = errorCode === 'MISSING_SECRETS'
        ? errorMsg
        : errorMsg.includes('not found in library') || errorMsg.includes('Module library')
        ? 'Configure module library in System → Module Library before deploying blueprints.'
        : errorMsg;
      
      notify.error(errorTitle, errorDescription);

      if (errorCode === 'MISSING_SECRETS' && targetProjectId) {
        onOpenChange(false);
        navigate(`/projects/${targetProjectId}?tab=secrets`);
      }
      
      // Also show alert for critical config errors to ensure visibility
      if (errorMsg.includes('System not configured') || errorMsg.includes('not found in library')) {
        alert(`${errorTitle}\n\n${errorDescription}`);
      }
    } finally {
      setIsDeploying(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-4xl max-h-[85vh] p-0 gap-0 flex flex-col bg-card border-border">
        {loadingTemplate ? (
          <>
            <VisuallyHidden>
              <DialogTitle>Loading Blueprint</DialogTitle>
              <DialogDescription>Please wait while we load the blueprint details</DialogDescription>
            </VisuallyHidden>
            <div className="p-12 flex items-center justify-center">
              <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-primary" />
            </div>
          </>
        ) : template ? (
          <>
            {/* Header — token-based, no gradient (D-020) */}
            <div className="relative p-4 border-b border-border bg-card">
              <div className="flex items-center gap-3">
                <div className="p-2 rounded-xl bg-primary/10 border border-primary/20">
                  <CategoryIcon className="h-6 w-6 text-primary" />
                </div>
                <div className="flex-1">
                  <div className="flex items-center gap-2 mb-1">
                    <DialogTitle className="text-xl font-bold text-foreground">
                      {template.name}
                    </DialogTitle>
                    <Badge variant="muted" className="text-[10px] uppercase tracking-wide">
                      {categoryLabels[template.category?.toLowerCase() || 'infrastructure'] || 'Blueprint'}
                    </Badge>
                    {template.maturity && (
                      <Badge variant="muted" className="text-[10px]">
                        {template.maturity === 'production-ready' ? 'Production Ready' :
                         template.maturity === 'reference' ? 'Reference' :
                         template.maturity}
                      </Badge>
                    )}
                  </div>
                  <DialogDescription className="text-muted-foreground text-sm">
                    {template.description}
                  </DialogDescription>
                  {template.platform_defaults && Object.keys(template.platform_defaults).length > 0 && (
                    <div className="flex items-center gap-1.5 mt-1.5">
                      <span className="text-[10px] text-muted-foreground uppercase tracking-wide">Platforms:</span>
                      {Object.keys(template.platform_defaults).map((p) => (
                        <Badge key={p} variant="outline" className="text-[10px] px-1.5 py-0">
                          {p.toUpperCase()}
                        </Badge>
                      ))}
                    </div>
                  )}
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

            <ScrollArea ref={scrollAreaRef} className="flex-1 overflow-auto">
              <div className="p-6 space-y-6">
                {/* Lifecycle stage — GAP-002 */}
                <SectionCard title="Lifecycle stage" compact>
                  <p className="text-sm text-foreground">
                    {categoryStageHelp[template.category?.toLowerCase() || 'infrastructure']}
                  </p>
                </SectionCard>

                {template.outcomes && template.outcomes.length > 0 && (
                  <SectionCard title="What you get">
                    <div className="space-y-2">
                      {template.outcomes.map((outcome, idx) => (
                        <div key={idx} className="flex items-start gap-2">
                          <CheckCircle2 className="h-4 w-4 mt-0.5 flex-shrink-0 text-success" />
                          <span className="text-sm text-foreground">
                            {outcome}
                          </span>
                        </div>
                      ))}
                    </div>
                  </SectionCard>
                )}

                {/* Prerequisites */}
                {template.prerequisites && template.prerequisites.length > 0 && (
                  <SectionCard title="Prerequisites">
                    <div className="space-y-2">
                      {template.prerequisites.map((prereq, idx) => {
                        const isSecret = prereq.type === 'project_secret';
                        return (
                          <div
                            key={idx}
                            className="p-3 rounded-lg border border-border bg-muted/30"
                          >
                            <div className="flex items-center gap-2 mb-1">
                              <p className="text-sm font-medium text-foreground">
                                {isSecret && prereq.name ? prereq.name : prereq.type}
                              </p>
                              {isSecret && (
                                <Badge variant="info" className="text-[10px] py-0">
                                  secret
                                </Badge>
                              )}
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
                  <SectionCard title="Input notes">
                    <div className="space-y-2">
                      {requiredInputs.summary.map((item, idx) => (
                        <div
                          key={`${item.label}-${idx}`}
                          className="p-3 rounded-lg border border-border bg-muted/30"
                        >
                          <p className="text-sm font-medium mb-1 text-foreground">
                            {item.label}
                          </p>
                          <p className="text-sm text-muted-foreground">
                            {item.value}
                          </p>
                        </div>
                      ))}
                    </div>
                  </SectionCard>
                )}

                {/* Modules List */}
                <SectionCard title={`Modules (${preview?.total_modules || template.modules?.length || 0})`}>
                  <div className="space-y-2">
                    {template.modules?.map((module, idx) => (
                      <ModuleItem
                        key={idx}
                        module={module}
                        index={idx}
                        isLast={idx === (template.modules?.length || 0) - 1}
                      />
                    ))}
                  </div>
                </SectionCard>

                {hasMissingCatalogModules && (
                  <Alert className="border-destructive/50 bg-destructive/5">
                    <AlertCircle className="h-4 w-4 text-destructive" />
                    <AlertDescription className="text-sm space-y-1">
                      <p className="font-medium">Blueprint has modules missing from the active catalog</p>
                      <p className="text-xs text-muted-foreground">
                        Deploy is blocked until these module paths are available from synced/imported module sources:
                      </p>
                      <ul className="text-xs space-y-0.5 mt-1">
                        {missingCatalogModules.map((module) => (
                          <li key={module.path} className="flex items-start gap-1.5">
                            <AlertTriangle className="h-3 w-3 text-destructive mt-0.5 flex-shrink-0" />
                            <span><strong>{module.path}</strong></span>
                          </li>
                        ))}
                      </ul>
                      <div className="mt-2">
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          className="h-7 text-xs gap-1.5"
                          disabled={syncModuleCatalog.isPending}
                          onClick={async () => {
                            try {
                              await syncModuleCatalog.mutateAsync(false);
                            } catch {
                              // Hook-level onError already notifies the user; absorb so the
                              // async onClick does not surface an unhandled rejection.
                              return;
                            }
                            // Await the refetch so the template query (and the
                            // missing-modules banner) reflects the catalog state
                            // before isPending flips back to false.
                            await queryClient.refetchQueries({
                              queryKey: stackKeys.template(slug),
                            });
                          }}
                        >
                          {syncModuleCatalog.isPending ? (
                            <Loader2 className="h-3 w-3 animate-spin" />
                          ) : (
                            <RefreshCw className="h-3 w-3" />
                          )}
                          Sync module catalog
                        </Button>
                      </div>
                    </AlertDescription>
                  </Alert>
                )}

                {/* Tags */}
                {template.tags && template.tags.length > 0 && (
                  <SectionCard title="Tags" compact>
                    <div className="flex flex-wrap gap-2">
                      {template.tags.map((tag) => (
                        <Badge key={tag} variant="outline">
                          {tag}
                        </Badge>
                      ))}
                    </div>
                  </SectionCard>
                )}
              </div>

            <Separator />

            {/* Deployment Configuration - Inside ScrollArea for proper scrolling */}
            <div className="p-6 space-y-6">
              {/* Deploy Mode Selector */}
              <SectionCard title="Deployment target" compact>
                {allowsNewProjectMode ? (
                  <RadioGroup
                    value={deployMode}
                    onValueChange={(v) => setDeployMode(v as 'new' | 'existing')}
                    className="flex gap-4"
                  >
                    <div className="flex items-center space-x-2">
                      <RadioGroupItem value="new" id="deploy-new" />
                      <Label htmlFor="deploy-new" className="text-sm flex items-center gap-1.5 cursor-pointer">
                        <FolderPlus className="h-3.5 w-3.5" />
                        New Project
                      </Label>
                    </div>
                    <div className="flex items-center space-x-2">
                      <RadioGroupItem value="existing" id="deploy-existing" />
                      <Label htmlFor="deploy-existing" className="text-sm flex items-center gap-1.5 cursor-pointer">
                        <FolderOpen className="h-3.5 w-3.5" />
                        Existing Project
                        {hasK8sPrerequisite && (
                          <Badge variant="outline" className="ml-1 text-[10px] py-0">Recommended</Badge>
                        )}
                      </Label>
                    </div>
                  </RadioGroup>
                ) : (
                  <div className="text-xs text-muted-foreground flex items-center gap-2">
                    <FolderOpen className="h-3.5 w-3.5" />
                    Existing project only for this blueprint.
                  </div>
                )}
              </SectionCard>

              {/* New Project Config */}
              {deployMode === 'new' && (
                <SectionCard title="New project" compact>
                  <div className="space-y-4">
                    <div className="space-y-1.5">
                      <Label htmlFor="projectName" className="text-xs">Project Name *</Label>
                      <Input
                        id="projectName"
                        placeholder={`My ${template.name}`}
                        value={projectName}
                        onChange={(e) => setProjectName(e.target.value)}
                        className="h-9"
                      />
                    </div>

                    {shouldShowCloudProvisioningInputs && (
                      <>
                        <div className="space-y-1.5" data-onboarding="auth-select">
                          <Label htmlFor="credentialTemplate" className="text-xs">Cloud Credentials</Label>
                          <Select
                            value={credentialTemplateId?.toString() || 'none'}
                            onValueChange={handleTemplateChange}
                            disabled={templatesLoading}
                          >
                            <SelectTrigger className="h-9">
                              <SelectValue placeholder="Use global credentials" />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="none">Use global credentials</SelectItem>
                              {templates && templates.length > 0 ? (
                                templates.map((template) => (
                                  <SelectItem key={template.id} value={template.id.toString()}>
                                    {template.name} ({template.provider.toUpperCase()})
                                  </SelectItem>
                                ))
                              ) : (
                                <SelectItem value="none" disabled>
                                  No templates available
                                </SelectItem>
                              )}
                            </SelectContent>
                          </Select>
                        </div>

                        <div className="space-y-1.5" data-onboarding="region-select">
                          <Label htmlFor="awsRegion" className="text-xs">Region *</Label>
                          <CloudRegionSelector
                            provider={blueprintProvider}
                            value={awsRegion}
                            onValueChange={setAwsRegion}
                            options={blueprintProvider === 'ibm' ? ibmRegions : undefined}
                          />

                          {/* Smart inheritance indicator */}
                          {selectedTemplate && regionMatchesTemplate && (
                            <div className="flex items-center gap-1.5 text-xs text-success">
                              <CheckCircle className="h-3 w-3" />
                              <span>Inherited from {selectedTemplate.name}</span>
                            </div>
                          )}

                          {/* Region mismatch warning */}
                          {selectedTemplate && !regionMatchesTemplate && (
                            <Alert className="py-2">
                              <AlertTriangle className="h-4 w-4" />
                              <AlertDescription className="text-xs">
                                Template region is <strong>{selectedTemplate.region}</strong>, but project will deploy to{' '}
                                <strong>{awsRegion}</strong>.
                              </AlertDescription>
                            </Alert>
                          )}

                          {/* No template selected */}
                          {!selectedTemplate && regionInfo && (
                            <p className="text-xs text-muted-foreground">
                              {regionInfo.flag} {regionInfo.label}
                            </p>
                          )}
                        </div>
                      </>
                    )}
                  </div>
                </SectionCard>
              )}

              {/* Existing Project Selector */}
              {deployMode === 'existing' && (
                <SectionCard title="Existing project" compact>
                  <div className="space-y-1.5">
                    <Label htmlFor="existingProject" className="text-xs">Select Project *</Label>
                    <Select
                      value={selectedProjectId?.toString() || ''}
                      onValueChange={(v) => { setSelectedProjectId(parseInt(v)); setSelectedBareMetalHostId(''); }}
                    >
                      <SelectTrigger className="h-9">
                        <SelectValue placeholder="Choose a project..." />
                      </SelectTrigger>
                      <SelectContent>
                        {eligibleExistingProjects.length > 0 ? (
                          eligibleExistingProjects.map((project: Project) => (
                            <SelectItem key={project.id} value={project.id.toString()}>
                              {project.name} ({project.cluster_count ?? 0} cluster{(project.cluster_count ?? 0) === 1 ? '' : 's'})
                            </SelectItem>
                          ))
                        ) : (
                          <SelectItem value="__none__" disabled>
                            {requiresRegisteredClusterTarget
                              ? 'No projects with registered clusters'
                              : 'No projects available'}
                          </SelectItem>
                        )}
                      </SelectContent>
                    </Select>
                    {requiresRegisteredClusterTarget && hasK8sPrerequisite && (
                      <p className="text-xs text-muted-foreground">
                        This blueprint targets an existing Kubernetes cluster. Select a project with at least one registered cluster.
                      </p>
                    )}
                    {selectedProjectId && (selectedTemplate || awsRegion) && (
                      <div className="mt-2 p-2.5 rounded-md border border-border bg-muted/30 text-xs space-y-1 text-muted-foreground">
                        <div className="font-medium">Inherited from project:</div>
                        {selectedTemplate && (
                          <div className="flex items-center gap-1.5">
                            <Key className="h-3 w-3" />
                            <span>{selectedTemplate.name} ({selectedTemplate.provider.toUpperCase()})</span>
                          </div>
                        )}
                        {awsRegion && (
                          <div className="flex items-center gap-1.5">
                            <Target className="h-3 w-3" />
                            <span>Region: {awsRegion}</span>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                </SectionCard>
              )}

              {/* S19-001: Required Secrets Warning */}
              {/* Show for existing project: live prerequisite check */}
              {deployMode === 'existing' && selectedProjectId && prerequisitesCheck && prerequisitesCheck.required_secrets.length > 0 && (
                <SectionCard title="Required secrets" compact>
                  <div className="space-y-2">
                    <div className="flex items-center gap-2">
                      <Badge variant={prerequisitesCheck.all_satisfied ? 'success' : 'destructive'} className="text-[10px]">
                        {prerequisitesCheck.all_satisfied
                          ? 'All configured'
                          : `${prerequisitesCheck.missing_secrets.length} missing`}
                      </Badge>
                    </div>
                    <div className="space-y-1.5">
                      {prerequisitesCheck.required_secrets.map((secret) => (
                        <div key={secret.name} className="flex items-center gap-2">
                          {secret.exists ? (
                            <CheckCircle className="h-3 w-3 text-success flex-shrink-0" />
                          ) : (
                            <AlertCircle className="h-3 w-3 text-destructive flex-shrink-0" />
                          )}
                          <div className="min-w-0">
                            <span className="text-xs font-medium text-foreground">
                              {secret.name}
                            </span>
                            {secret.description && (
                              <p className="text-xs text-muted-foreground truncate">{secret.description}</p>
                            )}
                            {secret.exists && secret.valid === false && secret.validation_error && (
                              <p className="text-xs text-destructive">
                                Invalid: {secret.validation_error}
                              </p>
                            )}
                            {secret.exists && secret.valid && secret.validated_source && (
                              <p className="text-xs text-muted-foreground">
                                Validated from {secret.validated_source.replace('_', ' ')}
                              </p>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                    {prerequisitesCheck.secret_source_policy && (
                      <p className="text-xs text-muted-foreground pt-1">
                        Secret policy: project secrets are authoritative.
                        {prerequisitesCheck.secret_source_policy.global_cne_pull_secret_default_supported
                          ? ' cne_pull_secret can fall back to encrypted System Defaults.'
                          : ' cne_pull_secret has no global/system fallback in this flow.'}
                      </p>
                    )}
                    {!prerequisitesCheck.all_satisfied && (
                      <div className="flex items-center justify-between gap-3 pt-1">
                        <p className="text-xs text-muted-foreground">
                          Configure missing/invalid secrets in Project Settings before deploying.
                        </p>
                        {selectedProjectId && (
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => {
                              onOpenChange(false);
                              navigate(`/projects/${selectedProjectId}?tab=secrets`);
                            }}
                            className="h-7 text-xs"
                          >
                            Open Project Secrets
                          </Button>
                        )}
                      </div>
                    )}
                  </div>
                </SectionCard>
              )}
              {/* Show for new project: static warning from template prerequisites */}
              {deployMode === 'new' && hasSecretPrerequisites && (
                <Alert className="border-warning/50 bg-warning/5">
                  <Key className="h-4 w-4 text-warning" />
                  <AlertDescription className="text-sm space-y-1">
                    <p className="font-medium">Required project secrets</p>
                    <p className="text-xs text-muted-foreground">
                      After creating the project, configure these secrets in Project Settings before deploying:
                    </p>
                    <ul className="text-xs space-y-0.5 mt-1">
                      {secretPrerequisites.map((p) => (
                        <li key={p.name} className="flex items-start gap-1.5">
                          <AlertTriangle className="h-3 w-3 text-warning mt-0.5 flex-shrink-0" />
                          <span><strong>{p.name}</strong>{p.description ? ` — ${p.description}` : ''}</span>
                        </li>
                      ))}
                    </ul>
                  </AlertDescription>
                </Alert>
              )}

              {/* Bare-Metal Host Selection */}
              {isBareMetalTemplate && deployMode === 'existing' && selectedProjectId && (
                <SectionCard title="Bare-metal host" compact>
                  <div className="space-y-2">
                    <Select
                      value={selectedBareMetalHostId}
                      onValueChange={setSelectedBareMetalHostId}
                    >
                      <SelectTrigger className="h-8 text-sm">
                        <SelectValue placeholder="Select a discovered host..." />
                      </SelectTrigger>
                      <SelectContent>
                        {bareMetalHosts && bareMetalHosts.length > 0 ? (
                          bareMetalHosts.map((host: BareMetalHost) => (
                            <SelectItem key={host.id} value={String(host.id)}>
                              {host.hostname || host.host_ip}
                              {' — '}
                              {host.topology ? `topology: ${host.topology}` : 'Not discovered'}
                            </SelectItem>
                          ))
                        ) : (
                          <SelectItem value="__none__" disabled>
                            No hosts registered — go to Bare Metal tab first
                          </SelectItem>
                        )}
                      </SelectContent>
                    </Select>
                    <p className="text-xs text-muted-foreground">
                      The selected host must have SSH access configured and discovery completed.
                    </p>
                  </div>
                </SectionCard>
              )}

              {/* Required Module Inputs */}
              {requiredInputs && requiredInputs.all_inputs.length > 0 && (
                <SectionCard
                  title="Module configuration"
                  compact
                  className="space-y-3"
                >
                  <div className="flex items-center justify-end">
                    <Badge variant={requiredInputs.total_required > 0 ? 'warning' : 'success'} className="text-[10px]">
                      {requiredInputs.total_required} required
                    </Badge>
                  </div>

                  {Object.entries(requiredInputs.inputs_by_module).map(([modulePath, inputs]: [string, StackInputDefinition[]]) => {
                    const isExpanded = !collapsedModuleSections.has(modulePath);
                    return (
                    <div key={modulePath} className="p-3 rounded-lg border border-border bg-muted/30 space-y-2">
                      <button
                        type="button"
                        className="w-full flex items-center justify-between text-xs font-medium"
                        onClick={() => toggleModuleSection(modulePath)}
                      >
                        <span>{inputs[0]?.module_name || modulePath}</span>
                        <ChevronDown className={cn('h-3 w-3 transition-transform', isExpanded && 'rotate-180')} />
                      </button>
                      {isExpanded && inputs.map((input: StackInputDefinition) => {
                        const autoValue = resolveAutoValue(input.name);
                        const inheritedValue = getInheritedTemplateValue(input.name);
                        const currentValue =
                          userInputs[modulePath]?.[input.name]
                          || autoValue
                          || inheritedValue
                          || (input.default != null ? String(input.default) : '');
                        const isAutoPopulated =
                          (!!autoValue && !userInputs[modulePath]?.[input.name]) ||
                          (!!input.default && !userInputs[modulePath]?.[input.name] && !autoValue && !inheritedValue);
                        const isInheritedFromTemplate = !!inheritedValue && !autoValue && !userInputs[modulePath]?.[input.name];
                        // The sentinel is itself a marker that the value lives on the
                        // server-side template — never render it verbatim, regardless of
                        // whether the input's metadata happened to flag `sensitive`.
                        const isSentinelInherited = inheritedValue === '__inherited_from_template__';
                        const errorKey = `${modulePath}:${input.name}`;
                        const error = validationErrors[errorKey];
                        const hasValidation = !!input.validation?.pattern;
                        
                        const useLargeInput = isLargeContentInput(input);

                        return (
                          <div key={`${modulePath}-${input.name}`} className="space-y-1">
                            <Label htmlFor={`${modulePath}-${input.name}`} className="text-xs flex items-center gap-2">
                              {input.name.replace(/_/g, ' ')}
                              {input.required && <span className="text-destructive">*</span>}
                              {input.sensitive && (
                                <Badge variant="outline" className="text-[10px] py-0">
                                  sensitive
                                </Badge>
                              )}
                              {isAutoPopulated && (
                                <Badge variant="success" className="text-[10px] py-0">
                                  Auto-detected
                                </Badge>
                              )}
                              {isInheritedFromTemplate && (
                                <Badge variant="info" className="text-[10px] py-0">
                                  Inherited
                                </Badge>
                              )}
                            </Label>
                            {/* Catalog selector for inputs with predefined options */}
                            {input.options && input.options.length > 0 && !customInputMode.has(`${modulePath}:${input.name}`) ? (
                              <div className="space-y-1">
                                <Select
                                  value={currentValue}
                                  onValueChange={(v) => {
                                    if (v === '__custom__') {
                                      setCustomInputMode(prev => new Set(prev).add(`${modulePath}:${input.name}`));
                                    } else {
                                      handleInputChange(modulePath, input.name, v, input);
                                    }
                                  }}
                                >
                                  <SelectTrigger className="h-8 text-sm">
                                    <SelectValue placeholder="Select from catalog..." />
                                  </SelectTrigger>
                                  <SelectContent>
                                    {input.options.map((opt) => (
                                      <SelectItem key={opt.value} value={opt.value}>
                                        <div className="flex flex-col">
                                          <span className="text-sm">{opt.label}</span>
                                          {opt.description && (
                                            <span className="text-xs text-muted-foreground">{opt.description}</span>
                                          )}
                                        </div>
                                      </SelectItem>
                                    ))}
                                    <SelectItem value="__custom__">
                                      <span className="text-sm italic text-muted-foreground">Enter custom URL...</span>
                                    </SelectItem>
                                  </SelectContent>
                                </Select>
                                {input.description && !error && (
                                  <p className="text-xs text-muted-foreground">{input.description}</p>
                                )}
                                {error && (
                                  <p className="text-xs text-destructive flex items-center gap-1">
                                    <AlertCircle className="h-3 w-3" />
                                    {error}
                                  </p>
                                )}
                              </div>
                            ) : useLargeInput && !isSentinelInherited ? (
                              <LargeContentInput
                                input={input}
                                modulePath={modulePath}
                                currentValue={currentValue}
                                isAutoPopulated={isAutoPopulated}
                                error={error}
                                hasValidation={hasValidation}

                                onChange={(value) => handleInputChange(modulePath, input.name, value, input)}
                              />
                            ) : (
                              <>
                                {/* Back to catalog link when in custom mode */}
                                {input.options && input.options.length > 0 && customInputMode.has(`${modulePath}:${input.name}`) && (
                                  <button
                                    type="button"
                                    className="text-xs text-primary hover:text-primary/80 hover:underline mb-1"
                                    onClick={() => {
                                      setCustomInputMode(prev => {
                                        const next = new Set(prev);
                                        next.delete(`${modulePath}:${input.name}`);
                                        return next;
                                      });
                                      handleInputChange(modulePath, input.name, '', input);
                                    }}
                                  >
                                    ← Back to catalog
                                  </button>
                                )}
                                <Input
                                  id={`${modulePath}-${input.name}`}
                                  value={isSentinelInherited ? '' : currentValue}
                                  placeholder={
                                    isSentinelInherited
                                      ? `Provided by ${selectedTemplate?.name ?? 'credential template'}`
                                      : input.example
                                        ? `Example: ${input.example}`
                                        : input.description
                                  }
                                  type={input.sensitive ? 'password' : 'text'}
                                  onChange={(e) => handleInputChange(modulePath, input.name, e.target.value, input)}
                                  className={cn(
                                    'h-8 text-sm',
                                    isAutoPopulated && 'border-success/40',
                                    isInheritedFromTemplate && 'border-info/40',
                                    error && 'border-destructive'
                                  )}
                                />
                                {isInheritedFromTemplate && selectedTemplate && (
                                  <p className="text-xs text-info flex items-center gap-1">
                                    <CheckCircle className="h-3 w-3" />
                                    {input.name === 'ibmcloud_api_key'
                                      ? `Provided securely by ${selectedTemplate.name}`
                                      : `Inherited from ${selectedTemplate.name}`}
                                  </p>
                                )}
                                {/* Validation hint */}
                                {hasValidation && input.example && !error && (
                                  <p className="text-xs text-warning flex items-center gap-1">
                                    <AlertTriangle className="h-3 w-3" />
                                    Format: {input.example}
                                  </p>
                                )}
                                {/* Validation error */}
                                {error && (
                                  <p className="text-xs text-destructive flex items-center gap-1">
                                    <AlertCircle className="h-3 w-3" />
                                    {error}
                                  </p>
                                )}
                                {/* Description (show if no error/hint) */}
                                {input.description && !error && !hasValidation && (
                                  <p className="text-xs text-muted-foreground">{input.description}</p>
                                )}
                              </>
                            )}
                          </div>
                        );
                      })}
                    </div>
                    );
                  })}
                </SectionCard>
              )}

              {/* GAP-003: Readiness Checklist — non-secret prerequisites */}
              {deployMode === 'new' && template.prerequisites && template.prerequisites.filter(p => p.type !== 'project_secret').length > 0 && (
                <SectionCard title="Readiness checklist" compact>
                  <p className="text-xs text-muted-foreground mb-2">
                    Confirm these requirements are met before deploying:
                  </p>
                  <div className="space-y-1.5">
                    {template.prerequisites.filter(p => p.type !== 'project_secret').map((prereq, idx) => (
                      <div key={idx} className="flex items-start gap-2">
                        <AlertCircle className="h-3.5 w-3.5 mt-0.5 flex-shrink-0 text-warning" />
                        <div className="min-w-0">
                          <span className="text-xs font-medium text-foreground">
                            {prereq.type === 'kubernetes_cluster' ? 'Kubernetes Cluster' :
                             prereq.type === 'stack_deployed' ? `Blueprint: ${prereq.name}` :
                             prereq.type}
                          </span>
                          {prereq.description && (
                            <p className="text-xs text-muted-foreground">
                              {prereq.description}
                            </p>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </SectionCard>
              )}

              <div className="flex gap-2 pt-2">
                <Button
                  data-onboarding="deploy-stack"
                  onClick={handleDeploy}
                  disabled={
                    (deployMode === 'new' && !projectName.trim()) ||
                    (deployMode === 'existing' && !selectedProjectId) ||
                    (deployMode === 'existing' && !!prerequisitesCheck && !prerequisitesCheck.all_satisfied) ||
                    isDeploying ||
                    hasMissingCatalogModules
                  }
                  className="flex-1 h-9 font-semibold text-sm"
                >
                  {isDeploying ? (
                    <>
                      <div className="animate-spin rounded-full h-3 w-3 border-b-2 border-white mr-2" />
                      Deploying...
                    </>
                  ) : (
                    <>
                       {deployMode === 'existing' ? 'Add to Project' : 'Deploy Blueprint'}
                      <ArrowRight className="ml-2 h-3 w-3" />
                    </>
                  )}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => onOpenChange(false)}
                  disabled={isDeploying}
                  className="h-9"
                >
                  Cancel
                </Button>
              </div>
            </div>
            </ScrollArea>
          </>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

// Module Item Component
function ModuleItem({
  module,
  index,
  isLast,
}: {
  module: StackTemplateModule;
  index: number;
  isLast: boolean;
}) {
  const isMissingFromCatalog = module.module_catalog_status === 'missing';
  const enginePresentation = getEnginePresentation(module.engine_type, undefined);
  const lifecycleSummary = getLifecycleSummaryBadges(module.lifecycle_capabilities);

  return (
    <div className="relative">
      <div className="flex items-start gap-4 p-3 rounded-lg border border-border bg-muted/30 hover:border-border/60 transition-colors">
        {/* Step Number */}
        <div className="flex-shrink-0 w-7 h-7 rounded-full flex items-center justify-center text-xs font-semibold bg-muted text-muted-foreground border border-border">
          {index + 1}
        </div>

        {/* Module Info */}
        <div className="flex-1 min-w-0">
          <div className="flex items-start justify-between gap-2 mb-1">
            <h4 className="text-sm font-semibold flex items-center gap-1.5 text-foreground">
              {module.name}
              {module.connectivity_risk && (
                <span title="This step may temporarily disrupt SSH connectivity. The host should reconnect automatically.">
                  <AlertTriangle className="h-3.5 w-3.5 text-warning flex-shrink-0" />
                </span>
              )}
            </h4>
            {module.required ? (
              <Badge variant="muted" className="text-xs">
                Required
              </Badge>
            ) : (
              <Badge variant="outline" className="text-xs">
                Optional
              </Badge>
            )}
          </div>
          <p className="text-xs mb-1 text-muted-foreground font-mono">
            {module.path}
          </p>
          {module.description && (
            <p className="text-sm text-muted-foreground">
              {module.description}
            </p>
          )}
          <div className="mt-2 flex flex-wrap gap-1.5">
            {isMissingFromCatalog ? (
              <Badge variant="destructive" className="text-[10px]">
                Missing from catalog
              </Badge>
            ) : (
              <Badge variant="outline" className={cn('text-[10px]', enginePresentation.className)}>
                {enginePresentation.label}
              </Badge>
            )}
            {!isMissingFromCatalog && module.lifecycle_capabilities && (
              lifecycleSummary.includes('Destroy') ? (
                <Badge variant="muted" className="text-[10px]">
                  Destroy supported
                </Badge>
              ) : (
                <Badge variant="warning" className="text-[10px]">
                  Destroy not supported
                </Badge>
              )
            )}
            {module.connectivity_risk && (
              <Badge variant="warning" className="text-[10px]">
                SSH may drop
              </Badge>
            )}
          </div>
          {isMissingFromCatalog && module.module_catalog_message && (
            <p className="text-xs mt-1 text-destructive">
              {module.module_catalog_message}
            </p>
          )}
        </div>
      </div>

      {/* Connector Line */}
      {!isLast && (
        <div className="absolute left-[19px] top-[52px] w-0.5 h-4 bg-border" />
      )}
    </div>
  );
}
