"""Unit tests for the benchmark scenario registry + expansion (Phase 6).

Pure tests — no DB, no I/O. Assert each preset expands to the expected child count,
key flags propagate, sweep math (rc=c*5 etc.) is correct, and mooncake is a single
open-loop trace variant.
"""

import pytest

from services.benchmark_scenarios import (
    DEFAULT_SWEEP,
    MOONCAKE_TRACE_URL,
    SCENARIO_PRESETS,
    expand_scenario,
    get_scenario,
    list_scenarios,
)

BASE = {"base_url": "http://proxy:10080", "endpoint": "/v1/chat/completions"}


def _expand(key, **kw):
    return expand_scenario(key, **{**BASE, **kw})


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

def test_catalog_lists_all_presets():
    catalog = list_scenarios()
    keys = {c["key"] for c in catalog}
    assert keys == set(SCENARIO_PRESETS.keys())
    for item in catalog:
        assert item["child_run_count"] >= 1
        assert set(item.keys()) == {"key", "name", "description", "trace_driven", "child_run_count", "tags"}


def test_catalog_child_count_matches_expansion():
    for item in list_scenarios():
        children = _expand(item["key"])
        assert len(children) == item["child_run_count"], item["key"]


# ---------------------------------------------------------------------------
# Per-scenario child counts
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "key,expected",
    [
        ("baseline", 4),                 # sweep of 4
        ("high-concurrency", 4),
        ("mixed-workload", 1 + 4 + 4),   # warmup + short sweep + long sweep
        ("multi-turn", 4 + 4 + 4 + 4),   # 4 turns x sweep
        ("prefix-cache", 4),             # 4 (concurrency, ISL) pairs, 80% prefix
        ("prefix-cache-completions", 4),
        ("bimodal", 4),
        ("sustained-load", 5),           # sweep {50,100,150,200,250}
        ("burst-recovery", 5 * 2),       # 5 rounds x (burst + probe)
        ("mooncake", 1),                 # single open-loop trace
    ],
)
def test_scenario_child_count(key, expected):
    assert len(_expand(key)) == expected


# ---------------------------------------------------------------------------
# Sweep math + flag correctness
# ---------------------------------------------------------------------------

def test_baseline_request_count_formula():
    children = _expand("baseline")
    by_c = {c["concurrency"]: c for c in children}
    assert set(by_c) == set(DEFAULT_SWEEP)
    assert by_c[50]["request_count"] == max(50 * 5, 20)
    assert by_c[200]["request_count"] == 200 * 5
    for c in children:
        assert c["synthetic_input_tokens_mean"] == 500
        assert c["output_tokens_mean"] == 128
        assert c["streaming"] is True
        assert c["extra_inputs"] == ["ignore_eos:true"]
        assert c["ui"] == "none"
        assert c["url"] == BASE["base_url"]


def test_high_concurrency_rc_is_c_times_5():
    children = _expand("high-concurrency")
    pairs = {(c["concurrency"], c["synthetic_input_tokens_mean"]) for c in children}
    assert pairs == {(150, 5000), (200, 7000), (250, 9000), (300, 10000)}
    for c in children:
        assert c["request_count"] == c["concurrency"] * 5
        assert c["output_tokens_mean"] == 128


def test_sustained_load_rc_is_c_times_10_and_sweep():
    children = _expand("sustained-load")
    concurrencies = sorted(c["concurrency"] for c in children)
    assert concurrencies == [50, 100, 150, 200, 250]
    for c in children:
        assert c["request_count"] == c["concurrency"] * 10
        assert c["synthetic_input_tokens_mean"] == 1500
        assert c["synthetic_input_tokens_stddev"] == 300


def test_prefix_cache_heavy_pairs_with_80pct_prefix():
    children = _expand("prefix-cache")
    # (concurrency, ISL) pairs with prefix = 80% of ISL, unique = 20% of ISL.
    seen = {(c["concurrency"], c["prefix_prompt_length"], c["synthetic_input_tokens_mean"]) for c in children}
    assert seen == {
        (150, 4000, 1000), (200, 5600, 1400), (250, 7200, 1800), (300, 8000, 2000),
    }
    for c in children:
        assert c["num_prefix_prompts"] == 20
        assert c["output_tokens_mean"] == 128
        assert c["endpoint_type"] == "chat"
        assert c["request_count"] == c["concurrency"] * 5


def test_prefix_cache_completions_uses_completions_and_fp8_tokenizer():
    children = _expand("prefix-cache-completions")
    for c in children:
        assert c["endpoint_type"] == "completions"
        assert c["tokenizer"] == "neuralmagic/Meta-Llama-3.1-70B-Instruct-FP8"
        assert c["_env"] == {"HF_HOME": "/model-store"}


def test_bimodal_seq_dist_flag():
    children = _expand("bimodal")
    for c in children:
        assert c["seq_dist"] == "300|100,64|16:70;4000|500,256|64:30"
        assert c["request_count"] == max(c["concurrency"] * 5, 20)


def test_mixed_workload_phases():
    children = _expand("mixed-workload")
    phases = [c["_phase"] for c in children]
    assert phases.count("warmup") == 1
    assert phases.count("short") == 4
    assert phases.count("long") == 4
    warmup = next(c for c in children if c["_phase"] == "warmup")
    assert warmup["concurrency"] == 50
    assert warmup["request_count"] == 150
    long_variant = next(c for c in children if c["_phase"] == "long")
    assert long_variant["prefix_prompt_length"] == 1400
    assert long_variant["num_prefix_prompts"] == 10


