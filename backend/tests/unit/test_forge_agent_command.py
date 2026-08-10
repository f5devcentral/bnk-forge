"""Unit tests for the forge_agent aiperf command builder + trace dilation (Phase 6).

Loads scripts/forge_agent.py directly (it lives outside the backend package). Tests
representative scenario child configs, including mooncake, and the 0.80x time-dilation
transform on a tiny synthetic JSONL.
"""

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

# Load scripts/forge_agent.py as a module.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_AGENT_PATH = _REPO_ROOT / "scripts" / "forge_agent.py"

pytestmark = pytest.mark.skipif(not _AGENT_PATH.exists(), reason="forge_agent.py not found")


def _load_agent_module():
    spec = importlib.util.spec_from_file_location("forge_agent", _AGENT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def agent():
    mod = _load_agent_module()
    return mod.ForgeAgent(forge_url="https://forge.local", agent_name="test-agent", token="tok")


# ---------------------------------------------------------------------------
# Command building — scalar, bool, list, and ui flags
# ---------------------------------------------------------------------------

def test_synthetic_config_basic_flags(agent):
    config = {
        "url": "http://proxy:10080",
        "model": "meta-llama/Llama-3.1-70B-Instruct",
        "endpoint_type": "chat",
        "endpoint": "/v1/chat/completions",
        "concurrency": 100,
        "request_count": 500,
        "synthetic_input_tokens_mean": 600,
        "synthetic_input_tokens_stddev": 150,
        "output_tokens_mean": 128,
        "prefix_prompt_length": 1400,
        "num_prefix_prompts": 20,
        "streaming": True,
        "extra_inputs": ["ignore_eos:true"],
        "ui": "none",
    }
    cmd = agent._build_aiperf_command(config)
    assert cmd[:2] == ["aiperf", "profile"]
    assert "--synthetic-input-tokens-mean" in cmd
    assert cmd[cmd.index("--synthetic-input-tokens-mean") + 1] == "600"
    assert "--synthetic-input-tokens-stddev" in cmd
    assert "--output-tokens-mean" in cmd
    assert "--prefix-prompt-length" in cmd
    assert "--num-prefix-prompts" in cmd
    assert "--concurrency" in cmd
    # ui none, not simple
    assert cmd[cmd.index("--ui") + 1] == "none"


def test_streaming_bool_flag_has_no_value(agent):
    cmd = agent._build_aiperf_command({"streaming": True})
    assert "--streaming" in cmd
    # The token right after --streaming must NOT be a value for it
    idx = cmd.index("--streaming")
    assert idx == len(cmd) - 1 or cmd[idx + 1].startswith("--")


def test_streaming_absent_when_falsey(agent):
    cmd = agent._build_aiperf_command({"streaming": False})
    assert "--streaming" not in cmd


def test_extra_inputs_repeated(agent):
    config = {"extra_inputs": ["ignore_eos:true", "temperature:0"]}
    cmd = agent._build_aiperf_command(config)
    assert cmd.count("--extra-inputs") == 2
    assert "ignore_eos:true" in cmd
    assert "temperature:0" in cmd


def test_extra_inputs_string_coerced_to_single(agent):
    cmd = agent._build_aiperf_command({"extra_inputs": "ignore_eos:true"})
    assert cmd.count("--extra-inputs") == 1
    assert "ignore_eos:true" in cmd


def test_seq_dist_flag(agent):
    seq = "300|100,64|16:70;4000|500,256|64:30"
    cmd = agent._build_aiperf_command({"seq_dist": seq})
    assert cmd[cmd.index("--seq-dist") + 1] == seq


def test_mooncake_config_flags(agent):
    """Mooncake: fixed_schedule bool, custom-dataset-type, input-file, output-artifact-dir, goodput."""
    config = {
        "url": "http://proxy:10080",
        "model": "Qwen/Qwen3-32B",
        "tokenizer": "Qwen/Qwen3-32B",
        "streaming": True,
        "custom_dataset_type": "mooncake_trace",
        "fixed_schedule": True,
        "random_seed": 42,
        "workers_max": 200,
        "request_timeout_seconds": 1000,
        "profile_export_level": "summary",
        "record_processors": 8,
        "goodput": "time_to_first_token:5000 inter_token_latency:100",
        "input_file": "/tmp/forge-runs/run-1/toolagent_trace_080x.jsonl",
        "output_artifact_dir": "/tmp/forge-runs/run-1/artifacts",
        "ui": "none",
    }
    cmd = agent._build_aiperf_command(config)
    # fixed-schedule is a bool flag with no value
    assert "--fixed-schedule" in cmd
    fs_idx = cmd.index("--fixed-schedule")
    assert fs_idx == len(cmd) - 1 or cmd[fs_idx + 1].startswith("--")
    assert cmd[cmd.index("--custom-dataset-type") + 1] == "mooncake_trace"
    assert cmd[cmd.index("--input-file") + 1].endswith("toolagent_trace_080x.jsonl")
    assert "--output-artifact-dir" in cmd
    assert "--artifact-dir" not in cmd
    assert cmd[cmd.index("--goodput") + 1] == "time_to_first_token:5000 inter_token_latency:100"
    assert cmd[cmd.index("--workers-max") + 1] == "200"
    assert cmd[cmd.index("--random-seed") + 1] == "42"
    assert cmd[cmd.index("--record-processors") + 1] == "8"
    assert cmd[cmd.index("--profile-export-level") + 1] == "summary"


def test_artifact_dir_vs_output_artifact_dir_distinct(agent):
    cmd1 = agent._build_aiperf_command({"artifact_dir": "/a"})
    assert "--artifact-dir" in cmd1 and "--output-artifact-dir" not in cmd1
    cmd2 = agent._build_aiperf_command({"output_artifact_dir": "/b"})
    assert "--output-artifact-dir" in cmd2 and "--artifact-dir" not in cmd2


# ---------------------------------------------------------------------------
# Trace dilation transform
# ---------------------------------------------------------------------------

def test_dilate_trace_divides_timestamp(agent, tmp_path):
    src = tmp_path / "trace_raw.jsonl"
    records = [
        {"timestamp": 0, "input_length": 100},
        {"timestamp": 80, "input_length": 200},
        {"timestamp": 160, "input_length": 300},
    ]
    src.write_text("\n".join(json.dumps(r) for r in records) + "\n")

    dst = tmp_path / "trace_080x.jsonl"
    count = agent._dilate_trace(src, dst, 0.80)
    assert count == 3

    out = [json.loads(line) for line in dst.read_text().splitlines()]
    assert out[0]["timestamp"] == 0 / 0.80
    assert out[1]["timestamp"] == 80 / 0.80   # 100.0
    assert out[2]["timestamp"] == 160 / 0.80  # 200.0
    # Non-timestamp fields preserved
    assert out[1]["input_length"] == 200


def test_dilate_trace_skips_blank_lines(agent, tmp_path):
    src = tmp_path / "raw.jsonl"
    src.write_text('{"timestamp": 40}\n\n  \n{"timestamp": 80}\n')
    dst = tmp_path / "out.jsonl"
    count = agent._dilate_trace(src, dst, 0.80)
    assert count == 2


def test_dilate_trace_passes_through_missing_timestamp(agent, tmp_path):
    src = tmp_path / "raw.jsonl"
    src.write_text('{"foo": "bar"}\n')
    dst = tmp_path / "out.jsonl"
    count = agent._dilate_trace(src, dst, 0.80)
    assert count == 1
    out = json.loads(dst.read_text().splitlines()[0])
    assert out == {"foo": "bar"}


# ---------------------------------------------------------------------------
# Run serialization — a run-group dispatches N children at once; the agent must
# execute them one at a time (concurrent aiperf runs collide + distort load).
# ---------------------------------------------------------------------------

def test_runs_are_serialized(agent):
    import asyncio

    order: list[tuple[str, int]] = []

    async def fake_handle(ws, msg):
        order.append(("enter", msg["run_id"]))
        await asyncio.sleep(0.05)
        order.append(("exit", msg["run_id"]))

    agent._handle_run = fake_handle

    async def drive():
        await asyncio.gather(
            agent._run_serialized(None, {"run_id": 1}),
            agent._run_serialized(None, {"run_id": 2}),
        )

    asyncio.run(drive())

    # Each run fully completes before the next starts (no interleaving).
    assert order in (
        [("enter", 1), ("exit", 1), ("enter", 2), ("exit", 2)],
        [("enter", 2), ("exit", 2), ("enter", 1), ("exit", 1)],
    )


def test_raise_fd_limit_is_safe_noop_for_low_target():
    """_raise_fd_limit must never lower the limit or crash (high-fan-out mooncake
    runs rely on it raising the soft fd limit so 200 workers can register)."""
    import resource
    mod = _load_agent_module()
    soft, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
    mod._raise_fd_limit(target=1)  # below current → must be a no-op
    assert resource.getrlimit(resource.RLIMIT_NOFILE)[0] == soft


# ---------------------------------------------------------------------------
# Cancel — must signal the whole aiperf process GROUP, not just the launcher PID
# ---------------------------------------------------------------------------

def test_handle_cancel_signals_process_group_with_sigterm(monkeypatch):
    """Regression: cancel sent SIGTERM to only the launcher PID, orphaning
    aiperf's ~45 children (load kept running). It must killpg the group."""
    mod = _load_agent_module()
    ag = mod.ForgeAgent(forge_url="https://forge.local", agent_name="t", token="tok")

    class FakeProc:
        pid = 4242
        async def wait(self):
            return -15

    ag.current_process = FakeProc()
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(mod.os, "getpgid", lambda pid: pid)  # own session → pgid == pid
    monkeypatch.setattr(mod.os, "killpg", lambda pgid, sig: killed.append((pgid, sig)))
    monkeypatch.setattr(mod.ForgeAgent, "_reap_orphans", staticmethod(lambda: None))

    asyncio.run(ag._handle_cancel({"run_id": 109}))

    assert killed == [(4242, mod.signal.SIGTERM)]
    assert ag.current_process is None


def test_handle_cancel_escalates_to_sigkill_on_timeout(monkeypatch):
    """If the group ignores SIGTERM, escalate to SIGKILL."""
    mod = _load_agent_module()
    ag = mod.ForgeAgent(forge_url="https://forge.local", agent_name="t", token="tok")

    class StubbornProc:
        pid = 5555
        def __init__(self):
            self.calls = 0
        async def wait(self):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError  # SIGTERM ignored
            return -9

    ag.current_process = StubbornProc()
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(mod.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(mod.os, "killpg", lambda pgid, sig: killed.append((pgid, sig)))
    monkeypatch.setattr(mod.ForgeAgent, "_reap_orphans", staticmethod(lambda: None))

    asyncio.run(ag._handle_cancel({"run_id": 110}))

    assert killed == [(5555, mod.signal.SIGTERM), (5555, mod.signal.SIGKILL)]
    assert ag.current_process is None


def test_handle_cancel_no_running_process_is_noop(monkeypatch):
    mod = _load_agent_module()
    ag = mod.ForgeAgent(forge_url="https://forge.local", agent_name="t", token="tok")
    ag.current_process = None
    monkeypatch.setattr(mod.os, "killpg", lambda *a: (_ for _ in ()).throw(AssertionError("must not signal")))
    asyncio.run(ag._handle_cancel({"run_id": 1}))  # no raise


# ---------------------------------------------------------------------------
# M2 — agent must send its JWT on WS connect as a ?token= query param
# ---------------------------------------------------------------------------

def test_ws_connect_url_carries_url_encoded_token(monkeypatch):
    """run_forever must connect to /ws/.../{agent_id}?token=<url-encoded JWT>.
    Captures the URL websockets.connect is called with and asserts the token
    round-trips through urllib.parse (handles '+', '/', '=' in JWTs)."""
    mod = _load_agent_module()
    raw_token = "abc.def+/=ghi"  # chars that MUST be percent-encoded in a query
    ag = mod.ForgeAgent(forge_url="https://forge.local", agent_name="t", token=raw_token)
    ag.agent_id = 7

    captured = {}

    class FakeWS:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    def fake_connect(url, **kwargs):
        captured["url"] = url
        captured["ssl"] = kwargs.get("ssl")
        ag.running = False  # stop the reconnect loop after the first attempt
        return FakeWS()

    # Make the connection body a no-op so run_forever falls through to "ended".
    async def fake_wait(tasks, **kwargs):
        return set(), set()

    monkeypatch.setattr(mod.websockets, "connect", fake_connect)
    monkeypatch.setattr(mod.asyncio, "wait", fake_wait)
    monkeypatch.setattr(mod.asyncio, "create_task", lambda coro: coro.close() or None)

    asyncio.run(ag.run_forever())

    url = captured["url"]
    assert url.startswith("wss://forge.local/ws/benchmarks/agents/7?")
    parsed = mod.urllib.parse.urlparse(url)
    qs = mod.urllib.parse.parse_qs(parsed.query)
    assert qs["token"] == [raw_token]  # decoded back to the exact JWT


# ---------------------------------------------------------------------------
# H2 — one insecure flag drives ALL TLS-skip behavior
# ---------------------------------------------------------------------------

def test_insecure_defaults_true_and_disables_warnings(monkeypatch):
    """Default (lab) is insecure; constructing an insecure agent silences
    urllib3 warnings. A verifying agent must NOT silence them."""
    mod = _load_agent_module()
    calls = []
    monkeypatch.setattr(
        mod.requests.packages.urllib3, "disable_warnings", lambda *a, **k: calls.append(1)
    )
    ag = mod.ForgeAgent(forge_url="https://forge.local", agent_name="t", token="tok")
    assert ag.insecure is True
    assert calls == [1]

    calls.clear()
    ag2 = mod.ForgeAgent(forge_url="https://forge.local", agent_name="t", token="tok", insecure=False)
    assert ag2.insecure is False
    assert calls == []


def test_register_verify_follows_insecure_flag(monkeypatch):
    """register() must pass verify=not insecure to requests.post."""
    mod = _load_agent_module()

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"id": 1, "status": "connected"}

        def get(self, *a):
            return None

    captured = {}

    def fake_post(url, **kwargs):
        captured["verify"] = kwargs.get("verify")
        return FakeResp()

    monkeypatch.setattr(mod.requests, "post", fake_post)
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(Exception("no aiperf")))

    ag = mod.ForgeAgent(forge_url="https://forge.local", agent_name="t", token="tok", insecure=True)
    ag.register()
    assert captured["verify"] is False

    ag2 = mod.ForgeAgent(forge_url="https://forge.local", agent_name="t", token="tok", insecure=False)
    ag2.register()
    assert captured["verify"] is True


