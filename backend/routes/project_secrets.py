"""
Project Secrets API Routes

Endpoints for managing project-level secrets (files and values).
"""

import base64
import io
import json
import logging
import tarfile

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session, selectinload

from core.cache import cache
from core.errors import BadRequestError, NotFoundError, handle_route_errors
from database import get_db
from models import User
from routes.auth import require_project_owner, require_viewer
from services.secrets_service import SecretsService
from services.stack_deployment_service import StackDeploymentService

logger = logging.getLogger(__name__)
router = APIRouter()

SPECIAL_SECRET_NAME_MAP = {
    "cne_pull_secret": "cne_pull_secret",
    "jwt_token": "jwt_token",
}


def _invalidate_secrets_cache(project_id: int):
    """PERF-013: Invalidate required secrets cache when secrets change."""
    cache.delete_pattern(f"project:secrets:required:{project_id}:*")


# Pydantic models for request/response
class ValueSecretCreate(BaseModel):
    name: str
    value: str
    description: str | None = None
    target_module_path: str | None = None
    target_variable_name: str | None = None


class ValueSecretUpdate(BaseModel):
    value: str | None = None
    description: str | None = None
    target_module_path: str | None = None
    target_variable_name: str | None = None


class SecretResponse(BaseModel):
    id: int
    name: str
    description: str | None
    secret_type: str
    target_module_path: str | None
    target_variable_name: str | None
    filename: str | None = None
    file_size: int | None = None
    mime_type: str | None = None
    created_at: str | None
    updated_at: str | None
    last_used_at: str | None


def _validate_jwt_token(value: str) -> tuple[bool, str | None]:
    token = (value or "").strip()
    if not token:
        return False, "JWT token is empty"
    if token.count(".") != 2:
        return False, "JWT must have three dot-separated segments"
    return True, None


def _validate_cne_pull_secret(value: str) -> tuple[bool, str | None]:
    try:
        decoded = base64.b64decode(value.strip()).decode("utf-8")
    except Exception as exc:
        return False, f"cne_pull_secret is not valid base64: {exc}"

    try:
        parsed = json.loads(decoded)
    except Exception as exc:
        return False, f"cne_pull_secret decoded content is not valid JSON: {exc}"

    auths = parsed.get("auths") if isinstance(parsed, dict) else None
    if not isinstance(auths, dict):
        return False, "cne_pull_secret JSON must contain an 'auths' object"

    host_entry = auths.get("repo.f5.com")
    if not isinstance(host_entry, dict):
        return False, "cne_pull_secret auths must include repo.f5.com"
    if not host_entry.get("auth"):
        return False, "cne_pull_secret auths.repo.f5.com.auth is missing"
    return True, None


def _extract_jwt_from_text(raw_text: str) -> str:
    text = (raw_text or "").strip()
    if not text:
        raise BadRequestError("Uploaded JWT payload is empty", code="INVALID_JWT_UPLOAD")

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            for key in ("jwt_token", "jwt", "token"):
                candidate = parsed.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    text = candidate.strip()
                    break
    except Exception:
        pass

    ok, error = _validate_jwt_token(text)
    if not ok:
        raise BadRequestError(
            f"Invalid JWT upload: {error}",
            code="INVALID_JWT_UPLOAD",
        )
    return text


