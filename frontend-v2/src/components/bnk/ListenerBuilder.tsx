/**
 * Listener Builder Component for Gateway Configuration
 *
 * Visual UI for configuring Gateway API listeners instead of JSON editing.
 * Features:
 * - Add/remove listeners with cards
 * - Protocol dropdown (HTTP, HTTPS, TCP, UDP, TLS)
 * - Port number input with validation
 * - Optional hostname input
 * - Presets: "HTTP Only (80)", "HTTP + HTTPS (80, 443)", "Custom"
 * - TLS certificate selector for HTTPS listeners
 */

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Card } from '@/components/ui/card';
import { Badge, type BadgeProps } from '@/components/ui/badge';
import { Plus, Trash2, Globe, Lock, Network } from 'lucide-react';
import { cn } from '@/lib/utils';
import { validatePort, validateK8sResourceName } from '@/lib/validators';

export interface Listener {
  name: string;
  protocol: 'HTTP' | 'HTTPS' | 'TCP' | 'UDP' | 'TLS';
  port: number;
  hostname?: string;
  tls_certificate_ref?: string;
}

interface ListenerBuilderProps {
  value: Listener[];
  onChange: (listeners: Listener[]) => void;
  showPresets?: boolean;
}

const PROTOCOL_OPTIONS = [
  { value: 'HTTP', label: 'HTTP', icon: Globe },
  { value: 'HTTPS', label: 'HTTPS', icon: Lock },
  { value: 'TCP', label: 'TCP', icon: Network },
  { value: 'UDP', label: 'UDP', icon: Network },
  { value: 'TLS', label: 'TLS', icon: Lock },
];

const PRESETS = [
  {
    name: 'HTTP Only (80)',
    listeners: [
      { name: 'http', protocol: 'HTTP' as const, port: 80 },
    ],
  },
  {
    name: 'HTTP + HTTPS (80, 443)',
    listeners: [
      { name: 'http', protocol: 'HTTP' as const, port: 80 },
      { name: 'https', protocol: 'HTTPS' as const, port: 443 },
    ],
  },
  {
    name: 'HTTPS Only (443)',
    listeners: [
      { name: 'https', protocol: 'HTTPS' as const, port: 443 },
    ],
  },
];

function protocolBadgeVariant(protocol: string): BadgeProps['variant'] {
  switch (protocol) {
    case 'HTTP':
      return 'info';
    case 'HTTPS':
      return 'success';
    case 'TCP':
    case 'UDP':
    case 'TLS':
      return 'secondary';
    default:
      return 'outline';
  }
}

