"""Unit tests for the cloud_traffic step and the traffic.py module.

No test shells out to `aws` or `ssh` — the EICETunnelRunner is never
instantiated. All probes use a fake TunnelRunner returning canned stdout.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from scripts.e2e.cloud_steps import step_cloud_traffic
from scripts.e2e.config import CloudBlueprintConfig, E2EConfig, TimeoutsConfig, TrafficConfig
from scripts.e2e.steps import Context
from scripts.e2e.traffic import (
    TRAFFIC_PATTERNS,
    BodyProbeResult,
    ProbeResult,
    build_curl_cmd,
    build_send_key_cmd,
    build_ssh_cmd,
    multivip_success,
    parse_body_probe,
    parse_code_probe,
    routing_success,
    run_traffic_pattern,
    split_success,
    with_last_octet,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeTunnelRunner:
    """Returns pre-canned stdout strings in order; raises RuntimeError when exhausted."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    def run_probe(self, _remote_cmd: str) -> str:
        if not self._responses:
            raise RuntimeError("FakeTunnelRunner: no more canned responses")
        return self._responses.pop(0)


def _make_ctx(
    *,
    cloud_provider: str = "aws",
    traffic: TrafficConfig | None = None,
    cloud: CloudBlueprintConfig | None = None,
) -> Context:
    if cloud is None:
        cloud = CloudBlueprintConfig(
            cloud_provider=cloud_provider,
            traffic=traffic,
        )
    cfg = E2EConfig.model_construct(
        bnk_forge_url="https://forge.test",
        bnk_forge_admin_user="admin",
        bnk_forge_admin_password="pw",
        project_name_prefix="e2e-",
        timeouts=TimeoutsConfig(),
        cloud=cloud,
        worker_node_ips=[],
        bmc_ips=[],
        worker_ssh_user="",
        worker_ssh_key_path="",
        bmc_password="",
        dpu_os_password="",
        bf_template="lag",
        bfb_image="",
        local_deploy=False,
    )
    return Context(cfg=cfg, client=MagicMock(), state={})


def _make_ctx_no_cloud() -> Context:
    cfg = E2EConfig.model_construct(
        bnk_forge_url="https://forge.test",
        bnk_forge_admin_user="admin",
        bnk_forge_admin_password="pw",
        project_name_prefix="e2e-",
        timeouts=TimeoutsConfig(),
        cloud=None,
        worker_node_ips=[],
        bmc_ips=[],
        worker_ssh_user="",
        worker_ssh_key_path="",
        bmc_password="",
        dpu_os_password="",
        bf_template="lag",
        bfb_image="",
        local_deploy=False,
    )
    return Context(cfg=cfg, client=MagicMock(), state={})


_AWS_ENV = {"AWS_ACCESS_KEY_ID": "AKI123", "AWS_SESSION_TOKEN": "tok"}


# ---------------------------------------------------------------------------
# with_last_octet
# ---------------------------------------------------------------------------


class TestWithLastOctet:
    def test_replaces_last_octet(self):
        assert with_last_octet("10.0.1.100", 101) == "10.0.1.101"

    def test_replaces_zero(self):
        assert with_last_octet("192.168.0.1", 0) == "192.168.0.0"

    def test_non_dotted_quad_unchanged(self):
        assert with_last_octet("notanip", 99) == "notanip"

    def test_split_octet_106(self):
        assert with_last_octet("10.0.1.100", 106) == "10.0.1.106"

    def test_split_octet_107(self):
        assert with_last_octet("10.0.1.100", 107) == "10.0.1.107"


# ---------------------------------------------------------------------------
# build_curl_cmd
# ---------------------------------------------------------------------------


