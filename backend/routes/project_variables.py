import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session, joinedload

# Configure logging
logger = logging.getLogger(__name__)

# Import auth dependencies
# Import database and models
from core.cache import cache, invalidate_cache

# Import error handling functions from core modules
from core.errors import get_or_404, handle_route_errors
from database import get_db
from models import ModuleLibrary, Project, ProjectModule, User
from models.enums import ModuleStatus
from routes.auth import require_project_owner, require_viewer

# Import Pydantic schemas
from schemas.projects import (
    ProjectVariablesMutationResponse,
    ProjectVariablesResponse,
    ProjectVariablesUpdate,
    SuccessResponse,
    VariableDefaultsResponse,
    VariableDefaultsUpdate,
)

# ============================================================
# FastAPI Router Setup
# ============================================================

router = APIRouter(prefix="/api/projects", tags=["project-variables"])

# ============================================================
# Variable Defaults Endpoints
# ============================================================

@router.get("/{project_id}/variable-defaults", dependencies=[Depends(require_viewer)], response_model=VariableDefaultsResponse)
@handle_route_errors("get variable defaults")
def get_variable_defaults(project_id: int, db: Session = Depends(get_db)):
    """
    Get variable defaults for a project.
    Returns both the common defaults and any project-specific overrides.
    """
    project = get_or_404(db, Project, id=project_id)

    # Base common defaults (static)
    common_defaults = {
        # Network CIDRs
        'vpc_cidr': '10.0.0.0/16',
        'cidr': '10.0.0.0/16',
        'cidr_block': '10.0.0.0/16',
        'public_subnet_cidr': '10.0.1.0/24',
        'private_subnet_cidr': '10.0.2.0/24',
        'private_external_subnet_a_cidr': '10.0.10.0/24',
        'private_external_subnet_b_cidr': '10.0.11.0/24',
        'private_internal_subnet_a_cidr': '10.0.20.0/24',
        'private_internal_subnet_b_cidr': '10.0.21.0/24',
        # Common resource names
        'name': project.name.lower().replace(' ', '-'),
        'cluster_name': f"{project.name.lower().replace(' ', '-')}-cluster",
        'instance_name': f"{project.name.lower().replace(' ', '-')}-instance",
        # Common sizing
        'instance_type': 't3.medium',
        'instance_count': 1,
        'min_size': 1,
        'max_size': 3,
        'desired_capacity': 2,
        # Common booleans
        'enabled': True,
        'enable_dns_hostnames': True,
        'enable_dns_support': True,
        'enable_nat_gateway': True,
        'single_nat_gateway': True,
        # Availability zones
        'availability_zones': ['a', 'b'],
        'azs': ['a', 'b'],
    }

    # Get project-specific overrides
    custom_defaults = {}
    if project.project_variables:
        custom_defaults = project.project_variables.get('variable_defaults', {})

    return {
        "project_id": project_id,
        "project_name": project.name,
        "common_defaults": common_defaults,
        "custom_defaults": custom_defaults,
        "effective_defaults": {**common_defaults, **custom_defaults}
    }


@router.put("/{project_id}/variable-defaults")
@handle_route_errors("update variable defaults")
def update_variable_defaults(
    project_id: int,
    body: VariableDefaultsUpdate,
    user: User = Depends(require_project_owner),
    db: Session = Depends(get_db),
):
    """
    Update variable defaults for a project.
    This allows users to customize default values for infrastructure variables.
    """
    defaults = body.defaults  # Pydantic already validated CIDRs via @model_validator
    project = get_or_404(db, Project, id=project_id)

    # Initialize project_variables if it doesn't exist
    if not project.project_variables:
        project.project_variables = {}

    # Update the variable_defaults section
    project.project_variables['variable_defaults'] = defaults

    # Mark the JSON field as modified so SQLAlchemy detects the change
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(project, "project_variables")

    db.commit()
    db.refresh(project)

    # Invalidate projects cache
    invalidate_cache("projects:*")

    logger.info(f"Updated variable defaults for project {project.name}: {len(defaults)} variables")

    return {
        "success": True,
        "message": f"Variable defaults updated for project '{project.name}'",
        "defaults_count": len(defaults)
    }


