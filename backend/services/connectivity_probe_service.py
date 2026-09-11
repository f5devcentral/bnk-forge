"""
Cluster Connectivity Probe Service

Lightweight, fast connectivity checks for Kubernetes clusters.
Tests ICMP (ping), TCP port reachability, and K8s API accessibility
WITHOUT requiring a full kubeconfig load or SSH tunnel setup.

Designed to give users immediate feedback on whether their cluster
is reachable from the Forge server, with actionable diagnostic messages.
"""

import logging
import os
import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import urlparse

from models import KubernetesCluster
from models.enums import ConnectivityStatus
from services.base_service import BaseService

logger = logging.getLogger(__name__)

# Timeouts (seconds) — keep probes fast
ICMP_TIMEOUT = 3
TCP_TIMEOUT = 5
K8S_API_TIMEOUT = 8


def _parse_api_server(api_server: str) -> tuple[str | None, int]:
    """Extract host and port from api_server URL.

    Port resolution order:
      1. Explicit port in URL (e.g. ``https://host:6443``)
      2. Scheme default (``https`` → 443, ``http`` → 80)
      3. K8s default 6443 — only when no scheme either

    EKS public endpoints answer on 443 (standard https), not 6443. The
    legacy fallback always returned 6443 which made the probe hit the
    wrong port for any cluster whose URL omitted an explicit port.
    """
    if not api_server:
        return None, 6443
    try:
        parsed = urlparse(api_server)
        host = parsed.hostname
        if parsed.port:
            return host, parsed.port
        if parsed.scheme == "https":
            return host, 443
        if parsed.scheme == "http":
            return host, 80
        return host, 6443
    except Exception:
        return None, 6443


def _probe_icmp(host: str, timeout: int = ICMP_TIMEOUT) -> dict[str, Any]:
    """
    Check if a host is reachable at the network level.

    Tries multiple methods in order:
    1. subprocess `ping` (works on hosts with ping installed)
    2. Raw ICMP socket (needs root or CAP_NET_RAW)
    3. TCP connect to common ports 80/443 as a reachability heuristic

    Returns reachable status and latency when available.
    """
    # Method 1: subprocess ping (most reliable when available)
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", str(timeout), host],
            capture_output=True, text=True, timeout=timeout + 2,
        )
        if result.returncode == 0:
            latency_ms = None
            for line in result.stdout.splitlines():
                if "time=" in line:
                    try:
                        latency_ms = float(line.split("time=")[1].split()[0])
                    except (IndexError, ValueError):
                        pass
                    break
            return {"reachable": True, "latency_ms": latency_ms}
        return {"reachable": False, "latency_ms": None}
    except FileNotFoundError:
        pass  # ping not installed — try raw socket
    except (subprocess.TimeoutExpired, Exception) as e:
        logger.debug(f"ICMP subprocess probe to {host} failed: {e}")
        return {"reachable": False, "latency_ms": None}

    # Method 2: unprivileged ICMP via SOCK_DGRAM (Linux, no root needed)
    # Then fall back to raw socket (needs CAP_NET_RAW or root)
    for sock_type in (socket.SOCK_DGRAM, socket.SOCK_RAW):
        sock = None
        try:
            import struct
            sock = socket.socket(socket.AF_INET, sock_type, socket.IPPROTO_ICMP)
            sock.settimeout(timeout)
            icmp_id = os.getpid() & 0xFFFF
            header = struct.pack("!BBHHH", 8, 0, 0, icmp_id, 1)
            payload = b"\x00" * 32
            data = header + payload
            s = sum(struct.unpack(f"!{len(data) // 2}H", data))
            s = (s >> 16) + (s & 0xFFFF)
            s += s >> 16
            checksum = ~s & 0xFFFF
            header = struct.pack("!BBHHH", 8, 0, checksum, icmp_id, 1)

            start = time.monotonic()
            sock.sendto(header + payload, (host, 0))
            sock.recvfrom(1024)
            latency_ms = (time.monotonic() - start) * 1000
            sock.close()
            return {"reachable": True, "latency_ms": round(latency_ms, 1)}
        except PermissionError:
            if sock:
                sock.close()
            continue  # Try next socket type
        except Exception as e:
            logger.debug(f"ICMP socket ({sock_type}) probe to {host} failed: {e}")
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
            # Timeout means unreachable, not a socket type issue
            if isinstance(e, socket.timeout):
                return {"reachable": False, "latency_ms": None}
            continue  # Try next socket type

    # Method 3: TCP connect to common ports as reachability heuristic
    # If we can connect to ANY port, the host is at least routable
    for probe_port in (80, 443, 22):
        try:
            start = time.monotonic()
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            result_code = s.connect_ex((host, probe_port))
            latency_ms = (time.monotonic() - start) * 1000
            s.close()
            if result_code == 0:
                return {"reachable": True, "latency_ms": round(latency_ms, 1)}
            # Connection refused (port closed) still means host is reachable
            if result_code == 111:  # ECONNREFUSED
                return {"reachable": True, "latency_ms": round(latency_ms, 1)}
        except Exception:
            try:
                s.close()
            except Exception:
                pass
            continue

    return {"reachable": False, "latency_ms": None}


