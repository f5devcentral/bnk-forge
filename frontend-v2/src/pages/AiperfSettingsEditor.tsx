/**
 * AiperfSettingsEditor — edit aiperf run settings as a form or as raw JSON.
 *
 * Keys are aiperf option names in snake_case (concurrency → --concurrency); the
 * agent passes any key the installed aiperf supports, so the JSON view can set
 * options the form does not list. Clearing a field removes the key, so the
 * saved-config / built-in default applies again.
 */
import { useEffect, useState } from 'react';
import { cn } from '@/lib/utils';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';

export type AiperfSettings = Record<string, unknown>;

type FieldKind = 'number' | 'bool' | 'select' | 'list';

interface FieldDef {
  /** First key is canonical; later keys are aliases read from older configs. */
  keys: string[];
  label: string;
  help: string;
  kind: FieldKind;
  options?: string[];
}

interface FieldGroup {
  title: string;
  hint?: string;
  fields: FieldDef[];
}

const GROUPS: FieldGroup[] = [
  {
    title: 'Load',
    hint: 'Open-loop request rate is the standard way to compare gateways: a slower proxy cannot hide behind fewer requests.',
    fields: [
      { keys: ['request_rate'], label: 'Request rate (req/s)', kind: 'number', help: 'Open loop: target arrival rate. Combine with concurrency as a ceiling.' },
      { keys: ['arrival_pattern'], label: 'Arrival pattern', kind: 'select', options: ['poisson', 'constant', 'gamma'], help: 'Poisson matches MLPerf Server.' },
      { keys: ['concurrency'], label: 'Concurrency', kind: 'number', help: 'Closed loop: keep this many requests in flight.' },
      { keys: ['request_count'], label: 'Requests', kind: 'number', help: 'Total requests to send.' },
      { keys: ['benchmark_duration'], label: 'Duration (s)', kind: 'number', help: 'Run for a fixed time instead. 60–120 s gives stable percentiles.' },
      { keys: ['warmup_request_count'], label: 'Warmup requests', kind: 'number', help: 'Sent before measuring; excluded from results.' },
      { keys: ['warmup_duration'], label: 'Warmup (s)', kind: 'number', help: 'Time-based warmup, excluded from results.' },
    ],
  },
  {
    title: 'Workload',
    hint: 'SemiAnalysis-style shapes: 1k/1k chat, 1k/8k reasoning, 8k/1k summarization.',
    fields: [
      { keys: ['synthetic_input_tokens_mean', 'isl'], label: 'Input tokens (mean)', kind: 'number', help: 'Prompt length (ISL).' },
      { keys: ['synthetic_input_tokens_stddev'], label: 'Input tokens (stddev)', kind: 'number', help: 'Spread of prompt lengths.' },
      { keys: ['output_tokens_mean', 'osl'], label: 'Output tokens (mean)', kind: 'number', help: 'Generated length (OSL).' },
      { keys: ['output_tokens_stddev'], label: 'Output tokens (stddev)', kind: 'number', help: 'Spread of output lengths.' },
      { keys: ['ignore_eos'], label: 'Ignore EOS', kind: 'bool', help: 'Force the full output length on every request.' },
      { keys: ['streaming'], label: 'Streaming', kind: 'bool', help: 'Needed for TTFT and inter-token latency.' },
      { keys: ['endpoint_type'], label: 'Endpoint type', kind: 'select', options: ['chat', 'completions'], help: 'OpenAI API flavour.' },
      { keys: ['prefix_prompt_length'], label: 'Shared prefix (tokens)', kind: 'number', help: 'Prefix-cache workloads: shared prompt prefix length.' },
      { keys: ['num_prefix_prompts'], label: 'Prefix groups', kind: 'number', help: 'Number of distinct shared prefixes (tenants).' },
      { keys: ['conversation_turn_mean'], label: 'Turns per conversation', kind: 'number', help: 'Real multi-turn sessions (context accumulates).' },
      { keys: ['conversation_num'], label: 'Conversations', kind: 'number', help: 'Number of multi-turn sessions.' },
    ],
  },
  {
    title: 'Service-level targets (goodput)',
    hint: 'A request counts toward goodput only if it meets every target (DistServe / MLPerf).',
    fields: [
      { keys: ['goodput.time_to_first_token'], label: 'TTFT target (ms)', kind: 'number', help: 'MLPerf Server uses 2000 ms.' },
      { keys: ['goodput.inter_token_latency'], label: 'Inter-token target (ms)', kind: 'number', help: 'MLPerf Server uses 200 ms.' },
      { keys: ['goodput.request_latency'], label: 'Request latency target (ms)', kind: 'number', help: 'End-to-end limit per request.' },
    ],
  },
  {
    title: 'Measurement',
    fields: [
      { keys: ['server_metrics'], label: 'Server metrics URLs', kind: 'list', help: 'aiperf scrapes the proxy URL by default; add vLLM pod / gateway /metrics URLs (comma-separated).' },
      { keys: ['slice_duration'], label: 'Time slices (s)', kind: 'number', help: 'Per-window metrics to see warmup, saturation and recovery.' },
      { keys: ['use_server_token_count'], label: 'Server token counts', kind: 'bool', help: 'Use the server-reported usage instead of the client tokenizer.' },
      { keys: ['connection_reuse_strategy'], label: 'Connection reuse', kind: 'select', options: ['pooled', 'never', 'sticky-user-sessions'], help: '"never" measures connection setup through the proxy.' },
      { keys: ['request_timeout_seconds'], label: 'Request timeout (s)', kind: 'number', help: 'Per-request HTTP timeout.' },
      { keys: ['random_seed'], label: 'Random seed', kind: 'number', help: 'Reproducible prompts across proxies.' },
    ],
  },
];

