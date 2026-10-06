"""Model-server counter snapshots → what the router did during a run."""

from services.model_server_stats import delta, parse_counters

METRICS = """# HELP vllm:request_success_total Count of successfully processed requests.
vllm:request_success_total{finished_reason="stop",model_name="llama3"} 7
vllm:request_success_total{finished_reason="length",model_name="llama3"} 3
vllm:request_success_created{model_name="llama3"} 1.7e9
vllm:prefix_cache_queries_total{model_name="llama3"} 1000
vllm:prefix_cache_hits_total{model_name="llama3"} 250
"""


def test_parse_sums_series_and_skips_created():
    assert parse_counters(METRICS) == {"requests": 10, "prefix_queries": 1000, "prefix_hits": 250}


def test_delta_per_pod_totals_and_restart():
    before = {"at": "t0", "pods": {
        "a": {"requests": 10, "prefix_queries": 1000, "prefix_hits": 250},
        "b": {"requests": 50, "prefix_queries": 5000, "prefix_hits": 4000},
    }}
    after = {"at": "t1", "pods": {
        "a": {"requests": 40, "prefix_queries": 4000, "prefix_hits": 2250},  # +30 req, 2000/3000 hit
        "b": {"requests": 10, "prefix_queries": 1000, "prefix_hits": 0},     # restarted: counts from 0
    }}
    out = delta(before, after)
    a, b = out["pods"]
    assert (a["requests"], a["hit_pct"], a["share_pct"]) == (30, 66.7, 75.0)
    assert b["counted_from"] == "pod start" and b["requests"] == 10
    assert out["totals"] == {"pods": 2, "requests": 40, "hit_pct": 50.0, "load_spread": 3.0, "max_share_pct": 75.0}


def test_no_after_snapshot_is_none():
    assert delta({"pods": {}}, None) is None


def test_fresh_pod_without_counters_starts_from_zero():
    before = {"at": "t0", "pods": {"a": {"node": "n1"}}}  # freshly restarted: no series yet
    after = {"at": "t1", "pods": {"a": {"requests": 5, "prefix_queries": 100, "prefix_hits": 40, "node": "n1"}}}
    out = delta(before, after)
    assert out["pods"][0]["requests"] == 5 and out["totals"]["hit_pct"] == 40.0
    assert delta(before, {"pods": {"a": {"node": "n1"}}}) is None  # no counters at all: not vLLM
