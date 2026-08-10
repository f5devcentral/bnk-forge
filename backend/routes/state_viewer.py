"""
Terraform State Viewer API Routes
Provides endpoints for parsing and viewing Terraform state files.

Supports:
- Standard state files (Terraform/OpenTofu)
- Encrypted state files (OpenTofu 1.8+ native encryption)
"""

import logging
import os

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse  # Used for specific response handling
from sqlalchemy.orm import Session

# Configure logging
logger = logging.getLogger(__name__)

# Import cache for performance optimization (PERF-017)
# Import database and models
from core.cache import cache
from core.errors import BadRequestError, InternalError, NotFoundError, handle_route_errors
from database import get_db
from models import Project, ProjectModule, User
from models.enums import ModuleStatus
from routes.auth import require_module_owner, require_viewer
from services.infrastructure_access_service import (
    cleanup_durable_infra_private_key,
    normalize_module_outputs_in_place,
)

# Import state decryption service
from services.state_decryption_service import (
    decrypt_state_async,
    get_state_metadata,
    get_state_resources_list,
    get_state_resources_list_async,
    is_state_encrypted,
)

# Import state parser service
from services.state_parser import StateFileInvalidError, TerraformStateParser

# Create router
router = APIRouter(prefix="/api/state", tags=["state-viewer"])


# ============================================================
# Helper Functions
# ============================================================

def get_module_state_path(module: ProjectModule, project: Project) -> str:
    """
    Get the path to a module's terraform.tfstate file (v2 architecture).

    In v2, state files are stored in: /app/state/{project_id}/{module_id}/terraform.tfstate
    This replaces the v1 pattern using clone_path (which no longer exists).

    Args:
        module: ProjectModule instance
        project: Project instance (kept for backward compatibility, not used)

    Returns:
        Path to terraform.tfstate file
    """
    return f"/app/state/{module.project_id}/{module.id}/terraform.tfstate"


def check_state_file_exists(state_path: str) -> bool:
    """Check if state file exists."""
    return os.path.exists(state_path)


# ============================================================
# API Endpoints
# ============================================================

@router.get("/module/{module_id}", dependencies=[Depends(require_viewer)])
@handle_route_errors("get module state")
async def get_module_state(module_id: int, db: Session = Depends(get_db)):
    """
    Get parsed Terraform state for a module.

    Supports encrypted state files (OpenTofu 1.8+) by decrypting using tofu CLI.

    PERF-012: Now async - decryption runs in ThreadPoolExecutor to avoid blocking.

    Returns:
        - resources: List of all resources in state
        - outputs: Terraform outputs
        - dependencies: Resource dependency graph
        - metadata: State file metadata
        - is_encrypted: Whether state is encrypted
    """
    # Get module
    module = db.query(ProjectModule).filter(ProjectModule.id == module_id).first()
    if not module:
        raise NotFoundError("module", module_id)

    # Get project
    project = db.query(Project).filter(Project.id == module.project_id).first()
    if not project:
        raise NotFoundError("project", module.project_id)

    # Get state file path
    state_path = get_module_state_path(module, project)

    # Check if state file exists
    if not check_state_file_exists(state_path):
        return JSONResponse(
            status_code=200,
            content={
                "exists": False,
                "message": "State file not found. Module may not have been applied yet.",
                "state_path": state_path,
                "resources": [],
                "outputs": {},
                "dependencies": [],
                "metadata": {},
                "is_encrypted": False
            }
        )

    # Check if state is encrypted
    if is_state_encrypted(state_path):
        # PERF-012: Use async decryption to avoid blocking event loop
        success, decrypted_state = await decrypt_state_async(
            state_path, project.id, module_id, db
        )

        if success:
            # Parse decrypted state
            parser = TerraformStateParser(state_path, decrypted_state=decrypted_state)
            state_data = parser.parse()

            return {
                "exists": True,
                "state_path": state_path,
                "is_encrypted": True,
                "decryption_successful": True,
                "resources": state_data['resources'],
                "outputs": state_data['outputs'],
                "dependencies": state_data['dependencies'],
                "metadata": state_data['metadata']
            }
        else:
            # Decryption failed - return metadata and outputs from DB
            metadata = get_state_metadata(state_path)
            return {
                "exists": True,
                "state_path": state_path,
                "is_encrypted": True,
                "decryption_successful": False,
                "decryption_error": decrypted_state.get("error", "Unknown error"),
                "resources": [],
                "outputs": normalize_module_outputs_in_place(module).outputs,  # Fallback to normalized DB outputs
                "dependencies": [],
                "metadata": metadata,
                "message": "State is encrypted. Showing outputs from database."
            }

    # Parse unencrypted state file
    try:
        parser = TerraformStateParser(state_path)
        state_data = parser.parse()

        return {
            "exists": True,
            "state_path": state_path,
            "is_encrypted": False,
            "resources": state_data['resources'],
            "outputs": state_data['outputs'],
            "dependencies": state_data['dependencies'],
            "metadata": state_data['metadata']
        }

    except StateFileInvalidError as e:
        logger.error(f"Invalid state file {state_path}: {e}")
        raise BadRequestError(f"Invalid state file: {str(e)}")


