"""Task Dispatch — routes module operations to the correct Celery task.

Dispatch is metadata-driven from the module catalog:
  1. execution_engine (normalized runtime metadata from catalog)
  2. engine_type (legacy alias metadata)
  3. default OpenTofu

Both kubernetes-direct and operator execution engines dispatch to the same
K8s task family; operator vs direct is resolved inside the task runtime.
"""

import logging

from services.engine_registry import engine_registry

logger = logging.getLogger(__name__)

_EXPLICIT_EXECUTION_ENGINES: set[str] = engine_registry.explicit_execution_engines()
_EXPLICIT_ENGINE_TYPES: set[str] = engine_registry.explicit_engine_types()


def _get_explicit_engine_type(module) -> str | None:
    """Return explicit stored engine type when present and recognized."""
    library_module = getattr(module, "library_module", None)
    if library_module is None:
        return None

    engine_type = getattr(library_module, "engine_type", None)
    if isinstance(engine_type, str):
        normalized = engine_type.strip().lower()
        if normalized in _EXPLICIT_ENGINE_TYPES:
            return normalized
        if normalized:
            logger.warning(
                "Unrecognized explicit engine_type '%s' for module %s; falling back to legacy dispatch",
                normalized,
                getattr(module, "id", "unknown"),
            )
    return None


def _get_explicit_execution_engine(module) -> str | None:
    """Return normalized execution engine metadata when present and recognized.

    Raises ValueError for a non-empty unrecognized value — silently routing to
    OpenTofu would deploy with the wrong engine and produce a confusing failure
    far from the root cause.
    """
    library_module = getattr(module, "library_module", None)
    if library_module is None:
        return None

    execution_engine = getattr(library_module, "execution_engine", None)
    if isinstance(execution_engine, str):
        normalized = execution_engine.strip().lower()
        if normalized in _EXPLICIT_EXECUTION_ENGINES:
            return normalized
        if normalized:
            raise ValueError(
                f"Unrecognized execution_engine '{normalized}' for module "
                f"{getattr(module, 'id', 'unknown')}. "
                f"Supported engines: {sorted(_EXPLICIT_EXECUTION_ENGINES)}"
            )
    return None


def _resolve_dispatch_engine(module) -> str:
    """Resolve dispatch family: ansible | kubernetes | opentofu."""
    explicit_execution_engine = _get_explicit_execution_engine(module)
    if explicit_execution_engine in {"kubernetes-direct", "operator"}:
        # Builtin modules with deploy_model="kubernetes" are legacy OpenTofu modules
        # that predate the K8s-native engine. Route them to OpenTofu.
        lib = getattr(module, "library_module", None)
        source_kind = (getattr(lib, "module_source_kind", "") or "").strip().lower() if lib else ""
        deploy_model = (getattr(lib, "deploy_model", "") or "").strip().lower() if lib else ""
        if source_kind == "builtin" and deploy_model not in {"helm", "manifests"}:
            return "opentofu"
        return "kubernetes"
    if explicit_execution_engine:
        return explicit_execution_engine

    # Legacy metadata alias; keep for callers still persisting engine_type.
    explicit_engine = _get_explicit_engine_type(module)
    if explicit_engine:
        return explicit_engine

    return "opentofu"


