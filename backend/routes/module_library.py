"""
Module Library API Routes
Endpoints for module catalog and library management
"""
import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from requests import exceptions as requests_exceptions
from sqlalchemy import func
from sqlalchemy.orm import Session

from core.cache import cache, invalidate_cache
from core.encryption import decrypt_value_or_none
from core.errors import BadRequestError, ForbiddenError, NotFoundError, handle_route_errors
from database import get_db
from models import ApplicationSetting, CloudCredentialTemplate, ModuleLibrary, Project
from routes.auth import require_operator, require_viewer
from services.defaults_service import get_default
from services.git_auth_service import GitAuthService
from services.module_capabilities import serialize_engine_metadata
from services.module_catalog_service import get_catalog_modules, sync_module_catalog
from services.module_metadata import derive_platform_compatibility
from services.module_version_query import recompute_is_latest
from services.user_module_service import UserModuleService
from services.variable_parser import VariableParser

router = APIRouter(prefix="/api/module-library", tags=["module-library"])
logger = logging.getLogger(__name__)


def _extract_raw_module_compatibility(module: ModuleLibrary) -> tuple[list[str], list[str]]:
    """Extract transitional supported_platforms + required_capabilities metadata."""
    compatibility = (module.inputs_metadata or {}).get("compatibility", {}) if module.inputs_metadata else {}
    supported_platforms = compatibility.get("supported_platforms", [])
    required_capabilities = compatibility.get("required_capabilities", [])

    return (
        [str(item) for item in supported_platforms] if isinstance(supported_platforms, list) else [],
        [str(item) for item in required_capabilities] if isinstance(required_capabilities, list) else [],
    )


def _serialize_module_compatibility(module: ModuleLibrary) -> dict:
    """Return additive canonical compatibility payload for module APIs."""
    supported_platforms, required_capabilities = _extract_raw_module_compatibility(module)
    return derive_platform_compatibility(
        supported_platforms=supported_platforms,
        required_capabilities=required_capabilities,
    )


def _module_variables_from_metadata(module: ModuleLibrary) -> list[dict]:
    """Convert inputs_metadata or variables_schema into frontend variable objects."""
    if module.inputs_metadata:
        variables: list[dict] = []
        for required, group_name in ((True, "required"), (False, "optional")):
            for inp in module.inputs_metadata.get(group_name, []) or []:
                variables.append({
                    "name": inp.get("name"),
                    "type": inp.get("type", "string"),
                    "description": inp.get("description", ""),
                    "required": required,
                    "sensitive": inp.get("sensitive", False),
                    "default": inp.get("default"),
                    "example": inp.get("example"),
                    "source": inp.get("source"),
                    "from_module": inp.get("from_module"),
                    "from_output": inp.get("from_output"),
                })
        return variables

    if module.variables_schema and isinstance(module.variables_schema, list):
        return module.variables_schema

    return []


# Pydantic schemas
class UserModuleCreate(BaseModel):
    """Schema for adding user modules"""
    name: str
    category: str  # 'infra', 'k8s', 'bnk'
    git_url: str  # Full git URL
    git_ref: str = "main"  # Branch or tag
    module_path: str = ""  # Path within repository (e.g., 'modules/vpc')
    provider: str | None = None
    description: str | None = None
    auto_extract_metadata: bool = True


class GitSourceValidation(BaseModel):
    """Schema for git source validation"""
    git_url: str
    git_ref: str = "main"


class MetadataExtraction(BaseModel):
    """Schema for metadata extraction"""
    git_url: str
    git_ref: str = "main"
    module_path: str = ""


class VersionUpdate(BaseModel):
    """Schema for version update"""
    new_version: str


