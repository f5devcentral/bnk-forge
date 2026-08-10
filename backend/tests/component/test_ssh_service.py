"""
BC-C59: Component tests for SSHService.

Tests validate_private_key, test_connection, execute_command,
upload_file, download_file, and generate_key_pair.
All paramiko operations are mocked.
"""

import io
from unittest.mock import MagicMock, patch

import pytest

from services.ssh_service import SSHService

# ── Validate Private Key ─────────────────────────────────────────────

class TestValidatePrivateKey:
    @patch("services.ssh_service.load_private_key_from_content")
    def test_valid_rsa_key(self, mock_load):
        # Mock needs __name__ to return "RSAKey" when type().__name__ is called
        fake_key = MagicMock()
        fake_key.__class__.__name__ = "RSAKey"
        mock_load.return_value = fake_key
        svc = SSHService()
        valid, key_type = svc.validate_private_key("-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----")
        assert valid is True
        assert key_type == "RSA"

    @patch("services.ssh_service.load_private_key_from_content")
    def test_valid_ed25519_key(self, mock_load):
        # Mock needs __name__ to return "Ed25519Key" when type().__name__ is called
        fake_key = MagicMock()
        fake_key.__class__.__name__ = "Ed25519Key"
        mock_load.return_value = fake_key
        svc = SSHService()
        valid, key_type = svc.validate_private_key("key-content")
        assert valid is True
        assert key_type == "Ed25519"

    @patch("services.ssh_service.load_private_key_from_content")
    def test_invalid_key(self, mock_load):
        from paramiko.ssh_exception import SSHException
        mock_load.side_effect = SSHException("bad key")
        svc = SSHService()
        valid, msg = svc.validate_private_key("garbage")
        assert valid is False
        assert "Invalid" in msg

    @patch("services.ssh_service.load_private_key_from_content")
    def test_validate_with_passphrase(self, mock_load):
        import paramiko
        fake_key = MagicMock(spec=paramiko.RSAKey)
        mock_load.return_value = fake_key
        svc = SSHService()
        valid, key_type = svc.validate_private_key("key-content", passphrase="secret")
        assert valid is True
        # Verify passphrase was passed
        mock_load.assert_called_once()
        call_kwargs = mock_load.call_args
        assert call_kwargs[1].get("password") == "secret" or call_kwargs[0][-1] == "secret"


# ── Test Connection ──────────────────────────────────────────────────

class TestTestConnection:
    @patch("services.ssh_service.SSHClient")
    def test_password_auth_success(self, mock_client):
        client = mock_client.return_value
        stdout = MagicMock()
        stdout.read.return_value = b"SSH connection test successful"
        client.exec_command.return_value = (MagicMock(), stdout, MagicMock())

        svc = SSHService()
        success, msg = svc.test_connection(
            host="10.0.0.1", username="admin", auth_type="password", password="pass"
        )
        assert success is True
        assert "successful" in msg.lower()
        client.connect.assert_called_once()

    def test_password_auth_missing_password(self):
        svc = SSHService()
        success, msg = svc.test_connection(
            host="10.0.0.1", username="admin", auth_type="password", password=None
        )
        assert success is False
        assert "Password is required" in msg

    @patch("services.ssh_service.load_private_key_from_content")
    @patch("services.ssh_service.SSHClient")
    def test_key_auth_success(self, mock_client, mock_load):
        import paramiko
        mock_load.return_value = MagicMock(spec=paramiko.RSAKey)
        client = mock_client.return_value
        stdout = MagicMock()
        stdout.read.return_value = b"SSH connection test successful"
        client.exec_command.return_value = (MagicMock(), stdout, MagicMock())

        svc = SSHService()
        success, msg = svc.test_connection(
            host="10.0.0.1", username="admin", auth_type="key",
            private_key="-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----"
        )
        assert success is True

    def test_key_auth_missing_key(self):
        svc = SSHService()
        success, msg = svc.test_connection(
            host="10.0.0.1", username="admin", auth_type="key", private_key=None
        )
        assert success is False
        assert "Private key is required" in msg

    def test_invalid_auth_type(self):
        svc = SSHService()
        success, msg = svc.test_connection(
            host="10.0.0.1", username="admin", auth_type="magic"
        )
        assert success is False
        assert "Invalid auth_type" in msg

    @patch("services.ssh_service.SSHClient")
    def test_authentication_failure(self, mock_client):
        from paramiko.ssh_exception import AuthenticationException
        client = mock_client.return_value
        client.connect.side_effect = AuthenticationException("bad creds")

        svc = SSHService()
        success, msg = svc.test_connection(
            host="10.0.0.1", username="admin", auth_type="password", password="wrong"
        )
        assert success is False
        assert "Authentication failed" in msg


# ── Execute Command ──────────────────────────────────────────────────

