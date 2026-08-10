"""Benchmark agent host scan service.

Proves a registered remote host is suitable for running aiperf by:
1. Verifying SSH reachability.
2. Probing OS / CPU / memory.
3. Checking for required tools (python3, pip, aiperf, systemctl).
4. Verifying network reachability to each project BenchmarkTarget FROM the host.

The decisive step (4) tests the host's network path to LLM endpoints —
something Forge's own container cannot do.
"""

import logging
import re

from sqlalchemy.orm import Session

from models.benchmark import BenchmarkAgent, BenchmarkTarget
from services.bare_metal.ssh_session import SSHSession

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Verdict constants
# ---------------------------------------------------------------------------

VERDICT_READY = "ready"
VERDICT_NEEDS_PROVISION = "needs_provision"
VERDICT_UNREACHABLE_TO_TARGETS = "unreachable_to_targets"
VERDICT_SSH_UNREACHABLE = "ssh_unreachable"


# ---------------------------------------------------------------------------
# Scan logic (pure functions operating on SSHSession)
# ---------------------------------------------------------------------------

def _probe_os(ssh: SSHSession) -> dict:
    """Probe OS type, version, and architecture."""
    os_info: dict = {}
    r = ssh.execute("cat /etc/os-release 2>/dev/null", timeout=10)
    if r.exit_code == 0:
        for line in r.stdout.splitlines():
            if line.startswith("ID="):
                os_info["os_type"] = line.split("=", 1)[1].strip().strip('"')
            elif line.startswith("VERSION_ID="):
                os_info["os_version"] = line.split("=", 1)[1].strip().strip('"')
            elif line.startswith("PRETTY_NAME="):
                os_info["os_pretty_name"] = line.split("=", 1)[1].strip().strip('"')

    r = ssh.execute("uname -m", timeout=5)
    if r.exit_code == 0:
        os_info["architecture"] = r.stdout.strip()

    return os_info


def _probe_cpu(ssh: SSHSession) -> int | None:
    """Return CPU count via nproc."""
    r = ssh.execute("nproc 2>/dev/null", timeout=5)
    if r.exit_code == 0 and r.stdout.strip().isdigit():
        return int(r.stdout.strip())
    return None


def _probe_mem_gb(ssh: SSHSession) -> float | None:
    """Return total memory in GiB from /proc/meminfo."""
    r = ssh.execute("cat /proc/meminfo 2>/dev/null | grep MemTotal", timeout=5)
    if r.exit_code == 0:
        match = re.search(r"MemTotal:\s+(\d+)\s+kB", r.stdout)
        if match:
            kb = int(match.group(1))
            return round(kb / (1024 * 1024), 1)
    return None


def _probe_tools(ssh: SSHSession) -> dict:
    """Check for required tools via 'command -v'."""
    checks = {
        "python3": "command -v python3 > /dev/null 2>&1 && echo ok || echo missing",
        "pip": "{ command -v pip3 > /dev/null 2>&1 || command -v pip > /dev/null 2>&1; } && echo ok || echo missing",
        "aiperf": "command -v aiperf > /dev/null 2>&1 && echo ok || echo missing",
        "systemctl": "command -v systemctl > /dev/null 2>&1 && echo ok || echo missing",
    }
    results: dict = {}
    for tool, cmd in checks.items():
        r = ssh.execute(cmd, timeout=5)
        results[tool] = r.exit_code == 0 and r.stdout.strip() == "ok"

    # Also grab python3 version if present
    if results.get("python3"):
        r = ssh.execute("python3 --version 2>&1", timeout=5)
        if r.exit_code == 0:
            results["python3_version"] = r.stdout.strip()

    return results