def dispatch_init(task_id: int, module, auto_apply: bool = False, force_reinit: bool = False):
    """
    Dispatch an init operation to the correct engine.

    Returns the Celery AsyncResult (same as .delay() would return).
    """
    dispatch_engine = _resolve_dispatch_engine(module)

    if dispatch_engine == "ssh":
        from tasks.ssh_tasks import run_ssh_init

        logger.info(f"Dispatching init for module {module.id} → SSH engine (explicit metadata)")
        return run_ssh_init.delay(task_id, module.id, auto_apply=auto_apply)

    if dispatch_engine == "tmos":
        from tasks.tmos_tasks import run_tmos_init

        logger.info(f"Dispatching init for module {module.id} → TMOS engine (explicit metadata)")
        return run_tmos_init.delay(task_id, module.id, auto_apply=auto_apply)

    if dispatch_engine == "cli-bnkctl":
        from tasks.cli_tasks import run_cli_init

        logger.info(f"Dispatching init for module {module.id} → CLI/bnkctl engine (explicit metadata)")
        return run_cli_init.apply_async(args=[task_id, module.id], kwargs={"auto_apply": auto_apply}, queue="cli")

    if dispatch_engine == "ansible":
        from tasks.ansible_tasks import run_ansible_init

        logger.info(f"Dispatching init for module {module.id} → Ansible engine (metadata)")
        return run_ansible_init.delay(task_id, module.id, auto_apply=auto_apply)

    if dispatch_engine == "container":
        from tasks.container_tasks import run_container_init

        logger.info(f"Dispatching init for module {module.id} → Container engine (metadata)")
        return run_container_init.delay(task_id, module.id, auto_apply=auto_apply)

    if dispatch_engine == "kubernetes":
        from tasks.kubernetes_tasks import run_k8s_init
        module_path = module.library_module.path if module.library_module else module.path_in_project
        logger.info(f"Dispatching init for module {module.id} ({module_path}) → K8s engine")
        return run_k8s_init.delay(task_id, module.id, auto_apply=auto_apply)

    from tasks.opentofu_tasks import run_opentofu_init

    logger.info(f"Dispatching init for module {module.id} → OpenTofu engine")
    return run_opentofu_init.delay(task_id, module.id, auto_apply=auto_apply, force_reinit=force_reinit)


def dispatch_plan(task_id: int, module):
    """Dispatch a plan operation to the correct engine."""
    dispatch_engine = _resolve_dispatch_engine(module)

    if dispatch_engine == "ssh":
        # SSH plan is a lightweight probe — runs as init with plan semantics.
        # No standalone plan task for SSH engine; plan is embedded in init/apply.
        logger.info(f"SSH plan for module {module.id} — delegating to init (probe only)")
        from tasks.ssh_tasks import run_ssh_init
        return run_ssh_init.delay(task_id, module.id, auto_apply=False)

    if dispatch_engine == "tmos":
        from tasks.tmos_tasks import run_tmos_plan

        logger.info(f"Dispatching plan for module {module.id} → TMOS engine (dry-run)")
        return run_tmos_plan.delay(task_id, module.id)

    if dispatch_engine == "cli-bnkctl":
        from tasks.cli_tasks import run_cli_plan

        logger.info(f"Dispatching plan for module {module.id} → CLI/bnkctl engine (dry-run)")
        return run_cli_plan.apply_async(args=[task_id, module.id], queue="cli")

    if dispatch_engine == "ansible":
        from tasks.ansible_tasks import run_ansible_plan

        logger.info(f"Dispatching plan for module {module.id} → Ansible engine (metadata)")
        return run_ansible_plan.delay(task_id, module.id)

    if dispatch_engine == "container":
        from tasks.container_tasks import run_container_plan

        logger.info(f"Dispatching plan for module {module.id} → Container engine (metadata)")
        return run_container_plan.delay(task_id, module.id)

    if dispatch_engine == "kubernetes":
        from tasks.kubernetes_tasks import run_k8s_plan

        module_path = module.library_module.path if module.library_module else module.path_in_project
        logger.info(f"Dispatching plan for module {module.id} ({module_path}) → K8s engine")
        return run_k8s_plan.delay(task_id, module.id)

    from tasks.opentofu_tasks import run_opentofu_plan

    logger.info(f"Dispatching plan for module {module.id} → OpenTofu engine")
    return run_opentofu_plan.delay(task_id, module.id)


