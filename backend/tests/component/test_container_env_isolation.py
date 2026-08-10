"""#443: an artifact container must receive ONLY cloud credentials.

The worker's process env is fine for a subprocess (opentofu et al. need
PATH/HOME) but becomes `-e` flags on a third-party image here — which
disclosed DATABASE_URL/CELERY_*/REDIS_URL, and let ambient DOCKER_HOST/HOME
override what the artifact manifest declared in state.home_env.
"""

import os
from unittest.mock import patch

from services.credentials_service import (
    CLOUD_CREDENTIAL_ENV_KEYS,
    get_cloud_credentials_only,
)
from services.execution.container_runner import DockerRunner, ResourceLimits, StepSpec

_LEAKY_WORKER_ENV = {
    "DATABASE_URL": "postgresql://bnkforge:hunter2@postgres:5432/bnkforge",
    "CELERY_BROKER_URL": "redis://:redispass@redis:6379/0",
    "CELERY_RESULT_BACKEND": "redis://:redispass@redis:6379/0",
    "REDIS_URL": "redis://:redispass@redis:6379/0",
    "DOCKER_HOST": "tcp://127.0.0.1:2375",
    "HOME": "/home/bnkforge",
    "PATH": "/usr/local/bin:/usr/bin",
}


def test_credentials_only_excludes_worker_environment(db, make_project):
    """The credential dict handed to a container carries no ambient env."""
    project = make_project()
    db.commit()

    with patch.dict(os.environ, _LEAKY_WORKER_ENV, clear=False):
        creds = get_cloud_credentials_only(project, db)

    for leaked in ("DATABASE_URL", "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND",
                   "REDIS_URL", "DOCKER_HOST", "HOME", "PATH"):
        assert leaked not in creds, f"{leaked} must never reach an artifact container"
    assert set(creds).issubset(CLOUD_CREDENTIAL_ENV_KEYS)


def test_credentials_only_keeps_env_bootstrapped_cloud_credentials(db, make_project):
    """The documented .env bootstrap path still works: allowlisted credential
    vars present in the worker env are passed through, so containers behave
    like subprocesses for credentials — just not for everything else."""
    project = make_project()
    db.commit()

    with patch.dict(os.environ, {**_LEAKY_WORKER_ENV, "AWS_ACCESS_KEY_ID": "AKIAEXAMPLE"}, clear=False):
        creds = get_cloud_credentials_only(project, db)

    assert creds.get("AWS_ACCESS_KEY_ID") == "AKIAEXAMPLE"
    assert "DATABASE_URL" not in creds


def test_run_argv_carries_no_worker_secrets_and_keeps_manifest_home_env():
    """End of the chain: the -e flags on the actual `docker run`.

    Regression for the functional half of #443 — step env is applied AFTER
    home_env, so a leaked worker DOCKER_HOST silently overrode the manifest's
    declared proxy address and the artifact could not reach the daemon.
    """
    with patch.dict(os.environ, _LEAKY_WORKER_ENV, clear=False):
        runner = DockerRunner()
        spec = StepSpec(
            image_digest="ghcr.io/org/runner@sha256:" + "a" * 64,
            args=["tool", "up"],
            workspace_host_path="/tmp/ws",
            mount_path="/state",
            workspace_volume="vol",
            workspace_subpath="1/2",
            env={"AWS_ACCESS_KEY_ID": "AKIAEXAMPLE"},  # what the fixed task passes
            home_env={"DOCKER_HOST": "tcp://docker-socket-proxy:2375"},
            limits=ResourceLimits(cpus="2", memory="2g"),
            timeout_seconds=60,
            pull_authfile_json=None,
            component_key="k",
            step_name="up",
        )
        argv = runner.build_run_argv(spec, None)

    env_flags = [argv[i + 1] for i, token in enumerate(argv) if token == "-e"]
    assert "DOCKER_HOST=tcp://docker-socket-proxy:2375" in env_flags
    assert not [f for f in env_flags if f.startswith("DOCKER_HOST=tcp://127.0.0.1")]
    joined = " ".join(env_flags)
    for secret in ("DATABASE_URL", "CELERY_BROKER_URL", "REDIS_URL", "hunter2", "redispass"):
        assert secret not in joined


def test_container_task_uses_the_credentials_only_accessor():
    """Wiring guard, in the spirit of test_celery_task_registration's AST check.

    The two accessors differ by one word and both "work"; only one is safe here.
    A future edit that reaches for get_cloud_credentials_env (as every other
    engine legitimately does) would silently re-open #443, and no behavioural
    test above would catch it — they test the accessor, not the caller.
    """
    import ast
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "tasks" / "container_tasks.py").read_text()
    tree = ast.parse(source)

    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "services.credentials_service"
        for alias in node.names
    }
    assert "get_cloud_credentials_only" in imported
    assert "get_cloud_credentials_env" not in imported, (
        "container steps must not receive the worker's ambient environment (#443)"
    )
