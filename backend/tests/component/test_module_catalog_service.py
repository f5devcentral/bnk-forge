"""
Tests for services.module_catalog_service — module catalog CRUD, parsing, and ordering.

BC-007: Module catalog service — real DB, tmp_path for filesystem ops,
mock encryption for token handling.
"""

import json
import os
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from models import ApplicationSetting, ModuleLibrary, ModuleSource, ProjectModule
from services.git_auth_service import GitAuthService
from services.module_catalog_service import (
    _NON_CATALOG_ENGINES,
    CATALOG_GIT_TIMEOUT_SECONDS,
    CATALOG_SYNC_TTL_SECONDS,
    _backfill_official_source_association,
    _ensure_official_module_source,
    add_user_module,
    discover_modules,
    get_catalog_modules,
    get_module_repo_config,
    parse_module_definition,
    recalculate_deployment_orders,
    sync_module_catalog,
    upsert_module,
)

# ---------------------------------------------------------------------------
# get_module_repo_config
# ---------------------------------------------------------------------------


class TestGetModuleRepoConfig:
    """Reading repo config from ApplicationSetting rows."""

    def test_missing_url_raises(self, db):
        result = db.query(ApplicationSetting).filter(
            ApplicationSetting.key == "module_library.git_url"
        ).first()
        assert result is None  # confirm nothing seeded
        with pytest.raises(ValueError, match="git_url not configured"):
            get_module_repo_config(db)

    def test_empty_url_raises(self, db):
        db.add(ApplicationSetting(key="module_library.git_url", value=""))
        db.flush()
        with pytest.raises(ValueError, match="git_url not configured"):
            get_module_repo_config(db)

    def test_url_and_default_ref(self, db):
        db.add(ApplicationSetting(
            key="module_library.git_url",
            value="https://github.com/org/modules.git",
        ))
        db.flush()
        config = get_module_repo_config(db)
        assert config["url"] == "https://github.com/org/modules.git"
        assert config["ref"] == "main"

    def test_custom_ref(self, db):
        db.add(ApplicationSetting(
            key="module_library.git_url",
            value="https://github.com/org/modules.git",
        ))
        db.add(ApplicationSetting(
            key="module_library.git_ref",
            value="develop",
        ))
        db.flush()
        config = get_module_repo_config(db)
        assert config["ref"] == "develop"

    def test_token_setting_does_not_mutate_repo_url(self, db):
        db.add(ApplicationSetting(
            key="module_library.git_url",
            value="https://github.com/org/modules.git",
        ))
        db.flush()
        config = get_module_repo_config(db)
        assert config["url"] == "https://github.com/org/modules.git"

    def test_placeholder_token_filtered(self, db):
        db.add(ApplicationSetting(
            key="module_library.git_url",
            value="https://github.com/org/modules.git",
        ))
        db.add(ApplicationSetting(
            key="module_library.git_token",
            value="change_me",
        ))
        db.flush()
        config = get_module_repo_config(db)
        # URL remains unchanged even when token setting exists
        assert "@github.com" not in config["url"]
        assert config["url"] == "https://github.com/org/modules.git"


# ---------------------------------------------------------------------------
# upsert_module
# ---------------------------------------------------------------------------


class TestUpsertModule:
    """Insert or update ModuleLibrary entries."""

    def test_creates_new_module(self, db):
        module_def = {
            "name": "vpc",
            "category": "infra",
            "git_source": "git::https://github.com/org/mods//infra/vpc?ref=main",
            "path": "infra/vpc",
            "description": "VPC module",
        }
        result = upsert_module(db, module_def)
        assert result == "created"
        mod = db.query(ModuleLibrary).filter(
            ModuleLibrary.git_source == module_def["git_source"]
        ).first()
        assert mod is not None
        assert mod.name == "vpc"
        assert mod.last_synced is not None

    def test_updates_existing_module(self, db, make_module_library):
        existing = make_module_library(
            name="vpc",
            git_source="git::https://github.com/org/mods//infra/vpc?ref=main",
        )
        module_def = {
            "name": "vpc-updated",
            "category": "infra",
            "git_source": "git::https://github.com/org/mods//infra/vpc?ref=main",
            "description": "Updated description",
        }
        result = upsert_module(db, module_def, existing_module=existing)
        assert result == "updated"
        assert existing.name == "vpc-updated"
        assert existing.description == "Updated description"

    def test_sets_last_synced_on_update(self, db, make_module_library):
        existing = make_module_library(
            name="eks",
            git_source="git::https://example.com//eks?ref=main",
        )
        before = datetime.now(UTC)
        upsert_module(db, {
            "name": "eks",
            "git_source": "git::https://example.com//eks?ref=main",
        }, existing_module=existing)
        assert existing.last_synced >= before

    def test_update_preserves_canonical_source_association_fields(self, db, make_module_library):
        existing = make_module_library(
            name="bnk-core",
            is_official=True,
            path="bnk/core",
            git_source="git::https://github.com/org/modules.git//bnk/core?ref=main",
            module_source_id=None,
            source_path=None,
            source_version=None,
        )

        result = upsert_module(
            db,
            {
                "name": "bnk-core",
                "git_source": "git::https://github.com/org/modules.git//bnk/core?ref=main",
                "module_source_id": 12,
                "source_path": "bnk/core",
                "source_version": "main",
            },
            existing_module=existing,
        )

        assert result == "updated"
        assert existing.module_source_id == 12
        assert existing.source_path == "bnk/core"
        assert existing.source_version == "main"

    def test_immutable_hashed_row_is_skipped_not_updated(self, db, make_module_library):
        """D-033: content-hashed version rows are owned by the manifest sync;
        the official-catalog upsert must leave them alone and report 'skipped'
        so sync stats don't count no-ops as updates."""
        existing = make_module_library(
            name="bnk-core",
            git_source="git::https://example.com//bnk/core?ref=main",
            content_sha256="a" * 64,
        )
        result = upsert_module(
            db,
            {
                "name": "renamed-should-not-apply",
                "git_source": "git::https://example.com//bnk/core?ref=main",
            },
            existing_module=existing,
        )
        assert result == "skipped"
        assert existing.name == "bnk-core"  # untouched
        assert existing.last_synced is not None


