"""
State Decryption Service
Handles decryption of OpenTofu encrypted state files using the tofu CLI.

OpenTofu 1.8+ supports native state encryption. When enabled, the state file
contains encrypted_data instead of plaintext resources/outputs.

This service provides utilities to:
1. Detect if a state file is encrypted
2. Decrypt state using tofu CLI with proper encryption config
3. Cache decrypted state for performance (PERF-011)
"""

import asyncio
import json
import logging
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from core.cache import cache

logger = logging.getLogger(__name__)

# Cache key prefix for decrypted state (PERF-011)
STATE_CACHE_PREFIX = "state:decrypted:"
# Cache key prefix for resource lists (CP-013)
RESOURCES_CACHE_PREFIX = "state:resources:"
STATE_CACHE_TTL = 300  # 5 minutes

# Thread pool for async decryption (PERF-012)
_executor = ThreadPoolExecutor(max_workers=4)

# Persistent decryption workspace base path (CP-012)
DECRYPT_WORKSPACE_BASE = "/app/state"


def invalidate_state_cache(module_id: int, project_id: int = None) -> int:
    """
    Invalidate all state-related caches for a module.

    Call this after apply/destroy operations that change state.
    Centralizes cache invalidation to keep code DRY (PERF-017).

    Invalidates:
    - Decrypted state cache for the module
    - Project modules state endpoint cache (if project_id provided)

    Args:
        module_id: Module ID to invalidate cache for
        project_id: Project ID to invalidate project-level state cache

    Returns:
        Number of cache entries deleted
    """
    count = 0

    # Invalidate module-level decrypted state cache
    pattern = f"{STATE_CACHE_PREFIX}{module_id}:*"
    count += cache.delete_pattern(pattern)

    # CP-013: Invalidate module-level resource list cache
    resources_pattern = f"{RESOURCES_CACHE_PREFIX}{module_id}:*"
    count += cache.delete_pattern(resources_pattern)

    # Invalidate project-level state viewer cache (PERF-017)
    if project_id:
        project_pattern = f"state:project:{project_id}:*"
        count += cache.delete_pattern(project_pattern)

    if count > 0:
        logger.info(f"Invalidated {count} state cache entries for module {module_id}")
    return count


def is_state_encrypted(state_path: str) -> bool:
    """
    Check if a state file is encrypted.

    Args:
        state_path: Path to the terraform.tfstate file

    Returns:
        True if the state is encrypted, False otherwise
    """
    try:
        with open(state_path) as f:
            state_data = json.load(f)
            return "encrypted_data" in state_data
    except Exception as e:
        logger.error(f"Error checking if state is encrypted: {e}")
        return False


def get_state_metadata(state_path: str) -> dict[str, Any]:
    """
    Get metadata from state file (works for both encrypted and unencrypted).

    Metadata fields (lineage, serial, meta) are NOT encrypted even when
    state encryption is enabled.

    Args:
        state_path: Path to the terraform.tfstate file

    Returns:
        Dictionary with state metadata
    """
    try:
        with open(state_path) as f:
            state_data = json.load(f)

        is_encrypted = "encrypted_data" in state_data

        metadata = {
            "lineage": state_data.get("lineage", "unknown"),
            "serial": state_data.get("serial", 0),
            "is_encrypted": is_encrypted,
        }

        if is_encrypted:
            metadata["encryption_version"] = state_data.get("encryption_version", "unknown")
            # terraform_version is inside encrypted_data
            metadata["terraform_version"] = "encrypted"
        else:
            metadata["terraform_version"] = state_data.get("terraform_version", "unknown")

        # File metadata
        stat = Path(state_path).stat()
        metadata["file_size"] = stat.st_size
        metadata["last_modified"] = stat.st_mtime

        return metadata

    except Exception as e:
        logger.error(f"Error reading state metadata: {e}")
        return {"error": str(e)}


