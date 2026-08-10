"""
Tests for services.workspace_manager — workspace lifecycle management.

BC-024: Workspace create/cleanup, init checks, plan validation, hash tracking.
Real DB for module records, tmp_path for filesystem operations.
"""

import hashlib
import json
import os
from unittest.mock import MagicMock, patch

import pytest

from services.workspace_manager import WorkspaceManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def wm(db):
    """Create a WorkspaceManager with the real DB session."""
    return WorkspaceManager(db)


@pytest.fixture()
def mock_module(db, make_project, make_project_module, make_module_library, tmp_path):
    """Create a module with a temporary workspace path."""
    project = make_project()
    lib = make_module_library(name="vpc", path="bnk/vpc", version="1.0.0")
    mod = make_project_module(project=project, library_module=lib, status="not_initialized")
    mod.workspace_path = str(tmp_path / "workspaces" / str(project.id) / str(mod.id))
    os.makedirs(mod.workspace_path, exist_ok=True)
    db.flush()
    return mod


# ---------------------------------------------------------------------------
# get_workspace_path / ensure_workspace
# ---------------------------------------------------------------------------

class TestWorkspacePath:
    """Workspace path management."""

    def test_get_workspace_path(self, wm, db, make_project, make_project_module, make_module_library):
        project = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        mod = make_project_module(project=project, library_module=lib)

        path = wm.get_workspace_path(mod)
        assert str(project.id) in path
        assert str(mod.id) in path

    def test_ensure_workspace_creates_directory(self, wm, db, make_project, make_project_module, make_module_library, tmp_path):
        project = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        mod = make_project_module(project=project, library_module=lib)

        wm.BASE_PATH = str(tmp_path / "workspaces")
        wm.ensure_workspace(mod)

        assert mod.workspace_path is not None
        assert os.path.isdir(mod.workspace_path)


# ---------------------------------------------------------------------------
# is_initialized / needs_reinit
# ---------------------------------------------------------------------------

class TestInitChecks:
    """Workspace initialization status checks."""

    def test_not_initialized_empty_workspace(self, wm, mock_module):
        assert wm.is_initialized(mock_module) is False

    def test_initialized_with_terraform_dir(self, wm, mock_module):
        tf_dir = os.path.join(mock_module.workspace_path, ".terraform")
        os.makedirs(tf_dir)
        lock_file = os.path.join(mock_module.workspace_path, ".terraform.lock.hcl")
        with open(lock_file, "w") as f:
            f.write("provider locked")
        assert wm.is_initialized(mock_module) is True

    def test_needs_reinit_when_not_initialized(self, wm, mock_module):
        """needs_reinit returns tuple (True, reason) when not initialized."""
        result = wm.needs_reinit(mock_module)
        assert result[0] is True
        assert "not initialized" in result[1].lower()

    def test_no_reinit_when_version_matches(self, wm, mock_module):
        """Reinit not needed when version file matches current version."""
        tf_dir = os.path.join(mock_module.workspace_path, ".terraform")
        os.makedirs(tf_dir)
        os.makedirs(os.path.join(mock_module.workspace_path, ".terraform", "modules"), exist_ok=True)
        lock_file = os.path.join(mock_module.workspace_path, ".terraform.lock.hcl")
        with open(lock_file, "w") as f:
            f.write("provider locked")

        # Save version file
        version_file = os.path.join(mock_module.workspace_path, ".bnk_init_version")
        with open(version_file, "w") as f:
            f.write(mock_module.library_module.version or "unknown")

        # Save backend hash
        hash_file = os.path.join(mock_module.workspace_path, ".bnk_backend_hash")
        with open(hash_file, "w") as f:
            f.write(wm._compute_backend_hash(mock_module))

        needs, reason = wm.needs_reinit(mock_module)
        assert needs is False
        assert reason == ""

    def test_needs_reinit_version_mismatch(self, wm, mock_module):
        """Reinit needed when module version changed."""
        tf_dir = os.path.join(mock_module.workspace_path, ".terraform")
        os.makedirs(tf_dir)
        os.makedirs(os.path.join(mock_module.workspace_path, ".terraform", "modules"), exist_ok=True)
        lock_file = os.path.join(mock_module.workspace_path, ".terraform.lock.hcl")
        with open(lock_file, "w") as f:
            f.write("provider locked")

        version_file = os.path.join(mock_module.workspace_path, ".bnk_init_version")
        with open(version_file, "w") as f:
            f.write("0.9.0")  # Old version

        needs, reason = wm.needs_reinit(mock_module)
        assert needs is True
        assert "version changed" in reason.lower()

    def test_not_initialized_no_workspace(self, wm, db, make_project, make_project_module, make_module_library):
        project = make_project()
        lib = make_module_library(name="test", path="test/mod")
        mod = make_project_module(project=project, library_module=lib)
        mod.workspace_path = None
        assert wm.is_initialized(mod) is False


