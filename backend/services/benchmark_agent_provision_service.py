"""Benchmark agent host provisioning service.

SSH-provisions a Forge-managed benchmark agent host by:
1. Waiting for SSH to be available.
2. Installing Python 3.10+, build-essential (needed for aiperf's crick C-extension),
   and aiperf + dependencies in a venv at /opt/forge/venv.
3. Uploading scripts/forge_agent.py → /opt/forge/forge_agent.py.
4. Minting a long-lived per-agent JWT token.
5. Writing /etc/forge/agent.env (EnvironmentFile, mode 600) with FORGE_URL,
   AGENT_NAME, AGENT_TOKEN, AGENT_ADVERTISE_IP.
6. Installing and starting a systemd unit forge-agent.service (falling back to
   nohup when systemctl is absent).

After the agent boots it self-registers via POST /api/benchmarks/agents (upsert by
name → same row) and connects via WebSocket — provision_status flips to "connected"
via the existing WS lifecycle.
"""

import logging
from pathlib import Path

from sqlalchemy.orm import Session

from core.config import settings
from core.errors import BadRequestError, NotFoundError
from models.benchmark import BenchmarkAgent
from services.bare_metal.ssh_session import SSHSession

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Path to the agent bootstrap script relative to the repo root / backend
# --------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.parent  # worktree root
_AGENT_SCRIPT_CANDIDATES = [
    _REPO_ROOT / "scripts" / "forge_agent.py",
    Path("/app/scripts/forge_agent.py"),  # container path
]


def _find_agent_script() -> str:
    """Return the path to forge_agent.py, raising if not found."""
    for candidate in _AGENT_SCRIPT_CANDIDATES:
        if candidate.exists():
            return str(candidate)
    # Last resort: raise with paths tried so the operator knows what to fix.
    tried = ", ".join(str(p) for p in _AGENT_SCRIPT_CANDIDATES)
    raise FileNotFoundError(f"forge_agent.py not found. Tried: {tried}")


# --------------------------------------------------------------------------
# OS detection helper
# --------------------------------------------------------------------------

def _is_debian_family(readiness: dict | None) -> bool:
    """Return True if readiness data indicates a Debian/Ubuntu host."""
    if not readiness:
        return True  # Assume debian-family (most common for agent hosts)
    os_type = (readiness.get("os") or {}).get("os_type", "").lower()
    return os_type in ("ubuntu", "debian", "linuxmint", "pop", "raspbian")


# --------------------------------------------------------------------------
# Provision steps
# --------------------------------------------------------------------------

def _step(agent: BenchmarkAgent, db: Session, message: str) -> None:
    """Stream a status message to the BenchmarkAgent row."""
    logger.info("Provision [host=%d]: %s", agent.id, message)
    agent.provision_message = message
    db.commit()


def _install_deps(ssh: SSHSession, agent: BenchmarkAgent, db: Session, has_sudo: bool) -> None:
    """Install build tools, create venv, pip install aiperf."""
    sudo = "sudo " if has_sudo else ""

    _step(agent, db, "Checking Python version…")
    py_version_result = ssh.execute("python3 --version 2>&1", timeout=15)
    py_ok = py_version_result.exit_code == 0 and "Python 3." in py_version_result.stdout
    if not py_ok:
        raise RuntimeError(
            "python3 not found on host. Install Python 3.10+ and re-run provision."
        )

    # Ensure build-essential (needed for aiperf's crick C-extension)
    _step(agent, db, "Installing build dependencies (build-essential)…")
    build_cmd = (
        f"{sudo}apt-get install -y build-essential python3-dev python3-venv 2>&1"
    )
    for line in ssh.execute_streaming(build_cmd, timeout=300):
        if isinstance(line, str):
            logger.debug("install-deps: %s", line.rstrip())

    # Create /opt/forge directory
    _step(agent, db, "Creating /opt/forge directory…")
    for line in ssh.execute_streaming(f"{sudo}mkdir -p /opt/forge /etc/forge && {sudo}chmod 755 /opt/forge", timeout=30):
        if isinstance(line, str):
            logger.debug("mkdir: %s", line.rstrip())

    # Create venv
    _step(agent, db, "Creating Python venv at /opt/forge/venv…")
    for line in ssh.execute_streaming(f"{sudo}python3 -m venv /opt/forge/venv 2>&1", timeout=120):
        if isinstance(line, str):
            logger.debug("venv: %s", line.rstrip())

    # Install aiperf + dependencies
    _step(agent, db, "Installing aiperf, websockets, requests (may take several minutes)…")
    pip_cmd = f"{sudo}/opt/forge/venv/bin/pip install --upgrade pip aiperf websockets requests 2>&1"
    for line in ssh.execute_streaming(pip_cmd, timeout=600):
        if isinstance(line, str):
            logger.debug("pip: %s", line.rstrip())

    # Verify aiperf installed
    verify_result = ssh.execute("/opt/forge/venv/bin/python -c 'import aiperf; print(\"ok\")' 2>&1", timeout=30)
    if verify_result.exit_code != 0 or "ok" not in verify_result.stdout:
        raise RuntimeError(
            f"aiperf import verification failed: {verify_result.stdout[:300]} {verify_result.stderr[:200]}"
        )
    _step(agent, db, "aiperf installed successfully.")