def _safe_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _derive_container_time_limits(module, phase: str = "apply", action: str | None = None) -> dict:
    """Per-module Celery time limits derived from a container module's step budget.

    The global ``task_time_limit`` assumes a worst case that a manifest can legitimately
    exceed once per-step retry/backoff is declared (e.g. a long cluster build that
    retries). Compute the worst-case budget — Σ(step.timeout_seconds × retry.max_attempts)
    plus inter-attempt backoffs — and, when it exceeds the global limit, raise *this*
    task's limit to cover it. The value is capped just under the broker
    ``visibility_timeout`` so a long task is never redelivered (double-executed) by the
    broker; a manifest needing more than the ceiling is logged, not silently truncated.

    ``phase`` selects the step-set: "apply", "destroy", or "action" (with the
    action name). A destroy or a long e2e/scenario action can outlive the global
    limit exactly as an apply can — and being hard-killed mid-run leaves the
    module lock behind for the reclaim sweep, so all three need the same
    treatment (issue #463 F5).

    Returns ``apply_async`` kwargs, or ``{}`` to use the global defaults.
    """
    lib = getattr(module, "library_module", None)
    manifest = getattr(lib, "pack_manifest", None)
    if not isinstance(manifest, dict):
        return {}
    if phase == "action":
        declared = (manifest.get("actions") or {})
        block = declared.get(action) if isinstance(declared, dict) else None
        steps = (block or {}).get("steps") if isinstance(block, dict) else None
    else:
        steps = (manifest.get("steps") or {}).get(phase)
    if not isinstance(steps, list):
        return {}

    budget = 0
    for step in steps:
        if not isinstance(step, dict):
            continue
        timeout = _safe_int(step.get("timeout_seconds"), 1800)
        retry = step.get("retry") if isinstance(step.get("retry"), dict) else {}
        attempts = max(1, _safe_int(retry.get("max_attempts"), 1))
        backoff = max(0, _safe_int(retry.get("backoff_seconds"), 0))
        budget += timeout * attempts + backoff * (attempts - 1)

    from celery_app import celery_app

    conf = celery_app.conf
    global_hard = conf.task_time_limit or 7500
    hard = budget + 600  # headroom: image pull, workspace setup, outputs read
    if hard <= global_hard:
        return {}  # global default already covers it

    vis_timeout = (conf.broker_transport_options or {}).get("visibility_timeout", 10800)
    ceiling = vis_timeout - 600
    if hard > ceiling:
        logger.warning(
            "Module %s %s budget (%ss) exceeds the safe ceiling (%ss) below the broker "
            "visibility_timeout (%ss); capping. Reduce step retries or raise visibility_timeout.",
            module.id, phase, hard, ceiling, vis_timeout,
        )
        hard = ceiling
    return {"time_limit": hard, "soft_time_limit": max(global_hard, hard - 300)}


def dispatch_apply(task_id: int, module, force_new_plan: bool = False, auto_approve: bool = False):
    """
    Dispatch an apply operation to the correct engine.

    Args:
        task_id: Database ID of the Task record.
        module: ProjectModule instance.
        force_new_plan: Force running a new plan even if a saved plan exists.
        auto_approve: Accepted for API compatibility but not used to force a new plan.
                      The OpenTofu engine already applies with `-auto-approve` unconditionally,
                      so this flag must not discard a saved plan the user has already reviewed —
                      only `force_new_plan` (computed from saved-plan validity) should do that.

    Returns the Celery AsyncResult.
    """
    dispatch_engine = _resolve_dispatch_engine(module)

    if dispatch_engine == "ssh":
        from tasks.ssh_tasks import run_ssh_apply

        logger.info(f"Dispatching apply for module {module.id} → SSH engine (explicit metadata)")
        return run_ssh_apply.delay(task_id, module.id)

    if dispatch_engine == "tmos":
        from tasks.tmos_tasks import run_tmos_apply

        logger.info(f"Dispatching apply for module {module.id} → TMOS engine (explicit metadata)")
        return run_tmos_apply.delay(task_id, module.id)

    if dispatch_engine == "cli-bnkctl":
        from tasks.cli_tasks import run_cli_apply

        logger.info(f"Dispatching apply for module {module.id} → CLI/bnkctl engine (explicit metadata)")
        return run_cli_apply.apply_async(args=[task_id, module.id], queue="cli")

    if dispatch_engine == "ansible":
        from tasks.ansible_tasks import run_ansible_apply

        logger.info(f"Dispatching apply for module {module.id} → Ansible engine (metadata)")
        return run_ansible_apply.delay(task_id, module.id)

    if dispatch_engine == "container":
        from tasks.container_tasks import run_container_apply

        limits = _derive_container_time_limits(module, "apply")
        logger.info(
            f"Dispatching apply for module {module.id} → Container engine (metadata)"
            + (f" with derived time limits {limits}" if limits else "")
        )
        return run_container_apply.apply_async((task_id, module.id), **limits)

    if dispatch_engine == "kubernetes":
        from tasks.kubernetes_tasks import run_k8s_apply
        module_path = module.library_module.path if module.library_module else module.path_in_project
        logger.info(f"Dispatching apply for module {module.id} ({module_path}) → K8s engine")
        return run_k8s_apply.delay(task_id, module.id)

    from tasks.opentofu_tasks import run_opentofu_apply

    # auto_approve does NOT force a new plan — it must apply the plan the user reviewed.
    logger.info(f"Dispatching apply for module {module.id} → OpenTofu engine (force_new_plan={force_new_plan})")
    return run_opentofu_apply.delay(task_id, module.id, force_new_plan=force_new_plan)