class TestOfficialModuleSourceRepairHelpers:
    def test_ensure_official_module_source_creates_then_reuses_row(self, db):
        created = _ensure_official_module_source(
            db,
            clean_url="https://github.com/JLCode-tech/bnk-forge-modules.git",
            repo_name="bnk-forge-modules",
            repo_ref="main",
        )

        assert created.id is not None
        assert created.source_type == "git"
        assert created.url == "https://github.com/JLCode-tech/bnk-forge-modules.git"
        assert created.branch == "main"
        assert created.git_ref == "main"

        reused = _ensure_official_module_source(
            db,
            clean_url="https://github.com/JLCode-tech/bnk-forge-modules.git",
            repo_name="bnk-forge-modules",
            repo_ref="release-1",
        )
        assert reused.id == created.id
        assert reused.branch == "release-1"
        assert reused.git_ref == "release-1"

    def test_backfill_official_source_association_repairs_matching_rows(self, db, make_module_library):
        source = ModuleSource(
            name="official-bnk-forge-modules",
            source_type="git",
            url="https://github.com/JLCode-tech/bnk-forge-modules.git",
            branch="main",
            git_ref="main",
            auth_type="none",
            credential_type="none",
            sync_status="success",
            is_active=True,
        )
        db.add(source)
        db.flush()

        matching = make_module_library(
            name="gateway",
            is_official=True,
            path="bnk/gateway",
            git_source="git::https://github.com/JLCode-tech/bnk-forge-modules.git//bnk/gateway?ref=main",
            module_source_id=None,
            source_path=None,
            source_version=None,
        )
        non_matching = make_module_library(
            name="custom",
            is_official=False,
            path="bnk/custom",
            git_source="git::https://github.com/JLCode-tech/bnk-forge-modules.git//bnk/custom?ref=main",
            module_source_id=None,
            source_path=None,
            source_version=None,
        )

        repaired = _backfill_official_source_association(
            db,
            clean_url="https://github.com/JLCode-tech/bnk-forge-modules.git",
            repo_ref="main",
            source_id=source.id,
        )

        assert repaired == 1
        assert matching.module_source_id == source.id
        assert matching.source_path == "bnk/gateway"
        assert matching.source_version == "main"
        assert matching.module_source_kind == "git_catalog"
        assert non_matching.module_source_id is None

    def test_backfill_official_source_association_migrates_builtin_official_rows(self, db, make_module_library):
        source = ModuleSource(
            name="official-bnk-forge-modules",
            source_type="git",
            url="https://github.com/JLCode-tech/bnk-forge-modules.git",
            branch="release/2.2",
            git_ref="release/2.2",
            auth_type="none",
            credential_type="none",
            sync_status="success",
            is_active=True,
        )
        db.add(source)
        db.flush()

        legacy_builtin = make_module_library(
            name="gateway",
            is_official=True,
            path="bnk/gateway",
            git_source="builtin://bnk/gateway",
            module_source_kind="builtin",
            module_source_id=None,
            source_path=None,
            source_version=None,
        )

        repaired = _backfill_official_source_association(
            db,
            clean_url="https://github.com/JLCode-tech/bnk-forge-modules.git",
            repo_ref="release/2.2",
            source_id=source.id,
        )

        assert repaired == 1
        assert legacy_builtin.module_source_id == source.id
        assert legacy_builtin.source_path == "bnk/gateway"
        assert legacy_builtin.source_version == "release/2.2"
        assert legacy_builtin.module_source_kind == "git_catalog"
        assert legacy_builtin.git_source == (
            "git::https://github.com/JLCode-tech/bnk-forge-modules.git//bnk/gateway?ref=release/2.2"
        )

    def test_backfill_migrates_builtin_official_row_in_place_and_preserves_project_module_fk(
        self,
        db,
        make_module_library,
        make_project,
    ):
        source = ModuleSource(
            name="official-bnk-forge-modules",
            source_type="git",
            url="https://github.com/JLCode-tech/bnk-forge-modules.git",
            branch="release/2.2",
            git_ref="release/2.2",
            auth_type="none",
            credential_type="none",
            sync_status="success",
            is_active=True,
        )
        db.add(source)
        db.flush()

        legacy_builtin = make_module_library(
            name="gateway",
            is_official=True,
            path="bnk/gateway",
            git_source="builtin://bnk/gateway",
            module_source_kind="builtin",
            module_source_id=None,
            source_path=None,
            source_version=None,
        )
        project = make_project(name="FK continuity project")
        project_module = ProjectModule(
            project_id=project.id,
            module_library_id=legacy_builtin.id,
            path_in_project="bnk/gateway",
            status="not_initialized",
            enabled=True,
            dependencies=[],
            variables={},
            variable_overrides={},
            deployment_order=0,
        )
        db.add(project_module)
        db.commit()

        repaired = _backfill_official_source_association(
            db,
            clean_url="https://github.com/JLCode-tech/bnk-forge-modules.git",
            repo_ref="release/2.2",
            source_id=source.id,
        )
        db.commit()

        rows = db.query(ModuleLibrary).filter(ModuleLibrary.path == "bnk/gateway").all()
        db.refresh(project_module)

        assert repaired == 1
        assert len(rows) == 1
        assert rows[0].id == legacy_builtin.id
        assert project_module.module_library_id == legacy_builtin.id