class TestBuildCurlCmd:
    def test_code_mode_no_host(self):
        cmd = build_curl_cmd(mode="code", vip="10.0.1.100", host=None, src_ip="10.0.2.50", timeout=10)
        assert cmd == (
            "curl -s -o /dev/null -w '%{http_code} %{time_total}' "
            "--interface 10.0.2.50 --max-time 10 http://10.0.1.100/"
        )

    def test_code_mode_with_host(self):
        cmd = build_curl_cmd(mode="code", vip="10.0.1.100", host="awsbnkctl.local", src_ip="10.0.2.50", timeout=10)
        assert "-H 'Host: awsbnkctl.local'" in cmd
        assert "-o /dev/null" in cmd
        assert "%{http_code} %{time_total}" in cmd

    def test_body_mode_no_host(self):
        cmd = build_curl_cmd(mode="body", vip="10.0.1.101", host=None, src_ip="10.0.2.50", timeout=15)
        assert cmd == (
            "curl -s -w '\\n%{http_code}' "
            "--interface 10.0.2.50 --max-time 15 http://10.0.1.101/"
        )

    def test_body_mode_with_host(self):
        cmd = build_curl_cmd(mode="body", vip="10.0.1.101", host="awsbnkctl-split.local", src_ip="10.0.2.50", timeout=10)
        assert "-H 'Host: awsbnkctl-split.local'" in cmd
        assert "-w '\\n%{http_code}'" in cmd
        assert "-o /dev/null" not in cmd

    def test_interface_always_present(self):
        for mode in ("code", "body"):
            cmd = build_curl_cmd(mode=mode, vip="10.0.0.1", host=None, src_ip="1.2.3.4", timeout=5)
            assert "--interface 1.2.3.4" in cmd


# ---------------------------------------------------------------------------
# build_send_key_cmd
# ---------------------------------------------------------------------------


class TestBuildSendKeyCmd:
    def test_basic(self):
        argv = build_send_key_cmd(
            instance_id="i-0abc123",
            region="us-west-2",
            pub_key_path="/tmp/key.pub",
        )
        assert argv[0] == "aws"
        assert "ec2-instance-connect" in argv
        assert "send-ssh-public-key" in argv
        assert "--instance-id" in argv
        assert "i-0abc123" in argv
        assert "--region" in argv
        assert "us-west-2" in argv
        assert "file:///tmp/key.pub" in argv

    def test_default_os_user(self):
        argv = build_send_key_cmd(
            instance_id="i-0abc123",
            region="us-east-1",
            pub_key_path="/tmp/k.pub",
        )
        assert "--instance-os-user" in argv
        idx = argv.index("--instance-os-user")
        assert argv[idx + 1] == "ec2-user"

    def test_custom_os_user(self):
        argv = build_send_key_cmd(
            instance_id="i-0abc123",
            region="us-east-1",
            pub_key_path="/tmp/k.pub",
            instance_os_user="ubuntu",
        )
        idx = argv.index("--instance-os-user")
        assert argv[idx + 1] == "ubuntu"


# ---------------------------------------------------------------------------
# build_ssh_cmd
# ---------------------------------------------------------------------------


class TestBuildSshCmd:
    def test_structure(self):
        argv = build_ssh_cmd(
            instance_id="i-0abc123",
            region="us-west-2",
            key_path="/tmp/id_ed25519",
            remote_cmd="curl -s http://10.0.1.100/",
        )
        assert argv[0] == "ssh"
        # ProxyCommand must reference open-tunnel
        proxy = next(v for v in argv if v.startswith("ProxyCommand="))
        assert "open-tunnel" in proxy
        assert "i-0abc123" in proxy
        assert "us-west-2" in proxy
        # StrictHostKeyChecking=no
        assert "StrictHostKeyChecking=no" in argv
        assert "UserKnownHostsFile=/dev/null" in argv
        assert "ConnectTimeout=15" in argv
        # Key file
        assert "-i" in argv
        assert "/tmp/id_ed25519" in argv
        # Target and remote cmd
        assert "ec2-user@i-0abc123" in argv
        assert "curl -s http://10.0.1.100/" in argv

    def test_all_o_flags_use_dash_o(self):
        argv = build_ssh_cmd(
            instance_id="i-x",
            region="eu-west-1",
            key_path="/k",
            remote_cmd="id",
        )
        # ssh uses -o for options; count them
        o_flags = [argv[i + 1] for i, a in enumerate(argv) if a == "-o"]
        assert len(o_flags) == 4  # ProxyCommand, StrictHostKeyChecking, UserKnownHostsFile, ConnectTimeout


# ---------------------------------------------------------------------------
# parse_code_probe
# ---------------------------------------------------------------------------


