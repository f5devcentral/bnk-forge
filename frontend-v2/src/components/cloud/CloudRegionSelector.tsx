import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { RegionSelector } from '@/components/aws/RegionSelector';

export interface CloudRegionOption {
  value: string;
  label: string;
}

interface CloudRegionSelectorProps {
  provider: string;
  value: string;
  onValueChange: (value: string) => void;
  disabled?: boolean;
  placeholder?: string;
  options?: CloudRegionOption[];
}

export function CloudRegionSelector({
  provider,
  value,
  onValueChange,
  disabled,
  placeholder = 'Select region',
  options = [],
}: CloudRegionSelectorProps) {
  if (provider === 'aws') {
    return <RegionSelector value={value} onValueChange={onValueChange} disabled={disabled} />;
  }

  if (options.length > 0) {
    return (
      <Select value={value} onValueChange={onValueChange} disabled={disabled}>
        <SelectTrigger>
          <SelectValue placeholder={placeholder} />
        </SelectTrigger>
        <SelectContent>
          {options.map((option) => (
            <SelectItem key={option.value} value={option.value}>
              {option.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    );
  }

  if (provider === 'ibm') {
    return (
      <div className="space-y-2">
        <Input
          value={value}
          onChange={(e) => onValueChange(e.target.value)}
          placeholder="e.g., us-south"
          disabled={disabled}
        />
        <p className="text-xs text-muted-foreground">
          No IBM regions loaded yet. Enter a region manually or load them from IBM Cloud.
        </p>
      </div>
    );
  }

  if (provider === 'azure') {
    return (
      <div className="space-y-2">
        <Input
          value={value}
          onChange={(e) => onValueChange(e.target.value)}
          placeholder="e.g., eastus"
          disabled={disabled}
        />
        <p className="text-xs text-muted-foreground">
          Enter an Azure location (e.g., eastus, westeurope, southeastasia).
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <Label className="sr-only">Region</Label>
      <Input
        value={value}
        onChange={(e) => onValueChange(e.target.value)}
        placeholder="Enter region"
        disabled={disabled}
      />
    </div>
  );
}
