#!/usr/bin/env python3
"""
Seed demo benchmark data: 3 runs (envoy, nginx, direct) + 3 configs.

Usage:
    python scripts/seed_benchmark_demo.py [FORGE_URL] [--user USER] [--pass PASS]
    python scripts/seed_benchmark_demo.py https://10.176.11.91
    python scripts/seed_benchmark_demo.py https://10.176.11.91 --user admin --pass admin
"""
import argparse, json, sys, uuid, requests, urllib3
from datetime import datetime, timezone, timedelta

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------------------------------------------------------------------------
# CLI args
# ---------------------------------------------------------------------------
parser = argparse.ArgumentParser(description="Seed demo benchmark data")
parser.add_argument("forge_url", nargs="?", default="https://10.176.11.91",
                    help="Forge base URL (default: https://10.176.11.91)")
parser.add_argument("--user", default="admin", help="Login username (default: admin)")
parser.add_argument("--pass", dest="password", default="admin",
                    help="Login password (default: admin)")
parser.add_argument("--clean", action="store_true",
                    help="Delete existing demo runs and configs before seeding")
args = parser.parse_args()

FORGE_URL = args.forge_url.rstrip("/")
API = f"{FORGE_URL}/api/benchmarks"

# ---------------------------------------------------------------------------
# Authenticate — get JWT token
# ---------------------------------------------------------------------------

def get_auth_headers() -> dict:
    """Login to Forge and return Authorization header dict."""
    login_url = f"{FORGE_URL}/api/auth/login"
    resp = requests.post(login_url,
                         json={"username": args.user, "password": args.password},
                         verify=False, timeout=10)
    resp.raise_for_status()
    token = resp.json()["token"]
    return {"Authorization": f"Bearer {token}"}

try:
    AUTH = get_auth_headers()
    print(f"Authenticated as '{args.user}'")
except Exception as e:
    print(f"ERROR: Login failed — {e}")
    sys.exit(1)

# ============================================================================
# Shared phase config (11 phases)
# ============================================================================

PHASES_CONFIG = [
    {"name": "warm-up",        "num_requests": 200,  "concurrency": 10000, "request_rate": 5.0, "prompt_pool": "short"},
    {"name": "burst-heavy",    "num_requests": 4500, "concurrency": 10000, "request_rate": 0,   "prompt_pool": "xl"},
    {"name": "cool-down",      "num_requests": 100,  "concurrency": 10000, "request_rate": 0.5, "prompt_pool": "tiny"},
    {"name": "burst-mixed",    "num_requests": 5000, "concurrency": 10000, "request_rate": 0,   "prompt_pool": "all",
     "category_quotas": {"xl": 50, "large": 100}},
    {"name": "valley",         "num_requests": 150,  "concurrency": 10000, "request_rate": 1.0, "prompt_pool": "short"},
    {"name": "burst-large",    "num_requests": 4000, "concurrency": 10000, "request_rate": 0,   "prompt_pool": "large"},
    {"name": "quiet",          "num_requests": 80,   "concurrency": 10000, "request_rate": 0.5, "prompt_pool": "tiny"},
    {"name": "burst-max",      "num_requests": 6000, "concurrency": 10000, "request_rate": 0,   "prompt_pool": "all"},
    {"name": "recovery",       "num_requests": 200,  "concurrency": 10000, "request_rate": 2.0, "prompt_pool": "medium"},
    {"name": "spike",          "num_requests": 4500, "concurrency": 10000, "request_rate": 0,   "prompt_pool": "large"},
    {"name": "final-cooldown", "num_requests": 100,  "concurrency": 10000, "request_rate": 0.5, "prompt_pool": "tiny"},
]

# ============================================================================
# Helper: generate a realistic BenchmarkResult
# ============================================================================