def _probe_tcp(host: str, port: int, timeout: int = TCP_TIMEOUT) -> dict[str, Any]:
    """
    Test TCP connectivity to host:port.
    Returns whether port is open and connection time.
    """
    try:
        start = time.monotonic()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        elapsed_ms = (time.monotonic() - start) * 1000
        sock.close()

        if result == 0:
            return {"open": True, "connect_ms": round(elapsed_ms, 1)}
        return {"open": False, "connect_ms": None}
    except (TimeoutError, socket.gaierror, OSError) as e:
        logger.debug(f"TCP probe to {host}:{port} failed: {e}")
        return {"open": False, "connect_ms": None}


def _probe_k8s_api(host: str, port: int, timeout: int = K8S_API_TIMEOUT) -> dict[str, Any]:
    """
    Try to hit the K8s /version endpoint (unauthenticated on most clusters).
    Uses raw HTTPS request to avoid needing kubeconfig.
    """
    try:
        import http.client
        import json
        import ssl

        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

        conn = http.client.HTTPSConnection(host, port, timeout=timeout, context=context)
        conn.request("GET", "/version")
        response = conn.getresponse()

        if response.status == 200:
            data = json.loads(response.read().decode("utf-8"))
            version = f"{data.get('major', '?')}.{data.get('minor', '?')}"
            return {"accessible": True, "version": version, "status_code": 200}
        # 401/403 means the API is there but needs auth — still "accessible"
        if response.status in (401, 403):
            return {"accessible": True, "version": None, "status_code": response.status}
        return {"accessible": False, "version": None, "status_code": response.status}
    except Exception as e:
        logger.debug(f"K8s API probe to {host}:{port} failed: {e}")
        return {"accessible": False, "version": None, "status_code": None}


