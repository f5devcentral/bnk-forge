"""Unit tests for SSHSession — no real SSH needed."""

from unittest.mock import MagicMock, call, patch

import paramiko
import pytest

from services.bare_metal.ssh_session import RemoteSSHSession, SSHResult, SSHSession

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_exec_return(stdout_bytes: bytes, stderr_bytes: bytes, exit_code: int):
    """Return (stdin, stdout, stderr) mocks for client.exec_command()."""
    mock_stdout = MagicMock()
    mock_stdout.read.return_value = stdout_bytes
    mock_stdout.channel.recv_exit_status.return_value = exit_code

    mock_stderr = MagicMock()
    mock_stderr.read.return_value = stderr_bytes

    return MagicMock(), mock_stdout, mock_stderr


# ---------------------------------------------------------------------------
# TestSSHSessionConnection — _connect() builds paramiko client correctly
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestSSHSessionConnection:
    """Test _connect() builds a paramiko client with the correct kwargs."""

    @patch("services.bare_metal.ssh_session.paramiko.SSHClient")
    def test_connect_basic_no_auth(self, mock_ssh_cls):
        """connect() is called with host/port/username and no auth material."""
        mock_client = mock_ssh_cls.return_value
        session = SSHSession(host="10.0.0.1", username="root")
        session._connect()

        mock_client.connect.assert_called_once()
        _, kwargs = mock_client.connect.call_args
        assert kwargs["hostname"] == "10.0.0.1"
        assert kwargs["port"] == 22
        assert kwargs["username"] == "root"
        assert kwargs["look_for_keys"] is False
        assert kwargs["allow_agent"] is False
        assert "pkey" not in kwargs
        assert "password" not in kwargs

    @patch("services.bare_metal.ssh_session.paramiko.SSHClient")
    def test_connect_with_password(self, mock_ssh_cls):
        """Password is passed to connect() when no key is set."""
        mock_client = mock_ssh_cls.return_value
        session = SSHSession(host="10.0.0.1", username="root", password="secret123")
        session._connect()

        _, kwargs = mock_client.connect.call_args
        assert kwargs["password"] == "secret123"
        assert "pkey" not in kwargs

    @patch("services.bare_metal.ssh_session.paramiko.SSHClient")
    @patch("services.bare_metal.ssh_session.load_private_key_from_file")
    def test_connect_with_private_key(self, mock_load_key, mock_ssh_cls):
        """pkey is passed to connect() when private_key_path is set (no passphrase)."""
        mock_client = mock_ssh_cls.return_value
        fake_pkey = MagicMock(spec=paramiko.Ed25519Key)
        mock_load_key.return_value = fake_pkey

        session = SSHSession(host="10.0.0.1", username="root", private_key_path="/tmp/key.pem")
        session._connect()

        mock_load_key.assert_called_once_with("/tmp/key.pem", passphrase=None)
        _, kwargs = mock_client.connect.call_args
        assert kwargs["pkey"] is fake_pkey
        assert "password" not in kwargs

    @patch("services.bare_metal.ssh_session.paramiko.SSHClient")
    @patch("services.bare_metal.ssh_session.load_private_key_from_file")
    def test_connect_key_takes_priority_over_password(self, mock_load_key, mock_ssh_cls):
        """When both key and password are set, key wins — password used as passphrase, not auth."""
        mock_client = mock_ssh_cls.return_value
        fake_pkey = MagicMock(spec=paramiko.RSAKey)
        mock_load_key.return_value = fake_pkey

        session = SSHSession(host="10.0.0.1", username="root",
                             private_key_path="/tmp/key.pem", password="secret")
        session._connect()

        _, kwargs = mock_client.connect.call_args
        assert kwargs["pkey"] is fake_pkey
        assert "password" not in kwargs

    @patch("services.bare_metal.ssh_session.paramiko.SSHClient")
    @patch("services.bare_metal.ssh_session.load_private_key_from_file")
    def test_connect_key_with_passphrase(self, mock_load_key, mock_ssh_cls):
        """When both key and password are set, password is used as key passphrase."""
        mock_load_key.return_value = MagicMock()

        session = SSHSession(host="10.0.0.1", username="root",
                             private_key_path="/tmp/key.pem", password="keypass")
        session._connect()

        mock_load_key.assert_called_once_with("/tmp/key.pem", passphrase="keypass")

    @patch("services.bare_metal.ssh_session.paramiko.SSHClient")
    def test_connect_uses_custom_port(self, mock_ssh_cls):
        """Custom port is forwarded to connect()."""
        mock_client = mock_ssh_cls.return_value
        session = SSHSession(host="10.0.0.1", username="ubuntu", port=2222)
        session._connect()

        _, kwargs = mock_client.connect.call_args
        assert kwargs["port"] == 2222

    @patch("services.bare_metal.ssh_session.paramiko.SSHClient")
    def test_connect_uses_connect_timeout(self, mock_ssh_cls):
        """connect_timeout is forwarded to connect()."""
        mock_client = mock_ssh_cls.return_value
        session = SSHSession(host="10.0.0.1", username="root", connect_timeout=30)
        session._connect()

        _, kwargs = mock_client.connect.call_args
        assert kwargs["timeout"] == 30

    @patch("services.bare_metal.ssh_session.paramiko.SSHClient")
    def test_connect_sets_autoadd_policy(self, mock_ssh_cls):
        """set_missing_host_key_policy is called with AutoAddPolicy."""
        mock_client = mock_ssh_cls.return_value
        session = SSHSession(host="10.0.0.1", username="root")
        session._connect()

        mock_client.set_missing_host_key_policy.assert_called_once()
        policy_arg = mock_client.set_missing_host_key_policy.call_args[0][0]
        assert isinstance(policy_arg, paramiko.AutoAddPolicy)

    @patch("services.bare_metal.ssh_session.paramiko.SSHClient")
    @patch("services.bare_metal.ssh_session.paramiko.Transport")
    @patch("services.bare_metal.ssh_session.load_private_key_from_file")
    def test_connect_single_jumphost_key(self, mock_load_key, mock_transport_cls, mock_ssh_cls):
        """Single key-auth jumphost: transport is opened, channel forwarded to target."""
        mock_client = mock_ssh_cls.return_value
        fake_pkey = MagicMock(spec=paramiko.Ed25519Key)
        mock_load_key.return_value = fake_pkey

        mock_transport = MagicMock()
        mock_transport_cls.return_value = mock_transport
        mock_channel = MagicMock()
        mock_transport.open_channel.return_value = mock_channel

        jumps = [{"host": "jump1.example.com", "username": "admin", "port": 22,
                  "private_key_path": "/tmp/jh.key"}]
        session = SSHSession(host="10.0.0.1", username="root", jumphost_chain=jumps)
        session._connect()

        # Transport created for jumphost
        mock_transport_cls.assert_called_once_with(("jump1.example.com", 22))
        # Connected with pkey
        mock_transport.connect.assert_called_once_with(username="admin", pkey=fake_pkey)
        # Channel opened to final target
        mock_transport.open_channel.assert_called_once_with(
            "direct-tcpip", ("10.0.0.1", 22), ("", 0)
        )
        # Final client.connect() receives the channel as sock
        _, kwargs = mock_client.connect.call_args
        assert kwargs["sock"] is mock_channel

    @patch("services.bare_metal.ssh_session.paramiko.SSHClient")
    @patch("services.bare_metal.ssh_session.paramiko.Transport")
    def test_connect_single_jumphost_password(self, mock_transport_cls, mock_ssh_cls):
        """Single password-auth jumphost connects transport with password."""
        mock_transport = MagicMock()
        mock_transport_cls.return_value = mock_transport
        mock_transport.open_channel.return_value = MagicMock()

        jumps = [{"host": "jump1.example.com", "username": "admin", "port": 22,
                  "password": "jumppass"}]
        session = SSHSession(host="10.0.0.1", username="root", jumphost_chain=jumps)
        session._connect()

        mock_transport.connect.assert_called_once_with(username="admin", password="jumppass")

    @patch("services.bare_metal.ssh_session.paramiko.SSHClient")
    @patch("services.bare_metal.ssh_session.paramiko.Transport")
    def test_connect_two_jumphost_chain(self, mock_transport_cls, mock_ssh_cls):
        """Two-hop chain: second transport is created from first hop's channel."""
        mock_transport1 = MagicMock()
        mock_transport2 = MagicMock()
        mock_transport_cls.side_effect = [mock_transport1, mock_transport2]

        mock_channel1 = MagicMock()
        mock_channel2 = MagicMock()
        mock_transport1.open_channel.return_value = mock_channel1
        mock_transport2.open_channel.return_value = mock_channel2

        jumps = [
            {"host": "bastion.example.com", "username": "admin", "port": 22},
            {"host": "10.0.1.50", "username": "ubuntu", "port": 22},
        ]
        session = SSHSession(host="192.168.100.2", username="root", jumphost_chain=jumps)
        session._connect()

        # First transport uses (host, port) tuple
        assert mock_transport_cls.call_args_list[0] == call(("bastion.example.com", 22))
        # Second transport uses the channel from hop 1
        assert mock_transport_cls.call_args_list[1] == call(mock_channel1)
        # First hop opens channel to second hop
        mock_transport1.open_channel.assert_called_once_with(
            "direct-tcpip", ("10.0.1.50", 22), ("", 0)
        )
        # Second hop opens channel to final target
        mock_transport2.open_channel.assert_called_once_with(
            "direct-tcpip", ("192.168.100.2", 22), ("", 0)
        )
        # client.connect gets second channel as sock
        _, kwargs = mock_ssh_cls.return_value.connect.call_args
        assert kwargs["sock"] is mock_channel2

    @patch("services.bare_metal.ssh_session.paramiko.SSHClient")
    @patch("services.bare_metal.ssh_session.paramiko.Transport")
    def test_connect_cleans_up_transports_on_partial_failure(self, mock_transport_cls, mock_ssh_cls):
        """If the second jumphost fails, the first transport is closed before re-raising."""
        mock_transport1 = MagicMock()
        mock_transport2 = MagicMock()
        # First transport succeeds; second raises on connect()
        mock_transport_cls.side_effect = [mock_transport1, mock_transport2]
        mock_transport1.open_channel.return_value = MagicMock()
        mock_transport2.connect.side_effect = paramiko.AuthenticationException("bad creds hop2")

        jumps = [
            {"host": "bastion.example.com", "username": "admin", "port": 22},
            {"host": "10.0.1.50", "username": "ubuntu", "port": 22},
        ]
        session = SSHSession(host="192.168.100.2", username="root", jumphost_chain=jumps)

        with pytest.raises(paramiko.AuthenticationException):
            session._connect()

        # First transport must have been closed during cleanup
        mock_transport1.close.assert_called_once()
        # Internal list is cleared
        assert session._transports == []

    def test_close_transports_clears_list(self):
        """_close_transports() calls close() on each transport and clears the list."""
        t1 = MagicMock()
        t2 = MagicMock()
        session = SSHSession(host="10.0.0.1", username="root")
        session._transports = [t1, t2]
        session._close_transports()

        t1.close.assert_called_once()
        t2.close.assert_called_once()
        assert session._transports == []

    # NOTE: Key loading tests moved to tests/unit/test_paramiko_utils.py
    # since the logic is now in services.ssh.paramiko_utils

    def test_control_path_stored_but_not_used(self):
        """control_path is accepted for interface compatibility and stored."""
        session = SSHSession(host="10.0.0.1", username="root",
                             control_path="/tmp/ctrl-%r@%h:%p")
        assert session.control_path == "/tmp/ctrl-%r@%h:%p"


