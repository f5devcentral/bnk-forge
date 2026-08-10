"""
Tests for services.module_sync_service — Git sync and Terraform module parsing.

Covers _find_terraform_modules, _parse_tf_files_basic, _extract_from_inspect,
_guess_category, _guess_provider, _import_module, and sync_git_source error paths.
"""

import json
import os
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from models import ModuleLibrary, ModuleSource
from services.git_auth_service import GitAuthContext, GitAuthService
from services.module_sync_service import ModuleSyncService

# ── Helpers ──────────────────────────────────────────────────────────────

def _make_service():
    db = MagicMock()
    return ModuleSyncService(db), db


# ── _guess_category ──────────────────────────────────────────────────────

class TestGuessCategory:
    def test_infra_from_path(self):
        svc, _ = _make_service()
        assert svc._guess_category("infra/aws/vpc", "vpc") == "infra"

    def test_k8s_from_path(self):
        svc, _ = _make_service()
        assert svc._guess_category("k8s/namespace", "namespace") == "k8s"

    def test_bnk_from_path(self):
        svc, _ = _make_service()
        assert svc._guess_category("bnk/gateway", "gateway") == "bnk"

    def test_other_fallback(self):
        svc, _ = _make_service()
        assert svc._guess_category("custom/thing", "thing") == "other"

    def test_vpc_name_matches_infra(self):
        svc, _ = _make_service()
        assert svc._guess_category("modules/my-vpc", "my-vpc") == "infra"

    def test_eks_name_matches_k8s(self):
        svc, _ = _make_service()
        assert svc._guess_category("modules/eks-cluster", "eks-cluster") == "k8s"


# ── _guess_provider ──────────────────────────────────────────────────────

class TestGuessProvider:
    def test_aws(self):
        svc, _ = _make_service()
        assert svc._guess_provider("infra/aws/vpc") == "aws"

    def test_azure(self):
        svc, _ = _make_service()
        assert svc._guess_provider("infra/azure/vnet") == "azure"

    def test_gcp(self):
        svc, _ = _make_service()
        assert svc._guess_provider("infra/gcp/gke") == "gcp"

    def test_google_alias(self):
        svc, _ = _make_service()
        assert svc._guess_provider("infra/google/vpc") == "gcp"

    def test_generic_fallback(self):
        svc, _ = _make_service()
        assert svc._guess_provider("k8s/namespace") == "generic"


# ── _find_terraform_modules ──────────────────────────────────────────────

class TestFindTerraformModules:
    def test_finds_tf_directories(self, tmp_path):
        # Create structure: root/mod_a/main.tf, root/mod_b/main.tf
        (tmp_path / "mod_a").mkdir()
        (tmp_path / "mod_a" / "main.tf").write_text('resource "null" "a" {}')
        (tmp_path / "mod_b").mkdir()
        (tmp_path / "mod_b" / "variables.tf").write_text('variable "x" {}')
        # Non-tf directory
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "readme.md").write_text("# Docs")

        svc, _ = _make_service()
        modules = svc._find_terraform_modules(str(tmp_path))
        paths = set(modules)
        assert "mod_a" in paths
        assert "mod_b" in paths
        assert "docs" not in paths

    def test_skips_git_directory(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "config.tf").write_text("git internal")
        (tmp_path / "real_mod").mkdir()
        (tmp_path / "real_mod" / "main.tf").write_text('resource "x" "y" {}')

        svc, _ = _make_service()
        modules = svc._find_terraform_modules(str(tmp_path))
        assert all(".git" not in m for m in modules)

    def test_root_tf_files_detected(self, tmp_path):
        (tmp_path / "main.tf").write_text('resource "null" "root" {}')
        svc, _ = _make_service()
        modules = svc._find_terraform_modules(str(tmp_path))
        assert "" in modules  # root directory


# ── _find_pack_modules ─────────────────────────────────────────────────────

class TestFindPackModules:
    def test_finds_pack_manifest_directories(self, tmp_path):
        (tmp_path / "pack_a").mkdir()
        (tmp_path / "pack_a" / "bnkforge.pack.json").write_text("{}")
        (tmp_path / "pack_b").mkdir()
        (tmp_path / "pack_b" / "bnkforge.pack.json").write_text("{}")
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "README.md").write_text("# docs")

        svc, _ = _make_service()
        modules = svc._find_pack_modules(str(tmp_path))
        assert set(modules) == {"pack_a", "pack_b"}

    def test_skips_git_directory(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "bnkforge.pack.json").write_text("{}")
        (tmp_path / "real_pack").mkdir()
        (tmp_path / "real_pack" / "bnkforge.pack.json").write_text("{}")

        svc, _ = _make_service()
        modules = svc._find_pack_modules(str(tmp_path))
        assert modules == ["real_pack"]


# ── _parse_tf_files_basic ────────────────────────────────────────────────

class TestParseTfFilesBasic:
    def test_extracts_variables(self, tmp_path):
        (tmp_path / "variables.tf").write_text(
            'variable "vpc_cidr" {\n  type = string\n}\n'
            'variable "name" {\n  type = string\n}\n'
        )
        svc, _ = _make_service()
        info = svc._parse_tf_files_basic(str(tmp_path))
        var_names = [v["name"] for v in info["variables"]]
        assert "vpc_cidr" in var_names
        assert "name" in var_names

    def test_extracts_outputs(self, tmp_path):
        (tmp_path / "outputs.tf").write_text(
            'output "vpc_id" {\n  value = module.vpc.id\n}\n'
        )
        svc, _ = _make_service()
        info = svc._parse_tf_files_basic(str(tmp_path))
        out_names = [o["name"] for o in info["outputs"]]
        assert "vpc_id" in out_names

    def test_empty_directory(self, tmp_path):
        svc, _ = _make_service()
        info = svc._parse_tf_files_basic(str(tmp_path))
        assert info["variables"] == []
        assert info["outputs"] == []


