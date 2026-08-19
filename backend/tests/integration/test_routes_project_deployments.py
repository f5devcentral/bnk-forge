"""
Integration tests for project deployment routes — /api/project-modules.

Covers: deployment logs, deployment history (per-module and per-project),
state info, state resources, RBAC enforcement.
Uses FastAPI TestClient with real SQLite DB.
"""

from datetime import UTC, datetime, timezone
from unittest.mock import patch

import pytest

from models import Deployment, DeploymentLog, ProjectModule


class TestGetDeploymentLogs:
    """GET /api/project-modules/{module_id}/logs."""

    def test_get_deployment_logs_empty(self, client, admin_headers, sample_module):
        """Module with no logs returns total_logs=0 and empty list."""
        mod = sample_module["module"]
        response = client.get(
            f"/api/project-modules/{mod.id}/logs", headers=admin_headers
        )
        assert response.status_code == 200

        data = response.json()
        assert data["module_id"] == mod.id
        assert data["module_name"] == "test-vpc"
        assert data["total_logs"] == 0
        assert data["logs"] == []

    def test_get_deployment_logs_with_data(
        self, client, admin_headers, sample_module, db
    ):
        """Logs are returned when DeploymentLog records exist."""
        mod = sample_module["module"]
        log = DeploymentLog(
            module_id=mod.id,
            level="info",
            message="Apply started",
            timestamp=datetime.now(UTC),
        )
        db.add(log)
        db.commit()

        response = client.get(
            f"/api/project-modules/{mod.id}/logs", headers=admin_headers
        )
        assert response.status_code == 200

        data = response.json()
        assert data["total_logs"] == 1
        assert data["logs"][0]["level"] == "info"
        assert data["logs"][0]["message"] == "Apply started"

    def test_logs_fall_back_to_task_logs_when_no_deployment_log_rows(
        self, client, admin_headers, sample_module, db
    ):
        """#154: every engine writes its step output to Task.logs, and only the
        retry path writes DeploymentLog. So a module that just applied with real
        output used to get 200 {"logs": []} from this endpoint -- indistinguishable
        from "this step produced no output". Serve the task logs, and say so."""
        from models import Task

        mod = sample_module["module"]
        task = Task(
            project_id=mod.project_id, module_id=mod.id, task_type="apply",
            status="completed", triggered_by="user", celery_task_id="cel-154",
            logs="[00:01:32] $ docker run ghcr.io/x/runner roksbnkctl cleanup --dry-run\n"
                 "[00:01:33] → Scanning for f5orph-* resources in regions: us-east\n"
                 "[00:01:42] ✓ No orphaned resources found.",
        )
        db.add(task)
        db.commit()

        data = client.get(f"/api/project-modules/{mod.id}/logs", headers=admin_headers).json()

        assert data["source"] == "task"
        assert data["task_id"] == task.id
        assert data["total_logs"] == 3
        assert "No orphaned resources found" in data["logs"][-1]["message"]

    def test_logs_prefer_deployment_log_rows_when_present(
        self, client, admin_headers, sample_module, db
    ):
        """DeploymentLog rows still win when they exist (the retry path)."""
        from models import Task

        mod = sample_module["module"]
        db.add(DeploymentLog(module_id=mod.id, level="warning",
                             message="Deployment retry (apply) queued", timestamp=datetime.now(UTC)))
        db.add(Task(project_id=mod.project_id, module_id=mod.id, task_type="apply",
                    status="completed", triggered_by="user", celery_task_id="cel-154b",
                    logs="task output that must NOT be served here"))
        db.commit()

        data = client.get(f"/api/project-modules/{mod.id}/logs", headers=admin_headers).json()

        assert data["source"] == "deployment_log"
        assert data["logs"][0]["message"] == "Deployment retry (apply) queued"

    def test_logs_with_nothing_at_all_carries_a_hint(
        self, client, admin_headers, sample_module
    ):
        """A genuinely empty module still returns 200, but is no longer silent
        about where output WOULD be."""
        mod = sample_module["module"]
        data = client.get(f"/api/project-modules/{mod.id}/logs", headers=admin_headers).json()

        assert data["total_logs"] == 0
        assert data["source"] == "none"
        assert "/api/tasks" in data["hint"]

    def test_logs_task_fallback_honours_limit(
        self, client, admin_headers, sample_module, db
    ):
        from models import Task

        mod = sample_module["module"]
        db.add(Task(project_id=mod.project_id, module_id=mod.id, task_type="apply",
                    status="completed", triggered_by="user", celery_task_id="cel-154c",
                    logs="\n".join(f"line {i}" for i in range(50))))
        db.commit()

        data = client.get(f"/api/project-modules/{mod.id}/logs?limit=5", headers=admin_headers).json()
        assert data["total_logs"] == 5
        assert data["logs"][-1]["message"] == "line 49"  # the tail, like a log should be

    def test_get_deployment_logs_module_not_found(
        self, client, admin_headers, sample_user
    ):
        """Nonexistent module returns 404."""
        response = client.get(
            "/api/project-modules/99999/logs", headers=admin_headers
        )
        assert response.status_code == 404


