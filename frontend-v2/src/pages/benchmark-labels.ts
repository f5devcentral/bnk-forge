/**
 * What a benchmark run measured and how the proxy routed it, in plain words: model server,
 * router, workload and prefix-cache summary. Shared by the run detail and compare views;
 * the per-pod components live in benchmark-routing.tsx.
 */
import type { ModelServerStats } from '@/types';
import { PROXY_LABELS } from './benchmark-utils';

type Tags = Record<string, unknown> | null | undefined;
type Config = Record<string, unknown> | null | undefined;

/** Proxies that forward without an endpoint picker. */
export const PLAIN_PROXIES = new Set(['haproxy', 'nginx', 'envoy', 'nodeport']);

/** "Simulated Llama-3.3-70B × 4" or "vllm-openai (real model) × 4". */
export function modelServerLabel(tags: Tags): string | null {
  const server = tags?.model_server;
  if (typeof server !== 'string') return null;
  const replicas = typeof tags?.model_server_replicas === 'number' ? ` × ${tags.model_server_replicas}` : '';
  if (server === 'llm-d-inference-sim') {
    const model = String(tags?.sim_model ?? tags?.sim_profile ?? 'model').split('/').pop();
    return `Simulated ${model}${replicas}`;
  }
  return `${server} (real model)${replicas}`;
}

export function isSimulated(tags: Tags): boolean {
  return tags?.model_server === 'llm-d-inference-sim';
}

/** The endpoint picker the run went through, or how a plain proxy balances. */
export function routerLabel(proxy: string, tags: Tags): string {
  if (typeof tags?.router === 'string') {
    const kv = typeof tags.router_kv_events === 'string' ? ` · KV events: ${tags.router_kv_events}` : '';
    return `${tags.router}${kv}`;
  }
  if (PLAIN_PROXIES.has(proxy)) return `${PROXY_LABELS[proxy] ?? proxy} — no endpoint picker (cache-unaware)`;
  return PROXY_LABELS[proxy] ?? proxy;
}

function tokens(n: number): string {
  return n >= 1000 ? `${Number((n / 1000).toFixed(1))}k` : String(n);
}

/** "300 shared 16k-token contexts + 1k new · 64 out · Poisson 32 rps · 4,000 requests". */
export function workloadSummary(config: Config): string | null {
  if (!config) return null;
  const num = (k: string) => (typeof config[k] === 'number' ? (config[k] as number) : Number(config[k]) || 0);
  const parts: string[] = [];
  const prefixes = num('num_prefix_prompts');
  const prefixLen = num('prefix_prompt_length');
  const isl = num('synthetic_input_tokens_mean') || num('isl');
  if (prefixes && prefixLen) parts.push(`${prefixes} shared ${tokens(prefixLen)}-token contexts${isl ? ` + ${tokens(isl)} new` : ''}`);
  else if (isl) parts.push(`${tokens(isl)} tokens in`);
  const osl = num('output_tokens_mean') || num('osl');
  if (osl) parts.push(`${osl} out`);
  const rate = num('request_rate');
  const concurrency = num('concurrency');
  if (rate) parts.push(`${config.arrival_pattern === 'poisson' ? 'Poisson ' : ''}${rate} rps`);
  else if (concurrency) parts.push(`${concurrency} concurrent`);
  const count = num('request_count');
  const duration = num('benchmark_duration');
  if (count) parts.push(`${count.toLocaleString()} requests`);
  else if (duration) parts.push(`${duration} s`);
  return parts.length ? parts.join(' · ') : null;
}

/** One sentence on what the cache hit rate means for this run. */
export function cacheSentence(stats: ModelServerStats | null | undefined): string | null {
  const hit = stats?.totals.hit_pct;
  if (hit == null) return null;
  return `${hit.toFixed(0)}% of prompt tokens were served from the KV cache; the other ${(100 - hit).toFixed(0)}% were recomputed (prefill).`;
}

