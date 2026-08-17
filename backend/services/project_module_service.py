"""
Project Module Service — DB and business logic for project modules.

Extracted from routes/project_modules.py and routes/project_execution.py
to separate HTTP handling from domain logic.

Covers:
- Module CRUD (list, add, update, delete)
- Variable validation and retrieval
- Dependency management (set, get, get dependents, calculate order, graph)
- Execution task creation (init, plan, apply, destroy, cancel, deploy, retry)
"""

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_
from sqlalchemy.orm import joinedload, undefer

from core.errors import BadRequestError, ConflictError, InternalError, NotFoundError
from models import Deployment, ModuleLibrary, Project, ProjectModule
from models.enums import ModuleStatus
from services.base_service import BaseService
from services.infrastructure_access_service import normalize_module_outputs_in_place
from services.module_capabilities import serialize_engine_metadata
from services.module_version_query import available_module_versions
from services.project_service import detect_module_dependencies, update_project_counts
from services.variable_parser import VariableValidator
from utils.dependency_graph import (
    CircularDependencyError,
    calculate_deployment_order,
    get_dependency_graph,
    validate_dependencies,
)
from utils.security import validate_action_inputs

logger = logging.getLogger(__name__)


def _module_variables_from_library(library_module: ModuleLibrary | None) -> list[dict]:
    """Convert manifest inputs or stored schema into frontend variable definitions."""
    if not library_module:
        return []

    if library_module.inputs_metadata:
        variables: list[dict] = []
        for required, group_name in ((True, "required"), (False, "optional")):
            for item in library_module.inputs_metadata.get(group_name, []) or []:
                variables.append({
                    "name": item.get("name"),
                    "type": item.get("type", "string"),
                    "description": item.get("description", ""),
                    "required": required,
                    "sensitive": item.get("sensitive", False),
                    "default": item.get("default"),
                    "source": item.get("source"),
                })
        return variables

    if library_module.variables_schema and isinstance(library_module.variables_schema, list):
        return library_module.variables_schema

    return []


