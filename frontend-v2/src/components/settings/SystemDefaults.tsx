import { useState, useEffect } from 'react';
import { SectionCard } from '@/components/ui/section-card';
import { Label } from '@/components/ui/label';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Loader2, Save, Globe, FolderGit2 } from 'lucide-react';
import { useSystemDefaults } from '@/hooks/useSettings';
import { AWS_REGIONS } from '@/lib/aws-regions';

// Azure Regions (commonly used)
const AZURE_REGIONS = [
  { value: 'eastus', label: 'East US' },
  { value: 'eastus2', label: 'East US 2' },
  { value: 'westus', label: 'West US' },
  { value: 'westus2', label: 'West US 2' },
  { value: 'westus3', label: 'West US 3' },
  { value: 'centralus', label: 'Central US' },
  { value: 'northeurope', label: 'North Europe' },
  { value: 'westeurope', label: 'West Europe' },
  { value: 'uksouth', label: 'UK South' },
  { value: 'ukwest', label: 'UK West' },
  { value: 'australiaeast', label: 'Australia East' },
  { value: 'southeastasia', label: 'Southeast Asia' },
  { value: 'japaneast', label: 'Japan East' },
];

// GCP Regions (commonly used)
const GCP_REGIONS = [
  { value: 'us-central1', label: 'US Central (Iowa)' },
  { value: 'us-east1', label: 'US East (South Carolina)' },
  { value: 'us-east4', label: 'US East (N. Virginia)' },
  { value: 'us-west1', label: 'US West (Oregon)' },
  { value: 'us-west2', label: 'US West (Los Angeles)' },
  { value: 'europe-west1', label: 'Europe West (Belgium)' },
  { value: 'europe-west2', label: 'Europe West (London)' },
  { value: 'europe-west3', label: 'Europe West (Frankfurt)' },
  { value: 'asia-east1', label: 'Asia East (Taiwan)' },
  { value: 'asia-southeast1', label: 'Asia Southeast (Singapore)' },
  { value: 'australia-southeast1', label: 'Australia Southeast (Sydney)' },
];

const IBM_REGIONS = [
  { value: 'us-south', label: 'US South (Dallas)' },
  { value: 'us-east', label: 'US East (Washington DC)' },
  { value: 'eu-de', label: 'EU Germany (Frankfurt)' },
  { value: 'eu-gb', label: 'EU United Kingdom (London)' },
  { value: 'jp-tok', label: 'Japan (Tokyo)' },
  { value: 'au-syd', label: 'Australia (Sydney)' },
  { value: 'ca-tor', label: 'Canada (Toronto)' },
  { value: 'br-sao', label: 'Brazil (Sao Paulo)' },
];

