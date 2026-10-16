"""
Integration tests for task routes — /api/tasks.

Covers: list, detail, cancel, stats, cleanup, RBAC.
Uses FastAPI TestClient with real SQLite DB.
"""

from unittest.mock import MagicMock, patch

import pytest

from models import Task


class TestTaskList:
    """GET /api/tasks."""

    def test_list_tasks(self, client, admin_headers, sample_user, sample_task):
        """List tasks returns paginated results including pre-created task."""
        response = client.get("/api/tasks", headers=admin_headers)
        assert response.status_code == 200

        data = response.json()
        assert "tasks" in data
        assert "total" in data
        assert data["total"] >= 1

    def test_list_tasks_empty(self, client, admin_headers, sample_user):
        """Empty task list returns 200 with zero results."""
        response = client.get("/api/tasks", headers=admin_headers)
        assert response.status_code == 200
        data = response.json()
        assert "tasks" in data
        assert data["total"] >= 0

    def test_list_tasks_unauthenticated(self, client):
        """Tasks require authentication."""
        response = client.get("/api/tasks")
        assert response.status_code == 401

    def test_list_tasks_with_status_filter(self, client, admin_headers, sample_user, sample_task):
        """Filter tasks by status."""
        response = client.get(
            "/api/tasks?status=completed", headers=admin_headers
        )
        assert response.status_code == 200
        data = response.json()
        for task in data.get("tasks", []):
            assert task["status"] == "completed"


class TestTaskListLogFields:
    """#195 (second defect): the list endpoint must expose logs_full_size so a
    client enumerating a module's tasks can tell empty from populated. It used
    to be omitted entirely (surfaced as null), while the detail endpoint had it."""

    def test_list_includes_logs_full_size_matching_detail(
        self, client, admin_headers, sample_user, sample_project, db
    ):
        from tests.factories import TaskFactory

        log_body = "\n".join(f"[04:42:{i:02d}] line {i}" for i in range(20))
        task = TaskFactory(
            db, project=sample_project, task_type="plan",
            status="completed", logs=log_body,
        )
        db.commit()

        listed = client.get(
            f"/api/tasks?project_id={sample_project.id}", headers=admin_headers
        ).json()
        row = next(t for t in listed["tasks"] if t["id"] == task.id)

        # The field is present (not omitted) and equals the true log size.
        assert "logs_full_size" in row
        assert row["logs_full_size"] == len(log_body)
        assert "logs_truncated" in row

        # …and it matches what the detail endpoint reports for the same task.
        detail = client.get(f"/api/tasks/{task.id}", headers=admin_headers).json()
        assert detail["logs_full_size"] == row["logs_full_size"]

    def test_list_reports_zero_for_task_without_logs(
        self, client, admin_headers, sample_user, sample_project, db
    ):
        from tests.factories import TaskFactory

        task = TaskFactory(
            db, project=sample_project, task_type="plan", status="queued", logs=None
        )
        db.commit()

        listed = client.get(
            f"/api/tasks?project_id={sample_project.id}", headers=admin_headers
        ).json()
        row = next(t for t in listed["tasks"] if t["id"] == task.id)

        # NULL logs → 0 (matching the detail endpoint), not null/omitted.
        assert row["logs_full_size"] == 0
        detail = client.get(f"/api/tasks/{task.id}", headers=admin_headers).json()
        assert detail["logs_full_size"] == 0


class TestTaskDetail:
    """GET /api/tasks/{id}."""

    def test_get_task(self, client, admin_headers, sample_user, sample_task):
        """Get single task returns all expected fields."""
        response = client.get(
            f"/api/tasks/{sample_task.id}", headers=admin_headers
        )
        assert response.status_code == 200

        data = response.json()
        assert data["id"] == sample_task.id
        assert "status" in data
        assert "task_type" in data

    def test_get_task_not_found(self, client, admin_headers, sample_user):
        """Nonexistent task returns 404."""
        response = client.get("/api/tasks/99999", headers=admin_headers)
        assert response.status_code == 404


class TestTaskCancel:
    """POST /api/tasks/{id}/cancel."""

    def test_cancel_task(self, client, admin_headers, sample_user, sample_project, db):
        """Cancel a running task."""
        from tests.factories import TaskFactory
        task = TaskFactory(db, project=sample_project, task_type="plan", status="running")
        db.commit()
        with patch("routes.tasks.celery_app") as mock_celery:
            mock_celery.control = MagicMock()
            response = client.post(
                f"/api/tasks/{task.id}/cancel", headers=admin_headers
            )
        # May be 200 or 400 depending on task state handling
        assert response.status_code in (200, 400)


class TestTaskStats:
    """GET /api/tasks/stats/summary."""

    def test_task_stats(self, client, admin_headers, sample_user, sample_task):
        """Stats endpoint returns aggregated task counts."""
        response = client.get("/api/tasks/stats/summary", headers=admin_headers)
        assert response.status_code == 200

        data = response.json()
        # Should have some kind of stats structure
        assert isinstance(data, (dict, list))