class TestParseCodeProbe:
    def test_basic(self):
        code, secs = parse_code_probe("200 0.123")
        assert code == 200
        assert abs(secs - 0.123) < 1e-9

    def test_leading_trailing_whitespace(self):
        code, secs = parse_code_probe("  404 1.5  ")
        assert code == 404
        assert abs(secs - 1.5) < 1e-9

    def test_malformed_too_few_parts(self):
        with pytest.raises(ValueError):
            parse_code_probe("200")

    def test_malformed_empty(self):
        with pytest.raises(ValueError):
            parse_code_probe("")

    def test_malformed_non_numeric_code(self):
        with pytest.raises(ValueError):
            parse_code_probe("abc 0.5")


# ---------------------------------------------------------------------------
# parse_body_probe
# ---------------------------------------------------------------------------


class TestParseBodyProbe:
    def test_basic(self):
        body, code = parse_body_probe("hello\n200")
        assert body == "hello"
        assert code == 200

    def test_multiline_body(self):
        body, code = parse_body_probe("line one\nline two\n404")
        assert body == "line one\nline two"
        assert code == 404

    def test_split_on_last_newline(self):
        # Body itself contains a newline — code must be parsed from LAST newline.
        body, code = parse_body_probe("backend-a response\n\n200")
        assert "backend-a response" in body
        assert code == 200

    def test_no_newline_raises(self):
        with pytest.raises(ValueError):
            parse_body_probe("200")

    def test_malformed_code_raises(self):
        with pytest.raises(ValueError):
            parse_body_probe("body\nabc")

    def test_body_with_marker(self):
        stdout = "Welcome from backend-b\n200"
        body, code = parse_body_probe(stdout)
        assert "backend-b" in body
        assert code == 200


# ---------------------------------------------------------------------------
# routing_success
# ---------------------------------------------------------------------------


class TestRoutingSuccess:
    def test_all_200_pass(self):
        probes = [ProbeResult(iteration=i, code=200, seconds=0.1) for i in range(1, 6)]
        assert routing_success(probes) is True

    def test_one_non_200_fails(self):
        probes = [ProbeResult(iteration=i, code=200, seconds=0.1) for i in range(1, 5)]
        probes.append(ProbeResult(iteration=5, code=503, seconds=0.1))
        assert routing_success(probes) is False

    def test_error_in_probe_fails(self):
        probes = [ProbeResult(iteration=1, code=200, seconds=0.1, err="timeout")]
        assert routing_success(probes) is False

    def test_empty_probes_fails(self):
        assert routing_success([]) is False


# ---------------------------------------------------------------------------
# split_success
# ---------------------------------------------------------------------------


class TestSplitSuccess:
    def test_both_markers_seen(self):
        probes = [
            BodyProbeResult(iteration=1, code=200, body="response from backend-a"),
            BodyProbeResult(iteration=2, code=200, body="response from backend-b"),
        ]
        assert split_success(probes) is True

    def test_only_a_seen(self):
        probes = [BodyProbeResult(iteration=1, code=200, body="backend-a only")]
        assert split_success(probes) is False

    def test_only_b_seen(self):
        probes = [BodyProbeResult(iteration=1, code=200, body="backend-b only")]
        assert split_success(probes) is False

    def test_neither_seen(self):
        probes = [BodyProbeResult(iteration=1, code=200, body="unknown")]
        assert split_success(probes) is False

    def test_empty(self):
        assert split_success([]) is False


# ---------------------------------------------------------------------------
# multivip_success
# ---------------------------------------------------------------------------


class TestMultivipSuccess:
    def test_both_vips_correct(self):
        a = [BodyProbeResult(iteration=1, code=200, body="multivip-backend-a")]
        b = [BodyProbeResult(iteration=1, code=200, body="multivip-backend-b")]
        assert multivip_success(a, b) is True

    def test_vip_a_wrong_marker(self):
        a = [BodyProbeResult(iteration=1, code=200, body="multivip-backend-b")]
        b = [BodyProbeResult(iteration=1, code=200, body="multivip-backend-b")]
        assert multivip_success(a, b) is False

    def test_vip_b_wrong_marker(self):
        a = [BodyProbeResult(iteration=1, code=200, body="multivip-backend-a")]
        b = [BodyProbeResult(iteration=1, code=200, body="multivip-backend-a")]
        assert multivip_success(a, b) is False

    def test_empty_probes(self):
        assert multivip_success([], []) is False


# ---------------------------------------------------------------------------
# run_traffic_pattern — routing
# ---------------------------------------------------------------------------