@router.get("", dependencies=[Depends(require_viewer)])
def list_modules(
    category: str | None = None,
    provider: str | None = None,
    official_only: bool = False,
    tested_only: bool = False,
    db: Session = Depends(get_db)
):
    """
    List available modules from the catalog

    Query Parameters:
    - category: Filter by 'infra', 'k8s', or 'bnk'
    - provider: Filter by 'aws', 'azure', 'gcp', 'on-prem'
    - official_only: Only show official BNK-Forge modules
    - tested_only: Only show tested modules
    """
    # Build cache key from query parameters
    cache_key = f"module_library:list:{category or 'all'}:{provider or 'all'}:{official_only}:{tested_only}"

    # Try to get from cache (15 minute TTL - module library rarely changes)
    cached_result = cache.get(cache_key)
    if cached_result is not None:
        return cached_result

    # Cache miss - fetch from database
    modules = get_catalog_modules(
        db,
        category=category,
        provider=provider,
        official_only=official_only,
        tested_only=tested_only
    )

    # Helper to convert inputs_metadata to variables format
    result = {
        "modules": [
            {
                "id": m.id,
                "name": m.name,
                "category": m.category,
                "provider": m.provider,
                "description": m.description,
                "git_source": m.git_source,
                "version": m.version,
                # D-033: catalog path (multiple version rows may share it) and
                # whether this row is the newest version of its (source, path).
                "path": m.path,
                "is_latest": bool(m.is_latest),
                "is_official": m.is_official,
                "is_tested": m.is_tested,
                "test_status": m.test_status,
                "workflow_compatibility": m.workflow_compatibility,
                "dependencies": m.dependencies,
                "variables_schema": m.variables_schema,
                "variables": _module_variables_from_metadata(m),  # Add variables array for frontend
                "tags": m.tags,
                "readme": m.readme,
                "documentation_url": m.git_source.replace("git::", "https://").split("?")[0] if m.git_source and not m.git_source.startswith("builtin://") else None,
                # Additive compatibility normalization (PLATFORM-CONTEXT-005)
                **_serialize_module_compatibility(m),
                # New metadata fields from module.json
                "dependencies_metadata": m.dependencies_metadata,
                "inputs_metadata": m.inputs_metadata,
                "outputs_metadata": m.outputs_metadata,
                "deployment_order": m.deployment_order,
                # Multi-source support (P2-1 / UX-002)
                "module_source_id": m.module_source_id,
                "source_path": m.source_path,
                "latest_version": m.latest_version,
                "update_available": m.update_available,
                # Engine type + built-in status
                **serialize_engine_metadata(
                    m,
                    include_module_classification=True,
                ),
            }
            for m in modules
        ],
        "total": len(modules)
    }

    # Store in cache with 15 minute TTL
    cache.set(cache_key, result, ttl_seconds=900)

    return result


@router.get("/categories", dependencies=[Depends(require_viewer)])
def get_categories(db: Session = Depends(get_db)):
    """
    Get available module categories with counts
    """
    cache_key = "module_library:categories"

    # Try cache first (15 minute TTL)
    cached_result = cache.get(cache_key)
    if cached_result is not None:
        return cached_result

    categories = db.query(
        ModuleLibrary.category,
        func.count(ModuleLibrary.id).label('count')
    ).group_by(
        ModuleLibrary.category
    ).order_by(
        func.count(ModuleLibrary.id).desc()
    ).all()

    result = {
        "categories": [
            {"name": cat, "count": count}
            for cat, count in categories
        ]
    }

    cache.set(cache_key, result, ttl_seconds=900)
    return result


@router.get("/providers", dependencies=[Depends(require_viewer)])
def get_providers(db: Session = Depends(get_db)):
    """
    Get available cloud providers with counts
    """
    cache_key = "module_library:providers"

    # Try cache first (15 minute TTL)
    cached_result = cache.get(cache_key)
    if cached_result is not None:
        return cached_result

    providers = db.query(
        ModuleLibrary.provider,
        func.count(ModuleLibrary.id).label('count')
    ).filter(
        ModuleLibrary.provider.isnot(None)
    ).group_by(
        ModuleLibrary.provider
    ).order_by(
        func.count(ModuleLibrary.id).desc()
    ).all()

    result = {
        "providers": [
            {"name": prov, "count": count}
            for prov, count in providers
        ]
    }

    cache.set(cache_key, result, ttl_seconds=900)
    return result