def decrypt_state_with_tofu(
    state_path: str,
    project_id: int,
    module_id: int,
    db_session,
    timeout: int = 30
) -> tuple[bool, dict[str, Any]]:
    """
    Decrypt an encrypted state file using the tofu CLI.

    PERF-011: Results are cached in Redis for 5 minutes using module_id:serial as key.

    This function:
    1. Checks cache first (instant return if hit)
    2. Creates a temporary workspace
    3. Copies the state file
    4. Writes the encryption config
    5. Runs 'tofu state pull' to get decrypted state
    6. Caches the result
    7. Cleans up

    Args:
        state_path: Path to the encrypted terraform.tfstate
        project_id: Project ID (for encryption config)
        module_id: Module ID
        db_session: SQLAlchemy database session
        timeout: Command timeout in seconds

    Returns:
        Tuple of (success, decrypted_state_dict or error_dict)
    """
    from models import ProjectModule
    from services.credentials_service import get_cloud_credentials_env
    from services.execution.config_writer import write_encryption_config

    # PERF-011: Get serial from state for cache key
    try:
        metadata = get_state_metadata(state_path)
        serial = metadata.get("serial", 0)
        cache_key = f"{STATE_CACHE_PREFIX}{module_id}:{serial}"

        # Check cache first
        cached = cache.get(cache_key)
        if cached:
            logger.debug(f"State cache hit for module {module_id} serial {serial}")
            return True, cached
    except Exception as e:
        logger.warning(f"Cache check failed, proceeding with decryption: {e}")
        cache_key = None

    # CP-012: Use persistent decryption workspace instead of temp dirs
    # This keeps .terraform/ between calls so tofu init only runs once
    work_dir = os.path.join(DECRYPT_WORKSPACE_BASE, str(project_id), str(module_id), ".decrypt-workspace")
    os.makedirs(work_dir, exist_ok=True)

    try:
        # Get module and project
        module = db_session.query(ProjectModule).filter(ProjectModule.id == module_id).first()
        if not module:
            return False, {"error": f"Module {module_id} not found"}

        project = module.project
        if not project:
            return False, {"error": f"Project not found for module {module_id}"}

        # Copy state file to workspace (always update to latest)
        shutil.copy(state_path, os.path.join(work_dir, "terraform.tfstate"))

        # Write encryption config using extracted config_writer
        write_encryption_config(work_dir, module, db=db_session)

        # Write minimal backend config (local state)
        backend_content = '''terraform {
  backend "local" {
    path = "terraform.tfstate"
  }
}
'''
        with open(os.path.join(work_dir, "backend.tf"), 'w') as f:
            f.write(backend_content)

        # Get cloud credentials for potential provider init
        env = os.environ.copy()
        env.update(get_cloud_credentials_env(project, db_session))

        # CP-012: Skip tofu init if .terraform/ already exists (persistent workspace)
        terraform_dir = os.path.join(work_dir, ".terraform")
        needs_init = not os.path.isdir(terraform_dir)

        if needs_init:
            logger.info(f"Initializing decrypt workspace for module {module_id} (first use)")
            init_result = subprocess.run(
                ["tofu", "init", "-reconfigure"],
                cwd=work_dir,
                capture_output=True,
                text=True,
                env=env,
                timeout=timeout
            )

            if init_result.returncode != 0:
                logger.error(f"tofu init failed: {init_result.stderr}")
                return False, {"error": f"Init failed: {init_result.stderr}"}

        # Run 'tofu state pull' to get decrypted state JSON
        result = subprocess.run(
            ["tofu", "state", "pull"],
            cwd=work_dir,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout
        )

        if result.returncode != 0:
            # If state pull fails, it may be due to stale .terraform — retry with fresh init
            if not needs_init:
                logger.warning(f"State pull failed with cached .terraform, retrying with fresh init for module {module_id}")
                init_result = subprocess.run(
                    ["tofu", "init", "-reconfigure"],
                    cwd=work_dir,
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=timeout
                )
                if init_result.returncode == 0:
                    result = subprocess.run(
                        ["tofu", "state", "pull"],
                        cwd=work_dir,
                        capture_output=True,
                        text=True,
                        env=env,
                        timeout=timeout
                    )

            if result.returncode != 0:
                logger.error(f"tofu state pull failed: {result.stderr}")
                return False, {"error": f"Failed to decrypt state: {result.stderr}"}

        # Parse the decrypted state
        try:
            decrypted_state = json.loads(result.stdout)

            # PERF-011: Cache successful decryption
            if cache_key:
                cache.set(cache_key, decrypted_state, ttl_seconds=STATE_CACHE_TTL)
                logger.debug(f"Cached decrypted state for module {module_id}")

            return True, decrypted_state
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse decrypted state JSON: {e}")
            return False, {"error": f"Invalid JSON in decrypted state: {e}"}

    except subprocess.TimeoutExpired:
        return False, {"error": "Timeout decrypting state"}
    except Exception as e:
        logger.error(f"Error decrypting state: {e}")
        return False, {"error": str(e)}


