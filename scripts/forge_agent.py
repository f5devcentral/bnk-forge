#!/usr/bin/env python3
"""
Forge Benchmark Agent — connects to Forge via WebSocket and runs aiperf on demand.

Usage:
    python forge_agent.py \
        --forge-url https://10.176.11.91 \
        --agent-name loadgen-01 \
        --token <jwt-token>

TLS: by default the agent skips TLS verification everywhere (register POST, WS
handshake, trace download) because HGX exposes Forge over self-signed NodePort
certs. Pass --no-insecure (or FORGE_AGENT_INSECURE=0) to verify against the
system trust store.

The agent:
  1. Registers itself with Forge (POST /api/benchmarks/agents)
  2. Connects via WebSocket (WS /ws/benchmarks/agents/{agent_id})
  3. Sends heartbeats every 30s
  4. Listens for "run" commands
  5. Executes `aiperf profile` with the provided config (non-blocking)
  6. Reads the result JSON and sends it back as "run_completed"
"""

import argparse
import asyncio
import glob
import json
import logging
import os
import platform
import signal
import socket
import ssl
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

try:
    import websockets
    import websockets.exceptions
except ImportError:
    print("ERROR: 'websockets' package required. Install: pip install websockets", file=sys.stderr)
    sys.exit(1)

try:
    import requests
except ImportError:
    print("ERROR: 'requests' package required. Install: pip install requests", file=sys.stderr)
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("forge-agent")

HEARTBEAT_INTERVAL = 15  # seconds (shorter to keep WS alive)
RECONNECT_DELAY = 5
MAX_RECONNECT_DELAY = 60

# Max bytes the agent will buffer to disk for a trace download (M3). The
# toolagent mooncake trace is ~tens of MiB; 512 MiB leaves generous headroom
# while bounding an SSRF/arbitrary-fetch sink that pulls a Forge-supplied URL.
TRACE_MAX_BYTES = 512 * 1024 * 1024

# Schemes the agent is willing to fetch a trace from (M3). https only by default;
# plain http is allowed because HGX lab NodePort endpoints are commonly http.
TRACE_ALLOWED_SCHEMES = ("https", "http")