@router.get("/module/{module_id}/resources", dependencies=[Depends(require_viewer)])
@handle_route_errors("get module resources")
async def get_module_resources(module_id: int, db: Session = Depends(get_db)):
    """
    Get list of all resources in a module's state.

    Supports encrypted state files by using tofu CLI for decryption.

    PERF-012: Now async - resource listing runs in ThreadPoolExecutor.

    Returns:
        - resources: List of resources with key information
        - counts_by_type: Resource counts grouped by type
        - counts_by_provider: Resource counts grouped by provider
        - is_encrypted: Whether state is encrypted
    """
    # Get module and project
    module = db.query(ProjectModule).filter(ProjectModule.id == module_id).first()
    if not module:
        raise NotFoundError("module", module_id)

    project = db.query(Project).filter(Project.id == module.project_id).first()
    if not project:
        raise NotFoundError("project", module.project_id)

    # Get state file path
    state_path = get_module_state_path(module, project)

    if not check_state_file_exists(state_path):
        return {
            "exists": False,
            "is_encrypted": False,
            "resources": [],
            "counts_by_type": {},
            "counts_by_provider": {}
        }

    # Check if encrypted
    if is_state_encrypted(state_path):
        # PERF-012: Use async to avoid blocking event loop
        success, resource_list = await get_state_resources_list_async(
            state_path, project.id, module_id, db
        )

        if success:
            # Group resources by type for counts
            counts_by_type = {}
            for resource in resource_list:
                # Resource format: type.name (e.g., aws_vpc.main)
                parts = resource.split(".")
                if len(parts) >= 2:
                    resource_type = parts[0]
                    counts_by_type[resource_type] = counts_by_type.get(resource_type, 0) + 1

            return {
                "exists": True,
                "is_encrypted": True,
                "resources": [{"address": r, "type": r.split(".")[0] if "." in r else "unknown"} for r in resource_list],
                "resource_count": len(resource_list),
                "counts_by_type": counts_by_type,
                "counts_by_provider": {}  # Cannot determine without full state
            }
        else:
            # Failed to get resources
            return {
                "exists": True,
                "is_encrypted": True,
                "error": resource_list[0] if resource_list else "Failed to list resources",
                "resources": [],
                "counts_by_type": {},
                "counts_by_provider": {}
            }

    # Parse unencrypted state
    parser = TerraformStateParser(state_path)
    state_data = parser.parse()

    return {
        "exists": True,
        "is_encrypted": False,
        "resources": state_data['resources'],
        "counts_by_type": parser.get_resource_count_by_type(),
        "counts_by_provider": parser.get_provider_summary()
    }


@router.get("/module/{module_id}/resource/{resource_address:path}", dependencies=[Depends(require_viewer)])
@handle_route_errors("get resource details")
def get_resource_details(
    module_id: int,
    resource_address: str,
    db: Session = Depends(get_db)
):
    """
    Get detailed information about a specific resource.

    Args:
        module_id: Module ID
        resource_address: Resource address (e.g., aws_vpc.main)

    Returns:
        Detailed resource information including all attributes
    """
    # Get module and project
    module = db.query(ProjectModule).filter(ProjectModule.id == module_id).first()
    if not module:
        raise NotFoundError("module", module_id)

    project = db.query(Project).filter(Project.id == module.project_id).first()
    if not project:
        raise NotFoundError("project", module.project_id)

    # Get state file path
    state_path = get_module_state_path(module, project)

    if not check_state_file_exists(state_path):
        raise NotFoundError("state", state_path)

    # Parse state and find resource
    parser = TerraformStateParser(state_path)
    parser.parse()  # Must parse first
    resource = parser.get_resource_by_address(resource_address)

    if not resource:
        raise NotFoundError("resource", resource_address)

    # Find dependencies and dependents
    dependencies = parser.extract_dependencies()
    resource_dependencies = [d for d in dependencies if d['source'] == resource['full_address']]
    resource_dependents = [d for d in dependencies if d['target'] == resource['full_address']]

    return {
        "resource": resource,
        "dependencies": resource_dependencies,
        "dependents": resource_dependents
    }