class TestRunTrafficPatternRouting:
    def test_all_200_ok(self):
        # 5 probes all returning "200 0.100"
        runner = FakeTunnelRunner(["200 0.100"] * 5)
        pattern = TRAFFIC_PATTERNS["http-routing-e2e"]
        result = run_traffic_pattern(
            pattern, runner,
            vip="10.0.1.100", src_ip="10.0.2.50",
            iterations=5, timeout=10,
        )
        assert result.ok is True
        assert result.pattern_name == "http-routing-e2e"
        assert len(result.probes) == 5

    def test_one_503_fails(self):
        responses = ["200 0.1"] * 4 + ["503 0.2"]
        runner = FakeTunnelRunner(responses)
        pattern = TRAFFIC_PATTERNS["http-routing-e2e"]
        result = run_traffic_pattern(
            pattern, runner,
            vip="10.0.1.100", src_ip="10.0.2.50",
            iterations=5, timeout=10,
        )
        assert result.ok is False

    def test_runner_error_fails(self):
        class ErrorRunner:
            def run_probe(self, _cmd: str) -> str:
                raise RuntimeError("ssh failed")

        pattern = TRAFFIC_PATTERNS["http-routing-e2e"]
        result = run_traffic_pattern(
            pattern, ErrorRunner(),
            vip="10.0.1.100", src_ip="10.0.2.50",
            iterations=1, timeout=10,
        )
        assert result.ok is False
        assert result.probes[0]["err"] != ""

    def test_host_header_in_curl_cmd(self):
        """routing pattern uses Host: awsbnkctl.local — verify via cmd string captured."""
        captured: list[str] = []

        class CapturingRunner:
            def run_probe(self, remote_cmd: str) -> str:
                captured.append(remote_cmd)
                return "200 0.1"

        pattern = TRAFFIC_PATTERNS["http-routing-e2e"]
        # min_iterations for routing is 5; pass 5 so actual == requested.
        run_traffic_pattern(
            pattern, CapturingRunner(),
            vip="10.0.1.100", src_ip="10.0.2.50",
            iterations=5, timeout=10,
        )
        assert len(captured) == 5
        assert all("awsbnkctl.local" in c for c in captured)
        assert all("--interface 10.0.2.50" in c for c in captured)

    def test_no_octet_pin_for_routing(self):
        """http-routing-e2e has no octet pin — VIP passed through unchanged."""
        captured: list[str] = []

        class CapturingRunner:
            def run_probe(self, remote_cmd: str) -> str:
                captured.append(remote_cmd)
                return "200 0.1"

        pattern = TRAFFIC_PATTERNS["http-routing-e2e"]
        assert pattern.vip_octet is None
        run_traffic_pattern(
            pattern, CapturingRunner(),
            vip="10.0.1.200", src_ip="10.0.2.50",
            iterations=1, timeout=10,
        )
        assert "10.0.1.200" in captured[0]


# ---------------------------------------------------------------------------
# run_traffic_pattern — split
# ---------------------------------------------------------------------------


class TestRunTrafficPatternSplit:
    def test_both_backends_seen_ok(self):
        # 10 probes alternating backend-a / backend-b
        responses = []
        for i in range(10):
            marker = "backend-a" if i % 2 == 0 else "backend-b"
            responses.append(f"response from {marker}\n200")
        runner = FakeTunnelRunner(responses)
        pattern = TRAFFIC_PATTERNS["http-traffic-split"]
        result = run_traffic_pattern(
            pattern, runner,
            vip="10.0.1.100", src_ip="10.0.2.50",
            iterations=10, timeout=10,
        )
        assert result.ok is True

    def test_only_a_seen_fails(self):
        responses = ["response from backend-a\n200"] * 10
        runner = FakeTunnelRunner(responses)
        pattern = TRAFFIC_PATTERNS["http-traffic-split"]
        result = run_traffic_pattern(
            pattern, runner,
            vip="10.0.1.100", src_ip="10.0.2.50",
            iterations=10, timeout=10,
        )
        assert result.ok is False

    def test_min_iterations_enforced(self):
        """split requires min 10 even if caller asks for fewer."""
        responses = []
        for i in range(10):
            marker = "backend-a" if i % 2 == 0 else "backend-b"
            responses.append(f"{marker}\n200")
        runner = FakeTunnelRunner(responses)
        pattern = TRAFFIC_PATTERNS["http-traffic-split"]
        result = run_traffic_pattern(
            pattern, runner,
            vip="10.0.1.100", src_ip="10.0.2.50",
            iterations=3,   # below min; should be lifted to 10
            timeout=10,
        )
        assert len(result.probes) == 10

    def test_octet_101_pin(self):
        """split pins VIP last octet to 101."""
        captured: list[str] = []

        class CapturingRunner:
            def run_probe(self, remote_cmd: str) -> str:
                captured.append(remote_cmd)
                return "backend-a\n200"

        pattern = TRAFFIC_PATTERNS["http-traffic-split"]
        assert pattern.vip_octet == 101
        run_traffic_pattern(
            pattern, CapturingRunner(),
            vip="10.0.1.5", src_ip="10.0.2.50",
            iterations=10, timeout=10,
        )
        assert all("10.0.1.101" in c for c in captured)