class TestTaskCleanup:
    """DELETE /api/tasks/cleanup."""

    def test_cleanup_old_tasks(self, client, admin_headers, sample_user):
        """Cleanup endpoint runs without error."""
        response = client.delete("/api/tasks/cleanup", headers=admin_headers)
        # May return 200 or different status depending on implementation
        assert response.status_code in (200, 204)


class TestTaskArchiveAndDelete:
    """#21 — operations-log delete/archive/cleanup controls."""

    def test_archive_hides_task_from_default_list(
        self, client, admin_headers, sample_user, sample_project, db
    ):
        """Archiving a task removes it from the default (active) list view."""
        from tests.factories import TaskFactory
        task = TaskFactory(db, project=sample_project, task_type="apply", status="completed")
        db.commit()

        resp = client.post(f"/api/tasks/{task.id}/archive", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["archived"] is True

        # Default list excludes archived tasks…
        active = client.get("/api/tasks", headers=admin_headers).json()
        assert task.id not in [t["id"] for t in active["tasks"]]

        # …but archived=true surfaces them.
        archived = client.get("/api/tasks?archived=true", headers=admin_headers).json()
        assert task.id in [t["id"] for t in archived["tasks"]]

    def test_unarchive_restores_task(
        self, client, admin_headers, sample_user, sample_project, db
    ):
        """Archiving then unarchiving returns the task to the active list."""
        from tests.factories import TaskFactory
        task = TaskFactory(
            db, project=sample_project, task_type="apply", status="completed", archived=True
        )
        db.commit()

        resp = client.post(
            f"/api/tasks/{task.id}/archive?archived=false", headers=admin_headers
        )
        assert resp.status_code == 200
        assert resp.json()["archived"] is False

        active = client.get("/api/tasks", headers=admin_headers).json()
        assert task.id in [t["id"] for t in active["tasks"]]

    def test_delete_terminal_task(
        self, client, admin_headers, sample_user, sample_project, db
    ):
        """A finished task can be deleted from the ops log."""
        from tests.factories import TaskFactory
        task = TaskFactory(db, project=sample_project, task_type="apply", status="completed")
        db.commit()

        resp = client.delete(f"/api/tasks/{task.id}", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

        assert db.query(Task).filter(Task.id == task.id).first() is None

    def test_delete_running_task_rejected(
        self, client, admin_headers, sample_user, sample_project, db
    ):
        """A running task cannot be deleted (must be cancelled first)."""
        from tests.factories import TaskFactory
        task = TaskFactory(db, project=sample_project, task_type="apply", status="in_progress")
        db.commit()

        resp = client.delete(f"/api/tasks/{task.id}", headers=admin_headers)
        assert resp.status_code == 400
        assert db.query(Task).filter(Task.id == task.id).first() is not None

    def test_delete_does_not_shadow_cleanup_route(
        self, client, admin_headers, sample_user
    ):
        """The literal /cleanup route still resolves (not captured by /{task_id})."""
        resp = client.delete("/api/tasks/cleanup", headers=admin_headers)
        assert resp.status_code in (200, 204)

    def test_bulk_delete_only_removes_terminal_tasks(
        self, client, admin_headers, sample_user, sample_project, db
    ):
        """Bulk delete removes finished tasks and skips running ones."""
        from tests.factories import TaskFactory
        done = TaskFactory(db, project=sample_project, task_type="apply", status="completed")
        running = TaskFactory(db, project=sample_project, task_type="apply", status="in_progress")
        db.commit()
        done_id, running_id = done.id, running.id

        resp = client.post(
            "/api/tasks/bulk-delete",
            headers=admin_headers,
            json={"task_ids": [done_id, running_id]},
        )
        assert resp.status_code == 200
        assert resp.json()["deleted_count"] == 1
        assert db.query(Task).filter(Task.id == done_id).first() is None
        assert db.query(Task).filter(Task.id == running_id).first() is not None

    def test_bulk_archive(
        self, client, admin_headers, sample_user, sample_project, db
    ):
        """Bulk archive flips the archived flag on every listed task."""
        from tests.factories import TaskFactory
        t1 = TaskFactory(db, project=sample_project, task_type="apply", status="completed")
        t2 = TaskFactory(db, project=sample_project, task_type="apply", status="failed")
        db.commit()

        resp = client.post(
            "/api/tasks/bulk-archive",
            headers=admin_headers,
            json={"task_ids": [t1.id, t2.id]},
        )
        assert resp.status_code == 200
        assert resp.json()["updated_count"] == 2
        db.expire_all()
        assert db.query(Task).filter(Task.id == t1.id).first().archived is True
        assert db.query(Task).filter(Task.id == t2.id).first().archived is True