def _upload_agent_script(ssh: SSHSession, agent: BenchmarkAgent, db: Session, has_sudo: bool) -> None:
    """Upload forge_agent.py to /opt/forge/forge_agent.py."""
    sudo = "sudo " if has_sudo else ""

    _step(agent, db, "Uploading forge_agent.py…")
    script_path = _find_agent_script()
    script_content = Path(script_path).read_text()

    # Upload to a temp path then move (SFTP may not honour sudo for /opt paths)
    tmp_path = "/tmp/forge_agent.py"
    ssh.upload_content(script_content, tmp_path, mode="644")
    for line in ssh.execute_streaming(f"{sudo}mv {tmp_path} /opt/forge/forge_agent.py && {sudo}chmod 755 /opt/forge/forge_agent.py 2>&1", timeout=30):
        if isinstance(line, str):
            logger.debug("upload: %s", line.rstrip())
    _step(agent, db, "forge_agent.py uploaded.")


def _mint_agent_token(agent: BenchmarkAgent) -> str:
    """Mint a 365-day JWT for this agent."""
    from datetime import timedelta

    from services.auth_service import create_access_token

    return create_access_token(
        {"agent_id": agent.id, "role": "agent", "sub": agent.name},
        expires_delta=timedelta(days=365),
    )


def _write_env_and_service(
    ssh: SSHSession,
    agent: BenchmarkAgent,
    db: Session,
    has_sudo: bool,
    token: str,
    has_systemctl: bool,
) -> None:
    """Write EnvironmentFile + systemd unit and start the agent."""
    sudo = "sudo " if has_sudo else ""

    forge_url = settings.FORGE_EXTERNAL_URL.rstrip("/")
    advertise_ip = agent.host_ip or ""

    _step(agent, db, "Writing /etc/forge/agent.env…")

    env_content = (
        f"FORGE_URL={forge_url}\n"
        f"AGENT_NAME={agent.name}\n"
        f"AGENT_TOKEN={token}\n"
        f"AGENT_ADVERTISE_IP={advertise_ip}\n"
    )

    # Write to tmp then sudo move — upload_content with mode 600 is fine when user owns /tmp
    tmp_env = "/tmp/forge_agent.env"
    ssh.upload_content(env_content, tmp_env, mode="600")
    for line in ssh.execute_streaming(
        f"{sudo}mkdir -p /etc/forge && {sudo}mv {tmp_env} /etc/forge/agent.env && {sudo}chmod 600 /etc/forge/agent.env 2>&1",
        timeout=30,
    ):
        if isinstance(line, str):
            logger.debug("env-file: %s", line.rstrip())

    if has_systemctl:
        _step(agent, db, "Installing forge-agent.service…")
        service_unit = (
            "[Unit]\n"
            "Description=Forge Benchmark Agent\n"
            "After=network-online.target\n"
            "Wants=network-online.target\n\n"
            "[Service]\n"
            "Type=simple\n"
            "EnvironmentFile=/etc/forge/agent.env\n"
            "ExecStart=/opt/forge/venv/bin/python /opt/forge/forge_agent.py\n"
            "Restart=always\n"
            "RestartSec=10\n\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n"
        )
        tmp_svc = "/tmp/forge-agent.service"
        ssh.upload_content(service_unit, tmp_svc, mode="644")
        for line in ssh.execute_streaming(
            f"{sudo}mv {tmp_svc} /etc/systemd/system/forge-agent.service && "
            f"{sudo}systemctl daemon-reload && "
            f"{sudo}systemctl enable --now forge-agent 2>&1",
            timeout=60,
        ):
            if isinstance(line, str):
                logger.debug("systemd: %s", line.rstrip())
        _step(agent, db, "forge-agent.service enabled and started via systemd.")
    else:
        # Fallback: nohup launch in the background
        _step(agent, db, "systemctl not available — starting agent via nohup…")
        nohup_cmd = (
            "nohup env $(cat /etc/forge/agent.env | xargs) "
            "/opt/forge/venv/bin/python /opt/forge/forge_agent.py "
            "> /var/log/forge-agent.log 2>&1 &"
        )
        r = ssh.execute(nohup_cmd, timeout=30)
        if r.exit_code != 0:
            logger.warning("nohup launch stderr: %s", r.stderr[:300])
        _step(agent, db, "Agent started via nohup (no systemd). Log: /var/log/forge-agent.log.")