# ---------------------------------------------------------------------------
# add_user_module
# ---------------------------------------------------------------------------


class TestAddUserModule:
    """Adding user-provided custom modules."""

    def test_creates_with_defaults(self, db):
        module = add_user_module(
            db, name="custom-mod", category="bnk",
            git_source="git::https://github.com/user/mod.git",
        )
        assert module.id is not None
        assert module.name == "custom-mod"
        assert module.is_official is False
        assert module.module_source_kind == "user_module"
        assert module.is_tested is False
        assert module.description == "User module: custom-mod"
        assert module.version == "latest"

    def test_creates_with_custom_params(self, db):
        module = add_user_module(
            db, name="my-vpc", category="infra",
            git_source="git::https://github.com/user/vpc.git",
            provider="aws",
            description="My custom VPC",
            version="v1.2.0",
        )
        assert module.provider == "aws"
        assert module.description == "My custom VPC"
        assert module.version == "v1.2.0"


# ---------------------------------------------------------------------------
# get_catalog_modules
# ---------------------------------------------------------------------------


class TestGetCatalogModules:
    """Querying the module catalog with filters."""

    def test_no_filters_returns_all_active(self, db, make_module_library):
        make_module_library(name="a", category="infra", is_active=True)
        make_module_library(name="b", category="k8s", is_active=True)
        results = get_catalog_modules(db)
        assert len(results) == 2

    def test_inactive_excluded(self, db, make_module_library):
        make_module_library(name="active", is_active=True)
        make_module_library(name="inactive", is_active=False)
        results = get_catalog_modules(db)
        assert len(results) == 1
        assert results[0].name == "active"

    def test_category_filter(self, db, make_module_library):
        make_module_library(name="vpc", category="infra")
        make_module_library(name="helm", category="k8s")
        results = get_catalog_modules(db, category="infra")
        assert len(results) == 1
        assert results[0].name == "vpc"

    def test_provider_filter(self, db, make_module_library):
        make_module_library(name="aws-vpc", provider="aws")
        make_module_library(name="gcp-vpc", provider="gcp")
        results = get_catalog_modules(db, provider="aws")
        assert len(results) == 1
        assert results[0].name == "aws-vpc"

    def test_official_only(self, db, make_module_library):
        make_module_library(name="official", is_official=True)
        make_module_library(name="user", is_official=False)
        results = get_catalog_modules(db, official_only=True)
        assert len(results) == 1
        assert results[0].name == "official"

    def test_tested_only(self, db, make_module_library):
        make_module_library(name="tested", is_tested=True)
        make_module_library(name="untested", is_tested=False)
        results = get_catalog_modules(db, tested_only=True)
        assert len(results) == 1
        assert results[0].name == "tested"

    def test_includes_legacy_builtin_official_row_before_migration_and_migrated_row_after(self, db, make_module_library):
        module = make_module_library(
            name="gateway",
            is_official=True,
            path="bnk/gateway",
            git_source="builtin://bnk/gateway",
            module_source_kind="builtin",
        )

        before = get_catalog_modules(db, official_only=True)
        assert any(m.id == module.id for m in before)

        module.module_source_kind = "git_catalog"
        module.git_source = "git::https://github.com/JLCode-tech/bnk-forge-modules.git//bnk/gateway?ref=release/2.2"
        db.flush()

        after = get_catalog_modules(db, official_only=True)
        assert any(m.id == module.id for m in after)


# ---------------------------------------------------------------------------
# parse_module_definition
# ---------------------------------------------------------------------------


