"""Integration tests for WO-4 audit fixes at the route level.

Covers:
- FEAT-0277/0278 / ERR-0013/0014 — module categories/providers sorted by count desc.
- FEAT-0280 / ERR-0015 — list_module_versions finds non-official modules (no 404).
- FEAT-0326 / ERR-0026 — create_project binds the provider-matched default template.
- FEAT-0015 / ERR-0002 — create_user honours must_change_password.
- FEAT-0014 / ERR-0029 — audit search covers the details column.
"""

from datetime import UTC, datetime
from unittest.mock import patch

from models import AuditLog, CloudCredentialTemplate, Project, User


class TestModuleLibrarySorting:
    def test_categories_sorted_by_count_desc(
        self, client, admin_headers, sample_user, make_module_library, mock_cache
    ):
        make_module_library(category="alpha")
        make_module_library(category="beta")
        make_module_library(category="beta")
        make_module_library(category="beta")

        resp = client.get("/api/module-library/categories", headers=admin_headers)
        assert resp.status_code == 200
        cats = resp.json()["categories"]
        counts = [c["count"] for c in cats]
        assert counts == sorted(counts, reverse=True)
        assert cats[0]["name"] == "beta"

    def test_providers_sorted_by_count_desc(
        self, client, admin_headers, sample_user, make_module_library, mock_cache
    ):
        make_module_library(provider="aws")
        make_module_library(provider="aws")
        make_module_library(provider="gcp")

        resp = client.get("/api/module-library/providers", headers=admin_headers)
        assert resp.status_code == 200
        provs = resp.json()["providers"]
        counts = [p["count"] for p in provs]
        assert counts == sorted(counts, reverse=True)
        assert provs[0]["name"] == "aws"


class TestListModuleVersionsFilter:
    def test_non_official_module_versions_resolve(
        self, client, admin_headers, sample_user, make_module_library
    ):
        module = make_module_library(
            is_official=False,
            git_source="git::https://github.com/acme/mod.git//path?ref=main",
        )

        with patch(
            "routes.module_library.UserModuleService.list_module_versions",
            return_value={"success": True, "branches": ["main"], "tags": ["v1.0.0"]},
        ):
            resp = client.get(
                f"/api/module-library/user-modules/{module.id}/versions",
                headers=admin_headers,
            )

        # Before the fix this 404'd because `not Column` was always False.
        assert resp.status_code == 200
        assert resp.json()["branches"] == ["main"]


class TestCreateProjectDefaultTemplate:
    def test_create_project_binds_default_template(
        self, client, admin_headers, sample_user, db
    ):
        template = CloudCredentialTemplate(
            name="aws-default", provider="aws", is_default=True
        )
        db.add(template)
        db.commit()

        resp = client.post(
            "/api/projects",
            headers=admin_headers,
            json={"name": "wo4-default-tmpl-proj", "project_type": "cloud-aws"},
        )
        assert resp.status_code in (200, 201)
        project_id = resp.json()["project_id"]

        project = db.query(Project).filter(Project.id == project_id).first()
        assert project.credential_template_id == template.id

    def test_create_project_no_default_for_other_provider(
        self, client, admin_headers, sample_user, db
    ):
        # A GCP default must NOT be bound to an AWS project.
        db.add(CloudCredentialTemplate(name="gcp-default", provider="gcp", is_default=True))
        db.commit()

        resp = client.post(
            "/api/projects",
            headers=admin_headers,
            json={"name": "wo4-no-cross-provider", "project_type": "cloud-aws"},
        )
        assert resp.status_code in (200, 201)
        project = (
            db.query(Project)
            .filter(Project.id == resp.json()["project_id"])
            .first()
        )
        assert project.credential_template_id is None


class TestCreateUserMustChangePassword:
    def test_must_change_password_is_wired(self, client, admin_headers, sample_user, db):
        resp = client.post(
            "/api/auth/users",
            headers=admin_headers,
            json={
                "username": "wo4newuser",
                "email": "wo4newuser@example.com",
                "password": "supersecret123",
                "role": "operator",
                "must_change_password": True,
            },
        )
        assert resp.status_code == 200
        user = db.query(User).filter(User.username == "wo4newuser").first()
        assert user is not None
        assert user.must_change_password is True


class TestAuditSearchDetails:
    def test_search_matches_details_column(self, client, admin_headers, sample_user, db):
        db.add(
            AuditLog(
                timestamp=datetime.now(UTC),
                user="testadmin",
                action="deploy",
                resource_type="project",
                details={"note": "needle-in-details"},
            )
        )
        db.commit()

        resp = client.get("/api/audit?search=needle-in-details", headers=admin_headers)
        assert resp.status_code == 200
        logs = resp.json()["logs"]
        assert any("needle-in-details" in str(log.get("details")) for log in logs)


class TestQKViewLegacyDelete409:
    def test_in_progress_delete_returns_409_not_500(
        self, client, admin_headers, sample_user
    ):
        from services.qkview_service import QKViewError

        async def _no_operator(*args, **kwargs):
            return None  # force the legacy fallback path

        def _raise_in_progress(*args, **kwargs):
            raise QKViewError("QKView is still in progress — cancel it first", 409)

        with (
            patch("routes.qkview._try_operator_dispatch", side_effect=_no_operator),
            patch("routes.qkview.delete_qkview", side_effect=_raise_in_progress),
        ):
            resp = client.delete(
                "/api/qkview/qk-123?cluster_id=1", headers=admin_headers
            )

        # Before the fix this fell through to general_exception_handler → 500.
        assert resp.status_code == 409