# ---------------------------------------------------------------------------
# TestSSHSessionExecute — execute() with mocked paramiko
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestSSHSessionExecute:
    """Test execute() with mocked paramiko.SSHClient."""

    @patch("services.bare_metal.ssh_session.paramiko.SSHClient")
    def test_execute_success(self, mock_ssh_cls):
        mock_client = mock_ssh_cls.return_value
        mock_client.exec_command.return_value = _make_exec_return(
            b"Linux 5.15.0\n", b"", 0
        )
        session = SSHSession(host="10.0.0.1", username="root")
        result = session.execute("uname -r")

        assert result.exit_code == 0
        assert "Linux 5.15.0" in result.stdout
        assert result.duration_seconds >= 0
        mock_client.exec_command.assert_called_once_with("uname -r", timeout=300)

    @patch("services.bare_metal.ssh_session.paramiko.SSHClient")
    def test_execute_failure(self, mock_ssh_cls):
        mock_client = mock_ssh_cls.return_value
        mock_client.exec_command.return_value = _make_exec_return(
            b"", b"command not found", 1
        )
        session = SSHSession(host="10.0.0.1", username="root")
        result = session.execute("bad-command")

        assert result.exit_code == 1
        assert "command not found" in result.stderr

    @patch("services.bare_metal.ssh_session.paramiko.SSHClient")
    def test_execute_connection_error_returns_minus1(self, mock_ssh_cls):
        """Connection errors are caught and returned as exit_code=-1."""
        mock_client = mock_ssh_cls.return_value
        mock_client.connect.side_effect = paramiko.AuthenticationException("bad creds")

        session = SSHSession(host="10.0.0.1", username="root")
        result = session.execute("uname -r")

        assert result.exit_code == -1
        assert "bad creds" in result.stderr

    @patch("services.bare_metal.ssh_session.paramiko.SSHClient")
    def test_execute_passes_timeout(self, mock_ssh_cls):
        mock_client = mock_ssh_cls.return_value
        mock_client.exec_command.return_value = _make_exec_return(b"", b"", 0)

        session = SSHSession(host="10.0.0.1", username="root")
        session.execute("echo hi", timeout=60)

        _, kwargs = mock_client.exec_command.call_args
        assert kwargs["timeout"] == 60

    @patch("services.bare_metal.ssh_session.paramiko.SSHClient")
    def test_execute_returns_duration(self, mock_ssh_cls):
        mock_client = mock_ssh_cls.return_value
        mock_client.exec_command.return_value = _make_exec_return(b"ok", b"", 0)

        session = SSHSession(host="10.0.0.1", username="root")
        result = session.execute("echo ok")

        assert isinstance(result.duration_seconds, float)
        assert result.duration_seconds >= 0

    @patch("services.bare_metal.ssh_session.paramiko.SSHClient")
    def test_execute_decodes_stdout_as_utf8(self, mock_ssh_cls):
        mock_client = mock_ssh_cls.return_value
        mock_client.exec_command.return_value = _make_exec_return(
            "héllo".encode(), b"", 0
        )
        session = SSHSession(host="10.0.0.1", username="root")
        result = session.execute("echo héllo")

        assert "héllo" in result.stdout

    @patch("services.bare_metal.ssh_session.paramiko.SSHClient")
    def test_execute_closes_client_on_success(self, mock_ssh_cls):
        """client.close() is called even on success."""
        mock_client = mock_ssh_cls.return_value
        mock_client.exec_command.return_value = _make_exec_return(b"ok", b"", 0)

        session = SSHSession(host="10.0.0.1", username="root")
        session.execute("echo ok")

        mock_client.close.assert_called_once()

    @patch("services.bare_metal.ssh_session.paramiko.SSHClient")
    def test_execute_closes_client_on_error(self, mock_ssh_cls):
        """client.close() is called even when exec_command raises."""
        mock_client = mock_ssh_cls.return_value
        mock_client.exec_command.side_effect = OSError("network error")

        session = SSHSession(host="10.0.0.1", username="root")
        result = session.execute("echo ok")

        mock_client.close.assert_called_once()
        assert result.exit_code == -1


