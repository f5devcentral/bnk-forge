"""
BC-034: Component tests for ApiService — core API service layer.

Tests cover: get_provider_summary, get_sync_status, get_settings,
update_setting, batch_update_settings, set_aws_auth_method,
get_aws_auth_method, get_database_stats, _upsert_setting.
Uses real SQLite DB via `db` fixture.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from core.errors import BadRequestError, NotFoundError
from models import ApplicationSetting, Project, SyncJob
from services.api_service import ApiService

# ── Helpers ──────────────────────────────────────────────────────────

def _create_setting(db, key, value, category="general", is_encrypted=False):
    setting = ApplicationSetting(
        key=key, value=value, category=category,
        description=f"Setting {key}", is_encrypted=is_encrypted,
    )
    db.add(setting)
    db.flush()
    return setting


def _create_project(db, name, cloud_provider="aws", **kwargs):
    project = Project(
        name=name, project_type="cloud-aws", cloud_provider=cloud_provider,
        environment="dev", backend_type="local", color="#abc", icon="cloud",
        is_active=True, **kwargs,
    )
    db.add(project)
    db.flush()
    return project


# ── get_provider_summary ─────────────────────────────────────────────

class TestProviderSummary:
    def test_empty_projects(self, db):
        svc = ApiService(db)
        result = svc.get_provider_summary()
        assert result["total_providers"] == 0
        assert result["providers"] == []

    def test_single_provider(self, db):
        _create_project(db, "AWS Project 1")
        _create_project(db, "AWS Project 2")

        svc = ApiService(db)
        result = svc.get_provider_summary()
        assert result["total_providers"] == 1
        assert result["providers"][0]["name"] == "AWS"
        assert result["providers"][0]["count"] == 2

    def test_multiple_providers(self, db):
        _create_project(db, "AWS", cloud_provider="aws")
        _create_project(db, "GCP", cloud_provider="gcp")

        svc = ApiService(db)
        result = svc.get_provider_summary()
        assert result["total_providers"] == 2


# ── get_sync_status ──────────────────────────────────────────────────

class TestSyncStatus:
    def test_empty_sync_history(self, db):
        svc = ApiService(db)
        result = svc.get_sync_status()
        assert result["recent_jobs"] == []
        assert result["running_jobs_count"] == 0

    def test_with_sync_jobs(self, db):
        job = SyncJob(
            job_type="module_library",
            resource_type="modules",
            status="success",
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
            records_synced=10,
            records_created=5,
            records_updated=5,
        )
        db.add(job)
        db.flush()

        svc = ApiService(db)
        result = svc.get_sync_status()
        assert len(result["recent_jobs"]) == 1
        assert result["recent_jobs"][0]["job_type"] == "module_library"


# ── get_settings / update_setting ────────────────────────────────────

class TestSettings:
    def test_get_settings_empty(self, db):
        svc = ApiService(db)
        result = svc.get_settings()
        assert result["settings"] == {}

    def test_get_settings_grouped_by_category(self, db):
        _create_setting(db, "aws.region", "us-east-1", category="aws")
        _create_setting(db, "general.theme", "dark", category="general")

        svc = ApiService(db)
        result = svc.get_settings()
        assert "aws" in result["settings"]
        assert "general" in result["settings"]

    def test_deprecated_settings_excluded(self, db):
        _create_setting(db, "old.key", "value", category="deprecated")

        svc = ApiService(db)
        result = svc.get_settings()
        assert "deprecated" not in result["settings"]

    def test_update_existing_setting(self, db):
        _create_setting(db, "aws.region", "us-east-1", category="aws")

        svc = ApiService(db)
        result = svc.update_setting("aws.region", "eu-west-1")
        assert result["key"] == "aws.region"
        assert result["value"] == "eu-west-1"

    def test_update_nonexistent_setting_raises(self, db):
        svc = ApiService(db)
        with pytest.raises(NotFoundError):
            svc.update_setting("nonexistent.key", "value")


# ── batch_update_settings ────────────────────────────────────────────

class TestBatchUpdateSettings:
    def test_update_existing(self, db):
        _create_setting(db, "a.key", "old")

        svc = ApiService(db)
        result = svc.batch_update_settings({"a.key": "new"})
        assert result["success"] is True
        assert "a.key" in result["updated"]

    def test_create_new_setting(self, db):
        svc = ApiService(db)
        result = svc.batch_update_settings({"new.key": "value"})
        assert "new.key" in result["updated"]

    def test_mixed_update_and_create(self, db):
        _create_setting(db, "existing", "old")

        svc = ApiService(db)
        result = svc.batch_update_settings({
            "existing": "updated",
            "brand_new": "fresh",
        })
        assert len(result["updated"]) == 2


# ── set_aws_auth_method ──────────────────────────────────────────────

class TestSetAWSAuthMethod:
    @patch.object(ApiService, "_refresh_eks_kubeconfigs", return_value=([], []))
    def test_set_profile_method(self, mock_refresh, db):
        svc = ApiService(db)
        result = svc.set_aws_auth_method({
            "auth_method": "profile",
            "credentials": {"profile": "my-profile"},
        })

        assert result["success"] is True
        assert result["auth_method"] == "profile"

    @patch.object(ApiService, "_refresh_eks_kubeconfigs", return_value=([], []))
    @patch("services.api_service.encrypt_value", return_value="encrypted")
    def test_set_access_keys_method(self, mock_encrypt, mock_refresh, db):
        svc = ApiService(db)
        result = svc.set_aws_auth_method({
            "auth_method": "access_keys",
            "credentials": {
                "access_key_id": "AKIA123",
                "secret_access_key": "secret",
            },
        })

        assert result["success"] is True
        assert "aws.access_key_id" in result["updated_keys"]

    def test_invalid_auth_method_raises(self, db):
        svc = ApiService(db)
        with pytest.raises(BadRequestError):
            svc.set_aws_auth_method({"auth_method": "invalid"})

    @patch.object(ApiService, "_refresh_eks_kubeconfigs", return_value=([], []))
    def test_profile_method_missing_name_raises(self, mock_refresh, db):
        svc = ApiService(db)
        with pytest.raises(BadRequestError):
            svc.set_aws_auth_method({
                "auth_method": "profile",
                "credentials": {},
            })


# ── get_aws_auth_method ──────────────────────────────────────────────

class TestGetAWSAuthMethod:
    @patch("services.api_service.get_default", return_value="us-east-1")
    def test_default_when_no_settings(self, mock_default, db):
        svc = ApiService(db)
        result = svc.get_aws_auth_method()

        assert result["success"] is True
        assert result["auth_method"] == "access_keys"  # default
        assert result["access_keys_configured"] is False

    @patch("services.api_service.get_default", return_value="us-east-1")
    def test_configured_profile(self, mock_default, db):
        _create_setting(db, "aws.auth_method", "profile", category="aws")
        _create_setting(db, "aws.profile", "production", category="aws")

        svc = ApiService(db)
        result = svc.get_aws_auth_method()
        assert result["auth_method"] == "profile"
        assert result["profile"] == "production"
        assert result["profile_configured"] is True


# ── get_database_stats ───────────────────────────────────────────────

class TestDatabaseStats:
    @patch("services.api_service.cache")
    def test_stats_returned(self, mock_cache, db):
        mock_cache.get.return_value = None
        _create_project(db, "Stat Project")

        # Mock db.execute for the raw SQL (test DB may lack some tables/columns)
        mock_rows = [
            ("projects", 1),
            ("active_projects", 1),
            ("synced_projects", 0),
            ("clusters", 0),
            ("gateways", 0),
            ("project_modules", 0),
            ("module_library", 0),
            ("sync_jobs", 0),
            ("settings", 0),
        ]
        mock_result = MagicMock()
        mock_result.fetchall.return_value = mock_rows
        original_execute = db.execute
        with patch.object(db, "execute", return_value=mock_result):
            svc = ApiService(db)
            result = svc.get_database_stats()

        assert "stats" in result
        assert "projects" in result
        assert result["stats"]["projects"] >= 1