# ---------------------------------------------------------------------------
# Plan management
# ---------------------------------------------------------------------------

class TestPlanManagement:
    """Saved plan operations."""

    def test_no_saved_plan(self, wm, mock_module):
        assert wm.has_saved_plan(mock_module) is False

    def test_has_saved_plan(self, wm, mock_module):
        plan_path = os.path.join(mock_module.workspace_path, "plan.out")
        with open(plan_path, "wb") as f:
            f.write(b"plan-data")
        assert wm.has_saved_plan(mock_module) is True

    def test_get_plan_path_when_exists(self, wm, mock_module):
        plan_path = os.path.join(mock_module.workspace_path, "plan.out")
        with open(plan_path, "wb") as f:
            f.write(b"plan-data")
        path = wm.get_plan_path(mock_module)
        assert path is not None
        assert path.endswith("plan.out")

    def test_get_plan_path_returns_none_when_no_plan(self, wm, mock_module):
        path = wm.get_plan_path(mock_module)
        assert path is None

    def test_clear_plan(self, wm, mock_module):
        plan_path = os.path.join(mock_module.workspace_path, "plan.out")
        with open(plan_path, "wb") as f:
            f.write(b"plan-data")
        wm.clear_plan(mock_module)
        assert not os.path.exists(plan_path)


# ---------------------------------------------------------------------------
# Variable hashing
# ---------------------------------------------------------------------------

class TestVariableHashing:
    """Track variable changes for plan invalidation."""

    def test_compute_vars_hash_deterministic(self, wm, mock_module):
        vars_a = {"cidr": "10.0.0.0/16", "region": "us-east-1"}
        hash1 = wm.compute_vars_hash(mock_module, vars_a)
        hash2 = wm.compute_vars_hash(mock_module, vars_a)
        assert hash1 == hash2

    def test_different_vars_different_hash(self, wm, mock_module):
        hash1 = wm.compute_vars_hash(mock_module, {"cidr": "10.0.0.0/16"})
        hash2 = wm.compute_vars_hash(mock_module, {"cidr": "10.1.0.0/16"})
        assert hash1 != hash2

    def test_vars_changed_false_when_no_hash_stored(self, wm, mock_module):
        """No stored hash means vars have NOT changed (can't determine, allow to proceed)."""
        mock_module.vars_hash = None
        assert wm.vars_changed(mock_module) is False

    def test_vars_changed_true_when_hash_differs(self, wm, mock_module):
        """When stored hash differs from current, vars have changed."""
        mock_module.vars_hash = "old-hash-value"
        # compute_vars_hash does a lazy import of build_variables inside the method,
        # so we mock compute_vars_hash directly to avoid hitting real services.
        with patch.object(wm, 'compute_vars_hash', return_value="different-hash-value"):
            result = wm.vars_changed(mock_module)
        assert result is True


# ---------------------------------------------------------------------------
# Cleanup operations
# ---------------------------------------------------------------------------