class ProjectModuleService(BaseService):
    """Service layer for project module operations."""

    # Public aliases for backward compatibility (used by routes)
    def get_module(self, module_id: int) -> ProjectModule:
        return self._get_module(module_id)

    def get_project(self, project_id: int) -> Project:
        return self._get_project(project_id)

    # ================================================================
    # Variable Validation
    # ================================================================

    def validate_variables(self, module_library_id: int, variables: dict) -> dict:
        """
        Validate variable values against a module's schema.

        Returns dict with keys: success, errors, warnings, validated.
        """
        module = self.db.query(ModuleLibrary).filter(
            ModuleLibrary.id == module_library_id
        ).first()
        if not module:
            raise NotFoundError("module", module_library_id)

        variables_schema = module.variables_schema
        if not variables_schema or not isinstance(variables_schema, list):
            return {
                "success": True,
                "errors": [],
                "warnings": ["Module has no variable schema defined - skipping validation"],
                "validated": False,
            }

        is_valid, errors = VariableValidator.validate_variables(variables, variables_schema)

        warnings = []
        schema_var_names = {v["name"] for v in variables_schema}
        for var_name in variables.keys():
            if var_name not in schema_var_names:
                warnings.append(f"Variable '{var_name}' is not defined in module schema")

        return {
            "success": is_valid,
            "errors": errors,
            "warnings": warnings,
            "validated": True,
        }

    # ================================================================
    # Module CRUD
    # ================================================================

    def list_modules(self, project_id: int) -> dict:
        """List all modules in a project with library module details."""
        self.get_project(project_id)

        modules = (
            self.db.query(ProjectModule)
            .options(
                joinedload(ProjectModule.library_module).load_only(
                    ModuleLibrary.id,
                    ModuleLibrary.name,
                    ModuleLibrary.category,
                    ModuleLibrary.provider,
                    ModuleLibrary.git_source,
                    ModuleLibrary.source_path,
                    ModuleLibrary.path,
                    ModuleLibrary.version,
                    ModuleLibrary.module_source_id,
                )
            )
            .filter(ProjectModule.project_id == project_id)
            .order_by(ProjectModule.deployment_order)
            .all()
        )

        return {
            "modules": [self._serialize_module(m) for m in modules],
            "total": len(modules),
        }

    def add_module(
        self,
        project_id: int,
        module_library_id: int,
        path_in_project: str,
        variable_overrides: dict | None = None,
        deployment_order: int = 0,
        enabled: bool = True,
        context_resolved_var_names: set[str] | None = None,
    ) -> dict:
        """Add a module from the library to a project."""
        self.get_project(project_id)

        library_module = self.db.query(ModuleLibrary).filter(
            ModuleLibrary.id == module_library_id
        ).first()
        if not library_module:
            raise NotFoundError("module", module_library_id)

        # Check for duplicate path
        existing = self.db.query(ProjectModule).filter(
            and_(
                ProjectModule.project_id == project_id,
                ProjectModule.path_in_project == path_in_project,
            )
        ).first()
        if existing:
            raise BadRequestError(f"Module already exists at path '{path_in_project}'")

        # Validate variables if provided — always, regardless of enabled state.
        # Invalid overrides must be rejected at add time so they don't persist
        # until apply time when the module is later enabled.
        warnings = []
        if variable_overrides:
            warnings = self._validate_module_variables(
                library_module, variable_overrides,
                context_resolved_var_names=context_resolved_var_names,
            )

        # Detect auto-dependencies
        existing_modules = (
            self.db.query(ProjectModule)
            .options(
                joinedload(ProjectModule.library_module).load_only(
                    ModuleLibrary.id,
                    ModuleLibrary.name,
                    ModuleLibrary.path,
                    ModuleLibrary.dependencies_metadata,
                )
            )
            .filter(ProjectModule.project_id == project_id)
            .all()
        )
        auto_dependencies = detect_module_dependencies(library_module, existing_modules)

        # Auto-adjust deployment order based on dependencies
        if auto_dependencies and deployment_order == 0:
            max_dep_order = max(
                [m.deployment_order for m in existing_modules if m.id in auto_dependencies],
                default=0,
            )
            deployment_order = max_dep_order + 1

        # Create module
        project_module = ProjectModule(
            project_id=project_id,
            module_library_id=module_library_id,
            path_in_project=path_in_project,
            variable_overrides=variable_overrides or {},
            deployment_order=deployment_order,
            dependencies=auto_dependencies,
            status="not_initialized",
            enabled=enabled,
        )
        self.db.add(project_module)
        self.db.flush()
        self.db.refresh(project_module)

        update_project_counts(self.db, project_id)

        # Build dependency info for response
        dependency_info = self._build_dependency_info(auto_dependencies)

        response = {
            "success": True,
            "message": f"Module '{library_module.name}' added to project",
            "module_id": project_module.id,
            "deployment_order": deployment_order,
            "dependencies": dependency_info,
        }
        if warnings:
            response["warnings"] = warnings

        return response

    def update_module(
        self,
        module_id: int,
        variable_overrides: dict | None = None,
        deployment_order: int | None = None,
        enabled: bool | None = None,
    ) -> dict:
        """Update module configuration (variables, order, enabled).

        When a module is being enabled (enabled=True on a previously-disabled module),
        dependencies and deployment order are recomputed so the sequencer has a
        consistent picture before any subsequent dispatch.
        """
        module = self.get_module(module_id)
        was_disabled = not module.enabled

        warnings = []
        if variable_overrides is not None:
            library_module = module.library_module
            if library_module and library_module.variables_schema:
                is_valid, errors = VariableValidator.validate_variables(
                    variable_overrides, library_module.variables_schema
                )
                if not is_valid:
                    raise BadRequestError(f"Variable validation failed: {'; '.join(errors)}")

                schema_var_names = {v["name"] for v in library_module.variables_schema}
                for var_name in variable_overrides.keys():
                    if var_name not in schema_var_names:
                        warnings.append(f"Variable '{var_name}' is not defined in module schema")

            module.variable_overrides = variable_overrides

        if deployment_order is not None:
            module.deployment_order = deployment_order
        if enabled is not None:
            module.enabled = enabled

        self.db.flush()
        self.db.refresh(module)

        # When a disabled module is being re-enabled, recompute dependencies and
        # deployment order for the whole project so the dispatch sequencer has an
        # up-to-date view before any apply is dispatched.
        enabling_module = (enabled is True and was_disabled)
        if enabling_module and module.library_module:
            try:
                self.calculate_deployment_order(module.project_id)
                self.db.flush()
                self.db.refresh(module)
            except Exception as exc:
                logger.warning(
                    "update_module: deployment order recompute failed for project %s: %s",
                    module.project_id, exc,
                )

        response = {"success": True, "message": "Module updated successfully"}
        if warnings:
            response["warnings"] = warnings
        return response

    def change_module_version(self, module_id: int, target_version: str) -> dict:
        """Re-pin a project module to a different catalog version of its path.

        D-033 PR-2: module version rows are immutable, so the ProjectModule FK
        is the version pin. This is the EXPLICIT operator action replacing the
        pre-D-033 implicit drift (sync mutating the row a deployed project
        points at). The swap does not re-plan or re-deploy anything — the new
        manifest takes effect on the module's next plan/apply/destroy.
        """
        module = self.get_module(module_id)
        current = module.library_module
        if current is None:
            raise NotFoundError("module_library", module.module_library_id)

        target_version = (target_version or "").strip()
        if not target_version:
            raise BadRequestError("target_version must be a non-empty version string")

        if current.version == target_version:
            return {
                "success": True,
                "message": f"Module already pinned to version {target_version}",
                "module_id": module.id,
                "path": current.path,
                "previous_version": current.version,
                "version": current.version,
                "module_library_id": current.id,
            }

        # Scope strictly to the CURRENT row's source: two sources can
        # legitimately catalog the same (path, version) with different
        # content/digests, and a re-pin must never silently jump sources.
        target = (
            self.db.query(ModuleLibrary)
            .filter(
                ModuleLibrary.path == current.path,
                ModuleLibrary.version == target_version,
                ModuleLibrary.module_source_id == current.module_source_id,
                ModuleLibrary.is_active,
            )
            .order_by(ModuleLibrary.id.desc())
            .first()
        )
        if target is None:
            available = available_module_versions(
                self.db, current.path, module_source_id=current.module_source_id
            )
            raise BadRequestError(
                f"Version {target_version} of module '{current.path}' is not in the active catalog "
                f"for this module's source. Available versions: {', '.join(available) or 'none'}.",
                code="MODULE_VERSION_NOT_FOUND",
                details={"path": current.path, "available_versions": available},
            )

        previous_version = current.version
        module.module_library_id = target.id
        self.db.flush()
        self.db.refresh(module)

        logger.info(
            "Project module %s re-pinned: %s %s -> %s",
            module.id, current.path, previous_version, target_version,
        )
        return {
            "success": True,
            "message": f"Module re-pinned from version {previous_version} to {target_version}. "
            f"The new version takes effect on the next plan/apply/destroy.",
            "module_id": module.id,
            "path": current.path,
            "previous_version": previous_version,
            "version": target_version,
            "module_library_id": target.id,
        }

    def remove_module(self, module_id: int) -> dict:
        """Remove a module from a project (checks lock, cleans up workspace)."""
        module = self.get_module(module_id)
        module_name = module.library_module.name if module.library_module else "Unknown"
        project_id = module.project_id

        self.get_project(project_id)

        # Check module lock before deletion (heartbeat + fence-token, not Redis)
        from services.module_lock import ModuleLockService

        if ModuleLockService(self.db).is_held(module.id):
            holder = ModuleLockService(self.db).get_holder(module.id)
            raise ConflictError(
                "module",
                f"Cannot delete: operation in progress on this module (holder: {holder})",
            )

        # Clean up persistent workspace
        from services.workspace_manager import WorkspaceManager

        workspace = WorkspaceManager(self.db)
        workspace_cleaned = workspace.cleanup_module_workspace(module)
        if workspace_cleaned:
            logger.info(f"Cleaned up persistent workspace for module {module.id}")

        logger.info(f"Deleting module from database: {module.id}")
        self.db.delete(module)

        update_project_counts(self.db, project_id)
        logger.info(f"Module {module_name} removed successfully.")

        return {"success": True, "message": f"Module '{module_name}' removed from project"}

    # ================================================================
    # Status & Variables
    # ================================================================

    def get_module_status(self, module_id: int) -> dict:
        """Get current status of a module."""
        module = self.get_module(module_id)
        return {
            "id": module.id,
            "status": module.status,
            "last_deployed_at": module.last_deployed_at.isoformat() if module.last_deployed_at else None,
            "deployment_error": module.deployment_error,
            "stage_detail": module.stage_detail,
        }

    def get_module_variables(self, module_id: int) -> dict:
        """Get variables and schema for a project module."""
        module = self.get_module(module_id)
        variables = module.variable_overrides or {}

        schema = []
        if module.library_module and module.library_module.variables_schema:
            platform_vars = {"project_name", "environment", "common_tags"}
            schema = [
                v
                for v in module.library_module.variables_schema
                if v.get("name") not in platform_vars
            ]

        return {
            "exists": True,
            "variables": variables,
            "schema": schema,
            "module_id": module.id,
            "module_name": module.library_module.name if module.library_module else None,
            "path_in_project": module.path_in_project,
        }

    # ================================================================
    # Dependency Management
    # ================================================================

    def set_dependencies(self, module_id: int, dependency_ids: list[int]) -> dict:
        """Set dependencies for a module (validates no circular deps, same project)."""
        module = self.get_module(module_id)

        all_modules = (
            self.db.query(ProjectModule)
            .filter(ProjectModule.project_id == module.project_id)
            .all()
        )

        modules_dict = [
            {"id": m.id, "project_id": m.project_id, "dependencies": m.dependencies or []}
            for m in all_modules
        ]

        is_valid, error_msg = validate_dependencies(
            module_id=module_id,
            dependency_ids=dependency_ids,
            project_id=module.project_id,
            all_modules=modules_dict,
        )
        if not is_valid:
            raise BadRequestError(error_msg)

        module.dependencies = dependency_ids
        self.db.flush()
        self.db.refresh(module)

        return {
            "success": True,
            "message": "Dependencies updated successfully",
            "dependencies": module.dependencies,
        }

    def get_dependencies(self, module_id: int) -> dict:
        """Get dependencies for a module and their current status."""
        module = self.get_module(module_id)
        dependency_ids = module.dependencies or []

        if not dependency_ids:
            return {"module_id": module_id, "dependencies": [], "all_met": True}

        dep_modules = (
            self.db.query(ProjectModule)
            .options(
                joinedload(ProjectModule.library_module).load_only(
                    ModuleLibrary.id, ModuleLibrary.name,
                )
            )
            .filter(ProjectModule.id.in_(dependency_ids))
            .all()
        )

        dependencies_info = []
        all_met = True
        for dep in dep_modules:
            is_met = dep.status == "applied"
            if not is_met:
                all_met = False
            dependencies_info.append({
                "id": dep.id,
                "name": dep.library_module.name,
                "path_in_project": dep.path_in_project,
                "status": dep.status,
                "is_deployed": is_met,
            })

        return {
            "module_id": module_id,
            "dependencies": dependencies_info,
            "all_met": all_met,
        }

    def get_dependents(self, module_id: int) -> dict:
        """Get all modules that depend on this module."""
        module = self.get_module(module_id)

        all_modules = (
            self.db.query(ProjectModule)
            .options(
                joinedload(ProjectModule.library_module).load_only(
                    ModuleLibrary.id, ModuleLibrary.name,
                )
            )
            .filter(ProjectModule.project_id == module.project_id)
            .all()
        )

        dependents = []
        for mod in all_modules:
            if module_id in (mod.dependencies or []):
                dependents.append({
                    "id": mod.id,
                    "name": mod.library_module.name,
                    "path_in_project": mod.path_in_project,
                    "status": mod.status,
                })

        return {"module_id": module_id, "dependents": dependents, "count": len(dependents)}

    def calculate_deployment_order(self, project_id: int, use_existing_dependencies: bool = False) -> dict:
        """
        Auto-calculate deployment order for all modules in a project.
        Uses topological sort based on dependencies.

        By default, dependencies are (re)derived from each module's library-level
        dependency metadata. When ``use_existing_dependencies`` is True, the
        already-persisted project-module dependencies are used as-is — required by
        blueprint imports, whose ``depends_on`` edges live in the manifest (set via
        set_dependencies) rather than in the library modules.
        """
        self.get_project(project_id)

        modules = (
            self.db.query(ProjectModule)
            .options(
                joinedload(ProjectModule.library_module).load_only(
                    ModuleLibrary.id,
                    ModuleLibrary.name,
                    ModuleLibrary.path,
                    ModuleLibrary.dependencies,
                ).options(
                    undefer(ModuleLibrary.dependencies_metadata),
                )
            )
            .filter(ProjectModule.project_id == project_id)
            .all()
        )

        if not modules:
            return {"success": True, "message": "No modules to order", "updated": 0}

        # Blueprint-backed projects set their dependency edges from the manifest
        # before calling this; skip re-resolution so we don't overwrite them.
        enabled_modules = [m for m in modules if m.enabled]

        if not use_existing_dependencies:
            # Resolve dependencies for enabled modules only. Replace (not union) the stored
            # dependency list so removed modules don't ghost as stale IDs and stall the sequencer.
            # Always call detect_module_dependencies first — it already prefers dependencies_metadata
            # and falls back to generic category heuristics internally. When dependencies_metadata is
            # present, its result is authoritative (even an empty list — e.g. a base-layer module with
            # no deps) so the acyclic metadata is never overridden by a corrupt/cyclic flat name-list.
            # Only when there is no metadata AND detection found nothing do we fall back to the
            # module's flat dependency name-list (e.g. bare-metal/SSH BNK chain modules whose deps
            # don't match the generic heuristics).
            module_name_to_id = {
                m.library_module.name: m.id for m in enabled_modules if m.library_module
            }
            for module in enabled_modules:
                if not module.library_module:
                    continue

                other_enabled = [m for m in enabled_modules if m.id != module.id]
                detected = detect_module_dependencies(module.library_module, other_enabled)
                if module.library_module.dependencies_metadata or detected:
                    module.dependencies = detected
                    continue

                resolved_deps = []
                for dep_name in module.library_module.dependencies or []:
                    dep_id = module_name_to_id.get(dep_name)
                    if dep_id is None:
                        logger.warning(
                            f"Module '{module.library_module.name}' depends on "
                            f"'{dep_name}', which is not in this project"
                        )
                        continue
                    if dep_id != module.id:
                        resolved_deps.append(dep_id)
                module.dependencies = resolved_deps

        modules_dict = [{"id": m.id, "dependencies": m.dependencies or []} for m in enabled_modules]

        try:
            order_map = calculate_deployment_order(modules_dict)
            old_order = {m.id: m.deployment_order for m in modules}

            for module in enabled_modules:
                module.deployment_order = order_map[module.id]

            changes = [
                {
                    "id": module.id,
                    "name": module.library_module.name,
                    "old_order": old_order[module.id],
                    "new_order": module.deployment_order,
                }
                for module in enabled_modules
            ]

            return {
                "success": True,
                "message": f"Deployment order calculated for {len(enabled_modules)} modules",
                "updated": len(enabled_modules),
                "changes": sorted(changes, key=lambda x: x["new_order"]),
            }

        except CircularDependencyError as e:
            error_msg = str(e)
            module_id_to_name = {
                m.id: m.library_module.name for m in modules if m.library_module
            }
            for mod_id, mod_name in module_id_to_name.items():
                error_msg = error_msg.replace(str(mod_id), f"{mod_name}({mod_id})")
            raise BadRequestError(
                f"Circular dependency detected in module metadata: {error_msg}. "
                "Please check the module library definitions - these dependencies "
                "are defined in the modules themselves, not set by you."
            )
        except Exception as e:
            logger.error(f"Error calculating deployment order: {e}")
            raise InternalError(f"Failed to calculate order: {str(e)}")

    def get_dependency_graph(self, project_id: int) -> dict:
        """Get dependency graph structure for visualization."""
        project = self.get_project(project_id)

        modules = (
            self.db.query(ProjectModule)
            .options(
                joinedload(ProjectModule.library_module).load_only(
                    ModuleLibrary.id, ModuleLibrary.name,
                )
            )
            .filter(ProjectModule.project_id == project_id)
            .all()
        )

        modules_dict = [
            {
                "id": m.id,
                "name": m.library_module.name,
                "path_in_project": m.path_in_project,
                "status": m.status,
                "deployment_order": m.deployment_order,
                "dependencies": m.dependencies or [],
            }
            for m in modules
        ]

        graph = get_dependency_graph(modules_dict)

        return {
            "project_id": project_id,
            "project_name": project.name,
            "graph": graph,
            "module_count": len(modules),
        }

    # ================================================================
    # Execution — Task Creation
    # ================================================================

    def check_module_dependencies(self, module: ProjectModule) -> tuple[bool, list[str]]:
        """
        Check if a module's dependencies are all in 'applied' state.

        Returns:
            Tuple of (is_valid, list_of_unmet_dependency_messages)
        """
        if not module.dependencies:
            return True, []

        dependencies = (
            self.db.query(ProjectModule)
            .options(
                joinedload(ProjectModule.library_module).load_only(
                    ModuleLibrary.id, ModuleLibrary.name,
                )
            )
            .filter(
                ProjectModule.id.in_(module.dependencies),
                ProjectModule.project_id == module.project_id,
            )
            .all()
        )

        dep_map = {d.id: d for d in dependencies}
        unmet = []
        for dep_id in module.dependencies:
            dep = dep_map.get(dep_id)
            if not dep:
                unmet.append(f"Module ID {dep_id} (Missing)")
            elif dep.status != "applied":
                name = dep.library_module.name if dep.library_module else dep.path_in_project.split("/")[-1]
                unmet.append(f"{name} ({dep.status})")

        return len(unmet) == 0, unmet

    def create_task(
        self,
        task_type: str,
        module: ProjectModule,
        triggered_by: str = "user",
        meta_data: dict | None = None,
    ) -> Any:
        """
        Create a Task record in the database for an execution operation.

        Returns the created Task object (committed, refreshed).

        The commit is mandatory: callers immediately dispatch a celery task
        with task.id, and the worker may pick up the message before any
        deferred caller commit propagates — racing into a "Task X not found"
        ValueError. Reproduced 2026-05-04 driving destroys from a CLI script.
        Committing here closes the race for every submit_* method.

        ``meta_data`` is stamped onto the Task row; the destroy event chain reads
        ``meta_data["destroy_scope"]`` from it to decide how far the teardown
        propagates (see submit_destroy).
        """
        from models import Task as TaskModel

        task = TaskModel(
            task_type=task_type,
            status="queued",
            project_id=module.project_id,
            module_id=module.id,
            triggered_by=triggered_by,
            created_at=datetime.now(UTC),
            meta_data=meta_data,
        )
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        return task

    def _commit_before_dispatch(self, task, module: ProjectModule, queued_module_status: str) -> None:
        """Commit task row + module status BEFORE the Celery worker can see the message.

        Why: workers run in separate processes with their own DB sessions. Until this
        transaction commits, a worker that picks up the message cannot SELECT the task
        row (transaction isolation) and raises "Task X not found".
        """
        module.status = queued_module_status
        self.db.commit()
        self.db.refresh(task)
        self.db.refresh(module)

    def _record_celery_id(self, task, celery_result) -> dict:
        """Store celery_task_id in a second commit, after dispatch has succeeded."""
        task.celery_task_id = celery_result.id
        self.db.commit()
        return {
            "task_id": task.id,
            "celery_task_id": celery_result.id,
            "status": "queued",
        }

    def submit_init(self, module_id: int, triggered_by: str = "user") -> dict:
        """Submit an OpenTofu init task for a module."""
        module = self.get_module(module_id)

        if not module.library_module:
            raise BadRequestError("Module has no library module associated")
        if not module.library_module.git_source:
            raise BadRequestError("Module library has no git source configured")

        # Before create_task/_commit_before_dispatch: those commit a queued Task
        # row and flip module.status, so a rejection at dispatch time would
        # strand a transitional status and leave an orphan task behind.
        if not module.enabled:
            raise BadRequestError("Module is disabled — enable it before running init")

        task = self.create_task("init", module, triggered_by)
        self._commit_before_dispatch(task, module, "initializing")

        from services.execution.task_dispatch import dispatch_init
        celery_result = dispatch_init(task.id, module)
        dispatch_state = self._record_celery_id(task, celery_result)

        # Audit the state transition (self-loop is a no-op for status; it
        # still records a transition row so the audit log captures every
        # task_id/reason). The status was already set by
        # _commit_before_dispatch above to win the dispatch race.
        module.stage_detail = None
        from services.module_state import transition_module_status
        transition_module_status(
            self.db, module, to_status="initializing",
            task_id=task.id, reason=f"submit_init by {triggered_by}",
        )

        logger.info(f"Submitted init task {task.id} (celery: {celery_result.id}) for module {module_id}")

        return {
            "success": True,
            "message": "Module initialization queued",
            **dispatch_state,
        }

    def submit_plan(self, module_id: int, triggered_by: str = "user") -> dict:
        """Submit an engine-aware plan task for a module."""

        module = self.get_module(module_id)
        project = module.project

        # Validate
        validation = self._validate_for_operation(module, project, "plan")
        if not validation["valid"]:
            raise BadRequestError(
                "Module validation failed:\n" + "\n".join(f"- {e}" for e in validation["errors"])
            )
        if validation["warnings"]:
            logger.warning(f"Module {module_id} plan warnings: {validation['warnings']}")

        task = self.create_task("plan", module, triggered_by)
        self._commit_before_dispatch(task, module, "planning")

        from services.execution.task_dispatch import dispatch_plan

        celery_result = dispatch_plan(task.id, module)
        dispatch_state = self._record_celery_id(task, celery_result)

        # Audit the state transition (self-loop is a no-op for status).
        module.stage_detail = None
        from services.module_state import transition_module_status
        transition_module_status(
            self.db, module, to_status="planning",
            task_id=task.id, reason=f"submit_plan by {triggered_by}",
        )

        logger.info(f"Submitted plan task {task.id} (celery: {celery_result.id}) for module {module_id}")

        return {
            "success": True,
            "message": "Plan queued",
            **dispatch_state,
        }

    def submit_apply(self, module_id: int, triggered_by: str = "user", auto_approve: bool = False) -> dict:
        """Submit an OpenTofu apply task for a module.

        Args:
            module_id: ProjectModule ID to apply.
            triggered_by: Username of the user triggering the operation.
            auto_approve: Accepted for API compatibility; does not force a new plan.
                          The underlying tofu apply always runs with -auto-approve on plan.out,
                          so a valid saved plan (the one the user reviewed) is always applied
                          as-is unless it is invalid/stale, which is detected independently.
        """
        module = self.get_module(module_id)
        project = module.project

        # Validate
        validation = self._validate_for_operation(module, project, "apply")
        if not validation["valid"]:
            raise BadRequestError(
                "Module validation failed:\n" + "\n".join(f"- {e}" for e in validation["errors"])
            )
        if validation["warnings"]:
            logger.warning(f"Module {module_id} apply warnings: {validation['warnings']}")

        # Check dependencies
        deps_met, unmet_msgs = self.check_module_dependencies(module)
        if not deps_met:
            raise BadRequestError(
                f"Cannot apply: dependencies not met. Please deploy these modules first: "
                f"{', '.join(unmet_msgs)}"
            )

        # Create pre-apply snapshot
        self._create_snapshot(module, "pre_apply", "Auto-snapshot before apply")

        task = self.create_task("apply", module, triggered_by)
        self._commit_before_dispatch(task, module, "applying")

        from services.execution.task_dispatch import dispatch_apply
        celery_result = dispatch_apply(task.id, module, auto_approve=auto_approve)
        dispatch_state = self._record_celery_id(task, celery_result)

        # Audit the state transition (self-loop is a no-op for status).
        module.stage_detail = None
        from services.module_state import transition_module_status
        transition_module_status(
            self.db, module, to_status="applying",
            task_id=task.id, reason=f"submit_apply by {triggered_by}",
        )

        logger.info(f"Submitted apply task {task.id} (celery: {celery_result.id}) for module {module_id}")

        return {
            "success": True,
            "message": "Apply queued",
            **dispatch_state,
        }

    def submit_destroy(self, module_id: int, triggered_by: str = "user") -> dict:
        """Submit an OpenTofu destroy task for a module."""
        module = self.get_module(module_id)

        # A failed module (plan_failed/apply_failed/failed) may still hold partial
        # infra, so it must be destroyable. Keep this in exact agreement with the
        # frontend Destroy-menu gate (RUNNING_STATUSES + DESTROY_HIDDEN_STATUSES in
        # ModuleTableRow.tsx) so every offered Destroy is accepted.
        if module.status not in [
            "applied", "initialized", "planned",
            "plan_failed", "apply_failed", "destroy_failed", "failed",
        ]:
            raise BadRequestError("Can only destroy initialized, planned, applied, or failed modules")

        # Create pre-destroy snapshot
        self._create_snapshot(module, "pre_destroy", "Auto-snapshot before destroy")

        module.stage_detail = None

        # Scope the teardown to THIS module only.
        #
        # The destroy event chain (_trigger_next_destroy_module) walks
        # module.dependencies and queues a destroy for each one as the predecessor
        # reaches a terminal state. That reverse-DAG walk is correct for a whole
        # project or stack teardown, where every module is meant to come down.
        # It is wrong here: this endpoint destroys one named module, and the
        # modules it *depends on* are exactly the ones that must survive.
        #
        # Without this stamp the chain fell through to the stack_instance_id
        # heuristic, so destroying a blueprint's bnk-install cascaded down into
        # cluster-create and deleted the ROKS cluster BNK was installed on
        # (issue #525) — ~48 minutes of infrastructure removed by a request that
        # named only the application layer.
        task = self.create_task(
            "destroy", module, triggered_by, meta_data={"destroy_scope": "module"}
        )
        self._commit_before_dispatch(task, module, "destroying")

        from services.execution.task_dispatch import dispatch_destroy
        celery_result = dispatch_destroy(task.id, module)
        dispatch_state = self._record_celery_id(task, celery_result)

        # Audit the state transition (self-loop is a no-op for status).
        from services.module_state import transition_module_status
        transition_module_status(
            self.db, module, to_status="destroying",
            task_id=task.id, reason=f"submit_destroy by {triggered_by}",
        )

        logger.info(f"Submitted destroy task {task.id} (celery: {celery_result.id}) for module {module_id}")

        return {
            "success": True,
            "message": "Destroy queued",
            **dispatch_state,
        }

    def list_module_actions(self, module_id: int) -> dict:
        """List actions declared in a container module's artifact manifest (D-034).

        Modules without a pack manifest (or without an actions block) get an
        empty list — the UI may probe any module.
        """
        module = self.get_module(module_id)
        lib = module.library_module
        manifest = getattr(lib, "pack_manifest", None) if lib else None
        actions_block = manifest.get("actions") if isinstance(manifest, dict) else None

        actions = []
        if isinstance(actions_block, dict):
            for name, definition in actions_block.items():
                if not isinstance(definition, dict):
                    continue
                actions.append({
                    "name": name,
                    "title": definition.get("title") or name,
                    "description": definition.get("description"),
                    "rating": definition.get("rating") or "green",
                    "inputs": definition.get("inputs") or [],
                    # Optional note shown after the action completes — e.g. "left
                    # cluster objects; run Clean when done" (D-034 #458).
                    "cleanup_note": definition.get("cleanup_note"),
                })

        return {"module_id": module.id, "actions": actions, "total": len(actions)}

    def submit_action(
        self,
        module_id: int,
        action: str,
        inputs: dict | None = None,
        triggered_by: str = "user",
    ) -> dict:
        """Submit a manifest-declared action run for a container module (D-034).

        Gates (all fail fast at submit; the task re-checks the status gate):
        the module must be a container artifact, the manifest must declare the
        action, the module must be applied, and no operation may hold its lock.
        The module's status is never changed by an action run.
        """
        module = self.get_module(module_id)

        # An action runs the artifact's own image against live infrastructure,
        # so a disabled module must not be actionable either (issue #527).
        if not module.enabled:
            raise BadRequestError("Module is disabled — enable it before running actions")

        lib = module.library_module
        manifest = getattr(lib, "pack_manifest", None) if lib else None
        if not isinstance(manifest, dict) or not isinstance(manifest.get("container_image"), dict):
            raise BadRequestError(
                "Module actions require a container artifact (this module has no artifact manifest)"
            )

        actions_block = manifest.get("actions")
        declared = actions_block if isinstance(actions_block, dict) else {}
        if action not in declared:
            names = ", ".join(sorted(declared)) if declared else "none"
            raise BadRequestError(
                f"Module declares no action '{action}' (declared actions: {names})"
            )

        # Runtime input filter (D-034 F1/F2): validate the caller's inputs against
        # the action's declared inputs before dispatch — this is the single choke
        # point that gates both the sync API path and the task. Values become argv
        # tokens in a CLI holding cloud creds, so undeclared keys, out-of-range
        # enums, and flag-injection (leading-dash) strings are rejected here.
        declared_inputs = declared[action].get("inputs") if isinstance(declared[action], dict) else None
        try:
            effective_inputs = validate_action_inputs(declared_inputs, inputs)
        except ValueError as exc:
            raise BadRequestError(f"Invalid action input: {exc}") from exc

        if module.status != "applied":
            raise BadRequestError(
                f"Cannot run action '{action}': module must be in a post-apply state "
                f"(applied), current status is '{module.status}'"
            )

        from services.module_lock import ModuleLockService
        lock_service = ModuleLockService(self.db)
        if lock_service.is_held(module.id):
            holder = lock_service.get_holder(module.id)
            raise ConflictError(
                "module",
                f"Cannot run action: operation in progress on this module (holder: {holder})",
            )

        task = self.create_task("action", module, triggered_by)
        task.meta_data = {"action": action}
        # Commit before dispatch so the worker can SELECT the task row
        # (create_task committed the row; this persists meta_data too).
        self.db.commit()

        from services.execution.task_dispatch import dispatch_container_action
        celery_result = dispatch_container_action(task.id, module, action, effective_inputs)
        dispatch_state = self._record_celery_id(task, celery_result)

        logger.info(
            f"Submitted action '{action}' task {task.id} (celery: {celery_result.id}) for module {module_id}"
        )

        return {
            "success": True,
            "message": f"Action '{action}' queued",
            "action": action,
            **dispatch_state,
        }

    # How long cancel waits for the worker to confirm the container kill. Short
    # on purpose: this runs inside a request, and a slow answer degrades to
    # "unconfirmed", which keeps the lock rather than dropping it optimistically.
    _KILL_CONFIRM_TIMEOUT_SECONDS = 15

    @staticmethod
    def _container_substrate(module) -> str:
        """Where a container artifact's steps actually run: "docker" | "kubernetes".

        Mirrors the precedence in container_tasks._resolve_runner — explicit
        execution.container_runner.backend, else deploy_model ("helm" ⟹
        kubernetes), else docker.
        """
        lib = getattr(module, "library_module", None)
        manifest = getattr(lib, "pack_manifest", None) if lib else None
        if not isinstance(manifest, dict):
            return "docker"

        execution = manifest.get("execution")
        runner_cfg = (
            execution.get("container_runner") if isinstance(execution, dict) else None
        )
        backend = (
            (runner_cfg or {}).get("backend") if isinstance(runner_cfg, dict) else None
        )
        if isinstance(backend, str) and backend.strip():
            return backend.strip().lower()

        deploy_model = manifest.get("deploy_model")
        if isinstance(deploy_model, str) and deploy_model.strip().lower() == "helm":
            return "kubernetes"
        return "docker"

    def _kill_containers_for_tasks(self, module, tasks) -> tuple[list[str], bool]:
        """Kill the daemon-side step container(s), on the WORKER.

        Returns ``(killed_ids, confirmed)``. ``confirmed`` is False when the
        docker endpoint could not be reached at all — the caller must NOT
        release the module lock in that case.

        Dispatched as a Celery task rather than called inline. cancel_operation
        runs in the FastAPI ``backend`` service, which is built from the ``api``
        Dockerfile stage: it has no docker CLI (only the ``worker`` stage copies
        one) and compose sets DOCKER_HOST only on the celery services. Inline,
        `docker ps` raised FileNotFoundError, the runner swallowed it, and the
        caller saw "0 containers killed" — so the lock was force-released while
        the container kept running, which is precisely the corruption this whole
        path exists to prevent.
        """
        from services.execution.task_dispatch import get_engine_type

        try:
            if get_engine_type(module) != "container":
                return [], True          # nothing to kill; not an unknown state
        except Exception as exc:
            logger.warning("cancel: could not resolve engine for module %s: %s", module.id, exc)
            return [], True

        # Resolve the SUBSTRATE, not just the dispatch family. `get_engine_type`
        # returns "container" for a container artifact regardless of where its
        # steps actually run; the substrate is chosen separately from
        # execution.container_runner.backend, or inferred from deploy_model.
        #
        # A container artifact on Kubernetes therefore took the docker branch,
        # `docker ps --filter label=bnkforge.task=<id>` returned zero rows with
        # exit 0, and that read as a CONFIRMED kill: the lock was released and
        # the user told the deployment had stopped, while the Job ran on to its
        # active_deadline_seconds against live infrastructure — and a re-apply
        # reused the same PVC.
        #
        # There is no Kubernetes kill path to fall back to: the runner stamps no
        # bnkforge.task label (so nothing could select the Job even if a delete
        # existed), and the reaper is DockerRunner-only. So this is UNCONFIRMED,
        # which retains the lock — the honest answer until a Job-deletion path
        # and a label exist.
        substrate = self._container_substrate(module)
        if substrate != "docker":
            logger.warning(
                "cancel: module %s runs on the %s substrate, which has no kill "
                "path — reporting UNCONFIRMED so the lock is retained.",
                module.id, substrate,
            )
            return [], False

        celery_ids = [t.celery_task_id for t in tasks if t.celery_task_id]
        if not celery_ids:
            return [], True

        try:
            from tasks.container_tasks import kill_module_containers

            async_result = kill_module_containers.apply_async(
                (celery_ids,), queue="default"
            )
            outcome = async_result.get(timeout=self._KILL_CONFIRM_TIMEOUT_SECONDS)
        except Exception as exc:
            # Broker down, no worker, or timeout — all mean "unconfirmed".
            logger.warning(
                "cancel: container kill for module %s was not confirmed: %s",
                module.id, exc,
            )
            return [], False

        if not isinstance(outcome, dict):
            return [], False
        return list(outcome.get("killed") or []), bool(outcome.get("reachable"))

    def cancel_operation(self, module_id: int) -> dict:
        """Cancel a running deployment operation (revoke Celery task, reset status)."""
        from celery_app import celery_app
        from models import Task as TaskModel

        module = self.get_module(module_id)

        # Match queued/pending as well as in-progress. Module status flips to
        # applying/destroying at SUBMIT time, which is what the UI's Stop gate
        # keys off — so Stop is routinely clicked while the Celery task is still
        # queued. Matching only "in_progress" left that task un-revoked, and the
        # worker later picked it up and ran the full apply after the user had
        # been told it stopped (issue #462 F4, issue #527 part 2).
        cancellable = (
            self.db.query(TaskModel)
            .filter(
                and_(
                    TaskModel.module_id == module_id,
                    TaskModel.status.in_(["in_progress", "queued", "pending"]),
                )
            )
            .order_by(TaskModel.created_at.desc())
            .all()
        )
        # Guard on whether ANY cancellable task carries a celery id, not on the
        # newest one. create_task commits before dispatch stamps the id, and
        # _trigger_next_stack_module commits its "pending" row before calling
        # dispatch_apply — so a NEWER, id-less row can sit in front of the
        # running task. Keying the guard on cancellable[0] meant the whole
        # cancel was skipped in that window: the running task was never revoked,
        # no container was killed, and the caller was told
        # "Reset stuck deployment status" with success=True.
        dispatchable = [t for t in cancellable if t.celery_task_id]

        if dispatchable:
            try:
                # Revoke every non-terminal task for this module, not just the
                # newest — a queued task behind the running one is exactly the
                # zombie F4 describes.
                for task in dispatchable:
                    celery_app.control.revoke(
                        task.celery_task_id, terminate=True, signal="SIGKILL"
                    )
                    logger.info(f"Revoked Celery task {task.celery_task_id} for module {module_id}")

                # Kill the daemon-side container BEFORE releasing the lock, so the
                # lock is never dropped while a live container still holds the
                # workspace.
                killed, kill_confirmed = self._kill_containers_for_tasks(module, cancellable)

                # Release the module lock ONLY on a confirmed kill.
                #
                # The lock is what stops a re-apply racing a still-running
                # container over the same workspace. Dropping it because the
                # kill *reported* nothing — when in fact the daemon was
                # unreachable — hands the user a green light into exactly that
                # corruption. Unconfirmed keeps the lock; the reaper sweeps the
                # container and the janitor frees the lock on its own schedule.
                if kill_confirmed:
                    try:
                        from services.module_lock import ModuleLockService
                        ModuleLockService(self.db).force_release(module.id)
                        logger.info(f"Released module lock for module={module.id}")
                    except Exception as lock_err:
                        logger.warning(f"Failed to release module lock on cancel: {lock_err}")
                else:
                    logger.warning(
                        "cancel: module %s lock RETAINED — the container kill could "
                        "not be confirmed, so a re-apply must not be allowed to race it.",
                        module.id,
                    )

                # Mark every revoked task cancelled — leaving a queued row
                # "queued" is what let it look live to the rest of the system.
                for task in cancellable:
                    task.status = "cancelled"
                    task.error = "Operation cancelled by user"
                    task.completed_at = datetime.now(UTC)

                self._reset_module_status(module, "Operation cancelled by user")

                update_project_counts(self.db, module.project_id)

                if kill_confirmed:
                    message = "Deployment cancelled successfully"
                    if killed:
                        message += f" ({len(killed)} container(s) killed)"
                else:
                    # Say so. The previous shape returned success=True here,
                    # which is how "it stopped" became a claim nobody had checked.
                    message = (
                        "Cancellation requested, but the container could not be "
                        "confirmed stopped — the docker endpoint was unreachable. "
                        "The module stays locked until the reaper clears it; do "
                        "not re-apply yet."
                    )
                return {
                    "success": True,
                    "message": message,
                    "containers_killed": len(killed),
                    "containers_kill_confirmed": kill_confirmed,
                    "tasks_cancelled": len(cancellable),
                }

            except Exception as e:
                logger.error(f"Error cancelling deployment: {e}")
                raise InternalError(f"Failed to cancel deployment: {str(e)}")
        else:
            # No active task — reset stuck status if needed
            if module.status in ["initializing", "planning", "applying", "destroying"]:
                logger.warning(
                    f"No active task found but module {module_id} is in {module.status} state - resetting"
                )
                # A module stuck in a running state with no cancellable task row
                # is the signature of a lost worker. Its container may still be
                # running; the reaper (container_reaper) owns that sweep, since
                # it can tell a dead task's orphan from a live sibling's step.
                killed = []

                self._reset_module_status(module, "Stuck deployment reset by user")

                update_project_counts(self.db, module.project_id)

                message = "Reset stuck deployment status"
                if killed:
                    message += f" ({len(killed)} container(s) killed)"
                return {
                    "success": True,
                    "message": message,
                    "containers_killed": len(killed),
                    "containers_kill_confirmed": True,
                    "tasks_cancelled": 0,
                }

            return {
                "success": False,
                "message": "No active deployment found for this module",
                "containers_killed": 0,
                "containers_kill_confirmed": True,
                "tasks_cancelled": 0,
            }

    def deploy_module(self, module_id: int, triggered_by: str = "user") -> dict:
        """Trigger full init→plan→apply chain for a module.

        - not_initialized: auto-inits with auto_apply=True so run_opentofu_init
          queues apply on success *if all dependencies are satisfied at that point*.
          When dependencies are not yet ready, init completes but apply is skipped
          (the dependency-chain event in _tofu_helpers picks it up when deps land).
        - All other non-deploying states: dispatches apply directly (which
          internally handles plan when no valid saved plan exists).
        - Already deploying (APPLYING/INITIALIZING/PLANNING): raises BadRequestError.
        """
        module = self.get_module(module_id)

        # A disabled module is not runnable by ANY path (issue #527). This one
        # does not go through _validate_for_operation the way submit_plan and
        # submit_apply do, so without this check the endpoint the UI's Deploy
        # button calls would still deploy a disabled module — the headline fix
        # defeated by the most likely route a user takes.
        if not module.enabled:
            raise BadRequestError("Module is disabled — enable it before deploying")

        if module.status in (ModuleStatus.APPLYING, ModuleStatus.INITIALIZING, ModuleStatus.PLANNING):
            raise BadRequestError(f"Module is already deploying (current status: {module.status})")

        from services.execution.task_dispatch import dispatch_apply, dispatch_init
        from services.module_state import transition_module_status

        module.last_deployed_at = datetime.now(UTC)
        module.deployment_error = None
        module.stage_detail = None

        if module.status == "not_initialized":
            # Auto-init with auto_apply=True: the init task queues apply on success
            # when all dependencies are satisfied at that point.
            task = self.create_task("init", module, triggered_by)
            celery_result = dispatch_init(task.id, module, auto_apply=True)
            task.celery_task_id = celery_result.id
            transition_module_status(
                self.db, module, to_status="initializing",
                task_id=task.id, reason=f"deploy_module (auto-init) by {triggered_by}",
            )
            logger.info(f"Deploy-module (auto-init) task {task.id} queued for module {module_id}")
            message = "Init queued; apply will follow automatically once init succeeds and dependencies are ready"
        else:
            # Already initialized (or failed/planned/applied): dispatch apply directly.
            # run_opentofu_apply handles plan internally when no valid saved plan exists.
            task = self.create_task("apply", module, triggered_by)
            celery_result = dispatch_apply(task.id, module)
            task.celery_task_id = celery_result.id
            transition_module_status(
                self.db, module, to_status="applying",
                task_id=task.id, reason=f"deploy_module by {triggered_by}",
            )
            logger.info(f"Deployment task {task.id} queued for module {module_id}")
            message = "Deployment queued successfully"

        return {
            "task_id": task.id,
            "celery_task_id": celery_result.id,
            "module_id": module_id,
            "status": "queued",
            "started_at": module.last_deployed_at.isoformat(),
            "message": message,
        }

    def retry_deployment(self, module_id: int, triggered_by: str = "user") -> dict:
        """Retry a failed deployment (init or apply based on failure type)."""
        from models import DeploymentLog

        module = self.get_module(module_id)

        if not module.enabled:
            raise BadRequestError("Module is disabled — enable it before retrying")

        failed_statuses = ["failed", "apply_failed", "init_failed", "destroy_failed"]
        if module.status not in failed_statuses:
            raise BadRequestError(f"Can only retry failed deployments. Current status: {module.status}")

        previous_error = module.deployment_error

        # Determine correct retry type
        if module.status == "init_failed":
            retry_task_type = "init"
            retry_status = "initializing"
        else:
            retry_task_type = "apply"
            retry_status = "applying"

        task = self.create_task(retry_task_type, module, triggered_by)

        # Log retry
        log_entry = DeploymentLog(
            module_id=module_id,
            level="warning",
            message=f"Deployment retry ({retry_task_type}) queued for module '{module.library_module.name}'",
        )
        self.db.add(log_entry)

        if previous_error:
            error_log = DeploymentLog(
                module_id=module_id,
                level="info",
                message=f"Previous error: {previous_error[:200]}...",
            )
            self.db.add(error_log)

        # Dispatch correct task type
        from services.execution.task_dispatch import dispatch_apply, dispatch_init
        from services.workspace_manager import WorkspaceManager

        if retry_task_type == "init":
            celery_result = dispatch_init(task.id, module)
        else:
            workspace = WorkspaceManager(self.db)
            has_saved_plan = workspace.has_saved_plan(module)
            plan_valid = False
            if has_saved_plan:
                plan_valid, _ = workspace.plan_is_valid(module)
            celery_result = dispatch_apply(
                task.id,
                module,
                force_new_plan=(not has_saved_plan or not plan_valid),
            )

        task.celery_task_id = celery_result.id
        module.deployment_error = None
        module.stage_detail = None
        module.last_deployed_at = datetime.now(UTC)
        from services.module_state import transition_module_status
        transition_module_status(
            self.db, module, to_status=retry_status,
            task_id=task.id,
            reason=f"retry_deployment ({retry_task_type}) by {triggered_by}",
        )

        logger.info(f"Deployment retry task {task.id} queued for module {module_id}")

        return {
            "task_id": task.id,
            "celery_task_id": celery_result.id,
            "module_id": module_id,
            "status": "queued",
            "message": "Retry queued successfully",
        }

    # ================================================================
    # Plan Status (read-only, delegates to WorkspaceManager)
    # ================================================================

    def get_plan_status(self, module_id: int) -> dict:
        """Get plan status for a module (saved plan existence, validity, age)."""
        from services.workspace_manager import WorkspaceManager

        module = self.get_module(module_id)
        workspace = WorkspaceManager(self.db)

        is_initialized = workspace.is_initialized(module)
        has_plan = workspace.has_saved_plan(module)
        plan_valid = False
        reason_invalid = ""

        if has_plan:
            plan_valid, reason_invalid = workspace.plan_is_valid(module)
        else:
            reason_invalid = "No saved plan exists"

        plan_age_seconds = None
        if module.plan_serial and module.plan_serial > 0:
            if module.updated_at:
                plan_age_seconds = int((datetime.now(UTC) - module.updated_at).total_seconds())

        # Resource deltas from the most recent plan run. Every engine records
        # them on a Deployment(action="plan") row, so this is the authoritative
        # "does applying this change anything" signal — non-interactive callers
        # (the CLI's `project plan`) key their exit code off it.
        last_plan = (
            self.db.query(Deployment)
            .filter(Deployment.module_id == module_id, Deployment.action == "plan")
            .order_by(Deployment.started_at.desc(), Deployment.id.desc())
            .first()
        )
        resource_changes = None
        has_changes = None
        if last_plan is not None:
            resource_changes = {
                "add": last_plan.resources_to_add or 0,
                "change": last_plan.resources_to_change or 0,
                "destroy": last_plan.resources_to_destroy or 0,
            }
            has_changes = sum(resource_changes.values()) > 0

        return {
            "module_id": module_id,
            "has_plan": has_plan,
            "plan_valid": plan_valid,
            "plan_serial": module.plan_serial or 0,
            "plan_age_seconds": plan_age_seconds,
            "reason_invalid": reason_invalid,
            "workspace_initialized": is_initialized,
            "last_init_at": module.last_init_at.isoformat() if module.last_init_at else None,
            "vars_hash": module.vars_hash[:16] + "..." if module.vars_hash else None,
            # None when the module has never been planned — distinct from False
            # ("planned, nothing to do"), so a caller never reads "no plan yet"
            # as "no changes".
            "has_changes": has_changes,
            "resource_changes": resource_changes,
            "plan_output_preview": (
                module.plan_output[:500] + "..."
                if module.plan_output and len(module.plan_output) > 500
                else module.plan_output
            ),
        }

    def validate_module(self, module_id: int, operation: str = "plan") -> dict:
        """Validate a module before running plan or apply."""
        module = self.get_module(module_id)
        project = module.project
        validation = self._validate_for_operation(module, project, operation)

        return {
            "module_id": module_id,
            "operation": operation,
            "valid": validation["valid"],
            "errors": validation["errors"],
            "warnings": validation["warnings"],
            "status": module.status,
            "path": module.path_in_project,
        }

    # ================================================================
    # Private Helpers
    # ================================================================

    def _serialize_module(self, m: ProjectModule) -> dict:
        """Serialize a ProjectModule to a response dict."""
        # Infrastructure access normalization only applies to OpenTofu modules
        # that produce SSH keys. SSH/Ansible engine modules use their own
        # credential systems (BareMetalHost) and must not be polluted with
        # false "recovery_required" status.
        engine_type = (
            m.library_module.engine_type.strip().lower()
            if m.library_module and isinstance(m.library_module.engine_type, str)
            else None
        )
        if engine_type not in ("ssh", "ansible"):
            normalized = normalize_module_outputs_in_place(m)
            outputs = normalized.outputs
        else:
            # Clean up any previously injected false fields from earlier runs
            if isinstance(m.outputs, dict):
                polluted_keys = {
                    "infrastructure_access_status",
                    "infrastructure_access_message",
                    "infrastructure_private_key_available",
                    "infrastructure_private_key_path",
                }
                if polluted_keys & set(m.outputs.keys()):
                    for key in polluted_keys:
                        m.outputs.pop(key, None)
            outputs = m.outputs or {}
        engine_metadata = (
            serialize_engine_metadata(m.library_module)
            if m.library_module
            else {"engine_type": "opentofu"}
        )

        return {
            "id": m.id,
            "project_id": m.project_id,
            "module_library_id": m.module_library_id,
            "module_name": m.library_module.name if m.library_module else m.path_in_project.split("/")[-1],
            "category": m.library_module.category if m.library_module else "unknown",
            "provider": m.library_module.provider if m.library_module else "unknown",
            "path_in_project": m.path_in_project,
            "status": m.status,
            "enabled": m.enabled,
            "deployment_order": m.deployment_order,
            "dependencies": m.dependencies or [],
            "variable_overrides": m.variable_overrides,
            "last_deployed_at": m.last_deployed_at.isoformat() if m.last_deployed_at else None,
            "deployment_error": m.deployment_error,
            "stage_detail": m.stage_detail,
            "git_source": m.library_module.git_source if m.library_module else None,
            **engine_metadata,
            "outputs": outputs,
            "stack_instance_id": m.stack_instance_id,
            "library_module": {
                "id": m.library_module.id,
                "name": m.library_module.name,
                "category": m.library_module.category,
                "provider": m.library_module.provider,
                "source_path": m.library_module.source_path,
                "path": m.library_module.path,
                # D-033: the pin — which version row this project module runs,
                # and its source (change-version is scoped to the same source).
                "version": m.library_module.version,
                "module_source_id": m.library_module.module_source_id,
                "variables": _module_variables_from_library(m.library_module),
                **engine_metadata,
            }
            if m.library_module
            else None,
        }

    def _validate_module_variables(
        self,
        library_module: ModuleLibrary,
        variable_overrides: dict,
        context_resolved_var_names: set[str] | None = None,
    ) -> list[str]:
        """Validate variables against module schema. Raises on errors, returns warnings.

        Args:
            library_module: The library module whose schema to validate against.
            variable_overrides: The variable values to validate.
            context_resolved_var_names: Names of variables whose values are resolved
                by Forge at deploy time (e.g. credential_template, project, project_secret).
                These are excluded from the strict required-present check so that
                create-from-release does not reject them as empty at create time.
        """
        warnings = []
        schema_var_names: set[str] = set()
        skip_names: set[str] = context_resolved_var_names or set()

        if library_module.inputs_metadata:
            # Strip context-resolved vars from both the overrides AND the metadata
            # before handing off to validate_user_variables so it neither sees them
            # as missing-required nor chokes on an empty-string value.
            filtered_overrides = {k: v for k, v in variable_overrides.items() if k not in skip_names}
            filtered_metadata: dict[str, list[dict]] = {
                group: [
                    inp for inp in (library_module.inputs_metadata.get(group) or [])
                    if isinstance(inp, dict) and inp.get("name") not in skip_names
                ]
                for group in ("required", "optional")
            }
            is_valid, errors = VariableValidator.validate_user_variables(
                filtered_overrides, filtered_metadata
            )
            schema_var_names = {
                item["name"]
                for group_name in ("required", "optional")
                for item in (library_module.inputs_metadata.get(group_name) or [])
                if isinstance(item, dict) and item.get("name")
            }
        elif library_module.variables_schema:
            # Strip context-resolved vars from both the overrides AND the schema.
            filtered_overrides = {k: v for k, v in variable_overrides.items() if k not in skip_names}
            filtered_schema = [
                item for item in library_module.variables_schema
                if isinstance(item, dict) and item.get("name") not in skip_names
            ]
            is_valid, errors = VariableValidator.validate_variables(
                filtered_overrides, filtered_schema
            )
            schema_var_names = {
                item["name"]
                for item in (library_module.variables_schema or [])
                if isinstance(item, dict) and item.get("name")
            }
        else:
            is_valid, errors = True, []

        if not is_valid:
            raise BadRequestError(f"Variable validation failed: {'; '.join(errors)}")

        for var_name in variable_overrides.keys():
            if schema_var_names and var_name not in schema_var_names:
                warnings.append(f"Variable '{var_name}' is not defined in module schema")

        return warnings

    def _build_dependency_info(self, dependency_ids: list[int]) -> list[dict]:
        """Build dependency info dicts for response."""
        if not dependency_ids:
            return []
        info = []
        for dep_id in dependency_ids:
            dep_mod = self.db.query(ProjectModule).filter(ProjectModule.id == dep_id).first()
            if dep_mod and dep_mod.library_module:
                info.append({
                    "id": dep_id,
                    "name": dep_mod.library_module.name,
                    "path": dep_mod.path_in_project,
                })
        return info

    def _validate_for_operation(
        self, module: ProjectModule, project: Project, operation: str = "plan"
    ) -> dict:
        """
        Validate a module is ready for plan/apply operations.

        Returns dict with keys: valid, errors, warnings.
        """
        import os

        errors = []
        warnings = []

        # A disabled module is not runnable by any path (issue #527). The
        # dependency chain honours this too; enforcing it here as well means
        # "disabled" cannot be defeated by calling plan/apply directly.
        if not module.enabled:
            errors.append("Module is disabled — enable it before running plan or apply")

        if not module.library_module:
            errors.append("Module has no library module associated")
        elif not module.library_module.git_source:
            errors.append("Module library has no git source configured")

        if module.status in ("failed", "apply_failed"):
            warnings.append("Module previously failed - review errors before retrying")

        if operation == "apply":
            cloud_provider = getattr(project, "cloud_provider", None)
            if cloud_provider and cloud_provider not in ("on-prem", "kubernetes", "none"):
                # IMP-013: Check credential template (v2) or legacy encrypted creds
                has_credentials = (
                    project.credential_template_id
                    or project.cloud_credentials_encrypted
                    or os.environ.get("AWS_ACCESS_KEY_ID")
                )
                if not has_credentials:
                    warnings.append(
                        "Cloud credentials not configured. "
                        "Configure credentials in Project Settings -> Cloud Credentials."
                    )

        if module.variables:
            if module.variables.get("user_ip") == "0.0.0.0/0":
                warnings.append(
                    "user_ip is set to 0.0.0.0/0 (all IPs). "
                    "Consider restricting to your specific IP for security."
                )

        return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings}

    def _reset_module_status(self, module: ProjectModule, error_message: str) -> None:
        """Reset a module's status from an in-progress state to its previous safe state."""
        status_map = {
            "initializing": "not_initialized",
            "planning": "initialized",
            "applying": "planned",
            "destroying": "applied",
        }
        if module.status in status_map:
            module.status = status_map[module.status]
            module.deployment_error = error_message
            module.stage_detail = None  # drop the stale in-progress phase on cancel/reset

    def _create_snapshot(self, module: ProjectModule, snapshot_type: str, description: str) -> None:
        """Create a snapshot for rollback capability. Logs errors but doesn't raise."""
        try:
            from services.snapshot_service import SnapshotService

            svc = SnapshotService(self.db)
            snapshot = svc.create_snapshot(
                project_id=module.project_id,
                trigger=snapshot_type,
                label=description,
                username="system",
            )
            logger.info(f"Created {snapshot_type} snapshot {snapshot.id} for module {module.id}")
        except Exception as e:
            logger.error(f"Failed to create {snapshot_type} snapshot: {e}")