# ---------------------------------------------------------------------------
# run_traffic_pattern — multi-vip
# ---------------------------------------------------------------------------


class TestRunTrafficPatternMultivip:
    def test_both_vips_ok(self):
        # VIP A probes return multivip-backend-a; VIP B return multivip-backend-b
        responses = (
            ["multivip-backend-a\n200"] * 5
            + ["multivip-backend-b\n200"] * 5
        )
        runner = FakeTunnelRunner(responses)
        pattern = TRAFFIC_PATTERNS["multi-vip"]
        result = run_traffic_pattern(
            pattern, runner,
            vip="10.0.1.100", src_ip="10.0.2.50",
            iterations=5, timeout=10,
        )
        assert result.ok is True

    def test_vip_a_wrong_marker_fails(self):
        responses = (
            ["multivip-backend-b\n200"] * 5   # wrong marker for VIP A
            + ["multivip-backend-b\n200"] * 5
        )
        runner = FakeTunnelRunner(responses)
        pattern = TRAFFIC_PATTERNS["multi-vip"]
        result = run_traffic_pattern(
            pattern, runner,
            vip="10.0.1.100", src_ip="10.0.2.50",
            iterations=5, timeout=10,
        )
        assert result.ok is False

    def test_octet_pins(self):
        """multi-vip must pin VIP A to .106 and VIP B to .107."""
        captured: list[str] = []

        class CapturingRunner:
            def run_probe(self, remote_cmd: str) -> str:
                captured.append(remote_cmd)
                return "multivip-backend-a\n200"

        pattern = TRAFFIC_PATTERNS["multi-vip"]
        assert pattern.vip_octet == 106
        assert pattern.vip_b_octet == 107
        run_traffic_pattern(
            pattern, CapturingRunner(),
            vip="10.0.1.100", src_ip="10.0.2.50",
            iterations=5, timeout=10,
        )
        # First 5 calls target .106, next 5 target .107
        assert all("10.0.1.106" in c for c in captured[:5])
        assert all("10.0.1.107" in c for c in captured[5:])

    def test_host_headers(self):
        """multi-vip uses multivip-a.local for VIP A and multivip-b.local for VIP B."""
        captured: list[str] = []

        class CapturingRunner:
            def run_probe(self, remote_cmd: str) -> str:
                captured.append(remote_cmd)
                return "multivip-backend-a\n200"

        pattern = TRAFFIC_PATTERNS["multi-vip"]
        # min_iterations is 5; use 5 so actual == requested (5 per VIP = 10 total).
        run_traffic_pattern(
            pattern, CapturingRunner(),
            vip="10.0.1.100", src_ip="10.0.2.50",
            iterations=5, timeout=10,
        )
        assert len(captured) == 10
        assert all("multivip-a.local" in c for c in captured[:5])
        assert all("multivip-b.local" in c for c in captured[5:])


# ---------------------------------------------------------------------------
# TrafficConfig — env fallbacks
# ---------------------------------------------------------------------------