@router.post("/sync", dependencies=[Depends(require_operator)])
@handle_route_errors("sync module catalog")
def sync_catalog(force: bool = False, db: Session = Depends(get_db)):
    """
    Sync official module catalog from git repositories.

    This route always performs a full sync (force=True internally). The TTL
    throttle in sync_module_catalog applies only to the boot-time service call
    made directly by startup_steps — not to this route. The force query param
    is accepted for backward compatibility but is ignored here.

    Parameters:
    - force: Accepted for backward compat; always treated as True here
    """
    # Manual /sync always forces a full sync regardless of the caller's force param.
    # TTL throttling only applies to the boot-time best-effort step.
    stats = sync_module_catalog(db, force=True)

    db.commit()

    # Invalidate all module library cache after sync
    invalidate_cache("module_library:*")

    return {
        "success": True,
        "message": "Module catalog synced successfully",
        "stats": stats
    }


@router.get("/version", dependencies=[Depends(require_viewer)])
def get_module_library_version(db: Session = Depends(get_db)):
    """
    Get current and latest module library versions.

    Compares the locally synced VERSION with the remote VERSION on GitHub.
    Uses the stored PAT for authentication (private repos).

    Returns:
        synced_version: Currently synced module library version
        latest_version: Latest available version from remote
        update_available: Boolean indicating if an update is available
        last_synced_at: ISO timestamp of last sync
        git_ref: Branch/tag being tracked
    """
    import requests

    result = {
        "synced_version": None,
        "latest_version": None,
        "update_available": False,
        "last_synced_at": None,
        "git_ref": None,
        "error": None
    }

    try:
        # Get current synced version from ApplicationSettings
        synced_version = db.query(ApplicationSetting).filter(
            ApplicationSetting.key == "module_library.synced_version"
        ).first()
        if synced_version and synced_version.value:
            result["synced_version"] = synced_version.value

        # Get last synced timestamp
        last_synced = db.query(ApplicationSetting).filter(
            ApplicationSetting.key == "module_library.last_synced_at"
        ).first()
        if last_synced and last_synced.value:
            result["last_synced_at"] = last_synced.value

        # Get git ref (branch)
        git_ref_setting = db.query(ApplicationSetting).filter(
            ApplicationSetting.key == "module_library.git_ref"
        ).first()
        git_ref = git_ref_setting.value if git_ref_setting and git_ref_setting.value else "main"
        result["git_ref"] = git_ref

        # Get git URL to construct raw URL
        git_url_setting = db.query(ApplicationSetting).filter(
            ApplicationSetting.key == "module_library.git_url"
        ).first()

        if not git_url_setting or not git_url_setting.value:
            result["error"] = "Module library URL not configured"
            return result

        # Extract owner/repo from URL (e.g. https://github.com/JLCode-tech/bnk-forge-modules.git)
        git_url = git_url_setting.value
        # Parse: https://github.com/{owner}/{repo}.git
        parts = git_url.rstrip('/').rstrip('.git').split('/')
        if len(parts) >= 2:
            owner = parts[-2]
            repo = parts[-1]
        else:
            result["error"] = "Could not parse repository URL"
            return result

        auth_ctx = GitAuthService.resolve_for_module_library_token_setting(db)

        # Fetch latest VERSION from remote
        raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{git_ref}/VERSION"
        headers = GitAuthService.build_http_headers(auth_ctx, raw_url)

        response = requests.get(raw_url, headers=headers, timeout=10)

        if response.status_code == 200:
            result["latest_version"] = response.text.strip()

            # Compare versions
            if result["synced_version"] and result["latest_version"]:
                result["update_available"] = result["synced_version"] != result["latest_version"]
        else:
            error_kind, guidance = GitAuthService.classify_http_failure(response.status_code, response.text)
            if error_kind == "repo_or_ref_not_found":
                result["error"] = "VERSION file not found in remote repository at the configured branch/ref"
            else:
                result["error"] = f"Version check failed [{error_kind}] {guidance}"

    except requests_exceptions.Timeout:
        result["error"] = "Version check failed [connectivity_dns] Remote request timed out"
    except requests_exceptions.SSLError:
        result["error"] = "Version check failed [tls_trust] TLS trust validation failed"
    except requests_exceptions.ConnectionError as e:
        text = GitAuthService.sanitize_error_text(str(e))
        error_kind, guidance = GitAuthService.classify_git_failure(text)
        result["error"] = f"Version check failed [{error_kind}] {guidance}"

    except Exception as e:
        sanitized = GitAuthService.sanitize_error_text(str(e), secrets=[auth_ctx.secret if 'auth_ctx' in locals() else None])
        logger.error(f"Failed to check module library version: {sanitized}")
        result["error"] = sanitized

    return result


