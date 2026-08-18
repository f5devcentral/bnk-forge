/**
 * WAF Policy Wizard — create an APPolicy CR + optional companion APLogConf.
 *
 * 4 steps:
 *   1. Basics   — name + namespace (checks live for name conflicts)
 *   2. Policy   — spec.policy JSON editor
 *   3. Logging  — pick an existing APLogConf or create one inline
 *   4. Review   — CR preview + Create
 *
 * Architecture note on logging:
 *   APLogConf is compiled independently by the Policy Controller into its own S3
 *   bundle (bundles/<name><timestamp>.tgz).  The association between an APLogConf
 *   and a specific Virtual Server is made through F5SecureContext (f5ingress), NOT
 *   inside APPolicy.spec.  The "pick a log profile" step here creates the APLogConf
 *   CR; the actual per-VS binding is configured in the Virtual Server / Secure
 *   Context UI (next feature).
 *
 * APSignatures lives in the "Signature Settings" tab on the WAF Policies page.
 * See docs/WAF_POLICY_MANAGER_DESIGN.md.
 */

import { useMemo, useState } from 'react';
import { useTheme } from '@/context/ThemeContext';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Switch } from '@/components/ui/switch';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Check, ChevronRight, ArrowLeft, ArrowRight, AlertTriangle, Copy, Shield, FileJson, FileText } from 'lucide-react';
import {
  validateWafWizardStep,
  validateWafWizardAll,
  type WafWizardState,
  type WafWizardStep,
} from '@/lib/waf-policy-validation';
import {
  useCreateWafPolicy,
  useCreateWafLogConf,
  useDeleteWafPolicy,
  useWafPolicies,
  useWafLogConfs,
} from '@/hooks/useWafPolicies';
import type { APLogConfFormat, APLogConfRequestType } from '@/types';

interface WafPolicyWizardProps {
  clusterId: number;
  onClose?: () => void;
}

const STEPS: { key: WafWizardStep; label: string; icon: typeof Shield }[] = [
  { key: 'basics',  label: 'Basics',         icon: Shield   },
  { key: 'policy',  label: 'Policy',          icon: FileJson },
  { key: 'logging', label: 'Logging',         icon: FileText },
  { key: 'review',  label: 'Review & Create', icon: Check    },
];

const LOG_FORMATS: APLogConfFormat[] = ['default', 'splunk', 'arcsight', 'user-defined', 'grpc'];
const REQUEST_TYPES: APLogConfRequestType[] = ['illegal', 'blocked', 'all'];

const RFC1123_RE = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/;
function validateK8sName(name: string): string | null {
  if (!name) return 'Name is required.';
  if (name.length > 63) return 'Must be ≤ 63 characters.';
  if (!RFC1123_RE.test(name)) return 'Lowercase alphanumeric with hyphens; cannot start or end with a hyphen.';
  return null;
}

function extractApiError(e: unknown): string {
  const resp = (e as { response?: { data?: { detail?: string; message?: string; error?: string | { message?: string } } } })?.response?.data;
  if (resp?.detail) return String(resp.detail);
  if (resp?.message) return String(resp.message);
  if (typeof resp?.error === 'string') return resp.error;
  if (typeof resp?.error === 'object' && resp.error?.message) return resp.error.message;
  return e instanceof Error ? e.message : String(e);
}

function createInitialState(): WafWizardState {
  return {
    name: '',
    namespace: 'default',
    policyJsonText: '{\n  "name": "my-policy"\n}',
    logging: {
      enabled: false,
      mode: 'pick',
      existingLogConfName: '',
      format: 'default',
      formatString: '',
      requestType: 'illegal',
      maxMessageSize: '10k',
      maxRequestSize: 'any',
    },
  };
}

// ---------------------------------------------------------------------------
// Step indicator
// ---------------------------------------------------------------------------

