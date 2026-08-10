"""
Tests for #324 — module sync must not persist an empty variables_schema when
the module source declares variables.

Covers both code paths:
  - _import_module      (git-catalog sync)
  - _import_registry_module  (registry sync)
"""

import logging
from unittest.mock import MagicMock, patch

import pytest

from services.module_sync_service import ModuleSyncService

# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_service():
    db = MagicMock()
    return ModuleSyncService(db), db


def _source(source_id: int = 1) -> MagicMock:
    src = MagicMock()
    src.id = source_id
    src.url = "https://github.com/org/repo.git"
    src.git_ref = None
    src.branch = "main"
    return src


def _existing_module(variables_schema=None) -> MagicMock:
    mod = MagicMock()
    mod.module_source_kind = "git_catalog"
    mod.execution_engine = "opentofu"
    mod.engine_type = "opentofu"
    mod.variables_schema = variables_schema
    mod.content_sha256 = None  # legacy row — D-033 hashed rows are skipped by the fallback
    return mod


# ── _import_module (git-catalog path) ────────────────────────────────────────

class TestImportModuleVariablesSchema:
    """_import_module: variables_schema persistence behaviour."""

    def test_nonempty_parse_persists_schema(self, tmp_path):
        """Non-empty parse result is written to existing module unconditionally."""
        svc, db = _make_service()
        source = _source()
        existing = _existing_module(variables_schema=[{"name": "old_var"}])
        db.query.return_value.filter.return_value.first.return_value = existing

        module_info = {
            "name": "vpc",
            "variables": [{"name": "cidr"}, {"name": "region"}],
            "outputs": [],
            "description": "VPC module",
            "provider": "aws",
        }
        # tmp_path has no .tf files — has_declarations will be False (irrelevant path)
        svc._import_module(source, module_info, "", str(tmp_path))

        assert existing.variables_schema == [{"name": "cidr"}, {"name": "region"}]

    def test_empty_parse_does_not_overwrite_existing_when_declarations_present(self, tmp_path):
        """
        Empty parse result MUST NOT overwrite a non-empty existing schema when
        variable declarations are present in the module source.
        """
        # Create a variables.tf with a real variable declaration
        (tmp_path / "variables.tf").write_text('variable "cidr_block" {\n  type = string\n}\n')

        svc, db = _make_service()
        source = _source()
        good_schema = [{"name": "cidr_block", "type": "string"}]
        existing = _existing_module(variables_schema=good_schema)
        db.query.return_value.filter.return_value.first.return_value = existing

        module_info = {
            "name": "vpc",
            "variables": [],   # parser returned empty — simulates a parse failure
            "outputs": [],
            "description": "VPC module",
            "provider": "aws",
        }
        svc._import_module(source, module_info, "", str(tmp_path))

        # Existing schema must be preserved
        assert existing.variables_schema == good_schema

    def test_empty_parse_does_not_overwrite_existing_logs_warning(self, tmp_path, caplog):
        """A warning is logged when a non-empty existing schema would have been overwritten."""
        (tmp_path / "variables.tf").write_text('variable "x" {}\n')

        svc, db = _make_service()
        source = _source()
        existing = _existing_module(variables_schema=[{"name": "x"}])
        db.query.return_value.filter.return_value.first.return_value = existing

        module_info = {"name": "mod", "variables": [], "outputs": [], "provider": "aws"}
        with caplog.at_level(logging.WARNING, logger="services.module_sync_service"):
            svc._import_module(source, module_info, "", str(tmp_path))

        assert any("empty variables_schema" in r.message for r in caplog.records)

    def test_empty_parse_no_declarations_allows_empty_schema(self, tmp_path):
        """
        A module with genuinely zero variable declarations should legitimately
        persist an empty schema — this is NOT a parse failure.
        """
        # No .tf files at all → _module_dir_has_variable_declarations returns False
        svc, db = _make_service()
        source = _source()
        existing = _existing_module(variables_schema=[])
        db.query.return_value.filter.return_value.first.return_value = existing

        module_info = {"name": "no_vars", "variables": [], "outputs": [], "provider": "generic"}
        svc._import_module(source, module_info, "", str(tmp_path))

        assert existing.variables_schema == []

    def test_new_module_empty_parse_with_declarations_logs_warning(self, tmp_path, caplog):
        """
        Brand-new module: empty parse + declarations present → warning logged,
        module still created (with empty schema as unresolved placeholder).
        """
        (tmp_path / "variables.tf").write_text('variable "region" {}\n')

        svc, db = _make_service()
        source = _source()
        # No existing module
        db.query.return_value.filter.return_value.first.return_value = None

        module_info = {"name": "infra", "variables": [], "outputs": [], "provider": "aws"}
        with caplog.at_level(logging.WARNING, logger="services.module_sync_service"):
            created = svc._import_module(source, module_info, "", str(tmp_path))

        assert created is True
        db.add.assert_called_once()
        assert any("empty variables_schema" in r.message for r in caplog.records)