@router.get("/module/{module_id}/graph", dependencies=[Depends(require_viewer)])
@handle_route_errors("get dependency graph")
def get_dependency_graph(module_id: int, db: Session = Depends(get_db)):
    """
    Get resource dependency graph for visualization.

    Returns:
        - nodes: List of resource nodes
        - edges: List of dependency edges
    """
    # Get module and project
    module = db.query(ProjectModule).filter(ProjectModule.id == module_id).first()
    if not module:
        raise NotFoundError("module", module_id)

    project = db.query(Project).filter(Project.id == module.project_id).first()
    if not project:
        raise NotFoundError("project", module.project_id)

    # Get state file path
    state_path = get_module_state_path(module, project)

    if not check_state_file_exists(state_path):
        return {
            "exists": False,
            "nodes": [],
            "edges": []
        }

    # Parse state and build graph
    parser = TerraformStateParser(state_path)
    parser.parse()  # Must parse first
    graph = parser.build_dependency_graph()

    return {
        "exists": True,
        **graph
    }


@router.get("/module/{module_id}/outputs", dependencies=[Depends(require_viewer)])
@handle_route_errors("get module outputs")
def get_module_outputs(module_id: int, db: Session = Depends(get_db)):
    """
    Get Terraform outputs from module state.

    Returns:
        Dictionary of output values (sensitive values are masked)
    """
    # Get module and project
    module = db.query(ProjectModule).filter(ProjectModule.id == module_id).first()
    if not module:
        raise NotFoundError("module", module_id)

    project = db.query(Project).filter(Project.id == module.project_id).first()
    if not project:
        raise NotFoundError("project", module.project_id)

    # Get state file path
    state_path = get_module_state_path(module, project)

    if not check_state_file_exists(state_path):
        return {
            "exists": False,
            "outputs": {}
        }

    # Parse state
    parser = TerraformStateParser(state_path)
    state_data = parser.parse()

    # Mask sensitive outputs
    outputs = state_data['outputs']
    for output_name, output_data in outputs.items():
        if output_data.get('sensitive'):
            output_data['value'] = '***SENSITIVE***'

    return {
        "exists": True,
        "outputs": outputs
    }


@router.post("/module/{module_id}/refresh")
@handle_route_errors("refresh module state")
def refresh_module_state(module_id: int, user: User = Depends(require_module_owner), db: Session = Depends(get_db)):
    """
    Refresh state information from filesystem.
    This endpoint re-reads the state file and returns fresh data.

    Returns:
        Refreshed state data
    """
    # Get module and project
    module = db.query(ProjectModule).filter(ProjectModule.id == module_id).first()
    if not module:
        raise NotFoundError("module", module_id)

    project = db.query(Project).filter(Project.id == module.project_id).first()
    if not project:
        raise NotFoundError("project", module.project_id)

    # Get state file path
    state_path = get_module_state_path(module, project)

    if not check_state_file_exists(state_path):
        return {
            "exists": False,
            "message": "State file not found",
            "refreshed_at": None
        }

    # Parse fresh state
    parser = TerraformStateParser(state_path)
    state_data = parser.parse()

    from datetime import datetime
    return {
        "exists": True,
        "message": "State refreshed successfully",
        "refreshed_at": datetime.now().isoformat(),
        "metadata": state_data['metadata']
    }


@router.get("/module/{module_id}/raw", dependencies=[Depends(require_viewer)])
@handle_route_errors("get raw state")
def get_raw_state(module_id: int, db: Session = Depends(get_db)):
    """
    Get raw Terraform state file content (for debugging/inspection).

    WARNING: This may contain sensitive data. Use with caution.

    Returns:
        Raw state file JSON
    """
    # Get module and project
    module = db.query(ProjectModule).filter(ProjectModule.id == module_id).first()
    if not module:
        raise NotFoundError("module", module_id)

    project = db.query(Project).filter(Project.id == module.project_id).first()
    if not project:
        raise NotFoundError("project", module.project_id)

    # Get state file path
    state_path = get_module_state_path(module, project)

    if not check_state_file_exists(state_path):
        raise NotFoundError("state", state_path)

    # Read raw state
    import json
    with open(state_path) as f:
        raw_state = json.load(f)

    return {
        "exists": True,
        "state_path": state_path,
        "raw_state": raw_state,
        "warning": "This data may contain sensitive information"
    }


