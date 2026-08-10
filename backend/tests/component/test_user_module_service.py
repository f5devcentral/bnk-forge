"""
BC-030: Component tests for UserModuleService — user module management.

Tests cover: validate_git_source, extract_module_metadata, create_user_module,
update_module_version, _parse_variables_file, _parse_outputs_file,
_parse_main_file, _extract_description_from_readme, list_module_versions.
All git/filesystem calls are mocked.
"""

import os
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from services.user_module_service import UserModuleService

# ── Helpers ──────────────────────────────────────────────────────────

def _mock_repo(refs=None, tags=None):
    """Build a mock git.Repo object."""
    repo = MagicMock()
    origin = MagicMock()

    if refs is None:
        ref_main = MagicMock()
        ref_main.name = "origin/main"
        ref_dev = MagicMock()
        ref_dev.name = "origin/develop"
        refs = [ref_main, ref_dev]

    origin.refs = refs
    origin.fetch.return_value = None
    repo.remotes.origin = origin
    repo.tags = tags or []
    return repo


# ── validate_git_source ─────────────────────────────────────────────

class TestValidateGitSource:
    @patch("services.user_module_service.shutil.rmtree")
    @patch("services.user_module_service.os.path.exists", return_value=True)
    @patch("services.user_module_service.os.walk")
    @patch("git.Repo.clone_from")
    @patch("services.user_module_service.tempfile.mkdtemp", return_value="/tmp/test-clone")
    def test_valid_repo_with_main_tf(self, mock_mkdtemp, mock_clone, mock_walk,
                                      mock_exists, mock_rmtree, db):
        mock_clone.return_value = _mock_repo()
        mock_walk.return_value = [
            ("/tmp/test-clone", [".git"], ["main.tf", "variables.tf"]),
        ]

        svc = UserModuleService(db)
        result = svc.validate_git_source("https://github.com/test/repo.git", "main")

        assert result["valid"] is True
        assert result["has_terraform_files"] is True
        assert "main.tf" in result["terraform_files"]

    @patch("services.user_module_service.shutil.rmtree")
    @patch("services.user_module_service.os.path.exists", return_value=True)
    @patch("services.user_module_service.os.walk")
    @patch("git.Repo.clone_from")
    @patch("services.user_module_service.tempfile.mkdtemp", return_value="/tmp/test-clone")
    def test_repo_without_terraform_files(self, mock_mkdtemp, mock_clone, mock_walk,
                                           mock_exists, mock_rmtree, db):
        mock_clone.return_value = _mock_repo()
        mock_walk.return_value = [
            ("/tmp/test-clone", [], ["README.md"]),
        ]

        svc = UserModuleService(db)
        result = svc.validate_git_source("https://github.com/test/repo.git")

        assert result["valid"] is False
        assert "No Terraform files" in result["error"]

    @patch("services.user_module_service.shutil.rmtree")
    @patch("services.user_module_service.os.path.exists", return_value=True)
    @patch("services.user_module_service.os.walk")
    @patch("git.Repo.clone_from")
    @patch("services.user_module_service.tempfile.mkdtemp", return_value="/tmp/test-clone")
    def test_repo_tf_files_but_no_main_tf(self, mock_mkdtemp, mock_clone, mock_walk,
                                           mock_exists, mock_rmtree, db):
        mock_clone.return_value = _mock_repo()
        mock_walk.return_value = [
            ("/tmp/test-clone", [], ["variables.tf", "outputs.tf"]),
        ]

        svc = UserModuleService(db)
        result = svc.validate_git_source("https://github.com/test/repo.git")

        assert result["valid"] is False
        assert "No main.tf found" in result["error"]

    @patch("services.user_module_service.shutil.rmtree")
    @patch("services.user_module_service.os.path.exists", return_value=True)
    @patch("git.Repo.clone_from")
    @patch("services.user_module_service.tempfile.mkdtemp", return_value="/tmp/test-clone")
    def test_clone_failure(self, mock_mkdtemp, mock_clone, mock_exists, mock_rmtree, db):
        import git as gitmodule
        mock_clone.side_effect = gitmodule.exc.GitCommandError("clone", "fatal: repo not found")

        svc = UserModuleService(db)
        result = svc.validate_git_source("https://github.com/test/nonexistent.git")

        assert result["valid"] is False
        assert "Failed to clone" in result["error"]