@router.post("/user-modules/validate", dependencies=[Depends(require_operator)])
def validate_git_source(request: GitSourceValidation, db: Session = Depends(get_db)):
    """
    Validate a git source before adding it as a module

    Checks:
    - Git repository is accessible
    - Contains Terraform files
    - Lists available branches and tags
    """
    service = UserModuleService(db)
    result = service.validate_git_source(request.git_url, request.git_ref)

    if not result['valid']:
        return {
            'success': False,
            'error': result['error'],
            'available_refs': result.get('available_refs', [])
        }

    return {
        'success': True,
        'message': 'Git source is valid',
        'terraform_files': result['terraform_files'],
        'available_refs': result['available_refs']
    }


@router.post("/user-modules/extract-metadata", dependencies=[Depends(require_operator)])
def extract_module_metadata(request: MetadataExtraction, db: Session = Depends(get_db)):
    """
    Extract metadata from a Terraform module without adding it to the catalog

    Returns:
    - Variables schema
    - Dependencies
    - Provider information
    - Description from README
    """
    service = UserModuleService(db)
    result = service.extract_module_metadata(
        request.git_url,
        request.git_ref,
        request.module_path
    )

    if not result['success']:
        raise BadRequestError(result['error'])

    return {
        'success': True,
        'metadata': {
            'variables': result['variables_schema'],
            'outputs': result['outputs_schema'],
            'dependencies': result['dependencies'],
            'provider': result['provider'],
            'description': result['description'],
            'terraform_version': result['terraform_version']
        }
    }


@router.post("/user-modules", dependencies=[Depends(require_operator)])
@handle_route_errors("save user module")
def create_user_module(module: UserModuleCreate, db: Session = Depends(get_db)):
    """
    Add a user-provided custom module to the catalog with validation and metadata extraction

    Features:
    - Validates git source is accessible
    - Automatically extracts variables, dependencies, and provider info
    - Parses README for description
    - Checks for duplicate modules
    """
    service = UserModuleService(db)
    result = service.create_user_module(
        name=module.name,
        category=module.category,
        git_url=module.git_url,
        git_ref=module.git_ref,
        module_path=module.module_path,
        provider=module.provider,
        description=module.description,
        auto_extract_metadata=module.auto_extract_metadata
    )

    if not result['success']:
        raise BadRequestError(result['error'])

    db.commit()

    return {
        'success': True,
        'message': f"User module '{module.name}' added successfully",
        'module': result['module']
    }


@router.get("/user-modules/{module_id}/versions", dependencies=[Depends(require_viewer)])
def list_module_versions(module_id: int, db: Session = Depends(get_db)):
    """
    List available versions (branches and tags) for a user module
    """
    module = db.query(ModuleLibrary).filter(
        ModuleLibrary.id == module_id,
        ModuleLibrary.is_official.is_(False)
    ).first()

    if not module:
        raise NotFoundError("module", module_id)

    # Extract git URL from git_source
    # Format: git::https://github.com/user/repo.git//path?ref=version
    # Remove 'git::' prefix first
    source_without_prefix = module.git_source.replace('git::', '', 1)

    # Now format is: https://github.com/user/repo.git//path?ref=version or https://github.com/user/repo.git?ref=version
    # Find the path separator '//' that's NOT part of the protocol
    # Look for '//' that comes after '://'
    parts = source_without_prefix.split('://', 1)  # Split protocol
    if len(parts) == 2:
        protocol, rest = parts  # protocol='https', rest='github.com/user/repo.git//path?ref=version'
        if '//' in rest:
            # Has path separator
            url_parts = rest.split('//', 1)
            git_url = f"{protocol}://{url_parts[0]}"  # https://github.com/user/repo.git
        else:
            # No path separator
            git_url = f"{protocol}://{rest.split('?')[0]}"  # https://github.com/user/repo.git
    else:
        # Fallback
        git_url = source_without_prefix.split('?')[0]

    service = UserModuleService(db)
    result = service.list_module_versions(git_url)

    if not result['success']:
        raise BadRequestError(result['error'])

    return {
        'success': True,
        'current_version': module.version,
        'branches': result['branches'],
        'tags': result['tags']
    }