# ── _extract_from_inspect ────────────────────────────────────────────────

class TestExtractFromInspect:
    def test_extracts_variables_and_outputs(self):
        svc, _ = _make_service()
        config = {
            "variables": {
                "region": {"type": "string", "description": "AWS region"},
                "name": {"type": "string", "required": True},
            },
            "outputs": {
                "vpc_id": {"description": "VPC ID"},
            },
            "required_providers": {
                "aws": {"source": "hashicorp/aws"},
            },
        }
        info = svc._extract_from_inspect(config)
        assert len(info["variables"]) == 2
        assert len(info["outputs"]) == 1
        assert info["provider"] == "aws"

    def test_no_providers(self):
        svc, _ = _make_service()
        config = {"variables": {}, "outputs": {}}
        info = svc._extract_from_inspect(config)
        assert "provider" not in info


# ── _import_module ───────────────────────────────────────────────────────

class TestImportModule:
    def test_creates_new_module(self):
        svc, db = _make_service()
        source = MagicMock()
        source.id = 1
        source.url = "https://github.com/org/repo.git"
        # No existing module
        db.query.return_value.filter.return_value.first.return_value = None

        module_info = {
            "name": "vpc",
            "variables": [{"name": "cidr"}],
            "outputs": [{"name": "vpc_id"}],
            "description": "VPC module",
            "provider": "aws",
        }
        created = svc._import_module(source, module_info, "infra/aws/vpc", "/tmp/repo")
        assert created is True
        db.add.assert_called_once()
        db.commit.assert_called_once()

    def test_updates_existing_module(self):
        svc, db = _make_service()
        source = MagicMock()
        source.id = 1
        source.url = "https://github.com/org/repo.git"
        existing = MagicMock()
        existing.engine_type = None
        existing.content_sha256 = None  # legacy row — hashed rows are skipped (D-033)
        db.query.return_value.filter.return_value.first.return_value = existing

        module_info = {
            "name": "vpc",
            "variables": [{"name": "cidr"}],
            "outputs": [{"name": "vpc_id"}],
            "description": "Updated VPC",
            "provider": "aws",
        }
        created = svc._import_module(source, module_info, "infra/aws/vpc", "/tmp/repo")
        assert created is False
        assert existing.description == "Updated VPC"
        assert existing.module_source_kind == "git_catalog"
        assert existing.execution_engine == "opentofu"
        assert existing.deploy_model == "tofu_module"
        assert existing.engine_type == "opentofu"
        db.commit.assert_called_once()


# ── _import_pack_module ────────────────────────────────────────────────────