# ── _import_registry_module (registry path) ──────────────────────────────────

class TestImportRegistryModuleVariablesSchema:
    """_import_registry_module: variables_schema persistence behaviour (no filesystem)."""

    def _make_mod_data(self, name: str = "vpc") -> dict:
        return {
            "namespace": "hashicorp",
            "name": name,
            "provider": "aws",
            "version": "1.0.0",
            "description": "test module",
        }

    def _detail_response(self, inputs: list) -> MagicMock:
        resp = MagicMock()
        resp.ok = True
        resp.json.return_value = {"root": {"inputs": inputs, "outputs": []}}
        return resp

    def test_nonempty_parse_persists_schema(self):
        """Registry: non-empty fetch result overwrites existing schema."""
        svc, db = _make_service()
        source = _source()
        existing = _existing_module(variables_schema=[{"name": "old"}])
        db.query.return_value.filter.return_value.first.return_value = existing

        with patch("services.module_sync_service.http_requests.get") as mock_get:
            mock_get.return_value = self._detail_response(
                [{"name": "cidr", "type": "string", "required": True}]
            )
            svc._import_registry_module(source, self._make_mod_data(), "https://registry.example.com", {})

        assert existing.variables_schema == [
            {"name": "cidr", "type": "string", "description": None, "default": None, "required": True}
        ]

    def test_empty_fetch_does_not_overwrite_existing_schema(self):
        """Registry: empty fetch must NOT overwrite an existing non-empty schema."""
        svc, db = _make_service()
        source = _source()
        good_schema = [{"name": "region", "type": "string"}]
        existing = _existing_module(variables_schema=good_schema)
        db.query.return_value.filter.return_value.first.return_value = existing

        with patch("services.module_sync_service.http_requests.get") as mock_get:
            mock_get.return_value = self._detail_response([])  # empty inputs from registry
            svc._import_registry_module(source, self._make_mod_data(), "https://registry.example.com", {})

        assert existing.variables_schema == good_schema

    def test_empty_fetch_does_not_overwrite_existing_schema_logs_warning(self, caplog):
        """Registry: a warning is emitted when the existing schema would have been clobbered."""
        svc, db = _make_service()
        source = _source()
        existing = _existing_module(variables_schema=[{"name": "region"}])
        db.query.return_value.filter.return_value.first.return_value = existing

        with patch("services.module_sync_service.http_requests.get") as mock_get:
            mock_get.return_value = self._detail_response([])
            with caplog.at_level(logging.WARNING, logger="services.module_sync_service"):
                svc._import_registry_module(
                    source, self._make_mod_data(), "https://registry.example.com", {}
                )

        assert any("empty variables" in r.message for r in caplog.records)

    def test_genuinely_no_inputs_module_allows_empty_schema(self):
        """Registry: module with no inputs → empty schema is legitimate, no warning."""
        svc, db = _make_service()
        source = _source()
        existing = _existing_module(variables_schema=[])
        db.query.return_value.filter.return_value.first.return_value = existing

        with patch("services.module_sync_service.http_requests.get") as mock_get:
            mock_get.return_value = self._detail_response([])
            svc._import_registry_module(source, self._make_mod_data(), "https://registry.example.com", {})

        assert existing.variables_schema == []
