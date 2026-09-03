import type { ReactNode } from 'react';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
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
  id?: string;
  label?: ReactNode;
}

export function CloudRegionSelector({
  provider,
  value,
  onValueChange,
  disabled,
  placeholder = 'Enter region',
  options = [],
  id = 'cloud-region-input',
  label,
}: CloudRegionSelectorProps) {
  if (provider === 'aws') {
    return (
      <div className="space-y-2">
        {label ? (
          <Label htmlFor={id}>{label}</Label>
        ) : null}
        <RegionSelector
          value={value}
          onValueChange={onValueChange}
          disabled={disabled}
          placeholder={placeholder}
          id={id}
        />
      </div>
    );
  }

  const listId = `${id}-${provider}-suggestions`;

  return (
    <div className="space-y-2">
      {label ? (
        <Label htmlFor={id}>{label}</Label>
      ) : (
        <Label htmlFor={id} className="sr-only">
          Region
        </Label>
      )}
      <Input
        id={id}
        list={options.length > 0 ? listId : undefined}
        value={value}
        onChange={(e) => onValueChange(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
      />
      {options.length > 0 && (
        <datalist id={listId}>
          {options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </datalist>
      )}
    </div>
  );
}

