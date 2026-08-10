"""
Workspace Manager for BNK-Forge v2

Manages persistent workspaces for OpenTofu modules.
Replaces temp workspace workflow with persistent workspaces that enable:
- Init once, plan once, apply saved plan
- Faster operations (skip redundant init)
- Plan/apply consistency (apply uses exact plan user reviewed)

ENG-006: This service retains its own db.commit() calls because its methods
are called at multiple points during long-running Celery task lifecycles
(init, plan, apply, destroy) with interleaved task-level commits. Refactoring
would require restructuring the entire OpenTofu task flow.

Workspace structure:
/app/workspaces/{project_id}/{module_id}/
  .terraform/              # Provider cache (persists after init)
  .terraform.lock.hcl      # Provider lock file
  terraform.tfvars.json    # Generated from DB vars
  backend.tf               # Backend configuration
  encryption.tf            # State encryption config
  bnk_forge_providers.tf   # Injected provider config
  plan.out                 # Saved plan file (after plan, before apply)
  *.tf                     # Module source files (cloned from git)
"""

import fcntl
import hashlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from services.git_auth_service import GitAuthService
from services.infrastructure_access_service import cleanup_durable_infra_private_key

if TYPE_CHECKING:
    from models import ProjectModule

logger = logging.getLogger(__name__)