# ---------------------------------------------------------------------------
# TestSSHSessionStreaming — execute_streaming() with mocked paramiko
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestSSHSessionStreaming:
    """Test execute_streaming() with mocked paramiko channel."""

    def _make_channel(self, lines: list[str], exit_code: int = 0) -> MagicMock:
        """Build a mock paramiko channel that yields lines then exits."""
        channel = MagicMock()

        # Simulate recv_ready: True for each line chunk, then False
        # exit_status_ready: False until all data consumed, then True
        data = "\n".join(lines) + ("\n" if lines else "")
        chunks = [data[i:i+64].encode() for i in range(0, len(data), 64)] or [b""]
        chunks.append(b"")  # sentinel for empty recv

        recv_ready_seq = [True] * len(chunks) + [False]
        channel.recv_ready.side_effect = recv_ready_seq + [False] * 10
        channel.exit_status_ready.side_effect = [False] * len(chunks) + [True] * 10
        channel.recv.side_effect = chunks
        channel.recv_exit_status.return_value = exit_code
        channel.recv_stderr_ready.return_value = False
        return channel

    @patch("services.bare_metal.ssh_session.paramiko.SSHClient")
    def test_streaming_yields_lines(self, mock_ssh_cls):
        mock_client = mock_ssh_cls.return_value
        mock_transport = MagicMock()
        mock_client.get_transport.return_value = mock_transport
        channel = self._make_channel(["line1", "line2", "line3"])
        mock_transport.open_session.return_value = channel

        session = SSHSession(host="10.0.0.1", username="root")
        gen = session.execute_streaming("tail -f /var/log/syslog")
        collected = list(gen)

        assert "line1" in collected
        assert "line2" in collected
        assert "line3" in collected

    @patch("services.bare_metal.ssh_session.paramiko.SSHClient")
    def test_streaming_result_exit_code(self, mock_ssh_cls):
        mock_client = mock_ssh_cls.return_value
        mock_transport = MagicMock()
        mock_client.get_transport.return_value = mock_transport
        channel = self._make_channel(["done"], exit_code=0)
        mock_transport.open_session.return_value = channel

        session = SSHSession(host="10.0.0.1", username="root")
        gen = session.execute_streaming("echo done")
        try:
            while True:
                next(gen)
        except StopIteration as exc:
            result = exc.value

        assert result.exit_code == 0

    @patch("services.bare_metal.ssh_session.paramiko.SSHClient")
    def test_streaming_timeout_returns_result(self, mock_ssh_cls):
        """TimeoutError is caught and returns SSHResult with exit_code=-1."""
        mock_client = mock_ssh_cls.return_value
        mock_client.connect.side_effect = TimeoutError("timed out")

        session = SSHSession(host="10.0.0.1", username="root")
        gen = session.execute_streaming("sleep 999", timeout=1)
        try:
            while True:
                next(gen)
        except StopIteration as exc:
            result = exc.value

        assert result.exit_code == -1
        assert "timed out" in result.stderr or result.stderr != ""


