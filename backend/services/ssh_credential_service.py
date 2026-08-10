"""
SSH Credential Service — CRUD and testing for first-class SSH credentials.

SSH credentials are an *access method*, not a cloud provider.
They can be attached to any Kubernetes cluster (regardless of cloud_provider)
and to projects for default SSH access.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from core.encryption import encrypt_value
from core.errors import BadRequestError, NotFoundError
from models import SSHCredential

logger = logging.getLogger(__name__)


class SSHCredentialService:
    """Service layer for SSH credential operations."""

    def __init__(self, db: Session):
        self.db = db

    # ================================================================
    # Helpers
    # ================================================================

    def _get_credential(self, credential_id: int) -> SSHCredential:
        cred = self.db.query(SSHCredential).filter(
            SSHCredential.id == credential_id
        ).first()
        if not cred:
            raise NotFoundError("ssh_credential", credential_id)
        return cred

    @staticmethod
    def serialize(cred: SSHCredential) -> dict[str, Any]:
        """Build a response dict for an SSH credential (never exposes secrets)."""
        return {
            "id": cred.id,
            "name": cred.name,
            "description": cred.description,
            "host": cred.host,
            "port": cred.port,
            "username": cred.username,
            "auth_type": cred.auth_type,
            "has_password": bool(cred.password_encrypted),
            "has_private_key": bool(cred.private_key_encrypted),
            "is_default": cred.is_default or False,
            "created_at": cred.created_at,
            "updated_at": cred.updated_at,
            "created_by": cred.created_by,
            "clusters_count": len(cred.clusters) if cred.clusters else 0,
            "projects_count": len(cred.projects) if cred.projects else 0,
            "last_test_status": cred.last_test_status,
            "last_test_at": cred.last_test_at,
            "last_test_message": cred.last_test_message,
        }

    # ================================================================
    # CRUD
    # ================================================================

    def list_credentials(self) -> list[dict]:
        """List all SSH credentials."""
        creds = self.db.query(SSHCredential).order_by(
            SSHCredential.is_default.desc(), SSHCredential.name
        ).all()
        return [self.serialize(c) for c in creds]

    def get_credential(self, credential_id: int) -> dict:
        """Get a specific SSH credential by ID."""
        return self.serialize(self._get_credential(credential_id))

    def create_credential(self, data) -> dict:
        """Create a new SSH credential."""
        # Duplicate name check
        existing = self.db.query(SSHCredential).filter(
            SSHCredential.name == data.name
        ).first()
        if existing:
            raise BadRequestError(f"SSH credential '{data.name}' already exists")

        # Default management — only one default
        if data.is_default:
            self.db.query(SSHCredential).filter(
                SSHCredential.is_default.is_(True)
            ).update({"is_default": False})

        cred = SSHCredential(
            name=data.name,
            description=data.description,
            host=data.host,
            port=data.port or 22,
            username=data.username,
            auth_type=data.auth_type or "key",
            is_default=data.is_default or False,
        )

        # Encrypt sensitive fields
        if data.password:
            cred.password_encrypted = encrypt_value(data.password)
        if data.private_key:
            cred.private_key_encrypted = encrypt_value(data.private_key)
        if data.key_passphrase:
            cred.key_passphrase_encrypted = encrypt_value(data.key_passphrase)

        self.db.add(cred)
        self.db.flush()
        self.db.refresh(cred)
        return self.serialize(cred)

    def update_credential(self, credential_id: int, data) -> dict:
        """Update an SSH credential."""
        cred = self._get_credential(credential_id)

        if data.name and data.name != cred.name:
            existing = self.db.query(SSHCredential).filter(
                SSHCredential.name == data.name
            ).first()
            if existing:
                raise BadRequestError(f"SSH credential '{data.name}' already exists")

        if data.is_default and not cred.is_default:
            self.db.query(SSHCredential).filter(
                SSHCredential.is_default.is_(True)
            ).update({"is_default": False})

        update_data = data.model_dump(exclude_unset=True)

        # Handle encrypted fields
        encrypted_map = {
            "password": "password_encrypted",
            "private_key": "private_key_encrypted",
            "key_passphrase": "key_passphrase_encrypted",
        }
        for plain_key, enc_attr in encrypted_map.items():
            if plain_key in update_data and update_data[plain_key]:
                setattr(cred, enc_attr, encrypt_value(update_data.pop(plain_key)))
            else:
                update_data.pop(plain_key, None)

        for key, value in update_data.items():
            if hasattr(cred, key):
                setattr(cred, key, value)

        cred.updated_at = datetime.now(UTC)
        self.db.flush()
        self.db.refresh(cred)
        return self.serialize(cred)

    def delete_credential(self, credential_id: int) -> None:
        """Delete an SSH credential."""
        cred = self._get_credential(credential_id)

        clusters_using = len(cred.clusters) if cred.clusters else 0
        projects_using = len(cred.projects) if cred.projects else 0
        total_using = clusters_using + projects_using

        if total_using > 0:
            parts = []
            if clusters_using:
                parts.append(f"{clusters_using} cluster(s)")
            if projects_using:
                parts.append(f"{projects_using} project(s)")
            raise BadRequestError(
                f"Cannot delete SSH credential. {' and '.join(parts)} are using it. "
                f"Please update them to use a different credential first."
            )

        self.db.delete(cred)

    # ================================================================
    # Auto-Setup (password → key bootstrap)
    # ================================================================

    def setup_credential(self, data) -> dict[str, Any]:
        """Auto-setup: connect with password, generate+install key, save credential.

        The password is used once for bootstrapping and is NOT stored.
        """
        # Duplicate name check
        existing = self.db.query(SSHCredential).filter(
            SSHCredential.name == data.name
        ).first()
        if existing:
            raise BadRequestError(f"SSH credential '{data.name}' already exists")

        from services.ssh_service import SSHService
        ssh_svc = SSHService()

        result = ssh_svc.setup_key_auth(
            host=data.host,
            username=data.username,
            password=data.password,
            port=data.port or 22,
        )

        if not result['success']:
            # Return structured error so frontend can react
            raise BadRequestError(
                result.get('error_detail', 'Auto key setup failed'),
                details={
                    'error_code': result.get('error', 'unknown'),
                    'error_detail': result.get('error_detail', ''),
                },
            )

        # Default management — only one default
        if data.is_default:
            self.db.query(SSHCredential).filter(
                SSHCredential.is_default.is_(True)
            ).update({"is_default": False})

        cred = SSHCredential(
            name=data.name,
            description=data.description,
            host=data.host,
            port=data.port or 22,
            username=data.username,
            auth_type="key",  # always key after auto-setup
            is_default=data.is_default or False,
        )

        # Store ONLY the generated private key — password is discarded
        cred.private_key_encrypted = encrypt_value(result['private_key'])

        self.db.add(cred)
        self.db.flush()
        self.db.refresh(cred)

        logger.info(f"Auto-setup SSH credential '{data.name}' for {data.username}@{data.host}")
        return self.serialize(cred)

    # ================================================================
    # Connection Testing
    # ================================================================

    def test_credential(self, credential_id: int) -> dict[str, Any]:
        """Test SSH connectivity using this credential.

        Persists the outcome on the row (last_test_status / _at / _message)
        so the UI can render a colour-coded status dot without re-testing.
        """
        from datetime import UTC, datetime

        from services.ssh_tunnel_manager import get_tunnel_manager

        cred = self._get_credential(credential_id)
        tunnel_mgr = get_tunnel_manager()
        result = tunnel_mgr.test_ssh_connection(
            ssh_host=cred.host,
            ssh_port=cred.port or 22,
            ssh_username=cred.username,
            ssh_auth_type=cred.auth_type or "password",
            ssh_password_encrypted=cred.password_encrypted,
            ssh_key_encrypted=cred.private_key_encrypted,
            ssh_key_passphrase_encrypted=cred.key_passphrase_encrypted,
        )

        ok = bool(result.get("success"))
        cred.last_test_status = "ok" if ok else "failed"
        cred.last_test_at = datetime.now(UTC)
        cred.last_test_message = (
            result.get("message")
            or result.get("error")
            or result.get("details")
            or None
        )
        self.db.commit()

        return {
            **result,
            "last_test_status": cred.last_test_status,
            "last_test_at": cred.last_test_at.isoformat() if cred.last_test_at else None,
            "last_test_message": cred.last_test_message,
        }

    def test_all_credentials(self) -> dict[str, Any]:
        """Test every SSH credential in parallel (thread-pool, capped).

        Returns a per-credential summary; each row's state is also
        persisted. Runs sequentially if the pool isn't available.
        """
        from concurrent.futures import ThreadPoolExecutor

        from models.ssh_credential import SSHCredential

        creds = self.db.query(SSHCredential).all()
        if not creds:
            return {"tested": 0, "results": []}

        # Credential IDs only — each worker opens its own session because
        # SQLAlchemy Sessions aren't thread-safe.
        ids = [c.id for c in creds]

        def _run(cred_id: int) -> dict[str, Any]:
            from database import SessionLocal
            db = SessionLocal()
            try:
                return {"id": cred_id, **SSHCredentialService(db).test_credential(cred_id)}
            finally:
                db.close()

        results: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=min(8, len(ids))) as pool:
            for outcome in pool.map(_run, ids):
                results.append(outcome)
        return {"tested": len(results), "results": results}

    # ================================================================
    # K8s Probe
    # ================================================================

    def probe_kubeconfig(self, credential_id: int) -> dict[str, Any]:
        """SSH into the host and retrieve the kubeconfig + available contexts.

        Runs ``kubectl config view --raw`` (or reads ``~/.kube/config`` directly)
        on the remote host via the stored SSH credential.  Returns the raw
        kubeconfig YAML and a list of contexts discovered in it.
        """
        import base64
        import io

        import yaml

        from core.encryption import decrypt_value

        cred = self._get_credential(credential_id)

        # Build SSH connection kwargs
        host = cred.host
        port = cred.port or 22
        username = cred.username

        # Decrypt credentials
        password = decrypt_value(cred.password_encrypted) if cred.password_encrypted else None
        private_key = decrypt_value(cred.private_key_encrypted) if cred.private_key_encrypted else None

        # Connect and execute
        import paramiko

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            if private_key:
                # Key-based auth
                key_file = io.StringIO(private_key)
                pkey = None
                for key_class in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
                    try:
                        key_file.seek(0)
                        passphrase = decrypt_value(cred.key_passphrase_encrypted) if cred.key_passphrase_encrypted else None
                        pkey = key_class.from_private_key(key_file, password=passphrase)
                        break
                    except (paramiko.SSHException, ValueError):
                        continue
                if not pkey:
                    return {"success": False, "error": "Failed to load SSH private key"}

                client.connect(
                    hostname=host, port=port, username=username,
                    pkey=pkey, timeout=15,
                    look_for_keys=False, allow_agent=False,
                )
            elif password:
                client.connect(
                    hostname=host, port=port, username=username,
                    password=password, timeout=15,
                    look_for_keys=False, allow_agent=False,
                )
            else:
                return {"success": False, "error": "No SSH credentials available (no key or password)"}

            # Fetch using the flatten ladder (oc → kubectl → raw cat)
            # so that OCP/RHEL kubeconfigs with file-path refs are inlined at source.
            from services.ssh_kubeconfig_fetch import fetch_flattened_kubeconfig_over_ssh

            try:
                kubeconfig_raw = fetch_flattened_kubeconfig_over_ssh(client)
            except ValueError as e:
                return {"success": False, "error": str(e)}

            # Parse contexts from the kubeconfig
            try:
                kc = yaml.safe_load(kubeconfig_raw)
            except yaml.YAMLError as e:
                return {"success": False, "error": f"Failed to parse kubeconfig YAML: {e}"}

            contexts = []
            current_context = kc.get("current-context", "")
            for ctx in kc.get("contexts", []):
                ctx_name = ctx.get("name", "")
                cluster_name = ctx.get("context", {}).get("cluster", "")
                user_name = ctx.get("context", {}).get("user", "")
                # Try to find the API server for this cluster
                api_server = ""
                for cl in kc.get("clusters", []):
                    if cl.get("name") == cluster_name:
                        api_server = cl.get("cluster", {}).get("server", "")
                        break

                contexts.append({
                    "name": ctx_name,
                    "cluster": cluster_name,
                    "user": user_name,
                    "api_server": api_server,
                    "is_current": ctx_name == current_context,
                })

            return {
                "success": True,
                "kubeconfig": base64.b64encode(kubeconfig_raw.encode("utf-8")).decode("ascii"),
                "contexts": contexts,
                "current_context": current_context,
                "host": host,
            }

        except paramiko.AuthenticationException:
            return {"success": False, "error": f"SSH authentication failed for {username}@{host}:{port}"}
        except Exception as e:
            logger.error(f"Kubeconfig probe failed for {host}: {e}")
            return {"success": False, "error": str(e)}
        finally:
            client.close()