class TestImportPackModule:
    def test_creates_new_pack_module(self):
        svc, db = _make_service()
        source = MagicMock()
        source.id = 1
        source.url = "https://github.com/org/repo.git"
        source.branch = "main"
        source.git_ref = None

        query = db.query.return_value
        query.filter.return_value.first.side_effect = [None, None, None, None]
        query.filter.return_value.all.return_value = []

        module_info = {
            "name": "ansible-pack",
            "path": "packs/ansible",
            "version": "0.1.0",
            "category": "infra",
            "provider": "aws",
            "description": "ansible module",
            "tags": ["infra", "ansible"],
            "engine_type": "ansible",
            "pack_manifest": {"schema_version": 1},
            "inputs_metadata": {"required": [], "optional": [], "providers": {}, "compatibility": {}},
            "outputs_metadata": [],
            "dependencies_metadata": {"required": [], "optional": []},
            "dependencies": [],
        }

        created = svc._import_pack_module(source, module_info, "packs/ansible")
        assert created == "created"
        db.add.assert_called_once()
        db.commit.assert_called_once()

    def test_updates_existing_pack_module(self):
        svc, db = _make_service()
        source = MagicMock()
        source.id = 1
        source.url = "https://github.com/org/repo.git"
        source.branch = "main"
        source.git_ref = "v1.2.3"

        existing = MagicMock()
        existing.deploy_model = None
        existing.content_sha256 = None
        query = db.query.return_value
        query.filter.return_value.first.side_effect = [existing]
        query.filter.return_value.all.return_value = []

        module_info = {
            "name": "ansible-pack",
            "path": "packs/ansible",
            "version": "0.2.0",
            "category": "infra",
            "provider": "aws",
            "description": "updated module",
            "tags": ["infra", "ansible"],
            "engine_type": "ansible",
            "pack_manifest": {"schema_version": 1},
            "inputs_metadata": {"required": [], "optional": [], "providers": {}, "compatibility": {}},
            "outputs_metadata": [],
            "dependencies_metadata": {"required": [], "optional": []},
            "dependencies": [],
        }

        created = svc._import_pack_module(source, module_info, "packs/ansible")
        assert created == "updated"
        assert existing.path == "packs/ansible"
        assert existing.source_path == "packs/ansible"
        assert existing.source_version == "v1.2.3"
        assert existing.module_source_kind == "git_catalog"
        assert existing.execution_engine == "ansible"
        assert existing.deploy_model == "ansible_playbook"
        assert existing.engine_type == "ansible"
        db.commit.assert_called_once()

    def test_updates_existing_pack_module_infers_kubernetes_manifest_deploy_model_from_pack_manifest(self):
        svc, db = _make_service()
        source = MagicMock()
        source.id = 1
        source.url = "https://github.com/org/repo.git"
        source.branch = "main"
        source.git_ref = "v1.2.3"

        existing = MagicMock()
        existing.deploy_model = None
        existing.content_sha256 = None
        query = db.query.return_value
        query.filter.return_value.first.side_effect = [existing]
        query.filter.return_value.all.return_value = []

        module_info = {
            "name": "k8s-pack",
            "path": "packs/k8s",
            "version": "0.2.0",
            "category": "k8s",
            "provider": "generic",
            "description": "k8s manifests pack",
            "tags": ["k8s"],
            "engine_type": "kubernetes",
            "pack_manifest": {
                "deployment_pack": {
                    "engine": "kubernetes",
                    "entrypoints": {
                        "manifest_path": "./manifests",
                    },
                }
            },
            "inputs_metadata": {"required": [], "optional": [], "providers": {}, "compatibility": {}},
            "outputs_metadata": [],
            "dependencies_metadata": {"required": [], "optional": []},
            "dependencies": [],
        }

        created = svc._import_pack_module(source, module_info, "packs/k8s")
        assert created == "updated"
        assert existing.execution_engine == "kubernetes-direct"
        assert existing.deploy_model == "manifests"
        db.commit.assert_called_once()

    def test_creates_new_pack_module_with_partial_metadata_without_wrong_tofu_default(self):
        svc, db = _make_service()
        source = MagicMock()
        source.id = 1
        source.url = "https://github.com/org/repo.git"
        source.branch = "main"
        source.git_ref = None

        query = db.query.return_value
        query.filter.return_value.first.side_effect = [None, None, None, None]
        query.filter.return_value.all.return_value = []

        module_info = {
            "name": "k8s-partial-pack",
            "path": "packs/k8s-partial",
            "version": "0.1.0",
            "category": "k8s",
            "provider": "generic",
            "description": "k8s partial pack",
            "tags": ["k8s"],
            "engine_type": "kubernetes",
            "pack_manifest": {
                "deployment_pack": {
                    "engine": "kubernetes",
                    "entrypoints": {
                        "manifest_path": "./rendered",
                    },
                }
            },
            "inputs_metadata": {"required": [], "optional": [], "providers": {}, "compatibility": {}},
            "outputs_metadata": [],
            "dependencies_metadata": {"required": [], "optional": []},
            "dependencies": [],
        }

        created = svc._import_pack_module(source, module_info, "packs/k8s-partial")
        assert created == "created"
        created_module = db.add.call_args.args[0]
        assert created_module.execution_engine == "kubernetes-direct"
        assert created_module.deploy_model == "manifests"
        db.commit.assert_called_once()


# ── sync_git_source error paths ──────────────────────────────────────────

class TestSyncGitSource:
    def test_rejects_non_git_source(self):
        svc, _ = _make_service()
        source = MagicMock()
        source.source_type = "local"
        with pytest.raises(ValueError, match="not a Git source"):
            svc.sync_git_source(source)

    @patch("subprocess.run")
    def test_clone_failure_sets_error_status(self, mock_run):
        svc, db = _make_service()
        source = MagicMock()
        source.source_type = "git"
        source.name = "test-source"
        source.url = "https://github.com/org/repo.git"
        source.auth_type = None
        source.branch = "main"

        mock_run.return_value = MagicMock(returncode=1, stderr="auth failed")

        with pytest.raises(RuntimeError, match="Git clone failed"):
            svc.sync_git_source(source)

        assert source.sync_status == "failed"

    def test_manifest_first_import_skips_invalid_pack(self):
        svc, _ = _make_service()
        source = MagicMock()
        source.source_type = "git"
        source.name = "test-source"
        source.url = "https://github.com/org/repo.git"
        source.branch = "main"
        source.git_ref = None

        valid_pack = {
            "name": "good-pack",
            "path": "packs/good",
            "version": "0.1.0",
            "category": "infra",
            "provider": "aws",
            "description": "good",
            "tags": [],
            "engine_type": "ansible",
            "pack_manifest": {"schema_version": 1},
            "inputs_metadata": {"required": [], "optional": [], "providers": {}, "compatibility": {}},
            "outputs_metadata": [],
            "dependencies_metadata": {"required": [], "optional": []},
            "dependencies": [],
        }

        with patch.object(svc, "_clone_repository", return_value="/tmp/repo"), \
                patch.object(svc, "_find_pack_modules", return_value=["packs/good", "packs/bad"]), \
                patch.object(svc, "_parse_pack_module", side_effect=[valid_pack, ValueError("invalid manifest")]), \
                patch.object(svc, "_import_pack_module", return_value="created") as import_pack, \
                patch.object(svc, "_find_terraform_modules") as find_tf, \
                patch("os.path.exists", return_value=False):
            result = svc.sync_git_source(source)

        assert result["modules_found"] == 2
        assert result["modules_created"] == 1
        assert result["modules_updated"] == 0
        assert len(result["errors"]) == 1
        assert result["sync_mode"] == "manifest"
        assert result["manifest_sync_used"] is True
        assert result["pack_manifests_discovered"] == 2
        assert result["stale_modules_inactivated"] == 0
        assert result["pack_errors"] == [
            {
                "path": "packs/bad",
                "stage": "manifest_parse_validate",
                "message": "invalid manifest",
            }
        ]
        import_pack.assert_called_once_with(source, valid_pack, "packs/good")
        find_tf.assert_not_called()

    def test_falls_back_to_terraform_when_no_manifests_found(self):
        svc, _ = _make_service()
        source = MagicMock()
        source.source_type = "git"
        source.name = "legacy-source"
        source.url = "https://github.com/org/repo.git"
        source.branch = "main"
        source.git_ref = None

        with patch.object(svc, "_clone_repository", return_value="/tmp/repo"), \
                patch.object(svc, "_find_pack_modules", return_value=[]), \
                patch.object(svc, "_find_terraform_modules", return_value=["infra/aws/vpc"]), \
                patch.object(svc, "_parse_terraform_module", return_value={
                    "name": "vpc",
                    "variables": [],
                    "outputs": [],
                    "description": "legacy tf",
                    "provider": "aws",
                }), \
                patch.object(svc, "_import_module", return_value=True) as import_tf, \
                patch("os.path.exists", return_value=False):
            result = svc.sync_git_source(source)

        assert result["modules_found"] == 1
        assert result["modules_created"] == 1
        assert result["modules_updated"] == 0
        assert result["errors"] == []
        assert result["sync_mode"] == "terraform_fallback"
        assert result["manifest_sync_used"] is False
        assert result["pack_manifests_discovered"] == 0
        assert result["stale_modules_inactivated"] == 0
        assert result["pack_errors"] == []
        import_tf.assert_called_once()

    def test_sync_success_emits_success_audit_event(self):
        svc, _ = _make_service()
        source = MagicMock()
        source.id = 10
        source.name = "sync-source"
        source.source_type = "git"
        source.url = "https://github.com/org/repo.git"
        source.branch = "main"
        source.git_ref = None
        source.credential_type = "github_app"
        source.auth_type = "token"

        with patch.object(svc, "_clone_repository", return_value="/tmp/repo"), \
                patch.object(svc, "_find_pack_modules", return_value=[]), \
                patch.object(svc, "_find_terraform_modules", return_value=[]), \
                patch("services.module_sync_service.create_audit_log") as mock_audit, \
                patch("os.path.exists", return_value=False):
            result = svc.sync_git_source(source)

        assert result["modules_found"] == 0
        mock_audit.assert_called_once()
        assert mock_audit.call_args.kwargs["action"] == "module_source_sync_succeeded"


