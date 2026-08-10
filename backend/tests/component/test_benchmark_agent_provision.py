"""Component tests — benchmark agent host provision (Slice 3).

Mocks SSHSession and auth_service so no real SSH or DB is required.
Asserts:
- FORGE_EXTERNAL_URL unset → BadRequestError before SSH
- install commands contain expected package names
- env-file content includes FORGE_URL, AGENT_NAME, AGENT_TOKEN, AGENT_ADVERTISE_IP
- systemd path writes the unit file and calls daemon-reload + enable
- nohup fallback path taken when systemctl=False in readiness
- provision route returns 202 and dispatches Celery task
- provision route rejects non-managed / nonexistent hosts
"""
from unittest.mock import MagicMock, call, patch

import pytest

from models.benchmark import BenchmarkAgent
from tests.factories import ProjectFactory, _next_seq

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_ssh(
    *,
    reachable: bool = True,
    has_sudo_env: bool = True,
) -> MagicMock:
    """Build a mock SSHSession."""
    from services.bare_metal.ssh_session import SSHResult

    def _ok(stdout: str = "") -> SSHResult:
        return SSHResult(exit_code=0, stdout=stdout, stderr="", duration_seconds=0.1)

    def _fail() -> SSHResult:
        return SSHResult(exit_code=1, stdout="", stderr="fail", duration_seconds=0.1)

    session = MagicMock()
    session.password = "secret" if has_sudo_env else None
    session.private_key_path = None
    session.private_key_content = None
    session.wait_for_ssh.return_value = reachable

    # sudo -n true → ok (has sudo)
    sudo_result = _ok("ok") if has_sudo_env else _fail()

    def _execute(cmd: str, timeout: int = 300) -> "SSHResult":  # noqa: ARG001
        if "sudo -n true" in cmd:
            return sudo_result
        if "python3 --version" in cmd:
            return _ok("Python 3.11.2\n")
        if "forge_agent.py" in cmd and "import aiperf" in cmd:
            return _ok("ok\n")
        if "/opt/forge/venv/bin/python -c" in cmd:
            return _ok("ok\n")
        return _ok()

    session.execute.side_effect = _execute

    # execute_streaming: yield nothing (no output lines) then return ok result
    session.execute_streaming.return_value = iter([])

    return session


def _make_agent(db, project_id: int | None = None) -> BenchmarkAgent:
    n = _next_seq("prov_host")
    from models.ssh_credential import SSHCredential
    cred = SSHCredential(name=f"tc-{n}", host=f"10.0.0.{n}", port=22, username="forge", auth_type="key")
    db.add(cred)
    db.flush()
    agent = BenchmarkAgent(
        name=f"prov-host-{n}",
        managed=True,
        project_id=project_id,
        host_ip=f"10.0.1.{n}",
        ssh_credential_id=cred.id,
        ssh_port=22,
        provision_status="unprovisioned",
        readiness={"tools": {"systemctl": True, "python3": True, "pip": True, "aiperf": False}},
    )
    db.add(agent)
    db.flush()
    return agent


# ---------------------------------------------------------------------------
# Unit-style tests for the service (no HTTP, real in-memory DB)
# ---------------------------------------------------------------------------