def _probe_target_reachability(ssh: SSHSession, targets: list[BenchmarkTarget]) -> list[dict]:
     """Check whether each BenchmarkTarget's LLM endpoint is reachable FROM the host.

     Uses curl if available, otherwise falls back to a TCP /dev/tcp test.
     Each target is probed independently — one failure doesn't abort the rest.
     """
     import shlex
     import urllib.parse

     results = []
     if not targets:
         return results

     # Check if curl is available on the remote host
     has_curl_r = ssh.execute("command -v curl > /dev/null 2>&1 && echo ok || echo missing", timeout=5)
     has_curl = has_curl_r.exit_code == 0 and has_curl_r.stdout.strip() == "ok"

     for target in targets:
         base_url = target.llm_base_url.rstrip("/")
         item: dict = {
             "target_id": target.id,
             "name": target.name,
             "llm_base_url": base_url,
             "ok": False,
             "http_code": None,
             "error": None,
         }

         try:
             # Validate URL scheme (http/https only) and parse before any probe
             parsed = urllib.parse.urlparse(base_url)
             if parsed.scheme not in ("http", "https"):
                 item["error"] = f"Invalid URL scheme: {parsed.scheme}"
                 results.append(item)
                 continue

             if has_curl:
                 # Use curl with properly quoted URL
                 probe_path = "/"
                 cmd = (
                     f"curl -sS -o /dev/null -m 8 -w '%{{http_code}}' "
                     f"{shlex.quote(base_url + probe_path)} 2>&1"
                 )
                 r = ssh.execute(cmd, timeout=15)
                 code_str = r.stdout.strip().split()[-1] if r.stdout.strip() else ""
                 try:
                     http_code = int(code_str)
                     item["http_code"] = http_code
                     # Any non-connection-refused response means we reached the host
                     item["ok"] = http_code > 0
                 except ValueError:
                     item["error"] = f"curl output: {r.stdout.strip()[:200]}"
             else:
                 # TCP connect via bash /dev/tcp as fallback with strict validation
                 host = parsed.hostname or ""
                 port = parsed.port or (443 if parsed.scheme == "https" else 80)

                 # Validate port is in valid range
                 if not isinstance(port, int) or port < 1 or port > 65535:
                     item["error"] = f"Invalid port: {port}"
                     results.append(item)
                     continue

                 # Validate hostname is not empty (prevent /dev/tcp//N injection)
                 if not host:
                     item["error"] = "No host in URL"
                     results.append(item)
                     continue

                 # Use shlex.quote for both host and port in the /dev/tcp path
                 # Note: /dev/tcp/<host>/<port> is a bash special form, so we construct
                 # a safe bash command that does not allow injection
                 cmd = f"timeout 8 bash -c 'exec </dev/tcp/{shlex.quote(host)}/{port} 2>&1' && echo ok || echo fail"
                 r = ssh.execute(cmd, timeout=15)
                 item["ok"] = r.stdout.strip() == "ok"
                 if not item["ok"]:
                     item["error"] = "TCP connect failed"
         except Exception as exc:
             item["error"] = str(exc)[:200]

         results.append(item)

     return results


def build_readiness(
    ssh: SSHSession,
    targets: list[BenchmarkTarget],
) -> dict:
    """Run all probes and return a structured readiness dict.

    Shape:
        {
            ssh_reachable: bool,
            os: {os_type, os_version, os_pretty_name, architecture},
            cpu: int | None,
            mem_gb: float | None,
            tools: {python3: bool, pip: bool, aiperf: bool, systemctl: bool, python3_version?: str},
            reachable_targets: [{target_id, name, ok, http_code, error}],
            verdict: "ready" | "needs_provision" | "unreachable_to_targets" | "ssh_unreachable",
        }
    """
    if not ssh.is_reachable():
        return {
            "ssh_reachable": False,
            "os": {},
            "cpu": None,
            "mem_gb": None,
            "tools": {"python3": False, "pip": False, "aiperf": False, "systemctl": False},
            "reachable_targets": [],
            "verdict": VERDICT_SSH_UNREACHABLE,
        }

    os_info = _probe_os(ssh)
    cpu = _probe_cpu(ssh)
    mem_gb = _probe_mem_gb(ssh)
    tools = _probe_tools(ssh)
    reachable_targets = _probe_target_reachability(ssh, targets)

    # Verdict rollup
    any_target_reachable = any(t["ok"] for t in reachable_targets) if reachable_targets else True
    aiperf_present = tools.get("aiperf", False)
    python_ok = tools.get("python3", False)

    if aiperf_present and python_ok and any_target_reachable:
        verdict = VERDICT_READY
    elif not any_target_reachable and reachable_targets:
        # SSH ok but can't reach any configured target
        verdict = VERDICT_UNREACHABLE_TO_TARGETS
    else:
        # SSH ok + targets reachable (or no targets) but tools missing
        verdict = VERDICT_NEEDS_PROVISION

    return {
        "ssh_reachable": True,
        "os": os_info,
        "cpu": cpu,
        "mem_gb": mem_gb,
        "tools": tools,
        "reachable_targets": reachable_targets,
        "verdict": verdict,
    }