class TestSyncGitSourceIdentityRegression:
    def _make_source(self, db, **overrides):
        defaults = dict(
            name="manifest-source",
            source_type="git",
            url="https://github.com/example/modules.git",
            branch="main",
            is_active=True,
            sync_status="pending",
        )
        defaults.update(overrides)
        source = ModuleSource(**defaults)
        db.add(source)
        db.commit()
        db.refresh(source)
        return source

    def _manifest_info(self, *, description: str) -> dict:
        return {
            "name": "ansible-pack",
            "path": "packs/ansible",
            "version": "0.1.0",
            "category": "infra",
            "provider": "aws",
            "description": description,
            "tags": ["infra", "ansible"],
            "engine_type": "ansible",
            "pack_manifest": {"schema_version": 1},
            "inputs_metadata": {"required": [], "optional": [], "providers": {}, "compatibility": {}},
            "outputs_metadata": [],
            "dependencies_metadata": {"required": [], "optional": []},
            "dependencies": [],
        }

    def test_repeated_manifest_sync_no_duplicates_and_same_version_drift_conflicts(self, db):
        """Repeated sync of the same (source, path, version) never duplicates rows.

        D-033: a hashed version row is immutable — re-sync with the SAME version
        but CHANGED content is reported as a version conflict and the stored row
        keeps its original content (pre-D-033 this silently updated in place)."""
        source = self._make_source(db)
        svc = ModuleSyncService(db)

        with patch.object(svc, "_clone_repository", return_value="/tmp/repo"), \
                patch.object(svc, "_find_pack_modules", return_value=["packs/ansible"]), \
                patch.object(svc, "_parse_pack_module", return_value=self._manifest_info(description="v1")), \
                patch("os.path.exists", return_value=False):
            first = svc.sync_git_source(source)

        with patch.object(svc, "_clone_repository", return_value="/tmp/repo"), \
                patch.object(svc, "_find_pack_modules", return_value=["packs/ansible"]), \
                patch.object(svc, "_parse_pack_module", return_value=self._manifest_info(description="v2")), \
                patch("os.path.exists", return_value=False):
            second = svc.sync_git_source(source)

        modules = db.query(ModuleLibrary).filter(
            ModuleLibrary.module_source_id == source.id,
            ModuleLibrary.source_path == "packs/ansible",
        ).all()

        assert len(modules) == 1
        assert modules[0].description == "v1"  # immutable: drift did not overwrite
        assert first["modules_created"] == 1
        assert first["modules_updated"] == 0
        assert second["modules_created"] == 0
        assert second["modules_updated"] == 0
        assert len(second["version_conflicts"]) == 1
        assert "0.1.0" in second["version_conflicts"][0]

        db.refresh(source)
        assert source.module_count == 1

    def test_import_pack_module_uses_legacy_path_lookup_fallback(self, db):
        source = self._make_source(db)
        legacy = ModuleLibrary(
            name="legacy-pack",
            category="infra",
            path="packs/ansible",
            provider="aws",
            description="legacy",
            git_source="https://github.com/example/modules.git//packs/ansible",
            module_source_id=source.id,
            source_path=None,
            is_official=False,
            is_active=True,
        )
        db.add(legacy)
        db.commit()

        svc = ModuleSyncService(db)
        created = svc._import_pack_module(source, self._manifest_info(description="upgraded"), "packs/ansible")

        assert created == "updated"
        modules = db.query(ModuleLibrary).filter(
            ModuleLibrary.module_source_id == source.id,
            ModuleLibrary.path == "packs/ansible",
        ).all()
        assert len(modules) == 1
        assert modules[0].source_path == "packs/ansible"
        assert modules[0].description == "upgraded"

    def test_manifest_sync_inactivates_stale_manifest_modules_only(self, db):
        source = self._make_source(db)
        existing_present = ModuleLibrary(
            name="present-pack",
            category="infra",
            path="packs/present",
            provider="aws",
            description="present",
            git_source="https://github.com/example/modules.git//packs/present",
            module_source_id=source.id,
            source_path="packs/present",
            engine_type="ansible",
            pack_manifest={"schema_version": 1},
            is_official=False,
            is_active=True,
        )
        existing_stale = ModuleLibrary(
            name="stale-pack",
            category="infra",
            path="packs/stale",
            provider="aws",
            description="stale",
            git_source="https://github.com/example/modules.git//packs/stale",
            module_source_id=source.id,
            source_path="packs/stale",
            engine_type="ansible",
            pack_manifest={"schema_version": 1},
            is_official=False,
            is_active=True,
        )
        legacy_terraform = ModuleLibrary(
            name="legacy-tf",
            category="infra",
            path="infra/aws/vpc",
            provider="aws",
            description="legacy",
            git_source="https://github.com/example/modules.git//infra/aws/vpc",
            module_source_id=source.id,
            source_path="infra/aws/vpc",
            engine_type=None,
            pack_manifest=None,
            is_official=False,
            is_active=True,
        )
        db.add_all([existing_present, existing_stale, legacy_terraform])
        db.commit()

        svc = ModuleSyncService(db)
        with patch.object(svc, "_clone_repository", return_value="/tmp/repo"), \
                patch.object(svc, "_find_pack_modules", return_value=["packs/present"]), \
                patch.object(svc, "_parse_pack_module", return_value=self._manifest_info(description="v2")), \
                patch("os.path.exists", return_value=False):
            result = svc.sync_git_source(source)

        db.refresh(existing_present)
        db.refresh(existing_stale)
        db.refresh(legacy_terraform)
        db.refresh(source)

        assert existing_present.is_active is True
        assert existing_stale.is_active is False
        assert legacy_terraform.is_active is True
        assert result["stale_modules_inactivated"] == 1
        assert source.module_count == 2

    def test_source_level_clone_failure_does_not_inactivate_existing_manifest_modules(self, db):
        source = self._make_source(db)
        existing_pack = ModuleLibrary(
            name="existing-pack",
            category="infra",
            path="packs/existing",
            provider="aws",
            description="existing",
            git_source="https://github.com/example/modules.git//packs/existing",
            module_source_id=source.id,
            source_path="packs/existing",
            engine_type="ansible",
            pack_manifest={"schema_version": 1},
            is_official=False,
            is_active=True,
        )
        db.add(existing_pack)
        db.commit()

        svc = ModuleSyncService(db)
        with patch.object(svc, "_clone_repository", side_effect=RuntimeError("clone failed")), \
                patch("os.path.exists", return_value=False):
            with pytest.raises(RuntimeError, match="clone failed"):
                svc.sync_git_source(source)

        db.refresh(existing_pack)
        assert existing_pack.is_active is True


