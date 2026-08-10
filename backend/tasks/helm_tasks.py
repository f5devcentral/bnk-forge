"""Helm Celery tasks — thin wrappers around HelmService for worker-side execution.

All helm operations require the `helm` binary and a fully-resolved kubeconfig
(including exec plugins like `aws`). These are only available in the Celery worker
image, not the slim api image. Tasks run on the worker; routes enqueue and poll.

Write tasks (install/upgrade/rollback/uninstall) acquire a Postgres advisory lock
keyed on (cluster_id, release_name) before touching the cluster. Concurrent ops on
the same release block (serialize) until the first finishes; ops on different releases
are fully independent. SQLite (test env) gets a no-op fallback.
"""

import hashlib
import logging
import subprocess
from contextlib import contextmanager

import sqlalchemy as sa

from celery_app import celery_app
from database import DATABASE_URL, get_db_context
from services.helm_service import HelmService

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Advisory lock helper — Postgres transaction-level, no-op on SQLite
# ---------------------------------------------------------------------------

def _helm_lock_key(cluster_id: int, release_name: str) -> int:
    """Return a signed 64-bit advisory lock key for (cluster_id, release_name).

    Computes SHA-256("{cluster_id}:{release_name}"), takes the lower 8 bytes,
    and masks to a positive 63-bit integer so the result fits in a Postgres
    BIGINT (signed 64-bit).
    """
    raw = f"{cluster_id}:{release_name}".encode()
    digest = hashlib.sha256(raw).digest()
    key = int.from_bytes(digest[:8], "big") & 0x7FFFFFFFFFFFFFFF
    return key


@contextmanager
def helm_release_lock(db, cluster_id: int, release_name: str):
    """Block until the advisory lock for (cluster_id, release_name) is acquired.

    On Postgres: uses session-level pg_advisory_lock / pg_advisory_unlock so the
    lock is held only during the helm subprocess, not across an idle open transaction.
    pg_advisory_xact_lock would hold an idle-in-transaction connection for the full
    duration of `helm install/upgrade` (potentially 5–30 min), causing lock bloat;
    session-level locks are released explicitly when the context exits regardless of
    transaction state.  The try/finally guarantees pg_advisory_unlock is always called
    even on exception, preventing lock leaks.

    On SQLite (tests): no-op; SQLite is single-process and has no advisory locks.
    """
    if DATABASE_URL.startswith("sqlite"):
        yield
        return

    key = _helm_lock_key(cluster_id, release_name)
    logger.debug(
        "helm_release_lock: acquiring advisory lock key=%d cluster=%d release=%s",
        key,
        cluster_id,
        release_name,
    )
    db.execute(sa.text("SELECT pg_advisory_lock(:key)"), {"key": key})
    logger.debug(
        "helm_release_lock: acquired advisory lock key=%d cluster=%d release=%s",
        key,
        cluster_id,
        release_name,
    )
    try:
        yield
    finally:
        db.execute(sa.text("SELECT pg_advisory_unlock(:key)"), {"key": key})
        logger.debug(
            "helm_release_lock: released advisory lock key=%d cluster=%d release=%s",
            key,
            cluster_id,
            release_name,
        )


# ── Read tasks (fast ops, short-poll from route) ────────────────────────────


@celery_app.task(name="tasks.helm.list_releases")
def list_releases(cluster_id: int, namespace: str | None, all_namespaces: bool) -> dict:
    with get_db_context() as db:
        try:
            result = HelmService(db).list_releases(
                cluster_id=cluster_id,
                namespace=namespace,
                all_namespaces=all_namespaces,
            )
            return {"success": True, "releases": result, "count": len(result)}
        except subprocess.TimeoutExpired as e:
            return {"success": False, "error": str(e)}
        except RuntimeError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.exception("list_releases task failed for cluster %s", cluster_id)
            return {"success": False, "error": str(e)}


@celery_app.task(name="tasks.helm.get_release")
def get_release(cluster_id: int, release_name: str, namespace: str) -> dict:
    with get_db_context() as db:
        try:
            result = HelmService(db).get_release(
                cluster_id=cluster_id,
                release_name=release_name,
                namespace=namespace,
            )
            return {"success": True, "release": result}
        except subprocess.TimeoutExpired as e:
            return {"success": False, "error": str(e)}
        except RuntimeError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.exception("get_release task failed for cluster %s release %s", cluster_id, release_name)
            return {"success": False, "error": str(e)}


@celery_app.task(name="tasks.helm.release_history")
def release_history(cluster_id: int, release_name: str, namespace: str, max_revisions: int) -> dict:
    with get_db_context() as db:
        try:
            result = HelmService(db).get_history(
                cluster_id=cluster_id,
                release_name=release_name,
                namespace=namespace,
                max_revisions=max_revisions,
            )
            return {"success": True, "history": result, "count": len(result)}
        except subprocess.TimeoutExpired as e:
            return {"success": False, "error": str(e)}
        except RuntimeError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.exception("release_history task failed for cluster %s release %s", cluster_id, release_name)
            return {"success": False, "error": str(e)}


@celery_app.task(name="tasks.helm.release_values")
def release_values(cluster_id: int, release_name: str, namespace: str, all_values: bool) -> dict:
    with get_db_context() as db:
        try:
            result = HelmService(db).get_values(
                cluster_id=cluster_id,
                release_name=release_name,
                namespace=namespace,
                all_values=all_values,
            )
            return {"success": True, "values": result}
        except subprocess.TimeoutExpired as e:
            return {"success": False, "error": str(e)}
        except RuntimeError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.exception("release_values task failed for cluster %s release %s", cluster_id, release_name)
            return {"success": False, "error": str(e)}


