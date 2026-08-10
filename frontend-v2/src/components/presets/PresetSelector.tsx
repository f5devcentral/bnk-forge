/**
 * PresetSelector — Card-based intent preset picker (UX-002)
 *
 * Pick a preset (Dev/Standard/Production) to fill all advanced fields,
 * or choose "Custom" to start empty. The "Advanced" panel is always
 * one click away to see and edit every raw field.
 *
 * Selecting a preset populates fields; editing any field shows a
 * "Modified" indicator next to the category.
 */

import { useState, useCallback, useMemo } from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible';
import { cn } from '@/lib/utils';
import {
  ChevronDown,
  DollarSign,
  Clock,
  Settings2,
  PenLine,
  Check,
  Sparkles,
} from 'lucide-react';
import type { JsonValue } from '@/types';
import type { PresetCategory, PresetOption } from '@/lib/presets';

// ============================================================================
// Types
// ============================================================================

interface PresetSelectorProps {
  /** Preset category definition (e.g. NETWORK_SIZE_PRESETS) */
  category: PresetCategory;
  /** Currently selected preset ID, or 'custom' */
  selectedPresetId: string | null;
  /** Called when user selects a preset or 'custom' */
  onSelect: (presetId: string | null) => void;
  /** Current field values — used to detect if user modified a preset */
  currentValues: Record<string, JsonValue>;
  /** Called when user changes an advanced field */
  onFieldChange: (fieldName: string, value: JsonValue) => void;
  /** Field definitions for the Advanced panel */
  advancedFields?: AdvancedFieldDef[];
}

export interface AdvancedFieldDef {
  name: string;
  label: string;
  type: 'text' | 'number';
  description?: string;
  placeholder?: string;
}

// ============================================================================
// Component
// ============================================================================