@router.put("/user-modules/{module_id}/version", dependencies=[Depends(require_operator)])
@handle_route_errors("save module version update")
def update_module_version(module_id: int, request: VersionUpdate, db: Session = Depends(get_db)):
    """
    Update a user module to a different version (branch or tag)

    Automatically re-extracts metadata from the new version
    """
    service = UserModuleService(db)
    result = service.update_module_version(module_id, request.new_version)

    if not result['success']:
        raise BadRequestError(result['error'])

    db.commit()

    return {
        'success': True,
        'message': f"Module updated to version {request.new_version}"
    }


@router.delete("/user-modules/{module_id}", dependencies=[Depends(require_operator)])
@handle_route_errors("delete user module")
def delete_user_module(module_id: int, db: Session = Depends(get_db)):
    """
    Delete a user module from the catalog

    Only user modules (non-official) can be deleted
    """
    module = db.query(ModuleLibrary).filter(
        ModuleLibrary.id == module_id
    ).first()

    if not module:
        raise NotFoundError("module", module_id)

    if module.is_official:
        raise ForbiddenError("Cannot delete official modules")

    # Check if module is used in any projects
    if module.project_modules and len(module.project_modules) > 0:
        return {
            'success': False,
            'error': f'Module is used in {len(module.project_modules)} project(s). Remove from projects first.'
        }

    deleted_name = module.name
    deleted_source_id = module.module_source_id
    deleted_path = module.path
    db.delete(module)
    db.flush()
    # D-033: if the deleted row held is_latest, hand the flag to the newest
    # remaining active version so the path doesn't vanish from latest views.
    if deleted_path:
        recompute_is_latest(db, deleted_source_id, deleted_path)
    db.commit()
    invalidate_cache("module_library:*")

    return {
        'success': True,
        'message': f'Module "{deleted_name}" deleted successfully'
    }


@router.get("/{module_id}", dependencies=[Depends(require_viewer)])
def get_module(module_id: int, db: Session = Depends(get_db)):
    """Get detailed information about a specific module"""
    module = db.query(ModuleLibrary).filter(ModuleLibrary.id == module_id).first()

    if not module:
        raise NotFoundError("module", module_id)

    # Determine engine metadata from stored catalog values
    engine_metadata = serialize_engine_metadata(
        module,
        include_module_classification=True,
    )

    return {
        "id": module.id,
        "name": module.name,
        "category": module.category,
        "provider": module.provider,
        "description": module.description,
        "readme": module.readme,
        "git_source": module.git_source,
        "version": module.version,
        "is_official": module.is_official,
        "is_tested": module.is_tested,
        "test_status": module.test_status,
        "test_last_run": module.test_last_run.isoformat() if module.test_last_run else None,
        "test_error": module.test_error,
        "workflow_compatibility": module.workflow_compatibility,
        "dependencies": module.dependencies,
        "variables_schema": module.variables_schema,
        "tags": module.tags,
        "documentation_url": module.git_source.replace("git::", "https://").split("?")[0] if module.git_source and not module.git_source.startswith("builtin://") else None,
        # Additive compatibility normalization (PLATFORM-CONTEXT-005)
        **_serialize_module_compatibility(module),
        "last_synced": module.last_synced.isoformat() if module.last_synced else None,
        "created_at": module.created_at.isoformat(),
        "updated_at": module.updated_at.isoformat(),
        # Engine type + built-in status
        **engine_metadata,
    }