def test_ws_ssl_context_verifies_when_not_insecure(monkeypatch):
    """A verifying agent's WS SSL context must keep check_hostname + CERT_REQUIRED;
    an insecure agent's context must be CERT_NONE."""
    mod = _load_agent_module()

    def make_ctx(insecure: bool):
        captured = {}

        class FakeWS:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

        ag = mod.ForgeAgent(forge_url="https://forge.local", agent_name="t", token="tok", insecure=insecure)
        ag.agent_id = 1

        def fake_connect(url, **kwargs):
            captured["ssl"] = kwargs.get("ssl")
            ag.running = False
            return FakeWS()

        async def fake_wait(tasks, **kwargs):
            return set(), set()

        monkeypatch.setattr(mod.websockets, "connect", fake_connect)
        monkeypatch.setattr(mod.asyncio, "wait", fake_wait)
        monkeypatch.setattr(mod.asyncio, "create_task", lambda coro: coro.close() or None)
        asyncio.run(ag.run_forever())
        return captured["ssl"]

    insecure_ctx = make_ctx(True)
    assert insecure_ctx.verify_mode == mod.ssl.CERT_NONE
    assert insecure_ctx.check_hostname is False

    verify_ctx = make_ctx(False)
    assert verify_ctx.verify_mode == mod.ssl.CERT_REQUIRED
    assert verify_ctx.check_hostname is True


