import { describe, expect, it } from 'vitest';
import { cacheSentence, modelServerLabel, routerLabel, workloadSummary } from '../benchmark-labels';

describe('benchmark labels', () => {
  it('describes a simulated model server and a real one', () => {
    expect(modelServerLabel({ model_server: 'llm-d-inference-sim', sim_model: 'unsloth/Llama-3.3-70B-Instruct', model_server_replicas: 4 }))
      .toBe('Simulated Llama-3.3-70B-Instruct × 4');
    expect(modelServerLabel({ model_server: 'vllm-openai', model_server_replicas: 2 })).toBe('vllm-openai (real model) × 2');
    expect(modelServerLabel(null)).toBeNull();
  });

  it('names the router, or says a plain proxy has none', () => {
    expect(routerLabel('haproxy', {})).toBe('HAProxy — no endpoint picker (cache-unaware)');
    expect(routerLabel('f5-bnk-epp', { router: 'F5 Endpoint Picker', router_kv_events: 'per pod' }))
      .toBe('F5 Endpoint Picker · KV events: per pod');
  });

  it('summarises an agentic Poisson workload', () => {
    expect(workloadSummary({
      num_prefix_prompts: 300, prefix_prompt_length: 16000, synthetic_input_tokens_mean: 1000,
      output_tokens_mean: 64, request_rate: 32, arrival_pattern: 'poisson', request_count: 6000,
    })).toBe('300 shared 16k-token contexts + 1k new · 64 out · Poisson 32 rps · 6,000 requests');
  });

  it('explains the cache hit rate', () => {
    expect(cacheSentence({ source: 's', pods: [], totals: { pods: 4, requests: 10, hit_pct: 49 } }))
      .toBe('49% of prompt tokens were served from the KV cache; the other 51% were recomputed (prefill).');
  });
});