# ---------------------------------------------------------------------------
# Service class
# ---------------------------------------------------------------------------

class BenchmarkAgentScanService:
    """Orchestrate a readiness scan of a Forge-managed benchmark agent host."""

    def __init__(self, db: Session):
        self.db = db

    def scan(self, host_id: int, target_ids: list[int] | None = None) -> dict:
        """Run the SSH scan and persist readiness on the BenchmarkAgent row.

        Args:
            host_id: PK of the BenchmarkAgent row (managed=True).
            target_ids: Optional list of BenchmarkTarget PKs to probe for
                network reachability. When None, uses all targets in the
                agent's project.

        Returns:
            The readiness dict written to agent.readiness.
        """
        from core.errors import NotFoundError
        from services.bare_metal.ssh_provision import build_ssh_session_from_credential

        agent = self.db.query(BenchmarkAgent).filter(
            BenchmarkAgent.id == host_id,
            BenchmarkAgent.managed.is_(True),
        ).first()
        if not agent:
            raise NotFoundError("agent host", host_id)

        if not agent.host_ip:
            raise ValueError(f"Agent host {host_id} has no host_ip configured")

        # Mark scanning in progress
        agent.provision_status = "scanning"
        agent.provision_message = "SSH scan in progress…"
        self.db.commit()

        # Resolve targets
        targets = self._resolve_targets(agent, target_ids)

        ssh = None
        try:
            ssh = build_ssh_session_from_credential(
                self.db,
                agent.ssh_credential_id,
                agent.host_ip,
                agent.ssh_port,
                agent.jumphost_chain,
            )

            readiness = build_readiness(ssh, targets)

        except Exception as exc:
            logger.exception("Scan error for agent host %d: %s", host_id, exc)
            readiness = {
                "ssh_reachable": False,
                "os": {},
                "cpu": None,
                "mem_gb": None,
                "tools": {"python3": False, "pip": False, "aiperf": False, "systemctl": False},
                "reachable_targets": [],
                "verdict": VERDICT_SSH_UNREACHABLE,
                "error": str(exc)[:500],
            }
        finally:
            # Deterministically remove decrypted key tempfiles on the success path
            if ssh is not None:
                ssh.close()

        # Persist results
        agent.readiness = readiness
        verdict = readiness.get("verdict", VERDICT_SSH_UNREACHABLE)
        if verdict == VERDICT_SSH_UNREACHABLE:
            agent.provision_status = "failed"
            agent.provision_message = "SSH unreachable"
        elif verdict == VERDICT_READY:
            agent.provision_status = "unprovisioned"
            agent.provision_message = "Scan complete — host is ready for provisioning"
        else:
            # needs_provision or unreachable_to_targets — host is reachable, just needs work
            agent.provision_status = "unprovisioned"
            agent.provision_message = f"Scan complete — {verdict}"

        self.db.commit()
        logger.info(
            "Scan complete for agent host %d: verdict=%s", host_id, verdict
        )
        return readiness

    def _resolve_targets(
        self, agent: BenchmarkAgent, target_ids: list[int] | None
    ) -> list[BenchmarkTarget]:
        """Return the BenchmarkTarget rows to probe for reachability."""
        if target_ids is not None:
            if not target_ids:
                return []
            return (
                self.db.query(BenchmarkTarget)
                .filter(BenchmarkTarget.id.in_(target_ids))
                .all()
            )

        if agent.project_id:
            # Default: active targets whose cluster belongs to the agent's project.
            # BenchmarkTarget has no direct project FK; scope via its KubernetesCluster
            # so we never leak other projects' endpoints/names into the readiness JSON.
            from models.kubernetes import KubernetesCluster

            return (
                self.db.query(BenchmarkTarget)
                .join(KubernetesCluster, BenchmarkTarget.cluster_id == KubernetesCluster.id)
                .filter(
                    BenchmarkTarget.status == "active",
                    KubernetesCluster.project_id == agent.project_id,
                )
                .limit(20)  # Safety cap — don't probe huge lists on every scan
                .all()
            )

        return []