def _build_diagnostic_message(
    host: str | None, port: int,
    icmp: dict, tcp: dict, k8s_api: dict,
    ssh_tunnel_enabled: bool,
) -> dict[str, Any]:
    """
    Build a human-readable diagnostic with status and actionable advice.

    Connectivity states (canonical — PLAT-REL-001):
    - connected:   TCP open + K8s API accessible
    - reachable:   TCP open but K8s API not responding (auth issue or wrong port)
    - partial:     ICMP works but TCP port filtered (firewall)
    - unreachable: Nothing works (no network path)
    - unknown:     No API server configured
    """
    if not host:
        return {
            "status": ConnectivityStatus.UNKNOWN,
            "message": "No API server URL configured for this cluster.",
            "suggestion": "Edit the cluster and provide a valid kubeconfig with an API server URL.",
        }

    if k8s_api.get("accessible"):
        version_info = f" (v{k8s_api['version']})" if k8s_api.get("version") else ""
        latency = f" Latency: {icmp['latency_ms']:.0f}ms." if icmp.get("latency_ms") else ""
        return {
            "status": ConnectivityStatus.CONNECTED,
            "message": f"Kubernetes API is accessible{version_info}.{latency}",
            "suggestion": None,
        }

    if tcp.get("open"):
        return {
            "status": ConnectivityStatus.REACHABLE,
            "message": f"Port {port} is open but Kubernetes API did not respond.",
            "suggestion": (
                f"The server at {host}:{port} accepts connections but the K8s API "
                f"is not responding. Verify the API server URL and port are correct."
            ),
        }

    if icmp.get("reachable"):
        latency_info = f" ({icmp['latency_ms']:.0f}ms)" if icmp.get("latency_ms") else ""
        if ssh_tunnel_enabled:
            return {
                "status": ConnectivityStatus.PARTIAL,
                "message": f"Host responds to ping{latency_info} but port {port} is blocked. SSH tunnel is enabled.",
                "suggestion": (
                    f"The host {host} is reachable via ICMP{latency_info} but TCP port {port} is filtered. "
                    f"Since SSH tunneling is enabled, ensure the SSH credential is configured and the "
                    f"tunnel can reach the K8s API on the remote side."
                ),
            }
        return {
            "status": ConnectivityStatus.PARTIAL,
            "message": f"Host responds to ping{latency_info} but port {port} is blocked.",
            "suggestion": (
                f"The host {host} is reachable via ICMP{latency_info} but TCP port {port} is filtered by a firewall. "
                f"Options: (1) Request firewall rule to allow TCP {port} from this server to {host}. "
                f"(2) Enable SSH tunneling with a jump host that can reach the cluster. "
                f"(3) If the K8s API listens on a different port, update the cluster configuration."
            ),
        }

    # Nothing works
    if ssh_tunnel_enabled:
        return {
            "status": ConnectivityStatus.UNREACHABLE,
            "message": f"Host {host} is completely unreachable. SSH tunnel is enabled.",
            "suggestion": (
                f"Cannot reach {host} via ICMP or TCP. Since SSH tunneling is enabled, "
                f"the connection will be routed through the SSH bastion. Ensure the SSH "
                f"credential is valid and the bastion host can reach {host}:{port}."
            ),
        }
    return {
        "status": ConnectivityStatus.UNREACHABLE,
        "message": f"Host {host} is completely unreachable from this server.",
        "suggestion": (
            f"Cannot reach {host} via ICMP or TCP port {port}. "
            f"Verify the API server URL is correct. If the cluster is on a different network, "
            f"enable SSH tunneling with a jump host that has access to {host}."
        ),
    }