@router.get("/{module_id}/variables", dependencies=[Depends(require_viewer)])
def get_module_variables(module_id: int, db: Session = Depends(get_db)):
    """
    Get variable definitions for a module

    First checks if variables_schema is already stored in database.
    If not, attempts to parse from git source.

    Returns:
    {
        "success": True,
        "variables": [
            {
                "name": "vpc_cidr",
                "type": "string",
                "description": "CIDR block for VPC",
                "default": "10.0.0.0/16",
                "required": False,
                "sensitive": False
            },
            ...
        ],
        "source": "database" or "parsed"
    }
    """
    module = db.query(ModuleLibrary).filter(ModuleLibrary.id == module_id).first()

    if not module:
        raise NotFoundError("module", module_id)

    metadata_variables = _module_variables_from_metadata(module)
    if metadata_variables:
        # Filter out platform-injected variables (project_name, environment, common_tags)
        # These are auto-injected by OpenTofuRuntime and shouldn't be editable by users
        # Platform-injected variables that are always auto-injected
        platform_vars = {'project_name', 'environment', 'common_tags'}
        # region is only auto-injected for AWS projects — don't hide it globally
        filtered_variables = [
            v for v in metadata_variables
            if v.get('name') not in platform_vars
        ]

        return {
            "success": True,
            "variables": filtered_variables,
            "source": "database"
        }

    # Parse from git source
    if not module.git_source:
        return {
            "success": False,
            "error": "Module has no git source defined",
            "variables": []
        }

    auth_ctx = GitAuthService.resolve_for_module_library_token_setting(db)

    # Extract module path from git source automatically
    # Pass empty string to let parser extract path from git_source
    # (git_source format: git::url//path?ref=version)
    module_path = ""

    success, variables, error = VariableParser.clone_and_parse_git_module(
        module.git_source,
        module_path,
        auth_context=auth_ctx,
    )

    if not success:
        return {
            "success": False,
            "error": f"Failed to parse variables: {error}",
            "variables": []
        }

    # Cache the parsed variables
    try:
        module.variables_schema = variables
        db.commit()
    except Exception as e:
        db.rollback()
        # Non-critical error
        logger.warning(f"Failed to cache variables schema: {e}")

    return {
        "success": True,
        "variables": variables,
        "source": "parsed"
    }

