"""
Integration tests for audit routes — /api/audit.

Covers: list logs, stats, filters, RBAC (admin vs non-admin), pagination.
Uses FastAPI TestClient with real SQLite DB.
"""

from datetime import UTC, datetime, timezone

import pytest

from models import AuditLog


class TestGetAuditLogs:
    """GET /api/audit."""

    def test_get_audit_logs_admin_sees_all(self, client, admin_headers, all_test_users, db):
        """Admin sees audit logs from all users."""
        log1 = AuditLog(
            user="testadmin",
            user_id=all_test_users["admin"].id,
            action="create",
            resource_type="project",
            resource_id="1",
            resource_name="Project Alpha",
            status="success",
            timestamp=datetime.now(UTC),
        )
        log2 = AuditLog(
            user="testviewer",
            user_id=all_test_users["viewer"].id,
            action="read",
            resource_type="module",
            resource_id="2",
            resource_name="Module Beta",
            status="success",
            timestamp=datetime.now(UTC),
        )
        db.add_all([log1, log2])
        db.commit()

        response = client.get("/api/audit", headers=admin_headers)
        assert response.status_code == 200

        data = response.json()
        assert data["total"] >= 2
        users_in_logs = {log["user"] for log in data["logs"]}
        assert "testadmin" in users_in_logs
        assert "testviewer" in users_in_logs

    def test_get_audit_logs_viewer_sees_own_only(
        self, client, viewer_headers, all_test_users, db
    ):
        """Non-admin user sees only their own audit logs."""
        log_admin = AuditLog(
            user="testadmin",
            action="delete",
            resource_type="project",
            status="success",
            timestamp=datetime.now(UTC),
        )
        log_viewer = AuditLog(
            user="testviewer",
            action="read",
            resource_type="project",
            status="success",
            timestamp=datetime.now(UTC),
        )
        db.add_all([log_admin, log_viewer])
        db.commit()

        response = client.get("/api/audit", headers=viewer_headers)
        assert response.status_code == 200

        data = response.json()
        # Viewer should only see their own logs
        for log in data["logs"]:
            assert log["user"] == "testviewer"

    def test_get_audit_logs_pagination(self, client, admin_headers, sample_user, db):
        """Pagination returns correct page_size and total count."""
        for i in range(5):
            db.add(AuditLog(
                user="testadmin",
                action="read",
                resource_type="project",
                resource_id=str(i),
                status="success",
                timestamp=datetime.now(UTC),
            ))
        db.commit()

        response = client.get(
            "/api/audit?page=1&page_size=2", headers=admin_headers
        )
        assert response.status_code == 200

        data = response.json()
        assert len(data["logs"]) == 2
        assert data["total"] >= 5
        assert data["page"] == 1
        assert data["page_size"] == 2

    def test_get_audit_logs_filters(self, client, admin_headers, sample_user, db):
        """Filter by action and resource_type narrows results."""
        db.add(AuditLog(
            user="testadmin",
            action="create",
            resource_type="project",
            status="success",
            timestamp=datetime.now(UTC),
        ))
        db.add(AuditLog(
            user="testadmin",
            action="delete",
            resource_type="module",
            status="success",
            timestamp=datetime.now(UTC),
        ))
        db.commit()

        # Filter by action
        response = client.get(
            "/api/audit?action=create", headers=admin_headers
        )
        assert response.status_code == 200
        data = response.json()
        for log in data["logs"]:
            assert log["action"] == "create"

        # Filter by resource_type
        response = client.get(
            "/api/audit?resource_type=module", headers=admin_headers
        )
        assert response.status_code == 200
        data = response.json()
        for log in data["logs"]:
            assert log["resource_type"] == "module"


class TestGetAuditStats:
    """GET /api/audit/stats."""

    def test_get_audit_stats(self, client, admin_headers, sample_user, db):
        """Stats endpoint returns period_days, total_events, and actions dict."""
        db.add(AuditLog(
            user="testadmin",
            action="create",
            resource_type="project",
            status="success",
            timestamp=datetime.now(UTC),
        ))
        db.add(AuditLog(
            user="testadmin",
            action="create",
            resource_type="module",
            status="success",
            timestamp=datetime.now(UTC),
        ))
        db.add(AuditLog(
            user="testadmin",
            action="delete",
            resource_type="project",
            status="success",
            timestamp=datetime.now(UTC),
        ))
        db.commit()

        response = client.get("/api/audit/stats", headers=admin_headers)
        assert response.status_code == 200

        data = response.json()
        assert "period_days" in data
        assert data["period_days"] == 7  # default
        assert "total_events" in data
        assert data["total_events"] >= 3
        assert "actions" in data
        assert isinstance(data["actions"], dict)
        assert "create" in data["actions"]


class TestGetAuditFilters:
    """GET /api/audit/filters."""

    def test_get_audit_filters(self, client, admin_headers, sample_user, db):
        """Filters endpoint returns distinct actions, resource_types, users, statuses."""
        db.add(AuditLog(
            user="testadmin",
            action="deploy",
            resource_type="stack",
            status="success",
            timestamp=datetime.now(UTC),
        ))
        db.add(AuditLog(
            user="testadmin",
            action="create",
            resource_type="project",
            status="failed",
            timestamp=datetime.now(UTC),
        ))
        db.commit()

        response = client.get("/api/audit/filters", headers=admin_headers)
        assert response.status_code == 200

        data = response.json()
        assert "actions" in data
        assert isinstance(data["actions"], list)
        assert "deploy" in data["actions"]
        assert "create" in data["actions"]

        assert "resource_types" in data
        assert isinstance(data["resource_types"], list)

        assert "users" in data
        assert isinstance(data["users"], list)

        assert "statuses" in data
        assert isinstance(data["statuses"], list)
        assert "success" in data["statuses"]
        assert "failed" in data["statuses"]


class TestAuditAuthentication:
    """Authentication enforcement for audit endpoints."""

    def test_get_audit_unauthenticated(self, client):
        """Audit endpoints require authentication — returns 401."""
        response = client.get("/api/audit")
        assert response.status_code == 401

        response = client.get("/api/audit/stats")
        assert response.status_code == 401

        response = client.get("/api/audit/filters")
        assert response.status_code == 401