export function PresetSelector({
  category,
  selectedPresetId,
  onSelect,
  currentValues,
  onFieldChange,
  advancedFields,
}: PresetSelectorProps) {
  const [advancedOpen, setAdvancedOpen] = useState(false);

  // Check if user has modified any preset values
  const isModified = useMemo(() => {
    if (!selectedPresetId || selectedPresetId === 'custom') return false;
    const preset = category.options.find((p) => p.id === selectedPresetId);
    if (!preset) return false;

    return Object.entries(preset.flatVariables).some(([key, presetVal]) => {
      const current = currentValues[key];
      if (current === undefined || current === null) return false;
      return String(current) !== String(presetVal);
    });
  }, [selectedPresetId, category.options, currentValues]);

  const handleSelect = useCallback(
    (presetId: string | null) => {
      onSelect(presetId);
    },
    [onSelect]
  );

  return (
    <div className="space-y-3">
      {/* Category header */}
      <div className="flex items-center justify-between">
        <div>
          <h4 className="text-sm font-semibold text-foreground flex items-center gap-2">
            {category.label}
            {isModified && (
              <Badge variant="warning" className="text-[10px] py-0">
                <PenLine className="h-2.5 w-2.5 mr-1" />
                Modified
              </Badge>
            )}
          </h4>
          <p className="text-xs text-muted-foreground">
            {category.description}
          </p>
        </div>
      </div>

      {/* Preset cards grid */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
        {category.options.map((option) => (
          <PresetCard
            key={option.id}
            option={option}
            isSelected={selectedPresetId === option.id}
            onClick={() => handleSelect(option.id)}
          />
        ))}

        {/* Custom card */}
        <PresetCard
          option={{
            id: 'custom',
            label: 'Custom',
            description: 'Build from scratch',
            variables: {},
            flatVariables: {},
          }}
          isSelected={selectedPresetId === 'custom'}
          onClick={() => handleSelect('custom')}
          isCustom
        />
      </div>

      {/* Advanced panel */}
      {advancedFields && advancedFields.length > 0 && (
        <Collapsible open={advancedOpen} onOpenChange={setAdvancedOpen}>
          <CollapsibleTrigger asChild>
            <Button
              variant="ghost"
              size="sm"
              className="w-full justify-between text-xs h-8 text-muted-foreground hover:text-foreground"
            >
              <span className="flex items-center gap-1.5">
                <Settings2 className="h-3.5 w-3.5" />
                Advanced
              </span>
              <ChevronDown
                className={cn(
                  'h-3.5 w-3.5 transition-transform',
                  advancedOpen && 'rotate-180'
                )}
              />
            </Button>
          </CollapsibleTrigger>
          <CollapsibleContent>
            <div className="mt-2 p-3 rounded-lg border border-border bg-muted/40 space-y-3">
              {advancedFields.map((field) => {
                const presetValue = getPresetFieldValue(
                  category,
                  selectedPresetId,
                  field.name
                );
                const currentValue = currentValues[field.name];
                const fieldModified =
                  presetValue !== undefined &&
                  currentValue !== undefined &&
                  String(currentValue) !== String(presetValue);

                return (
                  <div key={field.name} className="space-y-1">
                    <Label className="text-xs flex items-center gap-2">
                      {field.label}
                      {fieldModified && (
                        <Badge variant="warning" className="text-[10px] py-0">
                          Modified
                        </Badge>
                      )}
                    </Label>
                    <Input
                      type={field.type}
                      value={String(currentValue ?? '')}
                      onChange={(e) =>
                        onFieldChange(
                          field.name,
                          field.type === 'number'
                            ? Number(e.target.value)
                            : e.target.value
                        )
                      }
                      placeholder={field.placeholder || field.description}
                      className={cn(
                        'h-8 text-sm',
                        fieldModified && 'border-warning/50'
                      )}
                    />
                    {field.description && (
                      <p className="text-xs text-muted-foreground">
                        {field.description}
                      </p>
                    )}
                  </div>
                );
              })}
            </div>
          </CollapsibleContent>
        </Collapsible>
      )}
    </div>
  );
}

// ============================================================================
// PresetCard sub-component
// ============================================================================

function PresetCard({
  option,
  isSelected,
  onClick,
  isCustom = false,
}: {
  option: PresetOption;
  isSelected: boolean;
  onClick: () => void;
  isCustom?: boolean;
}) {
  return (
    <Card
      className={cn(
        'cursor-pointer transition-all hover:shadow-md',
        isSelected
          ? 'ring-2 ring-primary border-primary/50'
          : 'hover:border-foreground/20',
      )}
      onClick={onClick}
    >
      <CardContent className="p-3 space-y-1.5">
        <div className="flex items-start justify-between">
          <span
            className={cn(
              'text-sm font-semibold',
              isSelected ? 'text-primary' : 'text-foreground',
            )}
          >
            {option.label}
          </span>
          {isSelected ? (
            <div className="h-5 w-5 rounded-full bg-primary flex items-center justify-center">
              <Check className="h-3 w-3 text-primary-foreground" />
            </div>
          ) : isCustom ? (
            <Settings2 className="h-4 w-4 text-muted-foreground" />
          ) : (
            <Sparkles className="h-4 w-4 text-muted-foreground/60" />
          )}
        </div>

        <p className="text-xs line-clamp-2 text-muted-foreground">
          {option.description}
        </p>

        {option.estimatedCost && (
          <div className="flex items-center gap-2 pt-1">
            <span className="text-xs flex items-center gap-1 text-muted-foreground">
              <DollarSign className="h-3 w-3" />
              {option.estimatedCost}
            </span>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ============================================================================
// Composite Preset Selector (for environment-level presets)
// ============================================================================

interface EnvironmentPresetSelectorProps {
  /** Currently selected environment preset ID */
  selectedId: string | null;
  /** Called when user picks an environment preset */
  onSelect: (presetId: string | null) => void;
  /** Environment presets to display */
  presets: Array<{
    id: string;
    label: string;
    description: string;
    estimatedCost: string;
    estimatedTime: string;
  }>;
}

export function EnvironmentPresetSelector({
  selectedId,
  onSelect,
  presets,
}: EnvironmentPresetSelectorProps) {
  return (
    <div className="space-y-3">
      <div>
        <h4 className="text-sm font-semibold text-foreground">
          Environment Size
        </h4>
        <p className="text-xs text-muted-foreground">
          Choose the scale of your deployment
        </p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        {presets.map((preset) => (
          <Card
            key={preset.id}
            className={cn(
              'cursor-pointer transition-all hover:shadow-md',
              selectedId === preset.id
                ? 'ring-2 ring-primary border-primary/50'
                : 'hover:border-foreground/20',
            )}
            onClick={() => onSelect(preset.id)}
          >
            <CardContent className="p-4 space-y-2">
              <div className="flex items-start justify-between">
                <span
                  className={cn(
                    'text-base font-semibold',
                    selectedId === preset.id ? 'text-primary' : 'text-foreground',
                  )}
                >
                  {preset.label}
                </span>
                {selectedId === preset.id && (
                  <div className="h-5 w-5 rounded-full bg-primary flex items-center justify-center">
                    <Check className="h-3 w-3 text-primary-foreground" />
                  </div>
                )}
              </div>

              <p className="text-xs text-muted-foreground">
                {preset.description}
              </p>

              <div className="flex items-center gap-3 pt-1">
                <span className="text-xs flex items-center gap-1 font-medium text-success">
                  <DollarSign className="h-3 w-3" />
                  {preset.estimatedCost}
                </span>
                <span className="text-xs flex items-center gap-1 text-muted-foreground">
                  <Clock className="h-3 w-3" />
                  {preset.estimatedTime}
                </span>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}

// ============================================================================
// Helpers
// ============================================================================

function getPresetFieldValue(
  category: PresetCategory,
  presetId: string | null,
  fieldName: string
): JsonValue | undefined {
  if (!presetId || presetId === 'custom') return undefined;
  const preset = category.options.find((p) => p.id === presetId);
  return preset?.flatVariables[fieldName];
}