def _extract_cne_pull_secret_from_text(raw_text: str) -> str:
    text = (raw_text or "").strip()
    if not text:
        raise BadRequestError("Uploaded FAR payload is empty", code="INVALID_FAR_UPLOAD")

    def _try_as_docker_json_string(candidate: str) -> str | None:
        try:
            parsed_candidate = json.loads(candidate)
            if isinstance(parsed_candidate, dict) and isinstance(parsed_candidate.get("auths"), dict):
                canonical = json.dumps(parsed_candidate, separators=(",", ":"))
                b64_candidate = base64.b64encode(canonical.encode("utf-8")).decode("utf-8")
                ok_candidate, _ = _validate_cne_pull_secret(b64_candidate)
                if ok_candidate:
                    return b64_candidate
        except Exception:
            return None
        return None

    def _try_as_base64_string(candidate: str) -> str | None:
        ok_candidate, _ = _validate_cne_pull_secret(candidate)
        return candidate if ok_candidate else None

    def _try_as_gcp_service_account_key(candidate: str) -> str | None:
        """Convert a GCP service account JSON key into dockerconfigjson for repo.f5.com.

        FAR credentials from F5 are distributed as GCP service account keys
        (JSON with type field set to service_account).  The Kubernetes Secret
        needs a dockerconfigjson whose auths.repo.f5.com.auth value is
        base64(_json_key:<sa-key-json>).
        """
        try:
            parsed_sa = json.loads(candidate)
            if not isinstance(parsed_sa, dict):
                return None
            if parsed_sa.get("type") != "service_account":
                return None
            auth_value = base64.b64encode(
                ("_json_key:" + candidate).encode("utf-8")
            ).decode("utf-8")
            docker_config = {"auths": {"repo.f5.com": {"auth": auth_value}}}
            canonical = json.dumps(docker_config, separators=(",", ":"))
            b64 = base64.b64encode(canonical.encode("utf-8")).decode("utf-8")
            ok, _ = _validate_cne_pull_secret(b64)
            return b64 if ok else None
        except Exception:
            return None

    def _try_b64_decode_then_sa(candidate: str) -> str | None:
        """If *candidate* is base64-encoded text, decode it and check if the
        decoded content is a GCP service account key or docker config JSON."""
        try:
            decoded = base64.b64decode(candidate).decode("utf-8")
        except Exception:
            return None
        # Decoded is docker config directly?
        result = _try_as_docker_json_string(decoded)
        if result:
            return result
        # Decoded is a GCP SA key?
        result = _try_as_gcp_service_account_key(decoded)
        if result:
            return result
        return None

    def _collect_string_candidates(value: object) -> list[str]:
        out: list[str] = []
        if isinstance(value, str):
            s = value.strip()
            if s:
                out.append(s)
                if "=" in s:
                    # Handle env-style lines, e.g. CNE_PULL_SECRET=...
                    out.append(s.split("=", 1)[1].strip().strip('"').strip("'"))
        elif isinstance(value, dict):
            for v in value.values():
                out.extend(_collect_string_candidates(v))
        elif isinstance(value, list):
            for v in value:
                out.extend(_collect_string_candidates(v))
        return out

    # If plain JSON docker config, canonicalize and base64-encode.
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            # Already a valid docker config with auths?
            if isinstance(parsed.get("auths"), dict):
                canonical = json.dumps(parsed, separators=(",", ":"))
                b64 = base64.b64encode(canonical.encode("utf-8")).decode("utf-8")
                ok, error = _validate_cne_pull_secret(b64)
                if not ok:
                    raise BadRequestError(f"Invalid FAR upload: {error}", code="INVALID_FAR_UPLOAD")
                return b64

            # GCP service account key? Convert to dockerconfigjson.
            sa_result = _try_as_gcp_service_account_key(text)
            if sa_result:
                return sa_result

            for key in ("cne_pull_secret", ".dockerconfigjson"):
                nested = parsed.get(key)
                if isinstance(nested, str) and nested.strip():
                    return _extract_cne_pull_secret_from_text(nested)

            data_obj = parsed.get("data")
            if isinstance(data_obj, dict):
                nested = data_obj.get(".dockerconfigjson")
                if isinstance(nested, str) and nested.strip():
                    return _extract_cne_pull_secret_from_text(nested)

            # Broad fallback: scan any embedded string values for docker JSON/base64 payloads.
            for candidate in _collect_string_candidates(parsed):
                for tryf in (_try_as_docker_json_string, _try_as_gcp_service_account_key, _try_as_base64_string, _try_b64_decode_then_sa):
                    normalized = tryf(candidate)
                    if normalized:
                        return normalized
    except BadRequestError:
        raise
    except Exception:
        pass

    # Text fallback: try raw text as base64 containing a SA key or docker config.
    b64_result = _try_b64_decode_then_sa(text)
    if b64_result:
        return b64_result

    # Text fallback: try to detect payload hidden in line/value wrappers.
    for candidate in _collect_string_candidates(text):
        for tryf in (_try_as_docker_json_string, _try_as_gcp_service_account_key, _try_as_base64_string, _try_b64_decode_then_sa):
            normalized = tryf(candidate)
            if normalized:
                return normalized

    # Fallback: treat as base64 docker config.
    ok, error = _validate_cne_pull_secret(text)
    if not ok:
        raise BadRequestError(
            f"Invalid FAR upload: {error}",
            code="INVALID_FAR_UPLOAD",
        )
    return text