export default function SystemDefaults() {
  const { defaults, isLoading, updateDefaults, isUpdating } = useSystemDefaults();

  // Form state
  const [formValues, setFormValues] = useState<Record<string, string>>({});
  const [hasChanges, setHasChanges] = useState(false);

  // Initialize form values from defaults
  useEffect(() => {
    if (defaults) {
      const values: Record<string, string> = {};

      // Flatten all categories into form values
      Object.values(defaults).forEach((category: Record<string, { key: string; raw_value?: string }>) => {
        Object.values(category).forEach((setting: { key: string; raw_value?: string }) => {
          values[setting.key] = setting.raw_value || '';
        });
      });

      setFormValues(values);
      setHasChanges(false);
    }
  }, [defaults]);

  const handleChange = (key: string, value: string) => {
    setFormValues(prev => ({ ...prev, [key]: value }));
    setHasChanges(true);
  };

  const handleSave = () => {
    // Only send changed values
    const updates: Record<string, string> = {};

    if (defaults) {
      Object.values(defaults).forEach((category: Record<string, { key: string; raw_value?: string }>) => {
        Object.values(category).forEach((setting: { key: string; raw_value?: string }) => {
          const newValue = formValues[setting.key];
          if (newValue !== setting.raw_value) {
            updates[setting.key] = newValue;
          }
        });
      });
    }

    if (Object.keys(updates).length > 0) {
      updateDefaults(updates);
      setHasChanges(false);
    }
  };

  const handleReset = () => {
    if (defaults) {
      const values: Record<string, string> = {};
      Object.values(defaults).forEach((category: Record<string, { key: string; raw_value?: string }>) => {
        Object.values(category).forEach((setting: { key: string; raw_value?: string }) => {
          values[setting.key] = setting.raw_value || '';
        });
      });
      setFormValues(values);
      setHasChanges(false);
    }
  };

  if (isLoading) {
    return (
      <div className="flex justify-center p-8">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header with Save Button */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-semibold text-foreground">
            System Defaults
          </h3>
          <p className="text-sm text-muted-foreground">
            All settings must be configured before using BNK-Forge. No hardcoded defaults.
          </p>
        </div>
        <div className="flex gap-2">
          {hasChanges && (
            <Button
              variant="outline"
              onClick={handleReset}
            >
              Reset
            </Button>
          )}
          <Button
            onClick={handleSave}
            disabled={isUpdating || !hasChanges}
          >
            {isUpdating ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                Saving...
              </>
            ) : (
              <>
                <Save className="h-4 w-4 mr-2" />
                Save Changes
              </>
            )}
          </Button>
        </div>
      </div>

      {/* Project Defaults */}
      <SectionCard title="Project defaults">
        <p className="text-sm text-muted-foreground -mt-1 mb-4">
          Default settings for new projects
        </p>
        <div>
          <div className="space-y-2">
            <Label>
              <span className="flex items-center gap-2">
                <FolderGit2 className="h-4 w-4" />
                Default Project Type
              </span>
            </Label>
            <Select
              value={formValues['project.default_type'] || ''}
              onValueChange={(value) => handleChange('project.default_type', value)}
            >
              <SelectTrigger>
                <SelectValue placeholder="Select default type" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="cloud-aws">AWS Cloud</SelectItem>
                <SelectItem value="cloud-azure">Azure Cloud</SelectItem>
                <SelectItem value="cloud-gcp">GCP Cloud</SelectItem>
                <SelectItem value="cloud-ibm">IBM Cloud</SelectItem>
                <SelectItem value="kubernetes">On-Premises (Bare Metal / VM)</SelectItem>
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              Pre-selects project type when creating new projects
            </p>
          </div>
        </div>
      </SectionCard>

      {/* Cloud Provider Regions */}
      <SectionCard title="Cloud provider defaults">
        <p className="text-sm text-muted-foreground -mt-1 mb-4">
          Default regions used when creating new projects and credential templates
        </p>
        <div className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
            {/* AWS Region */}
            <div className="space-y-2">
              <Label htmlFor="system-default-aws-region">
                <span className="flex items-center gap-2">
                  <Globe className="h-4 w-4" />
                  AWS Region
                </span>
              </Label>
              <Input
                id="system-default-aws-region"
                list="system-default-aws-region-suggestions"
                value={formValues['cloud.aws.default_region'] || ''}
                onChange={(e) => handleChange('cloud.aws.default_region', e.target.value)}
                placeholder="e.g. us-east-1"
              />
              <datalist id="system-default-aws-region-suggestions">
                {AWS_REGIONS.map((region) => (
                  <option key={region.value} value={region.value}>
                    {region.label}
                  </option>
                ))}
              </datalist>
            </div>

            {/* Azure Region */}
            <div className="space-y-2">
              <Label htmlFor="system-default-azure-region">
                <span className="flex items-center gap-2">
                  <Globe className="h-4 w-4" />
                  Azure Default Region
                </span>
              </Label>
              <Input
                id="system-default-azure-region"
                list="system-default-azure-region-suggestions"
                value={formValues['cloud.azure.default_region'] || ''}
                onChange={(e) => handleChange('cloud.azure.default_region', e.target.value)}
                placeholder="e.g. westeurope"
              />
              <datalist id="system-default-azure-region-suggestions">
                {AZURE_REGIONS.map((region) => (
                  <option key={region.value} value={region.value}>
                    {region.label}
                  </option>
                ))}
              </datalist>
            </div>

            {/* GCP Region */}
            <div className="space-y-2">
              <Label htmlFor="system-default-gcp-region">
                <span className="flex items-center gap-2">
                  <Globe className="h-4 w-4" />
                  GCP Default Region
                </span>
              </Label>
              <Input
                id="system-default-gcp-region"
                list="system-default-gcp-region-suggestions"
                value={formValues['cloud.gcp.default_region'] || ''}
                onChange={(e) => handleChange('cloud.gcp.default_region', e.target.value)}
                placeholder="e.g. us-central1"
              />
              <datalist id="system-default-gcp-region-suggestions">
                {GCP_REGIONS.map((region) => (
                  <option key={region.value} value={region.value}>
                    {region.label}
                  </option>
                ))}
              </datalist>
            </div>

            {/* IBM Region */}
            <div className="space-y-2">
              <Label htmlFor="system-default-ibm-region">
                <span className="flex items-center gap-2">
                  <Globe className="h-4 w-4" />
                  IBM Default Region
                </span>
              </Label>
              <Input
                id="system-default-ibm-region"
                list="system-default-ibm-region-suggestions"
                value={formValues['cloud.ibm.default_region'] || ''}
                onChange={(e) => handleChange('cloud.ibm.default_region', e.target.value)}
                placeholder="e.g. eu-de"
              />
              <datalist id="system-default-ibm-region-suggestions">
                {IBM_REGIONS.map((region) => (
                  <option key={region.value} value={region.value}>
                    {region.label}
                  </option>
                ))}
              </datalist>
            </div>
          </div>
        </div>
      </SectionCard>

      {/* OpenTofu Timeouts */}
      <SectionCard title="OpenTofu timeouts">
        <p className="text-sm text-muted-foreground -mt-1 mb-4">
          Maximum time allowed for each OpenTofu operation (in seconds)
        </p>
        <div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div className="space-y-2">
              <Label>Init Timeout</Label>
              <div className="flex items-center gap-2">
                <Input
                  type="number"
                  value={formValues['opentofu.timeout.init'] || ''}
                  onChange={(e) => handleChange('opentofu.timeout.init', e.target.value)}
                  className="w-24"
                />
                <span className="text-sm text-muted-foreground">sec</span>
              </div>
              <p className="text-xs text-muted-foreground">Default: 300 (5 min)</p>
            </div>

            <div className="space-y-2">
              <Label>Plan Timeout</Label>
              <div className="flex items-center gap-2">
                <Input
                  type="number"
                  value={formValues['opentofu.timeout.plan'] || ''}
                  onChange={(e) => handleChange('opentofu.timeout.plan', e.target.value)}
                  className="w-24"
                />
                <span className="text-sm text-muted-foreground">sec</span>
              </div>
              <p className="text-xs text-muted-foreground">Default: 600 (10 min)</p>
            </div>

            <div className="space-y-2">
              <Label>Apply Timeout</Label>
              <div className="flex items-center gap-2">
                <Input
                  type="number"
                  value={formValues['opentofu.timeout.apply'] || ''}
                  onChange={(e) => handleChange('opentofu.timeout.apply', e.target.value)}
                  className="w-24"
                />
                <span className="text-sm text-muted-foreground">sec</span>
              </div>
              <p className="text-xs text-muted-foreground">Default: 1800 (30 min)</p>
            </div>

            <div className="space-y-2">
              <Label>Destroy Timeout</Label>
              <div className="flex items-center gap-2">
                <Input
                  type="number"
                  value={formValues['opentofu.timeout.destroy'] || ''}
                  onChange={(e) => handleChange('opentofu.timeout.destroy', e.target.value)}
                  className="w-24"
                />
                <span className="text-sm text-muted-foreground">sec</span>
              </div>
              <p className="text-xs text-muted-foreground">Default: 1800 (30 min)</p>
            </div>
          </div>
        </div>
      </SectionCard>

      {/* Execution Settings */}
      <SectionCard title="Execution settings">
        <p className="text-sm text-muted-foreground -mt-1 mb-4">
          Retry behavior for failed operations
        </p>
        <div>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label>Max Retries</Label>
              <Input
                type="number"
                min="0"
                max="10"
                value={formValues['execution.max_retries'] || ''}
                onChange={(e) => handleChange('execution.max_retries', e.target.value)}
                className="w-24"
              />
              <p className="text-xs text-muted-foreground">Number of retry attempts (0-10)</p>
            </div>

            <div className="space-y-2">
              <Label>Retry Delay</Label>
              <div className="flex items-center gap-2">
                <Input
                  type="number"
                  min="1"
                  max="60"
                  value={formValues['execution.retry_delay'] || ''}
                  onChange={(e) => handleChange('execution.retry_delay', e.target.value)}
                  className="w-24"
                />
                <span className="text-sm text-muted-foreground">sec</span>
              </div>
              <p className="text-xs text-muted-foreground">Delay between retries (1-60)</p>
            </div>
          </div>
        </div>
      </SectionCard>
    </div>
  );
}