# ---------------------------------------------------------------------------
# TestSSHSessionUpload — upload methods with mocked paramiko SFTP
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestSSHSessionUpload:
    """Test upload_file() and upload_content() with mocked SFTP."""

    @patch("services.bare_metal.ssh_session.paramiko.SSHClient")
    def test_upload_file_success(self, mock_ssh_cls):
        mock_client = mock_ssh_cls.return_value
        mock_sftp = MagicMock()
        mock_client.open_sftp.return_value = mock_sftp
        mock_sftp.get_channel.return_value = MagicMock()

        session = SSHSession(host="10.0.0.1", username="root")
        session.upload_file("/tmp/local.txt", "/tmp/remote.txt")

        mock_sftp.put.assert_called_once_with("/tmp/local.txt", "/tmp/remote.txt")

    @patch("services.bare_metal.ssh_session.paramiko.SSHClient")
    def test_upload_file_failure_raises_runtime_error(self, mock_ssh_cls):
        mock_client = mock_ssh_cls.return_value
        mock_sftp = MagicMock()
        mock_client.open_sftp.return_value = mock_sftp
        mock_sftp.get_channel.return_value = MagicMock()
        mock_sftp.put.side_effect = OSError("Permission denied")

        session = SSHSession(host="10.0.0.1", username="root")
        with pytest.raises(RuntimeError, match="SFTP upload failed"):
            session.upload_file("/tmp/local.txt", "/tmp/remote.txt")

    @patch("services.bare_metal.ssh_session.paramiko.SSHClient")
    def test_upload_file_sftp_closed_after_success(self, mock_ssh_cls):
        mock_client = mock_ssh_cls.return_value
        mock_sftp = MagicMock()
        mock_client.open_sftp.return_value = mock_sftp
        mock_sftp.get_channel.return_value = MagicMock()

        session = SSHSession(host="10.0.0.1", username="root")
        session.upload_file("/local/path", "/remote/path")

        mock_sftp.close.assert_called_once()
        mock_client.close.assert_called_once()

    @patch("services.bare_metal.ssh_session.paramiko.SSHClient")
    def test_upload_content_writes_via_sftp(self, mock_ssh_cls):
        """upload_content() writes content directly to a remote SFTP file."""
        mock_client = mock_ssh_cls.return_value
        mock_sftp = MagicMock()
        mock_client.open_sftp.return_value = mock_sftp
        mock_file = MagicMock()
        mock_sftp.file.return_value.__enter__ = MagicMock(return_value=mock_file)
        mock_sftp.file.return_value.__exit__ = MagicMock(return_value=False)

        session = SSHSession(host="10.0.0.1", username="root")
        session.upload_content("#!/bin/bash\necho hello", "/tmp/script.sh")

        mock_sftp.file.assert_called_once_with("/tmp/script.sh", "w")
        mock_file.write.assert_called_once_with("#!/bin/bash\necho hello")

    @patch("services.bare_metal.ssh_session.paramiko.SSHClient")
    def test_upload_content_with_mode_calls_chmod(self, mock_ssh_cls):
        """upload_content() calls sftp.chmod() when mode is provided."""
        mock_client = mock_ssh_cls.return_value
        mock_sftp = MagicMock()
        mock_client.open_sftp.return_value = mock_sftp
        mock_sftp.file.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_sftp.file.return_value.__exit__ = MagicMock(return_value=False)

        session = SSHSession(host="10.0.0.1", username="root")
        session.upload_content("content", "/tmp/script.sh", mode="755")

        mock_sftp.chmod.assert_called_once_with("/tmp/script.sh", 0o755)

    @patch("services.bare_metal.ssh_session.paramiko.SSHClient")
    def test_upload_content_no_mode_skips_chmod(self, mock_ssh_cls):
        """upload_content() does NOT call sftp.chmod() when mode is None."""
        mock_client = mock_ssh_cls.return_value
        mock_sftp = MagicMock()
        mock_client.open_sftp.return_value = mock_sftp
        mock_sftp.file.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_sftp.file.return_value.__exit__ = MagicMock(return_value=False)

        session = SSHSession(host="10.0.0.1", username="root")
        session.upload_content("data", "/tmp/file.txt")

        mock_sftp.chmod.assert_not_called()

    @patch("services.bare_metal.ssh_session.paramiko.SSHClient")
    def test_upload_content_failure_raises_runtime_error(self, mock_ssh_cls):
        mock_client = mock_ssh_cls.return_value
        mock_sftp = MagicMock()
        mock_client.open_sftp.return_value = mock_sftp
        mock_sftp.file.side_effect = OSError("no space left on device")

        session = SSHSession(host="10.0.0.1", username="root")
        with pytest.raises(RuntimeError, match="SFTP content upload failed"):
            session.upload_content("data", "/tmp/file.txt")

    @patch("services.bare_metal.ssh_session.paramiko.SSHClient")
    def test_upload_file_sets_timeout_on_channel(self, mock_ssh_cls):
        """upload_file() calls settimeout(timeout) on the SFTP channel."""
        mock_client = mock_ssh_cls.return_value
        mock_sftp = MagicMock()
        mock_client.open_sftp.return_value = mock_sftp
        mock_channel = MagicMock()
        mock_sftp.get_channel.return_value = mock_channel

        session = SSHSession(host="10.0.0.1", username="root")
        session.upload_file("/local/path", "/remote/path", timeout=90)

        mock_channel.settimeout.assert_called_once_with(90)