class TestCleanup:
    """Workspace cleanup operations."""

    def test_cleanup_module_workspace(self, wm, mock_module):
        """Cleaning up removes the workspace directory."""
        assert os.path.isdir(mock_module.workspace_path)
        wm.cleanup_module_workspace(mock_module)
        assert not os.path.isdir(mock_module.workspace_path)

    def test_cleanup_nonexistent_workspace_no_error(self, wm, mock_module):
        """Cleaning up a nonexistent workspace doesn't raise."""
        mock_module.workspace_path = "/nonexistent/path"
        wm.cleanup_module_workspace(mock_module)  # Should not raise

    def test_cleanup_project_workspaces(self, wm, db, make_project, tmp_path):
        """Clean up all workspaces for a project."""
        project = make_project()
        wm.BASE_PATH = str(tmp_path / "workspaces")
        project_dir = os.path.join(wm.BASE_PATH, str(project.id))
        os.makedirs(os.path.join(project_dir, "module1"))
        os.makedirs(os.path.join(project_dir, "module2"))

        wm.cleanup_project_workspaces(project.id)
        assert not os.path.exists(project_dir)

    def test_cleanup_blueprint_workspace(self, wm, db, make_project, make_stack_instance, tmp_path):
        project = make_project()
        stack = make_stack_instance(project=project)
        wm.BASE_PATH = str(tmp_path / "workspaces")

        blueprint_dir = wm.get_blueprint_workspace_path(project.id, stack.id)
        os.makedirs(blueprint_dir, exist_ok=True)

        assert wm.cleanup_blueprint_workspace(project.id, stack.id) is True
        assert not os.path.exists(blueprint_dir)

    def test_orphaned_workspaces_skip_blueprint_dirs(self, wm, db, make_project, tmp_path):
        project = make_project()
        wm.BASE_PATH = str(tmp_path / "workspaces")
        project_dir = os.path.join(wm.BASE_PATH, str(project.id))
        os.makedirs(os.path.join(project_dir, "blueprint-123"), exist_ok=True)

        orphaned = wm.get_orphaned_workspaces()
        assert os.path.join(project_dir, "blueprint-123") not in orphaned


# ---------------------------------------------------------------------------
# Blueprint cache methods
# ---------------------------------------------------------------------------

class TestBlueprintCache:
    """Blueprint cache attach/publish behavior."""

    def test_attach_cached_init_creates_symlinks(self, wm, mock_module, make_stack_instance, tmp_path):
        stack = make_stack_instance(project=mock_module.project)
        mock_module.stack_instance_id = stack.id
        mock_module.library_module.module_source_kind = "git_catalog"
        mock_module.library_module.execution_engine = "opentofu"
        wm.BASE_PATH = str(tmp_path / "workspaces")

        mock_module.workspace_path = wm.get_workspace_path(mock_module)
        os.makedirs(mock_module.workspace_path, exist_ok=True)
        with open(os.path.join(mock_module.workspace_path, "main.tf"), "w") as f:
            f.write('terraform { required_version = ">= 1.6" }')

        cache_key = wm.compute_cache_key(mock_module)
        cache_path = wm.get_blueprint_cache_path(mock_module.project_id, stack.id, cache_key)
        os.makedirs(os.path.join(cache_path, ".terraform", "providers"), exist_ok=True)
        os.makedirs(os.path.join(cache_path, ".terraform", "modules"), exist_ok=True)
        with open(os.path.join(cache_path, ".terraform.lock.hcl"), "w") as f:
            f.write("provider lock")

        attached = wm.attach_cached_init(mock_module, stack.id)

        providers_dst = os.path.join(mock_module.workspace_path, ".terraform", "providers")
        modules_dst = os.path.join(mock_module.workspace_path, ".terraform", "modules")
        assert attached is True
        assert os.path.islink(providers_dst)
        assert os.path.islink(modules_dst)
        assert os.path.exists(os.path.join(mock_module.workspace_path, ".terraform.lock.hcl"))

    def test_attach_cached_init_fallback_no_cache(self, wm, mock_module, make_stack_instance, tmp_path):
        stack = make_stack_instance(project=mock_module.project)
        mock_module.stack_instance_id = stack.id
        mock_module.library_module.module_source_kind = "git_catalog"
        mock_module.library_module.execution_engine = "opentofu"
        wm.BASE_PATH = str(tmp_path / "workspaces")

        mock_module.workspace_path = wm.get_workspace_path(mock_module)
        os.makedirs(mock_module.workspace_path, exist_ok=True)
        with open(os.path.join(mock_module.workspace_path, "main.tf"), "w") as f:
            f.write("terraform {}")

        assert wm.attach_cached_init(mock_module, stack.id) is False

    def test_publish_init_to_cache_atomic(self, wm, mock_module, make_stack_instance, tmp_path):
        stack = make_stack_instance(project=mock_module.project)
        mock_module.stack_instance_id = stack.id
        mock_module.library_module.module_source_kind = "git_catalog"
        mock_module.library_module.execution_engine = "opentofu"
        wm.BASE_PATH = str(tmp_path / "workspaces")

        mock_module.workspace_path = wm.get_workspace_path(mock_module)
        os.makedirs(os.path.join(mock_module.workspace_path, ".terraform", "providers"), exist_ok=True)
        os.makedirs(os.path.join(mock_module.workspace_path, ".terraform", "modules"), exist_ok=True)
        with open(os.path.join(mock_module.workspace_path, "main.tf"), "w") as f:
            f.write("terraform {}")
        with open(os.path.join(mock_module.workspace_path, ".terraform.lock.hcl"), "w") as f:
            f.write("provider lock")

        published = wm.publish_init_to_cache(mock_module, stack.id)
        cache_key = wm.compute_cache_key(mock_module)
        cache_path = wm.get_blueprint_cache_path(mock_module.project_id, stack.id, cache_key)

        assert published is True
        assert os.path.exists(os.path.join(cache_path, ".terraform", "providers"))
        assert os.path.exists(os.path.join(cache_path, ".terraform", "modules"))
        assert os.path.exists(os.path.join(cache_path, ".terraform.lock.hcl"))


