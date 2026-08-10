"""TMM traffic probe patterns for the cloud e2e harness.

Pure functions (builders, parsers, predicates) + the run loop with an
injected TunnelRunner so unit tests pass a fake returning canned stdout —
no AWS account required in tests.

Three patterns port the awsbnkctl Go scenarios exactly:
  - http-routing-e2e  : code-mode curl; all probes must return 200.
  - http-traffic-split: body-mode curl; both backend-a and backend-b must appear.
  - multi-vip         : body-mode curl on two VIPs; each VIP must serve its backend.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger("e2e.traffic")


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ProbeResult:
    """Outcome of one code-mode curl iteration."""

    iteration: int
    code: int
    seconds: float
    err: str = ""


@dataclass
class BodyProbeResult:
    """Outcome of one body-mode curl iteration."""

    iteration: int
    code: int
    body: str
    err: str = ""


@dataclass
class PatternResult:
    """Outcome of running one traffic pattern."""

    pattern_name: str
    ok: bool
    summary: str
    probes: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def with_last_octet(vip: str, octet: int) -> str:
    """Return vip with its last dotted-quad octet replaced.

    Returns vip unchanged if it is not a valid dotted-quad.
    """
    parts = vip.split(".")
    if len(parts) != 4:
        return vip
    parts[3] = str(octet)
    return ".".join(parts)


# ---------------------------------------------------------------------------
# Command builders (pure → str)
# ---------------------------------------------------------------------------


def build_curl_cmd(
    *,
    mode: str,
    vip: str,
    host: str | None,
    src_ip: str,
    timeout: int,
) -> str:
    """Return the remote curl command string.

    mode='code'  → -o /dev/null -w '%{http_code} %{time_total}'
    mode='body'  → -w '\\n%{http_code}'  (body captured to stdout)
    """
    host_hdr = f"-H 'Host: {host}' " if host else ""
    if mode == "code":
        return (
            f"curl -s -o /dev/null -w '%{{http_code}} %{{time_total}}' "
            f"{host_hdr}--interface {src_ip} --max-time {timeout} http://{vip}/"
        )
    # body mode
    return (
        f"curl -s -w '\\n%{{http_code}}' "
        f"{host_hdr}--interface {src_ip} --max-time {timeout} http://{vip}/"
    )


def build_send_key_cmd(
    *,
    instance_id: str,
    region: str,
    pub_key_path: str,
    instance_os_user: str = "ec2-user",
) -> list[str]:
    """Return argv for `aws ec2-instance-connect send-ssh-public-key`."""
    return [
        "aws",
        "ec2-instance-connect", "send-ssh-public-key",
        "--instance-id", instance_id,
        "--instance-os-user", instance_os_user,
        "--ssh-public-key", f"file://{pub_key_path}",
        "--region", region,
    ]


def build_ssh_cmd(
    *,
    instance_id: str,
    region: str,
    key_path: str,
    remote_cmd: str,
) -> list[str]:
    """Return argv for `ssh` via the EICE ProxyCommand."""
    proxy = (
        f"ProxyCommand=aws ec2-instance-connect open-tunnel "
        f"--instance-id {instance_id} --region {region}"
    )
    return [
        "ssh",
        "-o", proxy,
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=15",
        "-i", key_path,
        f"ec2-user@{instance_id}",
        remote_cmd,
    ]


# ---------------------------------------------------------------------------
# Parsers (pure)
# ---------------------------------------------------------------------------


def parse_code_probe(stdout: str) -> tuple[int, float]:
    """Parse code-mode curl output: '<code> <seconds>'.

    Returns (code, seconds). Raises ValueError on malformed input.
    """
    parts = stdout.strip().split()
    if len(parts) < 2:
        raise ValueError(f"unexpected code-mode output: {stdout!r}")
    try:
        code = int(parts[0])
    except ValueError as exc:
        raise ValueError(f"parsing http_code {parts[0]!r}: {exc}") from exc
    try:
        seconds = float(parts[1])
    except ValueError as exc:
        raise ValueError(f"parsing time_total {parts[1]!r}: {exc}") from exc
    return code, seconds


def parse_body_probe(stdout: str) -> tuple[str, int]:
    """Parse body-mode curl output: '<body>\\n<code>'.

    Splits on the LAST newline so multi-line bodies are preserved.
    Returns (body, code). Raises ValueError on malformed input.
    """
    last_nl = stdout.rfind("\n")
    if last_nl < 0:
        raise ValueError(f"unexpected body-mode output (no newline): {stdout!r}")
    body = stdout[:last_nl].strip()
    code_str = stdout[last_nl + 1:].strip()
    try:
        code = int(code_str)
    except ValueError as exc:
        raise ValueError(f"parsing http_code {code_str!r}: {exc}") from exc
    return body, code


# ---------------------------------------------------------------------------
# Success predicates (pure)
# ---------------------------------------------------------------------------


def routing_success(probes: list[ProbeResult]) -> bool:
    """True when every probe returned HTTP 200 with no error."""
    if not probes:
        return False
    return all(p.code == 200 and not p.err for p in probes)


def split_success(probes: list[BodyProbeResult]) -> bool:
    """True when both 'backend-a' and 'backend-b' appear in probe bodies."""
    seen_a = any("backend-a" in p.body for p in probes)
    seen_b = any("backend-b" in p.body for p in probes)
    return seen_a and seen_b


def multivip_success(
    probes_a: list[BodyProbeResult],
    probes_b: list[BodyProbeResult],
) -> bool:
    """True when VIP A serves 'multivip-backend-a' AND VIP B serves 'multivip-backend-b'."""
    ok_a = any("multivip-backend-a" in p.body for p in probes_a)
    ok_b = any("multivip-backend-b" in p.body for p in probes_b)
    return ok_a and ok_b


# ---------------------------------------------------------------------------
# TrafficPattern definitions
# ---------------------------------------------------------------------------


@dataclass
class TrafficPattern:
    name: str
    mode: str                   # "code" | "body"
    host: str | None            # Host header value, or None
    vip_octet: int | None       # pin last octet; None = use vip as-is
    min_iterations: int
    markers: list[str]          # body markers to check (empty for code-mode)
    # Additional per-pattern data used by step_cloud_traffic
    vip_b_octet: int | None = None        # multivip second VIP
    host_b: str | None = None             # multivip second Host header
    marker_b: str | None = None           # multivip second body marker


TRAFFIC_PATTERNS: dict[str, TrafficPattern] = {
    "http-routing-e2e": TrafficPattern(
        name="http-routing-e2e",
        mode="code",
        host="awsbnkctl.local",
        vip_octet=None,
        min_iterations=5,
        markers=[],
    ),
    "http-traffic-split": TrafficPattern(
        name="http-traffic-split",
        mode="body",
        host="awsbnkctl-split.local",
        vip_octet=101,
        min_iterations=10,
        markers=["backend-a", "backend-b"],
    ),
    "multi-vip": TrafficPattern(
        name="multi-vip",
        mode="body",
        host="multivip-a.local",
        vip_octet=106,
        min_iterations=5,
        markers=["multivip-backend-a"],
        vip_b_octet=107,
        host_b="multivip-b.local",
        marker_b="multivip-backend-b",
    ),
}


# ---------------------------------------------------------------------------
# TunnelRunner protocol + EICE implementation
# ---------------------------------------------------------------------------


class TunnelRunner(Protocol):
    """Injectable seam for the SSH+EICE probe.

    run_probe receives the remote curl command string and returns stdout.
    """

    def run_probe(self, remote_cmd: str) -> str:
        ...


class EICETunnelRunner:
    """Real SSH+EICE runner: mints key, pushes via ec2-instance-connect, runs SSH.

    Per-iteration: re-push the key to reset the EICE ~60s TTL.
    """

    def __init__(
        self,
        *,
        instance_id: str,
        region: str,
        instance_os_user: str = "ec2-user",
        settle_sleep: float = 2.0,
    ) -> None:
        self.instance_id = instance_id
        self.region = region
        self.instance_os_user = instance_os_user
        self.settle_sleep = settle_sleep
        # Set during context management
        self._key_path: str | None = None
        self._pub_key_path: str | None = None

    def _mint_key(self) -> tuple[str, str]:
        """Mint an ephemeral ed25519 keypair; return (key_path, pub_key_path)."""
        tmp_dir = tempfile.mkdtemp(prefix="e2e-eice-")
        key_path = os.path.join(tmp_dir, "id_ed25519")
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", key_path],
            check=True,
            capture_output=True,
        )
        pub_key_path = key_path + ".pub"
        return key_path, pub_key_path

    def _push_key(self, pub_key_path: str) -> None:
        """Push the public key via ec2-instance-connect (best-effort on re-push)."""
        argv = build_send_key_cmd(
            instance_id=self.instance_id,
            region=self.region,
            pub_key_path=pub_key_path,
            instance_os_user=self.instance_os_user,
        )
        subprocess.run(argv, check=True, capture_output=True)

    def prepare(self) -> None:
        """Mint key, push it, wait for settle. Call once before the probe loop."""
        key_path, pub_key_path = self._mint_key()
        self._key_path = key_path
        self._pub_key_path = pub_key_path
        self._push_key(pub_key_path)
        time.sleep(self.settle_sleep)

    def cleanup(self) -> None:
        """Remove temp key files."""
        for path in (self._key_path, self._pub_key_path):
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                    # Also clean up parent dir if we created it
                    parent = os.path.dirname(path)
                    if parent.startswith(tempfile.gettempdir()):
                        try:
                            os.rmdir(parent)
                        except OSError:
                            pass
                except OSError:
                    pass

    def run_probe(self, remote_cmd: str) -> str:
        """Re-push key (TTL reset) then run the remote curl via SSH+EICE."""
        if self._pub_key_path is None or self._key_path is None:
            raise RuntimeError("EICETunnelRunner.prepare() must be called first")
        # Re-push key before each probe to reset the ~60s EICE TTL.
        try:
            self._push_key(self._pub_key_path)
        except subprocess.CalledProcessError as exc:
            logger.warning("re-push key failed (continuing): %s", exc)

        argv = build_ssh_cmd(
            instance_id=self.instance_id,
            region=self.region,
            key_path=self._key_path,
            remote_cmd=remote_cmd,
        )
        result = subprocess.run(argv, capture_output=True, text=True)  # noqa: S603
        if result.returncode != 0:
            raise RuntimeError(
                f"ssh-curl failed (rc={result.returncode}): {result.stderr.strip()}"
            )
        return result.stdout


# ---------------------------------------------------------------------------
# run_traffic_pattern — orchestration loop with injected runner
# ---------------------------------------------------------------------------


def run_traffic_pattern(
    pattern: TrafficPattern,
    runner: TunnelRunner,
    *,
    vip: str,
    src_ip: str,
    iterations: int,
    timeout: int,
) -> PatternResult:
    """Run one traffic pattern using the injected runner.

    For 'multi-vip' the runner is called twice — once per VIP.
    Returns a PatternResult with ok=True iff the pattern's success predicate holds.
    """
    actual_iterations = max(iterations, pattern.min_iterations)

    if pattern.name == "multi-vip":
        return _run_multivip_pattern(
            pattern=pattern,
            runner=runner,
            vip=vip,
            src_ip=src_ip,
            iterations=actual_iterations,
            timeout=timeout,
        )

    if pattern.mode == "code":
        return _run_code_pattern(
            pattern=pattern,
            runner=runner,
            vip=vip,
            src_ip=src_ip,
            iterations=actual_iterations,
            timeout=timeout,
        )

    return _run_body_pattern(
        pattern=pattern,
        runner=runner,
        vip=vip,
        src_ip=src_ip,
        iterations=actual_iterations,
        timeout=timeout,
    )


def _run_code_pattern(
    *,
    pattern: TrafficPattern,
    runner: TunnelRunner,
    vip: str,
    src_ip: str,
    iterations: int,
    timeout: int,
) -> PatternResult:
    effective_vip = with_last_octet(vip, pattern.vip_octet) if pattern.vip_octet else vip
    probes: list[ProbeResult] = []

    for i in range(1, iterations + 1):
        remote_cmd = build_curl_cmd(
            mode="code",
            vip=effective_vip,
            host=pattern.host,
            src_ip=src_ip,
            timeout=timeout,
        )
        try:
            stdout = runner.run_probe(remote_cmd)
            code, secs = parse_code_probe(stdout)
            probes.append(ProbeResult(iteration=i, code=code, seconds=secs))
        except Exception as exc:  # noqa: BLE001
            probes.append(ProbeResult(iteration=i, code=0, seconds=0.0, err=str(exc)))

    ok = routing_success(probes)
    success_count = sum(1 for p in probes if p.code == 200 and not p.err)
    summary = f"{success_count}/{iterations} curls returned HTTP 200"
    if not ok:
        errors = [p.err for p in probes if p.err]
        if errors:
            summary += f" — last error: {errors[-1]}"
    return PatternResult(
        pattern_name=pattern.name,
        ok=ok,
        summary=summary,
        probes=[{"iteration": p.iteration, "code": p.code, "seconds": p.seconds, "err": p.err} for p in probes],
    )


def _run_body_pattern(
    *,
    pattern: TrafficPattern,
    runner: TunnelRunner,
    vip: str,
    src_ip: str,
    iterations: int,
    timeout: int,
) -> PatternResult:
    effective_vip = with_last_octet(vip, pattern.vip_octet) if pattern.vip_octet else vip
    probes: list[BodyProbeResult] = []

    for i in range(1, iterations + 1):
        remote_cmd = build_curl_cmd(
            mode="body",
            vip=effective_vip,
            host=pattern.host,
            src_ip=src_ip,
            timeout=timeout,
        )
        try:
            stdout = runner.run_probe(remote_cmd)
            body, code = parse_body_probe(stdout)
            probes.append(BodyProbeResult(iteration=i, code=code, body=body))
        except Exception as exc:  # noqa: BLE001
            probes.append(BodyProbeResult(iteration=i, code=0, body="", err=str(exc)))

    ok = split_success(probes)
    seen_a = any("backend-a" in p.body for p in probes)
    seen_b = any("backend-b" in p.body for p in probes)
    summary = f"{len(probes)} probes: seenA={seen_a} seenB={seen_b}"
    return PatternResult(
        pattern_name=pattern.name,
        ok=ok,
        summary=summary,
        probes=[{"iteration": p.iteration, "code": p.code, "body": p.body, "err": p.err} for p in probes],
    )


def _run_multivip_pattern(
    *,
    pattern: TrafficPattern,
    runner: TunnelRunner,
    vip: str,
    src_ip: str,
    iterations: int,
    timeout: int,
) -> PatternResult:
    vip_a = with_last_octet(vip, pattern.vip_octet) if pattern.vip_octet else vip
    vip_b = with_last_octet(vip, pattern.vip_b_octet) if pattern.vip_b_octet else vip
    host_a = pattern.host
    host_b = pattern.host_b

    probes_a: list[BodyProbeResult] = []
    probes_b: list[BodyProbeResult] = []

    for probes, cur_vip, cur_host, label in (
        (probes_a, vip_a, host_a, "A"),
        (probes_b, vip_b, host_b, "B"),
    ):
        for i in range(1, iterations + 1):
            remote_cmd = build_curl_cmd(
                mode="body",
                vip=cur_vip,
                host=cur_host,
                src_ip=src_ip,
                timeout=timeout,
            )
            try:
                stdout = runner.run_probe(remote_cmd)
                body, code = parse_body_probe(stdout)
                probes.append(BodyProbeResult(iteration=i, code=code, body=body))
            except Exception as exc:  # noqa: BLE001
                probes.append(BodyProbeResult(iteration=i, code=0, body="", err=str(exc)))

    ok = multivip_success(probes_a, probes_b)
    ok_a = any("multivip-backend-a" in p.body for p in probes_a)
    ok_b = any("multivip-backend-b" in p.body for p in probes_b)
    summary = f"VIP A (.{pattern.vip_octet}) ok={ok_a}; VIP B (.{pattern.vip_b_octet}) ok={ok_b}"
    all_probes = (
        [{"vip": "A", "iteration": p.iteration, "code": p.code, "body": p.body, "err": p.err} for p in probes_a]
        + [{"vip": "B", "iteration": p.iteration, "code": p.code, "body": p.body, "err": p.err} for p in probes_b]
    )
    return PatternResult(
        pattern_name=pattern.name,
        ok=ok,
        summary=summary,
        probes=all_probes,
    )