function StepIndicator({
  currentStep,
  onStepClick,
  isDark,
}: { currentStep: WafWizardStep; onStepClick: (s: WafWizardStep) => void; isDark: boolean }) {
  const currentIdx = STEPS.findIndex((s) => s.key === currentStep);
  return (
    <div className="flex items-center gap-1 flex-wrap">
      {STEPS.map((step, i) => {
        const isDone = i < currentIdx;
        const isActive = i === currentIdx;
        const Icon = step.icon;
        return (
          <div key={step.key} className="flex items-center">
            <button
              onClick={() => onStepClick(step.key)}
              className={cn(
                'flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium transition-colors',
                isActive   ? 'bg-primary text-white'
                : isDone   ? isDark ? 'bg-success/20/30 text-success/80 hover:bg-success/20/50'
                                    : 'bg-success/10 text-success hover:bg-success/10'
                           : isDark ? 'bg-card text-muted-foreground hover:bg-muted'
                                    : 'bg-muted text-muted-foreground hover:bg-muted'
              )}
            >
              {isDone ? <Check className="h-3 w-3" /> : <Icon className="h-3 w-3" />}
              {step.label}
            </button>
            {i < STEPS.length - 1 && (
              <ChevronRight className={cn('h-3.5 w-3.5 mx-0.5', isDark ? 'text-foreground/80' : 'text-muted-foreground/70')} />
            )}
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 1: Basics
// ---------------------------------------------------------------------------

function BasicsStep({
  state,
  onChange,
  nameConflict,
}: {
  state: WafWizardState;
  onChange: (p: Partial<WafWizardState>) => void;
  nameConflict: boolean;
}) {
  const nameError = state.name ? validateK8sName(state.name) : null;
  return (
    <div className="space-y-4 max-w-md">
      <div className="space-y-1.5">
        <Label htmlFor="waf-name">Policy Name <span className="text-destructive">*</span></Label>
        <Input
          id="waf-name"
          value={state.name}
          onChange={(e) => {
            const n = e.target.value;
            onChange({ name: n, policyJsonText: '{\n  "name": "' + (n || 'my-policy') + '"\n}' });
          }}
          placeholder="my-waf-policy"
          className={nameError || nameConflict ? 'border-destructive/50 focus-visible:ring-destructive' : ''}
        />
        {nameError ? (
          <p className="text-xs text-destructive flex items-center gap-1"><AlertTriangle className="h-3 w-3" />{nameError}</p>
        ) : nameConflict ? (
          <p className="text-xs text-destructive flex items-center gap-1">
            <AlertTriangle className="h-3 w-3" />
            A policy with this name already exists in namespace &quot;{state.namespace}&quot;.
          </p>
        ) : (
          <p className="text-xs text-muted-foreground">Lowercase alphanumeric with hyphens (RFC 1123), max 63 chars.</p>
        )}
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="waf-namespace">Namespace</Label>
        <Input
          id="waf-namespace"
          value={state.namespace}
          onChange={(e) => onChange({ namespace: e.target.value })}
          placeholder="default"
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 2: Policy JSON
// ---------------------------------------------------------------------------

function PolicyStep({ state, onChange }: { state: WafWizardState; onChange: (p: Partial<WafWizardState>) => void }) {
  return (
    <div className="space-y-2">
      <Label htmlFor="waf-policy-json">Policy JSON (spec.policy)</Label>
      <p className="text-xs text-muted-foreground">
        Raw App Protect policy JSON. The compiler performs full semantic validation.
      </p>
      <Textarea
        id="waf-policy-json"
        value={state.policyJsonText}
        onChange={(e) => onChange({ policyJsonText: e.target.value })}
        rows={18}
        className="font-mono text-xs"
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 3: Logging — pick existing APLogConf or create one inline
// ---------------------------------------------------------------------------

function LoggingStep({
  state,
  onChange,
  clusterId,
}: {
  state: WafWizardState;
  onChange: (p: Partial<WafWizardState>) => void;
  clusterId: number;
}) {
  const logging = state.logging;
  const setLogging = (p: Partial<WafWizardState['logging']>) => onChange({ logging: { ...logging, ...p } });

  const { data: logConfsData } = useWafLogConfs(clusterId, state.namespace, { enabled: logging.enabled });
  const existingLogConfs = logConfsData?.log_confs ?? [];

  return (
    <div className="space-y-4 max-w-md">
      <p className="text-xs text-muted-foreground">
        <strong>Optional:</strong> Create an APLogConf alongside this policy. The log profile is compiled independently
        and delivered as a separate resource to the WAF enforcer. The binding to a specific Virtual Server is configured
        later in the Virtual Server / Secure Context UI — not inside the policy itself.
      </p>
      <div className="flex items-center gap-2">
        <Switch checked={logging.enabled} onCheckedChange={(enabled) => setLogging({ enabled })} />
        <Label>Create or attach a log profile (APLogConf)</Label>
      </div>

      {logging.enabled && (
        <>
          <div className="space-y-1.5">
            <Label>Log Profile Source</Label>
            <Select
              value={logging.mode}
              onValueChange={(v) => setLogging({ mode: v as 'pick' | 'create', existingLogConfName: '' })}
            >
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="pick">Use existing log profile</SelectItem>
                <SelectItem value="create">Create a new log profile</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {logging.mode === 'pick' && (
            <div className="space-y-1.5">
              <Label>Existing Log Profile</Label>
              {existingLogConfs.length > 0 ? (
                <Select
                  value={logging.existingLogConfName || '__none__'}
                  onValueChange={(v) => setLogging({ existingLogConfName: v === '__none__' ? '' : v })}
                >
                  <SelectTrigger><SelectValue placeholder="Select a log profile" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none__">— select —</SelectItem>
                    {existingLogConfs.map((lc) => (
                      <SelectItem key={lc.metadata.name} value={lc.metadata.name}>
                        {lc.metadata.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              ) : (
                <p className="text-xs text-warning">
                  No APLogConf resources found in &quot;{state.namespace}&quot;. Switch to &quot;Create new&quot; to make one.
                </p>
              )}
            </div>
          )}

          {logging.mode === 'create' && (
            <div className="space-y-4 pl-3 border-l-2 border-border dark:border-border">
              <div className="space-y-1.5">
                <Label>Format</Label>
                <Select value={logging.format} onValueChange={(v) => setLogging({ format: v as APLogConfFormat })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {LOG_FORMATS.map((f) => <SelectItem key={f} value={f}>{f}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              {logging.format === 'user-defined' && (
                <div className="space-y-1.5">
                  <Label>Format String <span className="text-destructive">*</span></Label>
                  <Input
                    value={logging.formatString}
                    onChange={(e) => setLogging({ formatString: e.target.value })}
                    placeholder="%date_time,%src_ip,%method"
                  />
                </div>
              )}
              <div className="space-y-1.5">
                <Label>Request Type Filter</Label>
                <Select value={logging.requestType} onValueChange={(v) => setLogging({ requestType: v as APLogConfRequestType })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {REQUEST_TYPES.map((t) => <SelectItem key={t} value={t}>{t}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>Max Message Size</Label>
                <Input value={logging.maxMessageSize} onChange={(e) => setLogging({ maxMessageSize: e.target.value })} placeholder="64k" />
                <p className="text-xs text-muted-foreground">1k–64k</p>
              </div>
              <div className="space-y-1.5">
                <Label>Max Request Size</Label>
                <Input value={logging.maxRequestSize} onChange={(e) => setLogging({ maxRequestSize: e.target.value })} placeholder="any" />
                <p className="text-xs text-muted-foreground">A number, &quot;Nk&quot;, or &quot;any&quot;</p>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 4: Review
// ---------------------------------------------------------------------------

function ReviewStep({ state, isDark }: { state: WafWizardState; isDark: boolean }) {
  const [copied, setCopied] = useState(false);
  const preview = useMemo(() => {
    let policy: unknown = null;
    try { policy = JSON.parse(state.policyJsonText); } catch { policy = '<invalid JSON>'; }
    return JSON.stringify({
      apiVersion: 'appprotect.f5.com/v1',
      kind: 'APPolicy',
      metadata: { name: state.name, namespace: state.namespace },
      spec: { policy },
    }, null, 2);
  }, [state]);

  const logNote = state.logging.enabled
    ? state.logging.mode === 'pick'
      ? ` + existing APLogConf "${state.logging.existingLogConfName}"`
      : ` + new APLogConf "${state.name}-log"`
    : '';

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h4 className={cn('text-sm font-semibold', isDark ? 'text-white' : 'text-foreground')}>Review</h4>
          <p className={cn('text-xs mt-0.5', isDark ? 'text-muted-foreground' : 'text-muted-foreground')}>
            APPolicy will be created{logNote}.
          </p>
        </div>
        <Button
          variant="outline" size="sm" className="h-7 text-xs gap-1.5"
          onClick={() => { navigator.clipboard.writeText(preview); setCopied(true); setTimeout(() => setCopied(false), 2000); }}
        >
          {copied ? <Check className="h-3 w-3 text-success" /> : <Copy className="h-3 w-3" />}
          {copied ? 'Copied!' : 'Copy JSON'}
        </Button>
      </div>
      <div className={cn('rounded-lg border overflow-hidden', isDark ? 'border-border bg-card' : 'border-border bg-muted/50')}>
        <pre className="p-4 text-xs overflow-auto max-h-[380px]"><code>{preview}</code></pre>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main wizard
// ---------------------------------------------------------------------------

export function WafPolicyWizard({ clusterId, onClose }: WafPolicyWizardProps) {
  const { isDark } = useTheme();
  const [step, setStep] = useState<WafWizardStep>('basics');
  const [state, setState] = useState<WafWizardState>(createInitialState);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const createPolicy  = useCreateWafPolicy(clusterId);
  const createLogConf = useCreateWafLogConf(clusterId);
  const deletePolicy  = useDeleteWafPolicy(clusterId);

  // Live name-conflict check against cluster
  const { data: existingPolicies } = useWafPolicies(clusterId, state.namespace, { enabled: !!state.name && !!state.namespace });
  const nameConflict = useMemo(
    () => (existingPolicies?.policies ?? []).some((p) => p.metadata.name === state.name),
    [existingPolicies, state.name]
  );

  const onChange = (partial: Partial<WafWizardState>) => setState((prev) => ({ ...prev, ...partial }));

  const stepErrors = useMemo(() => validateWafWizardAll(state), [state]);
  const nameValidationError = state.name ? validateK8sName(state.name) : 'Name is required.';
  const currentStepErrors = validateWafWizardStep(state, step);
  const currentStepValid  = currentStepErrors.length === 0 && !(step === 'basics' && (nameConflict || !!nameValidationError));
  const allValid = Object.values(stepErrors).every((e) => e.length === 0) && !nameConflict && !nameValidationError;

  const stepIdx      = STEPS.findIndex((s) => s.key === step);
  const canGoBack    = stepIdx > 0;
  const canGoForward = stepIdx < STEPS.length - 1;
  const goNext = () => canGoForward && setStep(STEPS[stepIdx + 1].key);
  const goBack = () => canGoBack  && setStep(STEPS[stepIdx - 1].key);

  const isSubmitting = createPolicy.isPending || createLogConf.isPending;

  const handleCreate = async () => {
    if (!allValid) return;
    setSubmitError(null);

    let policyCreated = false;
    try {
      await createPolicy.mutateAsync({
        name: state.name,
        namespace: state.namespace,
        spec: { policy: JSON.parse(state.policyJsonText) as Record<string, unknown> },
      });
      policyCreated = true;
    } catch (e) {
      setSubmitError(`Failed to create policy: ${extractApiError(e)}`);
      return;
    }

    // 'pick' mode: no API call needed — existing APLogConf is referenced by name only
    if (state.logging.enabled && state.logging.mode === 'create') {
      try {
        await createLogConf.mutateAsync({
          name: `${state.name}-log`,
          namespace: state.namespace,
          spec: {
            content: {
              format: state.logging.format,
              ...(state.logging.format === 'user-defined' && state.logging.formatString
                ? { format_string: state.logging.formatString }
                : {}),
              max_message_size: state.logging.maxMessageSize,
              max_request_size: state.logging.maxRequestSize,
            },
            filter: { request_type: state.logging.requestType },
          },
        });
      } catch (e) {
        if (policyCreated) deletePolicy.mutate({ name: state.name, namespace: state.namespace });
        setSubmitError(`Failed to create log profile: ${extractApiError(e)}. The policy was rolled back.`);
        return;
      }
    }

    onClose?.();
  };

  return (
    <div className="flex flex-col gap-4">
      <StepIndicator currentStep={step} onStepClick={setStep} isDark={isDark} />

      <div className="min-h-[360px]">
        {step === 'basics'  && <BasicsStep  state={state} onChange={onChange} nameConflict={nameConflict} />}
        {step === 'policy'  && <PolicyStep  state={state} onChange={onChange} />}
        {step === 'logging' && <LoggingStep state={state} onChange={onChange} clusterId={clusterId} />}
        {step === 'review'  && <ReviewStep  state={state} isDark={isDark} />}
      </div>

      {currentStepErrors.length > 0 && (
        <div className={cn('flex flex-wrap gap-2 text-xs', isDark ? 'text-muted-foreground' : 'text-muted-foreground')}>
          {currentStepErrors.map((err) => (
            <span key={err} className="flex items-center gap-1">
              <AlertTriangle className="h-3 w-3 text-warning" />{err}
            </span>
          ))}
        </div>
      )}

      {submitError && (
        <div className="rounded-md border border-destructive/30 bg-destructive/10 dark:border-destructive/20 dark:bg-destructive/20/20 px-3 py-2 text-xs text-destructive dark:text-destructive/80 flex items-start gap-1.5">
          <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />{submitError}
        </div>
      )}

      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" className="h-8 text-xs gap-1.5" onClick={goBack} disabled={!canGoBack}>
            <ArrowLeft className="h-3.5 w-3.5" /> Back
          </Button>
          {onClose && (
            <Button variant="ghost" size="sm" className="h-8 text-xs" onClick={onClose}>Cancel</Button>
          )}
        </div>
        <div className="flex items-center gap-2">
          {step === 'review' ? (
            <Button
              size="sm"
              className="h-8 text-xs gap-1.5 bg-primary hover:bg-primary/90"
              onClick={handleCreate}
              disabled={!allValid || isSubmitting}
            >
              <Check className="h-3.5 w-3.5" /> Create
            </Button>
          ) : (
            <Button
              size="sm"
              className="h-8 text-xs gap-1.5 bg-primary hover:bg-primary/90"
              onClick={goNext}
              disabled={!canGoForward || !currentStepValid}
            >
              Next <ArrowRight className="h-3.5 w-3.5" />
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