# ---------------------------------------------------------------------------
# _ensure_blueprint_repo_locked — module_source branch fallback (Fix #1)
# ---------------------------------------------------------------------------

class TestEnsureBlueprintRepoLockedBranchFallback:
    """When ref and parsed_ref are empty, fall back to module_source.branch."""

    def test_uses_module_source_branch_when_ref_and_parsed_ref_empty(self, wm, db, make_project, make_stack_instance, tmp_path):
        """Clone should use module_source.branch when git_source has no ?ref= and ref=''."""
        project = make_project()
        stack = make_stack_instance(project=project)
        wm.BASE_PATH = str(tmp_path / "workspaces")

        # A module_source object with branch="release/2.3" and no git_ref
        module_source = MagicMock()
        module_source.git_ref = None
        module_source.branch = "release/2.3"

        # git_source without ?ref= embedded, ref="" passed by caller
        git_source = "https://github.com/example/catalog.git"
        ref = ""

        git_clone_calls = []

        def _fake_run(cmd, **kwargs):
            git_clone_calls.append(cmd)
            if "clone" in cmd:
                # Create the repo dir so the code can write meta
                repo_path = cmd[-1]
                os.makedirs(repo_path, exist_ok=True)
            result = MagicMock()
            result.returncode = 0
            return result

        with patch("services.workspace_manager.GitAuthService.resolve_for_module_source") as mock_auth, \
             patch("services.workspace_manager.GitAuthService.build_git_environment") as mock_env, \
             patch("subprocess.run", side_effect=_fake_run):

            mock_auth.return_value = MagicMock(secret="")
            mock_env.return_value = ({}, lambda: None)

            wm._ensure_blueprint_repo_locked(
                project.id, stack.id, git_source, ref, module_source=module_source
            )

        # There should be exactly one clone call; find it
        clone_calls = [c for c in git_clone_calls if "clone" in c]
        assert len(clone_calls) == 1, f"Expected one git clone call, got: {git_clone_calls}"
        clone_cmd = clone_calls[0]
        # --branch <ref> is always passed; verify it used "release/2.3"
        assert "--branch" in clone_cmd
        branch_idx = clone_cmd.index("--branch")
        assert clone_cmd[branch_idx + 1] == "release/2.3", (
            f"Expected clone to use 'release/2.3', got '{clone_cmd[branch_idx + 1]}'"
        )

    def test_explicit_ref_takes_priority_over_module_source_branch(self, wm, db, make_project, make_stack_instance, tmp_path):
        """An explicit ref= must not be overridden by module_source.branch."""
        project = make_project()
        stack = make_stack_instance(project=project)
        wm.BASE_PATH = str(tmp_path / "workspaces")

        module_source = MagicMock()
        module_source.git_ref = None
        module_source.branch = "release/2.3"

        # git_source has an embedded ?ref=
        git_source = "https://github.com/example/catalog.git?ref=v1.0.0"
        ref = ""  # no override from call site

        clone_calls = []

        def _fake_run(cmd, **kwargs):
            if "clone" in cmd:
                clone_calls.append(cmd)
                os.makedirs(cmd[-1], exist_ok=True)
            result = MagicMock()
            result.returncode = 0
            return result

        with patch("services.workspace_manager.GitAuthService.resolve_for_module_source") as mock_auth, \
             patch("services.workspace_manager.GitAuthService.build_git_environment") as mock_env, \
             patch("subprocess.run", side_effect=_fake_run):

            mock_auth.return_value = MagicMock(secret="")
            mock_env.return_value = ({}, lambda: None)

            wm._ensure_blueprint_repo_locked(
                project.id, stack.id, git_source, ref, module_source=module_source
            )

        assert len(clone_calls) == 1
        branch_idx = clone_calls[0].index("--branch")
        # parsed_ref from ?ref=v1.0.0 wins over module_source.branch
        assert clone_calls[0][branch_idx + 1] == "v1.0.0"

    def test_module_source_git_ref_preferred_over_branch(self, wm, db, make_project, make_stack_instance, tmp_path):
        """module_source.git_ref takes precedence over module_source.branch when both set."""
        project = make_project()
        stack = make_stack_instance(project=project)
        wm.BASE_PATH = str(tmp_path / "workspaces")

        module_source = MagicMock()
        module_source.git_ref = "refs/tags/v2.3.0"
        module_source.branch = "release/2.3"

        git_source = "https://github.com/example/catalog.git"
        ref = ""

        clone_calls = []

        def _fake_run(cmd, **kwargs):
            if "clone" in cmd:
                clone_calls.append(cmd)
                os.makedirs(cmd[-1], exist_ok=True)
            result = MagicMock()
            result.returncode = 0
            return result

        with patch("services.workspace_manager.GitAuthService.resolve_for_module_source") as mock_auth, \
             patch("services.workspace_manager.GitAuthService.build_git_environment") as mock_env, \
             patch("subprocess.run", side_effect=_fake_run):

            mock_auth.return_value = MagicMock(secret="")
            mock_env.return_value = ({}, lambda: None)

            wm._ensure_blueprint_repo_locked(
                project.id, stack.id, git_source, ref, module_source=module_source
            )

        assert len(clone_calls) == 1
        branch_idx = clone_calls[0].index("--branch")
        assert clone_calls[0][branch_idx + 1] == "refs/tags/v2.3.0"