class TestParseModuleDefinition:
    """Parsing Terraform module directories into definition dicts."""

    def test_basic_parse(self, tmp_path):
        mod_dir = tmp_path / "vpc"
        mod_dir.mkdir()
        (mod_dir / "main.tf").write_text('resource "aws_vpc" "main" {}')
        result = parse_module_definition(str(mod_dir), "vpc", "vpc", "infra")
        assert result["name"] == "vpc"
        assert result["category"] == "infra"
        assert result["path"] == "vpc"
        assert result["module_source_kind"] == "git_catalog"
        assert result["execution_engine"] == "opentofu"
        assert result["deploy_model"] == "tofu_module"
        assert "infra" in result["tags"]

    def test_readme_description(self, tmp_path):
        mod_dir = tmp_path / "eks"
        mod_dir.mkdir()
        (mod_dir / "main.tf").write_text("")
        (mod_dir / "README.md").write_text(
            "# EKS Module\n\n## Description\nManages EKS clusters.\n\n## Usage\n..."
        )
        result = parse_module_definition(str(mod_dir), "eks", "eks", "k8s")
        assert result["description"] == "Manages EKS clusters."

    def test_module_json_metadata(self, tmp_path):
        mod_dir = tmp_path / "gw"
        mod_dir.mkdir()
        (mod_dir / "main.tf").write_text("")
        metadata = {
            "module": {"description": "Gateway module from JSON"},
            "dependencies": {"required": [{"module": "infra/aws/vpc"}], "optional": []},
            "inputs": {"required": [{"name": "port", "source": "user"}], "optional": []},
            "outputs": {"key_outputs": ["endpoint"]},
            "deployment": {"order": 3},
        }
        (mod_dir / "module.json").write_text(json.dumps(metadata))
        result = parse_module_definition(str(mod_dir), "gw", "gw", "bnk")
        assert result["description"] == "Gateway module from JSON"
        assert result["deployment_order"] == 3
        assert result["dependencies_metadata"]["required"] == [{"module": "infra/aws/vpc"}]
        assert result["inputs_metadata"]["required"] == [{"name": "port", "source": "user"}]
        assert result["outputs_metadata"] == ["endpoint"]

    def test_pack_manifest_metadata_normalization(self, tmp_path):
        mod_dir = tmp_path / "ansible-pack"
        mod_dir.mkdir()
        pack_manifest = {
            "schema_version": 1,
            "module": {
                "name": "ansible-pack",
                "path": "infra/ansible-pack",
                "version": "0.1.0",
                "category": "infra",
                "description": "Pack metadata",
                "provider": "aws",
                "supported_platforms": ["aws"],
                "required_capabilities": ["gateway_api"],
                "tags": ["infra", "ansible"],
            },
            "deployment_pack": {
                "engine": "ansible",
                "runner_profile": "ansible-default",
                "working_directory": ".",
                "entrypoints": {
                    "playbook": "apply.yml",
                    "outputs_file": "outputs.json",
                },
                "lifecycle": {
                    "supports_init": True,
                    "supports_plan": True,
                    "supports_apply": True,
                    "supports_destroy": False,
                    "supports_refresh": True,
                    "supports_drift": False,
                },
            },
            "dependencies": {
                "required": [{"module": "infra/aws/vpc", "reason": "network"}],
                "optional": [{"module": "infra/aws/logging"}],
            },
            "inputs": {
                "required": [{"name": "cluster", "type": "string", "description": "cluster", "source": "user"}],
                "optional": [],
            },
            "outputs": {
                "key_outputs": [{"name": "endpoint", "type": "string", "description": "endpoint"}],
            },
        }
        (mod_dir / "bnkforge.pack.json").write_text(json.dumps(pack_manifest))

        result = parse_module_definition(
            str(mod_dir),
            "ansible-pack",
            "ansible-pack",
            "infra",
            discovered_repo_relative_path="infra/ansible-pack",
        )

        assert result["engine_type"] == "ansible"
        assert result["module_source_kind"] == "git_catalog"
        assert result["execution_engine"] == "ansible"
        assert result["deploy_model"] == "ansible_playbook"
        assert result["pack_manifest"] == pack_manifest
        assert result["inputs_metadata"]["required"][0]["name"] == "cluster"
        assert result["dependencies_metadata"]["required"][0]["module"] == "infra/aws/vpc"
        assert result["dependencies"] == ["infra/aws/vpc"]

    def test_pack_manifest_path_mismatch_raises(self, tmp_path):
        mod_dir = tmp_path / "bad-pack"
        mod_dir.mkdir()
        pack_manifest = {
            "schema_version": 1,
            "module": {
                "name": "bad-pack",
                "path": "infra/declared-other-path",
                "version": "0.1.0",
                "category": "infra",
                "description": "Bad path declaration",
            },
            "deployment_pack": {
                "engine": "ansible",
                "runner_profile": "ansible-default",
                "working_directory": ".",
                "entrypoints": {"playbook": "apply.yml"},
                "lifecycle": {
                    "supports_init": True,
                    "supports_plan": True,
                    "supports_apply": True,
                    "supports_destroy": False,
                    "supports_refresh": True,
                    "supports_drift": False,
                },
            },
            "inputs": {"required": [], "optional": []},
            "outputs": {"key_outputs": []},
        }
        (mod_dir / "bnkforge.pack.json").write_text(json.dumps(pack_manifest))

        with pytest.raises(ValueError, match="Pack manifest path mismatch"):
            parse_module_definition(
                str(mod_dir),
                "bad-pack",
                "bad-pack",
                "infra",
                discovered_repo_relative_path="infra/bad-pack",
            )

    def test_pack_manifest_does_not_break_legacy_module_json_path(self, tmp_path):
        mod_dir = tmp_path / "legacy-mod"
        mod_dir.mkdir()
        (mod_dir / "main.tf").write_text("")
        metadata = {
            "module": {"description": "Legacy module metadata"},
            "dependencies": {"required": [{"module": "infra/aws/vpc"}], "optional": []},
            "inputs": {"required": [{"name": "port", "source": "user"}], "optional": []},
            "outputs": {"key_outputs": ["endpoint"]},
        }
        (mod_dir / "module.json").write_text(json.dumps(metadata))

        result = parse_module_definition(str(mod_dir), "legacy-mod", "legacy-mod", "infra")

        assert result["description"] == "Legacy module metadata"
        assert result.get("engine_type") is None
        assert "pack_manifest" not in result

    def test_provider_inference_from_path(self, tmp_path):
        mod_dir = tmp_path / "aws-vpc"
        mod_dir.mkdir()
        (mod_dir / "main.tf").write_text("")
        result = parse_module_definition(str(mod_dir), "vpc", "aws/vpc", "infra")
        assert result["provider"] == "aws"

    def test_workflow_compatibility_by_category(self, tmp_path):
        mod_dir = tmp_path / "mod"
        mod_dir.mkdir()
        (mod_dir / "main.tf").write_text("")
        infra = parse_module_definition(str(mod_dir), "mod", "mod", "infra")
        k8s = parse_module_definition(str(mod_dir), "mod", "mod", "k8s")
        bnk = parse_module_definition(str(mod_dir), "mod", "mod", "bnk")
        assert infra["workflow_compatibility"] == ["greenfield"]
        assert "partial" in k8s["workflow_compatibility"]
        assert "minimal" in bnk["workflow_compatibility"]


