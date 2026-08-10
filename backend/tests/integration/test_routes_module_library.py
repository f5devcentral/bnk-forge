"""
Integration tests for module library routes — /api/module-library.

Covers: list, filter, categories, providers, detail, variables, sync, not-found, RBAC.
Most read endpoints query the DB directly (no mock needed).
Sync and user-module endpoints mock external services.
Uses FastAPI TestClient with real SQLite DB.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from models import ModuleLibrary


class TestModuleList:
    """GET /api/module-library."""

    def test_module_library_new_fields_nullable_for_legacy_rows(self, db, make_module_library):
        """Legacy module rows remain valid with new nullable fields unset."""
        module = make_module_library(name="legacy-module")
        db.refresh(module)

        assert module.engine_type is None
        assert module.pack_manifest is None

    def test_module_library_new_fields_persist_when_set(self, db, make_module_library):
        """New schema fields round-trip through model persistence."""
        manifest = {
            "schema_version": "1",
            "engine": "ansible",
            "lifecycle": {"supports_destroy": False},
        }
        module = make_module_library(
            name="ansible-pack-module",
            engine_type="ansible",
            pack_manifest=manifest,
        )
        db.refresh(module)

        assert module.engine_type == "ansible"
        assert module.pack_manifest == manifest

    def test_list_modules(self, client, viewer_headers, all_test_users, make_module_library):
        """Viewer can list modules from the catalog."""
        make_module_library(name="vpc-module", category="infra", provider="aws")
        make_module_library(name="eks-module", category="kubernetes", provider="aws")

        response = client.get("/api/module-library", headers=viewer_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 2
        names = [m["name"] for m in data["modules"]]
        assert "vpc-module" in names
        assert "eks-module" in names

    def test_list_modules_filter_category(self, client, viewer_headers, all_test_users, make_module_library):
        """Filtering by category returns only matching modules."""
        make_module_library(name="net-module", category="networking", provider="aws")
        make_module_library(name="k8s-module", category="kubernetes", provider="aws")

        response = client.get("/api/module-library?category=networking", headers=viewer_headers)
        assert response.status_code == 200
        data = response.json()
        # All returned modules should have category == networking
        for m in data["modules"]:
            assert m["category"] == "networking"

    def test_list_modules_unauthenticated(self, client):
        """Unauthenticated request returns 401."""
        response = client.get("/api/module-library")
        assert response.status_code == 401

    def test_list_modules_includes_canonical_platform_compatibility(
        self,
        client,
        viewer_headers,
        all_test_users,
        make_module_library,
    ):
        """List payload includes additive compatibility normalization fields."""
        make_module_library(
            name="compat-module",
            category="infra",
            provider="aws",
            inputs_metadata={
                "compatibility": {
                    "supported_platforms": ["aws", "on-prem"],
                    "required_capabilities": ["gateway_api"],
                }
            },
        )

        response = client.get("/api/module-library", headers=viewer_headers)
        assert response.status_code == 200
        data = response.json()
        module = next(m for m in data["modules"] if m["name"] == "compat-module")

        assert module["supported_platforms"] == ["aws", "on-prem"]
        assert module["required_capabilities"] == ["gateway_api"]
        assert module["platform_compatibility"]["supported_profiles"] == ["eks", "generic_onprem"]
        assert module["platform_compatibility"]["compatibility_scope"] == "declared_subset"

    def test_list_modules_prefers_stored_engine_and_includes_lifecycle_capabilities(
        self,
        client,
        viewer_headers,
        all_test_users,
        make_module_library,
    ):
        """Stored engine_type wins and lifecycle booleans serialize truthfully for pack-aware rows."""
        lifecycle = {
            "supports_init": True,
            "supports_plan": False,
            "supports_apply": True,
            "supports_destroy": False,
            "supports_refresh": False,
            "supports_drift": True,
        }
        make_module_library(
            name="ansible-pack-module",
            path="packs/ansible/web",
            module_source_kind="git_catalog",
            execution_engine="ansible",
            deploy_model="ansible_playbook",
            engine_type="ansible",
            pack_manifest={
                "deployment_pack": {
                    "engine": "ansible",
                    "lifecycle": lifecycle,
                }
            },
        )

        response = client.get("/api/module-library", headers=viewer_headers)
        assert response.status_code == 200
        data = response.json()
        module = next(m for m in data["modules"] if m["name"] == "ansible-pack-module")

        assert module["module_source_kind"] == "git_catalog"
        assert module["execution_engine"] == "ansible"
        assert module["deploy_model"] == "ansible_playbook"
        assert module["engine_type"] == "ansible"
        assert module["lifecycle_capabilities"] == lifecycle
        assert module["lifecycle_capabilities"]["supports_destroy"] is False

    def test_list_modules_legacy_row_keeps_fallback_engine_without_lifecycle_payload(
        self,
        client,
        viewer_headers,
        all_test_users,
        make_module_library,
    ):
        """Legacy rows without explicit pack metadata keep fallback behavior."""
        make_module_library(
            name="legacy-opentofu-module",
            path="infra/aws/vpc",
            engine_type=None,
            pack_manifest=None,
        )

        response = client.get("/api/module-library", headers=viewer_headers)
        assert response.status_code == 200
        data = response.json()
        module = next(m for m in data["modules"] if m["name"] == "legacy-opentofu-module")

        assert module["execution_engine"] == "opentofu"
        assert module["engine_type"] == "opentofu"
        assert "lifecycle_capabilities" not in module


class TestModuleCategories:
    """GET /api/module-library/categories."""

    def test_get_categories(self, client, viewer_headers, all_test_users, make_module_library):
        """Returns distinct categories with counts."""
        make_module_library(name="cat-infra-1", category="infra", provider="aws")
        make_module_library(name="cat-infra-2", category="infra", provider="azure")
        make_module_library(name="cat-k8s-1", category="kubernetes", provider="aws")

        response = client.get("/api/module-library/categories", headers=viewer_headers)
        assert response.status_code == 200
        data = response.json()
        assert "categories" in data
        cat_names = [c["name"] for c in data["categories"]]
        assert "infra" in cat_names
        assert "kubernetes" in cat_names

        # infra should have count >= 2
        infra = next(c for c in data["categories"] if c["name"] == "infra")
        assert infra["count"] >= 2


class TestModuleProviders:
    """GET /api/module-library/providers."""

    def test_get_providers(self, client, viewer_headers, all_test_users, make_module_library):
        """Returns distinct providers with counts."""
        make_module_library(name="prov-aws-1", category="infra", provider="aws")
        make_module_library(name="prov-azure-1", category="infra", provider="azure")

        response = client.get("/api/module-library/providers", headers=viewer_headers)
        assert response.status_code == 200
        data = response.json()
        assert "providers" in data
        prov_names = [p["name"] for p in data["providers"]]
        assert "aws" in prov_names
        assert "azure" in prov_names


class TestModuleDetail:
    """GET /api/module-library/{module_id}."""

    def test_get_module_detail(self, client, viewer_headers, all_test_users, make_module_library):
        """Viewer can get full module details by ID."""
        mod = make_module_library(
            name="detail-module",
            category="infra",
            provider="aws",
            description="A detailed test module",
        )

        response = client.get(f"/api/module-library/{mod.id}", headers=viewer_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == mod.id
        assert data["name"] == "detail-module"
        assert data["category"] == "infra"
        assert data["description"] == "A detailed test module"
        assert "created_at" in data

    def test_module_not_found(self, client, viewer_headers, all_test_users):
        """GET nonexistent module returns 404."""
        response = client.get("/api/module-library/99999", headers=viewer_headers)
        assert response.status_code == 404

    def test_module_detail_includes_any_expansion(
        self,
        client,
        viewer_headers,
        all_test_users,
        make_module_library,
    ):
        """Detail payload expands legacy any value conservatively via canonical profiles."""
        mod = make_module_library(
            name="any-compat-module",
            category="infra",
            provider="aws",
            inputs_metadata={
                "compatibility": {
                    "supported_platforms": ["any"],
                    "required_capabilities": [],
                }
            },
        )

        response = client.get(f"/api/module-library/{mod.id}", headers=viewer_headers)
        assert response.status_code == 200
        data = response.json()

        assert data["supported_platforms"] == ["any"]
        assert data["platform_compatibility"]["declared_any"] is True
        assert data["platform_compatibility"]["compatibility_scope"] == "declared_all_profiles"
        assert data["platform_compatibility"]["supported_profiles"] == [
            "generic_onprem",
            "eks",
            "aks",
            "gke",
            "ocp",
        ]

    def test_module_detail_prefers_stored_engine_over_legacy_heuristic(
        self,
        client,
        viewer_headers,
        all_test_users,
        make_module_library,
    ):
        """Stored engine_type should remain authoritative when present."""
        module_path = "packs/ansible/detail"
        mod = make_module_library(
            name="detail-ansible-pack",
            path=module_path,
            engine_type="ansible",
            pack_manifest={
                "deployment_pack": {
                    "engine": "ansible",
                    "lifecycle": {
                        "supports_init": True,
                        "supports_plan": False,
                        "supports_apply": True,
                        "supports_destroy": False,
                        "supports_refresh": False,
                        "supports_drift": True,
                    },
                }
            },
        )

        response = client.get(f"/api/module-library/{mod.id}", headers=viewer_headers)
        assert response.status_code == 200
        data = response.json()

        assert data["engine_type"] == "ansible"
        assert data["module_type"] == "terraform"
        assert data["lifecycle_capabilities"]["supports_destroy"] is False


class TestModuleVariables:
    """GET /api/module-library/{module_id}/variables."""

    def test_get_module_variables(self, client, viewer_headers, all_test_users, make_module_library):
        """Returns variables from stored schema."""
        mod = make_module_library(
            name="vars-module",
            category="infra",
            provider="aws",
            variables_schema=[
                {"name": "vpc_cidr", "type": "string", "description": "VPC CIDR block", "required": True},
                {"name": "enable_dns", "type": "bool", "description": "Enable DNS", "required": False, "default": True},
            ],
        )

        response = client.get(f"/api/module-library/{mod.id}/variables", headers=viewer_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["source"] == "database"
        assert len(data["variables"]) == 2
        var_names = [v["name"] for v in data["variables"]]
        assert "vpc_cidr" in var_names
        assert "enable_dns" in var_names

    def test_get_variables_not_found(self, client, viewer_headers, all_test_users):
        """Variables for nonexistent module returns 404."""
        response = client.get("/api/module-library/99999/variables", headers=viewer_headers)
        assert response.status_code == 404


class TestModuleSync:
    """POST /api/module-library/sync."""

    def test_sync_viewer_denied(self, client, viewer_headers, all_test_users):
        """Viewer cannot trigger module sync — returns 403."""
        response = client.post("/api/module-library/sync", headers=viewer_headers)
        assert response.status_code == 403

    @patch("routes.module_library.sync_module_catalog")
    @patch("routes.module_library.invalidate_cache")
    def test_sync_operator_allowed(self, mock_invalidate, mock_sync, client, operator_headers, all_test_users):
        """Operator can trigger module catalog sync."""
        mock_sync.return_value = {"created": 5, "updated": 2, "unchanged": 10}

        response = client.post("/api/module-library/sync", headers=operator_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["stats"]["created"] == 5
        mock_sync.assert_called_once()


class TestModuleLibraryVersion:
    @staticmethod
    def _seed_module_library_settings(db):
        from models import ApplicationSetting

        db.add(ApplicationSetting(key="module_library.git_url", value="https://github.com/org/modules.git"))
        db.add(ApplicationSetting(key="module_library.git_ref", value="main"))
        db.commit()

    def test_version_route_classifies_http_404(self, client, viewer_headers, all_test_users, db):
        self._seed_module_library_settings(db)

        with patch("requests.get") as mock_get:
            mock_get.return_value = SimpleNamespace(status_code=404, text="not found")
            response = client.get("/api/module-library/version", headers=viewer_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["latest_version"] is None
        assert "VERSION file not found" in data["error"]

    def test_version_route_classifies_connection_error(self, client, viewer_headers, all_test_users, db):
        from requests import exceptions as requests_exceptions

        self._seed_module_library_settings(db)

        with patch("requests.get") as mock_get:
            mock_get.side_effect = requests_exceptions.ConnectionError("Could not resolve host: github.com")
            response = client.get("/api/module-library/version", headers=viewer_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["latest_version"] is None
        assert "[connectivity_dns]" in data["error"]

    def test_version_route_sanitizes_secret_like_error_text(self, client, viewer_headers, all_test_users, db):
        self._seed_module_library_settings(db)

        with patch("requests.get") as mock_get:
            mock_get.side_effect = RuntimeError("request failed https://super-secret@github.com/org/modules")
            response = client.get("/api/module-library/version", headers=viewer_headers)

        assert response.status_code == 200
        data = response.json()
        assert "super-secret" not in (data["error"] or "")