@router.get("/{module_id}/smart-defaults", dependencies=[Depends(require_viewer)])
def get_smart_defaults(
    module_id: int,
    project_id: int,
    db: Session = Depends(get_db)
):
    """
    Generate smart default values for a module based on project context.

    This endpoint provides intelligent defaults for module variables:
    - Names: auto-generated as {project-name}-{module-name}
    - CIDRs: suggested values based on common patterns
    - Booleans: sensible defaults
    - Other values: example values or computed defaults

    Args:
        module_id: Module library ID
        project_id: Target project ID (for context)

    Returns:
        Dict of variable_name -> default_value
    """
    # Get module
    module = db.query(ModuleLibrary).filter(ModuleLibrary.id == module_id).first()
    if not module:
        raise NotFoundError("module", module_id)

    # Get project
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise NotFoundError("project", project_id)

    # Generate smart defaults
    defaults = {}
    credential_template = None
    if project.credential_template_id:
        credential_template = db.query(CloudCredentialTemplate).filter(
            CloudCredentialTemplate.id == project.credential_template_id
        ).first()

    # Project name normalized (lowercase, replace spaces/underscores with hyphens)
    project_slug = project.name.lower().replace(" ", "-").replace("_", "-")
    module_name = module.name.lower()

    # Common name pattern: {project}-{module}
    resource_name = f"{project_slug}-{module_name}"

    if module.inputs_metadata:
        all_inputs = (
            module.inputs_metadata.get("required", []) +
            module.inputs_metadata.get("optional", [])
        )

        for inp in all_inputs:
            var_name = inp.get("name")
            var_type = inp.get("type", "string")
            source = inp.get("source")

            # Skip auto-wired variables (from dependencies)
            if source == "module" or source == "project":
                continue

            # Project/template-scoped inheritance comes before generic heuristics.
            if project.cloud_provider == "ibm":
                if var_name == "ibmcloud_cluster_region" and project.region:
                    defaults[var_name] = project.region
                    continue
                if var_name == "ibmcloud_resource_group" and credential_template and credential_template.ibmcloud_resource_group:
                    defaults[var_name] = credential_template.ibmcloud_resource_group
                    continue
                if var_name == "ibmcloud_api_key" and credential_template and credential_template.ibmcloud_api_key_encrypted:
                    decrypted_api_key = decrypt_value_or_none(credential_template.ibmcloud_api_key_encrypted)
                    if decrypted_api_key:
                        defaults[var_name] = decrypted_api_key
                        continue

            # Skip if already has a default
            if inp.get("default") is not None:
                defaults[var_name] = inp.get("default")
                continue

            # Generate smart defaults based on variable name patterns
            var_lower = var_name.lower()

            # CIDR blocks - check this FIRST (before name checks)
            if "cidr" in var_lower:
                if "vpc" in var_lower:
                    defaults[var_name] = "10.0.0.0/16"
                elif "public" in var_lower and "subnet" in var_lower:
                    defaults[var_name] = "10.0.1.0/24"
                elif "private" in var_lower and "external" in var_lower:
                    # Check for suffix, not just substring (avoid matching 'a' in 'external')
                    if var_name.endswith("_a_cidr") or var_name.endswith("_1_cidr") or "_a" in var_name.split("_")[-2:]:
                        defaults[var_name] = "10.0.10.0/24"
                    else:
                        defaults[var_name] = "10.0.11.0/24"
                elif "private" in var_lower and "internal" in var_lower:
                    # Check for suffix, not just substring (avoid matching 'a' in 'internal')
                    if var_name.endswith("_a_cidr") or var_name.endswith("_1_cidr") or "_a" in var_name.split("_")[-2:]:
                        defaults[var_name] = "10.0.20.0/24"
                    else:
                        defaults[var_name] = "10.0.21.0/24"
                else:
                    # Generic CIDR
                    defaults[var_name] = inp.get("example", "10.0.0.0/24")

            # Name fields - check for actual name fields (not things that contain "id" like "cidr")
            elif var_type == "string" and (var_lower.endswith("_name") or var_lower.endswith("_identifier") or var_lower == "name" or var_lower == "identifier"):
                # Special case: cluster_name, vpc_name, etc.
                if "cluster" in var_lower:
                    defaults[var_name] = f"{project_slug}-cluster"
                else:
                    defaults[var_name] = resource_name

            # Boolean fields - default to false unless it's an enable/create field
            elif var_type in ["bool", "boolean"]:
                if any(x in var_lower for x in ["enable", "create", "allow"]):
                    defaults[var_name] = True
                else:
                    defaults[var_name] = False

            # Number fields
            elif var_type == "number":
                if "count" in var_lower or "size" in var_lower:
                    defaults[var_name] = 2  # Default to 2 for counts/sizes
                elif "port" in var_lower:
                    defaults[var_name] = 443  # Default HTTPS port
                else:
                    defaults[var_name] = inp.get("example", 1)

            # String fields - use example if available
            elif var_type == "string":
                if "version" in var_lower:
                    defaults[var_name] = inp.get("example", "latest")
                elif "region" in var_lower and project.cloud_provider == "aws":
                    defaults[var_name] = project.region or get_default(db, "cloud.aws.default_region")
                else:
                    defaults[var_name] = inp.get("example", "")

            # List/Map - use example or empty
            elif var_type in ["list", "map"]:
                defaults[var_name] = inp.get("example", inp.get("default"))

    logger.info(f"Generated {len(defaults)} smart defaults for module {module.name} in project {project.name}")

    return {
        "success": True,
        "defaults": defaults,
        "project_name": project.name,
        "module_name": module.name
    }