class TestTrafficConfigEnv:
    def test_reads_jumphost_instance_id_from_env(self):
        with patch.dict(os.environ, {"JUMPHOST_INSTANCE_ID": "i-env123"}, clear=False):
            cfg = TrafficConfig(enabled=True, vip="10.0.0.1")
            assert cfg.jumphost_instance_id == "i-env123"

    def test_reads_source_ip_from_env(self):
        with patch.dict(os.environ, {"JUMPHOST_BNK_EXT_ENI_IP": "10.0.2.99"}, clear=False):
            cfg = TrafficConfig(enabled=True, vip="10.0.0.1")
            assert cfg.source_interface_ip == "10.0.2.99"

    def test_explicit_config_takes_precedence(self):
        with patch.dict(os.environ, {"JUMPHOST_INSTANCE_ID": "i-env123"}, clear=False):
            cfg = TrafficConfig(enabled=True, jumphost_instance_id="i-explicit", vip="10.0.0.1")
            assert cfg.jumphost_instance_id == "i-explicit"


# ---------------------------------------------------------------------------
# step_cloud_traffic gating
# ---------------------------------------------------------------------------


class TestStepCloudTrafficGating:
    """step_cloud_traffic must skip (green) in each of the following conditions."""

    def _run_no_creds(self, ctx: Context) -> str:
        env = {k: v for k, v in os.environ.items()
               if k not in ("AWS_ACCESS_KEY_ID", "AWS_SESSION_TOKEN")}
        with patch.dict(os.environ, env, clear=True):
            return step_cloud_traffic(ctx).status

    def test_skips_no_cloud_section(self):
        ctx = _make_ctx_no_cloud()
        with patch.dict(os.environ, _AWS_ENV, clear=False):
            result = step_cloud_traffic(ctx)
        assert result.status == "skipped"

    def test_skips_no_aws_creds(self):
        traffic = TrafficConfig(
            enabled=True,
            jumphost_instance_id="i-0abc",
            source_interface_ip="10.0.2.50",
            vip="10.0.1.100",
        )
        ctx = _make_ctx(traffic=traffic)
        assert self._run_no_creds(ctx) == "skipped"

    def test_skips_no_aws_cli(self):
        traffic = TrafficConfig(
            enabled=True,
            jumphost_instance_id="i-0abc",
            source_interface_ip="10.0.2.50",
            vip="10.0.1.100",
        )
        ctx = _make_ctx(traffic=traffic)
        with patch.dict(os.environ, _AWS_ENV, clear=False):
            with patch("scripts.e2e.cloud_steps.shutil.which", return_value=None):
                result = step_cloud_traffic(ctx)
        assert result.status == "skipped"

    def test_skips_no_ssh(self):
        traffic = TrafficConfig(
            enabled=True,
            jumphost_instance_id="i-0abc",
            source_interface_ip="10.0.2.50",
            vip="10.0.1.100",
        )
        ctx = _make_ctx(traffic=traffic)

        def which_no_ssh(cmd: str) -> str | None:
            return "/usr/bin/aws" if cmd == "aws" else None

        with patch.dict(os.environ, _AWS_ENV, clear=False):
            with patch("scripts.e2e.cloud_steps.shutil.which", side_effect=which_no_ssh):
                result = step_cloud_traffic(ctx)
        assert result.status == "skipped"

    def test_skips_no_traffic_section(self):
        ctx = _make_ctx(traffic=None)
        with patch.dict(os.environ, _AWS_ENV, clear=False):
            with patch("scripts.e2e.cloud_steps.shutil.which", return_value="/usr/bin/aws"):
                result = step_cloud_traffic(ctx)
        assert result.status == "skipped"

    def test_skips_traffic_disabled(self):
        traffic = TrafficConfig(
            enabled=False,
            jumphost_instance_id="i-0abc",
            source_interface_ip="10.0.2.50",
            vip="10.0.1.100",
        )
        ctx = _make_ctx(traffic=traffic)
        with patch.dict(os.environ, _AWS_ENV, clear=False):
            with patch("scripts.e2e.cloud_steps.shutil.which", return_value="/usr/bin/aws"):
                result = step_cloud_traffic(ctx)
        assert result.status == "skipped"

    def test_skips_no_instance_id(self):
        env = {k: v for k, v in os.environ.items() if k != "JUMPHOST_INSTANCE_ID"}
        traffic = TrafficConfig(
            enabled=True,
            jumphost_instance_id=None,
            source_interface_ip="10.0.2.50",
            vip="10.0.1.100",
        )
        ctx = _make_ctx(traffic=traffic)
        with patch.dict(os.environ, {**env, **_AWS_ENV}, clear=True):
            with patch("scripts.e2e.cloud_steps.shutil.which", return_value="/usr/bin/aws"):
                result = step_cloud_traffic(ctx)
        assert result.status == "skipped"

    def test_skips_no_source_ip(self):
        env = {k: v for k, v in os.environ.items() if k != "JUMPHOST_BNK_EXT_ENI_IP"}
        traffic = TrafficConfig(
            enabled=True,
            jumphost_instance_id="i-0abc",
            source_interface_ip=None,
            vip="10.0.1.100",
        )
        ctx = _make_ctx(traffic=traffic)
        with patch.dict(os.environ, {**env, **_AWS_ENV}, clear=True):
            with patch("scripts.e2e.cloud_steps.shutil.which", return_value="/usr/bin/aws"):
                result = step_cloud_traffic(ctx)
        assert result.status == "skipped"

    def test_proceeds_with_fake_runner_ok(self):
        """When all gates pass and the fake runner returns 200s, step is ok."""
        traffic = TrafficConfig(
            enabled=True,
            patterns=["http-routing-e2e"],
            jumphost_instance_id="i-0abc",
            source_interface_ip="10.0.2.50",
            vip="10.0.1.100",
            region="us-west-2",
            iterations=5,
            timeout_seconds=10,
        )
        ctx = _make_ctx(traffic=traffic)

        fake = FakeTunnelRunner(["200 0.1"] * 5)

        def runner_factory(_pattern_name: str) -> FakeTunnelRunner:
            return fake

        with patch.dict(os.environ, _AWS_ENV, clear=False):
            with patch("scripts.e2e.cloud_steps.shutil.which", return_value="/usr/bin/aws"):
                result = step_cloud_traffic(ctx, _runner_factory=runner_factory)

        assert result.status == "ok", f"expected ok, got {result.status}: {result.summary}"

    def test_proceeds_with_fake_runner_fail(self):
        """When the runner returns 503s the step status is 'failed'."""
        traffic = TrafficConfig(
            enabled=True,
            patterns=["http-routing-e2e"],
            jumphost_instance_id="i-0abc",
            source_interface_ip="10.0.2.50",
            vip="10.0.1.100",
            region="us-west-2",
            iterations=5,
            timeout_seconds=10,
        )
        ctx = _make_ctx(traffic=traffic)

        fake = FakeTunnelRunner(["503 0.1"] * 5)

        def runner_factory(_pattern_name: str) -> FakeTunnelRunner:
            return fake

        with patch.dict(os.environ, _AWS_ENV, clear=False):
            with patch("scripts.e2e.cloud_steps.shutil.which", return_value="/usr/bin/aws"):
                result = step_cloud_traffic(ctx, _runner_factory=runner_factory)

        assert result.status == "failed"

    def test_no_client_calls_when_skipped(self):
        ctx = _make_ctx_no_cloud()
        with patch.dict(os.environ, _AWS_ENV, clear=False):
            step_cloud_traffic(ctx)
        ctx.client.list_blueprint_releases.assert_not_called()