# ---------------------------------------------------------------------------
# save_init_version / save_backend_hash
# ---------------------------------------------------------------------------

class TestVersionAndHashPersistence:
    """Persist init version and backend hash files."""

    def test_save_init_version(self, wm, mock_module):
        wm.save_init_version(mock_module)
        version_file = os.path.join(mock_module.workspace_path, ".bnk_init_version")
        assert os.path.exists(version_file)
        with open(version_file) as f:
            content = f.read()
        # Should contain the library module version
        assert content == (mock_module.library_module.version or "unknown")

    def test_save_backend_hash(self, wm, mock_module):
        wm.save_backend_hash(mock_module)
        hash_file = os.path.join(mock_module.workspace_path, ".bnk_backend_hash")
        assert os.path.exists(hash_file)
        with open(hash_file) as f:
            content = f.read()
        assert len(content) == 64  # SHA256 hex digest

    def test_compute_backend_hash_deterministic(self, wm, mock_module):
        hash1 = wm._compute_backend_hash(mock_module)
        hash2 = wm._compute_backend_hash(mock_module)
        assert hash1 == hash2
        assert len(hash1) == 64

    def test_save_init_version_no_workspace_noop(self, wm, db, make_project, make_project_module, make_module_library):
        """save_init_version is a no-op when workspace_path is None."""
        project = make_project()
        lib = make_module_library(name="test", path="test/mod")
        mod = make_project_module(project=project, library_module=lib)
        mod.workspace_path = None
        wm.save_init_version(mod)  # Should not raise


# ---------------------------------------------------------------------------
# plan_is_valid
# ---------------------------------------------------------------------------

class TestPlanIsValid:
    """Validate saved plan for apply."""

    def test_invalid_when_no_plan(self, wm, mock_module):
        valid, reason = wm.plan_is_valid(mock_module)
        assert valid is False
        assert "No saved plan" in reason

    def test_valid_plan(self, wm, mock_module):
        """Plan is valid when plan.out exists, vars unchanged, no reinit needed."""
        # Create plan file
        plan_path = os.path.join(mock_module.workspace_path, "plan.out")
        with open(plan_path, "wb") as f:
            f.write(b"saved-plan")

        # Ensure initialized
        tf_dir = os.path.join(mock_module.workspace_path, ".terraform")
        os.makedirs(tf_dir)
        os.makedirs(os.path.join(mock_module.workspace_path, ".terraform", "modules"), exist_ok=True)
        lock_file = os.path.join(mock_module.workspace_path, ".terraform.lock.hcl")
        with open(lock_file, "w") as f:
            f.write("locked")

        # Save version and hash
        wm.save_init_version(mock_module)

        # No stored vars_hash means vars_changed returns False
        mock_module.vars_hash = None

        valid, reason = wm.plan_is_valid(mock_module)
        assert valid is True
        assert reason == ""