# ---------------------------------------------------------------------------
# TestSSHSessionConnectivity — wait_for_ssh and is_reachable
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestSSHSessionConnectivity:
    """Test wait_for_ssh and is_reachable."""

    @patch.object(SSHSession, "execute")
    def test_is_reachable_true(self, mock_execute):
        mock_execute.return_value = SSHResult(exit_code=0, stdout="", stderr="", duration_seconds=0.1)
        session = SSHSession(host="10.0.0.1", username="root")
        assert session.is_reachable() is True

    @patch.object(SSHSession, "execute")
    def test_is_reachable_false(self, mock_execute):
        mock_execute.return_value = SSHResult(
            exit_code=255, stdout="", stderr="Connection refused", duration_seconds=0.1
        )
        session = SSHSession(host="10.0.0.1", username="root")
        assert session.is_reachable() is False

    @patch.object(SSHSession, "execute")
    def test_is_reachable_exception(self, mock_execute):
        mock_execute.side_effect = Exception("network error")
        session = SSHSession(host="10.0.0.1", username="root")
        assert session.is_reachable() is False

    @patch.object(SSHSession, "is_reachable")
    @patch("services.bare_metal.ssh_session.time.sleep")
    def test_wait_for_ssh_immediate(self, mock_sleep, mock_reachable):
        mock_reachable.return_value = True
        session = SSHSession(host="10.0.0.1", username="root")
        assert session.wait_for_ssh(timeout=30, interval=5) is True
        mock_sleep.assert_not_called()

    @patch.object(SSHSession, "is_reachable")
    @patch("services.bare_metal.ssh_session.time.sleep")
    @patch("services.bare_metal.ssh_session.time.monotonic")
    def test_wait_for_ssh_after_retries(self, mock_monotonic, mock_sleep, mock_reachable):
        # Simulate: starts at t=0, fails twice at t=0 and t=10, succeeds at t=20
        mock_monotonic.side_effect = [0, 0, 10, 10, 20, 20]
        mock_reachable.side_effect = [False, False, True]
        session = SSHSession(host="10.0.0.1", username="root")
        assert session.wait_for_ssh(timeout=60, interval=10) is True

    @patch.object(SSHSession, "is_reachable", return_value=False)
    @patch("services.bare_metal.ssh_session.time.sleep")
    @patch("services.bare_metal.ssh_session.time.monotonic")
    def test_wait_for_ssh_timeout(self, mock_monotonic, mock_sleep, mock_reachable):
        # Simulate timeout: always returns False, time progresses past deadline
        mock_monotonic.side_effect = [0, 0, 10, 10, 20, 20, 30, 30, 40]
        session = SSHSession(host="10.0.0.1", username="root")
        assert session.wait_for_ssh(timeout=30, interval=10) is False

    @patch.object(SSHSession, "execute")
    def test_is_reachable_uses_connect_timeout(self, mock_execute):
        mock_execute.return_value = SSHResult(exit_code=0, stdout="", stderr="", duration_seconds=0.1)
        session = SSHSession(host="10.0.0.1", username="root", connect_timeout=5)
        session.is_reachable()
        mock_execute.assert_called_once_with("true", timeout=10)  # connect_timeout + 5