def test_env_bool_parsing():
    mod = _load_agent_module()
    import os as _os

    saved = _os.environ.pop("FORGE_AGENT_INSECURE", None)
    try:
        assert mod._env_bool("FORGE_AGENT_INSECURE", default=True) is True
        assert mod._env_bool("FORGE_AGENT_INSECURE", default=False) is False
        for v, expect in [("0", False), ("false", False), ("no", False), ("off", False),
                          ("1", True), ("true", True), ("YES", True), ("On", True),
                          ("garbage", True)]:
            _os.environ["FORGE_AGENT_INSECURE"] = v
            assert mod._env_bool("FORGE_AGENT_INSECURE", default=True) is expect, v
    finally:
        _os.environ.pop("FORGE_AGENT_INSECURE", None)
        if saved is not None:
            _os.environ["FORGE_AGENT_INSECURE"] = saved


# ---------------------------------------------------------------------------
# M3 — trace download is a hardened sink: scheme allowlist + size cap
# ---------------------------------------------------------------------------

def test_download_trace_rejects_disallowed_scheme(agent, tmp_path):
    for bad in ["file:///etc/passwd", "ftp://host/x", "gopher://h/", "data:text/plain,hi", "/no/scheme"]:
        with pytest.raises(ValueError, match="scheme|host"):
            agent._download_trace(bad, tmp_path / "out.jsonl", max_bytes=1024)