# ---------------------------------------------------------------------------
# CLOUD_PHASE_STEPS registry includes cloud_traffic
# ---------------------------------------------------------------------------


class TestCloudPhaseStepsRegistryTraffic:
    def test_cloud_traffic_in_registry(self):
        from scripts.e2e.cloud_steps import CLOUD_PHASE_STEPS
        names = [name for name, _ in CLOUD_PHASE_STEPS]
        assert "cloud_traffic" in names

    def test_cloud_traffic_between_license_and_destroy(self):
        from scripts.e2e.cloud_steps import CLOUD_PHASE_STEPS
        names = [name for name, _ in CLOUD_PHASE_STEPS]
        idx_license = names.index("cloud_wait_license_active")
        idx_traffic = names.index("cloud_traffic")
        idx_destroy = names.index("cloud_destroy_all")
        assert idx_license < idx_traffic < idx_destroy

    def test_cloud_traffic_dry_run_listing(self, capsys):
        import tempfile

        import yaml

        from scripts.e2e.__main__ import main

        config_data = {"bnk_forge_url": "https://localhost"}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config_data, f)
            config_path = f.name

        rc = main([config_path, "--steps", "cloud", "--dry-run"])
        captured = capsys.readouterr()

        assert rc == 0
        assert "cloud_traffic" in captured.out