class TestGetDeploymentHistory:
    """GET /api/project-modules/{module_id}/deployments."""

    def test_get_deployment_history(
        self, client, admin_headers, sample_module, db
    ):
        """Deployment records are returned in history."""
        mod = sample_module["module"]
        dep = Deployment(
            module_id=mod.id,
            action="apply",
            status="success",
            triggered_by="testadmin",
            started_at=datetime.now(UTC),
            duration_seconds=45.0,
            resources_to_add=3,
        )
        db.add(dep)
        db.commit()

        response = client.get(
            f"/api/project-modules/{mod.id}/deployments",
            headers=admin_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["module_id"] == mod.id
        assert data["total_deployments"] == 1
        assert data["deployments"][0]["action"] == "apply"
        assert data["deployments"][0]["status"] == "success"
        assert data["deployments"][0]["resources_to_add"] == 3

    def test_deployment_rows_expose_task_id(
        self, client, admin_headers, sample_module, db
    ):
        """#154: `id` on a deployment row looked like the log handle but was
        not. Rows written by create_deployment_record carry the task id in
        meta_data; the route exposes it as task_id."""
        mod = sample_module["module"]
        db.add(Deployment(module_id=mod.id, action="apply", status="success",
                          triggered_by="user", started_at=datetime.now(UTC),
                          meta_data={"task_id": 4242, "celery_task_id": "cel-4242"}))
        # A pre-#154 row with no meta_data must not break the listing.
        db.add(Deployment(module_id=mod.id, action="plan", status="success",
                          triggered_by="user", started_at=datetime.now(UTC)))
        db.commit()

        rows = client.get(f"/api/project-modules/{mod.id}/deployments",
                          headers=admin_headers).json()["deployments"]
        by_action = {r["action"]: r for r in rows}
        assert by_action["apply"]["task_id"] == 4242
        assert by_action["plan"]["task_id"] is None

    def test_get_deployment_history_filter_by_action(
        self, client, admin_headers, sample_module, db
    ):
        """Filtering by action narrows results."""
        mod = sample_module["module"]
        db.add(Deployment(
            module_id=mod.id,
            action="apply",
            status="success",
            triggered_by="testadmin",
            started_at=datetime.now(UTC),
        ))
        db.add(Deployment(
            module_id=mod.id,
            action="destroy",
            status="success",
            triggered_by="testadmin",
            started_at=datetime.now(UTC),
        ))
        db.commit()

        response = client.get(
            f"/api/project-modules/{mod.id}/deployments?action=apply",
            headers=admin_headers,
        )
        assert response.status_code == 200

        data = response.json()
        for dep in data["deployments"]:
            assert dep["action"] == "apply"

    def test_get_deployment_history_module_not_found(
        self, client, admin_headers, sample_user
    ):
        """Nonexistent module returns 404."""
        response = client.get(
            "/api/project-modules/99999/deployments",
            headers=admin_headers,
        )
        assert response.status_code == 404


class TestGetProjectDeployments:
    """GET /api/project-modules/project/{project_id}/deployments."""

    def test_get_project_deployments(
        self, client, admin_headers, sample_module, db
    ):
        """Project-level deployments include module name and path."""
        mod = sample_module["module"]
        dep = Deployment(
            module_id=mod.id,
            action="apply",
            status="success",
            triggered_by="testadmin",
            started_at=datetime.now(UTC),
        )
        db.add(dep)
        db.commit()

        project = sample_module["project"]
        response = client.get(
            f"/api/project-modules/project/{project.id}/deployments",
            headers=admin_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["project_id"] == project.id
        assert data["total_deployments"] >= 1
        assert data["deployments"][0]["module_id"] == mod.id

    def test_get_project_deployments_project_not_found(
        self, client, admin_headers, sample_user
    ):
        """Nonexistent project returns 404."""
        response = client.get(
            "/api/project-modules/project/99999/deployments",
            headers=admin_headers,
        )
        assert response.status_code == 404


class TestGetStateInfo:
    """GET /api/project-modules/{module_id}/state-info."""

    def test_get_state_info_no_state_file(
        self, client, admin_headers, sample_module
    ):
        """When no state file exists, state_exists=False."""
        mod = sample_module["module"]

        with patch("os.path.exists", return_value=False):
            response = client.get(
                f"/api/project-modules/{mod.id}/state-info",
                headers=admin_headers,
            )
        assert response.status_code == 200

        data = response.json()
        assert data["module_id"] == mod.id
        assert data["state_exists"] is False

    def test_get_state_info_module_not_found(
        self, client, admin_headers, sample_user
    ):
        """Nonexistent module returns 404."""
        response = client.get(
            "/api/project-modules/99999/state-info", headers=admin_headers
        )
        assert response.status_code == 404


class TestStateResources:
    """GET /api/project-modules/{module_id}/state-resources."""

    def test_get_state_resources_no_state_file(
        self, client, admin_headers, sample_module
    ):
        """When no state file exists, returns empty resources list."""
        mod = sample_module["module"]

        with patch("os.path.exists", return_value=False):
            response = client.get(
                f"/api/project-modules/{mod.id}/state-resources",
                headers=admin_headers,
            )
        assert response.status_code == 200

        data = response.json()
        assert data["module_id"] == mod.id
        assert data["resources"] == []
        assert data["resources_count"] == 0


class TestDeploymentRBAC:
    """RBAC enforcement for deployment read endpoints."""

    def test_viewer_can_read_deployment_logs(
        self, client, viewer_headers, all_test_users, sample_project, db
    ):
        """Viewer can read deployment logs."""
        from tests.factories import ModuleLibraryFactory, ProjectModuleFactory

        lib = ModuleLibraryFactory(db, name="rbac-log-test", category="test")
        mod = ProjectModuleFactory(
            db, project=sample_project, library_module=lib
        )
        db.commit()

        response = client.get(
            f"/api/project-modules/{mod.id}/logs", headers=viewer_headers
        )
        assert response.status_code == 200


class TestRepairInfrastructureAccess:
    """POST /api/project-modules/{module_id}/repair-access."""

    def test_repair_access_requires_applied_status(self, client, admin_headers, sample_module, db):
        module = sample_module["module"]
        module.status = "initialized"
        db.commit()

        response = client.post(f"/api/project-modules/{module.id}/repair-access", headers=admin_headers)
        assert response.status_code == 400

    def test_repair_access_normalizes_stale_outputs(self, client, admin_headers, sample_module, db):
        module = sample_module["module"]
        module.status = "applied"
        module.outputs = {
            "infrastructure_private_key_path": "~/.ssh/missing.pem",
            "jumphost_ssh_command": "ssh -i ~/.ssh/missing.pem ubuntu@1.2.3.4",
        }
        db.commit()

        response = client.post(f"/api/project-modules/{module.id}/repair-access", headers=admin_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["infrastructure_private_key_available"] is False
        assert data["infrastructure_private_key_path"] is None
        assert data["infrastructure_access_status"] == "recovery_required"


class TestGetDeploymentOutput:
    """GET /api/project-modules/{module_id}/deployments/{deployment_id}/output.

    Issue #526: the deployments endpoint returned status, timing and resource
    counts but no log, and every other plausible path 404'd. A failed container
    deploy could only be diagnosed by opening the UI, which makes headless/CI
    deployment effectively undebuggable.
    """

    def _deployment(self, db, module, **overrides):
        fields = dict(
            module_id=module.id,
            action="apply",
            status="failed",
            exit_code=1,
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
            duration_seconds=804.79,
            stdout="[1/6] Container-runtime preflight ...\nerror: refusing to overwrite: /state/poc already exists\n",
            stderr="",
        )
        fields.update(overrides)
        dep = Deployment(**fields)
        db.add(dep)
        db.commit()
        db.refresh(dep)
        return dep

    def test_returns_the_captured_step_output(self, client, admin_headers, sample_module, db):
        """The stdout captured at deploy time is actually reachable over the API."""
        mod = sample_module["module"]
        dep = self._deployment(db, mod)

        response = client.get(
            f"/api/project-modules/{mod.id}/deployments/{dep.id}/output",
            headers=admin_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["deployment_id"] == dep.id
        assert data["module_id"] == mod.id
        assert data["status"] == "failed"
        assert data["exit_code"] == 1
        assert "refusing to overwrite" in data["stdout"], (
            "the failure reason is missing — this endpoint exists precisely so a "
            "failed deploy can be diagnosed without the UI (issue #526)"
        )
        assert data["truncated"] is False

    def test_keeps_the_tail_when_output_exceeds_the_cap(
        self, client, admin_headers, sample_module, db
    ):
        """Truncation keeps the END — the error is at the bottom of a deploy log."""
        mod = sample_module["module"]
        dep = self._deployment(
            db, mod, stdout=("filler line\n" * 5000) + "FINAL ERROR: cluster unreachable\n"
        )

        response = client.get(
            f"/api/project-modules/{mod.id}/deployments/{dep.id}/output?max_bytes=1024",
            headers=admin_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["truncated"] is True
        assert len(data["stdout"]) <= 1024
        assert "FINAL ERROR: cluster unreachable" in data["stdout"], (
            "truncation dropped the tail, discarding the only line that explains "
            "the failure"
        )

    def test_unknown_deployment_returns_404(self, client, admin_headers, sample_module):
        mod = sample_module["module"]
        response = client.get(
            f"/api/project-modules/{mod.id}/deployments/999999/output",
            headers=admin_headers,
        )
        assert response.status_code == 404

    def test_deployment_of_another_module_is_not_readable(
        self, client, admin_headers, sample_module, sample_project, db
    ):
        """A real deployment id belonging to a different module must 404 here."""
        mod = sample_module["module"]
        other = ProjectModule(
            project_id=sample_project.id,
            module_library_id=sample_module["library"].id,
            path_in_project="infra/other",
            status="applied",
        )
        db.add(other)
        db.commit()
        db.refresh(other)

        dep = self._deployment(db, other, stdout="secrets of another module")

        response = client.get(
            f"/api/project-modules/{mod.id}/deployments/{dep.id}/output",
            headers=admin_headers,
        )
        assert response.status_code == 404, (
            "a deployment was readable through the wrong module's path"
        )

    def test_viewer_can_read_output(
        self, client, viewer_headers, all_test_users, sample_project, db
    ):
        """Diagnosis is a read operation — viewers get it."""
        from tests.factories import ModuleLibraryFactory, ProjectModuleFactory

        lib = ModuleLibraryFactory(db, name="rbac-output-test", category="test")
        mod = ProjectModuleFactory(db, project=sample_project, library_module=lib)
        db.commit()
        dep = self._deployment(db, mod)

        response = client.get(
            f"/api/project-modules/{mod.id}/deployments/{dep.id}/output",
            headers=viewer_headers,
        )
        assert response.status_code == 200