def test_download_trace_streams_and_writes(agent, tmp_path, monkeypatch):
    mod = _load_agent_module()
    body = b'{"timestamp": 1}\n' * 10

    class FakeResp:
        headers = {"Content-Length": str(len(body))}

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size=0):
            yield body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(mod.requests, "get", lambda *a, **k: FakeResp())
    dst = tmp_path / "out.jsonl"
    n = agent._download_trace("https://lab/trace.jsonl", dst, max_bytes=len(body) + 1)
    assert n == len(body)
    assert dst.read_bytes() == body


def test_download_trace_aborts_when_content_length_exceeds_cap(agent, tmp_path, monkeypatch):
    mod = _load_agent_module()

    class FakeResp:
        headers = {"Content-Length": "999999999"}

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size=0):
            yield b"x"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(mod.requests, "get", lambda *a, **k: FakeResp())
    with pytest.raises(ValueError, match="exceeds max size"):
        agent._download_trace("https://lab/big.jsonl", tmp_path / "out.jsonl", max_bytes=1024)


def test_download_trace_aborts_when_streamed_body_exceeds_cap(agent, tmp_path, monkeypatch):
    """No/forged Content-Length: the streaming guard must still abort over-cap."""
    mod = _load_agent_module()

    class FakeResp:
        headers: dict = {}  # no Content-Length advertised

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size=0):
            for _ in range(100):
                yield b"y" * 1024

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(mod.requests, "get", lambda *a, **k: FakeResp())
    with pytest.raises(ValueError, match="exceeds max size"):
        agent._download_trace("https://lab/stream.jsonl", tmp_path / "out.jsonl", max_bytes=2048)