def get_state_resources_list(
    state_path: str,
    project_id: int,
    module_id: int,
    db_session,
    timeout: int = 30
) -> tuple[bool, list]:
    """
    Get list of resources from state (handles encrypted state).

    For encrypted state, uses 'tofu state list' command.
    For unencrypted state, parses directly.

    Args:
        state_path: Path to terraform.tfstate
        project_id: Project ID
        module_id: Module ID
        db_session: SQLAlchemy database session
        timeout: Command timeout in seconds

    Returns:
        Tuple of (success, list of resource addresses)
    """
    from models import ProjectModule
    from services.credentials_service import get_cloud_credentials_env
    from services.execution.config_writer import write_encryption_config

    # Check if encrypted
    if not is_state_encrypted(state_path):
        # Unencrypted - parse directly
        try:
            with open(state_path) as f:
                state_data = json.load(f)
            resources = state_data.get("resources", [])
            resource_list = [
                f"{r.get('type')}.{r.get('name')}"
                for r in resources
            ]
            return True, resource_list
        except Exception as e:
            return False, [f"Error: {e}"]

    # CP-013: Check Redis cache first for encrypted resource lists
    try:
        metadata = get_state_metadata(state_path)
        serial = metadata.get("serial", 0)
        resources_cache_key = f"{RESOURCES_CACHE_PREFIX}{module_id}:{serial}"

        cached_resources = cache.get(resources_cache_key)
        if cached_resources is not None:
            logger.debug(f"Resources cache hit for module {module_id} serial {serial}")
            return True, cached_resources
    except Exception as e:
        logger.warning(f"Resources cache check failed, proceeding: {e}")
        resources_cache_key = None

    # CP-012: Use persistent decryption workspace instead of temp dirs
    work_dir = os.path.join(DECRYPT_WORKSPACE_BASE, str(project_id), str(module_id), ".decrypt-workspace")
    os.makedirs(work_dir, exist_ok=True)

    try:
        module = db_session.query(ProjectModule).filter(ProjectModule.id == module_id).first()
        if not module:
            return False, [f"Module {module_id} not found"]

        # Copy state file (always update to latest)
        shutil.copy(state_path, os.path.join(work_dir, "terraform.tfstate"))

        # Write encryption config using extracted config_writer
        write_encryption_config(work_dir, module, db=db_session)

        # Write minimal backend config
        backend_content = '''terraform {
  backend "local" {
    path = "terraform.tfstate"
  }
}
'''
        with open(os.path.join(work_dir, "backend.tf"), 'w') as f:
            f.write(backend_content)

        # Get credentials
        env = os.environ.copy()
        env.update(get_cloud_credentials_env(module.project, db_session))

        # CP-012: Skip tofu init if .terraform/ already exists
        terraform_dir = os.path.join(work_dir, ".terraform")
        needs_init = not os.path.isdir(terraform_dir)

        if needs_init:
            logger.info(f"Initializing decrypt workspace for module {module_id} resource list (first use)")
            init_result = subprocess.run(
                ["tofu", "init", "-reconfigure"],
                cwd=work_dir,
                capture_output=True,
                text=True,
                env=env,
                timeout=timeout
            )

            if init_result.returncode != 0:
                logger.error(f"tofu init failed: {init_result.stderr}")
                return False, [f"Init failed: {init_result.stderr}"]

        # Run tofu state list
        result = subprocess.run(
            ["tofu", "state", "list"],
            cwd=work_dir,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout
        )

        if result.returncode != 0:
            # If state list fails, retry with fresh init (stale .terraform)
            if not needs_init:
                logger.warning(f"State list failed with cached .terraform, retrying with fresh init for module {module_id}")
                init_result = subprocess.run(
                    ["tofu", "init", "-reconfigure"],
                    cwd=work_dir,
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=timeout
                )
                if init_result.returncode == 0:
                    result = subprocess.run(
                        ["tofu", "state", "list"],
                        cwd=work_dir,
                        capture_output=True,
                        text=True,
                        env=env,
                        timeout=timeout
                    )

            if result.returncode != 0:
                logger.error(f"tofu state list failed: {result.stderr}")
                return False, [f"Error: {result.stderr}"]

        # Parse resources from output
        resources = [r.strip() for r in result.stdout.strip().split('\n') if r.strip()]

        # CP-013: Cache successful resource list
        if resources_cache_key:
            cache.set(resources_cache_key, resources, ttl_seconds=STATE_CACHE_TTL)
            logger.debug(f"Cached resource list for module {module_id} ({len(resources)} resources)")

        return True, resources

    except subprocess.TimeoutExpired:
        return False, ["Timeout listing resources"]
    except Exception as e:
        logger.error(f"Error listing state resources: {e}")
        return False, [f"Error: {e}"]


