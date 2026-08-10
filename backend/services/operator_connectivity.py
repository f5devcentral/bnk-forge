"""
Operator Connectivity Service — manages the 5 modes of operator-to-BNK-Forge communication.

Modes:
  1. direct_ws:    Operator opens outbound WebSocket to BNK-Forge's public address
  2. reverse_ssh:  BNK-Forge opens SSH to bastion, creates reverse tunnel so operator
                   can connect to a local port that tunnels back to BNK-Forge
  3. polling:      Operator polls HTTP endpoints for commands (no WebSocket needed)
  4. ngrok_tunnel: BNK-Forge exposes itself via ngrok/cloudflare, operator uses public URL
  5. in_cluster:   Both run in the same cluster — use K8s service networking

Each mode generates mode-specific:
  - Install commands (helm install / kubectl apply)
  - Environment variables for the operator
  - Status/health information
"""

import logging
import os
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONNECTIVITY_MODES = {
    "direct_ws": {
        "label": "Direct WebSocket",
        "description": "Operator connects outbound to BNK-Forge. Requires BNK-Forge to be reachable from the cluster.",
        "icon": "Wifi",
        "requires_public_access": True,
    },
    "reverse_ssh": {
        "label": "Reverse SSH Tunnel",
        "description": "Uses existing SSH access to create a reverse tunnel. Operator connects to a local port that tunnels back to BNK-Forge.",
        "icon": "Terminal",
        "requires_public_access": False,
    },
    "polling": {
        "label": "HTTP Polling",
        "description": "Operator polls BNK-Forge for commands over HTTP. Works through any proxy or firewall. Higher latency.",
        "icon": "RefreshCw",
        "requires_public_access": True,  # Still needs HTTP access, but more forgiving
    },
    "ngrok_tunnel": {
        "label": "ngrok / Cloudflare Tunnel",
        "description": "BNK-Forge exposes itself via ngrok or Cloudflare Tunnel. Instant public URL, great for demos.",
        "icon": "Globe",
        "requires_public_access": False,  # ngrok provides the public access
    },
    "in_cluster": {
        "label": "In-Cluster",
        "description": "BNK-Forge and operator run in the same Kubernetes cluster. Uses internal service networking.",
        "icon": "Server",
        "requires_public_access": False,
    },
}

# BNK-Forge defaults (proxy listens on 443 with host networking)
DEFAULT_WS_PORT = 443
DEFAULT_API_PORT = 443


@dataclass
class ConnectivitySetup:
    """Result of setting up a connectivity mode for an operator."""
    mode: str
    control_plane_url: str  # WebSocket or HTTP URL the operator should connect to
    env_vars: dict[str, str]  # Environment variables for the operator container
    helm_install_command: str  # Full helm install command
    kubectl_command: str  # Alternative kubectl apply command
    notes: list[str]  # User-facing notes/warnings
    setup_status: str  # "ready", "pending", "error"
    error: str | None = None


# ---------------------------------------------------------------------------
# Connectivity URL resolvers (one per mode)
# ---------------------------------------------------------------------------

def _get_bnk_forge_host() -> str:
    """Get the configured BNK-Forge hostname/IP."""
    return os.environ.get("BNK_FORGE_HOST", "localhost")


def _get_bnk_forge_port() -> int:
    """Get the BNK-Forge port."""
    return int(os.environ.get("BNK_FORGE_PORT", str(DEFAULT_WS_PORT)))


def resolve_direct_ws(
    db: Session,
    operator_id: str,
    config: dict[str, Any],
) -> ConnectivitySetup:
    """
    Direct WebSocket mode.

    Operator connects outbound to BNK-Forge. Needs BNK-Forge to be
    reachable from the cluster network.
    """
    host = config.get("bnk_forge_host") or _get_bnk_forge_host()
    port = config.get("bnk_forge_port") or _get_bnk_forge_port()
    use_tls = config.get("use_tls", False)

    scheme = "wss" if use_tls else "ws"
    ws_url = f"{scheme}://{host}:{port}/ws/operator"

    notes = []
    if host == "localhost" or host.startswith("127."):
        notes.append("WARNING: BNK-Forge host is 'localhost' — the operator won't be able to reach it from a remote cluster.")
        notes.append("Consider using ngrok_tunnel mode or setting BNK_FORGE_HOST to a reachable address.")

    return ConnectivitySetup(
        mode="direct_ws",
        control_plane_url=ws_url,
        env_vars={
            "CONTROL_PLANE_URL": ws_url,
            "CONNECTIVITY_MODE": "websocket",
        },
        helm_install_command=_build_helm_command(ws_url, config.get("token", ""), "websocket"),
        kubectl_command="kubectl apply -f <BNK_FORGE_URL>/api/operators/install/<token>.yaml",
        notes=notes,
        setup_status="ready",
    )


