/**
 * APUserSigForm — full-field tabbed form for APUserSig CRs.
 *
 * Tabs:
 *   1. Identity   — name (CR), tag*, softwareVersion, properties
 *   2. Signatures — array of signature rule objects (name, rule, signatureType, risk, accuracy, attackType, systems, description, references)
 *
 * Required: tag (referenced in APPolicy via signature-requirements[].tag)
 */

import { useState } from 'react';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Button } from '@/components/ui/button';
import { AlertTriangle, Plus, Trash2, Info } from 'lucide-react';
import { cn } from '@/lib/utils';
import { WafWizardFrame } from './WafWizardFrame';
import { validateK8sName, extractApiError } from './waf-utils';
import { useCreateWafUserSig, useUpdateWafUserSig } from '@/hooks/useWafPolicies';
import type { APUserSigResource, APUserSigSignature } from '@/types';

function FieldRow({ label, hint, required, children }: {
  label: string; hint?: string; required?: boolean; children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <Label className="text-xs">
        {label}
        {required && <span className="text-red-500 ml-0.5">*</span>}
      </Label>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

type SigEntry = Omit<APUserSigSignature, 'attackType' | 'systems' | 'references'> & {
  attackTypeName?: string;
  systemsRaw?: string;
  referencesRaw?: string;
};

function emptySig(): SigEntry {
  return {
    name: '',
    rule: '',
    signatureType: 'request',
    risk: 'medium',
    accuracy: 'medium',
    description: '',
    attackTypeName: '',
    systemsRaw: '[]',
    referencesRaw: '',
  };
}

function sigToSpec(s: SigEntry): APUserSigSignature {
  const out: APUserSigSignature = {
    name: s.name,
    rule: s.rule,
    signatureType: s.signatureType,
    risk: s.risk,
    accuracy: s.accuracy,
  };
  if (s.description?.trim()) out.description = s.description;
  if (s.attackTypeName?.trim()) out.attackType = { name: s.attackTypeName };
  try {
    const sys = JSON.parse(s.systemsRaw ?? '[]') as Array<{ name: string }>;
    if (sys.length > 0) out.systems = sys;
  } catch { /* ignore */ }
  if (s.referencesRaw?.trim()) {
    const parts = s.referencesRaw.split(',').map(r => r.trim()).filter(Boolean);
    if (parts.length > 0) {
      // NAP spec: references is a single object (first entry wins)
      const [type, value] = parts[0].split(':');
      out.references = { type: type as 'bugtraq' | 'cve' | 'nessus' | 'url', value };
    }
  }
  return out;
}

interface APUserSigFormProps {
  clusterId: number;
  namespace: string;
  existingItem?: APUserSigResource | null;
  onClose: () => void;
}

export function APUserSigForm({ clusterId, namespace, existingItem, onClose }: APUserSigFormProps) {
  const isEdit = !!existingItem;

  const [crName, setCrName] = useState(existingItem?.metadata.name ?? '');
  const [tag, setTag] = useState(existingItem?.spec?.tag ?? '');
  const [softwareVersion, setSoftwareVersion] = useState(existingItem?.spec?.softwareVersion ?? '');
  const [properties, setProperties] = useState(existingItem?.spec?.properties ?? '');
  const [signatures, setSignatures] = useState<SigEntry[]>(() => {
    const existing = existingItem?.spec?.signatures ?? [];
    if (existing.length === 0) return [emptySig()];
    return existing.map(s => ({
      ...s,
      attackTypeName: s.attackType?.name ?? '',
      systemsRaw: JSON.stringify(s.systems ?? [], null, 2),
      referencesRaw: (s.references as Array<{ type: string; value: string }> | undefined)?.map(r => `${r.type}:${r.value}`).join(', ') ?? '',
    }));
  });
  const [activeTab, setActiveTab] = useState('identity');
  const [submitError, setSubmitError] = useState<string | null>(null);

  const crNameError = crName ? validateK8sName(crName) : (isEdit ? null : 'Name is required.');
  const tagError = !tag.trim() ? 'Tag is required — referenced in APPolicy via signature-requirements[].tag.' : null;

  const createMutation = useCreateWafUserSig(clusterId);
  const updateMutation = useUpdateWafUserSig(clusterId, existingItem?.metadata.name ?? '');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const isPending = isSubmitting;

  const identityErrors = [
    ...(!isEdit && crNameError ? [crNameError] : []),
    ...(tagError ? [tagError] : []),
  ];

  const addSig = () => setSignatures(prev => [...prev, emptySig()]);
  const removeSig = (i: number) => setSignatures(prev => prev.filter((_, idx) => idx !== i));
  const updateSig = (i: number, patch: Partial<SigEntry>) =>
    setSignatures(prev => prev.map((s, idx) => idx === i ? { ...s, ...patch } : s));

  const handleSubmit = async () => {
    setSubmitError(null);
    setIsSubmitting(true);
    const spec = {
      tag: tag.trim(),
      ...(softwareVersion.trim() ? { softwareVersion: softwareVersion.trim() } : {}),
      ...(properties.trim() ? { properties: properties.trim() } : {}),
      signatures: signatures.filter(s => s.name?.trim() || s.rule?.trim()).map(sigToSpec),
    };
    try {
      if (isEdit) {
        await updateMutation.mutateAsync({ namespace: existingItem?.metadata.namespace ?? namespace, spec });
      } else {
        await createMutation.mutateAsync({ name: crName, namespace, spec });
      }
      onClose();
    } catch (e) {
      setSubmitError(extractApiError(e));
    } finally {
      setIsSubmitting(false);
    }
  };

  const identityTab = (
    <div className="grid grid-cols-2 gap-4">
      {!isEdit && (
        <FieldRow label="Name (metadata.name)" required hint="Kubernetes resource name — lowercase alphanumeric with hyphens.">
          <Input
            value={crName}
            onChange={e => setCrName(e.target.value)}
            placeholder="my-custom-sig"
            className={crNameError ? 'border-red-500' : ''}
          />
          {crNameError && <p className="text-xs text-red-500 flex items-center gap-1 mt-1"><AlertTriangle className="h-3 w-3" />{crNameError}</p>}
        </FieldRow>
      )}

      <FieldRow label="Tag" required hint="Identifier referenced in APPolicy.spec.policy['signature-requirements'][].tag. Must be unique within the namespace.">
        <Input
          value={tag}
          onChange={e => setTag(e.target.value)}
          placeholder="my-custom-tag"
          className={tagError ? 'border-red-500' : ''}
        />
        {tagError && <p className="text-xs text-red-500 flex items-center gap-1 mt-1"><AlertTriangle className="h-3 w-3" />{tagError}</p>}
      </FieldRow>

      <FieldRow label="Software Version" hint="Optional version identifier for this signature package. E.g. '1.2.0'">
        <Input value={softwareVersion} onChange={e => setSoftwareVersion(e.target.value)} placeholder="1.0.0" />
      </FieldRow>

      <FieldRow label="Properties" hint="Optional key=value pairs describing this signature set. Free-form string.">
        <Input value={properties} onChange={e => setProperties(e.target.value)} placeholder="author=security-team;team=AppSec" />
      </FieldRow>

      <div className="col-span-2">
        <div className={cn('rounded-md border p-3 text-xs flex gap-2 bg-blue-50 border-blue-200 dark:bg-blue-900/20 dark:border-blue-900')}>
          <Info className="h-3.5 w-3.5 mt-0.5 shrink-0 text-blue-500" />
          <span className="text-blue-800 dark:text-blue-300">
            <strong>How to use:</strong> After creating this APUserSig, reference it in your APPolicy by adding{' '}
            <code className="bg-blue-100 dark:bg-zinc-700 px-1 rounded">
              {`{"tag": "${tag || '<tag>'}"}`}
            </code>{' '}
            to <code className="bg-blue-100 dark:bg-zinc-700 px-1 rounded">spec.policy['signature-requirements']</code>.
          </span>
        </div>
      </div>
    </div>
  );

  const signaturesTab = (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-xs text-muted-foreground">
          Each entry defines one NAP signature rule. At least one signature should be defined; empty entries are ignored.
        </p>
        <Button size="sm" variant="outline" className="gap-1.5 h-7" onClick={addSig}>
          <Plus className="h-3.5 w-3.5" /> Add Signature
        </Button>
      </div>
      {signatures.map((sig, i) => (
        <div key={i} className={cn('rounded-md border p-3 space-y-3', 'border-slate-200 dark:border-zinc-700')}>
          <div className="flex items-center justify-between">
            <p className="text-xs font-medium">Signature #{i + 1}</p>
            {signatures.length > 1 && (
              <Button size="sm" variant="ghost" className="h-6 w-6 p-0 text-red-400 hover:text-red-600" onClick={() => removeSig(i)}>
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            )}
          </div>
          <div className="grid grid-cols-2 gap-3">
            <FieldRow label="Name" hint="Unique name for this individual signature rule.">
              <Input value={sig.name ?? ''} onChange={e => updateSig(i, { name: e.target.value })} placeholder="my-sql-injection-rule" />
            </FieldRow>
            <FieldRow label="Signature Type" hint="Whether this rule inspects requests or responses.">
              <Select value={sig.signatureType ?? 'request'} onValueChange={v => updateSig(i, { signatureType: v as 'request' | 'response' })}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent position="popper">
                  <SelectItem value="request">request (default) — inspect HTTP request</SelectItem>
                  <SelectItem value="response">response — inspect HTTP response</SelectItem>
                </SelectContent>
              </Select>
            </FieldRow>
            <div className="col-span-2 space-y-1.5">
              <Label className="text-xs">Rule <span className="text-red-500">*</span></Label>
              <Input
                value={sig.rule ?? ''}
                onChange={e => updateSig(i, { rule: e.target.value })}
                placeholder='content:"evil-payload"; nocase; http_uri;'
                className="font-mono text-xs"
              />
              <p className="text-xs text-muted-foreground">NAP signature rule syntax. Matches against the relevant part of the HTTP message.</p>
            </div>
            <FieldRow label="Risk" hint="Risk level of this attack type.">
              <Select value={sig.risk ?? 'medium'} onValueChange={v => updateSig(i, { risk: v as 'high' | 'medium' | 'low' })}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent position="popper">
                  <SelectItem value="high">high</SelectItem>
                  <SelectItem value="medium">medium (default)</SelectItem>
                  <SelectItem value="low">low</SelectItem>
                </SelectContent>
              </Select>
            </FieldRow>
            <FieldRow label="Accuracy" hint="Confidence level of this signature (false positive risk).">
              <Select value={sig.accuracy ?? 'medium'} onValueChange={v => updateSig(i, { accuracy: v as 'high' | 'medium' | 'low' })}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent position="popper">
                  <SelectItem value="high">high — low false positive rate</SelectItem>
                  <SelectItem value="medium">medium (default)</SelectItem>
                  <SelectItem value="low">low — higher false positive rate</SelectItem>
                </SelectContent>
              </Select>
            </FieldRow>
            <FieldRow label="Attack Type Name" hint="NAP attack type classification. E.g. 'SQL Injection', 'XSS'.">
              <Input value={sig.attackTypeName ?? ''} onChange={e => updateSig(i, { attackTypeName: e.target.value })} placeholder="SQL Injection" />
            </FieldRow>
            <FieldRow label="Description">
              <Input value={sig.description ?? ''} onChange={e => updateSig(i, { description: e.target.value })} placeholder="Detects basic SQL injection in URI" />
            </FieldRow>
            <FieldRow label="Systems" hint='JSON array of affected systems. E.g. [{"name":"MySQL"}]'>
              <Input
                value={sig.systemsRaw ?? '[]'}
                onChange={e => updateSig(i, { systemsRaw: e.target.value })}
                placeholder='[{"name":"MySQL"}]'
                className="font-mono text-xs"
              />
            </FieldRow>
            <FieldRow label="References" hint="Comma-separated type:value pairs. Types: bugtraq, cve, nessus, url. E.g. cve:CVE-2021-44228">
              <Input
                value={sig.referencesRaw ?? ''}
                onChange={e => updateSig(i, { referencesRaw: e.target.value })}
                placeholder="cve:CVE-2021-44228, url:https://example.com"
              />
            </FieldRow>
          </div>
        </div>
      ))}
    </div>
  );

  const tabs = [
    { key: 'identity', label: 'Identity', validate: () => identityErrors },
    { key: 'signatures', label: 'Signature Rules', validate: () => [] },
  ];

  return (
    <WafWizardFrame
      tabs={tabs.map(t => ({ ...t, content: t.key === 'identity' ? identityTab : signaturesTab }))}
      activeTab={activeTab}
      onTabChange={setActiveTab}
      allErrors={identityErrors}
      isPending={isPending}
      submitLabel={isEdit ? 'Save' : 'Create User Signature'}
      onSubmit={handleSubmit}
      onCancel={onClose}
      submitError={submitError}
    />
  );
}