def get_decrypted_state_or_fallback(
    state_path: str,
    module_id: int,
    db_session
) -> dict[str, Any]:
    """
    Get state data, decrypting if necessary. Falls back to metadata if decryption fails.

    This is a convenience function for use in API endpoints that need to display
    state information but can gracefully degrade if decryption isn't possible.

    Args:
        state_path: Path to terraform.tfstate
        module_id: Module ID
        db_session: SQLAlchemy database session

    Returns:
        Dictionary with state data (full if decryption succeeds, metadata-only if fails)
    """
    from models import ProjectModule

    module = db_session.query(ProjectModule).filter(ProjectModule.id == module_id).first()
    if not module:
        return {"error": f"Module {module_id} not found"}

    # Get metadata first (always works)
    metadata = get_state_metadata(state_path)

    if not metadata.get("is_encrypted", False):
        # Unencrypted - parse directly
        try:
            with open(state_path) as f:
                return json.load(f)
        except Exception as e:
            return {"error": str(e), "metadata": metadata}

    # Encrypted - try to decrypt
    success, result = decrypt_state_with_tofu(
        state_path,
        module.project_id,
        module_id,
        db_session
    )

    if success:
        return result
    else:
        # Return metadata with error info
        return {
            "error": result.get("error", "Failed to decrypt state"),
            "is_encrypted": True,
            "metadata": metadata,
            # Include outputs from database as fallback
            "outputs_from_db": module.outputs or {}
        }


# ============================================================
# Async Wrappers (PERF-012)
# ============================================================

async def decrypt_state_async(
    state_path: str,
    project_id: int,
    module_id: int,
    db_session,
    timeout: int = 30
) -> tuple[bool, dict[str, Any]]:
    """
    Async wrapper for decrypt_state_with_tofu.

    PERF-012: Runs the blocking subprocess calls in a ThreadPoolExecutor
    to avoid blocking the FastAPI event loop.

    Args:
        Same as decrypt_state_with_tofu

    Returns:
        Same as decrypt_state_with_tofu
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        _executor,
        lambda: decrypt_state_with_tofu(state_path, project_id, module_id, db_session, timeout)
    )


async def get_state_resources_list_async(
    state_path: str,
    project_id: int,
    module_id: int,
    db_session,
    timeout: int = 30
) -> tuple[bool, list]:
    """
    Async wrapper for get_state_resources_list.

    PERF-012: Runs the blocking subprocess calls in a ThreadPoolExecutor.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        _executor,
        lambda: get_state_resources_list(state_path, project_id, module_id, db_session, timeout)
    )