# ---------------------------------------------------------------------------
# discover_modules
# ---------------------------------------------------------------------------


class TestDiscoverModules:
    """Walking a repo directory to find Terraform modules."""

    def test_finds_modules_with_main_tf(self, tmp_path):
        (tmp_path / "vpc").mkdir()
        (tmp_path / "vpc" / "main.tf").write_text("")
        (tmp_path / "eks").mkdir()
        (tmp_path / "eks" / "main.tf").write_text("")
        # A directory without main.tf — should be skipped
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "README.md").write_text("docs only")
        modules = discover_modules(str(tmp_path), "infra")
        names = {m["name"] for m in modules}
        assert names == {"vpc", "eks"}

    def test_finds_pack_manifest_without_main_tf(self, tmp_path):
        pack_dir = tmp_path / "ansible-pack"
        pack_dir.mkdir()
        pack_manifest = {
            "schema_version": 1,
            "module": {
                "name": "ansible-pack",
                "path": "infra/ansible-pack",
                "version": "0.1.0",
                "category": "infra",
                "description": "Pack only module",
            },
            "deployment_pack": {
                "engine": "ansible",
                "runner_profile": "ansible-default",
                "working_directory": ".",
                "entrypoints": {"playbook": "apply.yml"},
                "lifecycle": {
                    "supports_init": True,
                    "supports_plan": True,
                    "supports_apply": True,
                    "supports_destroy": False,
                    "supports_refresh": True,
                    "supports_drift": False,
                },
            },
            "inputs": {"required": [], "optional": []},
            "outputs": {"key_outputs": []},
        }
        (pack_dir / "bnkforge.pack.json").write_text(json.dumps(pack_manifest))

        modules = discover_modules(str(tmp_path), "infra")
        assert len(modules) == 1
        assert modules[0]["name"] == "ansible-pack"
        assert modules[0]["engine_type"] == "ansible"

    def test_quarantines_pack_manifest_with_path_mismatch(self, tmp_path):
        pack_dir = tmp_path / "mismatch-pack"
        pack_dir.mkdir()
        pack_manifest = {
            "schema_version": 1,
            "module": {
                "name": "mismatch-pack",
                "path": "infra/not-this-folder",
                "version": "0.1.0",
                "category": "infra",
                "description": "Pack with mismatched path",
            },
            "deployment_pack": {
                "engine": "ansible",
                "runner_profile": "ansible-default",
                "working_directory": ".",
                "entrypoints": {"playbook": "apply.yml"},
                "lifecycle": {
                    "supports_init": True,
                    "supports_plan": True,
                    "supports_apply": True,
                    "supports_destroy": False,
                    "supports_refresh": True,
                    "supports_drift": False,
                },
            },
            "inputs": {"required": [], "optional": []},
            "outputs": {"key_outputs": []},
        }
        (pack_dir / "bnkforge.pack.json").write_text(json.dumps(pack_manifest))

        modules = discover_modules(str(tmp_path), "infra")
        assert len(modules) == 1
        assert modules[0]["name"] == "mismatch-pack"
        assert modules[0]["is_active"] is False
        assert modules[0]["validation_error"] is not None
        assert "path mismatch" in modules[0]["validation_error"].lower()

    def test_skips_terraform_dirs(self, tmp_path):
        mod_dir = tmp_path / "vpc"
        mod_dir.mkdir()
        (mod_dir / "main.tf").write_text("")
        tf_dir = mod_dir / ".terraform" / "modules" / "inner"
        tf_dir.mkdir(parents=True)
        (tf_dir / "main.tf").write_text("")
        modules = discover_modules(str(tmp_path), "infra")
        assert len(modules) == 1
        assert modules[0]["name"] == "vpc"

    def test_skips_examples_dirs(self, tmp_path):
        mod_dir = tmp_path / "vpc"
        mod_dir.mkdir()
        (mod_dir / "main.tf").write_text("")
        example_dir = tmp_path / "examples" / "demo"
        example_dir.mkdir(parents=True)
        (example_dir / "main.tf").write_text("")
        modules = discover_modules(str(tmp_path), "infra")
        assert len(modules) == 1
        assert modules[0]["name"] == "vpc"


