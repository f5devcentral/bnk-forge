"""Cloud-blueprint step functions (S1: deploy → gate-poll → guaranteed teardown).

Each step takes a `Context` and returns a `StepResult`. Cloud steps check
for cloud credentials at the top of each function and return `skipped`
when they are absent — so the whole `cloud` phase is green on a plain
dev box with no AWS keys.

State keys written/consumed:

  ctx.state["cloud_release_id"]      int       resolved release id
  ctx.state["cloud_project_id"]      int       project created from release
  ctx.state["cloud_deploy_exec_id"]  int       integer exec id from parallel-executions list
  ctx.state["cloud_cluster_id"]      int       cluster id for readiness / license gates
  ctx.state["cloud_stack_ids"]       list[int] stack ids for delete on teardown

Teardown is NOT a step in the normal plan — it is injected as a `finally`
block in `__main__.py` using `run_cloud_teardown()` so it runs even on
exception / timeout / KeyboardInterrupt.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from typing import Any

from .client import BnkForgeApiError, BnkForgeClient
from .result import StepRecorder, StepResult
from .traffic import (
    TRAFFIC_PATTERNS,
    EICETunnelRunner,
    TunnelRunner,
    run_traffic_pattern,
)

logger = logging.getLogger("e2e.cloud_steps")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _cloud_creds_present(cloud_provider: str) -> bool:
    """Return True iff the required cloud credentials are in the environment.

    For AWS: AWS_ACCESS_KEY_ID **and** AWS_SESSION_TOKEN must be set.
    GCP / Azure stubs always return False (S3 adds real checks).
    """
    if cloud_provider == "aws":
        return bool(
            os.environ.get("AWS_ACCESS_KEY_ID")
            and os.environ.get("AWS_SESSION_TOKEN")
        )
    # gcp / azure — skip until S3 catalogs land
    return False


def _require_state(ctx: Context, key: str) -> Any:
    v = ctx.state.get(key)
    if v is None:
        raise RuntimeError(
            f"cloud step prerequisite missing: '{key}' not in state. "
            "Run earlier cloud steps first or include them via --steps cloud.",
        )
    return v


# ---------------------------------------------------------------------------
# Context (re-exported from steps.py for cloud_steps; avoids circular import)
# ---------------------------------------------------------------------------

# We import Context here so callers only need to import from this module.
from .steps import Context  # noqa: E402 — after stdlib/third-party imports

# ---------------------------------------------------------------------------
# Step: resolve_release
# ---------------------------------------------------------------------------


def step_cloud_resolve_release(ctx: Context) -> StepResult:
    """Resolve and validate the configured blueprint release.

    Checks that `validation_state == 'valid'` and
    `release_state in {imported, approved}`. Stores `cloud_release_id`
    in context so later steps don't need to re-look it up.
    """
    with StepRecorder("cloud_resolve_release") as r:
        cfg = ctx.cfg
        cloud_cfg = getattr(cfg, "cloud", None)
        if cloud_cfg is None:
            r.skip("no [cloud] section in config")
            return r.to_result()
        provider = cloud_cfg.cloud_provider or "aws"
        if not _cloud_creds_present(provider):
            r.skip(f"cloud_provider={provider}: credentials not in environment")
            return r.to_result()

        # Use explicit release_id if given; otherwise search by name/version.
        if cloud_cfg.release_id is not None:
            rel = ctx.client.get_release(cloud_cfg.release_id)
        else:
            releases = ctx.client.list_blueprint_releases()
            candidates = [
                x for x in releases
                if (
                    cloud_cfg.release_name is None
                    or cloud_cfg.release_name.lower() in x.get("blueprint_name", "").lower()
                )
                and (
                    cloud_cfg.release_version is None
                    or x.get("blueprint_version") == cloud_cfg.release_version
                )
            ]
            if not candidates:
                r.fail(
                    f"no blueprint release found matching "
                    f"name={cloud_cfg.release_name!r} "
                    f"version={cloud_cfg.release_version!r}",
                )
                return r.to_result()
            # Prefer the most recently created one.
            rel = max(candidates, key=lambda x: x.get("id", 0))

        release_id = rel.get("id")
        validation_state = rel.get("validation_state")
        release_state = rel.get("release_state")

        r.record(
            release_id=release_id,
            blueprint_name=rel.get("blueprint_name"),
            blueprint_version=rel.get("blueprint_version"),
            validation_state=validation_state,
            release_state=release_state,
        )

        if validation_state != "valid":
            r.fail(
                f"release id={release_id} has validation_state={validation_state!r}; "
                "must be 'valid' before deployment",
            )
            return r.to_result()
        if release_state not in {"imported", "approved"}:
            r.fail(
                f"release id={release_id} has release_state={release_state!r}; "
                "must be 'imported' or 'approved'",
            )
            return r.to_result()

        ctx.state["cloud_release_id"] = release_id
        r.ok(
            f"resolved release id={release_id} "
            f"({rel.get('blueprint_name')} {rel.get('blueprint_version')})",
        )
    return r.to_result()


# ---------------------------------------------------------------------------
# Step: create_project_from_release
# ---------------------------------------------------------------------------


def step_cloud_create_project(ctx: Context) -> StepResult:
    """Create a forge project from the resolved blueprint release."""
    with StepRecorder("cloud_create_project") as r:
        cloud_cfg = getattr(ctx.cfg, "cloud", None)
        if cloud_cfg is None:
            r.skip("no [cloud] section in config")
            return r.to_result()
        provider = cloud_cfg.cloud_provider or "aws"
        if not _cloud_creds_present(provider):
            r.skip(f"cloud_provider={provider}: credentials not in environment")
            return r.to_result()

        release_id = _require_state(ctx, "cloud_release_id")
        ts = time.strftime("%Y%m%d-%H%M%S")
        name = f"{ctx.cfg.project_name_prefix}cloud-{ts}"

        body = ctx.client.create_project_from_release(
            release_id,
            cloud_provider=provider,
            region=cloud_cfg.region,
            credential_template_id=cloud_cfg.credential_template_id,
            variables=cloud_cfg.variables or {},
            name=name,
        )
        project_id = body.get("project_id")
        if not isinstance(project_id, int):
            raise RuntimeError(f"create_project_from_release returned no project_id: {body}")

        ctx.state["cloud_project_id"] = project_id
        r.ok(f"created project '{name}' id={project_id}")
        r.record(
            project_id=project_id,
            project_name=name,
            module_count=body.get("module_count"),
        )
    return r.to_result()


# ---------------------------------------------------------------------------
# Step: deploy_all
# ---------------------------------------------------------------------------


def step_cloud_deploy_all(ctx: Context) -> StepResult:
    """Trigger deploy-all and store the run_handle."""
    with StepRecorder("cloud_deploy_all") as r:
        cloud_cfg = getattr(ctx.cfg, "cloud", None)
        if cloud_cfg is None:
            r.skip("no [cloud] section in config")
            return r.to_result()
        provider = cloud_cfg.cloud_provider or "aws"
        if not _cloud_creds_present(provider):
            r.skip(f"cloud_provider={provider}: credentials not in environment")
            return r.to_result()

        project_id = _require_state(ctx, "cloud_project_id")
        body = ctx.client.deploy_all(project_id)

        exec_id = _find_latest_exec_id(ctx.client, project_id)
        ctx.state["cloud_deploy_exec_id"] = exec_id
        r.ok(f"deploy-all started exec_id={exec_id}")
        r.record(
            exec_id=exec_id,
            orchestrator_task_id=body.get("orchestrator_task_id"),
            total_modules=body.get("total_modules"),
            total_layers=body.get("total_layers"),
        )
    return r.to_result()


# ---------------------------------------------------------------------------
# Step: wait_deploy_terminal
# ---------------------------------------------------------------------------


def step_cloud_wait_deploy_terminal(ctx: Context) -> StepResult:
    """Poll the orchestration endpoint until status is completed or failed."""
    with StepRecorder("cloud_wait_deploy_terminal") as r:
        cloud_cfg = getattr(ctx.cfg, "cloud", None)
        if cloud_cfg is None:
            r.skip("no [cloud] section in config")
            return r.to_result()
        provider = cloud_cfg.cloud_provider or "aws"
        if not _cloud_creds_present(provider):
            r.skip(f"cloud_provider={provider}: credentials not in environment")
            return r.to_result()

        project_id = _require_state(ctx, "cloud_project_id")
        exec_id = _require_state(ctx, "cloud_deploy_exec_id")
        timeout = ctx.cfg.timeouts.cloud_deploy

        final = _wait_for_run_terminal(
            ctx.client, project_id, exec_id,
            timeout=timeout,
            poll_interval=ctx.cfg.timeouts.poll_interval,
        )
        status = final.get("status")
        r.record(
            exec_id=exec_id,
            status=status,
            progress_percent=final.get("progress_percent"),
            failed_modules=final.get("failed_modules"),
            error_message=final.get("error_message"),
        )

        # Stash cluster_id for later steps (comes from successful_modules context
        # but the readiness gate needs it — we extract it from the deploy response
        # if the provider adds it, or leave it to the operator config).
        cluster_id = cloud_cfg.cluster_id
        if cluster_id is not None:
            ctx.state["cloud_cluster_id"] = cluster_id

        if status == "failed":
            r.fail(
                f"deploy run failed: {final.get('error_message') or 'see evidence'}",
            )
            return r.to_result()
        if status != "completed":
            r.fail(f"deploy run reached unexpected status={status!r}")
            return r.to_result()

        r.ok(
            f"deploy completed: "
            f"{len(final.get('successful_modules') or [])} module(s) ok",
        )
    return r.to_result()


# ---------------------------------------------------------------------------
# Step: wait_bnk_ready
# ---------------------------------------------------------------------------


def step_cloud_wait_bnk_ready(ctx: Context) -> StepResult:
    """Poll the BNK health gate until platform severity is not 'unknown'.

    'healthy' overall (no 'critical') is the acceptance bar — we use
    the platform.flo and platform.controller severity fields to detect
    readiness, mirroring the D-029 spec.
    """
    with StepRecorder("cloud_wait_bnk_ready") as r:
        cloud_cfg = getattr(ctx.cfg, "cloud", None)
        if cloud_cfg is None:
            r.skip("no [cloud] section in config")
            return r.to_result()
        provider = cloud_cfg.cloud_provider or "aws"
        if not _cloud_creds_present(provider):
            r.skip(f"cloud_provider={provider}: credentials not in environment")
            return r.to_result()

        cluster_id = ctx.state.get("cloud_cluster_id")
        if cluster_id is None:
            r.skip("cloud_cluster_id not set — BNK readiness gate skipped")
            return r.to_result()

        timeout = ctx.cfg.timeouts.cloud_bnk_ready
        final = _wait_for_bnk_ready(
            ctx.client, cluster_id,
            timeout=timeout,
            poll_interval=ctx.cfg.timeouts.poll_interval,
        )
        overall = final.get("overall", "unknown")
        platform = final.get("platform") or {}
        r.record(
            overall=overall,
            platform_severity=platform.get("severity"),
            counts=final.get("counts"),
        )

        if overall in ("critical", "unknown"):
            r.fail(f"BNK health overall={overall!r} after waiting {timeout}s")
            return r.to_result()

        r.ok(f"BNK health overall={overall!r}")
    return r.to_result()


# ---------------------------------------------------------------------------
# Step: wait_license_active
# ---------------------------------------------------------------------------


def step_cloud_wait_license_active(ctx: Context) -> StepResult:
    """Poll the license gate; if not Active and a JWT is configured, activate.

    The license_state field in the status response comes from CWC and is
    a string such as 'Active', 'Device Registration Failed', etc.  We
    treat any value in {'active', 'licensed', 'verification complete'} as
    success (case-insensitive), mirroring the qkview_service convention.
    """
    with StepRecorder("cloud_wait_license_active") as r:
        cloud_cfg = getattr(ctx.cfg, "cloud", None)
        if cloud_cfg is None:
            r.skip("no [cloud] section in config")
            return r.to_result()
        provider = cloud_cfg.cloud_provider or "aws"
        if not _cloud_creds_present(provider):
            r.skip(f"cloud_provider={provider}: credentials not in environment")
            return r.to_result()

        cluster_id = ctx.state.get("cloud_cluster_id")
        if cluster_id is None:
            r.skip("cloud_cluster_id not set — license gate skipped")
            return r.to_result()

        jwt = cloud_cfg.license_jwt
        timeout = ctx.cfg.timeouts.cloud_license

        final = _wait_for_license_active(
            ctx.client, cluster_id, jwt=jwt,
            timeout=timeout,
            poll_interval=ctx.cfg.timeouts.poll_interval,
        )
        license_state = final.get("license_state", "unknown")
        r.record(license_state=license_state, raw=final)

        _ACTIVE_STATES = {"active", "licensed", "verification complete"}
        if license_state.lower() not in _ACTIVE_STATES:
            r.fail(f"license_state={license_state!r} after {timeout}s")
            return r.to_result()

        r.ok(f"license active: {license_state}")
    return r.to_result()


# ---------------------------------------------------------------------------
# Step: cloud_traffic
# ---------------------------------------------------------------------------


def step_cloud_traffic(
    ctx: Context,
    *,
    _runner_factory: Any = None,
) -> StepResult:
    """Run SSH+EICE TMM traffic probes via configured patterns.

    Gate: skips (green) unless ALL of the following hold:
      - cloud credentials present (AWS_ACCESS_KEY_ID + AWS_SESSION_TOKEN)
      - `aws` and `ssh` are on PATH
      - cloud.traffic section is present, enabled=True, and has a
        jumphost_instance_id and source_interface_ip set
    """
    with StepRecorder("cloud_traffic") as r:
        cloud_cfg = getattr(ctx.cfg, "cloud", None)
        if cloud_cfg is None:
            r.skip("no [cloud] section in config")
            return r.to_result()
        provider = cloud_cfg.cloud_provider or "aws"
        if not _cloud_creds_present(provider):
            r.skip(f"cloud_provider={provider}: credentials not in environment")
            return r.to_result()
        if not shutil.which("aws"):
            r.skip("'aws' CLI not on PATH — traffic step skipped")
            return r.to_result()
        if not shutil.which("ssh"):
            r.skip("'ssh' not on PATH — traffic step skipped")
            return r.to_result()

        traffic_cfg = getattr(cloud_cfg, "traffic", None)
        if traffic_cfg is None:
            r.skip("no [cloud.traffic] section in config")
            return r.to_result()
        if not traffic_cfg.enabled:
            r.skip("cloud.traffic.enabled=false")
            return r.to_result()
        if not traffic_cfg.jumphost_instance_id:
            r.skip("cloud.traffic.jumphost_instance_id not configured")
            return r.to_result()
        if not traffic_cfg.source_interface_ip:
            r.skip("cloud.traffic.source_interface_ip not configured")
            return r.to_result()

        vip = traffic_cfg.vip or ""
        if not vip:
            r.fail("cloud.traffic.vip is required for traffic probes")
            return r.to_result()

        region = traffic_cfg.region or cloud_cfg.region or ""
        if not region:
            r.fail("cloud.traffic.region (or cloud.region) is required")
            return r.to_result()

        pattern_names = traffic_cfg.patterns or ["http-routing-e2e"]
        unknown = [n for n in pattern_names if n not in TRAFFIC_PATTERNS]
        if unknown:
            r.fail(f"unknown traffic pattern(s): {', '.join(unknown)}")
            return r.to_result()

        failed_patterns: list[str] = []
        all_evidence: dict[str, Any] = {}

        for pattern_name in pattern_names:
            pattern = TRAFFIC_PATTERNS[pattern_name]

            if _runner_factory is not None:
                runner: TunnelRunner = _runner_factory(pattern_name)
            else:
                eice_runner = EICETunnelRunner(
                    instance_id=traffic_cfg.jumphost_instance_id,
                    region=region,
                    instance_os_user=traffic_cfg.instance_os_user,
                )
                eice_runner.prepare()
                runner = eice_runner

            try:
                result = run_traffic_pattern(
                    pattern,
                    runner,
                    vip=vip,
                    src_ip=traffic_cfg.source_interface_ip,
                    iterations=traffic_cfg.iterations,
                    timeout=traffic_cfg.timeout_seconds,
                )
            finally:
                if isinstance(runner, EICETunnelRunner):
                    runner.cleanup()

            all_evidence[pattern_name] = {
                "ok": result.ok,
                "summary": result.summary,
                "probes": result.probes,
            }
            if not result.ok:
                failed_patterns.append(f"{pattern_name}: {result.summary}")

        r.record(patterns=all_evidence)

        if failed_patterns:
            r.fail(f"{len(failed_patterns)} pattern(s) failed: " + "; ".join(failed_patterns))
            return r.to_result()

        r.ok(f"{len(pattern_names)} pattern(s) passed: {', '.join(pattern_names)}")
    return r.to_result()


# ---------------------------------------------------------------------------
# Step: destroy_all (used in both the normal plan and the finally-teardown)
# ---------------------------------------------------------------------------


def step_cloud_destroy_all(ctx: Context) -> StepResult:
    """Trigger destroy-all and poll to terminal.

    This step is added to the NORMAL PLAN (so it appears in the report
    table). It is ALSO invoked by `run_cloud_teardown()` from the
    `__main__.py` finally block to handle early-failure teardown.  The
    two paths are the same function body.
    """
    with StepRecorder("cloud_destroy_all") as r:
        cloud_cfg = getattr(ctx.cfg, "cloud", None)
        if cloud_cfg is None:
            r.skip("no [cloud] section in config")
            return r.to_result()
        provider = cloud_cfg.cloud_provider or "aws"
        if not _cloud_creds_present(provider):
            r.skip(f"cloud_provider={provider}: credentials not in environment")
            return r.to_result()

        project_id = ctx.state.get("cloud_project_id")
        if project_id is None:
            r.skip("no cloud_project_id in state — nothing to destroy")
            return r.to_result()

        # Trigger destroy, then find the integer exec id from the list endpoint
        ctx.client.destroy_all(project_id)
        exec_id = _find_latest_exec_id(ctx.client, project_id)

        timeout = ctx.cfg.timeouts.cloud_destroy
        final = _wait_for_run_terminal(
            ctx.client, project_id, exec_id,
            timeout=timeout,
            poll_interval=ctx.cfg.timeouts.poll_interval,
        )
        status = final.get("status")
        r.record(
            exec_id=exec_id,
            status=status,
            progress_percent=final.get("progress_percent"),
            failed_modules=final.get("failed_modules"),
            error_message=final.get("error_message"),
        )

        if status == "failed":
            r.fail(
                f"destroy run failed: {final.get('error_message') or 'see evidence'}",
            )
            return r.to_result()
        if status != "completed":
            r.fail(f"destroy run reached unexpected status={status!r}")
            return r.to_result()

        r.ok(f"destroy completed exec_id={exec_id}")
    return r.to_result()


# ---------------------------------------------------------------------------
# Step: delete_project
# ---------------------------------------------------------------------------


def step_cloud_delete_project(ctx: Context) -> StepResult:
    """Delete stacks then delete the project record.

    Deletes each stack with force=True, then deletes the project.
    If destroy_all succeeded there should be nothing left to force-delete
    in the stacks, but we pass force=True as a safety net.
    """
    with StepRecorder("cloud_delete_project") as r:
        cloud_cfg = getattr(ctx.cfg, "cloud", None)
        if cloud_cfg is None:
            r.skip("no [cloud] section in config")
            return r.to_result()
        provider = cloud_cfg.cloud_provider or "aws"
        if not _cloud_creds_present(provider):
            r.skip(f"cloud_provider={provider}: credentials not in environment")
            return r.to_result()

        project_id = ctx.state.get("cloud_project_id")
        if project_id is None:
            r.skip("no cloud_project_id in state — nothing to delete")
            return r.to_result()

        # Delete stacks (force) then project
        stack_ids: list[int] = ctx.state.get("cloud_stack_ids") or []
        deleted_stacks: list[int] = []
        stack_errors: list[str] = []
        for sid in stack_ids:
            try:
                ctx.client.delete_stack(project_id, sid, force=True)
                deleted_stacks.append(sid)
            except BnkForgeApiError as exc:
                stack_errors.append(f"stack {sid}: {exc.message}")

        # Delete the project itself
        try:
            ctx.client.delete_project(project_id)
        except BnkForgeApiError as exc:
            r.fail(f"project delete failed: {exc.message}")
            r.record(project_id=project_id, stack_errors=stack_errors)
            return r.to_result()

        # Clear state so the finally-teardown in __main__ knows we already deleted
        ctx.state.pop("cloud_project_id", None)

        r.record(
            project_id=project_id,
            deleted_stacks=deleted_stacks,
            stack_errors=stack_errors,
        )
        if stack_errors:
            r.warn(
                f"project deleted but {len(stack_errors)} stack delete(s) had errors — "
                "see evidence",
            )
        else:
            r.ok(f"project id={project_id} deleted")
    return r.to_result()


# ---------------------------------------------------------------------------
# Finally-teardown entry point (called by __main__.py)
# ---------------------------------------------------------------------------


def run_cloud_teardown(ctx: Context) -> StepResult:
    """Guaranteed teardown called from the finally block in __main__.py.

    Only acts when a `cloud_project_id` is still in state (i.e. the normal
    `destroy_all` + `delete_project` plan steps did not complete successfully).
    A failed teardown yields a `fail` StepResult — leaked cloud resources
    must be visible in the report.
    """
    project_id = ctx.state.get("cloud_project_id")
    if project_id is None:
        # Either delete_project already ran (cleared the key) or the run
        # never reached create_project — nothing to clean up.
        with StepRecorder("cloud_teardown_finally") as r:
            r.skip("no cloud_project_id — teardown not needed")
        return r.to_result()

    with StepRecorder("cloud_teardown_finally") as r:
        logger.info(
            "cloud_teardown_finally: project_id=%s — running guaranteed teardown",
            project_id,
        )
        timeout = getattr(
            ctx.cfg.timeouts, "cloud_destroy",
            ctx.cfg.timeouts.total,
        )
        poll_interval = ctx.cfg.timeouts.poll_interval

        try:
            ctx.client.destroy_all(project_id)
            exec_id = _find_latest_exec_id(ctx.client, project_id)
            final = _wait_for_run_terminal(
                ctx.client, project_id, exec_id,
                timeout=timeout,
                poll_interval=poll_interval,
            )
            status = final.get("status")
            r.record(exec_id=exec_id, status=status)
            if status != "completed":
                r.fail(
                    f"teardown destroy run did not complete "
                    f"(status={status!r}): POSSIBLE LEAKED CLOUD RESOURCES — "
                    "manual cleanup required",
                )
                return r.to_result()

            # Best-effort project delete after destroy
            try:
                ctx.client.delete_project(project_id)
                ctx.state.pop("cloud_project_id", None)
            except BnkForgeApiError as exc:
                r.warn(
                    f"destroy completed but project delete failed: {exc.message}",
                )
                return r.to_result()

            r.ok(f"teardown: project id={project_id} destroyed and deleted")

        except Exception as exc:  # noqa: BLE001 — must not propagate from finally
            r.fail(
                f"teardown failed: {type(exc).__name__}: {exc} — "
                "POSSIBLE LEAKED CLOUD RESOURCES — manual cleanup required",
            )
    return r.to_result()


# ---------------------------------------------------------------------------
# Polling helpers
# ---------------------------------------------------------------------------


def _find_latest_exec_id(client: BnkForgeClient, project_id: int) -> int:
    """List parallel-executions and return the id of the most-recent record.

    The list endpoint returns records newest-first (ordered by created_at desc).
    We take the first entry whose status is 'pending' or 'in_progress'; if none
    is in-flight we fall back to the most-recent record overall.  Raises
    RuntimeError when the list is empty.
    """
    records = client.get_parallel_executions(project_id)
    if not records:
        raise RuntimeError(
            f"parallel-executions list for project_id={project_id} is empty — "
            "no execution record found after deploy/destroy"
        )
    # Prefer the newest in-flight record; fall back to the newest overall.
    in_flight = [r for r in records if r.get("status") in ("pending", "in_progress")]
    chosen = in_flight[0] if in_flight else records[0]
    exec_id = chosen.get("id")
    if not isinstance(exec_id, int):
        raise RuntimeError(
            f"parallel-executions record missing integer 'id' field: {chosen}"
        )
    return exec_id


def _wait_for_run_terminal(
    client: BnkForgeClient,
    project_id: int,
    exec_id: int,
    *,
    timeout: int,
    poll_interval: int,
) -> dict[str, Any]:
    """Poll GET /api/projects/{project_id}/parallel-executions/{exec_id} until
    status is 'completed' or 'failed'.  Raises TimeoutError on cap.

    exec_id is the integer DB row id returned by the parallel-executions list
    endpoint — NOT the Celery orchestrator_task_id string.
    """
    deadline = time.time() + timeout
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = client.get_parallel_execution_status(project_id, exec_id)
        status = last.get("status")
        if status in ("completed", "failed"):
            return last
        time.sleep(poll_interval)
    raise TimeoutError(
        f"exec_id={exec_id} did not reach terminal status in "
        f"{timeout}s (last status={last.get('status')!r})",
    )


def _wait_for_bnk_ready(
    client: BnkForgeClient,
    cluster_id: int,
    *,
    timeout: int,
    poll_interval: int,
) -> dict[str, Any]:
    """Poll GET /api/k8s/clusters/{cluster_id}/f5bnk/health until
    overall severity is not 'unknown' (i.e. at least something has come up).
    Returns the last health snapshot."""
    deadline = time.time() + timeout
    last: dict[str, Any] = {}
    while time.time() < deadline:
        try:
            last = client.get_bnk_health(cluster_id)
        except BnkForgeApiError:
            # Cluster may still be initialising; keep polling.
            time.sleep(poll_interval)
            continue
        overall = last.get("overall", "unknown")
        # Any non-unknown, non-critical value is "ready" for our purposes.
        # We let step_cloud_wait_bnk_ready decide whether 'critical' is acceptable.
        if overall not in ("unknown",):
            return last
        time.sleep(poll_interval)
    raise TimeoutError(
        f"BNK health for cluster_id={cluster_id} remained 'unknown' after {timeout}s",
    )


def _wait_for_license_active(
    client: BnkForgeClient,
    cluster_id: int,
    *,
    jwt: str | None,
    timeout: int,
    poll_interval: int,
) -> dict[str, Any]:
    """Poll /api/licensing/{cluster_id}/status until license_state indicates active.

    If not yet active and a JWT is configured, tries one activation call then
    keeps polling.  Returns the last status snapshot."""
    _ACTIVE_STATES = {"active", "licensed", "verification complete"}
    activated = False
    deadline = time.time() + timeout
    last: dict[str, Any] = {}
    while time.time() < deadline:
        try:
            last = client.get_license_status(cluster_id)
        except BnkForgeApiError:
            time.sleep(poll_interval)
            continue

        license_state = (last.get("license_state") or "").lower()
        if license_state in _ACTIVE_STATES:
            return last

        # If we have a JWT and haven't tried activation yet, try once.
        # We set `activated = True` regardless of success so we don't
        # spam CWC — one attempt per polling session.
        if jwt and not activated:
            activated = True
            try:
                client.activate_license(cluster_id, jwt)
                logger.info("cloud_steps: sent activate_license for cluster %s", cluster_id)
            except BnkForgeApiError as exc:
                logger.warning(
                    "activate_license failed (cluster %s): %s — will keep polling",
                    cluster_id, exc.message,
                )
        time.sleep(poll_interval)

    raise TimeoutError(
        f"license for cluster_id={cluster_id} did not reach an active state in {timeout}s "
        f"(last license_state={last.get('license_state')!r})",
    )


# ---------------------------------------------------------------------------
# Cloud phase step registry
# ---------------------------------------------------------------------------


CLOUD_PHASE_STEPS: list[tuple[str, Any]] = [
    ("cloud_resolve_release", step_cloud_resolve_release),
    ("cloud_create_project", step_cloud_create_project),
    ("cloud_deploy_all", step_cloud_deploy_all),
    ("cloud_wait_deploy_terminal", step_cloud_wait_deploy_terminal),
    ("cloud_wait_bnk_ready", step_cloud_wait_bnk_ready),
    ("cloud_wait_license_active", step_cloud_wait_license_active),
    ("cloud_traffic", step_cloud_traffic),
    ("cloud_destroy_all", step_cloud_destroy_all),
    ("cloud_delete_project", step_cloud_delete_project),
]
