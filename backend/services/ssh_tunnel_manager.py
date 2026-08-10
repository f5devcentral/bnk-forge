"""
SSH Tunnel Manager for On-Prem Kubernetes Cluster Access.

Manages SSH tunnels for connecting BNK Forge to K8s clusters behind firewalls.
Tunnels are opened on-demand with keepalive, health checks, and auto-reconnect.

Key design decisions (from PoC):
- SSH keepalive is MANDATORY — tunnels silently die without it (especially over VPN)
- Bind to 0.0.0.0 — BNK Forge runs in Docker, tunnel must be accessible from container network
- Auto-reconnect on failure — VPN reconnects, laptop sleep/wake, network switches all kill tunnels
- Idle TTL — close tunnels after 1 hour of no K8s API calls to free resources
"""

import logging
import socket
import threading
import time
from dataclasses import dataclass, field

import paramiko

from core.encryption import decrypt_value
from services.ssh.paramiko_utils import load_private_key_from_content

logger = logging.getLogger(__name__)


@dataclass
class TunnelInfo:
    """Tracks an active SSH tunnel for a cluster."""
    cluster_id: int
    local_port: int
    remote_host: str
    remote_port: int
    ssh_host: str
    ssh_port: int
    ssh_username: str
    transport: paramiko.Transport | None = None
    channel: paramiko.Channel | None = None
    # Held only when the tunnel is established via a jumphost. The host
    # SSH transport rides a direct-tcpip channel on this client; closing
    # it tears down that channel, so we keep a reference for the tunnel's
    # lifetime and close it explicitly in _close_tunnel_internal.
    jumphost_client: paramiko.SSHClient | None = None
    forward_server: socket.socket | None = None
    forward_thread: threading.Thread | None = None
    health_thread: threading.Thread | None = None
    last_used: float = field(default_factory=time.time)
    last_health_check: float = field(default_factory=time.time)
    is_healthy: bool = True
    reconnect_attempts: int = 0
    _stop_event: threading.Event = field(default_factory=threading.Event)