# ---------------------------------------------------------------------------
# recalculate_deployment_orders
# ---------------------------------------------------------------------------


class TestRecalculateDeploymentOrders:
    """Topological sort of modules by dependency graph."""

    def test_no_modules(self, db):
        # Should not raise
        recalculate_deployment_orders(db)

    def test_linear_chain(self, db, make_module_library):
        """A -> B -> C  gives orders 1, 2, 3."""
        mod_a = make_module_library(
            name="vpc", category="infra", provider="aws",
            dependencies_metadata=None,
        )
        mod_b = make_module_library(
            name="subnet", category="infra", provider="aws",
            dependencies_metadata={"required": [{"module": "infra/aws/vpc"}], "optional": []},
        )
        mod_c = make_module_library(
            name="eks", category="infra", provider="aws",
            dependencies_metadata={"required": [{"module": "infra/aws/subnet"}], "optional": []},
        )
        recalculate_deployment_orders(db)
        db.flush()
        # Refresh to get updated values
        db.expire_all()
        assert mod_a.deployment_order == 1
        assert mod_b.deployment_order == 2
        assert mod_c.deployment_order == 3

    def test_modules_without_dependencies_get_order_1(self, db, make_module_library):
        mod_a = make_module_library(name="standalone-a", category="infra", provider="aws")
        mod_b = make_module_library(name="standalone-b", category="k8s", provider="aws")
        recalculate_deployment_orders(db)
        assert mod_a.deployment_order == 1
        assert mod_b.deployment_order == 1

    def test_circular_dependency_gets_high_order(self, db, make_module_library):
        """Circular deps should get order 999 and not hang."""
        mod_a = make_module_library(
            name="alpha", category="infra", provider="aws",
            dependencies_metadata={"required": [{"module": "infra/aws/beta"}], "optional": []},
        )
        mod_b = make_module_library(
            name="beta", category="infra", provider="aws",
            dependencies_metadata={"required": [{"module": "infra/aws/alpha"}], "optional": []},
        )
        recalculate_deployment_orders(db)
        db.expire_all()
        assert mod_a.deployment_order == 999
        assert mod_b.deployment_order == 999


# ---------------------------------------------------------------------------
# TestSyncModuleCatalogTTL — force=False respects last_synced_at TTL
# ---------------------------------------------------------------------------


