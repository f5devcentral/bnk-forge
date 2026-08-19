"""Reap step containers whose worker died.

Detached execution dropped ``--rm``: the exit code has to be read back after the
container stops, so removal moved from the daemon into a ``finally`` block in
``DockerRunner.run_step``. A worker killed by SIGKILL/OOM never reaches that
block, and the container keeps running with nothing left to remove it.

``DockerRunner._clear_workspace_predecessors`` already closes the dangerous half:
a step removes any container holding the workspace it is about to mount that
nothing live owns, so an orphan and its own retry can never run concurrently
against the same state directory. That has to be synchronous — the retry starts
seconds after the worker returns, long before any schedule is due.

Note what that sweep deliberately does NOT remove: a container owned by a
different task that is still live. ``workspace_subpath`` is shared by every
module of a deployment-scope blueprint and those modules run concurrently, so
sweeping on the workspace label alone would kill a live sibling's step. It is
scoped to unowned containers, not to everything on the workspace.

This closes the remaining half — an orphan whose step is never retried, which
would otherwise run until someone noticed. It compares each container's
``bnkforge.task`` label against the live Celery task set that
``execution_janitor`` already computes, so "dead" means the same thing here as
it does for tasks.

Runs on the WORKER, not the backend: only the celery services set DOCKER_HOST
(docker-compose.yml), so a sweep scheduled anywhere else would silently talk to
the wrong endpoint — or none.
"""

from __future__ import annotations

import logging
import os
import subprocess

from celery_app import celery_app
from services.execution.container_runner import (
    _LABEL_STEP,
    _LABEL_TASK,
    DockerRunner,
)

logger = logging.getLogger(__name__)

# Every individual docker call is short and bounded, as in the runner.
_DOCKER_CALL_TIMEOUT = 30


def _inspect_labels(runner: DockerRunner, cid: str, env: dict[str, str]) -> dict[str, str]:
    """The container's labels, or {} if it cannot be read."""
    result = subprocess.run(
        [runner.docker_bin, "inspect", "--format", "{{json .Config.Labels}}", cid],
        env=env, capture_output=True, text=True, timeout=_DOCKER_CALL_TIMEOUT,
    )
    if result.returncode != 0:
        return {}
    import json

    try:
        return json.loads((result.stdout or "").strip()) or {}
    except (ValueError, TypeError):
        return {}


def reap_orphaned_step_containers() -> dict:
    """Remove step containers whose Celery task is no longer live.

    A container with no ``bnkforge.task`` label is left alone: it predates the
    labelling, and without an owner there is no evidence it is an orphan rather
    than a step in flight. Removing on a guess here would kill a running
    deployment, which is worse than the leak.
    """
    from services.execution_janitor import get_live_task_ids

    runner = DockerRunner()
    env = dict(os.environ)
    env["DOCKER_HOST"] = runner.docker_host

    listed = subprocess.run(
        runner.build_ps_argv(label=f"{_LABEL_STEP}=1"),
        env=env, capture_output=True, text=True, timeout=_DOCKER_CALL_TIMEOUT,
    )
    if listed.returncode != 0:
        detail = (listed.stderr or listed.stdout or "").strip()
        logger.warning("Container reaper could not list step containers: %s", detail)
        return {"listed": 0, "reaped": 0, "error": detail}

    ids = [cid for cid in (listed.stdout or "").split() if cid]
    if not ids:
        return {"listed": 0, "reaped": 0}

    live = get_live_task_ids()
    if not live:
        # Same fail-open as the runner's sweep, with a wider blast radius: an
        # empty set would make every labelled container on the host look dead,
        # running ones included. This function IS a Celery task, so its own id
        # is in the set whenever the lookup works — empty means the lookup
        # failed, not that the host is idle.
        logger.warning(
            "Live-task set is empty — reaping nothing this pass rather than "
            "treating an unavailable lookup as 'nothing is running'"
        )
        return {"listed": len(ids), "reaped": 0, "skipped": "live-task set unavailable"}

    reaped, unowned = 0, 0
    for cid in ids:
        task_id = _inspect_labels(runner, cid, env).get(_LABEL_TASK)
        if not task_id:
            unowned += 1
            continue
        if task_id in live:
            continue
        logger.warning(
            "Reaping step container %s — its task %s is no longer live", cid, task_id
        )
        subprocess.run(
            runner.build_rm_argv(cid),
            env=env, capture_output=True, text=True, timeout=_DOCKER_CALL_TIMEOUT,
        )
        reaped += 1

    if reaped or unowned:
        logger.info(
            "Container reaper: %d listed, %d reaped, %d left alone (no owning task label)",
            len(ids), reaped, unowned,
        )
    return {"listed": len(ids), "reaped": reaped, "unowned": unowned}


@celery_app.task(name="tasks.container_reaper.reap_orphaned_step_containers")
def reap_orphaned_step_containers_task() -> dict:
    try:
        return reap_orphaned_step_containers()
    except Exception as exc:  # pragma: no cover - the sweep must never fail a beat tick
        logger.warning("Container reaper failed: %s", exc)
        return {"listed": 0, "reaped": 0, "error": str(exc)}