# ---------------------------------------------------------------------------
# TestRemoteSSHSession — runs commands via host SSH relay
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestRemoteSSHSession:
    """Test RemoteSSHSession — runs commands via host SSH relay."""

    def test_wrap_command_basic(self):
        host_ssh = MagicMock(spec=SSHSession)
        remote = RemoteSSHSession(host_ssh, "192.168.100.2")
        wrapped = remote._wrap_command("uname -r")
        assert "ssh" in wrapped
        assert "root@192.168.100.2" in wrapped
        assert "uname -r" in wrapped
        assert "StrictHostKeyChecking=no" in wrapped

    def test_wrap_command_escapes_quotes(self):
        host_ssh = MagicMock(spec=SSHSession)
        remote = RemoteSSHSession(host_ssh, "192.168.100.2")
        wrapped = remote._wrap_command("echo 'hello world'")
        # Single quotes should be escaped
        assert "hello world" in wrapped

    def test_wrap_command_custom_port(self):
        host_ssh = MagicMock(spec=SSHSession)
        remote = RemoteSSHSession(host_ssh, "192.168.100.2", remote_port=2222)
        wrapped = remote._wrap_command("true")
        assert "-p 2222" in wrapped

    @patch.object(SSHSession, "execute")
    def test_execute_delegates_to_host(self, mock_execute):
        mock_execute.return_value = SSHResult(exit_code=0, stdout="5.15.0\n", stderr="", duration_seconds=0.1)
        host_ssh = SSHSession(host="10.0.0.1", username="ubuntu")
        remote = RemoteSSHSession(host_ssh, "192.168.100.2")
        result = remote.execute("uname -r")

        assert result.exit_code == 0
        assert "5.15.0" in result.stdout
        mock_execute.assert_called_once()
        # The wrapped command should contain the original command
        call_cmd = mock_execute.call_args[0][0]
        assert "uname -r" in call_cmd
        assert "root@192.168.100.2" in call_cmd

    @patch.object(SSHSession, "execute")
    def test_is_reachable_true(self, mock_execute):
        mock_execute.return_value = SSHResult(exit_code=0, stdout="", stderr="", duration_seconds=0.1)
        host_ssh = SSHSession(host="10.0.0.1", username="ubuntu")
        remote = RemoteSSHSession(host_ssh, "192.168.100.2")
        assert remote.is_reachable() is True

    @patch.object(SSHSession, "execute")
    def test_is_reachable_false(self, mock_execute):
        mock_execute.return_value = SSHResult(exit_code=255, stdout="", stderr="Connection refused", duration_seconds=0.1)
        host_ssh = SSHSession(host="10.0.0.1", username="ubuntu")
        remote = RemoteSSHSession(host_ssh, "192.168.100.2")
        assert remote.is_reachable() is False

    def test_wrap_command_with_password(self):
        host_ssh = MagicMock(spec=SSHSession)
        remote = RemoteSSHSession(host_ssh, "192.168.100.2", remote_password="password")
        wrapped = remote._wrap_command("true")
        assert "SSHPASS=" in wrapped
        assert "sshpass -e" in wrapped
        assert "PreferredAuthentications=password" in wrapped

    def test_wrap_command_with_key(self):
        host_ssh = MagicMock(spec=SSHSession)
        remote = RemoteSSHSession(host_ssh, "192.168.100.2", remote_key_path="/home/ubuntu/.ssh/id_ed25519")
        wrapped = remote._wrap_command("true")
        assert "-i /home/ubuntu/.ssh/id_ed25519" in wrapped
        assert "sshpass" not in wrapped

    def test_wrap_command_no_auth(self):
        """No auth args when neither password nor key is set."""
        host_ssh = MagicMock(spec=SSHSession)
        remote = RemoteSSHSession(host_ssh, "192.168.100.2")
        wrapped = remote._wrap_command("true")
        assert "sshpass" not in wrapped
        assert "PreferredAuthentications" not in wrapped

    def test_wrap_command_key_takes_priority_over_password(self):
        """If both are set, key wins — no sshpass in wrapped command."""
        host_ssh = MagicMock(spec=SSHSession)
        remote = RemoteSSHSession(
            host_ssh, "192.168.100.2",
            remote_password="pw",
            remote_key_path="/tmp/key.pem",
        )
        wrapped = remote._wrap_command("true")
        assert "sshpass" not in wrapped
        assert "-i /tmp/key.pem" in wrapped

    def test_wrap_command_password_uses_ubuntu_user(self):
        host_ssh = MagicMock(spec=SSHSession)
        remote = RemoteSSHSession(host_ssh, "192.168.100.2", remote_user="ubuntu", remote_password="password")
        wrapped = remote._wrap_command("true")
        assert "ubuntu@192.168.100.2" in wrapped