class TestSyncModuleCatalogTTL:
    """sync_module_catalog TTL throttle: force=False skips when synced recently."""

    def _seed_git_url(self, db):
        db.add(ApplicationSetting(
            key="module_library.git_url",
            value="https://github.com/org/modules.git",
        ))
        db.flush()

    def test_skips_when_synced_within_ttl_and_force_is_false(self, db):
        """When last_synced_at is recent and force=False, returns skipped_recent."""
        self._seed_git_url(db)
        recent_ts = datetime.now(UTC).isoformat()
        db.add(ApplicationSetting(
            key="module_library.last_synced_at",
            value=recent_ts,
            category="module_library",
        ))
        db.flush()

        stats = sync_module_catalog(db, force=False)

        assert stats.get("skipped_recent") is True
        assert stats["created"] == 0
        assert stats["errors"] == []

    def test_does_not_skip_when_force_is_true_even_if_recently_synced(self, db):
        """When force=True, sync proceeds regardless of last_synced_at."""
        self._seed_git_url(db)
        recent_ts = datetime.now(UTC).isoformat()
        db.add(ApplicationSetting(
            key="module_library.last_synced_at",
            value=recent_ts,
            category="module_library",
        ))
        db.flush()

        # force=True should bypass the TTL and attempt git operations.
        # We mock git to avoid needing a real repo.
        with patch("services.module_catalog_service.get_module_repo_config") as mock_cfg, \
             patch("services.module_catalog_service.GitAuthService") as mock_auth, \
             patch("git.Repo.clone_from") as mock_clone, \
             patch("os.path.exists", return_value=False), \
             patch("os.makedirs"):
            mock_cfg.return_value = {
                "url": "https://github.com/org/modules.git",
                "ref": "main",
            }
            auth_ctx = MagicMock()
            auth_ctx.secret = None
            mock_auth.resolve_for_module_library_token_setting.return_value = auth_ctx
            mock_env = {}
            mock_auth.build_git_environment.return_value = (mock_env, lambda: None)
            mock_auth.strip_url_credentials.return_value = "https://github.com/org/modules.git"

            repo = MagicMock()
            repo.head.commit.hexsha = "abc1234567890"
            mock_clone.return_value = repo

            stats = sync_module_catalog(db, force=True)

        # Should NOT be skipped_recent
        assert not stats.get("skipped_recent")

    def test_does_not_skip_when_last_synced_at_is_old(self, db):
        """When last_synced_at is older than TTL, sync proceeds (exits early only if recent)."""
        self._seed_git_url(db)
        old_ts = datetime(2020, 1, 1, tzinfo=UTC).isoformat()
        db.add(ApplicationSetting(
            key="module_library.last_synced_at",
            value=old_ts,
            category="module_library",
        ))
        db.flush()

        # An old timestamp should NOT trigger the skip — mock the git path so we
        # can assert that the service attempted a sync (got past the TTL check).
        with patch("services.module_catalog_service.get_module_repo_config") as mock_cfg, \
             patch("services.module_catalog_service.GitAuthService") as mock_auth, \
             patch("git.Repo.clone_from") as mock_clone, \
             patch("os.path.exists", return_value=False), \
             patch("os.makedirs"):
            mock_cfg.return_value = {
                "url": "https://github.com/org/modules.git",
                "ref": "main",
            }
            auth_ctx = MagicMock()
            auth_ctx.secret = None
            mock_auth.resolve_for_module_library_token_setting.return_value = auth_ctx
            mock_auth.build_git_environment.return_value = ({}, lambda: None)
            mock_auth.strip_url_credentials.return_value = "https://github.com/org/modules.git"

            repo = MagicMock()
            repo.head.commit.hexsha = "abc1234567890"
            mock_clone.return_value = repo

            stats = sync_module_catalog(db, force=False)

        assert not stats.get("skipped_recent")

    def test_ttl_constant_is_reasonable(self):
        """CATALOG_SYNC_TTL_SECONDS should be a positive number (sanity check)."""
        assert isinstance(CATALOG_SYNC_TTL_SECONDS, int)
        assert CATALOG_SYNC_TTL_SECONDS > 0


# ---------------------------------------------------------------------------
# TestBackfillNonCatalogEngineExclusion — FIX 5 regression tests
# ---------------------------------------------------------------------------