@pytest.mark.component
class TestBenchmarkAgentProvisionService:
    """Direct service tests with mocked SSHSession."""

    def test_fails_when_forge_external_url_unset(self, db):
        """FORGE_EXTERNAL_URL not set → BadRequestError before any SSH."""
        from core.errors import BadRequestError
        from services.benchmark_agent_provision_service import BenchmarkAgentProvisionService

        agent = _make_agent(db)
        db.commit()

        svc = BenchmarkAgentProvisionService(db)

        with patch("core.config.settings.FORGE_EXTERNAL_URL", ""):
            with pytest.raises(BadRequestError, match="FORGE_EXTERNAL_URL"):
                svc.provision(agent.id)

    def test_install_commands_include_build_essential(self, db):
        """apt-get install command includes build-essential (needed for crick)."""
        from services.benchmark_agent_provision_service import BenchmarkAgentProvisionService

        agent = _make_agent(db)
        db.commit()

        mock_ssh = _make_ssh()
        seen_commands: list[str] = []

        def _capture_streaming(cmd: str, timeout: int = 300):  # noqa: ARG001
            seen_commands.append(cmd)
            return iter([])

        mock_ssh.execute_streaming.side_effect = _capture_streaming

        with (
            patch("core.config.settings.FORGE_EXTERNAL_URL", "https://forge.example.com"),
            patch("services.bare_metal.ssh_provision.build_ssh_session_from_credential", return_value=mock_ssh),
            patch("services.benchmark_agent_provision_service._find_agent_script", return_value="/tmp/forge_agent.py"),
            patch("pathlib.Path.read_text", return_value="# forge_agent stub"),
            patch("services.auth_service.create_access_token", return_value="tok-xyz"),
        ):
            svc = BenchmarkAgentProvisionService(db)
            svc.provision(agent.id)

        apt_cmds = [c for c in seen_commands if "apt-get" in c]
        assert apt_cmds, "No apt-get command seen"
        assert any("build-essential" in c for c in apt_cmds), f"build-essential missing from: {apt_cmds}"

    def test_env_file_content(self, db):
        """EnvironmentFile written to /etc/forge/agent.env with all 4 vars."""
        from services.benchmark_agent_provision_service import BenchmarkAgentProvisionService

        agent = _make_agent(db)
        db.commit()

        mock_ssh = _make_ssh()
        uploaded: dict[str, str] = {}

        def _upload(content: str, path: str, *, mode: str | None = None):  # noqa: ARG001
            uploaded[path] = content

        mock_ssh.upload_content.side_effect = _upload

        with (
            patch("core.config.settings.FORGE_EXTERNAL_URL", "https://forge.example.com"),
            patch("services.bare_metal.ssh_provision.build_ssh_session_from_credential", return_value=mock_ssh),
            patch("services.benchmark_agent_provision_service._find_agent_script", return_value="/tmp/forge_agent.py"),
            patch("pathlib.Path.read_text", return_value="# forge_agent stub"),
            patch("services.auth_service.create_access_token", return_value="tok-xyz"),
        ):
            svc = BenchmarkAgentProvisionService(db)
            svc.provision(agent.id)

        env_content = uploaded.get("/tmp/forge_agent.env", "")
        assert "FORGE_URL=https://forge.example.com" in env_content, env_content
        assert f"AGENT_NAME={agent.name}" in env_content, env_content
        assert "AGENT_TOKEN=tok-xyz" in env_content, env_content
        assert f"AGENT_ADVERTISE_IP={agent.host_ip}" in env_content, env_content

    def test_systemd_path_writes_unit_file(self, db):
        """When systemctl=True, a systemd unit is written."""
        from services.benchmark_agent_provision_service import BenchmarkAgentProvisionService

        agent = _make_agent(db)  # readiness has systemctl=True
        db.commit()

        mock_ssh = _make_ssh()
        uploaded: dict[str, str] = {}

        def _upload(content: str, path: str, *, mode: str | None = None):  # noqa: ARG001
            uploaded[path] = content

        mock_ssh.upload_content.side_effect = _upload

        with (
            patch("core.config.settings.FORGE_EXTERNAL_URL", "https://forge.example.com"),
            patch("services.bare_metal.ssh_provision.build_ssh_session_from_credential", return_value=mock_ssh),
            patch("services.benchmark_agent_provision_service._find_agent_script", return_value="/tmp/forge_agent.py"),
            patch("pathlib.Path.read_text", return_value="# forge_agent stub"),
            patch("services.auth_service.create_access_token", return_value="tok-xyz"),
        ):
            svc = BenchmarkAgentProvisionService(db)
            svc.provision(agent.id)

        unit_content = uploaded.get("/tmp/forge-agent.service", "")
        assert "[Unit]" in unit_content, unit_content
        assert "EnvironmentFile=/etc/forge/agent.env" in unit_content, unit_content
        assert "ExecStart=/opt/forge/venv/bin/python /opt/forge/forge_agent.py" in unit_content, unit_content
        assert "Restart=always" in unit_content, unit_content

        # systemctl daemon-reload + enable --now should appear in streaming calls
        seen = [args[0] for args, _ in mock_ssh.execute_streaming.call_args_list]
        assert any("daemon-reload" in c and "enable --now" in c for c in seen), f"systemctl commands not seen: {seen}"

    def test_nohup_fallback_when_no_systemctl(self, db):
        """When systemctl=False in readiness, nohup path is used instead."""
        from models.ssh_credential import SSHCredential
        from services.benchmark_agent_provision_service import BenchmarkAgentProvisionService
        n = _next_seq("nosystemctl")
        cred = SSHCredential(name=f"tc-{n}", host=f"10.1.1.{n}", port=22, username="forge", auth_type="key")
        db.add(cred)
        db.flush()
        agent = BenchmarkAgent(
            name=f"no-systemctl-{n}",
            managed=True,
            host_ip=f"10.1.2.{n}",
            ssh_credential_id=cred.id,
            provision_status="unprovisioned",
            readiness={"tools": {"systemctl": False, "python3": True, "pip": True, "aiperf": False}},
        )
        db.add(agent)
        db.flush()
        db.commit()

        mock_ssh = _make_ssh()
        nohup_called = False

        def _execute(cmd: str, timeout: int = 300) -> object:  # noqa: ARG001
            nonlocal nohup_called
            from services.bare_metal.ssh_session import SSHResult
            if "nohup" in cmd:
                nohup_called = True
            if "sudo -n true" in cmd:
                return SSHResult(exit_code=0, stdout="ok", stderr="", duration_seconds=0.1)
            if "python3 --version" in cmd:
                return SSHResult(exit_code=0, stdout="Python 3.11.2\n", stderr="", duration_seconds=0.1)
            if "/opt/forge/venv/bin/python -c" in cmd:
                return SSHResult(exit_code=0, stdout="ok\n", stderr="", duration_seconds=0.1)
            return SSHResult(exit_code=0, stdout="", stderr="", duration_seconds=0.1)

        mock_ssh.execute.side_effect = _execute

        with (
            patch("core.config.settings.FORGE_EXTERNAL_URL", "https://forge.example.com"),
            patch("services.bare_metal.ssh_provision.build_ssh_session_from_credential", return_value=mock_ssh),
            patch("services.benchmark_agent_provision_service._find_agent_script", return_value="/tmp/forge_agent.py"),
            patch("pathlib.Path.read_text", return_value="# forge_agent stub"),
            patch("services.auth_service.create_access_token", return_value="tok-xyz"),
        ):
            svc = BenchmarkAgentProvisionService(db)
            svc.provision(agent.id)

        assert nohup_called, "Expected nohup to be called when systemctl=False"

    def test_provision_marks_status_provisioned(self, db):
        """Successful provision → provision_status=provisioned on the row."""
        from services.benchmark_agent_provision_service import BenchmarkAgentProvisionService

        agent = _make_agent(db)
        db.commit()

        mock_ssh = _make_ssh()

        with (
            patch("core.config.settings.FORGE_EXTERNAL_URL", "https://forge.example.com"),
            patch("services.bare_metal.ssh_provision.build_ssh_session_from_credential", return_value=mock_ssh),
            patch("services.benchmark_agent_provision_service._find_agent_script", return_value="/tmp/forge_agent.py"),
            patch("pathlib.Path.read_text", return_value="# forge_agent stub"),
            patch("services.auth_service.create_access_token", return_value="tok-xyz"),
        ):
            svc = BenchmarkAgentProvisionService(db)
            result = svc.provision(agent.id)

        db.refresh(agent)
        assert result["status"] == "provisioned"
        assert agent.provision_status == "provisioned"

    def test_ssh_failure_marks_status_failed(self, db):
        """SSH unreachable → provision_status=failed."""
        from services.benchmark_agent_provision_service import BenchmarkAgentProvisionService

        agent = _make_agent(db)
        db.commit()

        mock_ssh = _make_ssh(reachable=False)

        with (
            patch("core.config.settings.FORGE_EXTERNAL_URL", "https://forge.example.com"),
            patch("services.bare_metal.ssh_provision.build_ssh_session_from_credential", return_value=mock_ssh),
        ):
            svc = BenchmarkAgentProvisionService(db)
            with pytest.raises(RuntimeError, match="SSH did not become available"):
                svc.provision(agent.id)

        db.refresh(agent)
        assert agent.provision_status == "failed"