def dispatch_destroy(task_id: int, module):
    """
    Dispatch a destroy operation to the correct engine.

    Returns the Celery AsyncResult.
    """
    dispatch_engine = _resolve_dispatch_engine(module)

    if dispatch_engine == "ssh":
        from tasks.ssh_tasks import run_ssh_destroy

        logger.info(f"Dispatching destroy for module {module.id} → SSH engine (explicit metadata)")
        return run_ssh_destroy.delay(task_id, module.id)

    if dispatch_engine == "tmos":
        from tasks.tmos_tasks import run_tmos_destroy

        logger.info(f"Dispatching destroy for module {module.id} → TMOS engine (explicit metadata)")
        return run_tmos_destroy.delay(task_id, module.id)

    if dispatch_engine == "cli-bnkctl":
        from tasks.cli_tasks import run_cli_destroy

        logger.info(f"Dispatching destroy for module {module.id} → CLI/bnkctl engine (explicit metadata)")
        return run_cli_destroy.apply_async(args=[task_id, module.id], queue="cli")

    if dispatch_engine == "ansible":
        from tasks.ansible_tasks import run_ansible_destroy

        logger.info(f"Dispatching destroy for module {module.id} → Ansible engine (metadata)")
        return run_ansible_destroy.delay(task_id, module.id)

    if dispatch_engine == "container":
        from tasks.container_tasks import run_container_destroy

        limits = _derive_container_time_limits(module, "destroy")
        logger.info(
            f"Dispatching destroy for module {module.id} → Container engine (metadata)"
            + (f" with derived time limits {limits}" if limits else "")
        )
        return run_container_destroy.apply_async((task_id, module.id), **limits)

    if dispatch_engine == "kubernetes":
        from tasks.kubernetes_tasks import run_k8s_destroy
        module_path = module.library_module.path if module.library_module else module.path_in_project
        logger.info(f"Dispatching destroy for module {module.id} ({module_path}) → K8s engine")
        return run_k8s_destroy.delay(task_id, module.id)

    from tasks.opentofu_tasks import run_opentofu_destroy

    logger.info(f"Dispatching destroy for module {module.id} → OpenTofu engine")
    return run_opentofu_destroy.delay(task_id, module.id)


def dispatch_container_action(task_id: int, module, action: str, action_inputs: dict | None = None):
    """Dispatch a manifest-declared module action run (D-034).

    Actions are a container-engine-only contract; any other engine is a
    caller error surfaced loudly rather than silently routed elsewhere.
    """
    dispatch_engine = _resolve_dispatch_engine(module)
    if dispatch_engine != "container":
        raise ValueError(
            f"Module {getattr(module, 'id', 'unknown')} uses engine '{dispatch_engine}'; "
            "module actions are only supported for container artifacts"
        )

    from tasks.container_tasks import run_container_action

    limits = _derive_container_time_limits(module, "action", action)
    logger.info(
        f"Dispatching action '{action}' for module {module.id} → Container engine"
        + (f" with derived time limits {limits}" if limits else "")
    )
    return run_container_action.apply_async(
        (task_id, module.id, action), {"action_inputs": action_inputs}, **limits
    )