def resolve_reverse_ssh(
    db: Session,
    operator_id: str,
    config: dict[str, Any],
) -> ConnectivitySetup:
    """
    Reverse SSH Tunnel mode.

    BNK-Forge opens an SSH connection to the cluster's bastion host and creates
    a reverse port forward (-R). This makes BNK-Forge's WS port accessible
    as a local port on the bastion. The operator connects to that local port.

    Requires: SSH credentials (same as existing SSH tunnel feature).

    Network flow:
      operator → bastion:REVERSE_PORT → SSH reverse tunnel → BNK-Forge:443
    """
    ssh_credential_id = config.get("ssh_credential_id")
    config.get("cluster_id")
    reverse_port = config.get("reverse_port", 443)

    if not ssh_credential_id:
        return ConnectivitySetup(
            mode="reverse_ssh",
            control_plane_url="",
            env_vars={},
            helm_install_command="",
            kubectl_command="",
            notes=["Select an SSH credential to enable reverse SSH tunnel."],
            setup_status="pending",
            error="SSH credential not configured",
        )

    # Resolve SSH host from first-class SSHCredential
    ssh_host = None

    from models import SSHCredential
    cred = db.query(SSHCredential).filter(SSHCredential.id == ssh_credential_id).first()
    if cred:
        ssh_host = cred.host

    if not ssh_host:
        return ConnectivitySetup(
            mode="reverse_ssh",
            control_plane_url="",
            env_vars={},
            helm_install_command="",
            kubectl_command="",
            notes=[],
            setup_status="error",
            error=f"SSH credential not found (id={ssh_credential_id})",
        )
    # The operator inside the cluster connects to the bastion's reverse-forwarded port
    # If operator is in the cluster, it uses the SSH host's internal IP or "localhost"
    # if the bastion is also the node.
    operator_ws_host = config.get("operator_ws_host", ssh_host)
    ws_url = f"ws://{operator_ws_host}:{reverse_port}/ws/operator"

    notes = [
        f"BNK-Forge will open a reverse SSH tunnel through {ssh_host}",
        f"Remote port {reverse_port} on {ssh_host} will forward to BNK-Forge's WebSocket port",
        f"The operator connects to ws://{operator_ws_host}:{reverse_port}/ws/operator",
        "Ensure the SSH credential has been tested and is working",
    ]

    return ConnectivitySetup(
        mode="reverse_ssh",
        control_plane_url=ws_url,
        env_vars={
            "CONTROL_PLANE_URL": ws_url,
            "CONNECTIVITY_MODE": "websocket",
        },
        helm_install_command=_build_helm_command(ws_url, config.get("token", ""), "websocket"),
        kubectl_command="",
        notes=notes,
        setup_status="ready",
    )