def _extract_texts_from_tarball(content: bytes) -> list[str]:
    try:
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:*") as tar:
            members = [m for m in tar.getmembers() if m.isfile()]
            if not members:
                raise BadRequestError("Uploaded FAR archive contains no files", code="INVALID_FAR_UPLOAD")

            preferred = sorted(
                members,
                key=lambda m: (
                    0 if "cne" in m.name.lower() or "pull" in m.name.lower() else 1,
                    0 if m.name.lower().endswith((".json", ".txt")) else 1,
                    -m.size,
                ),
            )
            texts: list[str] = []
            for member in preferred:
                f = tar.extractfile(member)
                if not f:
                    continue
                data = f.read()
                if data:
                    texts.append(data.decode("utf-8", errors="ignore"))
            if texts:
                return texts
    except BadRequestError:
        raise
    except Exception as exc:
        raise BadRequestError(
            f"Failed to parse FAR archive: {exc}",
            code="INVALID_FAR_UPLOAD",
        ) from exc

    raise BadRequestError("Unable to read FAR content from archive", code="INVALID_FAR_UPLOAD")


def _normalize_special_secret_upload(secret_name: str, filename: str | None, content: bytes) -> str:
    name = secret_name.strip().lower()
    filename_lower = (filename or "").strip().lower()
    raw_text = content.decode("utf-8", errors="ignore")

    if name == "jwt_token":
        return _extract_jwt_from_text(raw_text)

    if name == "cne_pull_secret":
        if filename_lower.endswith((".tgz", ".tar.gz", ".tar")):
            errors: list[str] = []
            for candidate in _extract_texts_from_tarball(content):
                try:
                    return _extract_cne_pull_secret_from_text(candidate)
                except BadRequestError as exc:
                    errors.append(str(exc))
            message = errors[0] if errors else "Unable to read FAR content from archive"
            raise BadRequestError(message, code="INVALID_FAR_UPLOAD")
        return _extract_cne_pull_secret_from_text(raw_text)

    raise BadRequestError(f"Unsupported special secret '{secret_name}'", code="INVALID_SECRET_KIND")


# --- List Secrets ---
@router.get("/api/projects/{project_id}/secrets", dependencies=[Depends(require_viewer)])
@handle_route_errors("list project secrets")
def list_project_secrets(
    project_id: int,
    db: Session = Depends(get_db)
):
    """
    List all secrets for a project.

    Returns metadata only (no sensitive content).
    """
    service = SecretsService(db)
    secrets = service.list_secrets(project_id)
    return {
        "success": True,
        "secrets": secrets,
        "count": len(secrets)
    }


# --- Create File Secret ---
@router.post("/api/projects/{project_id}/secrets/file")
@handle_route_errors("create file secret")
async def create_file_secret(
    project_id: int,
    file: UploadFile = File(...),
    name: str = Form(...),
    description: str | None = Form(None),
    target_module_path: str | None = Form(None),
    target_variable_name: str | None = Form(None),
    user: User = Depends(require_project_owner),
    db: Session = Depends(get_db),
):
    """
    Upload a file secret (credentials, certificates, etc.).

    The file is encrypted and stored in the database.
    At runtime, it's written to the workspace for module execution.
    """
    service = SecretsService(db)
    secret = await service.create_file_secret(
        project_id=project_id,
        name=name,
        file=file,
        description=description,
        target_module_path=target_module_path,
        target_variable_name=target_variable_name,
    )
    db.commit()
    _invalidate_secrets_cache(project_id)
    return {
        "success": True,
        "secret": {
            "id": secret.id,
            "name": secret.name,
            "secret_type": secret.secret_type,
            "filename": secret.filename,
            "file_size": secret.file_size,
        }
    }


