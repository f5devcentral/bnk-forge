"""
Component tests for the registry service (post-v2_105 rewrite).

Search and metadata calls hit a mocked HTTP session. Import calls mock the
module_fetcher (no real network), the catalog writer (no disk I/O), and the
smoke-test celery dispatch.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from services import module_fetcher
from services.registry_service import (
    OPENTOFU_REGISTRY,
    TERRAFORM_REGISTRY,
    RegistryService,
)

# ── Helpers ──────────────────────────────────────────────────────────


def _mock_response(json_data=None, status_code=200, raise_for_status=False):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    if raise_for_status:
        import requests
        resp.raise_for_status.side_effect = requests.exceptions.HTTPError()
    else:
        resp.raise_for_status.return_value = None
    return resp


def _fake_fetch_result(version: str = "5.0.0") -> module_fetcher.FetchResult:
    return module_fetcher.FetchResult(
        tarball_bytes=b"<fake tarball bytes>",
        sha256="0" * 64,
        canonical_url=f"https://registry.opentofu.org/v1/modules/ns/vpc/aws/{version}",
        upstream_version=version,
    )


# ── search_modules ───────────────────────────────────────────────────


class TestSearchModules:
    @patch.object(RegistryService, "_search_registry")
    def test_returns_opentofu_results(self, mock_search, db):
        mock_search.return_value = {
            "modules": [{"name": "vpc", "provider": "aws"}],
            "meta": {"total_count": 1},
        }
        svc = RegistryService(db)
        result = svc.search_modules(query="vpc")
        assert result["source"] == "opentofu"
        assert len(result["modules"]) == 1

    @patch.object(RegistryService, "_search_registry")
    def test_falls_back_to_terraform(self, mock_search, db):
        mock_search.side_effect = [
            Exception("opentofu down"),
            {"modules": [{"name": "vpc"}], "meta": {"total_count": 1}},
        ]
        svc = RegistryService(db)
        result = svc.search_modules(query="vpc")
        assert result["source"] == "terraform"

    @patch.object(RegistryService, "_search_registry")
    def test_both_fail_returns_empty(self, mock_search, db):
        mock_search.side_effect = Exception("all down")
        svc = RegistryService(db)
        result = svc.search_modules(query="vpc")
        assert result["modules"] == []
        assert result["source"] == "none"


# ── get_module_details / versions ────────────────────────────────────


class TestGetModuleDetails:
    def test_returns_details(self, db):
        svc = RegistryService(db)
        svc.session = MagicMock()
        svc.session.get.return_value = _mock_response({"name": "vpc", "description": "VPC module"})
        result = svc.get_module_details("terraform-aws-modules", "vpc", "aws")
        assert result["name"] == "vpc"

    def test_returns_none_on_failure(self, db):
        import requests
        svc = RegistryService(db)
        svc.session = MagicMock()
        svc.session.get.side_effect = requests.exceptions.ConnectionError()
        assert svc.get_module_details("ns", "name", "aws") is None


class TestGetModuleVersions:
    def test_returns_version_list(self, db):
        svc = RegistryService(db)
        svc.session = MagicMock()
        svc.session.get.return_value = _mock_response({
            "modules": [{"versions": [{"version": "3.0.0"}, {"version": "2.0.0"}]}]
        })
        result = svc.get_module_versions("ns", "vpc", "aws")
        assert len(result) == 2
        assert result[0]["version"] == "3.0.0"

    def test_returns_empty_on_failure(self, db):
        import requests
        svc = RegistryService(db)
        svc.session = MagicMock()
        svc.session.get.side_effect = requests.exceptions.Timeout()
        assert svc.get_module_versions("ns", "name", "aws") == []


# ── import_module_from_registry ──────────────────────────────────────


def _mock_import_pipeline(monkeypatch, tmp_path):
    """Stub fetcher / writer / smoke dispatch for import tests."""
    monkeypatch.setattr(
        "services.registry_service.module_fetcher.fetch_opentofu_registry",
        lambda namespace, name, provider, version: _fake_fetch_result(version),
    )
    monkeypatch.setattr(
        "services.registry_service.module_fetcher.fetch_terraform_registry",
        lambda namespace, name, provider, version: _fake_fetch_result(version),
    )
    target = tmp_path / "module"
    target.mkdir()
    monkeypatch.setattr(
        "services.registry_service.write_module",
        lambda **_: target,
    )
    monkeypatch.setattr(
        "services.registry_service.scrape",
        lambda _path: __import__("services.registry_variable_scraper", fromlist=["ScrapeResult"]).ScrapeResult(),
    )
    monkeypatch.setattr("services.registry_service.remove_module", lambda **_: None)
    return target


class TestImportModule:
    @patch.object(RegistryService, "get_module_versions")
    @patch.object(RegistryService, "get_module_details")
    def test_import_creates_module(self, mock_details, mock_versions, db, monkeypatch, tmp_path):
        mock_details.return_value = {"name": "vpc", "description": "AWS VPC module", "verified": True}
        mock_versions.return_value = [{"version": "5.0.0"}]
        _mock_import_pipeline(monkeypatch, tmp_path)
        # Avoid celery side-effects
        monkeypatch.setattr("tasks.registry_smoke_test.smoke_test_module.delay", lambda _: None)

        svc = RegistryService(db)
        result = svc.import_module_from_registry("terraform-aws-modules", "vpc", "aws")

        assert result is not None
        assert result.name == "vpc"
        assert result.version == "5.0.0"
        assert result.path == "registry/opentofu/terraform-aws-modules/vpc"
        assert result.tarball_sha256 == "0" * 64
        assert result.last_smoke_test_status == "pending"

    @patch.object(RegistryService, "get_module_details")
    def test_import_returns_none_when_not_found(self, mock_details, db):
        mock_details.return_value = None
        svc = RegistryService(db)
        assert svc.import_module_from_registry("ns", "missing", "aws") is None

    @patch.object(RegistryService, "get_module_versions")
    @patch.object(RegistryService, "get_module_details")
    def test_import_reuses_row_on_re_import(self, mock_details, mock_versions, db, monkeypatch, tmp_path):
        mock_details.return_value = {"name": "vpc", "description": "d", "verified": False}
        mock_versions.return_value = [{"version": "1.0.0"}]
        _mock_import_pipeline(monkeypatch, tmp_path)
        monkeypatch.setattr("tasks.registry_smoke_test.smoke_test_module.delay", lambda _: None)

        svc = RegistryService(db)
        first = svc.import_module_from_registry("ns", "vpc", "aws")
        second = svc.import_module_from_registry("ns", "vpc", "aws")

        assert first.id == second.id


class TestCheckForUpdates:
    def test_not_found_raises(self, db):
        svc = RegistryService(db)
        with pytest.raises(ValueError, match="not found"):
            svc.check_for_updates(999)

    @patch.object(RegistryService, "get_module_versions")
    @patch.object(RegistryService, "get_module_details")
    def test_update_available(self, mock_details, mock_versions, db, monkeypatch, tmp_path):
        mock_details.return_value = {"name": "vpc", "description": "d", "verified": False}
        mock_versions.return_value = [{"version": "1.0.0"}]
        _mock_import_pipeline(monkeypatch, tmp_path)
        monkeypatch.setattr("tasks.registry_smoke_test.smoke_test_module.delay", lambda _: None)

        svc = RegistryService(db)
        mod = svc.import_module_from_registry("ns", "vpc", "aws", version="1.0.0")

        mock_versions.return_value = [{"version": "2.0.0"}]
        result = svc.check_for_updates(mod.id)

        assert result["update_available"] is True
        assert result["latest_version"] == "2.0.0"
        assert result["current_version"] == "1.0.0"


def test_module_uses_canonical_constants():
    assert OPENTOFU_REGISTRY == "https://registry.opentofu.org/v1"
    assert TERRAFORM_REGISTRY == "https://registry.terraform.io/v1"
