"""
Tests for services.audit_service — audit log creation and request-based logging.
BC-010: Audit service — real DB, verify log creation, error handling, request extraction.
"""

from unittest.mock import MagicMock, patch

import pytest

from models import AuditLog
from services.audit_service import create_audit_log, log_from_request

# ---------------------------------------------------------------------------
# create_audit_log — basic creation
# ---------------------------------------------------------------------------

class TestCreateAuditLog:
    """Direct audit log creation via create_audit_log."""

    def test_creates_minimal_audit_log(self, db):
        result = create_audit_log(db, action="create", resource_type="project")
        assert result is not None
        assert result.action == "create"
        assert result.resource_type == "project"
        assert result.user == "system"
        assert result.status == "success"

    def test_creates_audit_log_with_all_fields(self, db, make_user):
        user = make_user(username="admin", role="admin")
        result = create_audit_log(
            db,
            action="deploy",
            resource_type="module",
            user="admin",
            user_id=user.id,
            resource_id="123",
            resource_name="vpc-module",
            status="failed",
            details={"reason": "timeout"},
            ip_address="10.0.0.1",
            user_agent="Mozilla/5.0",
            http_method="POST",
            http_path="/api/modules/123/deploy",
            http_status_code=500,
            duration_ms=1234,
        )
        assert result is not None
        assert result.user == "admin"
        assert result.user_id == user.id
        assert result.resource_id == "123"
        assert result.resource_name == "vpc-module"
        assert result.status == "failed"
        assert result.details == {"reason": "timeout"}
        assert result.ip_address == "10.0.0.1"
        assert result.user_agent == "Mozilla/5.0"
        assert result.http_method == "POST"
        assert result.http_path == "/api/modules/123/deploy"
        assert result.http_status_code == 500
        assert result.duration_ms == 1234

    def test_resource_id_cast_to_string(self, db):
        result = create_audit_log(
            db, action="update", resource_type="project", resource_id=42
        )
        assert result is not None
        assert result.resource_id == "42"

    def test_resource_id_none_stays_none(self, db):
        result = create_audit_log(
            db, action="list", resource_type="project", resource_id=None
        )
        assert result is not None
        assert result.resource_id is None

    def test_defaults_user_to_system(self, db):
        result = create_audit_log(db, action="sync", resource_type="module")
        assert result is not None
        assert result.user == "system"

    def test_defaults_details_to_empty_dict(self, db):
        result = create_audit_log(db, action="create", resource_type="project")
        assert result is not None
        assert result.details == {}

    def test_persisted_to_database(self, db):
        result = create_audit_log(
            db, action="delete", resource_type="stack", resource_id="7"
        )
        assert result is not None
        fetched = db.query(AuditLog).filter(AuditLog.id == result.id).first()
        assert fetched is not None
        assert fetched.action == "delete"
        assert fetched.resource_type == "stack"


# ---------------------------------------------------------------------------
# create_audit_log — error handling
# ---------------------------------------------------------------------------

class TestCreateAuditLogErrorHandling:
    """Verify graceful error handling when DB operations fail."""

    def test_returns_none_on_commit_failure(self, db):
        with patch.object(db, "commit", side_effect=Exception("DB down")):
            result = create_audit_log(db, action="create", resource_type="project")
        assert result is None

    def test_handles_rollback_failure_gracefully(self, db):
        with patch.object(db, "commit", side_effect=Exception("DB down")), \
             patch.object(db, "rollback", side_effect=Exception("Rollback failed")):
            result = create_audit_log(db, action="create", resource_type="project")
        assert result is None


# ---------------------------------------------------------------------------
# log_from_request — request extraction
# ---------------------------------------------------------------------------

class TestLogFromRequest:
    """Audit log creation from a FastAPI Request object."""

    def _make_request(self, *, method="GET", path="/api/projects",
                      client_host="192.168.1.1", user_agent="TestAgent/1.0",
                      forwarded_for=None):
        """Build a mock FastAPI Request."""
        request = MagicMock()
        request.method = method
        request.url.path = path
        request.client.host = client_host
        headers = {"User-Agent": user_agent}
        if forwarded_for:
            headers["X-Forwarded-For"] = forwarded_for
        request.headers.get = lambda key, default="": headers.get(key, default)
        return request

    def test_extracts_request_fields(self, db):
        request = self._make_request(
            method="POST", path="/api/projects", client_host="10.0.0.5"
        )
        result = log_from_request(
            db, action="create", resource_type="project", request=request
        )
        assert result is not None
        assert result.http_method == "POST"
        assert result.http_path == "/api/projects"
        assert result.ip_address == "10.0.0.5"
        assert result.user_agent == "TestAgent/1.0"

    def test_uses_x_forwarded_for_when_present(self, db):
        request = self._make_request(
            forwarded_for="203.0.113.50, 70.41.3.18", client_host="127.0.0.1"
        )
        result = log_from_request(
            db, action="login", resource_type="session", request=request
        )
        assert result is not None
        assert result.ip_address == "203.0.113.50"

    def test_extracts_user_from_user_obj(self, db, make_user):
        user = make_user(username="auditor", role="admin")
        request = self._make_request()
        result = log_from_request(
            db, action="update", resource_type="project",
            request=request, user_obj=user
        )
        assert result is not None
        assert result.user == "auditor"
        assert result.user_id == user.id

    def test_none_request_leaves_http_fields_empty(self, db):
        result = log_from_request(
            db, action="sync", resource_type="module", request=None
        )
        assert result is not None
        assert result.ip_address is None
        assert result.user_agent is None
        assert result.http_method is None
        assert result.http_path is None

    def test_passes_through_optional_kwargs(self, db):
        request = self._make_request()
        result = log_from_request(
            db, action="deploy", resource_type="module",
            request=request,
            resource_id="99",
            resource_name="eks-cluster",
            status="failed",
            details={"error": "timeout"},
            http_status_code=504,
            duration_ms=30000,
        )
        assert result is not None
        assert result.resource_id == "99"
        assert result.resource_name == "eks-cluster"
        assert result.status == "failed"
        assert result.http_status_code == 504
        assert result.duration_ms == 30000