# --- Create Value Secret ---
@router.post("/api/projects/{project_id}/secrets/value")
@handle_route_errors("create value secret")
def create_value_secret(
    project_id: int,
    data: ValueSecretCreate,
    user: User = Depends(require_project_owner),
    db: Session = Depends(get_db),
):
    """
    Create a value secret (API key, password, etc.).

    The value is encrypted and stored in the database.
    At runtime, it's injected as a variable.
    """
    service = SecretsService(db)
    secret = service.create_value_secret(
        project_id=project_id,
        name=data.name,
        value=data.value,
        description=data.description,
        target_module_path=data.target_module_path,
        target_variable_name=data.target_variable_name,
    )
    db.commit()
    _invalidate_secrets_cache(project_id)
    return {
        "success": True,
        "secret": {
            "id": secret.id,
            "name": secret.name,
            "secret_type": secret.secret_type,
        }
    }


@router.post("/api/projects/{project_id}/secrets/import")
@handle_route_errors("import special secret")
async def import_special_secret(
    project_id: int,
    secret_name: str = Form(...),
    file: UploadFile = File(...),
    user: User = Depends(require_project_owner),
    db: Session = Depends(get_db),
):
    """
    Import a special project secret from uploaded files.

    Supported secret_name values:
    - cne_pull_secret: accepts .tgz/.tar/.json/.txt payloads and normalizes to base64 docker config
    - jwt_token: accepts .jwt/.txt/.json payloads and extracts token
    """
    normalized_secret = SPECIAL_SECRET_NAME_MAP.get(secret_name.strip().lower())
    if not normalized_secret:
        raise BadRequestError(
            "Unsupported secret_name. Use cne_pull_secret or jwt_token.",
            code="INVALID_SECRET_KIND",
        )

    content = await file.read()
    if not content:
        raise BadRequestError("Uploaded file is empty", code="EMPTY_UPLOAD")

    normalized_value = _normalize_special_secret_upload(normalized_secret, file.filename, content)

    service = SecretsService(db)
    existing = service.get_secret_by_name(project_id, normalized_secret)
    if existing:
        updated = service.update_value_secret(
            project_id=project_id,
            secret_id=existing.id,
            value=normalized_value,
            target_variable_name=normalized_secret,
        )
        secret_id = updated.id
        action = "updated"
    else:
        created = service.create_value_secret(
            project_id=project_id,
            name=normalized_secret,
            value=normalized_value,
            description=(
                "F5 FAR pull secret imported from file"
                if normalized_secret == "cne_pull_secret"
                else "F5 license JWT token imported from file"
            ),
            target_variable_name=normalized_secret,
        )
        secret_id = created.id
        action = "created"

    db.commit()
    _invalidate_secrets_cache(project_id)
    return {
        "success": True,
        "action": action,
        "secret": {
            "id": secret_id,
            "name": normalized_secret,
            "secret_type": "value",
            "target_variable_name": normalized_secret,
        },
    }


# --- Update File Secret ---
@router.put("/api/projects/{project_id}/secrets/{secret_id}/file")
@handle_route_errors("update file secret")
async def update_file_secret(
    project_id: int,
    secret_id: int,
    file: UploadFile | None = File(None),
    description: str | None = Form(None),
    target_module_path: str | None = Form(None),
    target_variable_name: str | None = Form(None),
    user: User = Depends(require_project_owner),
    db: Session = Depends(get_db),
):
    """Update a file secret."""
    service = SecretsService(db)
    secret = await service.update_file_secret(
        project_id=project_id,
        secret_id=secret_id,
        file=file,
        description=description,
        target_module_path=target_module_path,
        target_variable_name=target_variable_name,
    )
    db.commit()
    _invalidate_secrets_cache(project_id)
    return {
        "success": True,
        "secret": {
            "id": secret.id,
            "name": secret.name,
            "filename": secret.filename,
            "file_size": secret.file_size,
        }
    }