const IGNORE_EOS = 'ignore_eos:true';

function parseGoodput(v: unknown): Record<string, number> {
  const out: Record<string, number> = {};
  if (typeof v !== 'string') return out;
  for (const pair of v.trim().split(/\s+/)) {
    const [k, n] = pair.split(':');
    if (k && n && !Number.isNaN(Number(n))) out[k] = Number(n);
  }
  return out;
}

function readField(settings: AiperfSettings, field: FieldDef): unknown {
  const [key] = field.keys;
  if (key.startsWith('goodput.')) return parseGoodput(settings.goodput)[key.slice(8)];
  if (key === 'ignore_eos') {
    const extra = settings.extra_inputs;
    return Array.isArray(extra) ? extra.includes(IGNORE_EOS) : extra === IGNORE_EOS ? true : undefined;
  }
  for (const k of field.keys) if (settings[k] !== undefined) return settings[k];
  return undefined;
}

function writeField(settings: AiperfSettings, field: FieldDef, value: unknown): AiperfSettings {
  const next = { ...settings };
  const [key] = field.keys;
  const empty = value === undefined || value === '' || (Array.isArray(value) && value.length === 0);
  if (key.startsWith('goodput.')) {
    const goodput = parseGoodput(next.goodput);
    if (empty) delete goodput[key.slice(8)];
    else goodput[key.slice(8)] = Number(value);
    const str = Object.entries(goodput).map(([k, n]) => `${k}:${n}`).join(' ');
    if (str) next.goodput = str;
    else delete next.goodput;
    return next;
  }
  if (key === 'ignore_eos') {
    const extra = (Array.isArray(next.extra_inputs) ? next.extra_inputs : next.extra_inputs ? [next.extra_inputs] : [])
      .filter((x) => x !== IGNORE_EOS);
    if (value) extra.push(IGNORE_EOS);
    if (extra.length) next.extra_inputs = extra;
    else delete next.extra_inputs;
    return next;
  }
  // Write to whichever alias the config already uses, else the canonical key.
  const target = field.keys.find((k) => next[k] !== undefined) ?? key;
  if (empty) delete next[target];
  else next[target] = value;
  return next;
}