def _has_sudo(ssh: SSHSession) -> bool:
    """Return True when the session credential can sudo (password or NOPASSWD)."""
    # Try a harmless sudo command
    r = ssh.execute("sudo -n true 2>/dev/null && echo ok || echo no", timeout=10)
    if r.exit_code == 0 and "ok" in r.stdout:
        return True
    # If we have a password, sudo -S will read it from stdin
    return bool(ssh.password)


def _has_systemctl(readiness: dict | None) -> bool:
    """Return True if the readiness scan says systemctl is present."""
    if not readiness:
        return True  # Optimistic default
    return bool((readiness.get("tools") or {}).get("systemctl", False))


# --------------------------------------------------------------------------
# Main provision entry point
# --------------------------------------------------------------------------

class BenchmarkAgentProvisionService:
    """Orchestrate SSH provisioning of a Forge-managed benchmark agent host."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def provision(self, host_id: int) -> dict:
        """Run all provision steps and persist status to the BenchmarkAgent row.

        Returns a dict with {"host_id": ..., "status": "provisioned"|"failed"}.
        """
        from services.bare_metal.ssh_provision import build_ssh_session_from_credential

        agent = self.db.query(BenchmarkAgent).filter(
            BenchmarkAgent.id == host_id,
            BenchmarkAgent.managed.is_(True),
        ).first()
        if not agent:
            raise NotFoundError("agent host", host_id)

        if not settings.FORGE_EXTERNAL_URL:
            raise BadRequestError(
                "Set FORGE_EXTERNAL_URL (the address remote agents reach Forge at) before provisioning.",
                code="FORGE_EXTERNAL_URL_UNSET",
            )

        if not agent.host_ip:
            raise BadRequestError("agent host has no host_ip configured", code="NO_HOST_IP")

        # Mark provisioning in progress
        agent.provision_status = "provisioning"
        agent.provision_message = "Starting SSH provisioning…"
        self.db.commit()

        ssh = None
        try:
            ssh = build_ssh_session_from_credential(
                self.db,
                agent.ssh_credential_id,
                agent.host_ip,
                agent.ssh_port,
                agent.jumphost_chain,
            )

            _step(agent, self.db, "Waiting for SSH…")
            if not ssh.wait_for_ssh(timeout=120, interval=10):
                raise RuntimeError("SSH did not become available within 120 seconds")

            has_sudo = _has_sudo(ssh)
            has_systemctl = _has_systemctl(agent.readiness)

            _install_deps(ssh, agent, self.db, has_sudo)
            _upload_agent_script(ssh, agent, self.db, has_sudo)

            token = _mint_agent_token(agent)
            _write_env_and_service(ssh, agent, self.db, has_sudo, token, has_systemctl)

            agent.provision_status = "provisioned"
            agent.provision_message = (
                "Provisioned — agent is starting. It will connect via WebSocket shortly."
            )
            self.db.commit()
            logger.info("Provision complete for agent host %d", host_id)
            return {"host_id": host_id, "status": "provisioned"}

        except Exception as exc:
            logger.exception("Provision failed for agent host %d: %s", host_id, exc)
            try:
                self.db.rollback()
                agent = self.db.query(BenchmarkAgent).filter_by(id=host_id).first()
                if agent:
                    agent.provision_status = "failed"
                    agent.provision_message = f"Provision failed: {exc!s}"[:1000]
                    self.db.commit()
            except Exception:
                logger.exception("Could not persist failure state for host %d", host_id)
            raise
        finally:
            # Deterministically remove decrypted key tempfiles on the success path
            if ssh is not None:
                ssh.close()