def resolve_polling(
    db: Session,
    operator_id: str,
    config: dict[str, Any],
) -> ConnectivitySetup:
    """
    HTTP Polling mode.

    The operator doesn't use WebSocket at all. Instead it:
      1. Polls GET /api/operators/{operator_id}/poll for pending commands
      2. Executes commands locally
      3. POSTs results to POST /api/operators/{operator_id}/results/{command_id}
      4. Sends heartbeat via POST /api/operators/{operator_id}/heartbeat

    This works through any HTTP proxy, corporate firewall, or even air-gapped
    networks with a one-way HTTP gateway.
    """
    host = config.get("bnk_forge_host") or _get_bnk_forge_host()
    port = config.get("bnk_forge_port") or _get_bnk_forge_port()
    use_tls = config.get("use_tls", False)
    poll_interval = config.get("poll_interval_seconds", 5)

    scheme = "https" if use_tls else "http"
    api_url = f"{scheme}://{host}:{port}"

    notes = [
        f"Operator will poll {api_url}/api/operators/poll every {poll_interval}s",
        "Higher latency than WebSocket (~5s vs instant), but works through any proxy/firewall",
        "No WebSocket or persistent connection required",
    ]

    if host == "localhost" or host.startswith("127."):
        notes.insert(0, "WARNING: BNK-Forge host is 'localhost' — set BNK_FORGE_HOST or use reverse_ssh mode.")

    return ConnectivitySetup(
        mode="polling",
        control_plane_url=api_url,
        env_vars={
            "CONTROL_PLANE_URL": api_url,
            "CONNECTIVITY_MODE": "polling",
            "POLL_INTERVAL_SECONDS": str(poll_interval),
        },
        helm_install_command=_build_helm_command(api_url, config.get("token", ""), "polling", poll_interval=poll_interval),
        kubectl_command="",
        notes=notes,
        setup_status="ready",
    )


def resolve_ngrok_tunnel(
    db: Session,
    operator_id: str,
    config: dict[str, Any],
) -> ConnectivitySetup:
    """
    ngrok / Cloudflare Tunnel mode.

    BNK-Forge exposes itself via ngrok or cloudflare tunnel.
    The public URL is passed to the operator's install command.

    Two sub-modes:
      - Auto: BNK-Forge launches ngrok itself (if ngrok is installed)
      - Manual: User provides a public URL from an external tunnel
    """
    public_url = config.get("public_url")  # User-provided external tunnel URL
    auto_tunnel = config.get("auto_tunnel", False)

    if auto_tunnel and not public_url:
        # Try to detect existing ngrok tunnel
        public_url = _detect_ngrok_tunnel()

    if not public_url:
        return ConnectivitySetup(
            mode="ngrok_tunnel",
            control_plane_url="",
            env_vars={},
            helm_install_command="",
            kubectl_command="",
            notes=[
                "Start a tunnel to expose BNK-Forge publicly:",
                "  ngrok http 443",
                "  # or: cloudflared tunnel --url https://localhost",
                "Then enter the public URL (e.g., https://abc123.ngrok-free.app)",
            ],
            setup_status="pending",
            error="Public URL not configured. Start ngrok or cloudflared and enter the URL.",
        )

    # Convert HTTPS URL to WSS
    ws_url = public_url.replace("https://", "wss://").replace("http://", "ws://")
    if not ws_url.endswith("/ws/operator"):
        ws_url = ws_url.rstrip("/") + "/ws/operator"

    notes = [
        f"Using tunnel URL: {public_url}",
        "The operator will connect via the public tunnel URL",
        "Ensure the tunnel stays running during the demo/PoC",
    ]

    return ConnectivitySetup(
        mode="ngrok_tunnel",
        control_plane_url=ws_url,
        env_vars={
            "CONTROL_PLANE_URL": ws_url,
            "CONNECTIVITY_MODE": "websocket",
        },
        helm_install_command=_build_helm_command(ws_url, config.get("token", ""), "websocket"),
        kubectl_command="",
        notes=notes,
        setup_status="ready",
    )


def resolve_in_cluster(
    db: Session,
    operator_id: str,
    config: dict[str, Any],
) -> ConnectivitySetup:
    """
    In-Cluster mode.

    Both BNK-Forge and the operator run in the same K8s cluster.
    The operator connects to BNK-Forge via the Kubernetes service DNS.

    Auto-detects if we're running inside K8s by checking for
    KUBERNETES_SERVICE_HOST environment variable.
    """
    # Auto-detect in-cluster
    is_in_k8s = os.environ.get("KUBERNETES_SERVICE_HOST") is not None

    service_name = config.get("service_name", "bnk-forge")
    service_namespace = config.get("service_namespace", "bnk-forge")
    service_port = config.get("service_port", DEFAULT_WS_PORT)

    ws_url = f"ws://{service_name}.{service_namespace}.svc.cluster.local:{service_port}/ws/operator"

    notes = []
    if not is_in_k8s:
        notes.append("WARNING: BNK-Forge does not appear to be running inside Kubernetes.")
        notes.append("In-cluster mode requires BNK-Forge to be deployed as a K8s service.")
    else:
        notes.append(f"BNK-Forge detected in K8s. Operator will connect to {ws_url}")

    return ConnectivitySetup(
        mode="in_cluster",
        control_plane_url=ws_url,
        env_vars={
            "CONTROL_PLANE_URL": ws_url,
            "CONNECTIVITY_MODE": "websocket",
        },
        helm_install_command=_build_helm_command(ws_url, config.get("token", ""), "websocket"),
        kubectl_command="",
        notes=notes,
        setup_status="ready" if is_in_k8s else "pending",
    )


