"""Benchmark scenario registry — faithful reproduction of the f5-epp benchmark methodology.

A SCENARIO is a named, opinionated load-test recipe (e.g. ``prefix-cache``). The backend
EXPANDS a scenario into one parent RUN-GROUP plus N child runs — one per concurrency point
and/or phase. Each child run is still a single aiperf invocation dispatched to the external
agent over the existing WebSocket protocol, so the agent stays one-run-one-call.

This module is PURE and easily unit-testable: ``expand_scenario(key, base_url, model, ...)``
returns a list of per-child aiperf config dicts. There is no DB or I/O here.

Config dict keys map directly to ``forge_agent._build_aiperf_command()`` flag handling:
the keys are aiperf flag names with dashes replaced by underscores (e.g. ``synthetic_input_tokens_mean``
→ ``--synthetic-input-tokens-mean``). Keys prefixed with ``_`` are Forge metadata, not aiperf flags.

Reference: f5-epp/benchmarks (analyzed). All synthetic scenarios target
``meta-llama/Llama-3.1-70B-Instruct`` with ``--streaming`` and ``--extra-inputs ignore_eos:true``.
The mooncake scenario is the production trace: open-loop, trace-driven, no concurrency sweep.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass, field

# Default synthetic model + sweep used by most scenarios.
SYNTHETIC_MODEL = "meta-llama/Llama-3.1-70B-Instruct"
DEFAULT_SWEEP: tuple[int, ...] = (50, 100, 150, 200)

# Heavy (concurrency, ISL) pairs from the real comparison harness
# (~/go/src/benchmarks/scripts/run-{prefix-cache,high-concurrency}.sh). High
# concurrency paired with large prompts is what actually exercises prefix-cache
# routing and separates prefix-cache-aware backends (GAIE EPP vs F5-epp); the
# small DEFAULT_SWEEP barely loads the gateways and hides the difference.
HEAVY_CONC_ISL: tuple[tuple[int, int], ...] = ((150, 5000), (200, 7000), (250, 9000), (300, 10000))


@dataclass(frozen=True)
class ScenarioPreset:
    """A scenario recipe: metadata + a pure variant-expansion function.

    ``build_variants`` returns a list of per-child config dicts (the variant-specific
    aiperf flags + Forge ``_variant_label``). The base flags (url/model/endpoint/etc.)
    are merged in by ``expand_scenario``. Presets with a ``sweep_param`` take the
    load steps as an argument, so a launch can replace ``default_steps``.
    """

    key: str
    name: str
    description: str
    base_flags: dict
    build_variants: Callable[..., list[dict]]
    trace_driven: bool = False
    tags: list[str] = field(default_factory=list)
    sweep_param: str | None = None
    default_steps: tuple[float, ...] = ()

    def variants(self, steps: list[float] | None = None) -> list[dict]:
        if self.sweep_param:
            return self.build_variants(tuple(steps or self.default_steps))
        return self.build_variants()


# ---------------------------------------------------------------------------
# Shared base-flag builders
# ---------------------------------------------------------------------------


def _synthetic_base(endpoint_type: str = "chat") -> dict:
    """Common base flags for all synthetic (non-trace) scenarios."""
    return {
        "model": SYNTHETIC_MODEL,
        "endpoint_type": endpoint_type,
        "streaming": True,
        "extra_inputs": ["ignore_eos:true"],
        "ui": "none",
    }


def _rc_min(c: int, mult: int = 5, floor: int = 20) -> int:
    """Request-count formula: max(c*mult, floor)."""
    return max(c * mult, floor)


# ---------------------------------------------------------------------------
# Variant builders (one per scenario) — pure functions returning child configs
# ---------------------------------------------------------------------------


def _baseline_variants() -> list[dict]:
    return [
        {
            "_variant_label": f"c{c}",
            "concurrency": c,
            "request_count": _rc_min(c),
            "synthetic_input_tokens_mean": 500,
            "output_tokens_mean": 128,
        }
        for c in DEFAULT_SWEEP
    ]


def _high_concurrency_variants() -> list[dict]:
    """High concurrency (150-300) paired with large prompts (ISL 5k-10k), no
    prefix sharing. From run-high-concurrency.sh."""
    return [
        {
            "_variant_label": f"c{c}-isl{isl}",
            "concurrency": c,
            "request_count": c * 5,
            "synthetic_input_tokens_mean": isl,
            "output_tokens_mean": 128,
        }
        for c, isl in HEAVY_CONC_ISL
    ]


def _mixed_workload_variants() -> list[dict]:
    """Three phases per concurrency: warmup (fixed c=50), short (sweep), long (sweep)."""
    variants: list[dict] = [
        {
            "_variant_label": "warmup",
            "_phase": "warmup",
            "concurrency": 50,
            "request_count": 150,
            "synthetic_input_tokens_mean": 500,
            "output_tokens_mean": 128,
        }
    ]
    for c in DEFAULT_SWEEP:
        variants.append(
            {
                "_variant_label": f"short-c{c}",
                "_phase": "short",
                "concurrency": c,
                "request_count": c * 5,
                "synthetic_input_tokens_mean": 500,
                "output_tokens_mean": 64,
            }
        )
    for c in DEFAULT_SWEEP:
        variants.append(
            {
                "_variant_label": f"long-c{c}",
                "_phase": "long",
                "concurrency": c,
                "request_count": c * 5,
                "synthetic_input_tokens_mean": 600,
                "output_tokens_mean": 128,
                "prefix_prompt_length": 1400,
                "num_prefix_prompts": 10,
            }
        )
    return variants


def _multi_turn_variants() -> list[dict]:
    """Turn 1 has no prefix; turns 2-4 add growing prefix-prompt-length (500/1000/1500)."""
    variants: list[dict] = [
        {
            "_variant_label": f"turn1-c{c}",
            "_turn": 1,
            "concurrency": c,
            "request_count": c * 5,
            "synthetic_input_tokens_mean": 500,
            "output_tokens_mean": 128,
        }
        for c in DEFAULT_SWEEP
    ]
    for turn, prefix_len in ((2, 500), (3, 1000), (4, 1500)):
        for c in DEFAULT_SWEEP:
            variants.append(
                {
                    "_variant_label": f"turn{turn}-c{c}",
                    "_turn": turn,
                    "concurrency": c,
                    "request_count": c * 5,
                    "synthetic_input_tokens_mean": 500,
                    "output_tokens_mean": 128,
                    "prefix_prompt_length": prefix_len,
                    "num_prefix_prompts": 10,
                }
            )
    return variants


def _prefix_cache_variants() -> list[dict]:
    """High-concurrency, large-prompt prefix sharing — the workload that actually
    differentiates prefix-cache-aware routing. From run-prefix-cache.sh: per
    (concurrency, ISL) pair, 80% of the prompt is a shared prefix
    (prefix=ISL*0.8, unique=ISL*0.2) drawn from 20 prefix groups."""
    variants: list[dict] = []
    for c, isl in HEAVY_CONC_ISL:
        prefix_len = isl * 8 // 10
        unique_len = isl - prefix_len
        variants.append(
            {
                "_variant_label": f"isl{isl}-c{c}",
                "concurrency": c,
                "request_count": c * 5,
                "synthetic_input_tokens_mean": unique_len,
                "synthetic_input_tokens_stddev": unique_len // 10,
                "output_tokens_mean": 128,
                "num_prefix_prompts": 20,
                "prefix_prompt_length": prefix_len,
            }
        )
    return variants


def _bimodal_variants() -> list[dict]:
    seq_dist = "300|100,64|16:70;4000|500,256|64:30"
    return [
        {
            "_variant_label": f"c{c}",
            "concurrency": c,
            "request_count": _rc_min(c),
            "seq_dist": seq_dist,
        }
        for c in DEFAULT_SWEEP
    ]


def _sustained_load_variants() -> list[dict]:
    sweep = (50, 100, 150, 200, 250)
    return [
        {
            "_variant_label": f"c{c}",
            "concurrency": c,
            "request_count": c * 10,
            "synthetic_input_tokens_mean": 1500,
            "synthetic_input_tokens_stddev": 300,
            "output_tokens_mean": 128,
        }
        for c in sweep
    ]


def _burst_recovery_variants() -> list[dict]:
    """5 rounds, each = burst phase (c=200, seq-dist) + probe phase (c=25)."""
    burst_seq_dist = "300|100,64|16:50;3000|400,200|50:50"
    variants: list[dict] = []
    for rnd in range(1, 6):
        variants.append(
            {
                "_variant_label": f"round{rnd}-burst",
                "_round": rnd,
                "_phase": "burst",
                "concurrency": 200,
                "request_count": 400,
                "seq_dist": burst_seq_dist,
            }
        )
        variants.append(
            {
                "_variant_label": f"round{rnd}-probe",
                "_round": rnd,
                "_phase": "probe",
                "concurrency": 25,
                "request_count": 50,
                "synthetic_input_tokens_mean": 256,
                "synthetic_input_tokens_stddev": 50,
                "output_tokens_mean": 64,
            }
        )
    return variants


# Open-loop Poisson request-rate sweeps — the gateway-comparison standard
# (MLPerf Server, GAIE / llm-d QPS sweeps). Requests arrive on a schedule no
# matter how fast the proxy answers, so a slower proxy shows up as queueing
# (higher TTFT) instead of hiding behind fewer requests as in a closed loop.
POISSON_RATES: tuple[float, ...] = (2, 4, 8, 16, 32)
POISSON_PREFIX_RATES: tuple[float, ...] = (1, 2, 4, 8, 16)
POISSON_AGENTIC_RATES: tuple[float, ...] = (8, 16, 24, 32)
# MLPerf Server-style per-request targets (Llama-2-70B: TTFT 2 s, TPOT 200 ms).
DEFAULT_GOODPUT = "time_to_first_token:2000 inter_token_latency:200"
# Every step warms up, then measures for at least STEP_DURATION_FLOOR_S, and long
# enough to collect STEP_MIN_REQUESTS so p99 rests on a few hundred samples.
STEP_WARMUP_S = 30
STEP_DURATION_FLOOR_S = 120
MAX_STEP_DURATION_S = 3600
STEP_MIN_REQUESTS = 300
MAX_STEPS = 12
MIN_STEP_RATE = 0.1
MAX_STEP_RATE = 10000.0
MAX_DATASET_ENTRIES = 50000


def step_duration_s(rate: float) -> int:
    """Measured seconds for one rate step: the floor, longer at low rates, capped at MAX_STEP_DURATION_S."""
    if not math.isfinite(rate) or rate <= 0:
        return STEP_DURATION_FLOOR_S
    return min(MAX_STEP_DURATION_S, max(STEP_DURATION_FLOOR_S, math.ceil(STEP_MIN_REQUESTS / rate)))


def _rate_label(rate: float) -> str:
    return f"{rate:g}rps"


def _poisson_step(rate: float, workload: dict, index: int = 0) -> dict:
    duration = step_duration_s(rate)
    return {
        "_variant_label": _rate_label(rate),
        "request_rate": rate,
        "arrival_pattern": "poisson",
        "warmup_duration": STEP_WARMUP_S,
        "benchmark_duration": duration,
        "goodput": DEFAULT_GOODPUT,
        # A distinct prompt for every request the step sends: aiperf otherwise cycles
        # through 100, which a KV cache holds whole and every request then hits.
        "num_dataset_entries": min(MAX_DATASET_ENTRIES, math.ceil(rate * (STEP_WARMUP_S + duration))),
        # Same prompts for every proxy, so sweeps compare like for like; a new seed per
        # step so each step brings new sessions instead of replaying the last step's.
        "random_seed": 42 + index,
        **workload,
    }


def _poisson_rate_variants(rates: tuple[float, ...]) -> list[dict]:
    """Chat 1k in / 128 out (ISL random ±10%), one step per request rate."""
    workload = {
        "synthetic_input_tokens_mean": 1000,
        "synthetic_input_tokens_stddev": 100,
        "output_tokens_mean": 128,
    }
    return [_poisson_step(r, workload, i) for i, r in enumerate(rates)]


def _poisson_prefix_variants(rates: tuple[float, ...]) -> list[dict]:
    """KV-aware routing workload: 5k prompts, 80% shared prefix over 20 groups."""
    workload = {
        "synthetic_input_tokens_mean": 1000,
        "synthetic_input_tokens_stddev": 100,
        "prefix_prompt_length": 4000,
        "num_prefix_prompts": 20,
        "output_tokens_mean": 128,
    }
    return [_poisson_step(r, workload, i) for i, r in enumerate(rates)]


def _poisson_agentic_variants(rates: tuple[float, ...]) -> list[dict]:
    """Agentic long context: 300 sessions sharing a 16k-token context, 1k new tokens / 64 out.

    The shared contexts (~4.8M tokens) exceed the KV cache of four 70B replicas, as with
    real agent traffic, so replicas evict: cache-aware routing keeps a session's context on
    one replica and must track evictions; round robin re-prefills it everywhere. Short
    outputs (tool calls) make prefill the larger share of each request.
    """
    workload = {
        "synthetic_input_tokens_mean": 1000,
        "synthetic_input_tokens_stddev": 100,
        "prefix_prompt_length": 16000,
        "num_prefix_prompts": 300,
        "output_tokens_mean": 64,
    }
    return [_poisson_step(r, workload, i) for i, r in enumerate(rates)]


# Mooncake production trace — open-loop, single variant, no sweep.
MOONCAKE_MODEL = "Qwen/Qwen3-32B"
MOONCAKE_TRACE_URL = (
    "https://raw.githubusercontent.com/kvcache-ai/Mooncake/refs/heads/main/FAST25-release/traces/toolagent_trace.jsonl"
)
MOONCAKE_DILATION = 0.80


def _mooncake_variants() -> list[dict]:
    """Single open-loop trace variant — the agent downloads + time-dilates the trace."""
    return [
        {
            "_variant_label": "trace",
            "_trace_dilation": MOONCAKE_DILATION,
            "trace_url": MOONCAKE_TRACE_URL,
            "custom_dataset_type": "mooncake_trace",
            "fixed_schedule": True,
            "random_seed": 42,
            "workers_max": 200,
            "request_timeout_seconds": 1000,
            "profile_export_level": "summary",
            "record_processors": 8,
            "goodput": "time_to_first_token:5000 inter_token_latency:100",
        }
    ]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

SCENARIO_PRESETS: dict[str, ScenarioPreset] = {
    "baseline": ScenarioPreset(
        key="baseline",
        name="Baseline",
        description="ISL 500 / OSL 128, concurrency sweep {50,100,150,200}, rc=max(c*5,20).",
        base_flags=_synthetic_base(),
        build_variants=_baseline_variants,
        tags=["synthetic"],
    ),
    "high-concurrency": ScenarioPreset(
        key="high-concurrency",
        name="High Concurrency",
        description="Large prompts (ISL 5000-10000) / OSL 128, no prefix sharing, "
        "(concurrency,ISL) pairs (150,5000)(200,7000)(250,9000)(300,10000), rc=c*5.",
        base_flags=_synthetic_base(),
        build_variants=_high_concurrency_variants,
        tags=["synthetic"],
    ),
    "mixed-workload": ScenarioPreset(
        key="mixed-workload",
        name="Mixed Workload",
        description="Warmup + short + long phases per concurrency; long phase uses prefix prompts.",
        base_flags=_synthetic_base(),
        build_variants=_mixed_workload_variants,
        tags=["synthetic", "multi-phase"],
    ),
    "multi-turn": ScenarioPreset(
        key="multi-turn",
        name="Multi-Turn",
        description="Turn 1 no-prefix, turns 2-4 with growing prefix-prompt-length (500/1000/1500).",
        base_flags=_synthetic_base(),
        build_variants=_multi_turn_variants,
        tags=["synthetic", "prefix-cache"],
    ),
    "prefix-cache": ScenarioPreset(
        key="prefix-cache",
        name="Prefix Cache (chat)",
        description="80% shared prefix on large prompts / OSL 128, 20 prefix prompts, "
        "prefix-prompt-length 4000-8000 (unique-token mean 1000-2000), "
        "(concurrency,ISL) pairs (150,5000)(200,7000)(250,9000)(300,10000).",
        base_flags=_synthetic_base("chat"),
        build_variants=_prefix_cache_variants,
        tags=["synthetic", "prefix-cache"],
    ),
    "prefix-cache-completions": ScenarioPreset(
        key="prefix-cache-completions",
        name="Prefix Cache (completions)",
        description="Same as prefix-cache but --endpoint-type completions with FP8 tokenizer.",
        base_flags={
            **_synthetic_base("completions"),
            "tokenizer": "neuralmagic/Meta-Llama-3.1-70B-Instruct-FP8",
            "_env": {"HF_HOME": "/model-store"},
        },
        build_variants=_prefix_cache_variants,
        tags=["synthetic", "prefix-cache", "completions"],
    ),
    "bimodal": ScenarioPreset(
        key="bimodal",
        name="Bimodal",
        description="Two-mode seq-dist (short 70% / long 30%), rc=max(c*5,20), sweep.",
        base_flags=_synthetic_base(),
        build_variants=_bimodal_variants,
        tags=["synthetic"],
    ),
    "sustained-load": ScenarioPreset(
        key="sustained-load",
        name="Sustained Load",
        description="ISL 1500±300 / OSL 128, rc=c*10, sweep {50,100,150,200,250}.",
        base_flags=_synthetic_base(),
        build_variants=_sustained_load_variants,
        tags=["synthetic"],
    ),
    "burst-recovery": ScenarioPreset(
        key="burst-recovery",
        name="Burst Recovery",
        description="5 rounds of burst (c=200) + probe (c=25) phases.",
        base_flags=_synthetic_base(),
        build_variants=_burst_recovery_variants,
        tags=["synthetic", "multi-phase"],
    ),
    "poisson-rate": ScenarioPreset(
        key="poisson-rate",
        name="Poisson Rate Sweep (chat)",
        description="Open-loop Poisson arrivals, one step per request rate. ISL 1000±100 / OSL 128. "
        f"Each step: {STEP_WARMUP_S} s warmup, then at least {STEP_DURATION_FLOOR_S} s and "
        f"{STEP_MIN_REQUESTS} requests measured. Goodput targets TTFT 2000 ms, ITL 200 ms.",
        base_flags=_synthetic_base(),
        build_variants=_poisson_rate_variants,
        tags=["synthetic", "open-loop"],
        sweep_param="request_rate",
        default_steps=POISSON_RATES,
    ),
    "poisson-rate-prefix": ScenarioPreset(
        key="poisson-rate-prefix",
        name="Poisson Rate Sweep (shared prefix)",
        description="Open-loop Poisson arrivals for KV-aware routing: 5000-token prompts with an 80% "
        "shared prefix (20 groups) / OSL 128, one step per request rate. "
        f"Each step: {STEP_WARMUP_S} s warmup, then at least {STEP_DURATION_FLOOR_S} s and "
        f"{STEP_MIN_REQUESTS} requests measured. Goodput targets TTFT 2000 ms, ITL 200 ms.",
        base_flags=_synthetic_base(),
        build_variants=_poisson_prefix_variants,
        tags=["synthetic", "open-loop", "prefix-cache"],
        sweep_param="request_rate",
        default_steps=POISSON_PREFIX_RATES,
    ),
    "poisson-rate-agentic": ScenarioPreset(
        key="poisson-rate-agentic",
        name="Poisson Rate Sweep (agentic long context)",
        description="Open-loop Poisson arrivals for cache-aware routing on agent traffic: 300 sessions "
        "sharing a 16000-token context, 1000 new tokens / OSL 64 per request, one step per request rate. "
        "The shared contexts exceed the KV cache of several replicas, so run it against several replicas. "
        f"Each step: {STEP_WARMUP_S} s warmup, then at least {STEP_DURATION_FLOOR_S} s and "
        f"{STEP_MIN_REQUESTS} requests measured. Goodput targets TTFT 2000 ms, ITL 200 ms.",
        base_flags=_synthetic_base(),
        build_variants=_poisson_agentic_variants,
        tags=["synthetic", "open-loop", "prefix-cache", "agentic"],
        sweep_param="request_rate",
        default_steps=POISSON_AGENTIC_RATES,
    ),
    "mooncake": ScenarioPreset(
        key="mooncake",
        name="Mooncake Trace (production)",
        description="Open-loop, trace-driven Qwen3-32B run with 0.80x time-dilation. No concurrency sweep.",
        base_flags={
            "model": MOONCAKE_MODEL,
            "tokenizer": MOONCAKE_MODEL,
            "streaming": True,
            "ui": "none",
        },
        build_variants=_mooncake_variants,
        trace_driven=True,
        tags=["trace", "production"],
    ),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_RATE_STEP_INFO = {
    "step_warmup_s": STEP_WARMUP_S,
    "step_duration_floor_s": STEP_DURATION_FLOOR_S,
    "step_min_requests": STEP_MIN_REQUESTS,
}


def list_scenarios() -> list[dict]:
    """Return catalog metadata for every scenario (for the catalog endpoint)."""
    return [
        {
            "key": p.key,
            "name": p.name,
            "description": p.description,
            "trace_driven": p.trace_driven,
            "child_run_count": len(p.variants()),
            "tags": p.tags,
            "sweep_param": p.sweep_param,
            "default_steps": list(p.default_steps),
            **(_RATE_STEP_INFO if p.sweep_param == "request_rate" else {}),
        }
        for p in SCENARIO_PRESETS.values()
    ]


def get_scenario(scenario_key: str) -> ScenarioPreset:
    """Return a scenario preset by key, or raise KeyError."""
    return SCENARIO_PRESETS[scenario_key]


def _check_steps(preset: ScenarioPreset, steps: list[float]) -> list[float]:
    if not preset.sweep_param:
        raise ValueError(f"Scenario '{preset.key}' has a fixed sweep; load steps cannot be changed")
    clean: list[float] = []
    for s in steps:
        try:
            val = float(s)
        except (ValueError, TypeError):
            raise ValueError(
                f"Invalid step rate '{s}': must be a finite number between {MIN_STEP_RATE} and {MAX_STEP_RATE}"
            )
        if not math.isfinite(val) or val < MIN_STEP_RATE or val > MAX_STEP_RATE:
            raise ValueError(
                f"Step rate {s} out of bounds: must be a finite number between {MIN_STEP_RATE} and {MAX_STEP_RATE} req/s"
            )
        clean.append(val)
    dedup = sorted(set(clean))
    if not dedup or len(dedup) > MAX_STEPS:
        raise ValueError(f"Give 1-{MAX_STEPS} load steps between {MIN_STEP_RATE} and {MAX_STEP_RATE}")
    return dedup


def expand_scenario(
    scenario_key: str,
    *,
    base_url: str,
    endpoint: str,
    model: str | None = None,
    overrides: dict | None = None,
    steps: list[float] | None = None,
) -> list[dict]:
    """Expand a scenario into a list of per-child aiperf config dicts.

    Each child config = scenario ``base_flags`` ∪ variant flags, with ``url``/``endpoint``
    injected and an optional ``model`` override (e.g. the target's deployed model). The
    caller's ``overrides`` are applied LAST so users can tune a single run-group.
    ``steps`` replaces the load steps of a sweep preset (ValueError otherwise).

    Immutable: returns fresh dicts; never mutates the registry presets.
    """
    preset = SCENARIO_PRESETS[scenario_key]
    overrides = overrides or {}
    if steps is not None:
        steps = _check_steps(preset, steps)

    # Belt-and-suspenders SSRF guard: strip keys that select or affect the
    # fetched dataset (trace_url) and any _-prefixed internal keys before
    # merging caller-supplied overrides. The schema-layer validator
    # (ScenarioRunRequest.no_internal_keys) is the first line of defence; this
    # ensures the preset's own trace_url always wins even if overrides somehow
    # bypass validation (e.g. internal callers, future code paths).
    _FORBIDDEN = frozenset({"trace_url"})
    safe_overrides = {k: v for k, v in overrides.items() if k not in _FORBIDDEN and not k.startswith("_")}

    child_configs: list[dict] = []
    for variant in preset.variants(steps):
        config = {
            **preset.base_flags,
            **variant,
            "url": base_url,
            "endpoint": endpoint,
            "_scenario_key": scenario_key,
        }
        config.update(safe_overrides)
        # What is being tested comes from the target/proxy, never from overrides.
        config["url"] = base_url
        config["endpoint"] = endpoint
        if model:
            config["model"] = model
        if preset.sweep_param == "request_rate":
            # The step's rate, warmup and duration floor hold whatever the
            # overrides say; a request count would end the step early.
            rate = variant["request_rate"]
            config["request_rate"] = rate
            config["warmup_duration"] = max(config.get("warmup_duration") or 0, STEP_WARMUP_S)
            config["benchmark_duration"] = max(config.get("benchmark_duration") or 0, step_duration_s(rate))
            config.pop("request_count", None)
        child_configs.append(config)

    return child_configs