class TestSecretSafeGitTransport:
    def test_clone_repository_uses_sanitized_url_and_env_auth(self):
        svc, _ = _make_service()
        source = SimpleNamespace(
            id=1,
            name="source-a",
            credential_type="legacy_pat",
            auth_type="token",
            url="https://oldsecret@github.com/org/repo.git",
            branch="main",
            git_ref=None,
        )

        with patch("services.module_sync_service.GitAuthService.resolve_for_module_source") as mock_resolve, \
                patch("services.module_sync_service.GitAuthService.build_git_environment") as mock_env, \
                patch("subprocess.run") as mock_run:
            mock_resolve.return_value = GitAuthContext(
                transport="https_token",
                credential_type="legacy_pat",
                secret="runtime-token",
                username="git",
            )
            mock_env.return_value = ({"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/tmp/askpass"}, lambda: None)
            mock_run.return_value = MagicMock(returncode=0, stderr="")

            temp_dir = svc._clone_repository(source)

        try:
            _, kwargs = mock_run.call_args
            cmd = mock_run.call_args.args[0]
            assert kwargs["env"]["GIT_ASKPASS"] == "/tmp/askpass"
            assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
            assert cmd[-2] == "https://github.com/org/repo.git"
            assert "runtime-token" not in cmd[-2]
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_clone_repository_sanitizes_secret_in_error_message(self):
        svc, _ = _make_service()
        source = SimpleNamespace(
            id=1,
            name="source-a",
            credential_type="legacy_pat",
            auth_type="token",
            url="https://github.com/org/repo.git",
            branch="main",
            git_ref=None,
        )

        with patch("services.module_sync_service.GitAuthService.resolve_for_module_source") as mock_resolve, \
                patch("services.module_sync_service.GitAuthService.build_git_environment") as mock_env, \
                patch("subprocess.run") as mock_run:
            mock_resolve.return_value = GitAuthContext(
                transport="https_token",
                credential_type="legacy_pat",
                secret="super-secret-token",
                username="git",
            )
            mock_env.return_value = ({"GIT_TERMINAL_PROMPT": "0"}, lambda: None)
            mock_run.return_value = MagicMock(
                returncode=1,
                stderr="Authentication failed for https://super-secret-token@github.com/org/repo.git",
            )

            with pytest.raises(RuntimeError) as exc_info:
                svc._clone_repository(source)

        message = str(exc_info.value)
        assert "super-secret-token" not in message
        assert "***" in message
        assert "[auth_failure]" in message

    def test_ssh_transport_uses_git_ssh_wrapper_not_command_with_key_path(self):
        svc, _ = _make_service()
        source = SimpleNamespace(
            id=1,
            name="source-a",
            credential_type="ssh_deploy_key",
            auth_type="ssh",
            url="ssh://git@github.com/org/repo.git",
            branch="main",
            git_ref=None,
        )

        with patch("services.module_sync_service.GitAuthService.resolve_for_module_source") as mock_resolve, \
                patch("services.module_sync_service.GitAuthService.build_git_environment") as mock_env, \
                patch("subprocess.run") as mock_run:
            mock_resolve.return_value = GitAuthContext(
                transport="ssh_key",
                credential_type="ssh_deploy_key",
                secret="PRIVATE KEY",
            )
            mock_env.return_value = ({"GIT_SSH": "/tmp/git_ssh.sh", "GIT_TERMINAL_PROMPT": "0"}, lambda: None)
            mock_run.return_value = MagicMock(returncode=0, stderr="")

            temp_dir = svc._clone_repository(source)

        try:
            _, kwargs = mock_run.call_args
            assert "GIT_SSH" in kwargs["env"]
            assert "GIT_SSH_COMMAND" not in kwargs["env"]
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_clone_repository_reports_runtime_auth_transport_failure(self):
        svc, _ = _make_service()
        source = SimpleNamespace(
            id=1,
            name="source-a",
            credential_type="ssh_deploy_key",
            auth_type="ssh",
            url="ssh://git@git.internal.example.com/org/repo.git",
            branch="main",
            git_ref=None,
        )

        with patch("services.module_sync_service.GitAuthService.resolve_for_module_source") as mock_resolve, \
                patch("services.module_sync_service.GitAuthService.build_git_environment", side_effect=RuntimeError("known_hosts required")), \
                patch("services.module_sync_service.create_audit_log") as mock_audit:
            mock_resolve.return_value = GitAuthContext(
                transport="ssh_key",
                credential_type="ssh_deploy_key",
                secret="PRIVATE KEY",
                ssh_known_hosts=None,
            )
            with pytest.raises(RuntimeError, match="known_hosts required"):
                svc._clone_repository(source)

        mock_audit.assert_called_once()
        assert mock_audit.call_args.kwargs["details"]["phase"] == "runtime_auth_transport"

    def test_clone_repository_ssh_runtime_uses_known_hosts_from_auth_context(self):
        svc, _ = _make_service()
        source = SimpleNamespace(
            id=1,
            name="source-a",
            credential_type="ssh_deploy_key",
            auth_type="ssh",
            url="ssh://git@git.internal.example.com/org/repo.git",
            branch="main",
            git_ref=None,
        )

        captured: dict[str, str] = {}
        original_build_env = GitAuthService.build_git_environment

        with patch("services.module_sync_service.GitAuthService.resolve_for_module_source") as mock_resolve, \
                patch("services.module_sync_service.GitAuthService.build_git_environment") as mock_build_env, \
                patch("subprocess.run") as mock_run:
            mock_resolve.return_value = GitAuthContext(
                transport="ssh_key",
                credential_type="ssh_deploy_key",
                secret="PRIVATE KEY",
                ssh_known_hosts="git.internal.example.com ssh-ed25519 AAAAC3Nza...",
            )

            def _build_env_with_capture(auth_ctx, base_env=None):
                env, cleanup = original_build_env(auth_ctx, base_env)
                wrapper_path = env["GIT_SSH"]
                with open(wrapper_path, encoding="utf-8") as fh:
                    captured["wrapper"] = fh.read()
                known_hosts_path = os.path.join(os.path.dirname(wrapper_path), "known_hosts")
                with open(known_hosts_path, encoding="utf-8") as fh:
                    captured["known_hosts"] = fh.read()
                return env, cleanup

            mock_build_env.side_effect = _build_env_with_capture

            def _capture_run(*args, **kwargs):
                return MagicMock(returncode=0, stderr="")

            mock_run.side_effect = _capture_run

            temp_dir = svc._clone_repository(source)

        try:
            assert mock_build_env.call_args.args[0].ssh_known_hosts is not None
            assert "StrictHostKeyChecking=yes" in captured["wrapper"]
            assert "UserKnownHostsFile=" in captured["wrapper"]
            assert "GlobalKnownHostsFile=/dev/null" in captured["wrapper"]
            assert "git.internal.example.com" in captured["known_hosts"]
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_clone_repository_audits_auth_resolution_failure(self):
        svc, db = _make_service()
        source = SimpleNamespace(
            id=9,
            name="github-source",
            credential_type="github_app",
            auth_type="token",
            url="https://github.com/org/repo.git",
            branch="main",
            git_ref=None,
        )

        with patch("services.module_sync_service.GitAuthService.resolve_for_module_source", side_effect=RuntimeError("no key")), \
                patch("services.module_sync_service.create_audit_log") as mock_audit:
            with pytest.raises(RuntimeError, match="no key"):
                svc._clone_repository(source)

        mock_audit.assert_called_once()

    def test_clone_repository_uses_gitlab_deploy_username_from_metadata(self):
        svc, _ = _make_service()
        source = SimpleNamespace(
            id=1,
            name="gitlab-source",
            credential_type="gitlab_deploy_token",
            auth_type="token",
            auth_token_encrypted="enc-token",
            credential_metadata={"gitlab": {"deploy_username": "gitlab+deploy-token-12"}},
            url="https://gitlab.example.com/team/repo.git",
            branch="main",
            git_ref=None,
        )

        with patch("services.module_sync_service.GitAuthService.resolve_for_module_source") as mock_resolve, \
                patch("services.module_sync_service.GitAuthService.build_git_environment") as mock_env, \
                patch("subprocess.run") as mock_run:
            mock_resolve.return_value = GitAuthContext(
                transport="https_token",
                credential_type="gitlab_deploy_token",
                secret="deploy-secret",
                username="gitlab+deploy-token-12",
            )
            mock_env.return_value = (
                {
                    "GIT_TERMINAL_PROMPT": "0",
                    "GIT_ASKPASS": "/tmp/askpass",
                    "GIT_AUTH_USERNAME": "gitlab+deploy-token-12",
                },
                lambda: None,
            )
            mock_run.return_value = MagicMock(returncode=0, stderr="")

            temp_dir = svc._clone_repository(source)

        try:
            _, kwargs = mock_run.call_args
            assert kwargs["env"]["GIT_AUTH_USERNAME"] == "gitlab+deploy-token-12"
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestPackVariablesSchema324:
    """#324 — pack modules must get variables_schema populated during sync so the
    deploy path (_strip_global_tfvar_secrets / undeclared-var filter) sees jwt_token
    et al. as declared."""

    def test_extract_tf_variables_reads_declared_vars(self, tmp_path):
        svc, _ = _make_service()
        (tmp_path / "variables.tf").write_text(
            'variable "jwt_token" {}\n'
            'variable "manifest_version" {\n  type = string\n}\n'
        )
        names = {v["name"] for v in svc._extract_tf_variables(str(tmp_path))}
        assert "jwt_token" in names
        assert "manifest_version" in names

    def test_extract_tf_variables_empty_dir(self, tmp_path):
        svc, _ = _make_service()
        assert svc._extract_tf_variables(str(tmp_path)) == []

    def test_create_persists_variables_schema(self):
        svc, db = _make_service()
        source = MagicMock(id=1, url="https://github.com/org/repo.git", branch="main", git_ref=None)
        db.query.return_value.filter.return_value.first.side_effect = [None, None, None, None]
        db.query.return_value.filter.return_value.all.return_value = []
        schema = [{"name": "jwt_token", "type": "string", "required": True}]
        module_info = {
            "name": "cneinstall", "path": "packs/cne", "engine_type": "kubernetes",
            "pack_manifest": {"schema_version": 1},
            "inputs_metadata": {"required": [], "optional": [], "providers": {}, "compatibility": {}},
            "variables_schema": schema,
            "outputs_metadata": [], "dependencies_metadata": {"required": [], "optional": []},
            "dependencies": [],
        }
        svc._import_pack_module(source, module_info, "packs/cne")
        created = db.add.call_args[0][0]
        assert created.variables_schema == schema

    def test_update_persists_variables_schema(self):
        svc, db = _make_service()
        source = MagicMock(id=1, url="https://github.com/org/repo.git", branch="main", git_ref=None)
        existing = MagicMock(deploy_model=None, variables_schema=[], content_sha256=None)
        db.query.return_value.filter.return_value.first.side_effect = [existing]
        db.query.return_value.filter.return_value.all.return_value = []
        schema = [{"name": "jwt_token", "type": "string", "required": True}]
        module_info = {
            "name": "cneinstall", "path": "packs/cne", "engine_type": "kubernetes",
            "pack_manifest": {"schema_version": 1},
            "inputs_metadata": {"required": [], "optional": [], "providers": {}, "compatibility": {}},
            "variables_schema": schema,
            "outputs_metadata": [], "dependencies_metadata": {"required": [], "optional": []},
            "dependencies": [],
        }
        svc._import_pack_module(source, module_info, "packs/cne")
        assert existing.variables_schema == schema

    def test_update_does_not_wipe_schema_when_reparse_empty(self):
        svc, db = _make_service()
        source = MagicMock(id=1, url="https://github.com/org/repo.git", branch="main", git_ref=None)
        good = [{"name": "jwt_token"}]
        existing = MagicMock(deploy_model=None, variables_schema=good, content_sha256=None)
        db.query.return_value.filter.return_value.first.side_effect = [existing]
        db.query.return_value.filter.return_value.all.return_value = []
        module_info = {
            "name": "cneinstall", "path": "packs/cne", "engine_type": "kubernetes",
            "pack_manifest": {"schema_version": 1},
            "inputs_metadata": {"required": [], "optional": [], "providers": {}, "compatibility": {}},
            "variables_schema": [],
            "outputs_metadata": [], "dependencies_metadata": {"required": [], "optional": []},
            "dependencies": [],
        }
        svc._import_pack_module(source, module_info, "packs/cne")
        assert existing.variables_schema == good


