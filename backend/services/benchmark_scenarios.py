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

    ``build_variants`` receives no arguments and returns a list of per-child config
    dicts (the variant-specific aiperf flags + Forge ``_variant_label``). The base
    flags (url/model/endpoint/etc.) are merged in by ``expand_scenario``.
    """

    key: str
    name: str
    description: str
    base_flags: dict
    build_variants: Callable[[], list[dict]]
    trace_driven: bool = False
    tags: list[str] = field(default_factory=list)


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
        variants.append({
            "_variant_label": f"short-c{c}",
            "_phase": "short",
            "concurrency": c,
            "request_count": c * 5,
            "synthetic_input_tokens_mean": 500,
            "output_tokens_mean": 64,
        })
    for c in DEFAULT_SWEEP:
        variants.append({
            "_variant_label": f"long-c{c}",
            "_phase": "long",
            "concurrency": c,
            "request_count": c * 5,
            "synthetic_input_tokens_mean": 600,
            "output_tokens_mean": 128,
            "prefix_prompt_length": 1400,
            "num_prefix_prompts": 10,
        })
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
            variants.append({
                "_variant_label": f"turn{turn}-c{c}",
                "_turn": turn,
                "concurrency": c,
                "request_count": c * 5,
                "synthetic_input_tokens_mean": 500,
                "output_tokens_mean": 128,
                "prefix_prompt_length": prefix_len,
                "num_prefix_prompts": 10,
            })
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
        variants.append({
            "_variant_label": f"isl{isl}-c{c}",
            "concurrency": c,
            "request_count": c * 5,
            "synthetic_input_tokens_mean": unique_len,
            "synthetic_input_tokens_stddev": unique_len // 10,
            "output_tokens_mean": 128,
            "num_prefix_prompts": 20,
            "prefix_prompt_length": prefix_len,
        })
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
        variants.append({
            "_variant_label": f"round{rnd}-burst",
            "_round": rnd,
            "_phase": "burst",
            "concurrency": 200,
            "request_count": 400,
            "seq_dist": burst_seq_dist,
        })
        variants.append({
            "_variant_label": f"round{rnd}-probe",
            "_round": rnd,
            "_phase": "probe",
            "concurrency": 25,
            "request_count": 50,
            "synthetic_input_tokens_mean": 256,
            "synthetic_input_tokens_stddev": 50,
            "output_tokens_mean": 64,
        })
    return variants


# Mooncake production trace — open-loop, single variant, no sweep.
MOONCAKE_MODEL = "Qwen/Qwen3-32B"
MOONCAKE_TRACE_URL = (
    "https://raw.githubusercontent.com/kvcache-ai/Mooncake/refs/heads/main/"
    "FAST25-release/traces/toolagent_trace.jsonl"
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

def list_scenarios() -> list[dict]:
    """Return catalog metadata for every scenario (for the catalog endpoint)."""
    return [
        {
            "key": p.key,
            "name": p.name,
            "description": p.description,
            "trace_driven": p.trace_driven,
            "child_run_count": len(p.build_variants()),
            "tags": p.tags,
        }
        for p in SCENARIO_PRESETS.values()
    ]


def get_scenario(scenario_key: str) -> ScenarioPreset:
    """Return a scenario preset by key, or raise KeyError."""
    return SCENARIO_PRESETS[scenario_key]


def expand_scenario(
    scenario_key: str,
    *,
    base_url: str,
    endpoint: str,
    model: str | None = None,
    overrides: dict | None = None,
) -> list[dict]:
    """Expand a scenario into a list of per-child aiperf config dicts.

    Each child config = scenario ``base_flags`` ∪ variant flags, with ``url``/``endpoint``
    injected and an optional ``model`` override (e.g. the target's deployed model). The
    caller's ``overrides`` are applied LAST so users can tune a single run-group.

    Immutable: returns fresh dicts; never mutates the registry presets.
    """
    preset = SCENARIO_PRESETS[scenario_key]
    overrides = overrides or {}

    # Belt-and-suspenders SSRF guard: strip keys that select or affect the
    # fetched dataset (trace_url) and any _-prefixed internal keys before
    # merging caller-supplied overrides. The schema-layer validator
    # (ScenarioRunRequest.no_internal_keys) is the first line of defence; this
    # ensures the preset's own trace_url always wins even if overrides somehow
    # bypass validation (e.g. internal callers, future code paths).
    _FORBIDDEN = frozenset({"trace_url"})
    safe_overrides = {
        k: v for k, v in overrides.items()
        if k not in _FORBIDDEN and not k.startswith("_")
    }

    child_configs: list[dict] = []
    for variant in preset.build_variants():
        config = {
            **preset.base_flags,
            **variant,
            "url": base_url,
            "endpoint": endpoint,
            "_scenario_key": scenario_key,
        }
        if model:
            config["model"] = model
        config.update(safe_overrides)
        child_configs.append(config)

    return child_configs