def dispatch_apply_signature(task_id: int, module, force_new_plan: bool = False, auto_approve: bool = False):
    """
    Return a Celery signature for an apply operation, routed to the correct engine.

    Unlike dispatch_apply() which calls .delay() immediately, this returns a
    .s() signature suitable for use in Celery groups/chords (parallel execution).

    Args:
        task_id: Database ID of the Task record.
        module: ProjectModule instance.
        force_new_plan: Force running a new plan even if a saved plan exists.
        auto_approve: Accepted for API compatibility but not used to force a new plan
                      (see dispatch_apply docstring).
    """
    dispatch_engine = _resolve_dispatch_engine(module)

    if dispatch_engine == "ssh":
        from tasks.ssh_tasks import run_ssh_apply

        logger.info(f"Creating apply signature for module {module.id} → SSH engine (explicit metadata)")
        return run_ssh_apply.s(task_id, module.id)

    if dispatch_engine == "tmos":
        from tasks.tmos_tasks import run_tmos_apply

        logger.info(f"Creating apply signature for module {module.id} → TMOS engine (explicit metadata)")
        return run_tmos_apply.s(task_id, module.id)

    if dispatch_engine == "cli-bnkctl":
        from tasks.cli_tasks import run_cli_apply

        logger.info(f"Creating apply signature for module {module.id} → CLI/bnkctl engine (explicit metadata)")
        return run_cli_apply.s(task_id, module.id).set(queue="cli")

    if dispatch_engine == "ansible":
        from tasks.ansible_tasks import run_ansible_apply

        logger.info(f"Creating apply signature for module {module.id} → Ansible engine (metadata)")
        return run_ansible_apply.s(task_id, module.id)

    if dispatch_engine == "container":
        from tasks.container_tasks import run_container_apply

        limits = _derive_container_time_limits(module, "apply")
        logger.info(f"Creating apply signature for module {module.id} → Container engine (metadata)")
        return run_container_apply.s(task_id, module.id).set(**limits)

    if dispatch_engine == "kubernetes":
        from tasks.kubernetes_tasks import run_k8s_apply
        module_path = module.library_module.path if module.library_module else module.path_in_project
        logger.info(f"Creating apply signature for module {module.id} ({module_path}) → K8s engine")
        return run_k8s_apply.s(task_id, module.id)

    from tasks.opentofu_tasks import run_opentofu_apply

    logger.info(f"Creating apply signature for module {module.id} → OpenTofu engine (force_new_plan={force_new_plan})")
    return run_opentofu_apply.s(task_id, module.id, force_new_plan=force_new_plan)


def dispatch_destroy_signature(task_id: int, module):
    """
    Return a Celery signature for a destroy operation, routed to the correct engine.

    Unlike dispatch_destroy() which calls .delay() immediately, this returns a
    .s() signature suitable for use in Celery groups/chords (parallel execution).
    """
    dispatch_engine = _resolve_dispatch_engine(module)

    if dispatch_engine == "ssh":
        from tasks.ssh_tasks import run_ssh_destroy

        logger.info(f"Creating destroy signature for module {module.id} → SSH engine (explicit metadata)")
        return run_ssh_destroy.s(task_id, module.id)

    if dispatch_engine == "tmos":
        from tasks.tmos_tasks import run_tmos_destroy

        logger.info(f"Creating destroy signature for module {module.id} → TMOS engine (explicit metadata)")
        return run_tmos_destroy.s(task_id, module.id)

    if dispatch_engine == "cli-bnkctl":
        from tasks.cli_tasks import run_cli_destroy

        logger.info(f"Creating destroy signature for module {module.id} → CLI/bnkctl engine (explicit metadata)")
        return run_cli_destroy.s(task_id, module.id).set(queue="cli")

    if dispatch_engine == "ansible":
        from tasks.ansible_tasks import run_ansible_destroy

        logger.info(f"Creating destroy signature for module {module.id} → Ansible engine (metadata)")
        return run_ansible_destroy.s(task_id, module.id)

    if dispatch_engine == "container":
        from tasks.container_tasks import run_container_destroy

        limits = _derive_container_time_limits(module, "destroy")
        logger.info(f"Creating destroy signature for module {module.id} → Container engine (metadata)")
        return run_container_destroy.s(task_id, module.id).set(**limits)

    if dispatch_engine == "kubernetes":
        from tasks.kubernetes_tasks import run_k8s_destroy
        module_path = module.library_module.path if module.library_module else module.path_in_project
        logger.info(f"Creating destroy signature for module {module.id} ({module_path}) → K8s engine")
        return run_k8s_destroy.s(task_id, module.id)

    from tasks.opentofu_tasks import run_opentofu_destroy

    logger.info(f"Creating destroy signature for module {module.id} → OpenTofu engine")
    return run_opentofu_destroy.s(task_id, module.id)


def get_engine_type(module) -> str:
    """
    Get the engine type string for a module.

    Returns "ssh", "ansible", "kubernetes", or "opentofu".
    Note: For K8s modules, the actual sub-type (operator vs direct) is resolved
    at task execution time based on whether an operator is connected.
    """
    return _resolve_dispatch_engine(module)
