import { useId } from 'react';
import { Input } from '@/components/ui/input';
import { AWS_REGIONS } from '@/lib/aws-regions';

interface RegionSelectorProps {
  value: string;
  onValueChange: (value: string) => void;
  disabled?: boolean;
  className?: string;
  placeholder?: string;
  id?: string;
}

export function RegionSelector({
  value,
  onValueChange,
  disabled,
  className,
  placeholder = 'e.g. us-east-1',
  id,
}: RegionSelectorProps) {
  const generatedId = useId();
  const inputId = id ?? generatedId;
  return (
    <div className="relative">
      <Input
        id={inputId}
        list={`${inputId}-suggestions`}
        value={value}
        onChange={(e) => onValueChange(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
        className={className}
      />
      <datalist id={`${inputId}-suggestions`}>
        {AWS_REGIONS.map((region) => (
          <option key={region.value} value={region.value}>
            {region.flag} {region.label}
          </option>
        ))}
      </datalist>
    </div>
  );
}