function FieldInput({ field, value, placeholder, onChange }: {
  field: FieldDef;
  value: unknown;
  placeholder: unknown;
  onChange: (v: unknown) => void;
}) {
  const id = `aiperf-${field.keys[0]}`;
  const ph = placeholder == null ? '' : Array.isArray(placeholder) ? placeholder.join(', ') : String(placeholder);
  if (field.kind === 'bool') {
    const checked = value === undefined ? placeholder === true : value === true;
    return <Switch aria-label={field.label} checked={checked} onCheckedChange={(c) => onChange(c)} />;
  }
  if (field.kind === 'select') {
    return (
      <Select value={value != null ? String(value) : 'default'} onValueChange={(v) => onChange(v === 'default' ? undefined : v)}>
        <SelectTrigger id={id} className="h-8"><SelectValue /></SelectTrigger>
        <SelectContent>
          <SelectItem value="default">{ph ? `Default (${ph})` : 'Default'}</SelectItem>
          {field.options?.map((o) => <SelectItem key={o} value={o}>{o}</SelectItem>)}
        </SelectContent>
      </Select>
    );
  }
  if (field.kind === 'list') {
    return (
      <Input
        id={id}
        className="h-8 font-mono text-xs"
        value={Array.isArray(value) ? value.join(', ') : value != null ? String(value) : ''}
        placeholder={ph || 'none'}
        onChange={(e) => onChange(e.target.value.split(',').map((s) => s.trim()).filter(Boolean))}
      />
    );
  }
  return (
    <Input
      id={id}
      type="number"
      className="h-8 font-mono"
      value={value != null ? String(value) : ''}
      placeholder={ph}
      onChange={(e) => onChange(e.target.value === '' ? undefined : Number(e.target.value))}
    />
  );
}

export function AiperfSettingsEditor({ value, onChange, defaults = {}, lockedNote, className }: {
  /** The settings being edited (the effective config). */
  value: AiperfSettings;
  onChange: (next: AiperfSettings) => void;
  /** Values used when a field is left empty — shown as placeholders. */
  defaults?: AiperfSettings;
  /** Shown above the form, e.g. why url/model are not editable here. */
  lockedNote?: string;
  className?: string;
}) {
  const [view, setView] = useState<'form' | 'json'>('form');
  const [jsonText, setJsonText] = useState(() => JSON.stringify(value, null, 2));
  const [jsonError, setJsonError] = useState<string | null>(null);

  // Keep the JSON view in step with form edits (and outside resets).
  useEffect(() => {
    if (view === 'form') setJsonText(JSON.stringify(value, null, 2));
  }, [value, view]);

  const onJsonChange = (text: string) => {
    setJsonText(text);
    try {
      const parsed = JSON.parse(text);
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        setJsonError(null);
        onChange(parsed as AiperfSettings);
      } else {
        setJsonError('Settings must be a JSON object');
      }
    } catch (e) {
      setJsonError(String(e));
    }
  };

  return (
    <div className={cn('space-y-3', className)}>
      <div className="flex items-center justify-between gap-2">
        {lockedNote ? <p className="text-xs text-muted-foreground">{lockedNote}</p> : <span />}
        <div className="inline-flex rounded-md border border-border p-0.5 text-xs shrink-0">
          {(['form', 'json'] as const).map((v) => (
            <button
              key={v}
              type="button"
              className={cn('px-2.5 py-1 rounded', view === v ? 'bg-muted text-foreground' : 'text-muted-foreground hover:text-foreground')}
              onClick={() => { if (!jsonError) setView(v); }}
            >
              {v === 'form' ? 'Form' : 'JSON'}
            </button>
          ))}
        </div>
      </div>

      {view === 'json' ? (
        <div className="space-y-1">
          <Textarea
            value={jsonText}
            onChange={(e) => onJsonChange(e.target.value)}
            className="font-mono text-xs min-h-[260px]"
            spellCheck={false}
          />
          {jsonError ? (
            <p className="text-xs text-destructive">{jsonError}</p>
          ) : (
            <p className="text-xs text-muted-foreground">Any aiperf option works here in snake_case (e.g. <code>arrival_smoothness</code>).</p>
          )}
        </div>
      ) : (
        GROUPS.map((group) => (
          <div key={group.title} className="space-y-2">
            <div>
              <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">{group.title}</h4>
              {group.hint && <p className="text-xs text-muted-foreground">{group.hint}</p>}
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-2">
              {group.fields.map((field) => (
                <div key={field.keys[0]} className={cn('space-y-1', field.kind === 'bool' && 'flex items-start justify-between gap-2 space-y-0')}>
                  <div>
                    <Label htmlFor={`aiperf-${field.keys[0]}`} className="text-xs">{field.label}</Label>
                    {field.kind === 'bool' && <p className="text-[11px] text-muted-foreground">{field.help}</p>}
                  </div>
                  <FieldInput
                    field={field}
                    value={readField(value, field)}
                    placeholder={readField(defaults, field)}
                    onChange={(v) => onChange(writeField(value, field, v))}
                  />
                  {field.kind !== 'bool' && <p className="text-[11px] text-muted-foreground">{field.help}</p>}
                </div>
              ))}
            </div>
          </div>
        ))
      )}
    </div>
  );
}