def test_multi_turn_prefix_growth():
    children = _expand("multi-turn")
    turn1 = [c for c in children if c["_turn"] == 1]
    assert all("prefix_prompt_length" not in c for c in turn1)
    by_turn_prefix = {c["_turn"]: c.get("prefix_prompt_length") for c in children if c["_turn"] != 1}
    assert by_turn_prefix == {2: 500, 3: 1000, 4: 1500}
    for c in children:
        assert c["synthetic_input_tokens_mean"] == 500


def test_burst_recovery_rounds_and_phases():
    children = _expand("burst-recovery")
    bursts = [c for c in children if c["_phase"] == "burst"]
    probes = [c for c in children if c["_phase"] == "probe"]
    assert len(bursts) == 5 and len(probes) == 5
    for b in bursts:
        assert b["concurrency"] == 200
        assert b["request_count"] == 400
        assert b["seq_dist"] == "300|100,64|16:50;3000|400,200|50:50"
    for p in probes:
        assert p["concurrency"] == 25
        assert p["request_count"] == 50


# ---------------------------------------------------------------------------
# Mooncake — single open-loop trace variant
# ---------------------------------------------------------------------------

def test_mooncake_is_single_open_loop_trace():
    preset = get_scenario("mooncake")
    assert preset.trace_driven is True
    children = _expand("mooncake")
    assert len(children) == 1
    c = children[0]
    # No concurrency sweep
    assert "concurrency" not in c
    assert c["fixed_schedule"] is True
    assert c["custom_dataset_type"] == "mooncake_trace"
    assert c["trace_url"] == MOONCAKE_TRACE_URL
    assert c["_trace_dilation"] == 0.80
    assert c["random_seed"] == 42
    assert c["workers_max"] == 200
    assert c["goodput"] == "time_to_first_token:5000 inter_token_latency:100"
    assert c["model"] == "Qwen/Qwen3-32B"
    assert c["tokenizer"] == "Qwen/Qwen3-32B"


# ---------------------------------------------------------------------------
# Overrides + model injection + immutability
# ---------------------------------------------------------------------------

def test_model_override_applies_to_all_children():
    children = _expand("baseline", model="my-deployed-model")
    assert all(c["model"] == "my-deployed-model" for c in children)


def test_overrides_applied_last():
    children = _expand("baseline", overrides={"request_timeout_seconds": 999, "concurrency": 7})
    assert all(c["request_timeout_seconds"] == 999 for c in children)
    assert all(c["concurrency"] == 7 for c in children)


def test_expansion_does_not_mutate_registry():
    before = SCENARIO_PRESETS["baseline"].base_flags.copy()
    _expand("baseline", overrides={"streaming": False})
    assert SCENARIO_PRESETS["baseline"].base_flags == before


def test_unknown_scenario_raises_keyerror():
    with pytest.raises(KeyError):
        get_scenario("does-not-exist")


# ---------------------------------------------------------------------------
# Description ↔ computed-variant consistency (L2 — catalog strings must not lie)
# ---------------------------------------------------------------------------

def test_prefix_cache_description_matches_computed_prefix_lengths():
    """The catalog description must reflect the real prefix-prompt-length range
    (4000-8000), not the stale 1400 & 2400 values."""
    desc = SCENARIO_PRESETS["prefix-cache"].description
    children = _expand("prefix-cache")
    prefix_lengths = sorted(c["prefix_prompt_length"] for c in children)
    assert str(prefix_lengths[0]) in desc
    assert str(prefix_lengths[-1]) in desc
    # Stale values must be gone.
    assert "1400" not in desc
    assert "2400" not in desc


def test_high_concurrency_description_matches_computed_isl():
    """The catalog description must reflect the real ISL range (5000-10000),
    not the stale ISL 1000 value."""
    desc = SCENARIO_PRESETS["high-concurrency"].description
    children = _expand("high-concurrency")
    isls = sorted(c["synthetic_input_tokens_mean"] for c in children)
    assert str(isls[0]) in desc
    assert str(isls[-1]) in desc
    assert "ISL 1000" not in desc


# ---------------------------------------------------------------------------
# Override-key SSRF guard (belt-and-suspenders layer in expand_scenario)
# ---------------------------------------------------------------------------

def test_expand_scenario_strips_trace_url_override():
    """expand_scenario must not let caller's trace_url override the preset value."""
    # Use mooncake — it has a real trace_url in its preset
    children = _expand("mooncake", overrides={"trace_url": "http://evil.example.com/x", "model": "tinyllama"})
    for c in children:
        # trace_url must remain the preset's value, NOT the evil value
        assert c["trace_url"] != "http://evil.example.com/x"
        assert c["trace_url"] == MOONCAKE_TRACE_URL
        # benign override is preserved
        assert c["model"] == "tinyllama"


def test_expand_scenario_strips_underscore_prefixed_overrides():
    """expand_scenario must strip _-prefixed keys from caller overrides (Forge metadata namespace)."""
    children = _expand("baseline", overrides={"_scenario_key": "injected", "request_timeout_seconds": 30})
    for c in children:
        assert c["_scenario_key"] == "baseline"   # preset value wins
        assert c["request_timeout_seconds"] == 30  # benign override preserved


def test_expand_scenario_benign_override_applied():
    """expand_scenario must apply safe overrides to every child config."""
    children = _expand("prefix-cache", overrides={"model": "tinyllama", "request_timeout_seconds": 60})
    for c in children:
        assert c["model"] == "tinyllama"
        assert c["request_timeout_seconds"] == 60
