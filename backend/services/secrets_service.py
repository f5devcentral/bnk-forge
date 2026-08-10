"""
Project Secrets Service

Manages project-level secrets (files and values) for module inputs.
Handles encryption, storage, and runtime mounting/injection.
"""

import base64
import logging
import os
from datetime import UTC, datetime

from fastapi import UploadFile
from sqlalchemy.orm import Session

from core.encryption import decrypt_value, encrypt_value
from models import ProjectSecret

logger = logging.getLogger(__name__)

# Directory for mounting secrets at runtime
SECRETS_MOUNT_DIR = os.getenv("SECRETS_MOUNT_DIR", "/app/project_secrets")

# Sources that forge resolves itself — these inputs must NOT be reported as
# required user-provided project secrets.
#   credential_template — resolved from the bound credential template / TF_VAR
#                         (e.g. aws_access_key_id, aws_secret_access_key, aws_session_token)
#   project             — resolved from project-level settings, not user-supplied secrets
#   module              — wired between modules by the orchestrator
#   auto                — forge auto-injects at runtime (e.g. forge_kubeconfig_content)
CONTEXT_RESOLVED_SECRET_SOURCES: frozenset[str] = frozenset({
    "credential_template",
    "project",
    "module",
    "auto",
})


class SecretsService:
    """Service for managing project secrets."""

    def __init__(self, db: Session):
        self.db = db

    def list_secrets(self, project_id: int, include_values: bool = False) -> list[dict]:
        """
        List all secrets for a project.

        Args:
            project_id: Project ID
            include_values: If True, include decrypted values (use carefully!)

        Returns:
            List of secret metadata (without sensitive content by default)
        """
        secrets = self.db.query(ProjectSecret).filter(
            ProjectSecret.project_id == project_id,
            ProjectSecret.is_active
        ).order_by(ProjectSecret.name).all()

        result = []
        for secret in secrets:
            item = {
                "id": secret.id,
                "name": secret.name,
                "description": secret.description,
                "secret_type": secret.secret_type,
                "target_module_path": secret.target_module_path,
                "target_variable_name": secret.target_variable_name,
                "created_at": secret.created_at.isoformat() if secret.created_at else None,
                "updated_at": secret.updated_at.isoformat() if secret.updated_at else None,
                "last_used_at": secret.last_used_at.isoformat() if secret.last_used_at else None,
            }

            if secret.secret_type == "file":
                item["filename"] = secret.filename
                item["file_size"] = secret.file_size
                item["mime_type"] = secret.mime_type
            elif include_values and secret.secret_type == "value":
                item["value"] = decrypt_value(secret.value_encrypted)

            result.append(item)

        return result

    def get_secret(self, project_id: int, secret_id: int) -> ProjectSecret | None:
        """Get a specific secret by ID."""
        return self.db.query(ProjectSecret).filter(
            ProjectSecret.id == secret_id,
            ProjectSecret.project_id == project_id,
            ProjectSecret.is_active
        ).first()

    def get_secret_by_name(self, project_id: int, name: str) -> ProjectSecret | None:
        """Get a secret by name."""
        return self.db.query(ProjectSecret).filter(
            ProjectSecret.project_id == project_id,
            ProjectSecret.name == name,
            ProjectSecret.is_active
        ).first()

    def _upsert_secret(
        self,
        project_id: int,
        name: str,
        fields: dict,
    ) -> ProjectSecret:
        """
        Create or reactivate a secret.

        If an active secret with the same name exists, raises ValueError.
        If a soft-deleted secret with the same name exists, reactivates it.
        Otherwise creates a new record.

        Args:
            project_id: Project ID
            name: Secret reference name
            fields: Dict of column values to set on the secret

        Returns:
            Created or reactivated ProjectSecret
        """
        # Check for active duplicate
        existing = self.get_secret_by_name(project_id, name)
        if existing:
            raise ValueError(f"Secret with name '{name}' already exists")

        # Check for soft-deleted secret — reactivate it
        inactive = self.db.query(ProjectSecret).filter(
            ProjectSecret.project_id == project_id,
            ProjectSecret.name == name,
            ProjectSecret.is_active == False,  # noqa: E712 — must use == False, not `not col` (Python bool, not SQL)
        ).first()

        if inactive:
            inactive.is_active = True
            for attr, val in fields.items():
                setattr(inactive, attr, val)
            self.db.flush()
            self.db.refresh(inactive)
            logger.info(f"Reactivated secret '{name}' for project {project_id}")
            return inactive

        secret = ProjectSecret(
            project_id=project_id,
            name=name,
            is_active=True,
            **fields,
        )
        self.db.add(secret)
        self.db.flush()
        self.db.refresh(secret)
        logger.info(f"Created secret '{name}' for project {project_id}")
        return secret

    async def create_file_secret(
        self,
        project_id: int,
        name: str,
        file: UploadFile,
        description: str | None = None,
        target_module_path: str | None = None,
        target_variable_name: str | None = None,
    ) -> ProjectSecret:
        """
        Create a file secret from an uploaded file.

        If a soft-deleted secret with the same name exists, it is reactivated
        and updated instead of inserting a new row (avoids unique constraint
        violation on the project_id+name index).
        """
        content = await file.read()
        content_b64 = base64.b64encode(content).decode('utf-8')
        encrypted_content = encrypt_value(content_b64)

        return self._upsert_secret(project_id, name, {
            "secret_type": "file",
            "filename": file.filename,
            "file_content_encrypted": encrypted_content,
            "file_size": len(content),
            "mime_type": file.content_type or "application/octet-stream",
            "description": description,
            "target_module_path": target_module_path,
            "target_variable_name": target_variable_name,
            "value_encrypted": None,
        })

    def create_value_secret(
        self,
        project_id: int,
        name: str,
        value: str,
        description: str | None = None,
        target_module_path: str | None = None,
        target_variable_name: str | None = None,
    ) -> ProjectSecret:
        """
        Create a value secret.

        If a soft-deleted secret with the same name exists, it is reactivated
        and updated instead of inserting a new row (avoids unique constraint
        violation on the project_id+name index).
        """
        return self._upsert_secret(project_id, name, {
            "secret_type": "value",
            "value_encrypted": encrypt_value(value),
            "description": description,
            "target_module_path": target_module_path,
            "target_variable_name": target_variable_name,
            "file_content_encrypted": None,
            "filename": None,
            "file_size": None,
            "mime_type": None,
        })

    async def update_file_secret(
        self,
        project_id: int,
        secret_id: int,
        file: UploadFile | None = None,
        description: str | None = None,
        target_module_path: str | None = None,
        target_variable_name: str | None = None,
    ) -> ProjectSecret:
        """Update a file secret."""
        secret = self.get_secret(project_id, secret_id)
        if not secret:
            raise ValueError("Secret not found")
        if secret.secret_type != "file":
            raise ValueError("Secret is not a file secret")

        if file:
            content = await file.read()
            content_b64 = base64.b64encode(content).decode('utf-8')
            secret.file_content_encrypted = encrypt_value(content_b64)
            secret.filename = file.filename
            secret.file_size = len(content)
            secret.mime_type = file.content_type or "application/octet-stream"

        if description is not None:
            secret.description = description
        if target_module_path is not None:
            secret.target_module_path = target_module_path
        if target_variable_name is not None:
            secret.target_variable_name = target_variable_name

        self.db.flush()
        self.db.refresh(secret)
        return secret

    def update_value_secret(
        self,
        project_id: int,
        secret_id: int,
        value: str | None = None,
        description: str | None = None,
        target_module_path: str | None = None,
        target_variable_name: str | None = None,
    ) -> ProjectSecret:
        """Update a value secret."""
        secret = self.get_secret(project_id, secret_id)
        if not secret:
            raise ValueError("Secret not found")
        if secret.secret_type != "value":
            raise ValueError("Secret is not a value secret")

        if value is not None:
            secret.value_encrypted = encrypt_value(value)
        if description is not None:
            secret.description = description
        if target_module_path is not None:
            secret.target_module_path = target_module_path
        if target_variable_name is not None:
            secret.target_variable_name = target_variable_name

        self.db.flush()
        self.db.refresh(secret)
        return secret

    def delete_secret(self, project_id: int, secret_id: int) -> bool:
        """Soft delete a secret."""
        secret = self.get_secret(project_id, secret_id)
        if not secret:
            return False

        secret.is_active = False
        logger.info(f"Deleted secret '{secret.name}' (ID: {secret_id}) from project {project_id}")
        return True

    def get_decrypted_file_content(self, secret: ProjectSecret) -> bytes:
        """
        Get decrypted file content.

        Args:
            secret: ProjectSecret (must be file type)

        Returns:
            Decrypted file content as bytes
        """
        if secret.secret_type != "file":
            raise ValueError("Secret is not a file secret")

        decrypted_b64 = decrypt_value(secret.file_content_encrypted)
        return base64.b64decode(decrypted_b64)

    def get_decrypted_value(self, secret: ProjectSecret) -> str:
        """
        Get decrypted value.

        Args:
            secret: ProjectSecret (must be value type)

        Returns:
            Decrypted string value
        """
        if secret.secret_type != "value":
            raise ValueError("Secret is not a value secret")

        return decrypt_value(secret.value_encrypted)

    def prepare_secrets_for_execution(
        self,
        project_id: int,
        work_dir: str,
        module_path: str | None = None
    ) -> tuple[dict[str, str], list[str]]:
        """
        Prepare secrets for module execution.

        1. Writes file secrets to work_dir/secrets/
        2. Returns dict of value secrets for variable injection
        3. Returns list of file paths for file secrets

        Only includes secrets that:
        - Have no target_module_path (global secrets), OR
        - Target the specified module_path

        Args:
            project_id: Project ID
            work_dir: Workspace directory for execution
            module_path: Optional module path to filter secrets (e.g., "bnk/far-setup")

        Returns:
            Tuple of (value_secrets_dict, file_paths_list)
        """
        secrets = self.db.query(ProjectSecret).filter(
            ProjectSecret.project_id == project_id,
            ProjectSecret.is_active
        ).all()

        value_secrets = {}
        file_paths = []

        # Create secrets directory in workspace
        secrets_dir = os.path.join(work_dir, "secrets")
        os.makedirs(secrets_dir, exist_ok=True)

        for secret in secrets:
            # Filter: only include secrets that target this module or have no target
            if module_path and secret.target_module_path:
                if secret.target_module_path != module_path:
                    logger.debug(f"Skipping secret '{secret.name}' - targets {secret.target_module_path}, not {module_path}")
                    continue

            if secret.secret_type == "file":
                # Write file to workspace
                file_path = os.path.join(secrets_dir, secret.filename or secret.name)
                content = self.get_decrypted_file_content(secret)
                with open(file_path, 'wb') as f:
                    f.write(content)
                os.chmod(file_path, 0o600)  # Secure permissions

                file_paths.append(file_path)

                # If this secret has a target variable, add the path to value_secrets
                if secret.target_variable_name:
                    value_secrets[secret.target_variable_name] = file_path

                logger.debug(f"Wrote file secret '{secret.name}' to {file_path}")

            elif secret.secret_type == "value":
                # Add to value secrets dict
                if secret.target_variable_name:
                    value_secrets[secret.target_variable_name] = self.get_decrypted_value(secret)
                else:
                    # Use secret name as variable name if no target specified
                    value_secrets[secret.name] = self.get_decrypted_value(secret)

            # Update last_used_at
            secret.last_used_at = datetime.now(UTC)

        logger.info(f"Prepared {len(file_paths)} file secrets and {len(value_secrets)} value secrets for module {module_path}")
        return value_secrets, file_paths

    def get_required_secrets_for_module(
        self,
        module_path: str,
        inputs_metadata: dict,
        pack_manifest: dict | None = None,
    ) -> list[dict]:
        """
        Analyze module inputs to determine required secrets.

        Args:
            module_path: Module path (e.g., "bnk/far-setup")
            inputs_metadata: Module's inputs_metadata from ModuleLibrary
            pack_manifest: Module's pack_manifest (container artifacts declare
                required secrets via the merged `secret_files` block — #442)

        Returns:
            List of required secret definitions
        """
        required_secrets = []
        seen_names: set[str] = set()

        # Container artifacts declare their requirement as secret_files: the
        # engine materializes each named project secret into the run workspace,
        # so the secret is required whether or not it is also an input.
        for entry in (pack_manifest or {}).get("secret_files") or []:
            if not isinstance(entry, dict):
                continue
            name = entry.get("secret_name")
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            required_secrets.append({
                "name": name,
                "type": "file",
                "description": entry.get("description")
                or f"Materialized into the module workspace at {entry.get('path')}",
                "module_path": module_path,
                "variable_name": name,
                "required": True,
            })

        all_inputs = (
            inputs_metadata.get("required", []) +
            inputs_metadata.get("optional", [])
        )

        for inp in all_inputs:
            validation = inp.get("validation", {})
            validation_type = validation.get("type", "")
            inp_source = inp.get("source")
            if inp.get("name") in seen_names:
                # Already required via secret_files — don't report it twice.
                continue

            # Skip inputs whose value forge resolves automatically — they must
            # not be reported as required user-provided project secrets.
            if inp_source in CONTEXT_RESOLVED_SECRET_SOURCES:
                continue

            # Check if this is a file or secret input
            if validation_type == "file_path":
                required_secrets.append({
                    "name": inp.get("name"),
                    "type": "file",
                    "description": inp.get("description"),
                    "module_path": module_path,
                    "variable_name": inp.get("name"),
                    "required": inp in inputs_metadata.get("required", []),
                })
            elif validation_type == "secret" or inp.get("sensitive"):
                required_secrets.append({
                    "name": inp.get("name"),
                    "type": "value",
                    "description": inp.get("description"),
                    "module_path": module_path,
                    "variable_name": inp.get("name"),
                    "required": inp in inputs_metadata.get("required", []),
                })

        return required_secrets