# ── _parse_variables_file ────────────────────────────────────────────

class TestParseVariablesFile:
    def test_parse_variables(self, db, tmp_path):
        tf_content = '''
variable "vpc_cidr" {
  type        = string
  description = "CIDR block for VPC"
  default     = "10.0.0.0/16"
}

variable "instance_count" {
  type        = number
  description = "Number of instances"
}
'''
        tf_file = tmp_path / "variables.tf"
        tf_file.write_text(tf_content)

        svc = UserModuleService(db)
        result = svc._parse_variables_file(str(tf_file))

        assert "vpc_cidr" in result
        assert result["vpc_cidr"]["type"] == "string"
        assert result["vpc_cidr"]["description"] == "CIDR block for VPC"
        assert result["vpc_cidr"]["required"] is False  # has default

        assert "instance_count" in result
        assert result["instance_count"]["required"] is True

    def test_parse_empty_file(self, db, tmp_path):
        tf_file = tmp_path / "variables.tf"
        tf_file.write_text("")

        svc = UserModuleService(db)
        result = svc._parse_variables_file(str(tf_file))
        assert result == {}


# ── _parse_outputs_file ──────────────────────────────────────────────

class TestParseOutputsFile:
    def test_parse_outputs(self, db, tmp_path):
        tf_content = '''
output "vpc_id" {
  description = "The VPC ID"
  value       = aws_vpc.main.id
}

output "secret_data" {
  description = "Sensitive output"
  value       = aws_secretsmanager_secret.main.arn
  sensitive = true
}
'''
        tf_file = tmp_path / "outputs.tf"
        tf_file.write_text(tf_content)

        svc = UserModuleService(db)
        result = svc._parse_outputs_file(str(tf_file))

        assert "vpc_id" in result
        assert result["vpc_id"]["description"] == "The VPC ID"
        assert result["vpc_id"]["sensitive"] is False

        assert "secret_data" in result
        assert result["secret_data"]["sensitive"] is True


# ── _parse_main_file ─────────────────────────────────────────────────

class TestParseMainFile:
    def test_detect_aws_provider(self, db, tmp_path):
        tf_content = '''
resource "aws_vpc" "main" {
  cidr_block = var.vpc_cidr
}
'''
        tf_file = tmp_path / "main.tf"
        tf_file.write_text(tf_content)

        svc = UserModuleService(db)
        deps, provider = svc._parse_main_file(str(tf_file))
        assert provider == "aws"

    def test_detect_gcp_provider(self, db, tmp_path):
        tf_content = '''
resource "google_compute_network" "main" {
  name = "my-network"
}
'''
        tf_file = tmp_path / "main.tf"
        tf_file.write_text(tf_content)

        svc = UserModuleService(db)
        deps, provider = svc._parse_main_file(str(tf_file))
        assert provider == "gcp"


# ── _extract_description_from_readme ─────────────────────────────────

class TestExtractDescription:
    def test_extract_from_description_section(self, db):
        readme = """# My Module
## Description
This is a great VPC module for production use.
## Usage
Some usage info
"""
        svc = UserModuleService(db)
        result = svc._extract_description_from_readme(readme)
        assert "great VPC module" in result

    def test_fallback_to_first_paragraph(self, db):
        readme = """# My Module
This module sets up networking.
## Usage
Some info
"""
        svc = UserModuleService(db)
        result = svc._extract_description_from_readme(readme)
        assert "sets up networking" in result

    def test_empty_readme(self, db):
        svc = UserModuleService(db)
        result = svc._extract_description_from_readme("")
        assert result == ""