# ---------------------------------------------------------------------------
# Resolver dispatcher
# ---------------------------------------------------------------------------

RESOLVERS = {
    "direct_ws": resolve_direct_ws,
    "reverse_ssh": resolve_reverse_ssh,
    "polling": resolve_polling,
    "ngrok_tunnel": resolve_ngrok_tunnel,
    "in_cluster": resolve_in_cluster,
}


def resolve_connectivity(
    db: Session,
    mode: str,
    operator_id: str,
    config: dict[str, Any],
) -> ConnectivitySetup:
    """Resolve the connectivity setup for a given mode."""
    resolver = RESOLVERS.get(mode)
    if not resolver:
        return ConnectivitySetup(
            mode=mode,
            control_plane_url="",
            env_vars={},
            helm_install_command="",
            kubectl_command="",
            notes=[],
            setup_status="error",
            error=f"Unknown connectivity mode: {mode}",
        )
    return resolver(db, operator_id, config)


def get_available_modes(db: Session) -> list[dict[str, Any]]:
    """
    Return list of available connectivity modes with current status.

    Checks which modes are actually usable in the current environment.
    """
    modes = []
    for mode_id, meta in CONNECTIVITY_MODES.items():
        available = True
        status_note = None

        if mode_id == "in_cluster":
            if not os.environ.get("KUBERNETES_SERVICE_HOST"):
                available = False
                status_note = "BNK-Forge is not running in Kubernetes"

        if mode_id == "ngrok_tunnel":
            ngrok_url = _detect_ngrok_tunnel()
            if ngrok_url:
                status_note = f"Active tunnel: {ngrok_url}"

        if mode_id == "reverse_ssh":
            # Check if any SSH credentials exist (first-class or legacy)
            from models import CloudCredentialTemplate, SSHCredential
            ssh_count = db.query(SSHCredential).count()
            if ssh_count == 0:
                # Fall back to legacy check
                ssh_count = db.query(CloudCredentialTemplate).filter(
                    CloudCredentialTemplate.provider == "ssh",
                ).count()
            if ssh_count == 0:
                status_note = "No SSH credentials configured"

        modes.append({
            **meta,
            "id": mode_id,
            "available": available,
            "status_note": status_note,
        })

    return modes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_helm_command(
    control_plane_url: str,
    token: str,
    connectivity_mode: str,
    poll_interval: int = 5,
) -> str:
    """Build a helm install command string."""
    cmd = (
        f"helm install bnk-operator ./bnk-operator/charts/bnk-operator \\\n"
        f"  --namespace bnk-operator --create-namespace \\\n"
        f"  --set controlPlane.url={control_plane_url} \\\n"
        f"  --set controlPlane.token={token} \\\n"
        f"  --set connectivityMode={connectivity_mode}"
    )
    if connectivity_mode == "polling":
        cmd += f" \\\n  --set pollIntervalSeconds={poll_interval}"
    return cmd


def _detect_ngrok_tunnel() -> str | None:
    """
    Detect an active ngrok tunnel by querying the ngrok local API.

    ngrok exposes an API at http://localhost:4040/api/tunnels when running.
    """
    try:
        import json
        import urllib.request
        req = urllib.request.Request("http://localhost:4040/api/tunnels", method="GET")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read())
            tunnels = data.get("tunnels", [])
            for t in tunnels:
                public_url = t.get("public_url", "")
                if public_url.startswith("https://"):
                    return public_url
            # Fall back to first tunnel
            if tunnels:
                return tunnels[0].get("public_url")
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Reverse SSH tunnel management
# ---------------------------------------------------------------------------