def _env_bool(name: str, default: bool) -> bool:
    """Parse a boolean env var. Accepts 1/true/yes/on (and their false twins),
    case-insensitive; falls back to ``default`` when unset or unrecognized."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    val = raw.strip().lower()
    if val in ("1", "true", "yes", "on"):
        return True
    if val in ("0", "false", "no", "off"):
        return False
    return default


def _raise_fd_limit(target: int = 65536) -> None:
    """Raise the soft open-file limit so high-fan-out aiperf runs (e.g. mooncake's
    --workers-max 200) can open enough sockets to register their worker processes.
    Spawned children inherit the raised soft limit. No-op if already high or if the
    limit can't be raised. Mirrors the f5-epp mooncake harness `ulimit -n 65536`.
    """
    try:
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        want = target if hard == resource.RLIM_INFINITY else min(target, hard)
        if soft < want:
            resource.setrlimit(resource.RLIMIT_NOFILE, (want, hard))
            log.info("Raised open-file soft limit %s -> %s (hard=%s)", soft, want, hard)
    except Exception as e:
        log.warning("Could not raise fd limit: %s", e)


class ForgeAgent:
    def __init__(
        self,
        forge_url: str,
        agent_name: str,
        token: str,
        insecure: bool = True,
        advertise_ip: str | None = None,
    ):
        self.forge_url = forge_url.rstrip("/")
        self.agent_name = agent_name
        self.token = token
        self.advertise_ip = advertise_ip  # explicit IP override for ip_address field
        # Single switch for ALL TLS-skip behavior (H2). Lab default is insecure:
        # HGX exposes Forge over self-signed NodePort certs, so the agent must
        # tolerate them out of the box. Set insecure=False (--no-insecure /
        # FORGE_AGENT_INSECURE=0) to verify the register POST, the WS handshake,
        # AND the trace download against the system trust store. When insecure we
        # also silence urllib3's per-request InsecureRequestWarning so logs stay
        # readable; when verifying we leave warnings intact.
        self.insecure = insecure
        if self.insecure:
            requests.packages.urllib3.disable_warnings()
        self.agent_id: int | None = None
        self.running = True
        self.current_process: asyncio.subprocess.Process | None = None
        # Serialize runs: one aiperf at a time. A run-group dispatches all of its
        # child runs at once, but running multiple aiperf processes concurrently
        # collides on shared state AND invalidates the load numbers (the clients
        # contend). Queue them and execute sequentially; aiperf's --concurrency is
        # what generates load.
        self._run_lock = asyncio.Lock()

        ws_scheme = "wss" if self.forge_url.startswith("https") else "ws"
        http_part = self.forge_url.split("://", 1)[1]
        self.ws_base = f"{ws_scheme}://{http_part}"

        # Token is optional — only sent when set (BENCHMARK_AGENT_AUTH_REQUIRED).
        self.headers: dict[str, str] = {"Content-Type": "application/json"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    def register(self) -> int:
        """Register (or upsert) the agent with Forge. Returns agent_id."""
        hostname = socket.gethostname()

        # Prefer explicit AGENT_ADVERTISE_IP over socket resolution
        if self.advertise_ip:
            ip = self.advertise_ip
        else:
            try:
                ip = socket.gethostbyname(hostname)
            except socket.gaierror:
                ip = "unknown"

        aiperf_version = "unknown"
        try:
            result = subprocess.run(
                ["aiperf", "--version"], capture_output=True, text=True, timeout=10
            )
            aiperf_version = result.stdout.strip() or result.stderr.strip() or "unknown"
        except Exception:
            pass

        payload = {
            "name": self.agent_name,
            "hostname": hostname,
            "ip_address": ip,
            # builtin:true lets the FE identify and badge this agent specially
            "tags": {"role": "forge-agent", "location": "auto", "builtin": True},
            "capabilities": {
                "platform": platform.system(),
                "python": platform.python_version(),
                "aiperf": aiperf_version,
            },
        }

        url = f"{self.forge_url}/api/benchmarks/agents"
        log.info("Registering agent '%s' at %s ...", self.agent_name, url)
        resp = requests.post(
            url, json=payload, headers=self.headers, verify=not self.insecure, timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        self.agent_id = data["id"]
        log.info("Registered as agent #%d (status=%s)", self.agent_id, data.get("status"))
        return self.agent_id

    async def run_forever(self):
        """Main loop: connect WS, heartbeat, listen for commands."""
        delay = RECONNECT_DELAY

        while self.running:
            try:
                # M2: the backend authenticates the agent WS by validating the
                # same JWT used in the register POST, passed as a ?token= query
                # param. URL-encode it so '+'/'='/'/' in the token survive.
                # Token is optional — only appended when set (the open agent
                # endpoints accept a token-less connection when auth is off).
                ws_url = f"{self.ws_base}/ws/benchmarks/agents/{self.agent_id}"
                if self.token:
                    token_q = urllib.parse.urlencode({"token": self.token})
                    ws_url = f"{ws_url}?{token_q}"
                log.info(
                    "Connecting to %s/ws/benchmarks/agents/%s ...",
                    self.ws_base,
                    self.agent_id,
                )

                # H2: TLS verification for the WS handshake follows the single
                # insecure flag. Lab default skips verification (self-signed HGX
                # NodePort cert); --no-insecure uses a verifying default context.
                ssl_ctx = ssl.create_default_context()
                if self.insecure:
                    ssl_ctx.check_hostname = False
                    ssl_ctx.verify_mode = ssl.CERT_NONE

                async with websockets.connect(
                    ws_url,
                    ssl=ssl_ctx if ws_url.startswith("wss") else None,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=10,
                    max_size=50 * 1024 * 1024,  # 50MB max message size
                ) as ws:
                    log.info("WebSocket connected!")
                    delay = RECONNECT_DELAY

                    # Run the receive loop AND the heartbeat concurrently; reconnect
                    # as soon as EITHER ends. The old code only awaited the receive
                    # loop, so a half-open socket (server-side handler gone, no close
                    # frame delivered) left run_forever blocked forever while the
                    # backend marked the agent disconnected — and no reconnect fired.
                    recv_task = asyncio.create_task(self._recv_loop(ws))
                    hb_task = asyncio.create_task(self._heartbeat_loop(ws))
                    done, pending = await asyncio.wait(
                        {recv_task, hb_task}, return_when=asyncio.FIRST_COMPLETED
                    )
                    for t in pending:
                        t.cancel()
                    for t in (*pending, *done):
                        try:
                            await t
                        except (asyncio.CancelledError, websockets.exceptions.ConnectionClosed):
                            pass
                        except Exception as e:
                            log.warning("Connection task ended: %s", e)
                    log.info("Connection ended — reconnecting")

            except Exception as e:
                log.error("Connection error: %s", e)

            if self.running:
                log.info("Reconnecting in %ds...", delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, MAX_RECONNECT_DELAY)

    async def _recv_loop(self, ws):
        """Receive and dispatch server commands until the connection closes."""
        async for raw_msg in ws:
            try:
                msg = json.loads(raw_msg)
            except json.JSONDecodeError:
                log.warning("Received non-JSON: %s", raw_msg[:100])
                continue

            msg_type = msg.get("type", "")
            log.info("Received command: %s", msg_type)

            if msg_type == "run":
                # Serialize execution (one aiperf at a time) via the run lock —
                # concurrent runs collide and distort load.
                asyncio.create_task(self._run_serialized(ws, msg))
            elif msg_type == "cancel":
                await self._handle_cancel(msg)
            elif msg_type == "ping":
                await ws.send(json.dumps({"type": "pong"}))
            else:
                log.warning("Unknown command: %s", msg_type)

    async def _heartbeat_loop(self, ws):
        """Send periodic heartbeats. Returns (ending the connection → reconnect) on
        any send failure OR timeout — a hung send is the signature of a half-open
        socket the OS hasn't torn down yet."""
        while True:
            try:
                await asyncio.wait_for(ws.send(json.dumps({
                    "type": "heartbeat",
                    "status": "running" if self.current_process else "connected",
                    "timestamp": time.time(),
                })), timeout=10)
            except Exception as e:
                log.warning("Heartbeat send failed/timed out (%s) — reconnecting", e)
                return
            await asyncio.sleep(HEARTBEAT_INTERVAL)

    async def _run_serialized(self, ws, msg: dict):
        """Serialize run execution: hold the run lock so queued runs execute one
        at a time. If a run-group dispatches N children at once, they queue here
        and run sequentially instead of colliding."""
        if self._run_lock.locked():
            log.info("Run #%s queued — waiting for the in-flight run to finish", msg.get("run_id"))
        async with self._run_lock:
            await self._handle_run(ws, msg)

    async def _handle_run(self, ws, msg: dict):
        """Execute an aiperf profile run using asyncio subprocess (non-blocking)."""
        run_id = msg.get("run_id")
        config = msg.get("config", {})
        log.info("=== Starting Run #%d ===", run_id)
        log.info("Config: %s", json.dumps(config, indent=2)[:500])

        try:
            await ws.send(json.dumps({
                "type": "progress",
                "run_id": run_id,
                "status": "running",
                "message": "aiperf profile starting...",
            }))
        except Exception:
            pass

        try:
            work_dir = Path(f"/tmp/forge-runs/run-{run_id}")
            work_dir.mkdir(parents=True, exist_ok=True)

            # Trace-driven (open-loop) scenarios: download + time-scale the trace,
            # then point aiperf at the local scaled file via --input-file.
            if config.get("trace_url") and config.get("custom_dataset_type"):
                config = self._prepare_trace_input(config, work_dir, run_id)

            cmd = self._build_aiperf_command(config)
            log.info("Command: %s", " ".join(cmd))

            env = os.environ.copy()
            env["AIPERF_OUTPUT_DIR"] = str(work_dir)
            # High-fan-out runs (e.g. mooncake: --workers-max 200) spawn many aiperf
            # worker processes that each open sockets to register. The default 1024
            # fd limit starves them ("Failed to register service Worker: TimeoutError")
            # and the run hangs. Raise the soft fd limit (children inherit it) and
            # set the HTTP connection cap to match workers-max, as the f5-epp
            # mooncake harness does (ulimit -n 65536 + AIPERF_HTTP_CONNECTION_LIMIT).
            workers_max = config.get("workers_max")
            if workers_max:
                _raise_fd_limit()
                env.setdefault("AIPERF_HTTP_CONNECTION_LIMIT", str(workers_max))

            # Use asyncio subprocess so we don't block the event loop.
            # start_new_session puts aiperf in its own session/process group, so a
            # cancel can signal the whole tree (aiperf forks ~45 helper processes)
            # via killpg. Without it they share the agent's group (PID 1) and only
            # the launcher PID is reachable — the children orphan and keep load up.
            self.current_process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(work_dir),
                env=env,
                start_new_session=True,
            )

            # Read output line by line (non-blocking)
            output_lines = []
            line_count = 0
            while True:
                line_bytes = await self.current_process.stdout.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode("utf-8", errors="replace").rstrip()
                output_lines.append(line)
                line_count += 1
                log.info("  [aiperf] %s", line)

                # Send progress every 10 lines
                if line_count % 10 == 0:
                    try:
                        await ws.send(json.dumps({
                            "type": "progress",
                            "run_id": run_id,
                            "status": "running",
                            "message": line[:200],
                            "lines": line_count,
                        }))
                    except Exception:
                        pass

            exit_code = await self.current_process.wait()
            self.current_process = None

            if exit_code != 0:
                last_lines = "\n".join(output_lines[-20:])
                raise RuntimeError(f"aiperf exited with code {exit_code}\n{last_lines}")

            # Find the result JSON
            result_file = self._find_result_json(work_dir)
            if not result_file:
                raise RuntimeError(f"No profile_export_aiperf.json found in {work_dir}")

            log.info("=== Run #%d completed successfully ===", run_id)
            log.info("Result file: %s", result_file)

            with open(result_file) as f:
                result_data = json.load(f)

            # Send the result back to Forge
            msg_payload = json.dumps({
                "type": "run_completed",
                "run_id": run_id,
                "result": result_data,
            })
            log.info("Sending run_completed (%d bytes)...", len(msg_payload))
            await ws.send(msg_payload)
            log.info("run_completed sent successfully for Run #%d", run_id)

        except Exception as e:
            log.error("=== Run #%d FAILED: %s ===", run_id, e)
            self.current_process = None
            try:
                await ws.send(json.dumps({
                    "type": "run_failed",
                    "run_id": run_id,
                    "error": str(e)[:2000],
                }))
            except Exception as send_err:
                log.error("Failed to send run_failed: %s", send_err)

    # Scalar flags: config key (underscores) → aiperf CLI flag (dashes).
    SCALAR_FLAG_MAP = {
        "url": "--url",
        "model": "--model",
        "endpoint_type": "--endpoint-type",
        "endpoint": "--endpoint",
        "request_count": "--request-count",
        "concurrency": "--concurrency",
        "request_rate": "--request-rate",
        "warmup_request_count": "--warmup-request-count",
        "request_timeout_seconds": "--request-timeout-seconds",
        "benchmark_duration": "--benchmark-duration",
        "isl": "--isl",
        "osl": "--osl",
        "synthetic_input_tokens_mean": "--synthetic-input-tokens-mean",
        "synthetic_input_tokens_stddev": "--synthetic-input-tokens-stddev",
        "output_tokens_mean": "--output-tokens-mean",
        "prefix_prompt_length": "--prefix-prompt-length",
        "num_prefix_prompts": "--num-prefix-prompts",
        "seq_dist": "--seq-dist",
        "tokenizer": "--tokenizer",
        "random_seed": "--random-seed",
        "workers_max": "--workers-max",
        "profile_export_level": "--profile-export-level",
        "record_processors": "--record-processors",
        "goodput": "--goodput",
        "custom_dataset_type": "--custom-dataset-type",
        "input_file": "--input-file",
        "artifact_dir": "--artifact-dir",
        "output_artifact_dir": "--output-artifact-dir",
    }

    # Boolean flags: present without a value when truthy.
    BOOL_FLAG_MAP = {
        "streaming": "--streaming",
        "fixed_schedule": "--fixed-schedule",
    }

    def _build_aiperf_command(self, config: dict) -> list[str]:
        """Build aiperf profile CLI args from config dict.

        Keys map to aiperf flags. ``ui`` defaults to ``none`` (matches f5-epp). Boolean
        flags appear without a value. ``extra_inputs`` is a list of ``key:val`` pairs each
        passed via a repeated ``--extra-inputs``. ``goodput`` is a single space-joined string.
        """
        cmd = ["aiperf", "profile"]

        for key, flag in self.SCALAR_FLAG_MAP.items():
            val = config.get(key)
            if val is not None:
                cmd.extend([flag, str(val)])

        for key, flag in self.BOOL_FLAG_MAP.items():
            if config.get(key):
                cmd.append(flag)

        # Repeated --extra-inputs key:val
        extra_inputs = config.get("extra_inputs")
        if extra_inputs:
            if isinstance(extra_inputs, str):
                extra_inputs = [extra_inputs]
            for item in extra_inputs:
                cmd.extend(["--extra-inputs", str(item)])

        # UI mode — config may force "none"; otherwise default to non-TUI.
        cmd.extend(["--ui", str(config.get("ui", "none"))])

        return cmd

    def _prepare_trace_input(self, config: dict, work_dir: Path, run_id) -> dict:
        """Download the trace, apply time-scaling, and return a config with --input-file set.

        Returns a NEW config dict (does not mutate the input). Drops the Forge-only
        ``trace_url`` key so it isn't passed to aiperf.

        On the dilation factor and its DIRECTION (M4): the canonical f5-epp mooncake
        harness (benchmarks/run-mooncake-aiperf.sh, mirroring NVIDIA's
        kv-router-ab-testing.md) defines the "0.80x" variant as
        ``timestamp = raw_timestamp / 0.80`` and calls it the "0.80x-SLOWED" trace.
        Dividing by a factor < 1 MULTIPLIES the timestamps (ts 80 -> 100), which
        STRETCHES inter-arrival gaps -> requests arrive SLOWER -> lower offered load.
        That is intentional and matches every committed baseline. So here "0.80x"
        means slowed, not sped up; the math below (``ts / dilation``) is the
        canonical transform, deliberately kept identical to the harness.
        """
        trace_url = config["trace_url"]
        dilation = float(config.get("_trace_dilation", 0.80))
        max_bytes = int(config.get("_trace_max_bytes", TRACE_MAX_BYTES))

        raw_path = work_dir / "trace_raw.jsonl"
        log.info("Downloading trace %s -> %s", trace_url, raw_path)
        self._download_trace(trace_url, raw_path, max_bytes)

        suffix = f"_{int(dilation * 100):03d}x.jsonl"
        dilated_path = work_dir / ("toolagent_trace" + suffix)
        count = self._dilate_trace(raw_path, dilated_path, dilation)
        log.info(
            "Time-scaled %d trace records by %.2fx (timestamps / %.2f -> slower arrivals) -> %s",
            count, dilation, dilation, dilated_path,
        )

        new_config = {k: v for k, v in config.items() if k != "trace_url"}
        new_config["input_file"] = str(dilated_path)
        # Mooncake uses --output-artifact-dir (not --artifact-dir).
        new_config.setdefault("output_artifact_dir", str(work_dir / "artifacts"))
        return new_config

    def _download_trace(self, trace_url: str, dst: Path, max_bytes: int) -> int:
        """Fetch a Forge-supplied trace URL to ``dst``, hardened against the
        SSRF / arbitrary-fetch sink this represents (M3).

        ``trace_url`` originates from the run config and can arrive via free-form
        API overrides, so it is fully untrusted. Hardening:
          - allowlist the URL scheme (https, plus http for lab NodePorts); reject
            file://, ftp://, gopher://, data:, etc.
          - stream the body to disk in chunks and abort once ``max_bytes`` is
            exceeded, so a hostile/huge target can't OOM the agent (the old code
            buffered the entire body in memory).

        Returns the number of bytes written. Raises ValueError on a disallowed
        scheme or oversize body.
        """
        parsed = urllib.parse.urlparse(trace_url)
        if parsed.scheme not in TRACE_ALLOWED_SCHEMES:
            raise ValueError(
                f"trace_url scheme '{parsed.scheme or '(none)'}' not allowed; "
                f"permitted: {', '.join(TRACE_ALLOWED_SCHEMES)}"
            )
        if not parsed.netloc:
            raise ValueError(f"trace_url has no host: {trace_url!r}")

        written = 0
        with requests.get(
            trace_url, timeout=120, stream=True, verify=not self.insecure
        ) as resp:
            resp.raise_for_status()
            # Fast-path reject when the server advertises an oversize body.
            declared = resp.headers.get("Content-Length")
            if declared is not None and declared.isdigit() and int(declared) > max_bytes:
                raise ValueError(
                    f"trace exceeds max size: Content-Length {declared} > {max_bytes} bytes"
                )
            with open(dst, "wb") as fout:
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > max_bytes:
                        raise ValueError(
                            f"trace exceeds max size: {written} > {max_bytes} bytes"
                        )
                    fout.write(chunk)
        return written

    @staticmethod
    def _dilate_trace(src: Path, dst: Path, dilation: float) -> int:
        """Divide each record's ``timestamp`` by ``dilation`` and write a new JSONL.

        Direction (M4): ``dilation < 1`` divides by a sub-unit factor, which
        INCREASES every timestamp and stretches inter-arrival gaps -> requests
        replay SLOWER (less offered load). ``dilation > 1`` would compress them
        (faster). This matches the canonical f5-epp harness, which labels the
        ``ts / 0.80`` variant "0.80x-slowed". See ``_prepare_trace_input``.

        Returns the number of records written. Blank lines are skipped; records
        without a numeric ``timestamp`` are passed through unchanged.
        """
        written = 0
        with open(src) as fin, open(dst, "w") as fout:
            for line in fin:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                ts = record.get("timestamp")
                if isinstance(ts, (int, float)):
                    record["timestamp"] = ts / dilation
                fout.write(json.dumps(record) + "\n")
                written += 1
        return written

    def _find_result_json(self, work_dir: Path) -> str | None:
        """Find profile_export_aiperf.json in artifacts dirs."""
        patterns = [
            str(work_dir / "artifacts" / "*" / "profile_export_aiperf.json"),
            str(work_dir / "*" / "profile_export_aiperf.json"),
            str(work_dir / "profile_export_aiperf.json"),
            os.path.expanduser("~/artifacts/*/profile_export_aiperf.json"),
        ]
        for pattern in patterns:
            files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
            if files:
                return files[0]
        return None

    async def _handle_cancel(self, msg: dict):
        """Cancel a running aiperf run — the whole process group.

        aiperf forks a tree of helpers (system_controller, workers,
        record_processors). The launcher runs in its own session
        (start_new_session=True), so signalling its process group takes the entire
        tree down; terminating just the launcher PID orphans the children and
        leaves load running. SIGTERM first, then SIGKILL if it doesn't exit.
        """
        run_id = msg.get("run_id")
        proc = self.current_process
        if not proc:
            return
        try:
            pgid = os.getpgid(proc.pid)
        except ProcessLookupError:
            self.current_process = None
            return

        log.warning("Cancelling run #%s (process group %d)", run_id, pgid)
        self._signal_group(pgid, signal.SIGTERM)
        try:
            await asyncio.wait_for(proc.wait(), timeout=10)
        except TimeoutError:
            log.warning("Run #%s ignored SIGTERM; sending SIGKILL to group %d", run_id, pgid)
            self._signal_group(pgid, signal.SIGKILL)
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except TimeoutError:
                pass
        self.current_process = None
        self._reap_orphans()

    @staticmethod
    def _signal_group(pgid: int, sig: int) -> None:
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            pass

    @staticmethod
    def _reap_orphans() -> None:
        """Best-effort reap of reparented zombies. The agent runs as PID 1, so
        killpg'd grandchildren reparent here and must be waited on, else they
        accumulate as <defunct> across runs."""
        while True:
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                break
            if pid == 0:
                break

    def shutdown(self):
        log.info("Shutting down...")
        self.running = False
        if self.current_process:
            self.current_process.terminate()


