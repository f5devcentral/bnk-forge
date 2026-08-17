"""
Execution Orchestration Service for BNK-Forge v2.

Uses dependency layers but executes modules sequentially to avoid duplicate dispatch,
provider race pressure, and noisy concurrent task behavior.

D-001 Phase 3 S3b: ParallelExecution table dropped. PE record creation removed.
Run progress is now tracked entirely via Task rows (run_handle column).
"""

import logging
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from models import ModuleLibrary, Project, ProjectModule
from models.enums import ModuleStatus
from services.dependency_graph_service import DependencyGraphService

logger = logging.getLogger(__name__)


class ParallelExecutionService:
    """
    Service for orchestrating module execution.

    Handles:
    - Sequential deployment using dependency-layer ordering
    - Sequential destruction in reverse layer order
    - Time estimation metadata for UI planning
    - Progress tracking and status updates
    """

    # Average time estimates per module type (in minutes)
    # These are defaults - can be overridden with historical data
    DEFAULT_TIME_ESTIMATES = {
        "vpc": 5,
        "security": 3,
        "eks": 15,
        "rds": 10,
        "storage": 7,
        "gateway": 7,
        "route": 2,
        "policy": 2,
        "default": 5,  # Fallback for unknown modules
    }

    def __init__(self, db: Session):
        self.db = db
        self.graph_service = DependencyGraphService(db)

    def get_module_time_estimate(self, module: ProjectModule) -> int:
        """
        Estimate execution time for a module in minutes.

        Uses module name to match against known estimates.
        Falls back to default if no match found.

        Args:
            module: ProjectModule to estimate

        Returns:
            Estimated time in minutes
        """
        lib = module.library_module
        if not lib:
            return self.DEFAULT_TIME_ESTIMATES["default"]

        # Prefer an estimate the pack manifest declares
        # (module.estimated_time_minutes) — name heuristics can't know that a
        # ROKS cluster create takes ~45m, not the 5m default.
        manifest = getattr(lib, "pack_manifest", None)
        if isinstance(manifest, dict):
            declared = (manifest.get("module") or {}).get("estimated_time_minutes")
            if isinstance(declared, (int, float)) and not isinstance(declared, bool) and declared > 0:
                return int(declared)

        module_name = lib.name.lower()

        # Check for exact match
        if module_name in self.DEFAULT_TIME_ESTIMATES:
            return self.DEFAULT_TIME_ESTIMATES[module_name]

        # Check for partial matches
        for key, time in self.DEFAULT_TIME_ESTIMATES.items():
            if key in module_name:
                return time

        return self.DEFAULT_TIME_ESTIMATES["default"]

    def get_execution_plan(self, project_id: int) -> dict:
        """
        Generate execution plan with time estimates.

        Shows how modules will be grouped into layers and estimates
        time savings from parallel execution.

        Args:
            project_id: Project ID

        Returns:
            Dict with execution plan:
            {
                "layers": [
                    {
                        "layer_index": 0,
                        "modules": [{"id": 1, "name": "vpc", "estimated_time_minutes": 5}],
                        "estimated_time_minutes": 5
                    }
                ],
                "total_estimated_time_sequential": 40,
                "total_estimated_time_parallel": 27,
                "time_savings_percent": 32.5,
                "parallelization_factor": 1.48
            }
        """
        try:
            layers = self.graph_service.build_layers(project_id)

            if not layers:
                return {
                    "layers": [],
                    "total_estimated_time_sequential": 0,
                    "total_estimated_time_parallel": 0,
                    "time_savings_percent": 0,
                    "parallelization_factor": 1.0
                }

            plan_layers = []
            sequential_total = 0
            parallel_total = 0

            for layer_idx, modules_in_layer in enumerate(layers):
                layer_modules = []
                layer_max_time = 0

                for module in modules_in_layer:
                    estimated_time = self.get_module_time_estimate(module)
                    sequential_total += estimated_time
                    layer_max_time = max(layer_max_time, estimated_time)

                    layer_modules.append({
                        "id": module.id,
                        "name": module.library_module.name if module.library_module else f"Module {module.id}",
                        "path": module.library_module.path if module.library_module else "",
                        "estimated_time_minutes": estimated_time,
                        "status": module.status
                    })

                # Layer time is the max time of any module in the layer (parallel execution)
                parallel_total += layer_max_time

                plan_layers.append({
                    "layer_index": layer_idx,
                    "modules": layer_modules,
                    "module_count": len(layer_modules),
                    "estimated_time_minutes": layer_max_time,
                    "can_run_parallel": len(layer_modules) > 1
                })

            # Calculate time savings
            time_savings = 0
            time_savings_percent = 0
            parallelization_factor = 1.0

            if sequential_total > 0:
                time_savings = sequential_total - parallel_total
                time_savings_percent = round((time_savings / sequential_total) * 100, 1)
                parallelization_factor = round(sequential_total / parallel_total, 2)

            return {
                "layers": plan_layers,
                "total_layers": len(plan_layers),
                "total_modules": sum(len(layer) for layer in layers),
                "total_estimated_time_sequential": sequential_total,
                "total_estimated_time_parallel": parallel_total,
                "time_savings_minutes": time_savings,
                "time_savings_percent": time_savings_percent,
                "parallelization_factor": parallelization_factor
            }

        except ValueError as e:
            # Circular dependency or other error
            logger.error(f"Error generating execution plan: {e}")
            return {
                "error": str(e),
                "layers": [],
                "total_estimated_time_sequential": 0,
                "total_estimated_time_parallel": 0
            }

    def deploy_project_parallel(
        self,
        project_id: int,
        create_tasks: bool = True,
        triggered_by: str = "user"
    ) -> dict:
        """
        Initiate parallel deployment of all modules in project.

        Creates Celery task for orchestration or returns execution plan
        for client-side orchestration.

        Args:
            project_id: Project ID to deploy
            create_tasks: If True, creates Celery tasks. If False, returns plan only.

        Returns:
            Dict with deployment info:
            {
                "orchestrator_task_id": str (if create_tasks=True),
                "execution_plan": dict,
                "total_modules": int,
                "total_layers": int
            }
        """
        project = self.db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise ValueError(f"Project {project_id} not found")

        # Get execution plan
        plan = self.get_execution_plan(project_id)

        if "error" in plan:
            raise ValueError(f"Cannot deploy: {plan['error']}")

        if plan["total_modules"] == 0:
            # S14-031: Return full response shape to prevent frontend crashes
            return {
                "message": "No modules to deploy",
                "orchestrator_task_id": None,
                "execution_plan": None,
                "total_modules": 0,
                "total_layers": 0,
                "estimated_time_minutes": 0
            }

        if create_tasks:
            # D-001 Phase 3 (S3b): generate ONE stable run_handle for the whole run.
            # Written to every Task row created by this run so the orchestration
            # endpoint can scope progress queries to this run.
            # ParallelExecution record removed (table dropped in v2_119).
            run_handle = uuid4().hex

            # Dispatch the first wave directly. Each apply task triggers
            # _trigger_next_stack_module() on completion, which queues newly-ready
            # downstream modules — so we don't need a separate orchestrator task
            # walking layers.
            first_task_id = self._dispatch_first_wave(project_id, run_handle=run_handle)

            logger.info(
                f"Started chain-based deployment for project {project_id}: "
                f"{plan['total_modules']} modules in {plan['total_layers']} layers "
                f"(run_handle: {run_handle}, first wave task id: {first_task_id})"
            )

            return {
                "orchestrator_task_id": run_handle,
                "execution_plan": plan,
                "total_modules": plan["total_modules"],
                "total_layers": plan["total_layers"],
                "estimated_time_minutes": plan["total_estimated_time_parallel"]
            }
        else:
            # Return plan for client-side orchestration
            return {
                "execution_plan": plan,
                "total_modules": plan["total_modules"],
                "total_layers": plan["total_layers"],
                "estimated_time_minutes": plan["total_estimated_time_parallel"]
            }

    def destroy_project_parallel(
        self,
        project_id: int,
        create_tasks: bool = True,
        triggered_by: str = "user",
        force_destroy: bool = False,
    ) -> dict:
        """
        Initiate parallel destruction of all modules using the reverse-DAG event chain.

        Mirrors deploy_project_parallel: dispatches the first destroy wave (leaf modules —
        those with no deployed dependents), then each worker fires _trigger_next_destroy_module
        on completion to queue the next layer automatically.

        D-001 Phase 3 (event-chain mechanism, see architect-design.md PIVOT 2026-05-27).

        Args:
            project_id: Project ID to destroy
            create_tasks: If True, dispatches Celery tasks. If False, returns plan only.

        Returns:
            Dict with destruction info (same format as deploy_project_parallel)
        """
        project = self.db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise ValueError(f"Project {project_id} not found")

        # Get execution plan (layers used for plan display and wave dispatch)
        plan = self.get_execution_plan(project_id)

        if "error" in plan:
            raise ValueError(f"Cannot destroy: {plan['error']}")

        if plan["total_modules"] == 0:
            # S14-031: Return full response shape to prevent frontend crashes
            return {
                "message": "No modules to destroy",
                "orchestrator_task_id": None,
                "execution_plan": None,
                "total_modules": 0,
                "total_layers": 0,
                "estimated_time_minutes": 0
            }

        if create_tasks:
            # D-001 Phase 3 (S3b): generate ONE stable run_handle for the whole destroy run.
            # ParallelExecution record removed (table dropped in v2_119).
            run_handle = uuid4().hex

            # Auto-snapshot before destroy (best-effort, non-blocking)
            try:
                from services.snapshot_service import SnapshotService
                snap_service = SnapshotService(self.db)
                snap_service.create_snapshot(
                    project_id=project_id,
                    trigger="auto-before-destroy",
                )
                self.db.commit()
                logger.info("Auto-snapshot created for project %s before destroy", project_id)
            except Exception as snap_err:
                logger.warning(
                    "Auto-snapshot failed for project %s (non-blocking): %s",
                    project_id, snap_err,
                )

            # Dispatch first destroy wave: leaf modules with no deployed dependents
            first_task_id = self._dispatch_first_destroy_wave(
                project_id, run_handle=run_handle, force_destroy=force_destroy,
            )

            self.db.commit()

            logger.info(
                "Started event-chain destroy for project %s: %d modules in %d layers "
                "(run_handle: %s, first wave task id: %s)",
                project_id, plan["total_modules"], plan["total_layers"], run_handle, first_task_id,
            )

            return {
                "orchestrator_task_id": run_handle,
                "execution_plan": plan,
                "total_modules": plan["total_modules"],
                "total_layers": plan["total_layers"],
                "estimated_time_minutes": plan["total_estimated_time_parallel"]
            }
        else:
            return {
                "execution_plan": plan,
                "total_modules": plan["total_modules"],
                "total_layers": plan["total_layers"],
                "estimated_time_minutes": plan["total_estimated_time_parallel"]
            }

    def _dispatch_first_destroy_wave(
        self,
        project_id: int,
        run_handle: str | None = None,
        force_destroy: bool = False,
    ) -> str | None:
        """
        Dispatch destroy for every module with no deployed dependents (first wave).

        Mirrors _dispatch_first_wave for deploy. Each dispatch fires the engine
        destroy task directly; on completion, _trigger_next_destroy_module queues
        the next eligible dependency.

        Skip-no-infra: modules with status not in {applied, apply_failed} have no
        infrastructure to destroy and are counted as already-destroyed.

        Idempotency: skip if a non-terminal destroy Task already exists (S15-008).

        force_destroy: when True, ALL in-cluster modules (kubernetes-direct / operator /
            kubernetes engine) in the project are pre-marked as destroyed with an audit
            suffix BEFORE the first wave is computed.  This ensures non-in-cluster infra
            modules whose only deployed dependents were in-cluster are correctly identified
            as leaves and dispatched.  If no infra tasks are dispatched after pre-marking,
            the project destroy is finalized immediately (avoids run_handle with zero rows).

        Args:
            project_id: Project to destroy.
            run_handle: Stable run identifier (uuid4 hex) written onto every Task row
                this wave creates. Passed from destroy_project_parallel (S2).
            force_destroy: Skip in-cluster modules instead of dispatching them.

        Returns the Celery task id of the first task dispatched (stable UI handle),
        or None if nothing needed dispatching.
        """
        from models import Task as TaskModel
        from models.enums import ModuleStatus, TaskStatus
        from services.execution.task_dispatch import dispatch_destroy_signature
        from tasks.parallel_tasks import NO_INFRA_STATUSES, is_in_cluster_module

        modules = (
            self.db.query(ProjectModule)
            .filter(ProjectModule.project_id == project_id)
            .all()
        )
        if not modules:
            return None

        # force_destroy pre-pass: mark ALL in-cluster modules as destroyed before
        # computing the first wave.  This ensures infra modules that ONLY had in-cluster
        # dependents are correctly identified as first-wave leaves (their deployed
        # dependents are now in NO_INFRA_STATUSES after pre-marking).
        if force_destroy:
            for module in modules:
                if module.status in NO_INFRA_STATUSES:
                    continue
                if is_in_cluster_module(module):
                    module.status = ModuleStatus.DESTROYED.value
                    module.deployment_error = (
                        (module.deployment_error or "") +
                        " [force-destroyed: skipped by force_destroy flag]"
                    )[:2000]
                    logger.info(
                        "force_destroy pre-pass: marking in-cluster module %s (%s) as destroyed",
                        module.id, module.path_in_project,
                    )
            self.db.flush()
            # Refresh the modules list so the dispatch loop sees updated statuses
            self.db.expire_all()
            modules = (
                self.db.query(ProjectModule)
                .filter(ProjectModule.project_id == project_id)
                .all()
            )

        graph_service = DependencyGraphService(self.db)
        first_task_id: str | None = None

        for module in modules:
            # Skip modules with no infra (includes in-cluster modules pre-marked above)
            if module.status in NO_INFRA_STATUSES:
                continue

            # Find modules that depend on this module (its dependents)
            dependents = graph_service.get_reverse_dependencies(module.id, project_id)

            # Only dispatch if no deployed dependents remain (leaf in destroy order).
            # After the force_destroy pre-pass, in-cluster dependents are now in
            # NO_INFRA_STATUSES, so they are naturally excluded here.
            deployed_dependents = [
                d for d in dependents
                if d.status not in NO_INFRA_STATUSES
            ]
            if deployed_dependents:
                continue  # Not a leaf yet — will be triggered when dependents complete

            # Idempotency: skip if non-terminal destroy task already exists (S15-008)
            existing = self.db.query(TaskModel).filter(
                TaskModel.module_id == module.id,
                TaskModel.task_type == "destroy",
                TaskModel.status.in_([
                    TaskStatus.PENDING.value,
                    TaskStatus.QUEUED.value,
                    TaskStatus.IN_PROGRESS.value,
                ]),
            ).first()
            if existing:
                # ADOPT the in-flight task into this run rather than merely
                # skipping it.
                #
                # Skipping left a module-scoped destroy (from
                # POST /project-modules/{id}/destroy) as the newest — and only —
                # destroy task for the module. When its worker finished, the
                # chain guard read destroy_scope="module" off that very row and
                # returned before chaining AND before terminal detection: the
                # module's dependencies were never queued (live cloud infra
                # stranded) and the stack/project sat in DESTROYING forever,
                # because "applied" is not a terminal destroy status.
                #
                # Restamping makes the executing task a member of this run, so
                # the guard resolves "project" and the chain proceeds. Scope
                # belongs to the teardown run, not to whichever request happened
                # to create the row.
                meta = dict(existing.meta_data or {})
                if meta.get("destroy_scope") != "project" or not existing.run_handle:
                    meta["destroy_scope"] = "project"
                    existing.meta_data = meta
                    existing.run_handle = existing.run_handle or run_handle
                    self.db.flush()
                    logger.info(
                        "First destroy wave: adopted in-flight task %s for module %s "
                        "into run %s (was %s)",
                        existing.id, module.id, run_handle,
                        (existing.meta_data or {}).get("destroy_scope"),
                    )
                else:
                    logger.info(
                        "First destroy wave: skipping module %s — task %s already %s "
                        "and already in this run",
                        module.id, existing.id, existing.status,
                    )
                if first_task_id is None:
                    first_task_id = existing.celery_task_id
                continue

            # Create Task row and dispatch.
            # Stamp destroy_scope so downstream trigger/detection code can
            # determine project vs stack scope without relying on stack_instance_id
            # (blueprint modules have stack_instance_id but are project-scope destroys).
            task = TaskModel(
                task_type="destroy",
                status=TaskStatus.QUEUED.value,
                project_id=module.project_id,
                module_id=module.id,
                created_at=datetime.now(UTC),
                run_handle=run_handle,  # S2: stamp run_handle onto every Task row
                meta_data={"destroy_scope": "project"},
            )
            self.db.add(task)
            module.status = ModuleStatus.DESTROYING.value
            self.db.flush()
            self.db.refresh(task)

            sig = dispatch_destroy_signature(task.id, module)
            async_result = sig.apply_async()
            task.celery_task_id = async_result.id

            if first_task_id is None:
                first_task_id = async_result.id

            logger.info(
                "First destroy wave: dispatched destroy task %s (celery %s) for module %s "
                "(run_handle: %s)",
                task.id, async_result.id, module.id, run_handle,
            )

        self.db.commit()

        # force_destroy post-check: if no infra tasks were dispatched (all modules were
        # in-cluster and got pre-marked), the run has zero Task rows.  Finalize directly
        # so the orchestration endpoint returns a terminal state rather than 404.
        if force_destroy and first_task_id is None:
            try:
                from tasks.parallel_tasks import _finalize_destroy
                _finalize_destroy(self.db, "project", project_id)
                logger.info(
                    "force_destroy: all modules were in-cluster — finalized project %s immediately",
                    project_id,
                )
            except Exception as fin_err:
                logger.warning(
                    "force_destroy: finalization failed for project %s: %s",
                    project_id, fin_err,
                )

        return first_task_id

    def _dispatch_first_wave(self, project_id: int, run_handle: str | None = None) -> str | None:
        """Queue init+auto_apply (or apply) for every module whose dependencies
        are already satisfied. The chain in `_trigger_next_stack_module` queues
        the rest as predecessors complete, so we dispatch only the first wave.

        Args:
            project_id: Project to deploy.
            run_handle: Stable run identifier (uuid4 hex) written onto every Task row
                this wave creates. Passed from deploy_project_parallel (S2).

        Returns the Celery task id of the first task we dispatched (or None when
        nothing needed dispatching), used as a stable handle for the UI.
        """
        from datetime import UTC, datetime

        from models import Task as TaskModel
        from models.enums import ModuleStatus, TaskStatus
        from services.execution.task_dispatch import dispatch_apply, dispatch_init
        from services.workspace_manager import WorkspaceManager

        modules = (
            self.db.query(ProjectModule)
            .filter(ProjectModule.project_id == project_id)
            .order_by(ProjectModule.deployment_order)
            .all()
        )
        if not modules:
            return None

        workspace = WorkspaceManager(self.db)
        first_task_id: str | None = None

        modules_by_id = {m.id: m for m in modules}

        for module in modules:
            # A disabled module is not runnable (issue #527). _check_missing_variables
            # already skips these, so dispatching one here also meant deploying a
            # module whose required variables were never validated.
            if not module.enabled:
                logger.info(
                    "Skipping first-wave dispatch for module %s — module is disabled",
                    module.id,
                )
                continue

            if module.status == ModuleStatus.APPLIED and not workspace.vars_changed(module):
                continue

            existing = self.db.query(TaskModel).filter(
                TaskModel.module_id == module.id,
                TaskModel.status.in_([TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.IN_PROGRESS]),
            ).first()
            if existing:
                logger.info(
                    "Skipping first-wave dispatch for module %s — task %s already %s",
                    module.id, existing.id, existing.status,
                )
                continue

            deps_ready = True
            for dep_id in (module.dependencies or []):
                dep = modules_by_id.get(dep_id) or self.db.query(ProjectModule).filter(
                    ProjectModule.id == dep_id
                ).first()
                if not dep or dep.status != ModuleStatus.APPLIED:
                    deps_ready = False
                    break
            if not deps_ready:
                continue

            if module.status == ModuleStatus.NOT_INITIALIZED:
                task = TaskModel(
                    task_type="init",
                    status=TaskStatus.QUEUED,
                    project_id=module.project_id,
                    module_id=module.id,
                    created_at=datetime.now(UTC),
                    run_handle=run_handle,  # S2: stamp run_handle
                )
                self.db.add(task)
                self.db.flush()
                self.db.refresh(task)
                celery_result = dispatch_init(task.id, module, auto_apply=True)
                task.celery_task_id = celery_result.id
                module.status = ModuleStatus.INITIALIZING
                if first_task_id is None:
                    first_task_id = celery_result.id
                logger.info(f"First wave: queued init+auto_apply task {task.id} for module {module.id}")
                continue

            task = TaskModel(
                task_type="apply",
                status=TaskStatus.QUEUED,
                project_id=module.project_id,
                module_id=module.id,
                created_at=datetime.now(UTC),
                run_handle=run_handle,  # S2: stamp run_handle
            )
            self.db.add(task)
            self.db.flush()
            self.db.refresh(task)

            has_saved_plan = workspace.has_saved_plan(module)
            plan_valid = False
            plan_serial = module.plan_serial or 0
            if has_saved_plan and plan_serial > 0:
                plan_valid, _ = workspace.plan_is_valid(module)

            celery_result = dispatch_apply(
                task.id, module,
                force_new_plan=(not has_saved_plan or plan_serial <= 0 or not plan_valid),
            )
            task.celery_task_id = celery_result.id
            module.status = ModuleStatus.APPLYING
            if first_task_id is None:
                first_task_id = celery_result.id
            logger.info(f"First wave: queued apply task {task.id} for module {module.id}")

        return first_task_id

    def validate_project_ready(self, project_id: int, action: str) -> tuple[bool, str | None]:
        """
        Validate that project is ready for parallel execution.

        Args:
            project_id: Project ID
            action: "deploy" or "destroy"

        Returns:
            Tuple of (is_ready, error_message)
        """
        # Check for circular dependencies
        is_valid, cycle = self.graph_service.validate_no_cycles(project_id)
        if not is_valid:
            return False, f"Circular dependency detected: {cycle}"

        # Get all modules
        modules = self.db.query(ProjectModule).filter(
            ProjectModule.project_id == project_id
        ).all()

        if not modules:
            return False, "No modules in project"

        if action == "deploy":
            # Check if any modules are already in progress
            in_progress = [m for m in modules if m.status in [ModuleStatus.INITIALIZING, ModuleStatus.PLANNING, ModuleStatus.APPLYING]]
            if in_progress:
                return False, f"{len(in_progress)} modules already in progress"

            # CP-004: Pre-flight variable validation — check required user variables
            missing_vars = self._check_missing_variables(modules)
            if missing_vars:
                details = "; ".join(
                    f"{m}: {', '.join(v)}" for m, v in missing_vars.items()
                )
                return False, f"Missing required variables — {details}"

        elif action == "destroy":
            # Check if any modules are not deployed
            not_applied = [m for m in modules if m.status not in [ModuleStatus.APPLIED, ModuleStatus.APPLY_FAILED]]
            if not_applied:
                logger.warning(
                    f"{len(not_applied)} modules not in 'applied' state, will skip during destroy"
                )
                # Not an error - we can skip non-applied modules

        return True, None

    def _check_missing_variables(self, modules: list[ProjectModule]) -> dict[str, list[str]]:
        """
        CP-004: Check that all required user-provided variables are set for each module.
        Returns dict of {module_name: [missing_var_names]} for modules with missing vars.
        Skips variables with source="module" (auto-wired) or source="project" (auto-filled).
        """
        missing = {}
        for module in modules:
            if not module.enabled:
                continue
            # Already applied — no need to check
            if module.status == ModuleStatus.APPLIED:
                continue

            lib_module = None
            if module.module_library_id:
                lib_module = self.db.query(ModuleLibrary).filter(
                    ModuleLibrary.id == module.module_library_id
                ).first()

            if not lib_module or not lib_module.inputs_metadata:
                continue

            required_inputs = lib_module.inputs_metadata.get("required", [])
            user_vars = module.variable_overrides or module.variables or {}

            # Also consider project-level variables
            project = self.db.query(Project).filter(Project.id == module.project_id).first()
            project_vars = {}
            if project and project.project_variables:
                pv = project.project_variables
                if isinstance(pv, dict):
                    project_vars = pv.get('variable_defaults', pv) if 'variable_defaults' in pv else pv

            # Merge all available variable sources
            all_available = {**project_vars, **user_vars}

            module_missing = []
            for inp in required_inputs:
                source = inp.get("source", "user")
                # Skip auto-wired (from dependency outputs) and project-sourced vars
                if source in ("module", "project"):
                    continue
                # Skip if it has a default value
                if inp.get("default") is not None:
                    continue
                var_name = inp.get("name", "")
                if var_name and var_name not in all_available:
                    module_missing.append(var_name)

            if module_missing:
                module_name = module.path_in_project.split("/")[-1] if module.path_in_project else f"module-{module.id}"
                missing[module_name] = module_missing

        return missing