class TestExecuteCommand:
    @patch("services.ssh_service.load_private_key_from_content")
    @patch("services.ssh_service.SSHClient")
    def test_execute_success(self, mock_client, mock_load):
        import paramiko
        mock_load.return_value = MagicMock(spec=paramiko.RSAKey)
        client = mock_client.return_value
        stdout = MagicMock()
        stdout.read.return_value = b"output data"
        stdout.channel.recv_exit_status.return_value = 0
        stderr = MagicMock()
        stderr.read.return_value = b""
        client.exec_command.return_value = (MagicMock(), stdout, stderr)

        svc = SSHService()
        result = svc.execute_command(
            host="10.0.0.1", username="admin", command="ls -la",
            auth_type="key", private_key="fake-key"
        )
        assert result["success"] is True
        assert result["exit_code"] == 0
        assert "output data" in result["stdout"]

    @patch("services.ssh_service.SSHClient")
    def test_execute_password_auth_success(self, mock_client):
        client = mock_client.return_value
        stdout = MagicMock()
        stdout.read.return_value = b"ok"
        stdout.channel.recv_exit_status.return_value = 0
        stderr = MagicMock()
        stderr.read.return_value = b""
        client.exec_command.return_value = (MagicMock(), stdout, stderr)

        svc = SSHService()
        result = svc.execute_command(
            host="10.0.0.1", username="admin", command="echo hi",
            auth_type="password", password="pass"
        )
        assert result["success"] is True

    @patch("services.ssh_service.SSHClient")
    def test_execute_failure(self, mock_client):
        client = mock_client.return_value
        client.connect.side_effect = Exception("Connection refused")

        svc = SSHService()
        result = svc.execute_command(
            host="10.0.0.1", username="admin", command="ls",
            auth_type="password", password="pass"
        )
        assert result["success"] is False
        assert result["exit_code"] == -1


# ── File Transfer ────────────────────────────────────────────────────

class TestFileTransfer:
    @patch("services.ssh_service.paramiko")
    @patch("services.ssh_service.load_private_key_from_content")
    def test_upload_success(self, mock_load, mock_paramiko):
        import paramiko as real_paramiko
        mock_load.return_value = MagicMock(spec=real_paramiko.RSAKey)
        transport = MagicMock()
        mock_paramiko.Transport.return_value = transport
        sftp = MagicMock()
        mock_paramiko.SFTPClient.from_transport.return_value = sftp

        svc = SSHService()
        success, msg = svc.upload_file(
            host="10.0.0.1", username="admin",
            local_path="/tmp/file.txt", remote_path="/home/admin/file.txt",
            auth_type="key", private_key="fake-key"
        )
        assert success is True
        sftp.put.assert_called_once()

    @patch("services.ssh_service.paramiko")
    @patch("services.ssh_service.load_private_key_from_content")
    def test_download_success(self, mock_load, mock_paramiko):
        import paramiko as real_paramiko
        mock_load.return_value = MagicMock(spec=real_paramiko.RSAKey)
        transport = MagicMock()
        mock_paramiko.Transport.return_value = transport
        sftp = MagicMock()
        mock_paramiko.SFTPClient.from_transport.return_value = sftp

        svc = SSHService()
        success, msg = svc.download_file(
            host="10.0.0.1", username="admin",
            remote_path="/home/admin/file.txt", local_path="/tmp/file.txt",
            auth_type="key", private_key="fake-key"
        )
        assert success is True
        sftp.get.assert_called_once()

    @patch("services.ssh_service.paramiko")
    def test_upload_failure(self, mock_paramiko):
        mock_paramiko.Transport.side_effect = Exception("Connection refused")

        svc = SSHService()
        success, msg = svc.upload_file(
            host="10.0.0.1", username="admin",
            local_path="/tmp/file.txt", remote_path="/remote/file.txt",
            auth_type="password", password="pass"
        )
        assert success is False
        assert "Connection refused" in msg


# ── Generate Key Pair ────────────────────────────────────────────────

class TestGenerateKeyPair:
    @patch("services.ssh_service.RSAKey")
    def test_generate_rsa(self, mock_rsa):
        key = MagicMock()
        key.get_name.return_value = "ssh-rsa"
        key.get_base64.return_value = "AAAA..."
        mock_rsa.generate.return_value = key

        svc = SSHService()
        result = svc.generate_key_pair(key_type="rsa", bits=2048)
        assert result["key_type"] == "RSA"
        assert "private_key" in result
        assert "public_key" in result
        mock_rsa.generate.assert_called_once_with(bits=2048)

    @patch("services.ssh_service.Ed25519Key")
    def test_generate_ed25519(self, mock_ed):
        key = MagicMock()
        key.get_name.return_value = "ssh-ed25519"
        key.get_base64.return_value = "BBBB..."
        mock_ed.generate.return_value = key

        svc = SSHService()
        result = svc.generate_key_pair(key_type="ed25519")
        assert result["key_type"] == "ED25519"

    def test_unsupported_key_type(self):
        svc = SSHService()
        with pytest.raises(Exception, match="Unsupported key type"):
            svc.generate_key_pair(key_type="dsa")