def make_result(proxy: str, base_url: str, run_label: str, *,
                latency_base: float, rps_base: float, success_rate: float,
                duration: float, total_reqs: int):
    """Generate a BenchmarkResult matching the aiperf schema."""
    now = datetime.now(timezone.utc)
    start = now - timedelta(seconds=duration)

    # Phase results — distribute requests across 11 phases proportionally
    phase_names = [p["name"] for p in PHASES_CONFIG]
    phase_reqs = [p["num_requests"] for p in PHASES_CONFIG]
    total_phase_reqs = sum(phase_reqs)
    phases = {}
    for pname, preqs in zip(phase_names, phase_reqs):
        scaled = int(preqs / total_phase_reqs * total_reqs)
        is_burst = "burst" in pname or "spike" in pname or "max" in pname
        p50_mult = 1.8 if is_burst else 0.7
        p99_mult = 3.5 if is_burst else 1.5
        failed = max(0, int(scaled * (1 - success_rate / 100)))
        phases[pname] = {
            "name": pname,
            "requests": scaled,
            "success": scaled - failed,
            "failed": failed,
            "duration_seconds": round(duration * preqs / total_phase_reqs, 2),
            "input_tokens": scaled * 48,
            "output_tokens": scaled * 385,
            "latency": {
                "min": round(latency_base * 0.3, 4),
                "p25": round(latency_base * 0.6 * p50_mult, 4),
                "p50": round(latency_base * p50_mult, 4),
                "p75": round(latency_base * 1.3 * p50_mult, 4),
                "p90": round(latency_base * 1.8 * p50_mult, 4),
                "p95": round(latency_base * 2.5 * p50_mult, 4),
                "p99": round(latency_base * p99_mult, 4),
                "max": round(latency_base * 5.0, 4),
                "mean": round(latency_base * 1.1 * p50_mult, 4),
            },
            "rps": round(rps_base * (0.5 if not is_burst else 1.2), 1),
        }

    successful = int(total_reqs * success_rate / 100)
    failed = total_reqs - successful

    # Sample timeline (100 points)
    timeline = []
    for i in range(100):
        t = round(i * duration / 100, 2)
        phase_idx = min(i * len(phase_names) // 100, len(phase_names) - 1)
        lat = round(latency_base * (0.5 + 2.0 * ((i % 20) / 20)), 4)
        timeline.append({
            "t": t, "phase": phase_names[phase_idx],
            "event": "success", "latency": lat,
            "input_tokens": 48, "output_tokens": 385,
        })

    return {
        "result_id": str(uuid.uuid4()),
        "result_version": "1.0",
        "labels": {
            "proxy": proxy,
            "run_label": run_label,
            "base_url": base_url,
            "model": "openai/gpt-oss-120b",
            "endpoint": "/v1/chat/completions",
        },
        "tags": {"environment": "demo", "test_type": "standard"},
        "run_start": start.isoformat(),
        "run_end": now.isoformat(),
        "duration_seconds": duration,
        "duration_minutes": round(duration / 60, 2),
        "config": {
            "base_url": base_url,
            "endpoint": "/v1/chat/completions",
            "model": "openai/gpt-oss-120b",
            "max_tokens": 8192,
            "timeout": 600,
            "total_requests": total_reqs,
            "proxy": proxy,
            "run_label": run_label,
            "phases": PHASES_CONFIG,
        },
        "proxy_detection": {
            "server_header": f"{proxy}/1.28" if proxy != "nodeport" else "vllm/0.4.3",
            "detected_type": proxy,
        },
        "total_requests": total_reqs,
        "successful": successful,
        "failed": failed,
        "success_rate_pct": success_rate,
        "total_input_tokens": successful * 48,
        "total_output_tokens": successful * 385,
        "avg_input_tokens": 48.0,
        "avg_output_tokens": 385.0,
        "latency": {
            "min": round(latency_base * 0.2, 4),
            "p25": round(latency_base * 0.6, 4),
            "p50": round(latency_base, 4),
            "p75": round(latency_base * 1.5, 4),
            "p90": round(latency_base * 2.2, 4),
            "p95": round(latency_base * 3.0, 4),
            "p99": round(latency_base * 4.5, 4),
            "max": round(latency_base * 8.0, 4),
            "mean": round(latency_base * 1.2, 4),
        },
        "throughput": {
            "overall_rps": rps_base,
            "p50_rps": round(rps_base * 1.1, 1),
            "p90_rps": round(rps_base * 0.7, 1),
            "p99_rps": round(rps_base * 0.4, 1),
            "peak_rps": round(rps_base * 1.8, 1),
            "min_rps": round(rps_base * 0.2, 1),
            "gen_tokens_per_sec": round(rps_base * 385, 1),
        },
        "phases": phases,
        "timeline": timeline,
        "agent_name": None,
        "agent_hostname": None,
    }


# ============================================================================
# Seed runs
# ============================================================================

RUNS = [
    make_result("envoy", "http://envoy-svc.bench:8000", "envoy-standard-001",
                latency_base=0.145, rps_base=208.3, success_rate=99.8,
                duration=2700, total_reqs=25000),
    make_result("nginx", "http://nginx-svc.bench:8000", "nginx-standard-001",
                latency_base=0.162, rps_base=195.7, success_rate=99.6,
                duration=2850, total_reqs=25000),
    make_result("nodeport", "http://vllm-svc.bench:8000", "nodeport-standard-001",
                latency_base=0.128, rps_base=224.1, success_rate=99.9,
                duration=2500, total_reqs=25000),
]

# ============================================================================
# Seed configs
# ============================================================================

CONFIGS = [
    {
        "name": "NodePort (No Proxy)",
        "description": "Baseline — NodePort direct to vLLM, no proxy layer. Use for comparison baseline.",
        "tool": "aiperf",
        "config_json": {
            "base_url": "http://vllm-svc.bench:8000",
            "endpoint": "/v1/chat/completions",
            "model": "openai/gpt-oss-120b",
            "max_tokens": 8192,
            "timeout": 600,
            "total_requests": 25000,
            "proxy": "nodeport",
            "phases": PHASES_CONFIG,
        },
    },
    {
        "name": "Envoy L7 Proxy",
        "description": "Envoy as L7 proxy — standard 11-phase burst test through Envoy sidecar.",
        "tool": "aiperf",
        "config_json": {
            "base_url": "http://envoy-svc.bench:8000",
            "endpoint": "/v1/chat/completions",
            "model": "openai/gpt-oss-120b",
            "max_tokens": 8192,
            "timeout": 600,
            "total_requests": 25000,
            "proxy": "envoy",
            "phases": PHASES_CONFIG,
        },
    },
    {
        "name": "Nginx Reverse Proxy",
        "description": "Nginx as reverse proxy — standard 11-phase burst test through Nginx.",
        "tool": "aiperf",
        "config_json": {
            "base_url": "http://nginx-svc.bench:8000",
            "endpoint": "/v1/chat/completions",
            "model": "openai/gpt-oss-120b",
            "max_tokens": 8192,
            "timeout": 600,
            "total_requests": 25000,
            "proxy": "nginx",
            "phases": PHASES_CONFIG,
        },
    },
]

# ============================================================================
# Push to Forge
# ============================================================================

def clean_existing():
    """Delete all existing benchmark runs and configs."""
    print("Cleaning existing data...")

    # Delete runs first (may reference configs)
    resp = requests.get(f"{API}/runs?limit=100", headers=AUTH, verify=False)
    if resp.ok:
        runs = resp.json().get("runs", [])
        for run in runs:
            dr = requests.delete(f"{API}/runs/{run['id']}", headers=AUTH, verify=False)
            status = "deleted" if dr.status_code == 204 else f"error {dr.status_code}"
            print(f"  Run id={run['id']} ({run.get('proxy','?')}): {status}")

    # Delete configs
    resp = requests.get(f"{API}/configs", headers=AUTH, verify=False)
    if resp.ok:
        configs = resp.json() if isinstance(resp.json(), list) else resp.json().get("configs", [])
        for cfg in configs:
            dr = requests.delete(f"{API}/configs/{cfg['id']}", headers=AUTH, verify=False)
            status = "deleted" if dr.status_code == 204 else f"error {dr.status_code}"
            print(f"  Config id={cfg['id']} ({cfg.get('name','?')}): {status}")

    print()


def main():
    print(f"Seeding demo data to {FORGE_URL}")
    print(f"{'='*60}")

    if args.clean:
        clean_existing()

    # Seed configs
    for cfg in CONFIGS:
        resp = requests.post(f"{API}/configs", json=cfg, headers=AUTH, verify=False)
        if resp.status_code == 201:
            print(f"  Config created: {cfg['name']} (id={resp.json()['id']})")
        elif resp.status_code == 422:
            print(f"  Config skipped (validation): {cfg['name']} — {resp.text[:200]}")
        else:
            print(f"  Config error {resp.status_code}: {resp.text[:200]}")

    # Seed runs
    for run in RUNS:
        proxy = run["labels"]["proxy"]
        resp = requests.post(f"{API}/results", json=run, headers=AUTH, verify=False)
        if resp.status_code == 201:
            data = resp.json()
            print(f"  Run created: {proxy} (id={data['id']}, status={data['status']})")
        else:
            print(f"  Run error {resp.status_code} ({proxy}): {resp.text[:300]}")

    print(f"\n{'='*60}")
    print("Done! Check the Benchmarks page.")

if __name__ == "__main__":
    main()