@router.delete("/module/{module_id}/reset")
@handle_route_errors("reset module state")
def reset_module_state(module_id: int, user: User = Depends(require_module_owner), db: Session = Depends(get_db)):
    """
    Reset module state by deleting the state file and clearing module status.

    Use this when:
    - Module code has changed significantly and old state is incompatible
    - Infrastructure was deleted manually and state is stale
    - Destroy failed due to state/code mismatch

    WARNING: This permanently deletes the state file. Use with caution.

    Returns:
        Success message with reset details
    """
    import shutil
    from datetime import datetime

    # Get module
    module = db.query(ProjectModule).filter(ProjectModule.id == module_id).first()
    if not module:
        raise NotFoundError("module", module_id)

    # Get project
    project = db.query(Project).filter(Project.id == module.project_id).first()
    if not project:
        raise NotFoundError("project", module.project_id)

    # Get state directory path
    state_dir = f"/app/state/{module.project_id}/{module.id}"

    module_name = module.library_module.name if module.library_module else f"module-{module_id}"

    result = {
        "module_id": module_id,
        "module_name": module_name,
        "previous_status": module.status,
        "state_deleted": False,
        "reset_at": datetime.now().isoformat()
    }

    # Delete state file and directory if exists
    if os.path.exists(state_dir):
        try:
            shutil.rmtree(state_dir)
            result["state_deleted"] = True
            result["state_path"] = state_dir
            logger.info(f"Deleted state directory for module {module_id}: {state_dir}")
        except Exception as e:
            logger.error(f"Failed to delete state directory: {e}")
            raise InternalError(f"Failed to delete state directory: {str(e)}")
    else:
        result["message"] = "State directory did not exist"

    # Reset module status and clear outputs
    module.status = ModuleStatus.NOT_INITIALIZED
    module.outputs = None
    module.deployment_error = None
    module.last_deployed_at = None
    cleanup_durable_infra_private_key(module.project_id, module.id)

    db.commit()

    # PERF-017: Invalidate state caches after reset
    from services.state_decryption_service import invalidate_state_cache
    invalidate_state_cache(module_id, module.project_id)

    result["new_status"] = ModuleStatus.NOT_INITIALIZED
    result["success"] = True

    logger.info(f"Reset module {module_id} ({module_name}) state successfully")

    return result


@router.get("/project/{project_id}/modules", dependencies=[Depends(require_viewer)])
@handle_route_errors("get project modules with state")
def get_project_modules_with_state(
    project_id: int,
    skip_cache: bool = False,
    db: Session = Depends(get_db)
):
    """
    Get all modules in a project with their state status.

    Supports encrypted state files.

    PERF-017: Results cached for 60 seconds to avoid N file reads + N subprocess calls.
    Use skip_cache=true to force refresh.

    Returns:
        List of modules with state file information
    """
    cache_key = f"state:project:{project_id}:modules"

    # Check cache first (unless skip_cache requested)
    if not skip_cache:
        cached_result = cache.get(cache_key)
        if cached_result is not None:
            logger.debug(f"Cache HIT for project {project_id} modules state")
            return cached_result

    # Get project
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise NotFoundError("project", project_id)

    # Get all modules
    modules = db.query(ProjectModule).filter(ProjectModule.project_id == project_id).all()

    result = []
    for module in modules:
        state_path = get_module_state_path(module, project)
        state_exists = check_state_file_exists(state_path)

        module_info = {
            "id": module.id,
            "path_in_project": module.path_in_project,
            "status": module.status,
            "state_exists": state_exists,
            "state_path": state_path,
            "is_encrypted": False
        }

        # If state exists, add resource count
        if state_exists:
            # Check if encrypted
            if is_state_encrypted(state_path):
                module_info["is_encrypted"] = True
                # Get metadata
                metadata = get_state_metadata(state_path)
                module_info["serial"] = metadata.get("serial", 0)
                module_info["lineage"] = metadata.get("lineage", "unknown")

                # Get resource count using tofu state list
                success, resource_list = get_state_resources_list(
                    state_path, project_id, module.id, db
                )
                if success:
                    module_info["resource_count"] = len(resource_list)
                else:
                    module_info["resource_count"] = None  # Unknown

                # Get output count from database
                module_info["output_count"] = len(module.outputs) if module.outputs else 0
            else:
                # Unencrypted state
                try:
                    parser = TerraformStateParser(state_path)
                    parser.parse()
                    module_info["resource_count"] = len(parser.extract_resources())
                    module_info["output_count"] = len(parser.extract_outputs())
                except Exception as e:
                    logger.warning(f"Could not parse state for module {module.id}: {e}")
                    module_info["resource_count"] = 0
                    module_info["output_count"] = 0

        result.append(module_info)

    response = {
        "project_id": project_id,
        "project_name": project.name,
        "modules": result
    }

    # Cache for 60 seconds (state doesn't change frequently)
    cache.set(cache_key, response, ttl_seconds=60)

    return response
