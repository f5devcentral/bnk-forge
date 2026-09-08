/**
 * CreatePolicyModal — NIM-style 3-step wizard for APPolicy creation.
 *
 * Step 1: Guided basics (name, namespace, enforcement, template)
 * Step 2: Full APPolicyForm (existing component)
 * Step 3: Review summary → submit
 */
import { useState } from 'react';
import { Shield, ChevronRight, ChevronLeft, Check } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { APPolicyForm } from './APPolicyForm';
import { validateK8sName } from './waf-utils';

interface Props {
  clusterId: number;
  namespace: string;
  onClose: () => void;
}

const TEMPLATES = [
  { value: 'POLICY_TEMPLATE_NGINX_BASE', label: 'NGINX Base (recommended)', desc: 'General-purpose baseline with OWASP Top 10 protection' },
  { value: 'POLICY_TEMPLATE_NGINX_STRICT', label: 'NGINX Strict', desc: 'High-security baseline with stricter signature matching' },
  { value: '', label: 'Blank', desc: 'Start from scratch with no pre-configured rules' },
];

const STEPS = ['Basics', 'Configure', 'Review'];

export function CreatePolicyModal({ clusterId, namespace, onClose }: Props) {
  const [step, setStep]                 = useState(0);
  const [name, setName]                 = useState('');
  const [enforcement, setEnforcement]   = useState<'blocking' | 'transparent'>('blocking');
  const [template, setTemplate]         = useState('POLICY_TEMPLATE_NGINX_BASE');
  const [nameError, setNameError]       = useState('');

  const nameErrMsg = name ? validateK8sName(name) : null;

  const canAdvance = name.trim() && !nameErrMsg;

  const handleNextFromStep0 = () => {
    const err = validateK8sName(name);
    if (err) { setNameError(err); return; }
    setNameError('');
    setStep(1);
  };

  return (
    <div className="flex flex-col h-full">
      {/* Step indicator */}
      <div className="px-6 pt-5 pb-4 border-b border-border">
        <div className="flex items-center gap-0">
          {STEPS.map((label, i) => (
            <div key={label} className="flex items-center">
              <div className="flex items-center gap-2">
                <div className={cn(
                  'flex h-6 w-6 items-center justify-center rounded-full text-xs font-semibold border-2 transition-colors',
                  i < step  ? 'bg-primary border-primary text-primary-foreground'
                  : i === step ? 'border-primary text-primary bg-background'
                  : 'border-border text-muted-foreground bg-background',
                )}>
                  {i < step ? <Check className="h-3 w-3" /> : i + 1}
                </div>
                <span className={cn(
                  'text-xs font-medium',
                  i === step ? 'text-foreground' : 'text-muted-foreground',
                )}>{label}</span>
              </div>
              {i < STEPS.length - 1 && (
                <div className={cn('mx-3 h-px w-8', i < step ? 'bg-primary' : 'bg-border')} />
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Step 1: Basics */}
      {step === 0 && (
        <div className="flex-1 overflow-y-auto p-6 space-y-5">
          <div className="space-y-1">
            <h3 className="text-sm font-semibold">Policy Details</h3>
            <p className="text-xs text-muted-foreground">Define the core settings for your WAF policy.</p>
          </div>

          {/* Name */}
          <div className="space-y-1.5">
            <Label htmlFor="pol-name">
              Policy Name <span className="text-destructive">*</span>
            </Label>
            <Input
              id="pol-name"
              value={name}
              onChange={(e) => { setName(e.target.value); setNameError(''); }}
              placeholder="e.g. my-waf-policy"
              className={cn(nameError && 'border-destructive')}
            />
            {nameError && <p className="text-xs text-destructive">{nameError}</p>}
            <p className="text-xs text-muted-foreground">Lowercase alphanumeric and hyphens only (Kubernetes name format).</p>
          </div>

          {/* Enforcement Mode */}
          <div className="space-y-1.5">
            <Label>Enforcement Mode</Label>
            <div className="grid grid-cols-2 gap-3">
              {(['blocking', 'transparent'] as const).map((mode) => (
                <button
                  key={mode}
                  type="button"
                  onClick={() => setEnforcement(mode)}
                  className={cn(
                    'rounded-lg border p-3 text-left transition-colors',
                    enforcement === mode
                      ? 'border-primary bg-primary/5 ring-1 ring-primary'
                      : 'border-border hover:bg-accent',
                  )}
                >
                  <p className="text-xs font-semibold capitalize">{mode}</p>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    {mode === 'blocking'
                      ? 'Actively blocks matching attack requests'
                      : 'Detects and logs attacks but does not block'}
                  </p>
                </button>
              ))}
            </div>
          </div>

          {/* Template */}
          <div className="space-y-1.5">
            <Label>Template</Label>
            <div className="space-y-2">
              {TEMPLATES.map((t) => (
                <button
                  key={t.value}
                  type="button"
                  onClick={() => setTemplate(t.value)}
                  className={cn(
                    'w-full rounded-lg border p-3 text-left transition-colors',
                    template === t.value
                      ? 'border-primary bg-primary/5 ring-1 ring-primary'
                      : 'border-border hover:bg-accent',
                  )}
                >
                  <p className="text-xs font-semibold">{t.label}</p>
                  <p className="text-xs text-muted-foreground mt-0.5">{t.desc}</p>
                </button>
              ))}
            </div>
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" size="sm" onClick={onClose}>Cancel</Button>
            <Button size="sm" className="gap-1.5" onClick={handleNextFromStep0} disabled={!canAdvance}>
              Next: Configure <ChevronRight className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
      )}

      {/* Step 2: Full APPolicyForm (pre-seeded with step 1 values) */}
      {step === 1 && (
        <div className="flex-1 overflow-y-auto">
          <div className="px-6 py-3 border-b border-border flex items-center justify-between bg-muted/20">
            <div>
              <p className="text-xs font-medium text-foreground">Configuring: <span className="font-mono">{name}</span></p>
              <p className="text-xs text-muted-foreground">Refine policy rules, signatures, and logging below.</p>
            </div>
            <div className="flex gap-2">
              <Button variant="ghost" size="sm" className="gap-1" onClick={() => setStep(0)}>
                <ChevronLeft className="h-3.5 w-3.5" /> Back
              </Button>
              <Button size="sm" className="gap-1" onClick={() => setStep(2)}>
                Review <ChevronRight className="h-3.5 w-3.5" />
              </Button>
            </div>
          </div>
          <APPolicyForm
            clusterId={clusterId}
            namespace={namespace}
            initialName={name}
            initialEnforcementMode={enforcement}
            initialTemplate={template}
            onClose={onClose}
            onAfterSave={onClose}
          />
        </div>
      )}

      {/* Step 3: Review */}
      {step === 2 && (
        <div className="flex-1 overflow-y-auto p-6 space-y-5">
          <div className="space-y-1">
            <h3 className="text-sm font-semibold">Review &amp; Submit</h3>
            <p className="text-xs text-muted-foreground">Confirm the policy configuration before creating.</p>
          </div>

          <div className="rounded-lg border border-border bg-card divide-y divide-border">
            {[
              ['Policy Name', <span className="font-mono">{name}</span>],
              ['Namespace', namespace],
              ['Enforcement Mode', <span className={cn('capitalize font-medium',
                enforcement === 'blocking' ? 'text-destructive' : 'text-warning'
              )}>{enforcement}</span>],
              ['Template', TEMPLATES.find(t => t.value === template)?.label ?? 'Blank'],
            ].map(([k, v]) => (
              <div key={String(k)} className="flex items-center justify-between px-4 py-2.5 text-xs">
                <span className="text-muted-foreground">{k}</span>
                <span className="font-medium">{v}</span>
              </div>
            ))}
          </div>

          <div className="rounded-md border border-warning/30 bg-warning/5 px-3 py-2 text-xs text-warning/80 flex gap-2">
            <Shield className="h-3.5 w-3.5 shrink-0 mt-0.5" />
            <span>
              Once created, the Policy Controller will compile this policy into a bundle and deploy it to the cluster.
              Compilation may take 30–60 seconds.
            </span>
          </div>

          <div className="flex justify-between gap-2 pt-2">
            <Button variant="ghost" size="sm" className="gap-1" onClick={() => setStep(1)}>
              <ChevronLeft className="h-3.5 w-3.5" /> Back
            </Button>
            <div className="flex gap-2">
              <Button variant="ghost" size="sm" onClick={onClose}>Cancel</Button>
              {/* Go back to form step to submit (APPolicyForm handles the actual save) */}
              <Button size="sm" className="gap-1.5" onClick={() => setStep(1)}>
                <Check className="h-3.5 w-3.5" /> Confirm &amp; Submit
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
