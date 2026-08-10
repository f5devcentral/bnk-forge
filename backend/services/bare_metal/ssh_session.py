"""SSH execution via paramiko for bare-metal operations."""

import logging
import os
import time
from collections.abc import Generator
from dataclasses import dataclass

import paramiko

from services.ssh.paramiko_utils import load_private_key_from_content, load_private_key_from_file

logger = logging.getLogger(__name__)


@dataclass
class SSHResult:
    """Result of an SSH command execution."""

    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float


class SSHSession:
    """
    SSH execution via paramiko — mirrors bnk-poc-deployer SSHSession.

    Uses paramiko.SSHClient for native password + key auth without any
    external binary dependency (no sshpass). Jumphost chains are handled
    via chained paramiko transports instead of ProxyCommand.
    """

    def __init__(
        self,
        host: str,
        username: str,
        port: int = 22,
        *,
        private_key_path: str | None = None,
        private_key_content: str | None = None,
        password: str | None = None,
        jumphost_chain: list[dict] | None = None,
        control_path: str | None = None,  # Accepted but unused — ControlMaster is subprocess-only
        connect_timeout: int = 15,
    ):
        self.host = host
        self.username = username
        self.port = port
        self.private_key_path = private_key_path
        self.private_key_content = private_key_content
        self.password = password
        self.jumphost_chain = jumphost_chain or []
        self.control_path = control_path  # Stored for interface compatibility; ignored by paramiko
        self.connect_timeout = connect_timeout
        self._transports: list[paramiko.Transport] = []
        # Track temp files created by build_ssh_session_from_credential for cleanup
        self._temp_key_files: list[str] = []

    def close(self) -> None:
        """Deterministically remove any temp SSH key files created for this session.

        Safe to call multiple times. Prefer this (or the context-manager form)
        over relying on ``__del__``/GC so decrypted private keys never linger in
        /tmp on the success path.
        """
        for fp in getattr(self, "_temp_key_files", []):
            try:
                os.remove(fp)
            except OSError:
                pass
        self._temp_key_files = []

    def __enter__(self) -> "SSHSession":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def __del__(self) -> None:
        """Backstop cleanup on GC — deterministic cleanup should use close()."""
        self.close()

    def _close_transports(self) -> None:
        """Close any jumphost transports opened by _connect()."""
        for transport in reversed(self._transports):
            try:
                transport.close()
            except Exception:
                pass
        self._transports = []

    def _load_private_key(self) -> "paramiko.PKey | None":
        """Load the private key for the final target host.

        Prefers content-based auth (``private_key_content``) over file-based
        (``private_key_path``).  Returns ``None`` when neither is set.

        The passphrase is taken from ``self.password`` when a key is present —
        matching the convention used by the file-based path.
        """
        passphrase = self.password if (self.private_key_content or self.private_key_path) and self.password else None
        if self.private_key_content:
            return load_private_key_from_content(self.private_key_content, passphrase=passphrase)
        if self.private_key_path:
            return load_private_key_from_file(self.private_key_path, passphrase=passphrase)
        return None

    def _connect(self) -> paramiko.SSHClient:
        """Build a connected paramiko client, chaining through jumphosts."""
        self._transports = []
        try:
            sock: paramiko.Channel | None = None

            for i, jh in enumerate(self.jumphost_chain):
                # Connect this hop (through previous sock if chained)
                if sock is None:
                    transport = paramiko.Transport((jh["host"], jh.get("port", 22)))
                else:
                    transport = paramiko.Transport(sock)

                jh_key_path = jh.get("private_key_path")
                jh_key_content = jh.get("private_key_content")
                jh_password = jh.get("password")
                jh_passphrase = jh.get("passphrase")
                jh_user = jh.get("username", "root")

                if jh_key_content:
                    pkey = load_private_key_from_content(jh_key_content, passphrase=jh_passphrase)
                    transport.connect(username=jh_user, pkey=pkey)
                elif jh_key_path:
                    pkey = load_private_key_from_file(jh_key_path, passphrase=jh_passphrase)
                    transport.connect(username=jh_user, pkey=pkey)
                elif jh_password:
                    transport.connect(username=jh_user, password=jh_password)
                else:
                    transport.connect(username=jh_user)

                self._transports.append(transport)

                # Open a channel to the next hop (or to the final target)
                if i + 1 < len(self.jumphost_chain):
                    next_host = self.jumphost_chain[i + 1]["host"]
                    next_port = self.jumphost_chain[i + 1].get("port", 22)
                else:
                    next_host = self.host
                    next_port = self.port

                sock = transport.open_channel("direct-tcpip", (next_host, next_port), ("", 0))

            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            connect_kwargs: dict = dict(
                hostname=self.host,
                port=self.port,
                username=self.username,
                timeout=self.connect_timeout,
                look_for_keys=False,
                allow_agent=False,
            )

            if sock is not None:
                connect_kwargs["sock"] = sock

            pkey = self._load_private_key()
            if pkey is not None:
                connect_kwargs["pkey"] = pkey
            elif self.password:
                connect_kwargs["password"] = self.password

            client.connect(**connect_kwargs)
            return client
        except Exception:
            self._close_transports()
            raise

    def _needs_sudo_password(self, command: str) -> bool:
        """Check if we should pipe the session password to sudo -S.

        Returns True when:
        - The command contains ``sudo `` (with trailing space)
        - We have a password (``self.password`` is set)
        - We authenticated with password, NOT a private key (when a key is
          present the password field is the key passphrase, not the user password)
        """
        return bool("sudo " in command and self.password and not self.private_key_content and not self.private_key_path)

    @staticmethod
    def _inject_sudo_stdin_flag(command: str) -> str:
        """Replace ``sudo `` with ``sudo -S `` so sudo reads the password from stdin.

        ``-S`` is harmless when NOPASSWD is configured (stdin is simply ignored).
        Handles multiple ``sudo`` invocations in a single command string.
        """
        return command.replace("sudo ", "sudo -S ")

    def execute(self, command: str, *, timeout: int = 300) -> SSHResult:
        """Execute a command on the remote host and return the result.

        When the command contains ``sudo`` and the session was authenticated
        with a password (not a private key), the password is automatically
        piped to ``sudo -S`` via stdin so hosts without NOPASSWD still work.

        Args:
            command: The shell command to execute
            timeout: Maximum seconds to wait for completion

        Returns:
            SSHResult with exit_code, stdout, stderr, and duration
        """
        start = time.monotonic()
        last_error: Exception | None = None
        max_retries = 3
        for attempt in range(max_retries):
            try:
                client = self._connect()
                try:
                    needs_sudo_pw = self._needs_sudo_password(command)
                    actual_command = self._inject_sudo_stdin_flag(command) if needs_sudo_pw else command

                    stdin, stdout, stderr = client.exec_command(actual_command, timeout=timeout)

                    if needs_sudo_pw:
                        stdin.write(self.password + "\n")
                        stdin.flush()
                        stdin.channel.shutdown_write()

                    exit_code = stdout.channel.recv_exit_status()
                    return SSHResult(
                        exit_code=exit_code,
                        stdout=stdout.read().decode("utf-8", errors="replace"),
                        stderr=stderr.read().decode("utf-8", errors="replace"),
                        duration_seconds=round(time.monotonic() - start, 3),
                    )
                finally:
                    client.close()
                    self._close_transports()
            except Exception as e:
                self._close_transports()
                last_error = e
                err_msg = str(e).lower()
                # Retry on transient SSH transport errors (connection drops,
                # rate-limiting, sshd restarts). Non-transient errors bail out.
                is_transient = any(s in err_msg for s in (
                    "socket is closed", "no existing session", "ssh session not active",
                    "connection reset", "broken pipe", "eof during negotiation",
                    "error reading ssh protocol banner", "connection refused",
                ))
                if is_transient and attempt < max_retries - 1:
                    wait = 2 ** attempt  # 1s, 2s
                    logger.warning(
                        "SSH transient error (attempt %d/%d) for %s@%s: %s — retrying in %ds",
                        attempt + 1, max_retries, self.username, self.host, e, wait,
                    )
                    time.sleep(wait)
                    continue
                logger.warning("SSH command failed for %s@%s: %s", self.username, self.host, e)
                break

        duration = time.monotonic() - start
        return SSHResult(
            exit_code=-1,
            stdout="",
            stderr=str(last_error),
            duration_seconds=round(duration, 3),
        )

    def execute_streaming(self, command: str, *, timeout: int = 3600) -> Generator[str, None, SSHResult]:
        """Execute a command and yield stdout lines as they arrive.

        When the command contains ``sudo`` and the session was authenticated
        with a password, the password is piped to the channel's stdin so
        ``sudo -S`` can read it (same logic as :meth:`execute`).

        Usage:
            gen = session.execute_streaming("long-running-cmd")
            for line in gen:
                print(line)
            result = gen.value  # Access the final SSHResult after generator exhausts

        Note: The SSHResult is returned via generator return (access via .value after StopIteration).
        """
        start = time.monotonic()
        stdout_lines: list[str] = []

        needs_sudo_pw = self._needs_sudo_password(command)
        actual_command = self._inject_sudo_stdin_flag(command) if needs_sudo_pw else command

        try:
            client = self._connect()
            try:
                channel = client.get_transport().open_session()  # type: ignore[union-attr]
                channel.settimeout(timeout)
                channel.exec_command(actual_command)

                if needs_sudo_pw:
                    channel.sendall((self.password + "\n").encode())
                    channel.shutdown_write()

                buf = ""
                while not channel.exit_status_ready() or channel.recv_ready():
                    if channel.recv_ready():
                        chunk = channel.recv(4096).decode("utf-8", errors="replace")
                        buf += chunk
                        while "\n" in buf:
                            line, buf = buf.split("\n", 1)
                            stdout_lines.append(line)
                            yield line
                    else:
                        time.sleep(0.05)

                # Drain any remaining data
                while channel.recv_ready():
                    chunk = channel.recv(4096).decode("utf-8", errors="replace")
                    buf += chunk

                if buf:
                    stdout_lines.append(buf)
                    yield buf

                exit_code = channel.recv_exit_status()

                stderr_data = b""
                while channel.recv_stderr_ready():
                    stderr_data += channel.recv_stderr(4096)

                return SSHResult(
                    exit_code=exit_code,
                    stdout="\n".join(stdout_lines),
                    stderr=stderr_data.decode("utf-8", errors="replace"),
                    duration_seconds=round(time.monotonic() - start, 3),
                )
            finally:
                client.close()
                self._close_transports()

        except TimeoutError:
            self._close_transports()
            return SSHResult(
                exit_code=-1,
                stdout="\n".join(stdout_lines),
                stderr=f"Command timed out after {timeout}s",
                duration_seconds=round(time.monotonic() - start, 3),
            )
        except Exception as e:
            self._close_transports()
            return SSHResult(
                exit_code=-1,
                stdout="\n".join(stdout_lines),
                stderr=str(e),
                duration_seconds=round(time.monotonic() - start, 3),
            )

    def upload_file(self, local_path: str, remote_path: str, *, timeout: int = 120) -> None:
        """Upload a file to the remote host via SFTP.

        Raises:
            RuntimeError: If the upload fails
        """
        try:
            client = self._connect()
            try:
                sftp = client.open_sftp()
                sftp.get_channel().settimeout(timeout)
                sftp.put(local_path, remote_path)
                sftp.close()
            finally:
                client.close()
                self._close_transports()
        except Exception as e:
            self._close_transports()
            raise RuntimeError(f"SFTP upload failed: {e}") from e

    def upload_content(self, content: str, remote_path: str, *, mode: str | None = None) -> None:
        """Upload string content to a remote file via SFTP.

        Writes content directly over SFTP without creating a temp file.

        Args:
            content: String content to write
            remote_path: Destination path on remote host
            mode: Optional chmod mode (e.g., "755", "600")
        """
        try:
            client = self._connect()
            try:
                sftp = client.open_sftp()
                with sftp.file(remote_path, "w") as f:
                    f.write(content)
                if mode:
                    sftp.chmod(remote_path, int(mode, 8))
                sftp.close()
            finally:
                client.close()
                self._close_transports()
        except Exception as e:
            self._close_transports()
            raise RuntimeError(f"SFTP content upload failed: {e}") from e

    def wait_for_ssh(self, *, timeout: int = 600, interval: int = 10) -> bool:
        """Poll until SSH becomes available (post-reboot scenario).

        Args:
            timeout: Maximum seconds to wait
            interval: Seconds between connection attempts

        Returns:
            True if SSH became available, False if timed out
        """
        deadline = time.monotonic() + timeout
        attempt = 0

        while time.monotonic() < deadline:
            attempt += 1
            logger.debug("SSH wait attempt %d for %s@%s:%d", attempt, self.username, self.host, self.port)

            if self.is_reachable():
                logger.info("SSH available after %d attempts for %s@%s", attempt, self.username, self.host)
                return True

            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(interval, remaining))

        logger.warning("SSH wait timed out after %ds for %s@%s", timeout, self.username, self.host)
        return False

    def is_reachable(self) -> bool:
        """Quick SSH connectivity check (runs 'true' command)."""
        try:
            result = self.execute("true", timeout=self.connect_timeout + 5)
            return result.exit_code == 0
        except Exception:
            return False