def open_reverse_ssh_tunnel(
    db: Session,
    ssh_credential_id: int | None = None,
    remote_port: int = 443,
    local_port: int | None = None,
) -> dict[str, Any]:
    """
    Open a reverse SSH tunnel so a remote operator can reach BNK-Forge.

    This creates an SSH connection to the bastion and sets up remote port forwarding:
      bastion:remote_port → BNK-Forge:local_port
    """
    from core.encryption import decrypt_value

    cred = None

    if ssh_credential_id:
        from models import SSHCredential
        cred = db.query(SSHCredential).filter(SSHCredential.id == ssh_credential_id).first()

    if not cred:
        return {"success": False, "error": "SSH credential not found or wrong type"}

    if not local_port:
        local_port = _get_bnk_forge_port()

    ssh_host = cred.host
    ssh_port = cred.port or 22
    ssh_username = cred.username
    ssh_key = decrypt_value(cred.private_key_encrypted) if cred.private_key_encrypted else None
    ssh_password = decrypt_value(cred.password_encrypted) if cred.password_encrypted else None

    try:
        import socket
        import threading

        import paramiko

        # Connect
        sock = socket.create_connection((ssh_host, ssh_port), timeout=15)
        transport = paramiko.Transport(sock)
        transport.set_keepalive(60)

        if ssh_key:
            pkey = _load_paramiko_key(ssh_key, cred.key_passphrase_encrypted)
            transport.connect(username=ssh_username, pkey=pkey)
        elif ssh_password:
            transport.connect(username=ssh_username, password=ssh_password)
        else:
            return {"success": False, "error": "No SSH key or password available"}

        # Request reverse port forward
        # This makes remote_port on the SSH server forward to local_host:local_port
        transport.request_port_forward("", remote_port)

        # Start a thread that accepts reverse-forwarded connections and proxies to local
        def _reverse_forward_loop():
            while transport.is_active():
                try:
                    channel = transport.accept(timeout=1)
                    if channel is None:
                        continue
                    # Forward to local BNK-Forge
                    _proxy_channel_to_local(channel, "localhost", local_port)
                except Exception as e:
                    if transport.is_active():
                        logger.warning(f"Reverse tunnel accept error: {e}")
                    break

        thread = threading.Thread(target=_reverse_forward_loop, daemon=True)
        thread.start()

        logger.info(
            f"Reverse SSH tunnel opened: {ssh_host}:{remote_port} → localhost:{local_port}"
        )

        return {
            "success": True,
            "ssh_host": ssh_host,
            "remote_port": remote_port,
            "local_port": local_port,
            "message": f"Reverse tunnel active: {ssh_host}:{remote_port} → localhost:{local_port}",
        }

    except Exception as e:
        logger.error(f"Failed to open reverse SSH tunnel: {e}")
        return {"success": False, "error": str(e)}


def _proxy_channel_to_local(channel, local_host: str, local_port: int):
    """Proxy an SSH channel to a local TCP port."""
    import select
    import socket
    import threading

    def _proxy():
        try:
            local_sock = socket.create_connection((local_host, local_port), timeout=5)
        except Exception as e:
            logger.warning(f"Cannot connect to local {local_host}:{local_port}: {e}")
            channel.close()
            return

        try:
            while True:
                r, _, _ = select.select([channel, local_sock], [], [], 1.0)
                if channel in r:
                    data = channel.recv(65536)
                    if not data:
                        break
                    local_sock.sendall(data)
                if local_sock in r:
                    data = local_sock.recv(65536)
                    if not data:
                        break
                    channel.sendall(data)
        except Exception:
            pass
        finally:
            channel.close()
            local_sock.close()

    t = threading.Thread(target=_proxy, daemon=True)
    t.start()


def _load_paramiko_key(key_content: str, passphrase_encrypted: str | None = None):
    """Load a paramiko key from string content, trying multiple key types."""
    import io

    import paramiko

    passphrase = None
    if passphrase_encrypted:
        from core.encryption import decrypt_value
        passphrase = decrypt_value(passphrase_encrypted)

    key_file = io.StringIO(key_content)

    for key_class in (paramiko.RSAKey, paramiko.ECDSAKey, paramiko.Ed25519Key):
        try:
            key_file.seek(0)
            return key_class.from_private_key(key_file, password=passphrase)
        except Exception:
            continue

    raise ValueError("Cannot load SSH key — unsupported key type")
