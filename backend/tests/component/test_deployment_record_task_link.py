"""create_deployment_record must carry the task id (#154).

A Deployment row has no task_id column, and the task is the handle for a
run's output (GET /api/tasks/{id}). Until #154 that handle was not reachable
from any module-facing endpoint: /deployments returned a deployment `id` that
looked like the log handle but was not. The shared writer every engine goes
through now records it in meta_data, with no schema change.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from models import Deployment, Task
from tasks._tofu_helpers import create_deployment_record
from tests.factories import ModuleLibraryFactory, ProjectFactory, ProjectModuleFactory


@pytest.mark.component
def test_create_deployment_record_links_the_task(db):
    project = ProjectFactory(db)
    lib = ModuleLibraryFactory(db, category="container", execution_engine="container")
    module = ProjectModuleFactory(db, project=project, library_module=lib)
    task = Task(
        project_id=project.id, module_id=module.id, task_type="apply",
        status="completed", triggered_by="user", celery_task_id="cel-link-1",
        started_at=datetime.now(UTC), completed_at=datetime.now(UTC), exit_code=0,
    )
    db.add(task)
    db.commit()

    dep = create_deployment_record(db, task, module, "apply", logs="ok")

    stored = db.query(Deployment).filter_by(id=dep.id).one()
    assert stored.meta_data["task_id"] == task.id
    assert stored.meta_data["celery_task_id"] == "cel-link-1"