# --- Update Value Secret ---
@router.put("/api/projects/{project_id}/secrets/{secret_id}/value")
@handle_route_errors("update value secret")
def update_value_secret(
    project_id: int,
    secret_id: int,
    data: ValueSecretUpdate,
    user: User = Depends(require_project_owner),
    db: Session = Depends(get_db),
):
    """Update a value secret."""
    service = SecretsService(db)
    secret = service.update_value_secret(
        project_id=project_id,
        secret_id=secret_id,
        value=data.value,
        description=data.description,
        target_module_path=data.target_module_path,
        target_variable_name=data.target_variable_name,
    )
    db.commit()
    _invalidate_secrets_cache(project_id)
    return {
        "success": True,
        "secret": {
            "id": secret.id,
            "name": secret.name,
        }
    }


# --- Delete Secret ---
@router.delete("/api/projects/{project_id}/secrets/{secret_id}")
@handle_route_errors("delete secret")
def delete_secret(
    project_id: int,
    secret_id: int,
    user: User = Depends(require_project_owner),
    db: Session = Depends(get_db),
):
    """Delete a secret (soft delete)."""
    service = SecretsService(db)
    success = service.delete_secret(project_id, secret_id)
    if not success:
        raise NotFoundError("secret", secret_id)
    db.commit()
    _invalidate_secrets_cache(project_id)
    return {"success": True}


# --- Update Stack Variable Secret ---
class StackVariableSecretUpdate(BaseModel):
    name: str
    value: str


@router.put("/api/projects/{project_id}/secrets/stack-variable")
@handle_route_errors("update stack variable secret")
def update_stack_variable_secret(
    project_id: int,
    data: StackVariableSecretUpdate,
    user: User = Depends(require_project_owner),
    db: Session = Depends(get_db),
):
    """
    Update a secret value stored in stack instance variables.

    Finds the active stack instance for this project and updates the
    named variable wherever it appears (flat or nested under a module path).
    """
    from models import StackInstance

    stack = (
        db.query(StackInstance)
        .filter(
            StackInstance.project_id == project_id,
            StackInstance.status.notin_(["destroyed"]),
        )
        .order_by(StackInstance.created_at.desc())
        .first()
    )
    if not stack or not stack.variables:
        raise NotFoundError("stack_instance", "active")

    variables = dict(stack.variables)
    updated = False

    # Update flat variable
    if data.name in variables and not isinstance(variables[data.name], dict):
        variables[data.name] = data.value
        updated = True

    # Update nested module-path variables
    for module_path, module_vars in variables.items():
        if isinstance(module_vars, dict) and data.name in module_vars:
            module_vars_copy = dict(module_vars)
            module_vars_copy[data.name] = data.value
            variables[module_path] = module_vars_copy
            updated = True

    if not updated:
        raise NotFoundError("stack_variable", data.name)

    stack.variables = variables
    db.commit()
    _invalidate_secrets_cache(project_id)
    return {"success": True, "name": data.name}


@router.delete("/api/projects/{project_id}/secrets/stack-variable/{name}")
@handle_route_errors("delete stack variable secret")
def delete_stack_variable_secret(
    project_id: int,
    name: str,
    user: User = Depends(require_project_owner),
    db: Session = Depends(get_db),
):
    """
    Remove a secret value from stack instance variables.

    Sets the value to empty string rather than deleting the key,
    so the module input structure is preserved.
    """
    from models import StackInstance

    stack = (
        db.query(StackInstance)
        .filter(
            StackInstance.project_id == project_id,
            StackInstance.status.notin_(["destroyed"]),
        )
        .order_by(StackInstance.created_at.desc())
        .first()
    )
    if not stack or not stack.variables:
        raise NotFoundError("stack_instance", "active")

    variables = dict(stack.variables)
    cleared = False

    # Clear flat variable
    if name in variables and not isinstance(variables[name], dict):
        variables[name] = ""
        cleared = True

    # Clear nested module-path variables
    for module_path, module_vars in variables.items():
        if isinstance(module_vars, dict) and name in module_vars:
            module_vars_copy = dict(module_vars)
            module_vars_copy[name] = ""
            variables[module_path] = module_vars_copy
            cleared = True

    if not cleared:
        raise NotFoundError("stack_variable", name)

    stack.variables = variables
    db.commit()
    _invalidate_secrets_cache(project_id)
    return {"success": True}