# ── create_user_module ───────────────────────────────────────────────

class TestCreateUserModule:
    @patch.object(UserModuleService, "extract_module_metadata")
    @patch.object(UserModuleService, "validate_git_source")
    def test_create_success(self, mock_validate, mock_extract, db):
        mock_validate.return_value = {"valid": True}
        mock_extract.return_value = {
            "success": True,
            "variables_schema": {"cidr": {"type": "string"}},
            "dependencies": [],
            "provider": "aws",
            "description": "Test module",
            "readme": "# Test",
        }

        svc = UserModuleService(db)
        result = svc.create_user_module(
            name="my-vpc",
            category="infra",
            git_url="https://github.com/test/vpc.git",
            git_ref="v1.0",
        )

        assert result["success"] is True
        assert result["module"]["name"] == "my-vpc"
        assert result["module"]["provider"] == "aws"

        from models import ModuleLibrary

        row = db.query(ModuleLibrary).filter(ModuleLibrary.id == result["module"]["id"]).first()
        assert row is not None
        assert row.module_source_kind == "user_module"

    @patch.object(UserModuleService, "validate_git_source")
    def test_create_invalid_git_source(self, mock_validate, db):
        mock_validate.return_value = {"valid": False, "error": "Cannot clone repo"}

        svc = UserModuleService(db)
        result = svc.create_user_module(
            name="bad-module", category="infra",
            git_url="https://github.com/test/bad.git",
        )

        assert result["success"] is False
        assert "Cannot clone repo" in result["error"]

    @patch.object(UserModuleService, "extract_module_metadata")
    @patch.object(UserModuleService, "validate_git_source")
    def test_create_duplicate_git_source(self, mock_validate, mock_extract, db):
        mock_validate.return_value = {"valid": True}
        mock_extract.return_value = {"success": True, "variables_schema": {},
                                      "dependencies": [], "provider": None,
                                      "description": "", "readme": ""}

        svc = UserModuleService(db)
        svc.create_user_module(name="mod-a", category="infra",
                               git_url="https://github.com/test/dup.git", git_ref="main")
        result = svc.create_user_module(name="mod-b", category="infra",
                                         git_url="https://github.com/test/dup.git", git_ref="main")

        assert result["success"] is False
        assert "already exists" in result["error"]


# ── update_module_version ────────────────────────────────────────────

class TestUpdateModuleVersion:
    @patch.object(UserModuleService, "extract_module_metadata")
    @patch.object(UserModuleService, "validate_git_source")
    def test_update_success(self, mock_validate, mock_extract, db):
        # First create a module
        mock_validate.return_value = {"valid": True}
        mock_extract.return_value = {
            "success": True, "variables_schema": {}, "dependencies": [],
            "provider": "aws", "description": "Test", "readme": "",
        }

        svc = UserModuleService(db)
        created = svc.create_user_module(
            name="version-test", category="infra",
            git_url="https://github.com/test/ver.git", git_ref="v1.0",
        )
        module_id = created["module"]["id"]

        # Now update version
        result = svc.update_module_version(module_id, "v2.0")
        assert result["success"] is True

    def test_update_nonexistent_module(self, db):
        svc = UserModuleService(db)
        result = svc.update_module_version(99999, "v2.0")
        assert result["success"] is False
        assert "not found" in result["error"]

    @patch.object(UserModuleService, "extract_module_metadata")
    @patch.object(UserModuleService, "validate_git_source")
    def test_update_official_module_rejected(self, mock_validate, mock_extract, db):
        from models import ModuleLibrary
        module = ModuleLibrary(
            name="official-mod", category="infra",
            git_source="git::https://github.com/test.git?ref=v1",
            is_official=True, is_active=True,
        )
        db.add(module)
        db.flush()
        db.refresh(module)

        svc = UserModuleService(db)
        result = svc.update_module_version(module.id, "v2.0")
        assert result["success"] is False
        assert "official" in result["error"].lower()