# ── Artifact manifest merge (DEPLOY-ENGINE-EXT-007) ───────────────────────

class TestArtifactManifestMerge:
    """A co-located bnkforge.artifact.json is validated and its runtime blocks
    (container_image/steps/state/execution/references) are merged into the
    stored pack_manifest, so the container engine has an image + steps to run."""

    @staticmethod
    def _write(mod_dir: Path):
        pack = {
            "schema_version": 1,
            "module": {
                "name": "roksbnkctl-workspace", "path": "roksbnkctl/workspace",
                "version": "1.0.0", "category": "bnk", "description": "x",
            },
            "deployment_pack": {
                "engine": "container", "runner_profile": "container-default",
                "working_directory": ".",
                "lifecycle": {
                    "supports_init": False, "supports_plan": False,
                    "supports_apply": True, "supports_destroy": True,
                    "supports_refresh": False, "supports_drift": False,
                },
                "entrypoints": {},
            },
            "dependencies": {"required": [], "optional": []},
            "inputs": {"required": [], "optional": []},
            "outputs": {"key_outputs": []},
        }
        artifact = {
            "schema_version": 1, "name": "roksbnkctl-workspace", "version": "1.0.0",
            "kind": "container_image",
            "container_image": {
                "registry_host": "ghcr.io", "repository": "jgruberf5/x",
                "digest": "sha256:" + "a" * 64,
            },
            "lifecycle": {"supports_apply": True, "supports_destroy": True},
            "steps": {
                "apply": [{"name": "up", "args": ["roksbnkctl", "up"]}],
                "destroy": [{"name": "down", "args": ["roksbnkctl", "down"]}],
            },
            "actions": {
                "run-scenario": {
                    "title": "Run a functional scenario",
                    "rating": "green",
                    "steps": [{"name": "run", "args": ["roksbnkctl", "scenario", "run", "{{inputs.scenario}}"]}],
                    "inputs": [{"name": "scenario", "type": "string", "source": "user"}],
                },
            },
            "state": {"mount_path": "/work", "home_env": {"ROKSBNKCTL_HOME": "/work"}},
            "execution": {"engine": "container", "limits": {"cpus": "2", "memory": "2g"}},
            "references": [],
        }
        (mod_dir / "bnkforge.pack.json").write_text(json.dumps(pack))
        (mod_dir / "bnkforge.artifact.json").write_text(json.dumps(artifact))

    def test_merges_colocated_artifact_into_pack_manifest(self, tmp_path):
        mod_dir = tmp_path / "roksbnkctl" / "workspace"
        mod_dir.mkdir(parents=True)
        self._write(mod_dir)

        svc, _db = _make_service()
        with patch.object(ModuleSyncService, "_registry_host_allowlist", return_value=["ghcr.io"]):
            result = svc._parse_pack_module("roksbnkctl/workspace", str(tmp_path))

        pm = result["pack_manifest"]
        assert pm["container_image"]["digest"].startswith("sha256:")
        assert pm["container_image"]["registry_host"] == "ghcr.io"
        assert "apply" in pm["steps"] and "destroy" in pm["steps"]
        # D-034: the actions block is merged too — the merge list is explicit
        # (the #442 lesson) and silently drops undeclared blocks.
        assert "run-scenario" in pm["actions"]
        assert pm["actions"]["run-scenario"]["title"] == "Run a functional scenario"
        assert pm["state"]["mount_path"] == "/work"
        assert pm["execution"]["limits"]["memory"] == "2g"
        assert result["execution_engine"] == "container"
        assert result["artifact_references"]["root"] == "roksbnkctl-workspace@1.0.0"

    def test_artifact_ingest_is_fail_closed_on_unlisted_registry(self, tmp_path):
        """A registry_host outside the allowlist is rejected at ingest even when
        the operator has not configured container.registry_host_allowlist — the
        built-in default allowlist is enforced fail-closed (supply-chain gate)."""
        from services.module_metadata import InvalidMetadataSchemaError

        mod_dir = tmp_path / "roksbnkctl" / "workspace"
        mod_dir.mkdir(parents=True)
        self._write(mod_dir)
        # Repoint the co-located artifact at a host NOT in the built-in allowlist.
        artifact_path = mod_dir / "bnkforge.artifact.json"
        artifact = json.loads(artifact_path.read_text())
        artifact["container_image"]["registry_host"] = "evil.example.com"
        artifact_path.write_text(json.dumps(artifact))

        svc, _db = _make_service()
        # Simulate an unconfigured setting: get_default returns None → the method
        # must fall back to the built-in allowlist rather than skip the check.
        with patch("services.defaults_service.get_default", return_value=None):
            with pytest.raises(InvalidMetadataSchemaError):
                svc._parse_pack_module("roksbnkctl/workspace", str(tmp_path))

    def test_registry_host_allowlist_falls_back_to_builtin_when_unset(self):
        """_registry_host_allowlist never returns None; it falls back to the
        built-in safe default so the validator host check is always enforced."""
        svc, _db = _make_service()
        with patch("services.defaults_service.get_default", return_value=None):
            allow = svc._registry_host_allowlist()
        assert "ghcr.io" in allow
        assert "evil.example.com" not in allow

    def test_pack_without_artifact_has_no_container_block(self, tmp_path):
        mod_dir = tmp_path / "roksbnkctl" / "workspace"
        mod_dir.mkdir(parents=True)
        self._write(mod_dir)
        (mod_dir / "bnkforge.artifact.json").unlink()  # remove the artifact

        svc, _db = _make_service()
        result = svc._parse_pack_module("roksbnkctl/workspace", str(tmp_path))
        assert "container_image" not in result["pack_manifest"]
        assert result.get("artifact_references") is None