@router.delete("/{project_id}/variable-defaults", response_model=SuccessResponse)
@handle_route_errors("reset variable defaults")
def reset_variable_defaults(project_id: int, user: User = Depends(require_project_owner), db: Session = Depends(get_db)):
    """
    Reset variable defaults to common defaults (remove all project-specific overrides).
    """
    project = get_or_404(db, Project, id=project_id)

    # Remove variable_defaults from project_variables
    if project.project_variables and 'variable_defaults' in project.project_variables:
        del project.project_variables['variable_defaults']

        # Mark the JSON field as modified
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(project, "project_variables")

        db.commit()
        db.refresh(project)

        # Invalidate projects cache
        invalidate_cache("projects:*")

        logger.info(f"Reset variable defaults for project {project.name}")

    return {
        "success": True,
        "message": f"Variable defaults reset to common defaults for project '{project.name}'"
    }


# ============================================================
# Project Variables (replaces Variable Defaults)
# ============================================================

@router.get("/{project_id}/variables", dependencies=[Depends(require_viewer)], response_model=ProjectVariablesResponse)
@handle_route_errors("get project variables")
def get_project_variables(project_id: int, db: Session = Depends(get_db)):
    """
    Get project variables - key-value pairs that are injected into all module deployments.

    These are project-wide variables like cluster_name, vpc_id, etc. that modules need.
    The platform will auto-wire some of these from deployed module outputs.

    PERF-013: Results cached for 60s to reduce database queries.
    """
    # PERF-013: Check cache first
    cache_key = f"project:variables:{project_id}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    project = get_or_404(db, Project, id=project_id)

    # Get stored project variables
    stored_vars = {}
    if project.project_variables:
        # Support both flat and nested structures
        if 'variable_defaults' in project.project_variables:
            stored_vars = project.project_variables.get('variable_defaults', {})
        else:
            stored_vars = project.project_variables

    # Auto-discover variables from ALL deployed module outputs
    # No hardcoding - modules control what they expose via outputs.tf
    # First module to output a variable name wins (respects deployment_order)
    discovered_vars = {}

    # B3: load_only() — variable discovery only needs library_module name/path
    all_modules = db.query(ProjectModule).options(
        joinedload(ProjectModule.library_module).load_only(
            ModuleLibrary.id, ModuleLibrary.name, ModuleLibrary.path
        )
    ).filter(
        ProjectModule.project_id == project_id
    ).order_by(ProjectModule.deployment_order).all()

    # Filter to deployed modules for output discovery
    deployed_modules = [pm for pm in all_modules if pm.status == ModuleStatus.APPLIED]

    for pm in deployed_modules:
        if not pm.outputs or not pm.library_module:
            continue

        module_name = pm.library_module.name
        module_path = pm.library_module.path

        for output_name, output_value in pm.outputs.items():
            # Skip if already discovered (first module wins)
            if output_name in discovered_vars:
                continue

            # Determine value type for UI display
            value_type = "string"
            if isinstance(output_value, bool):
                value_type = "bool"
            elif isinstance(output_value, (int, float)):
                value_type = "number"
            elif isinstance(output_value, list):
                value_type = "list"
            elif isinstance(output_value, dict):
                value_type = "object"

            # For complex objects, truncate display but keep full value
            is_truncated = False
            if isinstance(output_value, (dict, list)) and len(str(output_value)) > 500:
                is_truncated = True

            discovered_vars[output_name] = {
                'value': output_value,
                'display_value': str(output_value)[:100] + '...' if is_truncated else output_value,
                'type': value_type,
                'source': module_path,
                'source_module': module_name,
                'auto_discovered': True,
                'is_truncated': is_truncated
            }

    # Discover ALL input variables from module schemas
    # Group by module so UI can show per-module configuration
    # Only skip variables that are ACTUALLY auto-discovered (in discovered_vars)
    configurable_vars = {}
    var_usage = {}  # Track which modules use each variable
    modules_config = {}  # Group variables by module for UI

    # all_modules already queried above with eager loading

    # Set of auto-discovered variable names (these are wired from outputs)
    discovered_var_names = set(discovered_vars.keys())

    for pm in all_modules:
        if not pm.library_module or not pm.library_module.variables_schema:
            continue

        module_name = pm.library_module.name
        module_path = pm.library_module.path
        module_vars = []

        for var_def in pm.library_module.variables_schema:
            var_name = var_def.get('name')
            if not var_name:
                continue

            # Check if this is auto-wired from another module's output
            is_auto_wired = var_name in discovered_var_names

            # Track usage across modules
            if var_name not in var_usage:
                var_usage[var_name] = []
            var_usage[var_name].append(module_name)

            # Build variable info
            var_info = {
                'type': var_def.get('type', 'string'),
                'description': var_def.get('description', ''),
                'default': var_def.get('default'),
                'required': var_def.get('required', False),
                'sensitive': var_def.get('sensitive', False),
                'is_auto_wired': is_auto_wired,
            }

            # Add to module's variable list
            module_vars.append({
                'name': var_name,
                **var_info
            })

            # First definition wins for flat configurable_vars
            if var_name not in configurable_vars:
                configurable_vars[var_name] = {
                    **var_info,
                    'used_by': [],  # Will be filled after loop
                    'defined_in': module_name,
                }

        # Store module's variables for grouped view
        if module_vars:
            modules_config[module_name] = {
                'path': module_path,
                'status': pm.status,
                'deployment_order': pm.deployment_order,
                'variables': module_vars
            }

    # Add usage info and current values to flat configurable_vars
    for var_name, var_info in configurable_vars.items():
        var_info['used_by'] = var_usage.get(var_name, [])
        var_info['modules_count'] = len(var_info['used_by'])

        # Current value priority: user-set > discovered > default
        if var_name in stored_vars:
            var_info['current_value'] = stored_vars[var_name]
            var_info['value_source'] = 'user'
        elif var_name in discovered_vars:
            var_info['current_value'] = discovered_vars[var_name]['value']
            var_info['value_source'] = 'discovered'
        elif var_info['default'] is not None:
            var_info['current_value'] = var_info['default']
            var_info['value_source'] = 'default'
        else:
            var_info['current_value'] = None
            var_info['value_source'] = 'unset'

    result = {
        "project_id": project_id,
        "project_name": project.name,
        "variables": stored_vars,
        "discovered": discovered_vars,
        "configurable": configurable_vars,
        "modules": modules_config,  # Grouped by module for UI
        "effective": {
            **{k: v['value'] for k, v in discovered_vars.items()},
            **stored_vars  # User vars override discovered
        }
    }

    # PERF-013: Cache result for 60 seconds
    cache.set(cache_key, result, ttl_seconds=60)
    return result


@router.put("/{project_id}/variables", response_model=ProjectVariablesMutationResponse)
@handle_route_errors("update project variables")
def update_project_variables(
    project_id: int,
    body: ProjectVariablesUpdate,
    user: User = Depends(require_project_owner),
    db: Session = Depends(get_db),
):
    """
    Update project variables.

    These variables are injected into all module deployments.
    """
    variables = body.variables
    project = get_or_404(db, Project, id=project_id)

    # Store as flat structure in project_variables
    project.project_variables = variables

    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(project, "project_variables")

    db.commit()
    db.refresh(project)

    # PERF-013: Invalidate specific variable cache
    cache.delete(f"project:variables:{project_id}")
    invalidate_cache("projects:*")

    logger.info(f"Updated project variables for project {project.name}: {list(variables.keys())}")

    return {
        "success": True,
        "message": "Project variables updated",
        "variables_count": len(variables),
        "variables": variables
    }
