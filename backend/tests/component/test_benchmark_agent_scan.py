"""Component tests — benchmark agent host scan (Slice 2).

Mocks SSHSession so no real SSH is required.  Asserts:
- readiness shape and verdict for each scenario
- scan route dispatches task and returns 202
- scan route rejects non-managed / nonexistent hosts
"""
from unittest.mock import MagicMock, patch

import pytest

from services.benchmark_agent_scan_service import (
    VERDICT_NEEDS_PROVISION,
    VERDICT_READY,
    VERDICT_SSH_UNREACHABLE,
    VERDICT_UNREACHABLE_TO_TARGETS,
    BenchmarkAgentScanService,
    build_readiness,
)
from tests.factories import (
    BenchmarkTargetFactory,
    KubernetesClusterFactory,
    ProjectFactory,
    _next_seq,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session(
    *,
    reachable: bool = True,
    os_stdout: str = 'ID=ubuntu\nVERSION_ID="22.04"\nPRETTY_NAME="Ubuntu 22.04"\n',
    nproc_stdout: str = "4",
    meminfo_stdout: str = "MemTotal:       65536000 kB\n",
    python3_present: bool = True,
    pip_present: bool = True,
    aiperf_present: bool = True,
    systemctl_present: bool = True,
) -> MagicMock:
    """Build a mock SSHSession that returns deterministic probe outputs."""
    from services.bare_metal.ssh_session import SSHResult

    def _ok(stdout: str = "") -> SSHResult:
        return SSHResult(exit_code=0, stdout=stdout, stderr="", duration_seconds=0.1)

    def _fail(msg: str = "") -> SSHResult:
        return SSHResult(exit_code=1, stdout="", stderr=msg, duration_seconds=0.1)

    session = MagicMock()
    session.is_reachable.return_value = reachable

    tool_map = {
        "python3": python3_present,
        "pip": pip_present,
        "aiperf": aiperf_present,
        "systemctl": systemctl_present,
    }

    def _execute(cmd: str, timeout: int = 30) -> SSHResult:  # noqa: ARG001
        if "os-release" in cmd:
            return _ok(os_stdout)
        if "nproc" in cmd:
            return _ok(nproc_stdout)
        if "MemTotal" in cmd:
            return _ok(meminfo_stdout)
        if "python --version" in cmd or "python3 --version" in cmd:
            return _ok("Python 3.11.0")
        if "uname -m" in cmd:
            return _ok("x86_64")
        # curl availability check must come BEFORE the generic curl branch
        if "command -v curl" in cmd:
            return _ok("ok")
        # Tool presence check
        for tool, present in tool_map.items():
            if f"command -v {tool}" in cmd or (tool == "pip" and "command -v pip" in cmd):
                return _ok("ok") if present else _ok("missing")
        # curl-based reachability (starts with "curl -sS ...")
        if cmd.strip().startswith("curl -sS"):
            return _ok("200") if reachable else _ok("000")
        # TCP fallback reachability
        if "bash -c '</dev/tcp/" in cmd:
            return _ok("ok") if reachable else _fail("TCP refused")
        return _ok("ok")

    session.execute.side_effect = _execute
    session.host = "10.0.1.100"
    session.username = "root"
    session.port = 22
    return session


def _make_target(target_id: int = 1, name: str = "target-1", base_url: str = "http://llm:8000") -> MagicMock:
    t = MagicMock()
    t.id = target_id
    t.name = name
    t.llm_base_url = base_url
    return t


# ---------------------------------------------------------------------------
# Unit tests — build_readiness verdict logic (no DB)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestBuildReadinessVerdicts:
    def test_ssh_unreachable_verdict(self):
        session = _make_session(reachable=False)
        r = build_readiness(session, [])
        assert r["ssh_reachable"] is False
        assert r["verdict"] == VERDICT_SSH_UNREACHABLE
        assert r["tools"]["aiperf"] is False

    def test_ready_verdict_when_tools_present_and_targets_reachable(self):
        target = _make_target()
        session = _make_session(aiperf_present=True, python3_present=True)
        r = build_readiness(session, [target])
        assert r["ssh_reachable"] is True
        assert r["verdict"] == VERDICT_READY

    def test_ready_verdict_when_no_targets(self):
        session = _make_session(aiperf_present=True, python3_present=True)
        r = build_readiness(session, [])
        assert r["verdict"] == VERDICT_READY

    def test_needs_provision_when_aiperf_missing(self):
        target = _make_target()
        session = _make_session(aiperf_present=False)
        r = build_readiness(session, [target])
        assert r["ssh_reachable"] is True
        assert r["verdict"] == VERDICT_NEEDS_PROVISION
        assert r["tools"]["aiperf"] is False

    def test_unreachable_to_targets_verdict(self):
        """SSH ok, tools present, but all targets are unreachable."""
        session = _make_session(aiperf_present=True, python3_present=True)
        target = _make_target()

        # Override execute to make all reachability probes (curl + TCP fallback) fail
        from services.bare_metal.ssh_session import SSHResult

        original_side_effect = session.execute.side_effect

        def _execute_unreachable(cmd: str, timeout: int = 30) -> SSHResult:  # noqa: ARG001
            if cmd.strip().startswith("curl -sS"):
                return SSHResult(exit_code=0, stdout="000", stderr="", duration_seconds=0.1)
            if "bash -c '</dev/tcp/" in cmd:
                return SSHResult(exit_code=1, stdout="fail", stderr="", duration_seconds=0.1)
            return original_side_effect(cmd, timeout=timeout)

        session.execute.side_effect = _execute_unreachable
        r = build_readiness(session, [target])
        assert r["verdict"] == VERDICT_UNREACHABLE_TO_TARGETS
        assert r["reachable_targets"][0]["ok"] is False

    def test_readiness_shape_complete(self):
        """All expected keys are present in the readiness dict."""
        session = _make_session()
        r = build_readiness(session, [])
        assert "ssh_reachable" in r
        assert "os" in r
        assert "cpu" in r
        assert "mem_gb" in r
        assert "tools" in r
        assert "reachable_targets" in r
        assert "verdict" in r

    def test_os_and_hw_info_parsed(self):
        session = _make_session(nproc_stdout="8", meminfo_stdout="MemTotal: 33554432 kB\n")
        r = build_readiness(session, [])
        assert r["cpu"] == 8
        assert r["mem_gb"] == pytest.approx(32.0, abs=1.0)
        assert r["os"].get("os_type") == "ubuntu"
        assert r["os"].get("architecture") == "x86_64"

    def test_multiple_targets_per_results(self):
        targets = [_make_target(1, "t1"), _make_target(2, "t2")]
        session = _make_session()
        r = build_readiness(session, targets)
        assert len(r["reachable_targets"]) == 2
        ids = {t["target_id"] for t in r["reachable_targets"]}
        assert ids == {1, 2}


# ---------------------------------------------------------------------------
# Component tests — scan route (HTTP level)
# ---------------------------------------------------------------------------

def _ssh_cred(db):
    from models.ssh_credential import SSHCredential
    n = _next_seq("ssh_cred_scan")
    c = SSHCredential(name=f"scan-cred-{n}", host=f"10.0.{n}.1", port=22, username="root", auth_type="key")
    db.add(c)
    db.flush()
    return c


@pytest.mark.component
class TestAgentHostScanRoute:
    """POST /api/benchmarks/agent-hosts/{id}/scan."""

    def test_scan_returns_202_and_task_id(self, client, admin_headers, sample_user, db):
        project = ProjectFactory(db, user_id=sample_user.id)
        cred = _ssh_cred(db)
        db.commit()

        create_resp = client.post(
            "/api/benchmarks/agent-hosts",
            json={
                "name": "scan-host-01",
                "project_id": project.id,
                "host_ip": "10.0.1.100",
                "ssh_credential_id": cred.id,
                "ssh_port": 22,
            },
            headers=admin_headers,
        )
        assert create_resp.status_code == 201, create_resp.text
        host_id = create_resp.json()["id"]

        with patch("tasks.benchmark_agent_tasks.scan_benchmark_agent_host.delay") as mock_delay:
            mock_delay.return_value = MagicMock(id="fake-task-id-123")
            resp = client.post(
                f"/api/benchmarks/agent-hosts/{host_id}/scan",
                json={},
                headers=admin_headers,
            )

        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["host_id"] == host_id
        assert "celery_task_id" in body
        assert "message" in body

    def test_scan_nonexistent_host_returns_404(self, client, admin_headers, sample_user):
        resp = client.post("/api/benchmarks/agent-hosts/99999/scan", json={}, headers=admin_headers)
        assert resp.status_code == 404

    def test_viewer_cannot_scan(self, client, viewer_headers, admin_headers, sample_user, sample_viewer_user, db):
        project = ProjectFactory(db, user_id=sample_user.id)
        cred = _ssh_cred(db)
        db.commit()

        create_resp = client.post(
            "/api/benchmarks/agent-hosts",
            json={
                "name": "scan-host-no-viewer",
                "project_id": project.id,
                "host_ip": "10.0.1.200",
                "ssh_credential_id": cred.id,
            },
            headers=admin_headers,
        )
        assert create_resp.status_code == 201
        host_id = create_resp.json()["id"]

        resp = client.post(
            f"/api/benchmarks/agent-hosts/{host_id}/scan",
            json={},
            headers=viewer_headers,
        )
        assert resp.status_code == 403

    def test_scan_with_explicit_target_ids(self, client, admin_headers, sample_user, db):
        project = ProjectFactory(db, user_id=sample_user.id)
        cred = _ssh_cred(db)
        db.commit()

        create_resp = client.post(
            "/api/benchmarks/agent-hosts",
            json={
                "name": "scan-host-targets",
                "project_id": project.id,
                "host_ip": "10.0.1.150",
                "ssh_credential_id": cred.id,
            },
            headers=admin_headers,
        )
        assert create_resp.status_code == 201
        host_id = create_resp.json()["id"]

        with patch("tasks.benchmark_agent_tasks.scan_benchmark_agent_host.delay") as mock_delay:
            mock_delay.return_value = MagicMock(id="fake-task-id-456")
            resp = client.post(
                f"/api/benchmarks/agent-hosts/{host_id}/scan",
                json={"target_ids": [1, 2, 3]},
                headers=admin_headers,
            )

        assert resp.status_code == 202, resp.text
        # Verify target_ids were passed through to the task
        mock_delay.assert_called_once_with(host_id, [1, 2, 3])


# ---------------------------------------------------------------------------
# Component tests — _resolve_targets project scoping (regression: cross-project leak)
# ---------------------------------------------------------------------------

def _managed_agent(db, *, project_id: int, host_ip: str = "10.0.9.1"):
    from models.benchmark import BenchmarkAgent
    n = _next_seq("scan_agent")
    agent = BenchmarkAgent(
        name=f"managed-scan-agent-{n}",
        managed=True,
        project_id=project_id,
        host_ip=host_ip,
    )
    db.add(agent)
    db.flush()
    return agent


@pytest.mark.component
class TestResolveTargetsProjectScoping:
    """`_resolve_targets` must not leak other projects' targets into readiness."""

    def test_default_scope_excludes_other_projects(self, sample_user, db):
        proj_a = ProjectFactory(db, user_id=sample_user.id)
        proj_b = ProjectFactory(db, user_id=sample_user.id)

        cluster_a = KubernetesClusterFactory(db, project=proj_a)
        cluster_b = KubernetesClusterFactory(db, project=proj_b)

        mine = BenchmarkTargetFactory(db, cluster=cluster_a, name="mine-target")
        theirs = BenchmarkTargetFactory(db, cluster=cluster_b, name="other-target")
        db.commit()

        agent = _managed_agent(db, project_id=proj_a.id)
        svc = BenchmarkAgentScanService(db)
        resolved = svc._resolve_targets(agent, None)

        ids = {t.id for t in resolved}
        assert mine.id in ids
        assert theirs.id not in ids, "must not leak another project's target"

    def test_default_scope_excludes_inactive_targets(self, sample_user, db):
        proj = ProjectFactory(db, user_id=sample_user.id)
        cluster = KubernetesClusterFactory(db, project=proj)
        active = BenchmarkTargetFactory(db, cluster=cluster, name="active-t", status="active")
        BenchmarkTargetFactory(db, cluster=cluster, name="inactive-t", status="inactive")
        db.commit()

        agent = _managed_agent(db, project_id=proj.id)
        svc = BenchmarkAgentScanService(db)
        resolved = svc._resolve_targets(agent, None)
        assert {t.id for t in resolved} == {active.id}

    def test_explicit_target_ids_bypass_scoping(self, sample_user, db):
        proj = ProjectFactory(db, user_id=sample_user.id)
        cluster = KubernetesClusterFactory(db, project=proj)
        t1 = BenchmarkTargetFactory(db, cluster=cluster, name="explicit-t")
        db.commit()

        agent = _managed_agent(db, project_id=proj.id)
        svc = BenchmarkAgentScanService(db)
        resolved = svc._resolve_targets(agent, [t1.id])
        assert {t.id for t in resolved} == {t1.id}