export function ListenerBuilder({
  value,
  onChange,
  showPresets = true,
}: ListenerBuilderProps) {
  const [errors, setErrors] = useState<Record<string, string>>({});

  const addListener = () => {
    const newListener: Listener = {
      name: `listener-${value.length + 1}`,
      protocol: 'HTTP',
      port: 80,
    };
    onChange([...value, newListener]);
  };

  const removeListener = (index: number) => {
    const newListeners = value.filter((_, i) => i !== index);
    onChange(newListeners);
  };

  const updateListener = (index: number, updates: Partial<Listener>) => {
    const newListeners = [...value];
    newListeners[index] = { ...newListeners[index], ...updates };
    onChange(newListeners);
  };

  const applyPreset = (presetListeners: Listener[]) => {
    onChange(presetListeners);
    setErrors({});
  };

  const validateListener = (index: number, listener: Listener) => {
    const newErrors = { ...errors };

    // Validate name
    const nameValidation = validateK8sResourceName(listener.name);
    if (!nameValidation.isValid) {
      newErrors[`${index}-name`] = nameValidation.error || 'Invalid name';
    } else {
      delete newErrors[`${index}-name`];
    }

    // Validate port
    const portValidation = validatePort(listener.port);
    if (!portValidation.isValid) {
      newErrors[`${index}-port`] = portValidation.error || 'Invalid port';
    } else {
      delete newErrors[`${index}-port`];
    }

    // Validate HTTPS/TLS requires certificate
    if ((listener.protocol === 'HTTPS' || listener.protocol === 'TLS') && !listener.tls_certificate_ref) {
      newErrors[`${index}-cert`] = 'HTTPS/TLS listeners require a TLS certificate';
    } else {
      delete newErrors[`${index}-cert`];
    }

    setErrors(newErrors);
  };

  const getProtocolIcon = (protocol: string) => {
    const option = PROTOCOL_OPTIONS.find((opt) => opt.value === protocol);
    return option ? option.icon : Globe;
  };

  return (
    <div className="space-y-4">
      {/* Presets */}
      {showPresets && (
        <div className="space-y-2">
          <Label className="text-sm font-medium">Quick Presets</Label>
          <div className="flex flex-wrap gap-2">
            {PRESETS.map((preset) => (
              <Button
                key={preset.name}
                variant="outline"
                size="sm"
                onClick={() => applyPreset(preset.listeners)}
              >
                {preset.name}
              </Button>
            ))}
            {value.length > 0 && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => onChange([])}
                className="text-destructive"
              >
                <Trash2 className="h-3 w-3 mr-1" />
                Clear All
              </Button>
            )}
          </div>
        </div>
      )}

      {/* Listener Cards */}
      <div className="space-y-3">
        {value.length === 0 ? (
          <div className="text-center py-8 rounded-lg border-2 border-dashed border-border text-muted-foreground">
            <Globe className="h-12 w-12 mx-auto mb-2 opacity-50" />
            <p className="text-sm font-medium">No listeners configured</p>
            <p className="text-xs mt-1">Add a listener or choose a preset</p>
          </div>
        ) : (
          value.map((listener, index) => {
            const ProtocolIcon = getProtocolIcon(listener.protocol);
            return (
              <Card key={index} className="p-4">
                <div className="space-y-4">
                  {/* Header */}
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <Badge variant={protocolBadgeVariant(listener.protocol)} className="px-2 py-1 font-mono text-xs">
                        <ProtocolIcon className="h-3 w-3 mr-1" />
                        {listener.protocol}
                      </Badge>
                      <span className="text-sm font-medium text-foreground">
                        {listener.name}
                      </span>
                    </div>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => removeListener(index)}
                      className="h-7 w-7 p-0 text-destructive hover:bg-destructive/10"
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>

                  {/* Fields */}
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    {/* Name */}
                    <div className="space-y-1">
                      <Label htmlFor={`listener-${index}-name`} className="text-xs">
                        Name
                      </Label>
                      <Input
                        id={`listener-${index}-name`}
                        value={listener.name}
                        onChange={(e) => {
                          updateListener(index, { name: e.target.value });
                          validateListener(index, { ...listener, name: e.target.value });
                        }}
                        onBlur={() => validateListener(index, listener)}
                        placeholder="listener-name"
                        className={cn('text-sm', errors[`${index}-name`] && 'border-destructive')}
                      />
                      {errors[`${index}-name`] && (
                        <p className="text-xs text-destructive">{errors[`${index}-name`]}</p>
                      )}
                    </div>

                    {/* Protocol */}
                    <div className="space-y-1">
                      <Label htmlFor={`listener-${index}-protocol`} className="text-xs">
                        Protocol
                      </Label>
                      <Select
                        value={listener.protocol}
                        onValueChange={(value) => {
                          updateListener(index, { protocol: value as Listener['protocol'] });
                          validateListener(index, { ...listener, protocol: value as Listener['protocol'] });
                        }}
                      >
                        <SelectTrigger
                          id={`listener-${index}-protocol`}
                          className="text-sm"
                        >
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {PROTOCOL_OPTIONS.map((option) => {
                            const Icon = option.icon;
                            return (
                              <SelectItem key={option.value} value={option.value}>
                                <div className="flex items-center gap-2">
                                  <Icon className="h-3 w-3" />
                                  {option.label}
                                </div>
                              </SelectItem>
                            );
                          })}
                        </SelectContent>
                      </Select>
                    </div>

                    {/* Port */}
                    <div className="space-y-1">
                      <Label htmlFor={`listener-${index}-port`} className="text-xs">
                        Port
                      </Label>
                      <Input
                        id={`listener-${index}-port`}
                        type="number"
                        value={listener.port}
                        onChange={(e) => {
                          const port = parseInt(e.target.value);
                          updateListener(index, { port });
                          validateListener(index, { ...listener, port });
                        }}
                        onBlur={() => validateListener(index, listener)}
                        min={1}
                        max={65535}
                        placeholder="80"
                        className={cn('text-sm', errors[`${index}-port`] && 'border-destructive')}
                      />
                      {errors[`${index}-port`] && (
                        <p className="text-xs text-destructive">{errors[`${index}-port`]}</p>
                      )}
                    </div>

                    {/* Hostname (optional) */}
                    <div className="space-y-1">
                      <Label htmlFor={`listener-${index}-hostname`} className="text-xs">
                        Hostname <span className="text-muted-foreground">(optional)</span>
                      </Label>
                      <Input
                        id={`listener-${index}-hostname`}
                        value={listener.hostname || ''}
                        onChange={(e) => updateListener(index, { hostname: e.target.value || undefined })}
                        placeholder="*.example.com"
                        className="text-sm"
                      />
                    </div>
                  </div>

                  {/* TLS Certificate (for HTTPS/TLS) */}
                  {(listener.protocol === 'HTTPS' || listener.protocol === 'TLS') && (
                    <div className="space-y-1">
                      <Label htmlFor={`listener-${index}-cert`} className="text-xs">
                        TLS Certificate Secret <span className="text-destructive">*</span>
                      </Label>
                      <Input
                        id={`listener-${index}-cert`}
                        value={listener.tls_certificate_ref || ''}
                        onChange={(e) => {
                          updateListener(index, { tls_certificate_ref: e.target.value });
                          validateListener(index, { ...listener, tls_certificate_ref: e.target.value });
                        }}
                        onBlur={() => validateListener(index, listener)}
                        placeholder="tls-secret-name"
                        className={cn('text-sm', errors[`${index}-cert`] && 'border-destructive')}
                      />
                      {errors[`${index}-cert`] && (
                        <p className="text-xs text-destructive">{errors[`${index}-cert`]}</p>
                      )}
                      <p className="text-xs text-muted-foreground">
                        Reference to a Kubernetes Secret containing TLS certificate
                      </p>
                    </div>
                  )}
                </div>
              </Card>
            );
          })
        )}
      </div>

      {/* Add Button */}
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={addListener}
        className="w-full"
      >
        <Plus className="h-4 w-4 mr-2" />
        Add Listener
      </Button>

      {/* Summary */}
      {value.length > 0 && (
        <div className="text-xs rounded p-3 bg-muted text-muted-foreground">
          <span className="font-medium">Summary:</span> {value.length} listener{value.length !== 1 ? 's' : ''}{' '}
          configured
          {Object.keys(errors).length > 0 && (
            <span className="text-destructive ml-2">
              • {Object.keys(errors).length} validation error{Object.keys(errors).length !== 1 ? 's' : ''}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