# ---------------------------------------------------------------------------
# Route-level tests
# ---------------------------------------------------------------------------

@pytest.mark.component
class TestProvisionRoute:
    """POST /api/benchmarks/agent-hosts/{id}/provision route."""

    def test_provision_returns_202(self, client, admin_headers, sample_user, db):
        project = ProjectFactory(db, user_id=sample_user.id)
        from models.ssh_credential import SSHCredential
        n = _next_seq("prov_route")
        cred = SSHCredential(name=f"tc-{n}", host=f"10.0.0.{n}", port=22, username="forge", auth_type="key")
        db.add(cred)
        db.flush()
        agent = BenchmarkAgent(
            name=f"prov-route-host-{n}",
            managed=True,
            project_id=project.id,
            host_ip="10.0.1.50",
            ssh_credential_id=cred.id,
            provision_status="unprovisioned",
        )
        db.add(agent)
        db.commit()

        with patch("tasks.benchmark_agent_tasks.provision_benchmark_agent_host") as mock_task:
            mock_task.delay.return_value = MagicMock(id="celery-task-abc")
            resp = client.post(
                f"/api/benchmarks/agent-hosts/{agent.id}/provision",
                headers=admin_headers,
            )

        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["host_id"] == agent.id
        assert body["celery_task_id"] == "celery-task-abc"
        mock_task.delay.assert_called_once_with(agent.id)

    def test_provision_nonexistent_returns_404(self, client, admin_headers, sample_user, db):
        resp = client.post("/api/benchmarks/agent-hosts/99999/provision", headers=admin_headers)
        assert resp.status_code == 404

    def test_provision_unmanaged_returns_404(self, client, admin_headers, sample_user, db):
        """Non-managed (built-in) agents are not provisionable."""
        agent = BenchmarkAgent(name=f"builtin-{_next_seq('prov_builtin')}", managed=False)
        db.add(agent)
        db.commit()

        resp = client.post(f"/api/benchmarks/agent-hosts/{agent.id}/provision", headers=admin_headers)
        assert resp.status_code == 404

    def test_viewer_cannot_provision(self, client, viewer_headers, sample_viewer_user, db):
        from models.ssh_credential import SSHCredential
        n = _next_seq("prov_viewer")
        cred = SSHCredential(name=f"tc-{n}", host=f"10.0.0.{n}", port=22, username="forge", auth_type="key")
        db.add(cred)
        db.flush()
        agent = BenchmarkAgent(
            name=f"prov-viewer-host-{n}",
            managed=True,
            host_ip="10.0.1.51",
            ssh_credential_id=cred.id,
            provision_status="unprovisioned",
        )
        db.add(agent)
        db.commit()

        resp = client.post(f"/api/benchmarks/agent-hosts/{agent.id}/provision", headers=viewer_headers)
        assert resp.status_code == 403