class RemoteSSHSession:
    """Adapter that runs commands on a remote host via an existing SSH session.

    Instead of opening a direct SSH connection to the target (which may require
    complex multi-hop tunneling), this runs commands by SSH-ing from the host
    that's already connected:

        container → host_ssh → "ssh root@dpu_ip '<command>'"

    This eliminates nested ProxyCommand chains and works naturally for
    host-local addresses like rshim (192.168.100.2).

    Implements the same execute()/is_reachable() interface as SSHSession
    so probe functions work without modification.
    """

    def __init__(
        self,
        host_ssh: "SSHSession",
        remote_host: str,
        remote_user: str = "root",
        remote_port: int = 22,
        *,
        remote_password: str | None = None,
        remote_key_path: str | None = None,
    ):
        self.host_ssh = host_ssh
        self.host = remote_host
        self.username = remote_user
        self.port = remote_port
        self.password = remote_password
        self.private_key_path = remote_key_path

    def _wrap_command(self, command: str) -> str:
        """Wrap a command to run on the remote host via the host's SSH.

        Uses SSHPASS env-var mode (sshpass -e) for password auth to avoid
        exposing the password in ``ps`` output on the intermediate host.
        Key auth is passed via ``-i`` when a key path is provided.
        """
        escaped = command.replace("'", "'\\''")

        parts: list[str] = []

        # Password auth via sshpass on the host (env-var mode keeps it out of ps)
        if self.password and not self.private_key_path:
            parts.append(f"SSHPASS='{self.password}' sshpass -e")

        parts.append("ssh")
        parts.append("-o StrictHostKeyChecking=no")
        parts.append("-o UserKnownHostsFile=/dev/null")
        parts.append("-o ConnectTimeout=10")
        parts.append(f"-p {self.port}")

        if self.private_key_path:
            # Key is on the HOST filesystem, not the container
            parts.append(f"-i {self.private_key_path}")

        if self.password and not self.private_key_path:
            parts.append("-o PreferredAuthentications=password")
            parts.append("-o PubkeyAuthentication=no")

        parts.append(f"{self.username}@{self.host}")
        parts.append(f"'{escaped}'")

        return " ".join(parts)

    def execute(self, command: str, *, timeout: int = 300) -> SSHResult:
        """Execute a command on the remote host via the host SSH session."""
        wrapped = self._wrap_command(command)
        return self.host_ssh.execute(wrapped, timeout=timeout)

    def is_reachable(self) -> bool:
        """Check if the remote host is SSH-reachable from the host."""
        try:
            result = self.execute("true", timeout=15)
            return result.exit_code == 0
        except Exception:
            return False