class ConnectivityProbeService(BaseService):
    """Fast, lightweight connectivity probes for Kubernetes clusters."""

    def probe_cluster(self, cluster_id: int) -> dict[str, Any]:
        """
        Run a connectivity probe against a single cluster.
        Tests ICMP, TCP port, and K8s API version endpoint.

        Returns structured result with status, details, and actionable suggestions.
        """
        cluster = self._get_cluster(cluster_id)
        return self._probe_cluster_obj(cluster)

    def probe_all_clusters(self) -> dict[str, Any]:
        """
        Run connectivity probes against all clusters in parallel.
        Returns results keyed by cluster_id.
        """
        clusters = self.db.query(KubernetesCluster).all()
        if not clusters:
            return {"results": [], "summary": {"total": 0, "connected": 0, "reachable": 0, "partial": 0, "unreachable": 0, "unknown": 0}}

        results = []
        with ThreadPoolExecutor(max_workers=min(len(clusters), 10)) as pool:
            futures = {pool.submit(self._probe_cluster_obj, c): c for c in clusters}
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as e:
                    cluster = futures[future]
                    logger.error(f"Probe failed for cluster {cluster.name}: {e}")
                    results.append({
                        "cluster_id": cluster.id,
                        "cluster_name": cluster.name,
                        "api_server": cluster.api_server,
                        "status": ConnectivityStatus.UNKNOWN,
                        "message": f"Probe error: {e}",
                        "suggestion": "An unexpected error occurred during the connectivity probe.",
                        "icmp": {"reachable": False, "latency_ms": None},
                        "tcp": {"open": False, "connect_ms": None, "port": None},
                        "k8s_api": {"accessible": False, "version": None, "status_code": None},
                        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    })

        # Sort by cluster_id for stable output
        results.sort(key=lambda r: r["cluster_id"])

        # Summary counts — use canonical ConnectivityStatus values
        summary: dict[str, int] = {
            "total": len(results),
            "connected": 0,
            "reachable": 0,
            "partial": 0,
            "unreachable": 0,
            "unknown": 0,
        }
        for r in results:
            status = str(r.get("status", ConnectivityStatus.UNKNOWN))
            if status in summary:
                summary[status] += 1
            else:
                summary["unknown"] += 1

        return {"results": results, "summary": summary}

    def probe_project_clusters(self, project_id: int) -> dict[str, Any]:
        """
        Run connectivity probes against all clusters in a project in parallel.
        Returns the same shape as probe_all_clusters.
        """
        clusters = (
            self.db.query(KubernetesCluster)
            .filter(KubernetesCluster.project_id == project_id)
            .all()
        )
        if not clusters:
            return {"results": [], "summary": {"total": 0, "connected": 0, "reachable": 0, "partial": 0, "unreachable": 0, "unknown": 0}}

        results = []
        with ThreadPoolExecutor(max_workers=min(len(clusters), 10)) as pool:
            futures = {pool.submit(self._probe_cluster_obj, c): c for c in clusters}
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as e:
                    cluster = futures[future]
                    logger.error(f"Probe failed for cluster {cluster.name}: {e}")
                    results.append({
                        "cluster_id": cluster.id,
                        "cluster_name": cluster.name,
                        "api_server": cluster.api_server,
                        "status": ConnectivityStatus.UNKNOWN,
                        "message": f"Probe error: {e}",
                        "suggestion": "An unexpected error occurred during the connectivity probe.",
                        "icmp": {"reachable": False, "latency_ms": None},
                        "tcp": {"open": False, "connect_ms": None, "port": None},
                        "k8s_api": {"accessible": False, "version": None, "status_code": None},
                        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    })

        results.sort(key=lambda r: r["cluster_id"])

        summary: dict[str, int] = {
            "total": len(results),
            "connected": 0,
            "reachable": 0,
            "partial": 0,
            "unreachable": 0,
            "unknown": 0,
        }
        for r in results:
            status = str(r.get("status", ConnectivityStatus.UNKNOWN))
            if status in summary:
                summary[status] += 1
            else:
                summary["unknown"] += 1

        return {"results": results, "summary": summary}

    def _probe_cluster_obj(self, cluster: KubernetesCluster) -> dict[str, Any]:
        """Run the actual probe against a cluster object."""
        host, port = _parse_api_server(cluster.api_server)

        if not host:
            diag = _build_diagnostic_message(
                host, port, {"reachable": False}, {"open": False}, {"accessible": False},
                ssh_tunnel_enabled=cluster.ssh_tunnel_enabled,
            )
            return {
                "cluster_id": cluster.id,
                "cluster_name": cluster.name,
                "api_server": cluster.api_server,
                **diag,
                "icmp": {"reachable": False, "latency_ms": None},
                "tcp": {"open": False, "connect_ms": None, "port": None},
                "k8s_api": {"accessible": False, "version": None, "status_code": None},
                "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }

        # Run ICMP and TCP probes in parallel for speed
        with ThreadPoolExecutor(max_workers=2) as pool:
            icmp_future = pool.submit(_probe_icmp, host)
            tcp_future = pool.submit(_probe_tcp, host, port)
            icmp = icmp_future.result()
            tcp = tcp_future.result()

        # Only try K8s API if TCP port is open
        k8s_api: dict[str, Any] = {"accessible": False, "version": None, "status_code": None}
        if tcp.get("open"):
            k8s_api = _probe_k8s_api(host, port)

        diag = _build_diagnostic_message(
            host, port, icmp, tcp, k8s_api,
            ssh_tunnel_enabled=cluster.ssh_tunnel_enabled,
        )

        return {
            "cluster_id": cluster.id,
            "cluster_name": cluster.name,
            "api_server": cluster.api_server,
            **diag,
            "icmp": {**icmp},
            "tcp": {"open": tcp.get("open", False), "connect_ms": tcp.get("connect_ms"), "port": port},
            "k8s_api": {**k8s_api},
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