def main():
    # Environment variable defaults allow the built-in container to need zero args.
    # CLI flags override env vars for manual / external-agent usage.
    parser = argparse.ArgumentParser(description="Forge Benchmark Agent")
    parser.add_argument(
        "--forge-url",
        default=os.environ.get("FORGE_URL", ""),
        help="Forge base URL (e.g. http://backend:8000). Env: FORGE_URL",
    )
    parser.add_argument(
        "--agent-name",
        default=os.environ.get("AGENT_NAME", "forge-local"),
        help="Unique agent name. Env: AGENT_NAME (default: forge-local)",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("AGENT_TOKEN", ""),
        help=(
            "JWT bearer token. Env: AGENT_TOKEN. "
            "Optional — only required when BENCHMARK_AGENT_AUTH_REQUIRED is enabled on the server."
        ),
    )
    parser.add_argument(
        "--advertise-ip",
        default=os.environ.get("AGENT_ADVERTISE_IP", ""),
        help="IP address to report (overrides socket resolution). Env: AGENT_ADVERTISE_IP",
    )
    # H2: ONE switch governs all TLS-skip behavior (register POST, WS handshake,
    # trace download). Default is insecure (lab default — HGX self-signed
    # NodePort certs). Pass --no-insecure (or FORGE_AGENT_INSECURE=0) to verify.
    insecure_default = _env_bool("FORGE_AGENT_INSECURE", default=True)
    parser.add_argument(
        "--insecure",
        dest="insecure",
        action="store_true",
        default=insecure_default,
        help="Skip all TLS verification (default; HGX self-signed certs)",
    )
    parser.add_argument(
        "--no-insecure",
        dest="insecure",
        action="store_false",
        help="Verify TLS for register POST, WebSocket, and trace download",
    )
    args = parser.parse_args()

    if not args.forge_url:
        log.error("FORGE_URL / --forge-url is required")
        sys.exit(1)
    if not args.token:
        log.info("No token set — relying on open agent endpoints (BENCHMARK_AGENT_AUTH_REQUIRED is off)")

    agent = ForgeAgent(
        args.forge_url,
        args.agent_name,
        args.token,
        insecure=args.insecure,
        advertise_ip=args.advertise_ip or None,
    )

    def sighandler(sig, frame):
        agent.shutdown()
    signal.signal(signal.SIGINT, sighandler)
    signal.signal(signal.SIGTERM, sighandler)

    # Retry registration with backoff — the built-in container starts before
    # the backend is fully up, so the first few attempts may fail.
    register_delay = RECONNECT_DELAY
    while True:
        try:
            agent.register()
            break
        except Exception as e:
            log.warning("Registration failed (%s), retrying in %ds...", e, register_delay)
            time.sleep(register_delay)
            register_delay = min(register_delay * 2, MAX_RECONNECT_DELAY)

    log.info("Starting agent loop (Ctrl+C to stop)...")
    asyncio.run(agent.run_forever())


if __name__ == "__main__":
    main()
