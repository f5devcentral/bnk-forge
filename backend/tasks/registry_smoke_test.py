"""Smoke test imported registry/git modules with `tofu init -backend=false`.

Runs in the worker container (which has the `tofu` binary on $PATH) after a
RegistryService import dispatched the task. Outcome is recorded back on the
ModuleLibrary row — failures never raise, they're recorded as warnings so the
import flow stays unblocked.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from celery_app import celery_app
from database import get_db_context
from models import ModuleLibrary

logger = logging.getLogger(__name__)

SMOKE_TEST_TIMEOUT = 120
OUTPUT_TRUNCATE = 8000

_PROVIDER_ERROR_MARKERS = (
    "could not retrieve the list of available versions",
    "Failed to query available provider packages",
    "no available releases match",
    "registry.terraform.io",
    "Inconsistent dependency lock file",
    "Required plugins are not installed",
)


def _classify(exit_code: int, output: str) -> str:
    if exit_code == 0:
        return "passed"
    if any(marker.lower() in output.lower() for marker in _PROVIDER_ERROR_MARKERS):
        return "warned"
    return "failed"


@celery_app.task(name="tasks.registry.smoke_test_module")
def smoke_test_module(module_library_id: int) -> dict:
    """Run `tofu init -backend=false` against a copy of the imported module.

    Returns a small dict for observability. Never raises.
    """
    with get_db_context() as db:
        module = db.query(ModuleLibrary).filter(ModuleLibrary.id == module_library_id).first()
        if not module:
            logger.warning("smoke_test_module: ModuleLibrary id=%s not found", module_library_id)
            return {"status": "missing"}
        local_path = module.local_path

    if not local_path or not Path(local_path).is_dir():
        with get_db_context() as db:
            row = db.query(ModuleLibrary).filter(ModuleLibrary.id == module_library_id).first()
            if row:
                row.last_smoke_test_status = "failed"
                row.last_smoke_test_output = f"local_path missing or invalid: {local_path!r}"
                row.last_smoke_test_at = datetime.now(UTC)
                db.commit()
        return {"status": "failed", "reason": "missing local_path"}

    workdir = Path(tempfile.mkdtemp(prefix=f"smoke-{module_library_id}-"))
    try:
        # Copy the module into a clean tempdir so .terraform/ artifacts don't
        # accumulate in the catalog directory.
        for item in Path(local_path).iterdir():
            if item.name == ".terraform":
                continue
            if item.is_dir():
                shutil.copytree(item, workdir / item.name)
            else:
                shutil.copy2(item, workdir / item.name)

        try:
            proc = subprocess.run(
                ["tofu", "init", "-backend=false", "-no-color", "-input=false"],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=SMOKE_TEST_TIMEOUT,
            )
            output = (proc.stdout or "") + (proc.stderr or "")
            status = _classify(proc.returncode, output)
        except subprocess.TimeoutExpired as exc:
            output = (exc.stdout or "") + (exc.stderr or "") + f"\n[smoke test timed out after {SMOKE_TEST_TIMEOUT}s]"
            status = "failed"
        except FileNotFoundError:
            output = "tofu binary not found on PATH — smoke test cannot run in this container"
            status = "failed"
        except Exception as exc:
            logger.exception("smoke_test_module: unexpected failure for module_library_id=%s", module_library_id)
            output = f"smoke test crashed: {exc}"
            status = "failed"

        with get_db_context() as db:
            row = db.query(ModuleLibrary).filter(ModuleLibrary.id == module_library_id).first()
            if row:
                row.last_smoke_test_status = status
                row.last_smoke_test_output = output[:OUTPUT_TRUNCATE]
                row.last_smoke_test_at = datetime.now(UTC)
                db.commit()

        return {"status": status, "module_library_id": module_library_id}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
