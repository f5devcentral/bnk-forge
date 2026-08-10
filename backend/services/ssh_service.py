"""
SSH Service
Handles SSH key management, authentication, and remote command execution
"""
import io
import logging

import paramiko
from paramiko import AutoAddPolicy, ECDSAKey, Ed25519Key, RSAKey, SSHClient
from paramiko.ssh_exception import AuthenticationException, BadHostKeyException, NoValidConnectionsError, SSHException

from services.ssh.paramiko_utils import load_private_key_from_content

logger = logging.getLogger(__name__)


class SSHService:
    """Service for SSH key management and remote operations"""

    def __init__(self):
        self.client = None

    def validate_private_key(self, private_key: str, passphrase: str | None = None) -> tuple[bool, str]:
        """
        Validate an SSH private key

        Args:
            private_key: SSH private key content (PEM format)
            passphrase: Optional passphrase for encrypted keys

        Returns:
            tuple: (is_valid, key_type or error_message)
        """
        try:
            key = load_private_key_from_content(private_key, passphrase)
            # Determine key type from class name
            key_type_name = type(key).__name__.replace("Key", "")
            logger.info(f"Valid {key_type_name} private key detected")
            return True, key_type_name

        except paramiko.SSHException:
            return False, "Invalid private key format or incorrect passphrase"
        except Exception as e:
            logger.error(f"Failed to validate private key: {e}")
            return False, str(e)

    def test_connection(
        self,
        host: str,
        username: str,
        port: int = 22,
        auth_type: str = "key",
        password: str | None = None,
        private_key: str | None = None,
        passphrase: str | None = None,
        timeout: int = 10
    ) -> tuple[bool, str]:
        """
        Test SSH connection to a remote host

        Args:
            host: Hostname or IP address
            username: SSH username
            port: SSH port (default: 22)
            auth_type: Authentication type ("password" or "key")
            password: Password for password auth
            private_key: Private key content for key auth
            passphrase: Passphrase for encrypted private key
            timeout: Connection timeout in seconds

        Returns:
            tuple: (success, message)
        """
        try:
            client = SSHClient()
            client.set_missing_host_key_policy(AutoAddPolicy())

            if auth_type == "password":
                if not password:
                    return False, "Password is required for password authentication"

                client.connect(
                    hostname=host,
                    port=port,
                    username=username,
                    password=password,
                    timeout=timeout,
                    look_for_keys=False,
                    allow_agent=False
                )

            elif auth_type == "key":
                if not private_key:
                    return False, "Private key is required for key authentication"

                try:
                    pkey = load_private_key_from_content(private_key, passphrase)
                except paramiko.SSHException:
                    return False, "Failed to load private key"

                client.connect(
                    hostname=host,
                    port=port,
                    username=username,
                    pkey=pkey,
                    timeout=timeout,
                    look_for_keys=False,
                    allow_agent=False
                )

            else:
                return False, f"Invalid auth_type: {auth_type}"

            # Test command execution
            stdin, stdout, stderr = client.exec_command('echo "SSH connection test successful"')
            output = stdout.read().decode('utf-8').strip()

            client.close()

            logger.info(f"SSH connection test successful: {username}@{host}:{port}")
            return True, f"Connection successful. Test output: {output}"

        except AuthenticationException as e:
            logger.error(f"SSH authentication failed: {e}")
            return False, "Authentication failed. Please check credentials."

        except BadHostKeyException as e:
            logger.error(f"Bad host key: {e}")
            return False, "Host key verification failed."

        except NoValidConnectionsError as e:
            logger.error(f"No valid connections: {e}")
            return False, f"Unable to connect to {host}:{port}. Check host and port."

        except SSHException as e:
            logger.error(f"SSH error: {e}")
            return False, f"SSH error: {str(e)}"

        except Exception as e:
            logger.error(f"Connection test failed: {e}")
            return False, f"Connection failed: {str(e)}"

    def execute_command(
        self,
        host: str,
        username: str,
        command: str,
        port: int = 22,
        auth_type: str = "key",
        password: str | None = None,
        private_key: str | None = None,
        passphrase: str | None = None,
        timeout: int = 30
    ) -> dict:
        """
        Execute a command on a remote host via SSH

        Args:
            host: Hostname or IP address
            username: SSH username
            command: Command to execute
            port: SSH port (default: 22)
            auth_type: Authentication type ("password" or "key")
            password: Password for password auth
            private_key: Private key content for key auth
            passphrase: Passphrase for encrypted private key
            timeout: Command timeout in seconds

        Returns:
            dict: Command execution result with stdout, stderr, exit_code
        """
        try:
            client = SSHClient()
            client.set_missing_host_key_policy(AutoAddPolicy())

            # Connect using appropriate auth method
            if auth_type == "password":
                client.connect(
                    hostname=host,
                    port=port,
                    username=username,
                    password=password,
                    timeout=timeout,
                    look_for_keys=False,
                    allow_agent=False
                )

            elif auth_type == "key":
                pkey = load_private_key_from_content(private_key, passphrase)

                client.connect(
                    hostname=host,
                    port=port,
                    username=username,
                    pkey=pkey,
                    timeout=timeout,
                    look_for_keys=False,
                    allow_agent=False
                )

            # Execute command
            stdin, stdout, stderr = client.exec_command(command, timeout=timeout)

            stdout_data = stdout.read().decode('utf-8')
            stderr_data = stderr.read().decode('utf-8')
            exit_code = stdout.channel.recv_exit_status()

            client.close()

            logger.info(f"Command executed successfully on {host}: {command[:50]}...")

            return {
                'success': True,
                'stdout': stdout_data,
                'stderr': stderr_data,
                'exit_code': exit_code
            }

        except Exception as e:
            logger.error(f"Command execution failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'stdout': '',
                'stderr': '',
                'exit_code': -1
            }

    def upload_file(
        self,
        host: str,
        username: str,
        local_path: str,
        remote_path: str,
        port: int = 22,
        auth_type: str = "key",
        password: str | None = None,
        private_key: str | None = None,
        passphrase: str | None = None
    ) -> tuple[bool, str]:
        """
        Upload a file to remote host via SFTP

        Args:
            host: Hostname or IP address
            username: SSH username
            local_path: Local file path
            remote_path: Remote file path
            port: SSH port (default: 22)
            auth_type: Authentication type
            password: Password for password auth
            private_key: Private key for key auth
            passphrase: Passphrase for encrypted key

        Returns:
            tuple: (success, message)
        """
        try:
            # Create SSH client and connect
            transport = paramiko.Transport((host, port))

            if auth_type == "password":
                transport.connect(username=username, password=password)

            elif auth_type == "key":
                pkey = load_private_key_from_content(private_key, passphrase)
                transport.connect(username=username, pkey=pkey)

            # Create SFTP session
            sftp = paramiko.SFTPClient.from_transport(transport)

            # Upload file
            sftp.put(local_path, remote_path)

            sftp.close()
            transport.close()

            logger.info(f"File uploaded successfully: {local_path} -> {remote_path}")
            return True, "File uploaded successfully"

        except Exception as e:
            logger.error(f"File upload failed: {e}")
            return False, f"Upload failed: {str(e)}"

    def download_file(
        self,
        host: str,
        username: str,
        remote_path: str,
        local_path: str,
        port: int = 22,
        auth_type: str = "key",
        password: str | None = None,
        private_key: str | None = None,
        passphrase: str | None = None
    ) -> tuple[bool, str]:
        """
        Download a file from remote host via SFTP

        Args:
            host: Hostname or IP address
            username: SSH username
            remote_path: Remote file path
            local_path: Local file path
            port: SSH port (default: 22)
            auth_type: Authentication type
            password: Password for password auth
            private_key: Private key for key auth
            passphrase: Passphrase for encrypted key

        Returns:
            tuple: (success, message)
        """
        try:
            # Create SSH client and connect
            transport = paramiko.Transport((host, port))

            if auth_type == "password":
                transport.connect(username=username, password=password)

            elif auth_type == "key":
                pkey = load_private_key_from_content(private_key, passphrase)
                transport.connect(username=username, pkey=pkey)

            # Create SFTP session
            sftp = paramiko.SFTPClient.from_transport(transport)

            # Download file
            sftp.get(remote_path, local_path)

            sftp.close()
            transport.close()

            logger.info(f"File downloaded successfully: {remote_path} -> {local_path}")
            return True, "File downloaded successfully"

        except Exception as e:
            logger.error(f"File download failed: {e}")
            return False, f"Download failed: {str(e)}"

    def generate_key_pair(self, key_type: str = "ed25519", bits: int = 4096) -> dict:
        """
        Generate a new SSH key pair

        Args:
            key_type: Key type ("rsa", "ed25519", "ecdsa")
            bits: Key size in bits (for RSA, default: 4096)

        Returns:
            dict: private_key and public_key content
        """
        try:
            if key_type.lower() == "ed25519":
                # paramiko Ed25519Key.generate() doesn't exist in paramiko <3.x
                # Use cryptography library directly
                from cryptography.hazmat.primitives import serialization
                from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

                crypto_key = Ed25519PrivateKey.generate()
                private_key = crypto_key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.OpenSSH,
                    serialization.NoEncryption(),
                ).decode()
                public_key = crypto_key.public_key().public_bytes(
                    serialization.Encoding.OpenSSH,
                    serialization.PublicFormat.OpenSSH,
                ).decode()
            else:
                private_key_file = io.StringIO()

                if key_type.lower() == "rsa":
                    key = RSAKey.generate(bits=bits)
                elif key_type.lower() == "ecdsa":
                    key = ECDSAKey.generate()
                else:
                    raise ValueError(f"Unsupported key type: {key_type}")

                key.write_private_key(private_key_file)
                private_key = private_key_file.getvalue()
                public_key = f"{key.get_name()} {key.get_base64()}"

            logger.info(f"Generated {key_type.upper()} key pair successfully")

            return {
                'private_key': private_key,
                'public_key': public_key,
                'key_type': key_type.upper()
            }

        except Exception as e:
            logger.error(f"Key generation failed: {e}")
            raise Exception(f"Failed to generate key pair: {str(e)}")

    def setup_key_auth(
        self,
        host: str,
        username: str,
        password: str,
        port: int = 22,
        timeout: int = 15,
    ) -> dict:
        """
        Auto-setup key-based auth: generate Ed25519 keypair, SSH in with password,
        install the public key in ~/.ssh/authorized_keys, return the private key.

        The password is used ONCE for bootstrap and should NOT be stored.

        Args:
            host: SSH server hostname or IP
            username: SSH user on the remote host
            password: One-time password for bootstrapping
            port: SSH port (default 22)
            timeout: Connection timeout in seconds

        Returns:
            dict with keys: success, private_key, public_key, message, error
        """
        client = None
        try:
            # 1. Generate Ed25519 keypair
            keypair = self.generate_key_pair(key_type="ed25519")
            public_key = keypair['public_key']
            private_key = keypair['private_key']

            # 2. Connect with password
            client = SSHClient()
            client.set_missing_host_key_policy(AutoAddPolicy())

            try:
                client.connect(
                    hostname=host,
                    port=port,
                    username=username,
                    password=password,
                    timeout=timeout,
                    look_for_keys=False,
                    allow_agent=False,
                )
            except AuthenticationException:
                return {
                    'success': False,
                    'private_key': None,
                    'public_key': None,
                    'message': None,
                    'error': 'password_auth_denied',
                    'error_detail': (
                        "This server does not accept password authentication. "
                        "Please provide your SSH private key instead."
                    ),
                }

            # 3. Install public key in authorized_keys
            # This mirrors what ssh-copy-id does:
            #   - Create ~/.ssh if it doesn't exist (mode 700)
            #   - Create authorized_keys if it doesn't exist (mode 600)
            #   - Append the public key if not already present
            install_cmd = (
                'mkdir -p ~/.ssh && chmod 700 ~/.ssh && '
                'touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && '
                f'grep -qxF "{public_key}" ~/.ssh/authorized_keys || '
                f'echo "{public_key}" >> ~/.ssh/authorized_keys'
            )

            _stdin, stdout, stderr = client.exec_command(install_cmd, timeout=10)
            exit_code = stdout.channel.recv_exit_status()
            stderr_out = stderr.read().decode('utf-8').strip()

            if exit_code != 0:
                logger.error(f"Key install failed on {host}: exit={exit_code}, stderr={stderr_out}")
                return {
                    'success': False,
                    'private_key': None,
                    'public_key': None,
                    'message': None,
                    'error': 'key_install_failed',
                    'error_detail': f"Failed to install public key: {stderr_out or 'unknown error'}",
                }

            client.close()
            client = None

            # 4. Verify key auth works by reconnecting with the new key
            verify_client = SSHClient()
            verify_client.set_missing_host_key_policy(AutoAddPolicy())
            key_file = io.StringIO(private_key)
            pkey = Ed25519Key.from_private_key(key_file)

            try:
                verify_client.connect(
                    hostname=host,
                    port=port,
                    username=username,
                    pkey=pkey,
                    timeout=timeout,
                    look_for_keys=False,
                    allow_agent=False,
                )
                verify_client.close()
            except Exception as verify_err:
                logger.error(f"Key verification failed on {host}: {verify_err}")
                return {
                    'success': False,
                    'private_key': None,
                    'public_key': None,
                    'message': None,
                    'error': 'key_verify_failed',
                    'error_detail': (
                        "Public key was installed but key-based login failed. "
                        "The server may have restrictions on key auth. "
                        "Please provide your SSH private key manually."
                    ),
                }

            logger.info(f"Auto key-setup successful for {username}@{host}:{port}")
            return {
                'success': True,
                'private_key': private_key,
                'public_key': public_key,
                'message': f"Key-based authentication configured for {username}@{host}",
                'error': None,
                'error_detail': None,
            }

        except NoValidConnectionsError:
            return {
                'success': False,
                'private_key': None,
                'public_key': None,
                'message': None,
                'error': 'connection_failed',
                'error_detail': f"Unable to connect to {host}:{port}. Check host and port.",
            }
        except SSHException as e:
            return {
                'success': False,
                'private_key': None,
                'public_key': None,
                'message': None,
                'error': 'ssh_error',
                'error_detail': f"SSH error: {str(e)}",
            }
        except Exception as e:
            logger.error(f"Auto key-setup failed for {host}: {e}")
            return {
                'success': False,
                'private_key': None,
                'public_key': None,
                'message': None,
                'error': 'unexpected_error',
                'error_detail': f"Setup failed: {str(e)}",
            }
        finally:
            if client:
                try:
                    client.close()
                except Exception:
                    pass