@celery_app.task(name="tasks.helm.release_manifest")
def release_manifest(cluster_id: int, release_name: str, namespace: str, revision: int | None) -> dict:
    with get_db_context() as db:
        try:
            result = HelmService(db).get_manifest(
                cluster_id=cluster_id,
                release_name=release_name,
                namespace=namespace,
                revision=revision,
            )
            return {"success": True, "manifest": result}
        except subprocess.TimeoutExpired as e:
            return {"success": False, "error": str(e)}
        except RuntimeError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.exception("release_manifest task failed for cluster %s release %s", cluster_id, release_name)
            return {"success": False, "error": str(e)}


@celery_app.task(name="tasks.helm.release_compare")
def release_compare(
    cluster_id: int,
    release_name: str,
    revision1: int,
    revision2: int,
    namespace: str,
) -> dict:
    with get_db_context() as db:
        try:
            result = HelmService(db).compare_revisions(
                cluster_id=cluster_id,
                release_name=release_name,
                revision1=revision1,
                revision2=revision2,
                namespace=namespace,
            )
            return result
        except subprocess.TimeoutExpired as e:
            return {"success": False, "error": str(e)}
        except RuntimeError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.exception("release_compare task failed for cluster %s release %s", cluster_id, release_name)
            return {"success": False, "error": str(e)}


# ── Write tasks (long-running, immediate enqueue) ───────────────────────────


@celery_app.task(name="tasks.helm.install_release")
def install_release(
    cluster_id: int,
    release_name: str,
    chart: str,
    namespace: str,
    values: dict | None,
    version: str | None,
    create_namespace: bool,
    wait: bool,
    timeout: str,
) -> dict:
    with get_db_context() as db:
        try:
            with helm_release_lock(db, cluster_id, release_name):
                result = HelmService(db).install_chart(
                    cluster_id=cluster_id,
                    release_name=release_name,
                    chart=chart,
                    namespace=namespace,
                    values=values,
                    version=version,
                    create_namespace=create_namespace,
                    wait=wait,
                    timeout=timeout,
                )
            return {"success": True, "result": result, "message": f"Chart {chart} installed as {release_name}"}
        except subprocess.TimeoutExpired as e:
            return {"success": False, "error": str(e)}
        except (RuntimeError, ValueError) as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.exception("install_release task failed for cluster %s release %s", cluster_id, release_name)
            return {"success": False, "error": str(e)}


@celery_app.task(name="tasks.helm.upgrade_release")
def upgrade_release(
    cluster_id: int,
    release_name: str,
    chart: str | None,
    namespace: str,
    values: dict | None,
    version: str | None,
    install: bool,
    wait: bool,
    timeout: str,
) -> dict:
    with get_db_context() as db:
        try:
            with helm_release_lock(db, cluster_id, release_name):
                result = HelmService(db).upgrade_release(
                    cluster_id=cluster_id,
                    release_name=release_name,
                    chart=chart,
                    namespace=namespace,
                    values=values,
                    version=version,
                    install=install,
                    wait=wait,
                    timeout=timeout,
                )
            return {"success": True, "result": result, "message": f"Release {release_name} upgraded successfully"}
        except subprocess.TimeoutExpired as e:
            return {"success": False, "error": str(e)}
        except RuntimeError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.exception("upgrade_release task failed for cluster %s release %s", cluster_id, release_name)
            return {"success": False, "error": str(e)}


@celery_app.task(name="tasks.helm.rollback_release")
def rollback_release(
    cluster_id: int,
    release_name: str,
    revision: int | None,
    namespace: str,
    wait: bool,
    timeout: str,
) -> dict:
    with get_db_context() as db:
        try:
            with helm_release_lock(db, cluster_id, release_name):
                result = HelmService(db).rollback_release(
                    cluster_id=cluster_id,
                    release_name=release_name,
                    revision=revision,
                    namespace=namespace,
                    wait=wait,
                    timeout=timeout,
                )
            return {"success": True, "result": result, "message": f"Release {release_name} rolled back successfully"}
        except subprocess.TimeoutExpired as e:
            return {"success": False, "error": str(e)}
        except RuntimeError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.exception("rollback_release task failed for cluster %s release %s", cluster_id, release_name)
            return {"success": False, "error": str(e)}


@celery_app.task(name="tasks.helm.uninstall_release")
def uninstall_release(
    cluster_id: int,
    release_name: str,
    namespace: str,
    keep_history: bool,
    wait: bool,
    timeout: str,
) -> dict:
    with get_db_context() as db:
        try:
            with helm_release_lock(db, cluster_id, release_name):
                result = HelmService(db).uninstall_release(
                    cluster_id=cluster_id,
                    release_name=release_name,
                    namespace=namespace,
                    keep_history=keep_history,
                    wait=wait,
                    timeout=timeout,
                )
            return {"success": True, "result": result, "message": f"Release {release_name} uninstalled successfully"}
        except subprocess.TimeoutExpired as e:
            return {"success": False, "error": str(e)}
        except RuntimeError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.exception("uninstall_release task failed for cluster %s release %s", cluster_id, release_name)
            return {"success": False, "error": str(e)}


@celery_app.task(name="tasks.helm.test_release")
def test_release(cluster_id: int, release_name: str, namespace: str, timeout: str) -> dict:
    with get_db_context() as db:
        try:
            result = HelmService(db).test_release(
                cluster_id=cluster_id,
                release_name=release_name,
                namespace=namespace,
                timeout=timeout,
            )
            return result
        except subprocess.TimeoutExpired as e:
            return {"success": False, "error": str(e)}
        except RuntimeError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.exception("test_release task failed for cluster %s release %s", cluster_id, release_name)
            return {"success": False, "error": str(e)}
