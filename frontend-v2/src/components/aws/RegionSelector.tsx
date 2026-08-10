import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from '@/components/ui/select';
import { AWS_REGIONS_GROUPED, CONTINENT_ORDER } from '@/lib/aws-regions';

interface RegionSelectorProps {
  value: string;
  onValueChange: (value: string) => void;
  disabled?: boolean;
  className?: string;
}

export function RegionSelector({ value, onValueChange, disabled, className }: RegionSelectorProps) {
  return (
    <Select value={value} onValueChange={onValueChange} disabled={disabled}>
      <SelectTrigger className={className}>
        <SelectValue placeholder="Select region" />
      </SelectTrigger>
      <SelectContent className="max-h-[400px]">
        {CONTINENT_ORDER.map((continent) => {
          const regions = AWS_REGIONS_GROUPED[continent];
          if (!regions || regions.length === 0) return null;

          return (
            <SelectGroup key={continent}>
              <SelectLabel>{continent}</SelectLabel>
              {regions.map((region) => (
                <SelectItem key={region.value} value={region.value}>
                  <span className="flex items-center gap-2">
                    <span>{region.flag}</span>
                    <span>{region.label}</span>
                  </span>
                </SelectItem>
              ))}
            </SelectGroup>
          );
        })}
      </SelectContent>
    </Select>
  );
}