class WorkspaceManager:
    """
    Manages persistent workspaces for OpenTofu modules.

    Workspaces persist across operations to enable:
    - Skip init if already initialized
    - Save plan.out for later apply
    - Detect variable changes (invalidate plan)
    """

    BASE_PATH = "/app/workspaces"

    # Named volume backing the persistent artifact workspaces. A sibling
    # container started via the docker-socket-proxy mounts this volume BY NAME
    # with a per-component subpath — NOT a host path: on Docker Desktop the
    # named volume and /var/lib/docker/volumes/<name>/_data are different
    # storage, so a host-path bind would not share state with the worker (which
    # mounts the named volume). Overridable via WORKSPACE_VOLUME.
    DEFAULT_WORKSPACE_VOLUME = "bnk-forge_workspace_data"

    # Legacy host-side base path for the bind-mount fallback, used ONLY when an
    # operator sets WORKSPACE_HOST_BASE (non-default volume driver / bind layout).
    DEFAULT_VOLUME_HOST_BASE = "/var/lib/docker/volumes/bnk-forge_workspace_data/_data"

    def __init__(self, db: Session):
        self.db = db

    def get_workspace_path(self, module: "ProjectModule") -> str:
        """
        Get the workspace path for a module.

        Path structure: /app/workspaces/{project_id}/{module_id}/

        Args:
            module: ProjectModule instance

        Returns:
            Full path to workspace directory
        """
        return os.path.join(self.BASE_PATH, str(module.project_id), str(module.id))

    # =========================================================================
    # Artifact-component workspaces (container engine)
    # =========================================================================
    # Artifact components reuse the same persistent workspace, keyed by
    # (project_id, module_id) == (deployment, component). The directory is the
    # canonical state location: an apply writes its state into the workspace and
    # a later destroy reads it back, so the SAME subpath must be restored before
    # every run. These helpers operate on (project_id, module_id) ints directly
    # so they can run from the container task layer without a ProjectModule.

    @classmethod
    def _host_base(cls) -> str:
        """Resolve the host-side base path of the workspace named volume."""
        return os.environ.get("WORKSPACE_HOST_BASE") or cls.DEFAULT_VOLUME_HOST_BASE

    @staticmethod
    def _deployment_group(module) -> str | None:
        """Stable shared-workspace group key for blueprint-deployed modules.

        Modules from one imported-blueprint deployment share the path prefix
        ``imported-blueprint/<release_id>/…`` (set by imported_blueprint_service).
        That release id groups the deployment's phase modules so they can share
        one workspace. Returns None when no group can be derived.
        """
        path = getattr(module, "path_in_project", "") or ""
        parts = path.split("/")
        if len(parts) >= 2 and parts[0] == "imported-blueprint" and parts[1]:
            return f"bp-{parts[1]}"
        return None

    def artifact_workspace_key(self, module, scope: str | None) -> str:
        """Workspace identity for an artifact module.

        ``scope == "deployment"`` shares one workspace across all modules of a
        blueprint deployment (so e.g. a `bnk` phase sees the `cluster` phase's
        state); anything else isolates per module (the default). Falls back to
        the module id when no deployment group is derivable.
        """
        if scope == "deployment":
            group = self._deployment_group(module)
            if group:
                return group
        return str(module.id)

    def artifact_workspace_path(self, project_id: int, key: int | str) -> str:
        """In-container path for an artifact component's persistent workspace.

        Keyed by (project_id, key) where key is the module id (component scope)
        or a deployment-group id (deployment scope) — one durable directory
        across apply/destroy.
        """
        return os.path.join(self.BASE_PATH, str(project_id), str(key))

    def artifact_workspace_host_path(self, project_id: int, key: int | str) -> str:
        """Host-side path of an artifact workspace, for sibling-container bind mounts.

        Used only for the host-path bind fallback (WORKSPACE_HOST_BASE); the
        default path mounts the named volume by name + subpath instead.
        """
        return os.path.join(self._host_base(), str(project_id), str(key))

    @classmethod
    def artifact_workspace_volume(cls) -> str | None:
        """Named volume the sibling mounts, or None to use a host-path bind.

        Returns None when WORKSPACE_HOST_BASE is set (operator opted into a
        host-path bind layout); otherwise the named volume (default
        ``bnk-forge_workspace_data``, overridable via WORKSPACE_VOLUME).
        """
        if os.environ.get("WORKSPACE_HOST_BASE"):
            return None
        return os.environ.get("WORKSPACE_VOLUME") or cls.DEFAULT_WORKSPACE_VOLUME

    @staticmethod
    def artifact_workspace_subpath(project_id: int, key: int | str) -> str:
        """Subpath within the workspace volume — (project, key)."""
        return f"{project_id}/{key}"

    def ensure_artifact_workspace(self, project_id: int, key: int | str) -> str:
        """Create (if needed) and return the artifact workspace in-container path.

        Restoring before each run is implicit: the directory persists in the
        named volume, so apply's state is present when destroy runs. We only
        ensure the directory exists (a fresh component or a wiped volume).
        """
        path = self.artifact_workspace_path(project_id, key)
        if not os.path.exists(path):
            os.makedirs(path, exist_ok=True)
            logger.info("Created artifact workspace directory: %s", path)
        return path

    def get_blueprint_workspace_path(self, project_id: int, stack_id: int) -> str:
        """Get shared blueprint workspace path for a stack."""
        return os.path.join(self.BASE_PATH, str(project_id), f"blueprint-{stack_id}")

    def get_blueprint_cache_path(self, project_id: int, stack_id: int, cache_key: str) -> str:
        """Get path to blueprint init cache directory for a cache key."""
        return os.path.join(self.get_blueprint_workspace_path(project_id, stack_id), "cache", cache_key)

    @staticmethod
    def _parse_git_source(git_source: str) -> tuple[str, str, str]:
        """Parse git source into repo URL, ref, and optional subpath."""
        source = git_source or ""
        if source.startswith("git::"):
            source = source[5:]

        ref = "main"
        if "?ref=" in source:
            source, ref = source.rsplit("?ref=", 1)

        subpath = ""
        if "://" in source:
            protocol_part, rest = source.split("://", 1)
            if "//" in rest:
                repo_part, subpath = rest.split("//", 1)
                source = f"{protocol_part}://{repo_part}"
            else:
                source = f"{protocol_part}://{rest}"
        elif "//" in source:
            source, subpath = source.split("//", 1)

        return GitAuthService.strip_url_credentials(source), ref, subpath

    @contextmanager
    def _blueprint_cache_lock(self, project_id: int, stack_id: int):
        """Acquire per-blueprint lock to serialize cache publishing."""
        blueprint_path = self.get_blueprint_workspace_path(project_id, stack_id)
        os.makedirs(blueprint_path, exist_ok=True)
        lock_path = os.path.join(blueprint_path, "blueprint_cache.lock")
        with open(lock_path, "w") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def ensure_blueprint_repo(
        self,
        project_id: int,
        stack_id: int,
        git_source: str,
        ref: str,
        module_source=None,
    ) -> str:
        """Ensure shared blueprint repo exists for stack and return repo path.

        Serialized via the per-blueprint cache lock so two modules in the same
        stack can't race on the clone target dir. Without the lock, multiple
        sibling-module inits triggered by chain-based dispatch (PR #79) all
        called this concurrently and one would crash with 'destination path
        already exists and is not an empty directory'.
        """
        with self._blueprint_cache_lock(project_id, stack_id):
            return self._ensure_blueprint_repo_locked(
                project_id, stack_id, git_source, ref, module_source,
            )

    def _ensure_blueprint_repo_locked(
        self,
        project_id: int,
        stack_id: int,
        git_source: str,
        ref: str,
        module_source=None,
    ) -> str:
        # Guard: builtin:// is a marker for modules shipped in the catalog volume,
        # not a real git URL.  Return the catalog path directly.
        if (git_source or "").startswith("builtin://"):
            catalog_path = os.environ.get("MODULE_CATALOG_PATH", "/tmp/bnk-forge-modules")
            logger.info("Builtin module — using catalog path %s (not git clone)", catalog_path)
            return catalog_path

        blueprint_path = self.get_blueprint_workspace_path(project_id, stack_id)
        repo_path = os.path.join(blueprint_path, "repo")
        meta_path = os.path.join(blueprint_path, ".blueprint_meta.json")
        os.makedirs(blueprint_path, exist_ok=True)

        parsed_repo_url, parsed_ref, _ = self._parse_git_source(git_source)
        # parsed_ref defaults to "main" when ?ref= is absent in git_source, so we must
        # treat that default as "not explicitly set" and prefer the module_source ref.
        git_source_has_explicit_ref = bool(ref or ("?ref=" in (git_source or "")))
        if git_source_has_explicit_ref:
            target_ref = ref or parsed_ref
        else:
            # Fall back to module_source's git_ref / branch before the hardcoded "main" default
            module_source_ref = (
                (getattr(module_source, "git_ref", None) or getattr(module_source, "branch", None))
                if module_source is not None
                else None
            )
            target_ref = module_source_ref or parsed_ref

        existing_meta = None
        if os.path.exists(meta_path):
            try:
                with open(meta_path) as f:
                    existing_meta = json.load(f)
            except Exception:
                existing_meta = None

        repo_exists = os.path.isdir(repo_path) and bool(os.listdir(repo_path))
        repo_matches = (
            existing_meta
            and existing_meta.get("git_source") == parsed_repo_url
            and existing_meta.get("ref") == target_ref
        )

        if module_source is not None:
            auth_ctx = GitAuthService.resolve_for_module_source(module_source, db=self.db)
        else:
            auth_ctx = GitAuthService.resolve_for_module_library_token_setting(self.db)

        env, cleanup_env = GitAuthService.build_git_environment(auth_ctx)
        try:
            if repo_exists and repo_matches:
                # Repo already cloned for same source+ref — pull latest commits.
                # Branch refs (e.g. "main", "fix/foo") can have new commits pushed
                # after the initial clone; without pulling, workspaces get stale source.
                try:
                    subprocess.run(
                        ["git", "pull", "--ff-only", "origin", target_ref],
                        env=env,
                        cwd=repo_path,
                        check=True,
                        capture_output=True,
                        text=True,
                        timeout=120,
                    )
                    logger.info("Blueprint repo pulled latest for stack %s (ref=%s)", stack_id, target_ref)
                except subprocess.CalledProcessError as e:
                    # Pull failed (e.g. force-pushed branch, detached HEAD).
                    # Fall through to full re-clone below.
                    logger.warning(
                        "Blueprint repo pull failed for stack %s, will re-clone: %s",
                        stack_id,
                        (e.stderr or "").strip()[:200],
                    )
                    shutil.rmtree(repo_path, ignore_errors=True)
                    repo_exists = False
                else:
                    return repo_path

            if os.path.exists(repo_path):
                shutil.rmtree(repo_path, ignore_errors=True)

            subprocess.run(
                ["git", "clone", "--depth=1", "--branch", target_ref, "--", parsed_repo_url, repo_path],
                env=env,
                check=True,
                capture_output=True,
                text=True,
                timeout=300,
            )
        except subprocess.CalledProcessError as e:
            stderr = GitAuthService.sanitize_error_text(e.stderr or "", secrets=[auth_ctx.secret])
            error_kind, guidance = GitAuthService.classify_git_failure(stderr)
            raise RuntimeError(
                f"Blueprint git clone failed [{error_kind}] {guidance} (exit code {e.returncode}): {stderr.strip()}"
            ) from e
        finally:
            cleanup_env()

        with open(meta_path, "w") as f:
            json.dump(
                {
                    "stack_id": stack_id,
                    "project_id": project_id,
                    "created_at": datetime.now(UTC).isoformat(),
                    "git_source": parsed_repo_url,
                    "ref": target_ref,
                },
                f,
                indent=2,
                sort_keys=True,
            )

        return repo_path

    def is_blueprint_eligible(self, module: "ProjectModule") -> bool:
        """Return True when module can use shared blueprint clone/cache."""
        if not module.stack_instance_id:
            return False
        if not module.library_module:
            return False
        if module.library_module.module_source_kind != "git_catalog":
            return False
        return module.library_module.execution_engine in (None, "opentofu")

    def _provider_shape_hash(self, module: "ProjectModule") -> str:
        """Compute provider shape hash from module .tf sources."""
        workspace_path = module.workspace_path or self.get_workspace_path(module)
        tf_hasher = hashlib.sha256()
        if os.path.isdir(workspace_path):
            tf_files = sorted(f for f in os.listdir(workspace_path) if f.endswith(".tf"))
            for tf_file in tf_files:
                tf_hasher.update(tf_file.encode())
                with open(os.path.join(workspace_path, tf_file), "rb") as f:
                    tf_hasher.update(f.read())
        return tf_hasher.hexdigest()

    def compute_cache_key(self, module: "ProjectModule") -> str:
        """Compute deterministic cache key for blueprint init cache."""
        if not module.library_module:
            raise ValueError(f"Module {module.id} has no library module")

        _, git_ref, _ = self._parse_git_source(module.library_module.git_source or "")
        provider_shape_hash = self._provider_shape_hash(module)
        payload = (
            f"{module.library_module.id}:"
            f"{module.library_module.version or 'unknown'}:"
            f"{git_ref}:"
            f"{provider_shape_hash}"
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def attach_cached_init(self, module: "ProjectModule", stack_id: int) -> bool:
        """Attach cached providers/modules via symlink if available."""
        if not self.is_blueprint_eligible(module):
            return False

        workspace_path = module.workspace_path or self.get_workspace_path(module)
        cache_key = self.compute_cache_key(module)
        cache_path = self.get_blueprint_cache_path(module.project_id, stack_id, cache_key)
        providers_src = os.path.join(cache_path, ".terraform", "providers")
        modules_src = os.path.join(cache_path, ".terraform", "modules")
        lock_src = os.path.join(cache_path, ".terraform.lock.hcl")

        if not (os.path.exists(providers_src) and os.path.exists(modules_src) and os.path.exists(lock_src)):
            return False

        try:
            terraform_dir = os.path.join(workspace_path, ".terraform")
            os.makedirs(terraform_dir, exist_ok=True)

            providers_dst = os.path.join(terraform_dir, "providers")
            modules_dst = os.path.join(terraform_dir, "modules")

            for dst in (providers_dst, modules_dst):
                if os.path.islink(dst) or os.path.isfile(dst):
                    os.unlink(dst)
                elif os.path.isdir(dst):
                    shutil.rmtree(dst, ignore_errors=True)

            os.symlink(providers_src, providers_dst)
            os.symlink(modules_src, modules_dst)
            shutil.copy2(lock_src, os.path.join(workspace_path, ".terraform.lock.hcl"))
            logger.info("Attached blueprint init cache for module %s (key=%s)", module.id, cache_key[:12])
            return True
        except Exception as e:
            logger.warning("Failed to attach blueprint cache for module %s: %s", module.id, e)
            return False

    def publish_init_to_cache(self, module: "ProjectModule", stack_id: int) -> bool:
        """Publish initialized providers/modules into blueprint cache atomically."""
        if not self.is_blueprint_eligible(module):
            return False

        workspace_path = module.workspace_path or self.get_workspace_path(module)
        terraform_dir = os.path.join(workspace_path, ".terraform")
        providers_src = os.path.join(terraform_dir, "providers")
        modules_src = os.path.join(terraform_dir, "modules")
        lock_src = os.path.join(workspace_path, ".terraform.lock.hcl")

        if not (os.path.exists(providers_src) and os.path.exists(modules_src) and os.path.exists(lock_src)):
            logger.debug("Skipping blueprint cache publish for module %s: init artifacts missing", module.id)
            return False

        cache_key = self.compute_cache_key(module)
        cache_path = self.get_blueprint_cache_path(module.project_id, stack_id, cache_key)
        cache_parent = os.path.dirname(cache_path)
        os.makedirs(cache_parent, exist_ok=True)

        with self._blueprint_cache_lock(module.project_id, stack_id):
            if os.path.exists(cache_path):
                return True

            temp_dir = tempfile.mkdtemp(prefix=f".{cache_key}-", dir=cache_parent)
            try:
                temp_tf_dir = os.path.join(temp_dir, ".terraform")
                os.makedirs(temp_tf_dir, exist_ok=True)
                shutil.copytree(providers_src, os.path.join(temp_tf_dir, "providers"), symlinks=True)
                shutil.copytree(modules_src, os.path.join(temp_tf_dir, "modules"), symlinks=True)
                shutil.copy2(lock_src, os.path.join(temp_dir, ".terraform.lock.hcl"))
                os.replace(temp_dir, cache_path)
                logger.info("Published blueprint init cache for module %s (key=%s)", module.id, cache_key[:12])
                return True
            finally:
                if os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir, ignore_errors=True)

    def cleanup_blueprint_workspace(self, project_id: int, stack_id: int) -> bool:
        """Delete stack-level blueprint workspace and cache."""
        blueprint_path = self.get_blueprint_workspace_path(project_id, stack_id)
        if os.path.exists(blueprint_path):
            shutil.rmtree(blueprint_path, ignore_errors=True)
            logger.info("Cleaned up blueprint workspace: %s", blueprint_path)
            return True
        return False

    def ensure_workspace(self, module: "ProjectModule") -> str:
        """
        Ensure workspace directory exists for a module.

        Creates the directory if it doesn't exist and updates module.workspace_path.
        Does NOT clone source or run init - that's handled by the tasks.

        Args:
            module: ProjectModule instance

        Returns:
            Path to workspace directory
        """
        path = self.get_workspace_path(module)

        if not os.path.exists(path):
            os.makedirs(path, exist_ok=True)
            logger.info(f"Created workspace directory: {path}")

        # Update module with workspace path if not set
        if module.workspace_path != path:
            module.workspace_path = path
            self.db.commit()
            logger.info(f"Set workspace_path for module {module.id}: {path}")

        return path

    def is_initialized(self, module: "ProjectModule") -> bool:
        """
        Check if module workspace is initialized (tofu init has been run).

        Checks for:
        - .terraform/ directory exists
        - .terraform.lock.hcl exists (created by init)

        Args:
            module: ProjectModule instance

        Returns:
            True if workspace is initialized
        """
        if not module.workspace_path:
            return False

        if not os.path.exists(module.workspace_path):
            return False

        terraform_dir = os.path.join(module.workspace_path, ".terraform")
        lock_file = os.path.join(module.workspace_path, ".terraform.lock.hcl")

        initialized = os.path.exists(terraform_dir) and os.path.exists(lock_file)

        if initialized:
            logger.debug(f"Module {module.id} workspace is initialized")
        else:
            logger.debug(f"Module {module.id} workspace not initialized (terraform_dir={os.path.exists(terraform_dir)}, lock_file={os.path.exists(lock_file)})")

        return initialized

    def needs_reinit(self, module: "ProjectModule") -> tuple[bool, str]:
        """
        Check if module needs re-initialization.

        Reasons for reinit:
        - Module source version changed (library module version vs workspace version)
        - .terraform/lock.hcl is missing or corrupted
        - Backend config changed (project backend settings)

        Args:
            module: ProjectModule instance

        Returns:
            Tuple of (needs_reinit, reason)
        """
        if not self.is_initialized(module):
            return True, "Module workspace not initialized"

        if not module.workspace_path or not os.path.exists(module.workspace_path):
            return True, "Workspace directory missing"

        # Check if lock file exists and is valid
        lock_file = os.path.join(module.workspace_path, ".terraform.lock.hcl")
        if not os.path.exists(lock_file):
            return True, "Provider lock file missing"

        # Check if downloaded modules cache was cleared (e.g. by re-clone after destroy)
        terraform_modules_dir = os.path.join(module.workspace_path, ".terraform", "modules")
        if not os.path.exists(terraform_modules_dir):
            return True, "Downloaded modules cache missing (source was re-cloned)"

        # Check if module source version changed
        # Compare library module version with stored init version
        init_version_file = os.path.join(module.workspace_path, ".bnk_init_version")
        if module.library_module and module.library_module.version:
            current_version = module.library_module.version

            if os.path.exists(init_version_file):
                try:
                    with open(init_version_file) as f:
                        stored_version = f.read().strip()
                    if stored_version != current_version:
                        logger.info(f"Module {module.id} source version changed: {stored_version} -> {current_version}")
                        return True, f"Module source version changed from {stored_version} to {current_version}"
                except Exception as e:
                    logger.warning(f"Error reading init version file: {e}")

        # S15-012: Check if backend type/config changed
        # This catches switches between local/s3/etc that require re-init
        backend_hash_file = os.path.join(module.workspace_path, ".bnk_backend_hash")
        if os.path.exists(backend_hash_file):
            try:
                with open(backend_hash_file) as f:
                    stored_backend_hash = f.read().strip()
                current_backend_hash = self._compute_backend_hash(module)
                if stored_backend_hash != current_backend_hash:
                    logger.info(f"Module {module.id} backend config changed")
                    return True, "Backend configuration changed"
            except Exception as e:
                logger.warning(f"Error checking backend hash: {e}")

        # All checks passed
        return False, ""

    def _compute_backend_hash(self, module: "ProjectModule") -> str:
        """
        Compute hash of backend configuration for change detection.

        Args:
            module: ProjectModule instance

        Returns:
            SHA256 hash of backend config
        """
        project = module.project
        backend_config = {
            "backend_type": getattr(project, 'backend_type', 'local'),
            "backend_bucket": getattr(project, 'backend_bucket', None),
            "backend_region": getattr(project, 'backend_region', None),
            "backend_key_prefix": getattr(project, 'backend_key_prefix', None),
        }
        config_json = json.dumps(backend_config, sort_keys=True, default=str)
        return hashlib.sha256(config_json.encode()).hexdigest()

    def save_backend_hash(self, module: "ProjectModule") -> None:
        """
        Save backend config hash after successful init.

        Args:
            module: ProjectModule instance
        """
        if not module.workspace_path:
            return

        backend_hash = self._compute_backend_hash(module)
        hash_file = os.path.join(module.workspace_path, ".bnk_backend_hash")
        try:
            with open(hash_file, 'w') as f:
                f.write(backend_hash)
            logger.debug(f"Saved backend hash for module {module.id}: {backend_hash[:8]}...")
        except Exception as e:
            logger.warning(f"Failed to save backend hash: {e}")

    def save_init_version(self, module: "ProjectModule") -> None:
        """
        Save the current library module version after successful init.

        Called after tofu init succeeds to track which version was initialized.
        Also saves backend hash for change detection (S15-012).

        Args:
            module: ProjectModule instance
        """
        if not module.workspace_path:
            return

        version = ""
        if module.library_module:
            version = module.library_module.version or "unknown"

        init_version_file = os.path.join(module.workspace_path, ".bnk_init_version")
        try:
            with open(init_version_file, 'w') as f:
                f.write(version)
            logger.debug(f"Saved init version for module {module.id}: {version}")
        except Exception as e:
            logger.warning(f"Failed to save init version: {e}")

        # S15-012: Also save backend hash for detecting backend config changes
        self.save_backend_hash(module)

    def has_saved_plan(self, module: "ProjectModule") -> bool:
        """
        Check if module has a saved plan file.

        Args:
            module: ProjectModule instance

        Returns:
            True if plan.out exists in workspace
        """
        if not module.workspace_path:
            return False

        plan_path = os.path.join(module.workspace_path, "plan.out")
        return os.path.exists(plan_path)

    def get_plan_path(self, module: "ProjectModule") -> str | None:
        """
        Get the path to the saved plan file.

        Args:
            module: ProjectModule instance

        Returns:
            Path to plan.out if exists, None otherwise
        """
        if not module.workspace_path:
            return None

        plan_path = os.path.join(module.workspace_path, "plan.out")
        if os.path.exists(plan_path):
            return plan_path
        return None

    def plan_is_valid(self, module: "ProjectModule") -> tuple[bool, str]:
        """
        Check if saved plan is still valid for apply.

        A plan is invalid if:
        - No saved plan exists
        - Variables have changed since plan was created
        - Backend/provider config has changed since plan was created
        - Plan is too old (future: configurable max age)

        S15-020: Also checks config hashes beyond just vars_hash.

        Args:
            module: ProjectModule instance

        Returns:
            Tuple of (is_valid, reason_if_invalid)
        """
        if not self.has_saved_plan(module):
            return False, "No saved plan exists. Run Plan first."

        if self.vars_changed(module):
            return False, "Variables have changed since plan was created. Re-run Plan."

        # S15-020: Check if backend config changed since plan
        if module.workspace_path:
            backend_hash_file = os.path.join(module.workspace_path, ".bnk_backend_hash")
            if os.path.exists(backend_hash_file):
                try:
                    with open(backend_hash_file) as f:
                        stored_hash = f.read().strip()
                    current_hash = self._compute_backend_hash(module)
                    if current_hash and stored_hash != current_hash:
                        return False, "Backend configuration has changed since plan was created. Re-run Plan."
                except Exception as e:
                    logger.warning(f"Error checking backend hash: {e}")

        # S15-020: Check if module source version changed since plan
        if self.needs_reinit(module)[0]:
            return False, "Module source has changed, re-initialization required. Run Init then Plan."

        # Future: Add plan age check
        # plan_age = datetime.now(timezone.utc) - module.last_plan_at
        # if plan_age > timedelta(hours=24):
        #     return False, "Plan is more than 24 hours old"

        return True, ""

    def vars_changed(self, module: "ProjectModule") -> bool:
        """
        Check if variables have changed since last plan.

        Compares current vars_hash with stored vars_hash.

        Args:
            module: ProjectModule instance

        Returns:
            True if variables have changed
        """
        if not module.vars_hash:
            # No previous hash stored - can't determine if changed
            # Treat as not changed (allow operations to proceed)
            return False

        current_hash = self.compute_vars_hash(module)
        changed = current_hash != module.vars_hash

        if changed:
            logger.info(f"Module {module.id} variables changed (stored={module.vars_hash[:8]}..., current={current_hash[:8]}...)")

        return changed

    def compute_vars_hash(self, module: "ProjectModule", variables: dict | None = None) -> str:
        """
        Compute SHA256 hash of module variables including secret metadata.

        If variables not provided, builds them using variable_assembler.

        S15-019: Also includes a hash of project secret metadata so that
        rotating a secret invalidates existing plans.

        Args:
            module: ProjectModule instance
            variables: Optional pre-built variables dict

        Returns:
            SHA256 hex digest of variables JSON + secret metadata
        """
        if variables is None:
            # Build variables using shared variable_assembler
            from services.execution.variable_assembler import build_variables
            variables = build_variables(self.db, module)

        # Serialize to JSON with sorted keys for deterministic hash
        vars_json = json.dumps(variables, sort_keys=True, default=str)

        # S15-019: Include secret metadata in hash so secret changes invalidate plans
        secret_hash_input = ""
        try:
            from services.secrets_service import SecretsService
            secrets_service = SecretsService(self.db)
            secrets = secrets_service.list_secrets(module.project_id)
            if secrets:
                # Hash secret names + encrypted values (not decrypted - just detect changes)
                secret_data = [(s.name, s.encrypted_value, s.updated_at.isoformat() if s.updated_at else "") for s in secrets]
                secret_hash_input = json.dumps(secret_data, sort_keys=True, default=str)
        except Exception as e:
            logger.warning(f"Could not include secrets in vars_hash: {e}")

        combined = vars_json + secret_hash_input
        return hashlib.sha256(combined.encode()).hexdigest()

    def update_vars_hash(self, module: "ProjectModule", variables: dict | None = None) -> str:
        """
        Compute and store vars_hash for module.

        Call this after plan to record what variables were used.

        Args:
            module: ProjectModule instance
            variables: Optional pre-built variables dict

        Returns:
            The computed hash
        """
        hash_value = self.compute_vars_hash(module, variables)
        module.vars_hash = hash_value
        self.db.commit()
        logger.info(f"Updated vars_hash for module {module.id}: {hash_value[:8]}...")
        return hash_value

    def save_plan_output(self, module: "ProjectModule", plan_output: str) -> None:
        """
        Save plan output summary to database.

        Args:
            module: ProjectModule instance
            plan_output: Human-readable plan output
        """
        module.plan_output = plan_output
        module.plan_serial = (module.plan_serial or 0) + 1
        self.db.commit()
        logger.info(f"Saved plan output for module {module.id}, serial={module.plan_serial}")

    def clear_plan(self, module: "ProjectModule") -> None:
        """
        Clear saved plan after apply or on invalidation.

        Removes plan.out file and clears DB fields.

        Args:
            module: ProjectModule instance
        """
        # Remove plan.out file
        if module.workspace_path:
            plan_path = os.path.join(module.workspace_path, "plan.out")
            if os.path.exists(plan_path):
                os.remove(plan_path)
                logger.info(f"Deleted plan file: {plan_path}")

        # Clear DB fields
        module.plan_output = None
        # Note: Don't clear vars_hash or plan_serial - useful for history
        self.db.commit()
        logger.info(f"Cleared plan for module {module.id}")

    def mark_initialized(self, module: "ProjectModule") -> None:
        """
        Mark module as initialized and record timestamp.

        Args:
            module: ProjectModule instance
        """
        module.last_init_at = datetime.now(UTC)
        self.db.commit()
        logger.info(f"Marked module {module.id} as initialized at {module.last_init_at}")

    def cleanup_module_workspace(self, module: "ProjectModule") -> bool:
        """
        Delete workspace for a module (called on module deletion).

        Args:
            module: ProjectModule instance

        Returns:
            True if workspace was deleted
        """
        workspace_path = module.workspace_path or self.get_workspace_path(module)

        if os.path.exists(workspace_path):
            shutil.rmtree(workspace_path, ignore_errors=True)
            logger.info(f"Cleaned up workspace: {workspace_path}")
            cleanup_durable_infra_private_key(module.project_id, module.id)
            return True

        cleanup_durable_infra_private_key(module.project_id, module.id)
        return False

    def cleanup_project_workspaces(self, project_id: int) -> int:
        """
        Delete all workspaces for a project (called on project deletion).

        Args:
            project_id: Project ID

        Returns:
            Number of workspaces deleted
        """
        project_path = os.path.join(self.BASE_PATH, str(project_id))

        if os.path.exists(project_path):
            # Count subdirectories (module workspaces)
            count = 0
            try:
                for item in os.listdir(project_path):
                    if os.path.isdir(os.path.join(project_path, item)):
                        count += 1
            except Exception:
                count = 0

            shutil.rmtree(project_path, ignore_errors=True)
            logger.info(f"Cleaned up {count} workspaces for project {project_id}")
            return count

        return 0

    def get_orphaned_workspaces(self) -> list:
        """
        Find workspaces that don't correspond to existing modules.

        Returns:
            List of orphaned workspace paths
        """
        from models import Project, ProjectModule

        orphaned = []

        if not os.path.exists(self.BASE_PATH):
            return orphaned

        # Get all project IDs that exist in DB
        existing_projects = {p.id for p in self.db.query(Project.id).all()}

        for project_dir in os.listdir(self.BASE_PATH):
            project_path = os.path.join(self.BASE_PATH, project_dir)
            if not os.path.isdir(project_path):
                continue

            try:
                project_id = int(project_dir)
            except ValueError:
                # Non-numeric directory name
                orphaned.append(project_path)
                continue

            # Check if project exists
            if project_id not in existing_projects:
                orphaned.append(project_path)
                continue

            # Check each module workspace
            existing_modules = {
                m.id for m in self.db.query(ProjectModule.id).filter(
                    ProjectModule.project_id == project_id
                ).all()
            }

            for module_dir in os.listdir(project_path):
                module_path = os.path.join(project_path, module_dir)
                if not os.path.isdir(module_path):
                    continue

                # Stack-level shared blueprint workspace is not a module workspace.
                if module_dir.startswith("blueprint-"):
                    continue

                try:
                    module_id = int(module_dir)
                except ValueError:
                    orphaned.append(module_path)
                    continue

                if module_id not in existing_modules:
                    orphaned.append(module_path)

        return orphaned

    def cleanup_orphaned_workspaces(self) -> int:
        """
        Delete all orphaned workspaces.

        Returns:
            Number of orphaned workspaces deleted
        """
        orphaned = self.get_orphaned_workspaces()

        for path in orphaned:
            shutil.rmtree(path, ignore_errors=True)
            logger.info(f"Deleted orphaned workspace: {path}")

        return len(orphaned)

    def get_workspace_stats(self) -> dict:
        """
        Get statistics about workspaces.

        Returns:
            Dict with workspace statistics
        """

        stats = {
            "total_workspaces": 0,
            "initialized_workspaces": 0,
            "workspaces_with_plans": 0,
            "orphaned_workspaces": 0,
            "total_size_mb": 0,
        }

        if not os.path.exists(self.BASE_PATH):
            return stats

        # Count and measure workspaces
        total_size = 0
        for project_dir in os.listdir(self.BASE_PATH):
            project_path = os.path.join(self.BASE_PATH, project_dir)
            if not os.path.isdir(project_path):
                continue

            for module_dir in os.listdir(project_path):
                module_path = os.path.join(project_path, module_dir)
                if not os.path.isdir(module_path):
                    continue

                stats["total_workspaces"] += 1

                # Check if initialized
                if os.path.exists(os.path.join(module_path, ".terraform")):
                    stats["initialized_workspaces"] += 1

                # Check if has plan
                if os.path.exists(os.path.join(module_path, "plan.out")):
                    stats["workspaces_with_plans"] += 1

                # Calculate size
                for dirpath, dirnames, filenames in os.walk(module_path):
                    for f in filenames:
                        fp = os.path.join(dirpath, f)
                        try:
                            total_size += os.path.getsize(fp)
                        except OSError:
                            pass

        stats["total_size_mb"] = round(total_size / (1024 * 1024), 2)
        stats["orphaned_workspaces"] = len(self.get_orphaned_workspaces())

        return stats
