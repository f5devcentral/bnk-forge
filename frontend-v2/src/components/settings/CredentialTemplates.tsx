import { useEffect, useState } from 'react';
import { SectionCard } from '@/components/ui/section-card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
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
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Switch } from '@/components/ui/switch';
import {
  Loader2,
  Key,
  Plus,
  Edit,
  Trash2,
  CheckCircle,
  Cloud,
  Shield,
  Terminal,
  TestTube2,
  AlertTriangle,
  AlertCircle,
  Clock,
  CheckCircle2,
  MoreVertical,
} from 'lucide-react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { notify, notifyError } from '@/lib/notify';
import { api } from '@/lib/api';
import { loadIbmRegionsFromApiKey } from '@/lib/ibm-cloud';
import type { CloudCredentialTemplate, CloudCredentialTemplateCreate, CloudRegionOption, IBCosInstanceOption } from '@/types';
import { RegionSelector } from '@/components/aws/RegionSelector';
import { CloudRegionSelector } from '@/components/cloud/CloudRegionSelector';
import { SSOAuthDialog } from './SSOAuthDialog';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { resolveCredStatus } from './resolveCredStatus';
import { useAppMutation } from '@/hooks/lib/useAppMutation';

const TEMPLATE_PROVIDER_OPTIONS = [
  { value: 'aws', label: 'Amazon Web Services (AWS)' },
  { value: 'gcp', label: 'Google Cloud Platform (GCP)' },
  { value: 'azure', label: 'Microsoft Azure' },
  { value: 'ibm', label: 'IBM Cloud' },
] as const;

/**
 * Single authoritative AWS credential status badge.
 *
 * Delegates to resolveCredStatus() which collapses the lease prediction
 * (aws_credentials_expiry) and the observation columns (last boto3 call)
 * into one priority-ordered result: failed > expired > warning > ok > unknown.
 */
