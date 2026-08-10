/**
 * DOCA Release Picker — a combo dropdown+text field for selecting a DOCA
 * release from the unified BFB+DOCA catalog or typing a custom value.
 *
 * Rendered by ModuleVariableEditor for the `doca_release_id` variable.
 * The value is the catalog row's integer ID (as a string). At apply-time,
 * probe-dpu looks up the row by ID and resolves both BFB + host-package URLs.
 */
import { useEffect, useState } from 'react';
import { useBluefieldImages } from '@/hooks/useBluefieldImages';
import type { BluefieldImage } from '@/lib/api/bluefield-images';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { HelpCircle, Cpu } from 'lucide-react';

interface DocaPickerVariable {
  name: string;
  type?: string;
  description?: string;
  required?: boolean;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  default?: any;
}

interface DocaReleasePickerProps {
  variable: DocaPickerVariable;
  value: string;
  onChange: (value: string) => void;
  hasError: boolean;
  renderValidationState: () => React.ReactNode;
}

const CUSTOM_VALUE = '__custom__';

/** Filter to entries that have a doca_version set. */
function docaEntries(images: BluefieldImage[] | undefined): BluefieldImage[] {
  return (images ?? []).filter((img) => img.doca_version);
}

export function DocaReleasePicker({
  variable,
  value,
  onChange,
  hasError,
  renderValidationState,
}: DocaReleasePickerProps) {
  const { data: images, isLoading } = useBluefieldImages();
  const [isCustom, setIsCustom] = useState(false);

  const entries = docaEntries(images);

  // Determine if current value matches a catalog entry's ID
  const matchesCatalog = entries.some((img) => String(img.id) === value);

  // If the user has typed something not in the catalog, show custom input
  useEffect(() => {
    if (value && images && !matchesCatalog) {
      setIsCustom(true);
    }
  }, [value, images, matchesCatalog]);

  // Find the default image to auto-select when no value is set
  const defaultImage = entries.find((img) => img.is_default);

  // Auto-select default when value is empty and catalog is loaded
  useEffect(() => {
    if (!value && defaultImage && !isCustom) {
      onChange(String(defaultImage.id));
    }
  }, [defaultImage, value, isCustom, onChange]);

  const handleSelectChange = (selected: string) => {
    if (selected === CUSTOM_VALUE) {
      setIsCustom(true);
      onChange('');
    } else {
      setIsCustom(false);
      onChange(selected);
    }
  };

  const displayLabel = (img: BluefieldImage) => {
    let label = `DOCA ${img.doca_version}`;
    if (img.host_os) label += ` \u2014 ${img.host_os}`;
    if (img.host_os_version) label += ` ${img.host_os_version}`;
    if (img.host_arch) label += ` / ${img.host_arch}`;
    if (img.is_default) label += ' (default)';
    return label;
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <Label htmlFor={variable.name}>
          {variable.name}
          {variable.required && <span className="text-destructive ml-1">*</span>}
        </Label>
        <Badge variant="warning" className="text-xs">
          <Cpu className="h-3 w-3 mr-1" />
          DOCA Release
        </Badge>
      </div>

      {!isCustom ? (
        <Select
          value={value || undefined}
          onValueChange={handleSelectChange}
          disabled={isLoading}
        >
          <SelectTrigger
            id={variable.name}
            className={hasError ? 'border-destructive' : ''}
          >
            <SelectValue placeholder={isLoading ? 'Loading DOCA releases\u2026' : 'Select DOCA release\u2026'} />
          </SelectTrigger>
          <SelectContent>
            {entries.map((img) => (
              <SelectItem key={img.id} value={String(img.id)}>
                <div className="flex flex-col py-0.5">
                  <span>{displayLabel(img)}</span>
                  <span className="text-xs text-muted-foreground truncate max-w-[420px]">
                    BFB: {img.image_filename || 'N/A'} &middot; Host: {img.doca_host_url ? '\u2713' : '\u2014'}
                  </span>
                </div>
              </SelectItem>
            ))}
            <SelectItem value={CUSTOM_VALUE}>
              Custom value\u2026
            </SelectItem>
          </SelectContent>
        </Select>
      ) : (
        <div className="flex gap-2">
          <Input
            id={variable.name}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            placeholder="Catalog row ID or legacy URL..."
            className={hasError ? 'border-destructive' : ''}
          />
          {entries.length > 0 && (
            <button
              type="button"
              onClick={() => {
                setIsCustom(false);
                if (defaultImage) onChange(String(defaultImage.id));
              }}
              className="text-xs text-primary hover:underline whitespace-nowrap px-2"
            >
              Pick from catalog
            </button>
          )}
        </div>
      )}

      {variable.description && (
        <p className="text-xs text-muted-foreground flex items-start gap-1">
          <HelpCircle className="h-3 w-3 mt-0.5 flex-shrink-0" />
          {variable.description}
        </p>
      )}
      {renderValidationState()}
    </div>
  );
}