# --- Get Required Secrets for Stack ---
@router.get("/api/projects/{project_id}/secrets/required", dependencies=[Depends(require_viewer)])
@handle_route_errors("get required secrets")
def get_required_secrets(
    project_id: int,
    stack_slug: str | None = None,
    db: Session = Depends(get_db)
):
    """
    Get required secrets for a project or stack.

    Analyzes module inputs to determine which secrets are needed.
    Returns which secrets exist vs which are missing.

    PERF-013: Results cached for 60s to reduce database queries.
    """
    from models import ModuleLibrary, Project, ProjectModule, StackInstance, StackTemplate

    # PERF-013: Check cache first
    cache_key = f"project:secrets:required:{project_id}:{stack_slug or 'all'}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    service = SecretsService(db)

    # Get existing secrets for this project
    existing_secrets = service.list_secrets(project_id)
    existing_by_var = {s.get("target_variable_name"): s for s in existing_secrets if s.get("target_variable_name")}

    required_secrets = []
    stack_secret_policy: dict | None = None
    stack_var_names: set[str] = set()  # SEC-001: populated when stack_slug provided

    def _is_satisfied_with_policy(variable_name: str, existing_names: set[str]) -> tuple[bool, str | None]:
        policy_service = StackDeploymentService(db)
        exists, source = policy_service.resolve_secret_source(variable_name, existing_names)
        # SEC-001: Fall back to stack variables
        if not exists and variable_name in stack_var_names:
            return True, "stack_variable"
        return exists, source

    if stack_slug:
        # Get secrets required by a specific stack
        template = db.query(StackTemplate).filter(
            StackTemplate.slug == stack_slug,
            StackTemplate.is_active
        ).first()

        if not template:
            raise NotFoundError("stack_template", stack_slug)

        # SEC-001: Find active stack instance for this project + template to get wizard-provided variables
        active_stack = db.query(StackInstance).filter(
            StackInstance.project_id == project_id,
            StackInstance.template_id == template.id,
            StackInstance.status.notin_(["destroyed"]),
        ).order_by(StackInstance.created_at.desc()).first()
        stack_variables = active_stack.variables if active_stack else None
        # Populate the nonlocal set so _is_satisfied_with_policy can use it
        policy_svc = StackDeploymentService(db)
        stack_var_names.update(policy_svc._extract_variable_names(stack_variables))

        # Batch lookup all modules at once to avoid N+1.
        # D-033: multiple version rows may share a path — iterate so the
        # preferred row (is_latest, newest id) lands last and wins the map,
        # matching the version stack deploy resolves.
        module_paths = [m.get("path") for m in (template.modules or []) if m.get("path")]
        lib_modules = db.query(ModuleLibrary).filter(
            ModuleLibrary.path.in_(module_paths),
            ModuleLibrary.is_active
        ).order_by(ModuleLibrary.is_latest.asc(), ModuleLibrary.id.asc()).all()
        lib_modules_by_path = {m.path: m for m in lib_modules}

        # Keep stack-level secret policy aligned with stack deploy prerequisite checks.
        policy_service = StackDeploymentService(db)
        stack_prereq_check = policy_service.check_prerequisites(
            project_id, template, stack_variables=stack_variables
        )
        stack_secret_policy = stack_prereq_check.get("secret_source_policy")

        existing_names = {s["name"] for s in existing_secrets}

        for module_def in template.modules or []:
            module_path = module_def.get("path")
            lib_module = lib_modules_by_path.get(module_path)

            # pack_manifest matters even when inputs_metadata is empty: a
            # container artifact can require secrets purely via secret_files.
            if lib_module and (lib_module.inputs_metadata or lib_module.pack_manifest):
                module_secrets = service.get_required_secrets_for_module(
                    module_path,
                    lib_module.inputs_metadata or {},
                    pack_manifest=lib_module.pack_manifest,
                )
                for sec in module_secrets:
                    exists, source = _is_satisfied_with_policy(sec["variable_name"], existing_names)
                    sec["exists"] = exists
                    sec["source"] = source
                    if sec["exists"] and source == "project":
                        sec["secret_id"] = existing_by_var[sec["variable_name"]].get("id")
                    if not sec["exists"] and sec["variable_name"] == "cne_pull_secret":
                        sec["guidance"] = (
                            "Configure cne_pull_secret in Project Settings → Secrets. "
                            "A global fallback can be configured in System → Defaults."
                        )
                required_secrets.extend(module_secrets)

        # Truthful stack-scoped contract: include template-required project secrets
        # even if module metadata does not currently mark them as required.
        for prereq in policy_service.get_required_secret_prerequisites(template):
            variable_name = prereq["name"]
            exists, source = _is_satisfied_with_policy(variable_name, existing_names)
            secret_entry = {
                "name": variable_name,
                "type": "value",
                "description": prereq.get("description", ""),
                "module_path": None,
                "variable_name": variable_name,
                "required": True,
                "exists": exists,
                "source": source,
            }
            if exists and source == "project":
                secret_entry["secret_id"] = existing_by_var[variable_name].get("id")
            if not exists and variable_name == "cne_pull_secret":
                secret_entry["guidance"] = (
                    "Configure cne_pull_secret in Project Settings → Secrets. "
                    "A global fallback can be configured in System → Defaults."
                )
            required_secrets.append(secret_entry)
    else:
        # Get secrets required by all modules in project
        # PERF-022: Use selectinload for collections to avoid cartesian products
        # joinedload on one-to-many causes row multiplication; selectinload uses separate IN query
        project = db.query(Project).options(
            selectinload(Project.project_modules).joinedload(
                ProjectModule.library_module
            ).load_only(
                ModuleLibrary.id, ModuleLibrary.path, ModuleLibrary.inputs_metadata,
                ModuleLibrary.pack_manifest,
            )
        ).filter(Project.id == project_id).first()
        if not project:
            raise NotFoundError("project", project_id)

        for pm in project.project_modules:
            if pm.library_module and (
                pm.library_module.inputs_metadata or pm.library_module.pack_manifest
            ):
                module_secrets = service.get_required_secrets_for_module(
                    pm.library_module.path,
                    pm.library_module.inputs_metadata or {},
                    pack_manifest=pm.library_module.pack_manifest,
                )
                for sec in module_secrets:
                    sec["exists"] = sec["variable_name"] in existing_by_var
                    if sec["exists"]:
                        sec["secret_id"] = existing_by_var[sec["variable_name"]].get("id")
                    required_secrets.append(sec)

    # Deduplicate by variable_name while preserving truthful strictness.
    # If any source marks a secret as required (e.g., stack template prereq),
    # keep it required even when module metadata marks it optional.
    unique_by_variable: dict[str, dict] = {}
    for sec in required_secrets:
        variable_name = sec["variable_name"]
        if variable_name not in unique_by_variable:
            unique_by_variable[variable_name] = sec
            continue

        existing = unique_by_variable[variable_name]
        merged = dict(existing)

        # Keep required=true if any source requires it.
        merged["required"] = bool(existing.get("required") or sec.get("required"))

        # Prefer satisfied status when any source is satisfied.
        if not existing.get("exists") and sec.get("exists"):
            merged.update(sec)
        elif existing.get("exists") and not sec.get("exists"):
            pass
        else:
            merged.update(sec)

        unique_by_variable[variable_name] = merged

    unique_secrets = list(unique_by_variable.values())

    missing_count = sum(1 for s in unique_secrets if not s.get("exists"))

    result = {
        "success": True,
        "required_secrets": unique_secrets,
        "total_required": len(unique_secrets),
        "missing_count": missing_count,
        "all_satisfied": missing_count == 0,
        "secret_source_policy": stack_secret_policy,
    }

    # PERF-013: Cache result for 60 seconds
    cache.set(cache_key, result, ttl_seconds=60)
    return result