def test_download_trace_verify_follows_insecure_flag(tmp_path, monkeypatch):
    mod = _load_agent_module()
    body = b"{}\n"

    class FakeResp:
        headers = {"Content-Length": str(len(body))}

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size=0):
            yield body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    captured = {}

    def fake_get(url, **kwargs):
        captured["verify"] = kwargs.get("verify")
        captured["stream"] = kwargs.get("stream")
        return FakeResp()

    monkeypatch.setattr(mod.requests, "get", fake_get)

    ag_ins = mod.ForgeAgent(forge_url="https://f.local", agent_name="t", token="tok", insecure=True)
    ag_ins._download_trace("https://lab/t.jsonl", tmp_path / "a.jsonl", max_bytes=1024)
    assert captured["verify"] is False
    assert captured["stream"] is True

    ag_ver = mod.ForgeAgent(forge_url="https://f.local", agent_name="t", token="tok", insecure=False)
    ag_ver._download_trace("https://lab/t.jsonl", tmp_path / "b.jsonl", max_bytes=1024)
    assert captured["verify"] is True


# ---------------------------------------------------------------------------
# M4 — dilation DIRECTION: factor < 1 must slow arrivals (stretch gaps), matching
# the canonical f5-epp mooncake harness which labels ts/0.80 the "0.80x-slowed"
# trace. This asserts the resulting request-rate direction, not just arithmetic.
# ---------------------------------------------------------------------------

def test_dilation_below_one_slows_arrival_rate(agent, tmp_path):
    """Two records 80 apart at dilation 0.80: the inter-arrival gap must GROW
    (slower arrivals / lower offered load), per the harness's "0.80x-slowed"."""
    src = tmp_path / "raw.jsonl"
    src.write_text('{"timestamp": 0}\n{"timestamp": 80}\n')
    dst = tmp_path / "out.jsonl"
    agent._dilate_trace(src, dst, 0.80)

    out = [json.loads(line) for line in dst.read_text().splitlines()]
    raw_gap = 80 - 0
    new_gap = out[1]["timestamp"] - out[0]["timestamp"]
    assert new_gap > raw_gap  # gap stretched -> requests arrive SLOWER
    assert new_gap == 80 / 0.80  # exactly 100.0


def test_dilation_above_one_speeds_arrival_rate(agent, tmp_path):
    """Mirror check: factor > 1 must COMPRESS gaps (faster arrivals)."""
    src = tmp_path / "raw.jsonl"
    src.write_text('{"timestamp": 0}\n{"timestamp": 80}\n')
    dst = tmp_path / "out.jsonl"
    agent._dilate_trace(src, dst, 2.0)

    out = [json.loads(line) for line in dst.read_text().splitlines()]
    new_gap = out[1]["timestamp"] - out[0]["timestamp"]
    assert new_gap < 80  # gap compressed -> requests arrive FASTER
    assert new_gap == 80 / 2.0  # exactly 40.0