function AwsCredStatusBadge({ template }: { template: CloudCredentialTemplate }) {
  const { level, headline, detail } = resolveCredStatus(template);
  if (level === 'unknown') return null;

  const icon =
    level === 'failed' ? <AlertTriangle className="h-3 w-3" /> :
    level === 'expired' ? <AlertCircle className="h-3 w-3" /> :
    level === 'warning' ? <Clock className="h-3 w-3" /> :
    <CheckCircle2 className="h-3 w-3" />;

  const variant =
    level === 'failed' || level === 'expired' ? 'destructive' :
    level === 'warning' ? 'warning' :
    'success';

  const badge = (
    <Badge variant={variant} className="gap-1 text-xs">
      {icon}
      {headline}
    </Badge>
  );

  if (!detail) return badge;

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>{badge}</TooltipTrigger>
        <TooltipContent>
          <p className="text-xs">{detail}</p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

export default function CredentialTemplates() {
  const queryClient = useQueryClient();
  const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false);
  const [isEditDialogOpen, setIsEditDialogOpen] = useState(false);
  const [editingTemplate, setEditingTemplate] = useState<CloudCredentialTemplate | null>(null);
  const [filterProvider, setFilterProvider] = useState<string>('all');
  const [ssoAuthDialogOpen, setSsoAuthDialogOpen] = useState(false);
  const [ssoAuthTemplateId, setSsoAuthTemplateId] = useState<number | null>(null);
  const [ssoAuthTemplateName, setSsoAuthTemplateName] = useState<string>('');
  const [testingTemplateId, setTestingTemplateId] = useState<number | null>(null);
  const [ibmRegionOptions, setIbmRegionOptions] = useState<CloudRegionOption[]>([]);
  const [ibmRegionError, setIbmRegionError] = useState<string | null>(null);
  const [ibmCosInstanceOptions, setIbmCosInstanceOptions] = useState<IBCosInstanceOption[]>([]);
  const [ibmCosInstanceError, setIbmCosInstanceError] = useState<string | null>(null);

  // Form state
  const [formData, setFormData] = useState<CloudCredentialTemplateCreate>({
    name: '',
    description: '',
    provider: 'aws',
    aws_auth_method: 'access_keys',
    region: '',
    aws_profile: '',
    aws_access_key_id: '',
    aws_secret_access_key: '',
    aws_session_token: '',
    aws_sso_enabled: false,
    aws_sso_start_url: '',
    aws_sso_region: '',
    aws_sso_account_id: '',
    aws_sso_role_name: '',
    ibmcloud_api_key: '',
    ibm_cos_instance_name: '',
    gcp_project_id: '',
    gcp_credentials: '',
    is_default: false,
  });

  // Fetch templates
  const { data: templates, isLoading } = useQuery({
    queryKey: ['credential-templates', filterProvider],
    queryFn: () => api.listCredentialTemplates(filterProvider === 'all' ? undefined : filterProvider),
  });

  // Create mutation
  const createMutation = useAppMutation({
    mutationFn: (data: CloudCredentialTemplateCreate) => api.createCredentialTemplate(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['credential-templates'] });
      notify.success('Credential template created successfully', undefined, { category: 'credentials' });
      setIsCreateDialogOpen(false);
      resetForm();
    },
    onError: (error) => {
      notifyError(error);
    },
  });

  // Update mutation
  const updateMutation = useAppMutation({
    mutationFn: ({ id, data }: { id: number; data: Partial<CloudCredentialTemplateCreate> }) =>
      api.updateCredentialTemplate(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['credential-templates'] });
      notify.success('Credential template updated successfully', undefined, { category: 'credentials' });
      setIsEditDialogOpen(false);
      setEditingTemplate(null);
      resetForm();
    },
    onError: (error) => {
      notifyError(error);
    },
  });

  // Delete mutation
  const deleteMutation = useAppMutation({
    mutationFn: (id: number) => api.deleteCredentialTemplate(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['credential-templates'] });
      notify.success('Credential template deleted successfully', undefined, { category: 'credentials' });
    },
    onError: (error) => {
      notifyError(error);
    },
  });

  // Test credential mutation
  const testMutation = useAppMutation({
    mutationFn: (id: number) => {
      setTestingTemplateId(id);
      return api.testCredentialTemplate(id);
    },
    onSuccess: (data) => {
      setTestingTemplateId(null);
      if (data.success) {
        const detail = data.arn || data.hostname || data.ssh_banner || data.message || '';
        notify.success(`Connection test passed${detail ? `: ${detail}` : ''}`, undefined, { category: 'credentials' });
      } else {
        notify.error(`Connection test failed: ${data.error || data.message || 'Unknown error'}`);
      }
    },
    onError: (error) => {
      setTestingTemplateId(null);
      notifyError(error);
    },
  });

  const loadIBMRegionsMutation = useAppMutation({
    mutationFn: (apiKey: string) => loadIbmRegionsFromApiKey(apiKey),
    onSuccess: (regions) => {
      setIbmRegionOptions(regions);
      setIbmRegionError(null);
      if (!formData.region && regions.length > 0) {
        setFormData((prev) => ({ ...prev, region: regions[0].value }));
      }
      notify.success(`Loaded ${regions.length} IBM Cloud region(s)`, undefined, { category: 'credentials' });
    },
    onError: (error) => {
      setIbmRegionError(error instanceof Error ? error.message : 'Failed to load IBM Cloud regions');
      notifyError(error);
    },
  });

  const loadIBMCosInstancesMutation = useAppMutation({
    mutationFn: (apiKey: string) => api.listIBMCosInstances(apiKey),
    onSuccess: (data) => {
      setIbmCosInstanceOptions(data.instances);
      setIbmCosInstanceError(null);
      if (!formData.ibm_cos_instance_name && data.instances.length > 0) {
        setFormData((prev) => ({ ...prev, ibm_cos_instance_name: data.instances[0].value }));
      }
      notify.success(`Loaded ${data.instances.length} IBM COS instance(s)`, undefined, { category: 'credentials' });
    },
    onError: (error) => {
      setIbmCosInstanceError(error instanceof Error ? error.message : 'Failed to load IBM COS instances');
      notifyError(error);
    },
  });

  const resetForm = () => {
    setIbmRegionOptions([]);
    setIbmRegionError(null);
    setIbmCosInstanceOptions([]);
    setIbmCosInstanceError(null);
    setFormData({
      name: '',
      description: '',
      provider: 'aws',
      aws_auth_method: 'access_keys',
      region: '',
      aws_profile: '',
      aws_access_key_id: '',
      aws_secret_access_key: '',
      aws_session_token: '',
      aws_sso_enabled: false,
      aws_sso_start_url: '',
      aws_sso_region: '',
      aws_sso_account_id: '',
      aws_sso_role_name: '',
      ibmcloud_api_key: '',
      ibm_cos_instance_name: '',
      gcp_project_id: '',
      gcp_credentials: '',
      is_default: false,
    });
  };

  const handleCreate = () => {
    createMutation.mutate(formData);
  };

  const handleEdit = (template: CloudCredentialTemplate) => {
    setEditingTemplate(template);
    setIbmRegionOptions([]);
    setIbmRegionError(null);
    setIbmCosInstanceOptions([]);
    setIbmCosInstanceError(null);
    setFormData({
      name: template.name,
      description: template.description || '',
      provider: template.provider,
      aws_auth_method: template.aws_auth_method || 'access_keys',
      region: template.region || '',
      aws_profile: template.aws_profile || '',
      aws_access_key_id: template.aws_access_key_id || '',
      aws_secret_access_key: '', // Don't populate existing secrets
      aws_session_token: '',
      aws_sso_enabled: template.aws_sso_enabled || false,
      aws_sso_start_url: template.aws_sso_start_url || '',
      aws_sso_region: template.aws_sso_region || '',
      aws_sso_account_id: template.aws_sso_account_id || '',
      aws_sso_role_name: template.aws_sso_role_name || '',
      ibmcloud_api_key: '',
      ibm_cos_instance_name: template.ibm_cos_instance_name || '',
      gcp_project_id: template.gcp_project_id || '',
      gcp_credentials: '',  // Don't populate existing secrets
      is_default: template.is_default,
    });
    setIsEditDialogOpen(true);
  };

  const handleUpdate = () => {
    if (!editingTemplate) return;

    // Only include fields that have values
    const updateData: Partial<CloudCredentialTemplateCreate> = {
      name: formData.name,
      description: formData.description,
      is_default: formData.is_default,
    };

    if (formData.provider === 'aws') {
      updateData.aws_auth_method = formData.aws_auth_method;
      updateData.region = formData.region;
      if (formData.aws_profile) updateData.aws_profile = formData.aws_profile;
      if (formData.aws_access_key_id) updateData.aws_access_key_id = formData.aws_access_key_id;
      if (formData.aws_secret_access_key) updateData.aws_secret_access_key = formData.aws_secret_access_key;
      if (formData.aws_session_token) updateData.aws_session_token = formData.aws_session_token;

      if (formData.aws_sso_enabled) {
        updateData.aws_sso_enabled = true;
        updateData.aws_sso_start_url = formData.aws_sso_start_url;
        updateData.aws_sso_region = formData.aws_sso_region;
        updateData.aws_sso_account_id = formData.aws_sso_account_id;
        updateData.aws_sso_role_name = formData.aws_sso_role_name;
      }
    }

    if (formData.provider === 'ibm') {
      updateData.region = formData.region;
      if (formData.ibmcloud_api_key) updateData.ibmcloud_api_key = formData.ibmcloud_api_key;
      if (formData.ibm_cos_instance_name) updateData.ibm_cos_instance_name = formData.ibm_cos_instance_name;
    }

    if (formData.provider === 'gcp') {
      if (formData.gcp_project_id) updateData.gcp_project_id = formData.gcp_project_id;
      if (formData.gcp_credentials) updateData.gcp_credentials = formData.gcp_credentials;
    }

    updateMutation.mutate({ id: editingTemplate.id, data: updateData });
  };

  const handleDelete = (template: CloudCredentialTemplate) => {
    if (template.projects_count > 0) {
      notify.error(`Cannot delete template. ${template.projects_count} project(s) are using it.`);
      return;
    }

    if (confirm(`Delete credential template "${template.name}"? This action cannot be undone.`)) {
      deleteMutation.mutate(template.id);
    }
  };

  useEffect(() => {
    if (formData.provider !== 'ibm') {
      setIbmRegionOptions([]);
      setIbmRegionError(null);
      setIbmCosInstanceOptions([]);
      setIbmCosInstanceError(null);
    }
  }, [formData.provider]);

  const handleLoadIBMRegions = () => {
    if (!formData.ibmcloud_api_key) {
      setIbmRegionError('Enter an IBM Cloud API key first');
      return;
    }
    loadIBMRegionsMutation.mutate(formData.ibmcloud_api_key);
  };

  const handleLoadIBMCosInstances = () => {
    if (!formData.ibmcloud_api_key) {
      setIbmCosInstanceError('Enter an IBM Cloud API key first');
      return;
    }
    loadIBMCosInstancesMutation.mutate(formData.ibmcloud_api_key);
  };

  const renderTemplateForm = () => (
    <div className="space-y-4">
      <div>
        <Label htmlFor="name">Template Name *</Label>
        <Input
          id="name"
          value={formData.name}
          onChange={(e) => setFormData({ ...formData, name: e.target.value })}
          placeholder="e.g., Production Credentials"
        />
      </div>

      <div>
        <Label htmlFor="description">Description</Label>
        <Textarea
          id="description"
          value={formData.description}
          onChange={(e) => setFormData({ ...formData, description: e.target.value })}
          placeholder="Optional description"
          rows={2}
        />
      </div>

      <div>
        <Label htmlFor="provider">Cloud Provider *</Label>
        <Select
          value={formData.provider}
          onValueChange={(value) => setFormData({ ...formData, provider: value })}
          disabled={!!editingTemplate} // Can't change provider after creation
        >
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {TEMPLATE_PROVIDER_OPTIONS.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {formData.provider === 'aws' && (
        <>
          <div>
            <Label>Authentication Method *</Label>
            <RadioGroup
              value={formData.aws_auth_method}
              onValueChange={(value) => setFormData({ ...formData, aws_auth_method: value })}
            >
              <div className="flex items-center space-x-2">
                <RadioGroupItem value="access_keys" id="access_keys" />
                <Label htmlFor="access_keys" className="font-normal cursor-pointer">
                  Access Keys (IAM User)
                </Label>
              </div>
              <div className="flex items-center space-x-2">
                <RadioGroupItem value="profile" id="profile" />
                <Label htmlFor="profile" className="font-normal cursor-pointer">
                  AWS Profile
                </Label>
              </div>
              <div className="flex items-center space-x-2">
                <RadioGroupItem value="sso" id="sso" />
                <Label htmlFor="sso" className="font-normal cursor-pointer">
                  AWS SSO
                </Label>
              </div>
            </RadioGroup>
          </div>

          {formData.aws_auth_method === 'profile' && (
            <div>
              <Label htmlFor="aws_profile">AWS Profile Name *</Label>
              <Input
                id="aws_profile"
                value={formData.aws_profile}
                onChange={(e) => setFormData({ ...formData, aws_profile: e.target.value })}
                placeholder="default"
              />
            </div>
          )}

          {formData.aws_auth_method === 'access_keys' && (
            <>
              <div>
                <Label htmlFor="aws_access_key_id">AWS Access Key ID *</Label>
                <Input
                  id="aws_access_key_id"
                  value={formData.aws_access_key_id}
                  onChange={(e) => setFormData({ ...formData, aws_access_key_id: e.target.value })}
                  placeholder="AKIAIOSFODNN7EXAMPLE"
                />
              </div>

              <div>
                <Label htmlFor="aws_secret_access_key">
                  AWS Secret Access Key {editingTemplate && '(leave blank to keep existing)'}
                </Label>
                <Input
                  id="aws_secret_access_key"
                  type="password"
                  value={formData.aws_secret_access_key}
                  onChange={(e) => setFormData({ ...formData, aws_secret_access_key: e.target.value })}
                  placeholder={editingTemplate ? '(existing key hidden)' : 'wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY'}
                />
              </div>

              <div>
                <Label htmlFor="aws_session_token">
                  AWS Session Token (optional) {editingTemplate && '(leave blank to keep existing)'}
                </Label>
                <Input
                  id="aws_session_token"
                  type="password"
                  value={formData.aws_session_token}
                  onChange={(e) => setFormData({ ...formData, aws_session_token: e.target.value })}
                  placeholder="For temporary credentials"
                />
              </div>
            </>
          )}

          {formData.aws_auth_method === 'sso' && (
            <>
              <div className="flex items-center space-x-2">
                <Switch
                  checked={formData.aws_sso_enabled}
                  onCheckedChange={(checked) => setFormData({ ...formData, aws_sso_enabled: checked })}
                />
                <Label>Enable AWS SSO</Label>
              </div>

              {formData.aws_sso_enabled && (
                <>
                  <div>
                    <Label htmlFor="aws_sso_start_url">SSO Start URL *</Label>
                    <Input
                      id="aws_sso_start_url"
                      value={formData.aws_sso_start_url}
                      onChange={(e) => setFormData({ ...formData, aws_sso_start_url: e.target.value })}
                      placeholder="https://my-company.awsapps.com/start"
                    />
                  </div>

                  <div>
                    <Label htmlFor="aws_sso_region">SSO Region *</Label>
                    <Input
                      id="aws_sso_region"
                      value={formData.aws_sso_region}
                      onChange={(e) => setFormData({ ...formData, aws_sso_region: e.target.value })}
                      placeholder="e.g., us-east-1"
                    />
                  </div>

                  <div>
                    <Label htmlFor="aws_sso_account_id">Account ID *</Label>
                    <Input
                      id="aws_sso_account_id"
                      value={formData.aws_sso_account_id}
                      onChange={(e) => setFormData({ ...formData, aws_sso_account_id: e.target.value })}
                      placeholder="123456789012"
                    />
                  </div>

                  <div>
                    <Label htmlFor="aws_sso_role_name">Role Name *</Label>
                    <Input
                      id="aws_sso_role_name"
                      value={formData.aws_sso_role_name}
                      onChange={(e) => setFormData({ ...formData, aws_sso_role_name: e.target.value })}
                      placeholder="AdministratorAccess"
                    />
                  </div>
                </>
              )}
            </>
          )}

          <div>
            <Label htmlFor="region">Default Region *</Label>
            <RegionSelector
              value={formData.region || ''}
              onValueChange={(value) => setFormData({ ...formData, region: value })}
            />
            <p className="text-xs text-muted-foreground mt-1">
              Projects using this template will inherit this region by default
            </p>
          </div>
        </>
      )}

      {formData.provider === 'ibm' && (
        <>
          <div>
            <Label htmlFor="ibmcloud_api_key">
              IBM Cloud API Key {editingTemplate && '(leave blank to keep existing)'}
            </Label>
            <Input
              id="ibmcloud_api_key"
              type="password"
              value={formData.ibmcloud_api_key || ''}
              onChange={(e) => setFormData({ ...formData, ibmcloud_api_key: e.target.value })}
              placeholder={editingTemplate ? '(existing key hidden)' : 'Enter IBM Cloud API key'}
            />
          </div>

          <div>
            <div className="flex items-center justify-between gap-3">
              <Label htmlFor="region">Default Region (visible to your account)</Label>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={handleLoadIBMRegions}
                disabled={loadIBMRegionsMutation.isPending}
              >
                {loadIBMRegionsMutation.isPending ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    Loading Regions...
                  </>
                ) : (
                  'Load Regions'
                )}
              </Button>
            </div>
            <div className="mt-2">
              <CloudRegionSelector
                provider="ibm"
                value={formData.region || ''}
                onValueChange={(value) => setFormData({ ...formData, region: value })}
                options={ibmRegionOptions}
              />
            </div>
            {ibmRegionError && <p className="text-xs text-destructive mt-1">{ibmRegionError}</p>}
            <p className="text-xs text-muted-foreground mt-1">
              Load regions from IBM Cloud using the API key above, or enter one manually.
            </p>
          </div>

          <div>
            <Label htmlFor="ibmcloud_resource_group">IBM Resource Group</Label>
            <Input
              id="ibmcloud_resource_group"
              value={formData.ibmcloud_resource_group || ''}
              onChange={(e) => setFormData({ ...formData, ibmcloud_resource_group: e.target.value })}
              placeholder="default"
            />
            <p className="text-xs text-muted-foreground mt-1">
              Projects and blueprints using this template can inherit the IBM Cloud resource group automatically.
            </p>
          </div>

          <div>
            <div className="flex items-center justify-between gap-3">
              <Label htmlFor="ibm_cos_instance_name">IBM COS Instance Name</Label>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={handleLoadIBMCosInstances}
                disabled={loadIBMCosInstancesMutation.isPending}
              >
                {loadIBMCosInstancesMutation.isPending ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    Loading Instances...
                  </>
                ) : (
                  'Load COS Instances'
                )}
              </Button>
            </div>
            <div className="mt-2">
              <Select
                value={formData.ibm_cos_instance_name || ''}
                onValueChange={(value) => setFormData({ ...formData, ibm_cos_instance_name: value })}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Select IBM COS instance" />
                </SelectTrigger>
                <SelectContent>
                  {ibmCosInstanceOptions.map((instance) => (
                    <SelectItem key={instance.crn} value={instance.value}>
                      {instance.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {ibmCosInstanceError && <p className="text-xs text-destructive mt-1">{ibmCosInstanceError}</p>}
            <p className="text-xs text-muted-foreground mt-1">
              Select the IBM Cloud Object Storage instance to use for remote state. BNK-Forge will handle the required COS access behind the scenes.
            </p>
          </div>
        </>
      )}

      {formData.provider === 'gcp' && (
        <>
          <div>
            <Label htmlFor="gcp_project_id">GCP Project ID *</Label>
            <Input
              id="gcp_project_id"
              value={formData.gcp_project_id}
              onChange={(e) => setFormData({ ...formData, gcp_project_id: e.target.value })}
              placeholder="my-gcp-project-123456"
            />
          </div>

          <div>
            <Label htmlFor="gcp_credentials">
              Service Account JSON Key {editingTemplate && '(leave blank to keep existing)'}
            </Label>
            <Textarea
              id="gcp_credentials"
              value={formData.gcp_credentials}
              onChange={(e) => setFormData({ ...formData, gcp_credentials: e.target.value })}
              placeholder={editingTemplate ? '(existing key hidden)' : '{ "type": "service_account", "project_id": "...", ... }'}
              rows={8}
              className="font-mono text-xs"
            />
            <p className="text-xs text-muted-foreground mt-1">
              Paste the full JSON key for a service account with at least <code>roles/container.viewer</code> on the project.
              Used to mint OAuth tokens for GKE; the gcloud CLI is not required.
            </p>
          </div>
        </>
      )}

      {/* SSH credentials are now managed in the dedicated SSH Credentials section above */}

      <div className="flex items-center space-x-2">
        <Switch
          checked={formData.is_default}
          onCheckedChange={(checked) => setFormData({ ...formData, is_default: checked })}
        />
        <Label>Set as default template for {formData.provider.toUpperCase()}</Label>
      </div>
    </div>
  );

  return (
    <SectionCard title="Cloud Credential Templates">
      <p className="text-sm text-muted-foreground -mt-1 mb-6">
        Cloud provider credentials (AWS, GCP, Azure, IBM Cloud) for cloud-managed projects.
        For on-prem/SSH access, use SSH Credentials above.
      </p>
      <div className="space-y-6">
        <div className="flex items-center gap-2">
          <Select value={filterProvider} onValueChange={setFilterProvider}>
            <SelectTrigger className="w-48">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Providers</SelectItem>
              <SelectItem value="aws">AWS Only</SelectItem>
              <SelectItem value="gcp">GCP Only</SelectItem>
              <SelectItem value="azure">Azure Only</SelectItem>
              <SelectItem value="ibm">IBM Only</SelectItem>
            </SelectContent>
          </Select>

          <Button
            data-onboarding="create-auth"
            onClick={() => setIsCreateDialogOpen(true)}
          >
            <Plus className="h-4 w-4 mr-2" />
            New Template
          </Button>
        </div>

        {isLoading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : templates && templates.length > 0 ? (
          <div className="border border-border rounded-md overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="text-xs uppercase tracking-wider text-muted-foreground">
                    Template
                  </TableHead>
                  <TableHead className="text-xs uppercase tracking-wider text-muted-foreground">
                    Provider
                  </TableHead>
                  <TableHead className="text-xs uppercase tracking-wider text-muted-foreground">
                    Details
                  </TableHead>
                  <TableHead className="text-xs uppercase tracking-wider text-muted-foreground">
                    Status
                  </TableHead>
                  <TableHead className="text-xs uppercase tracking-wider text-muted-foreground">
                    Used by
                  </TableHead>
                  <TableHead className="text-xs uppercase tracking-wider text-muted-foreground text-right w-16">
                    {/* actions */}
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {templates.map((template) => {
                  const isRowTesting =
                    testingTemplateId === template.id && testMutation.isPending;
                  const isLegacySsh = template.provider === 'ssh';

                  return (
                    <TableRow key={template.id}>
                      {/* Template name + badges */}
                      <TableCell>
                        <div className="flex flex-col gap-1">
                          <div className="flex items-center gap-2 flex-wrap">
                            {isLegacySsh ? (
                              <Terminal className="h-4 w-4 text-muted-foreground" />
                            ) : (
                              <Cloud className="h-4 w-4 text-muted-foreground" />
                            )}
                            <span className="font-medium text-foreground">{template.name}</span>
                            {template.is_default && (
                              <Badge variant="default">Default</Badge>
                            )}
                          </div>
                          {template.description && (
                            <span className="text-xs text-muted-foreground line-clamp-1">
                              {template.description}
                            </span>
                          )}
                        </div>
                      </TableCell>

                      {/* Provider badge */}
                      <TableCell>
                        <div className="flex flex-col gap-1">
                          <Badge variant="outline">
                            {isLegacySsh ? 'SSH / On-Prem' : template.provider.toUpperCase()}
                          </Badge>
                          {isLegacySsh && (
                            <Badge variant="secondary" className="text-[10px]">
                              Legacy — use SSH Credentials instead
                            </Badge>
                          )}
                        </div>
                      </TableCell>

                      {/* Provider-specific detail */}
                      <TableCell>
                        <div className="text-xs text-muted-foreground space-y-0.5">
                          {template.provider === 'aws' && (
                            <>
                              <div>Method: {template.aws_auth_method}</div>
                              {template.region && <div>Region: {template.region}</div>}
                              {template.aws_profile && <div>Profile: {template.aws_profile}</div>}
                              {template.has_aws_secret_access_key && (
                                <div className="flex items-center gap-1 text-success">
                                  <CheckCircle className="h-3 w-3" />
                                  Credentials Configured
                                </div>
                              )}
                              {template.aws_sso_enabled && template.aws_sso_authenticated_at && (
                                <div className="flex items-center gap-1 text-primary">
                                  <Shield className="h-3 w-3" />
                                  SSO Authenticated
                                </div>
                              )}
                            </>
                          )}
                          {isLegacySsh && (
                            <>
                              <div className="flex items-center gap-1">
                                <span className="font-mono">
                                  {template.ssh_username}@{template.ssh_host}:{template.ssh_port || 22}
                                </span>
                              </div>
                              <div>Auth: {template.ssh_auth_type === 'key' ? 'SSH Key' : 'Password'}</div>
                              {(template.has_ssh_password || template.has_ssh_key) && (
                                <div className="flex items-center gap-1 text-success">
                                  <CheckCircle className="h-3 w-3" />
                                  Credentials Configured
                                </div>
                              )}
                            </>
                          )}
                          {template.provider === 'ibm' && (
                            <>
                              {template.region && <div>Region: {template.region}</div>}
                              {template.ibmcloud_resource_group && (
                                <div>Resource Group: {template.ibmcloud_resource_group}</div>
                              )}
                              {template.has_ibmcloud_api_key && (
                                <div className="flex items-center gap-1 text-success">
                                  <CheckCircle className="h-3 w-3" />
                                  Credentials Configured
                                </div>
                              )}
                              {template.ibm_cos_instance_name && (
                                <div className="flex items-center gap-1 text-success">
                                  <CheckCircle className="h-3 w-3" />
                                  COS Instance: {template.ibm_cos_instance_name}
                                </div>
                              )}
                            </>
                          )}
                        </div>
                      </TableCell>

                      {/* Status badge (AWS only) */}
                      <TableCell>
                        {template.provider === 'aws' && (
                          <AwsCredStatusBadge template={template} />
                        )}
                      </TableCell>

                      {/* Used by */}
                      <TableCell>
                        <span className="text-xs text-muted-foreground">
                          Used by {template.projects_count} project(s)
                        </span>
                      </TableCell>

                      {/* Actions dropdown */}
                      <TableCell className="text-right">
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-8 w-8 p-0"
                              aria-label="Template actions"
                            >
                              <MoreVertical className="h-4 w-4" />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            {(template.provider === 'aws' || template.provider === 'ibm') && (
                              <DropdownMenuItem
                                onClick={() => testMutation.mutate(template.id)}
                                disabled={testMutation.isPending}
                              >
                                {isRowTesting ? (
                                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                                ) : (
                                  <TestTube2 className="h-4 w-4 mr-2" />
                                )}
                                Test connection
                              </DropdownMenuItem>
                            )}
                             {template.provider === 'aws' &&
                              (template.aws_sso_enabled ||
                                template.aws_auth_method === 'sso' ||
                                !!(template.aws_sso_start_url && template.aws_sso_region && template.aws_sso_account_id && template.aws_sso_role_name)) && (
                              <DropdownMenuItem
                                onClick={() => {
                                  setSsoAuthTemplateId(template.id);
                                  setSsoAuthTemplateName(template.name);
                                  setSsoAuthDialogOpen(true);
                                }}
                              >
                                <Shield className="h-4 w-4 mr-2" />
                                Authenticate SSO
                              </DropdownMenuItem>
                            )}
                            {/* Legacy SSH templates are read-only — managed in SSH Credentials */}
                            {!isLegacySsh && (
                              <>
                                {(template.provider === 'aws' || template.provider === 'ibm') && (
                                  <DropdownMenuSeparator />
                                )}
                                <DropdownMenuItem onClick={() => handleEdit(template)}>
                                  <Edit className="h-4 w-4 mr-2" />
                                  Edit
                                </DropdownMenuItem>
                                <DropdownMenuItem
                                  onClick={() => handleDelete(template)}
                                  disabled={template.projects_count > 0}
                                >
                                  <Trash2 className="h-4 w-4 mr-2 text-destructive" />
                                  Delete
                                </DropdownMenuItem>
                              </>
                            )}
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
            <Key className="h-12 w-12 mx-auto mb-4 opacity-50" />
            <p>No credential templates found</p>
            <p className="text-xs mt-2">Create a template to get started</p>
          </div>
        )}

        {/* Create Dialog */}
        <Dialog
          open={isCreateDialogOpen}
          onOpenChange={(open) => {
            setIsCreateDialogOpen(open);
            if (!open) {
              resetForm();
            }
          }}
        >
          <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
            <DialogHeader>
              <DialogTitle>Create Credential Template</DialogTitle>
              <DialogDescription>
                Create a reusable credential template for your projects. Credentials are encrypted at rest.
              </DialogDescription>
            </DialogHeader>
            {renderTemplateForm()}
            <DialogFooter>
              <Button variant="outline" onClick={() => setIsCreateDialogOpen(false)}>
                Cancel
              </Button>
              <Button onClick={handleCreate} disabled={createMutation.isPending}>
                {createMutation.isPending ? (
                  <><Loader2 className="h-4 w-4 mr-2 animate-spin" /> Creating...</>
                ) : (
                  'Create Template'
                )}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* Edit Dialog */}
        <Dialog
          open={isEditDialogOpen}
          onOpenChange={(open) => {
            setIsEditDialogOpen(open);
            if (!open) {
              setEditingTemplate(null);
              resetForm();
            }
          }}
        >
          <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
            <DialogHeader>
              <DialogTitle>Edit Credential Template</DialogTitle>
              <DialogDescription>
                Update credential template. Leave sensitive fields blank to keep existing values.
              </DialogDescription>
            </DialogHeader>
            {renderTemplateForm()}
            <DialogFooter>
              <Button variant="outline" onClick={() => setIsEditDialogOpen(false)}>
                Cancel
              </Button>
              <Button onClick={handleUpdate} disabled={updateMutation.isPending}>
                {updateMutation.isPending ? (
                  <><Loader2 className="h-4 w-4 mr-2 animate-spin" /> Updating...</>
                ) : (
                  'Update Template'
                )}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* SSO Authentication Dialog */}
        {ssoAuthTemplateId && (
          <SSOAuthDialog
            open={ssoAuthDialogOpen}
            onOpenChange={setSsoAuthDialogOpen}
            templateId={ssoAuthTemplateId}
            templateName={ssoAuthTemplateName}
            onSuccess={() => {
              queryClient.invalidateQueries({ queryKey: ['credential-templates'] });
            }}
          />
        )}
      </div>
    </SectionCard>
  );
}