class TestBackfillNonCatalogEngineExclusion:
    """Backfill must NOT re-parent non-catalog engine modules (k8s, ssh, cli-bnkctl)."""

    def _make_source(self, db):
        source = ModuleSource(
            name="official-bnk-forge-modules",
            source_type="git",
            url="https://github.com/JLCode-tech/bnk-forge-modules.git",
            branch="main",
            git_ref="main",
            auth_type="none",
            credential_type="none",
            sync_status="success",
            is_active=True,
        )
        db.add(source)
        db.flush()
        return source

    def test_backfill_does_not_touch_k8s_builtin_modules(self, db, make_module_library):
        """k8s engine modules with builtin:// git_source must NOT be migrated to git catalog."""
        source = self._make_source(db)

        k8s_builtin = make_module_library(
            name="bnk-prerequisites",
            is_official=True,
            path="k8s/bnk-prerequisites",
            git_source="builtin://k8s/bnk-prerequisites",
            module_source_kind="builtin",
            execution_engine="kubernetes-direct",
            module_source_id=None,
            source_path=None,
            source_version=None,
        )

        repaired = _backfill_official_source_association(
            db,
            clean_url="https://github.com/JLCode-tech/bnk-forge-modules.git",
            repo_ref="main",
            source_id=source.id,
        )

        assert repaired == 0, "k8s engine builtins must not be repaired by catalog backfill"
        assert k8s_builtin.module_source_id is None, "module_source_id must not change"
        assert k8s_builtin.git_source == "builtin://k8s/bnk-prerequisites", "git_source must not be rewritten"
        assert k8s_builtin.module_source_kind == "builtin", "module_source_kind must not become git_catalog"

    def test_backfill_does_not_touch_python_ssh_modules(self, db, make_module_library):
        """Python SSH modules with builtin://python-registry git_source must NOT be migrated."""
        source = self._make_source(db)

        python_mod = make_module_library(
            name="bnk-layer-install",
            is_official=True,
            path="bare-metal/bnk/bnk-layer-install",
            git_source="builtin://python-registry",
            module_source_kind="builtin",
            execution_engine="ssh",
            module_source_id=None,
            source_path=None,
            source_version=None,
        )

        repaired = _backfill_official_source_association(
            db,
            clean_url="https://github.com/JLCode-tech/bnk-forge-modules.git",
            repo_ref="main",
            source_id=source.id,
        )

        assert repaired == 0
        assert python_mod.module_source_id is None
        assert python_mod.git_source == "builtin://python-registry"

    def test_backfill_does_not_touch_cli_bnkctl_modules(self, db, make_module_library):
        """cli-bnkctl modules must NOT be migrated to the git catalog source."""
        source = self._make_source(db)

        cli_mod = make_module_library(
            name="bnk-demo",
            is_official=True,
            path="bare-metal/cli-bnkctl/awsbnkctl/bnk-demo",
            git_source="builtin://cli-bnkctl/awsbnkctl/bnk-demo",
            module_source_kind="builtin",
            execution_engine="cli-bnkctl",
            module_source_id=None,
            source_path=None,
            source_version=None,
        )

        repaired = _backfill_official_source_association(
            db,
            clean_url="https://github.com/JLCode-tech/bnk-forge-modules.git",
            repo_ref="main",
            source_id=source.id,
        )

        assert repaired == 0
        assert cli_mod.module_source_id is None
        assert cli_mod.git_source == "builtin://cli-bnkctl/awsbnkctl/bnk-demo"

    def test_backfill_still_migrates_legacy_catalog_opentofu_modules(self, db, make_module_library):
        """Legacy catalog modules with opentofu engine and builtin:// git_source ARE migrated."""
        source = self._make_source(db)

        legacy_catalog = make_module_library(
            name="gateway",
            is_official=True,
            path="bnk/gateway",
            git_source="builtin://bnk/gateway",
            module_source_kind="builtin",
            execution_engine="opentofu",
            module_source_id=None,
            source_path=None,
            source_version=None,
        )

        repaired = _backfill_official_source_association(
            db,
            clean_url="https://github.com/JLCode-tech/bnk-forge-modules.git",
            repo_ref="main",
            source_id=source.id,
        )

        assert repaired == 1
        assert legacy_catalog.module_source_id == source.id
        assert legacy_catalog.module_source_kind == "git_catalog"

    def test_non_catalog_engines_constant_contains_expected_values(self):
        """_NON_CATALOG_ENGINES must cover the three known non-catalog engine types."""
        assert "kubernetes-direct" in _NON_CATALOG_ENGINES
        assert "ssh" in _NON_CATALOG_ENGINES
        assert "cli-bnkctl" in _NON_CATALOG_ENGINES


class TestSyncModuleCatalogTimestamp:
    """F1 (#468 review): the catalog-level last_synced_at that the boot TTL throttle
    reads must be written on every successful sync, including content repos that ship
    no top-level VERSION file — otherwise the throttle never engages and boot re-clones
    on every restart."""

    @patch("git.Repo.clone_from")
    @patch.object(GitAuthService, "build_git_environment")
    @patch.object(GitAuthService, "resolve_for_module_library_token_setting")
    def test_sync_records_last_synced_at_when_no_version_file(
        self, mock_resolve, mock_build_env, mock_clone, db, tmp_path
    ):
        db.add(ApplicationSetting(
            key="module_library.git_url",
            value="https://github.com/org/content.git",
        ))
        db.flush()
        mock_resolve.return_value = MagicMock()
        mock_build_env.return_value = ({}, lambda: None)
        mock_clone.return_value = MagicMock()

        # tmp_path exists but has no "content" subdir → the sync takes the clone
        # branch and finds no VERSION file and no category dirs.
        with patch("services.module_catalog_service.MODULE_CATALOG_PATH", str(tmp_path)):
            stats = sync_module_catalog(db, force=True)

        assert stats["errors"] == []
        mock_clone.assert_called_once()
        # No VERSION file → no synced_version recorded...
        assert "synced_version" not in stats
        # ...but the last_synced_at setting is still written (the F1 fix).
        setting = db.query(ApplicationSetting).filter(
            ApplicationSetting.key == "module_library.last_synced_at"
        ).first()
        assert setting is not None and setting.value

    @patch("git.Repo.clone_from")
    @patch.object(GitAuthService, "build_git_environment")
    @patch.object(GitAuthService, "resolve_for_module_library_token_setting")
    def test_clone_passes_overall_timeout_cap(
        self, mock_resolve, mock_build_env, mock_clone, db, tmp_path
    ):
        """F2 (#468 review): every git network op is bounded by an overall
        kill_after_timeout so a slow/hung clone can't gate boot readiness forever."""
        db.add(ApplicationSetting(
            key="module_library.git_url",
            value="https://github.com/org/content.git",
        ))
        db.flush()
        mock_resolve.return_value = MagicMock()
        mock_build_env.return_value = ({}, lambda: None)
        mock_clone.return_value = MagicMock()

        with patch("services.module_catalog_service.MODULE_CATALOG_PATH", str(tmp_path)):
            sync_module_catalog(db, force=True)

        _, kwargs = mock_clone.call_args
        assert kwargs["kill_after_timeout"] == CATALOG_GIT_TIMEOUT_SECONDS