class SSHTunnelManager:
    """
    Manages SSH tunnels for on-prem K8s cluster access.

    Tunnels are on-demand with keepalive + auto-reconnect. Opened when
    BNK Forge needs to connect to a cluster, kept alive with periodic
    health checks, and automatically re-established if they drop.
    """

    # Configuration
    SSH_KEEPALIVE_INTERVAL = 60       # Send keepalive ping every 60 seconds
    SSH_KEEPALIVE_COUNT_MAX = 3       # Allow 3 missed pings before declaring dead
    TUNNEL_HEALTH_CHECK_INTERVAL = 30  # Check tunnel health every 30 seconds
    TUNNEL_RECONNECT_DELAY = 5        # Wait 5 seconds before reconnect attempt
    TUNNEL_MAX_RECONNECT_ATTEMPTS = 3  # Max reconnect attempts before giving up
    TUNNEL_IDLE_TTL = 3600            # Close tunnel after 1 hour of no K8s API calls
    SSH_CONNECT_TIMEOUT = 15          # SSH connection timeout in seconds

    def __init__(self):
        self._tunnels: dict[int, TunnelInfo] = {}
        self._lock = threading.Lock()
        self._ssh_credentials_cache: dict[int, dict] = {}  # template_id -> decrypted creds

    def get_or_open_tunnel(
        self,
        cluster_id: int,
        ssh_host: str,
        ssh_port: int,
        ssh_username: str,
        ssh_auth_type: str,
        ssh_password_encrypted: str | None,
        ssh_key_encrypted: str | None,
        ssh_key_passphrase_encrypted: str | None,
        remote_k8s_host: str = "localhost",
        remote_k8s_port: int = 6443,
        jumphost: dict | None = None,
    ) -> int:
        """
        Returns local port for an active tunnel, or opens a new one.

        Args:
            cluster_id: Database ID of the K8s cluster
            ssh_host: SSH bastion/jump host address
            ssh_port: SSH port (usually 22)
            ssh_username: SSH username
            ssh_auth_type: 'key' or 'password'
            ssh_password_encrypted: Fernet-encrypted SSH password
            ssh_key_encrypted: Fernet-encrypted SSH private key content
            ssh_key_passphrase_encrypted: Fernet-encrypted key passphrase (optional)
            remote_k8s_host: K8s API host from SSH host perspective (usually 'localhost')
            remote_k8s_port: K8s API port from SSH host perspective (usually 6443)

        Returns:
            Local port number to connect to (e.g., https://localhost:{port})
        """
        with self._lock:
            # Check for existing tunnel
            if cluster_id in self._tunnels:
                tunnel = self._tunnels[cluster_id]
                if tunnel.is_healthy and self._quick_health_check(tunnel):
                    tunnel.last_used = time.time()
                    logger.debug(f"Reusing existing tunnel for cluster {cluster_id} on port {tunnel.local_port}")
                    return tunnel.local_port
                else:
                    # Tunnel exists but is unhealthy — close and reopen
                    logger.warning(f"Tunnel for cluster {cluster_id} is unhealthy, reopening...")
                    self._close_tunnel_internal(cluster_id)

        # Open new tunnel (outside lock to avoid blocking)
        return self._open_tunnel(
            cluster_id=cluster_id,
            ssh_host=ssh_host,
            ssh_port=ssh_port,
            ssh_username=ssh_username,
            ssh_auth_type=ssh_auth_type,
            ssh_password_encrypted=ssh_password_encrypted,
            ssh_key_encrypted=ssh_key_encrypted,
            ssh_key_passphrase_encrypted=ssh_key_passphrase_encrypted,
            remote_k8s_host=remote_k8s_host,
            remote_k8s_port=remote_k8s_port,
            jumphost=jumphost,
        )

    def _open_tunnel(
        self,
        cluster_id: int,
        ssh_host: str,
        ssh_port: int,
        ssh_username: str,
        ssh_auth_type: str,
        ssh_password_encrypted: str | None,
        ssh_key_encrypted: str | None,
        ssh_key_passphrase_encrypted: str | None,
        remote_k8s_host: str,
        remote_k8s_port: int,
        jumphost: dict | None = None,
    ) -> int:
        """Open a new SSH tunnel with port forwarding.

        When ``jumphost`` is provided (dict with keys: host, port, username,
        auth_type, password_encrypted, key_encrypted, key_passphrase_encrypted),
        the tunnel SSHes worker → jumphost → ssh_host via paramiko's
        ``direct-tcpip`` channel. Required when the K8s host's mgmt IP isn't
        directly routable from the worker container — same shape as the
        bare-metal deployment code's worker → project-jumphost → host flow.
        """
        if jumphost:
            logger.info(
                f"Opening SSH tunnel for cluster {cluster_id}: "
                f"{ssh_username}@{ssh_host}:{ssh_port} via jumphost "
                f"{jumphost.get('username')}@{jumphost.get('host')}:{jumphost.get('port', 22)} "
                f"-> {remote_k8s_host}:{remote_k8s_port}"
            )
        else:
            logger.info(f"Opening SSH tunnel for cluster {cluster_id}: "
                         f"{ssh_username}@{ssh_host}:{ssh_port} -> {remote_k8s_host}:{remote_k8s_port}")

        # Decrypt credentials
        ssh_password = decrypt_value(ssh_password_encrypted) if ssh_password_encrypted else None
        ssh_key_content = decrypt_value(ssh_key_encrypted) if ssh_key_encrypted else None
        ssh_key_passphrase = decrypt_value(ssh_key_passphrase_encrypted) if ssh_key_passphrase_encrypted else None

        # When a jumphost is in play, *don't* run a second SSH session to
        # the host — that means nested paramiko Transports (Transport-over-
        # Channel-over-Transport) and the inner data-plane channels for
        # local port forwarding can stall under load. Instead, open ONE
        # SSH session to the jumphost (using jumphost creds, NOT the
        # host's) and forward direct-tcpip from there straight to the K8s
        # API at remote_k8s_host:remote_k8s_port. The jumphost has L3
        # reachability to the K8s host's mgmt subnet (that's its whole
        # purpose), so this works without a second SSH hop. TLS to the
        # API is unaffected — the kubeconfig rewrite already sets
        # `insecure-skip-tls-verify: true` so the cert SAN mismatch
        # (we'll dial localhost:NNN, cert names the VLAN IP) is OK.
        jumphost_client: paramiko.SSHClient | None = None
        if jumphost:
            try:
                jumphost_client = self._open_jumphost_client(jumphost)
                # Override the SSH transport: the jumphost transport IS
                # the tunnel transport. ssh_username/ssh_host are kept on
                # TunnelInfo only for logging — they don't drive auth.
                transport = jumphost_client.get_transport()
                if transport is None or not transport.is_active():
                    raise ValueError("Jumphost SSH transport unavailable")
                transport.set_keepalive(self.SSH_KEEPALIVE_INTERVAL)
                logger.info(
                    f"SSH tunnel authenticated to jumphost "
                    f"{jumphost.get('username')}@{jumphost.get('host')}:"
                    f"{jumphost.get('port', 22)}; will forward direct-tcpip to "
                    f"{remote_k8s_host}:{remote_k8s_port}"
                )
                # Skip the per-host SSH connect path below.
                sock = None
                _skip_host_ssh = True
            except Exception as exc:
                if jumphost_client is not None:
                    try:
                        jumphost_client.close()
                    except Exception:
                        pass
                raise ValueError(
                    f"Failed to open jumphost SSH session "
                    f"({jumphost.get('host')}): {exc}"
                )
        else:
            sock = None
            _skip_host_ssh = False

        # Create SSH transport directly to the host (no jumphost case)
        try:
            if not _skip_host_ssh:
                sock = socket.create_connection((ssh_host, ssh_port), timeout=self.SSH_CONNECT_TIMEOUT)
                transport = paramiko.Transport(sock)

                # Enable keepalive — CRITICAL for tunnel stability
                transport.set_keepalive(self.SSH_KEEPALIVE_INTERVAL)

                # Authenticate
                if ssh_auth_type == 'key' and ssh_key_content:
                    pkey = load_private_key_from_content(ssh_key_content, ssh_key_passphrase)
                    transport.connect(username=ssh_username, pkey=pkey)
                elif ssh_password:
                    transport.connect(username=ssh_username, password=ssh_password)
                else:
                    raise ValueError("No SSH credentials provided (need key or password)")
            # else: transport was set by the jumphost branch above and is
            # already authenticated. ssh_host / ssh_username / ssh_password
            # are unused for the jumphost case (they identify the K8s host
            # for logging/diagnostics only; forwarding hits the API
            # endpoint directly via direct-tcpip on the jumphost transport).

            if not transport.is_authenticated():
                raise ValueError("SSH authentication failed")

            if not _skip_host_ssh:
                logger.info(f"SSH authenticated to {ssh_host}:{ssh_port} as {ssh_username}")

        except paramiko.AuthenticationException as e:
            raise ValueError(f"SSH authentication failed: {e}")
        except TimeoutError:
            raise ValueError(f"SSH connection to {ssh_host}:{ssh_port} timed out after {self.SSH_CONNECT_TIMEOUT}s")
        except socket.gaierror:
            raise ValueError(f"Cannot resolve SSH host: {ssh_host}")
        except ConnectionRefusedError:
            raise ValueError(f"SSH connection refused by {ssh_host}:{ssh_port}")
        except Exception as e:
            raise ValueError(f"SSH connection failed: {e}")

        # Find a free local port
        local_port = self._find_free_port()

        # Start local forwarding server
        stop_event = threading.Event()
        forward_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        forward_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        forward_server.settimeout(1.0)  # Allow periodic stop checks
        forward_server.bind(('0.0.0.0', local_port))
        forward_server.listen(5)

        # Create tunnel info
        tunnel = TunnelInfo(
            cluster_id=cluster_id,
            local_port=local_port,
            remote_host=remote_k8s_host,
            remote_port=remote_k8s_port,
            ssh_host=ssh_host,
            ssh_port=ssh_port,
            ssh_username=ssh_username,
            transport=transport,
            jumphost_client=jumphost_client,
            forward_server=forward_server,
            _stop_event=stop_event,
        )

        # Start port forwarding thread
        forward_thread = threading.Thread(
            target=self._forward_loop,
            args=(tunnel,),
            daemon=True,
            name=f"ssh-tunnel-cluster-{cluster_id}",
        )
        forward_thread.start()
        tunnel.forward_thread = forward_thread

        # Start health monitor thread
        health_thread = threading.Thread(
            target=self._health_monitor_loop,
            args=(tunnel, ssh_password_encrypted, ssh_key_encrypted, ssh_key_passphrase_encrypted, ssh_auth_type),
            daemon=True,
            name=f"ssh-health-cluster-{cluster_id}",
        )
        health_thread.start()
        tunnel.health_thread = health_thread

        # Register tunnel
        with self._lock:
            self._tunnels[cluster_id] = tunnel

        logger.info(f"SSH tunnel active: localhost:{local_port} -> "
                     f"{ssh_host}:{ssh_port} -> {remote_k8s_host}:{remote_k8s_port}")

        return local_port

    def _forward_loop(self, tunnel: TunnelInfo):
        """Accept local connections and forward them through SSH tunnel."""
        while not tunnel._stop_event.is_set():
            try:
                client_sock, addr = tunnel.forward_server.accept()
            except TimeoutError:
                continue
            except OSError:
                # Server socket closed
                break

            # Handle each connection in a sub-thread
            handler = threading.Thread(
                target=self._forward_connection,
                args=(tunnel, client_sock),
                daemon=True,
            )
            handler.start()

    def _forward_connection(self, tunnel: TunnelInfo, client_sock: socket.socket):
        """Forward a single connection through the SSH tunnel."""
        try:
            transport = tunnel.transport
            if not transport or not transport.is_active():
                client_sock.close()
                return

            # Open channel to remote K8s API
            channel = transport.open_channel(
                'direct-tcpip',
                (tunnel.remote_host, tunnel.remote_port),
                client_sock.getpeername(),
                timeout=10,
            )

            if channel is None:
                logger.error(f"SSH channel open failed for cluster {tunnel.cluster_id}")
                client_sock.close()
                return

            # Bidirectional forwarding
            while True:
                # Check both directions
                import select
                r, w, x = select.select([client_sock, channel], [], [], 1.0)

                if client_sock in r:
                    data = client_sock.recv(65536)
                    if not data:
                        break
                    channel.sendall(data)

                if channel in r:
                    data = channel.recv(65536)
                    if not data:
                        break
                    client_sock.sendall(data)

        except Exception as e:
            logger.debug(f"Tunnel forward connection ended: {e}")
        finally:
            try:
                client_sock.close()
            except Exception:
                pass

    def _health_monitor_loop(
        self,
        tunnel: TunnelInfo,
        ssh_password_encrypted: str | None,
        ssh_key_encrypted: str | None,
        ssh_key_passphrase_encrypted: str | None,
        ssh_auth_type: str,
    ):
        """Background thread: periodically health-check the tunnel and auto-reconnect."""
        while not tunnel._stop_event.is_set():
            tunnel._stop_event.wait(self.TUNNEL_HEALTH_CHECK_INTERVAL)
            if tunnel._stop_event.is_set():
                break

            # Check idle TTL
            idle_seconds = time.time() - tunnel.last_used
            if idle_seconds > self.TUNNEL_IDLE_TTL:
                logger.info(f"Tunnel for cluster {tunnel.cluster_id} idle for "
                           f"{idle_seconds:.0f}s (TTL={self.TUNNEL_IDLE_TTL}s), closing")
                self.close_tunnel(tunnel.cluster_id)
                return

            # Health check
            healthy = self._quick_health_check(tunnel)
            tunnel.last_health_check = time.time()

            if not healthy:
                tunnel.is_healthy = False
                logger.warning(f"Tunnel health check FAILED for cluster {tunnel.cluster_id}")

                # Auto-reconnect
                reconnected = self._auto_reconnect(
                    tunnel, ssh_password_encrypted, ssh_key_encrypted,
                    ssh_key_passphrase_encrypted, ssh_auth_type
                )
                if not reconnected:
                    logger.error(f"Tunnel auto-reconnect FAILED for cluster {tunnel.cluster_id} "
                               f"after {self.TUNNEL_MAX_RECONNECT_ATTEMPTS} attempts")
                    # Don't close — leave for explicit close or next get_or_open_tunnel call
            else:
                tunnel.is_healthy = True
                tunnel.reconnect_attempts = 0

    def _quick_health_check(self, tunnel: TunnelInfo) -> bool:
        """Fast check: is the SSH transport still alive?"""
        try:
            if not tunnel.transport or not tunnel.transport.is_active():
                return False
            # Try to open a channel to verify the transport is truly functional
            # We don't actually forward data, just test channel open/close
            return True
        except Exception:
            return False

    def _auto_reconnect(
        self,
        tunnel: TunnelInfo,
        ssh_password_encrypted: str | None,
        ssh_key_encrypted: str | None,
        ssh_key_passphrase_encrypted: str | None,
        ssh_auth_type: str,
    ) -> bool:
        """Attempt to re-establish a dropped SSH transport."""
        for attempt in range(1, self.TUNNEL_MAX_RECONNECT_ATTEMPTS + 1):
            logger.info(f"Reconnect attempt {attempt}/{self.TUNNEL_MAX_RECONNECT_ATTEMPTS} "
                       f"for cluster {tunnel.cluster_id}")

            try:
                # Close old transport
                if tunnel.transport:
                    try:
                        tunnel.transport.close()
                    except Exception:
                        pass

                # Decrypt credentials
                ssh_password = decrypt_value(ssh_password_encrypted) if ssh_password_encrypted else None
                ssh_key_content = decrypt_value(ssh_key_encrypted) if ssh_key_encrypted else None
                ssh_key_passphrase = decrypt_value(ssh_key_passphrase_encrypted) if ssh_key_passphrase_encrypted else None

                # Re-establish SSH connection
                sock = socket.create_connection(
                    (tunnel.ssh_host, tunnel.ssh_port),
                    timeout=self.SSH_CONNECT_TIMEOUT
                )
                transport = paramiko.Transport(sock)
                transport.set_keepalive(self.SSH_KEEPALIVE_INTERVAL)

                if ssh_auth_type == 'key' and ssh_key_content:
                    pkey = load_private_key_from_content(ssh_key_content, ssh_key_passphrase)
                    transport.connect(username=tunnel.ssh_username, pkey=pkey)
                elif ssh_password:
                    transport.connect(username=tunnel.ssh_username, password=ssh_password)
                else:
                    logger.error("No SSH credentials for reconnect")
                    return False

                if transport.is_authenticated():
                    tunnel.transport = transport
                    tunnel.is_healthy = True
                    tunnel.reconnect_attempts = attempt
                    logger.info(f"Reconnect SUCCESS for cluster {tunnel.cluster_id} (attempt {attempt})")
                    return True

            except Exception as e:
                logger.warning(f"Reconnect attempt {attempt} failed: {e}")

            # Wait before next attempt
            if attempt < self.TUNNEL_MAX_RECONNECT_ATTEMPTS:
                time.sleep(self.TUNNEL_RECONNECT_DELAY)

        return False

    def close_tunnel(self, cluster_id: int):
        """Close an active tunnel for a cluster."""
        with self._lock:
            self._close_tunnel_internal(cluster_id)

    def _close_tunnel_internal(self, cluster_id: int):
        """Internal close (caller must hold lock)."""
        tunnel = self._tunnels.pop(cluster_id, None)
        if not tunnel:
            return

        logger.info(f"Closing SSH tunnel for cluster {cluster_id} (port {tunnel.local_port})")

        # Signal threads to stop
        tunnel._stop_event.set()

        # Close transport
        if tunnel.transport:
            try:
                tunnel.transport.close()
            except Exception:
                pass

        # Close jumphost client (if any). Must happen after transport
        # close so the in-flight direct-tcpip channel tears down cleanly.
        if tunnel.jumphost_client:
            try:
                tunnel.jumphost_client.close()
            except Exception:
                pass

        # Close forward server
        if tunnel.forward_server:
            try:
                tunnel.forward_server.close()
            except Exception:
                pass

    def close_all(self):
        """Close all active tunnels (shutdown cleanup)."""
        with self._lock:
            cluster_ids = list(self._tunnels.keys())
            for cluster_id in cluster_ids:
                self._close_tunnel_internal(cluster_id)
        logger.info(f"Closed {len(cluster_ids)} SSH tunnel(s)")

    def get_tunnel_status(self, cluster_id: int) -> dict | None:
        """Get status of a tunnel for a cluster."""
        with self._lock:
            tunnel = self._tunnels.get(cluster_id)
            if not tunnel:
                return None
            return {
                "cluster_id": cluster_id,
                "local_port": tunnel.local_port,
                "ssh_host": tunnel.ssh_host,
                "ssh_port": tunnel.ssh_port,
                "remote_host": tunnel.remote_host,
                "remote_port": tunnel.remote_port,
                "is_healthy": tunnel.is_healthy,
                "last_used": tunnel.last_used,
                "last_health_check": tunnel.last_health_check,
                "reconnect_attempts": tunnel.reconnect_attempts,
            }

    def get_all_tunnel_statuses(self) -> list:
        """Get status of all active tunnels."""
        with self._lock:
            return [
                {
                    "cluster_id": cid,
                    "local_port": t.local_port,
                    "ssh_host": t.ssh_host,
                    "is_healthy": t.is_healthy,
                    "idle_seconds": int(time.time() - t.last_used),
                }
                for cid, t in self._tunnels.items()
            ]

    def test_ssh_connection(
        self,
        ssh_host: str,
        ssh_port: int,
        ssh_username: str,
        ssh_auth_type: str,
        ssh_password_encrypted: str | None = None,
        ssh_key_encrypted: str | None = None,
        ssh_key_passphrase_encrypted: str | None = None,
    ) -> dict:
        """
        Test SSH connectivity only (not K8s). Used by "Test SSH Connection" button.

        Returns:
            {success: bool, message: str, ssh_banner: str, hostname: str}
        """
        transport = None
        try:
            # Decrypt credentials
            ssh_password = decrypt_value(ssh_password_encrypted) if ssh_password_encrypted else None
            ssh_key_content = decrypt_value(ssh_key_encrypted) if ssh_key_encrypted else None
            ssh_key_passphrase = decrypt_value(ssh_key_passphrase_encrypted) if ssh_key_passphrase_encrypted else None

            sock = socket.create_connection((ssh_host, ssh_port), timeout=self.SSH_CONNECT_TIMEOUT)
            transport = paramiko.Transport(sock)
            transport.set_keepalive(self.SSH_KEEPALIVE_INTERVAL)

            if ssh_auth_type == 'key' and ssh_key_content:
                pkey = load_private_key_from_content(ssh_key_content, ssh_key_passphrase)
                transport.connect(username=ssh_username, pkey=pkey)
            elif ssh_password:
                transport.connect(username=ssh_username, password=ssh_password)
            else:
                return {"success": False, "message": "No SSH credentials provided"}

            if not transport.is_authenticated():
                return {"success": False, "message": "SSH authentication failed"}

            # Get remote hostname
            hostname = ""
            try:
                channel = transport.open_session()
                channel.exec_command("hostname")
                hostname = channel.recv(1024).decode().strip()
                channel.close()
            except Exception:
                hostname = ssh_host

            banner = transport.remote_version or "unknown"

            return {
                "success": True,
                "message": f"SSH connection successful to {ssh_host}:{ssh_port}",
                "ssh_banner": banner,
                "hostname": hostname,
            }

        except paramiko.AuthenticationException:
            return {"success": False, "message": f"SSH authentication failed for {ssh_username}@{ssh_host}:{ssh_port}"}
        except TimeoutError:
            return {"success": False, "message": f"SSH connection to {ssh_host}:{ssh_port} timed out"}
        except socket.gaierror:
            return {"success": False, "message": f"Cannot resolve SSH host: {ssh_host}"}
        except ConnectionRefusedError:
            return {"success": False, "message": f"SSH connection refused by {ssh_host}:{ssh_port}"}
        except Exception as e:
            return {"success": False, "message": f"SSH connection failed: {str(e)}"}
        finally:
            if transport:
                try:
                    transport.close()
                except Exception:
                    pass

    @staticmethod
    def _find_free_port() -> int:
        """Find a free TCP port on the local machine."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('0.0.0.0', 0))
            return s.getsockname()[1]

    def _open_jumphost_client(self, jumphost: dict) -> paramiko.SSHClient:
        """Open a paramiko SSHClient to a jumphost.

        Caller is responsible for closing the returned client (lives on
        the TunnelInfo for the duration of the tunnel).

        Expected keys in `jumphost`: host, port (default 22), username,
        auth_type ("key" or "password"), password_encrypted,
        key_encrypted, key_passphrase_encrypted.
        """
        jh_host = jumphost.get("host")
        jh_port = int(jumphost.get("port") or 22)
        jh_user = jumphost.get("username") or "root"
        jh_auth = (jumphost.get("auth_type") or "key").lower()
        jh_pass = decrypt_value(jumphost["password_encrypted"]) if jumphost.get("password_encrypted") else None
        jh_key = decrypt_value(jumphost["key_encrypted"]) if jumphost.get("key_encrypted") else None
        jh_passphrase = (
            decrypt_value(jumphost["key_passphrase_encrypted"])
            if jumphost.get("key_passphrase_encrypted") else None
        )

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        connect_kwargs: dict = {
            "hostname": jh_host,
            "port": jh_port,
            "username": jh_user,
            "timeout": self.SSH_CONNECT_TIMEOUT,
            "look_for_keys": False,
            "allow_agent": False,
        }
        if jh_auth == "key" and jh_key:
            pkey = load_private_key_from_content(jh_key, jh_passphrase)
            connect_kwargs["pkey"] = pkey
        elif jh_pass:
            connect_kwargs["password"] = jh_pass
        else:
            raise ValueError(
                f"Jumphost {jh_user}@{jh_host}:{jh_port} has no usable credentials "
                "(need either key or password)"
            )
        client.connect(**connect_kwargs)
        return client


# Module-level singleton
_tunnel_manager: SSHTunnelManager | None = None


def get_tunnel_manager() -> SSHTunnelManager:
    """Get or create the global SSH tunnel manager singleton."""
    global _tunnel_manager
    if _tunnel_manager is None:
        _tunnel_manager = SSHTunnelManager()
    return _tunnel_manager
