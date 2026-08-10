"""
Module Catalog Service
Syncs official BNK-Forge module library and manages user-added modules
"""
import copy
import json
import logging
import os
from datetime import UTC, datetime

from sqlalchemy import or_
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from models import ApplicationSetting, ModuleLibrary, ModuleSource
from services.git_auth_service import GitAuthService

# Module catalog cache directory
MODULE_CATALOG_PATH = os.getenv("MODULE_CATALOG_PATH", "/tmp/bnk-forge-modules")

# Skip re-sync when the last successful sync completed within this many seconds.
# Applies only when force=False (boot step). Manual /sync always forces.
CATALOG_SYNC_TTL_SECONDS = 300

# Overall wall-clock cap on any single git network op (clone/fetch/pull), enforced
# via GitPython's kill_after_timeout. GIT_HTTP_LOW_SPEED_* only aborts *stalled*
# transfers (<1 KB/s for 30s); this bounds the modes it misses — connect/TLS hangs
# and slow-but-progressing clones — so the boot sync can't gate readiness forever
# and trip a k8s startup-probe restart loop.
CATALOG_GIT_TIMEOUT_SECONDS = 300

logger = logging.getLogger(__name__)


def _build_official_source_name(repo_name: str) -> str:
    """Build deterministic canonical source name for official module repo."""
    return f"official-{repo_name}"


def _ensure_official_module_source(db: Session, *, clean_url: str, repo_name: str, repo_ref: str) -> ModuleSource:
    """Create/reuse canonical ModuleSource row for official module catalog repo."""
    existing = db.query(ModuleSource).filter(
        ModuleSource.source_type == "git",
        ModuleSource.url == clean_url,
        ModuleSource.is_active,
    ).first()
    if existing:
        existing.branch = repo_ref
        existing.git_ref = repo_ref
        existing.sync_error = None
        existing.sync_status = "success"
        db.flush()
        return existing

    base_name = _build_official_source_name(repo_name)
    candidate_name = base_name
    suffix = 2
    while db.query(ModuleSource).filter(ModuleSource.name == candidate_name).first() is not None:
        candidate_name = f"{base_name}-{suffix}"
        suffix += 1

    source = ModuleSource(
        name=candidate_name,
        source_type="git",
        url=clean_url,
        branch=repo_ref,
        git_ref=repo_ref,
        auth_type="none",
        credential_type="none",
        sync_status="success",
        sync_error=None,
        is_active=True,
        auto_sync=False,
        description="Canonical official module catalog source",
    )
    db.add(source)
    db.flush()
    return source


# execution_engine values that identify non-catalog engine modules (k8s builtins,
# python SSH modules, cli-bnkctl modules).  These are seeded by dedicated seeders
# and must NOT be re-parented onto the git catalog ModuleSource during backfill —
# their git_source / module_source_id / module_source_kind are authoritative from
# their own seeder, not from the catalog repo.
_NON_CATALOG_ENGINES = frozenset({"kubernetes-direct", "ssh", "cli-bnkctl"})


def _backfill_official_source_association(db: Session, *, clean_url: str, repo_ref: str, source_id: int) -> int:
    """Backfill official rows to canonical source linkage and normalized source metadata.

    The `builtin://` branch migrates legacy catalog rows that pre-date the
    ModuleSource link mechanism (they had git_source="builtin://<category>/<name>"
    as a placeholder).  Non-catalog engine modules (k8s builtins, python SSH,
    cli-bnkctl) also carry builtin:// git_source values but must not be touched —
    they are excluded by checking execution_engine in _NON_CATALOG_ENGINES.
    """
    matching_rows = db.query(ModuleLibrary).filter(
        ModuleLibrary.is_official,
        ModuleLibrary.is_active,
        or_(
            ModuleLibrary.git_source.like(f"git::{clean_url}//%"),
            ModuleLibrary.git_source.like("builtin://%"),
        ),
    ).all()

    repaired = 0
    for module in matching_rows:
        # Skip non-catalog engine modules — their git_source and source association
        # are owned by their respective seeders, not the git catalog.
        if module.execution_engine in _NON_CATALOG_ENGINES:
            continue
        changed = False
        if module.module_source_id != source_id:
            module.module_source_id = source_id
            changed = True
        if not module.source_path and module.path:
            module.source_path = module.path
            changed = True
        if not module.source_version:
            module.source_version = repo_ref
            changed = True
        if module.git_source and module.git_source.startswith("builtin://") and module.path:
            module.git_source = f"git::{clean_url}//{module.path}?ref={repo_ref}"
            changed = True
        if module.module_source_kind in {None, "", "catalog", "builtin"}:
            module.module_source_kind = "git_catalog"
            changed = True
        if changed:
            repaired += 1

    return repaired


def get_module_repo_config(db: Session) -> dict[str, str]:
    """
    Get module repository configuration from database settings

    Settings are seeded from .env on startup (see main.py lifespan handler).
    Priority: Database settings > defaults

    Args:
        db: Database session

    Returns:
        Dict with 'url' and 'ref' keys
    """
    # Get settings from database (seeded from .env on startup)
    git_url_setting = db.query(ApplicationSetting).filter(
        ApplicationSetting.key == "module_library.git_url"
    ).first()

    git_ref_setting = db.query(ApplicationSetting).filter(
        ApplicationSetting.key == "module_library.git_ref"
    ).first()

    # Get values from database (must be seeded from .env first)
    if not git_url_setting or not git_url_setting.value:
        raise ValueError("Module library git_url not configured. Set MODULE_LIBRARY_GIT_URL in .env file and restart.")

    git_url = GitAuthService.strip_url_credentials(git_url_setting.value)
    git_ref = git_ref_setting.value if git_ref_setting else "main"

    return {
        "url": git_url,
        "ref": git_ref
    }


def sync_module_catalog(db: Session, force: bool = False):
    """
    Sync official BNK-Forge module catalog from git repository

    Args:
        db: Database session
        force: Force re-sync even if recently synced. When False, skips sync
               if the last successful sync completed within CATALOG_SYNC_TTL_SECONDS.

    Returns:
        Dict with sync statistics
    """
    if not force:
        synced_at_setting = db.query(ApplicationSetting).filter(
            ApplicationSetting.key == "module_library.last_synced_at"
        ).first()
        if synced_at_setting and synced_at_setting.value:
            try:
                last_synced = datetime.fromisoformat(synced_at_setting.value)
                age_seconds = (datetime.now(UTC) - last_synced).total_seconds()
                if age_seconds < CATALOG_SYNC_TTL_SECONDS:
                    logger.info(
                        "Module catalog sync skipped — last sync was %.0fs ago (TTL=%ds)",
                        age_seconds,
                        CATALOG_SYNC_TTL_SECONDS,
                    )
                    return {
                        "skipped_recent": True,
                        "total_modules": 0,
                        "created": 0,
                        "updated": 0,
                        "quarantined": 0,
                        "quarantined_paths": [],
                        "errors": [],
                        "source_rows_repaired": 0,
                    }
            except (ValueError, TypeError):
                # Malformed timestamp — proceed with sync rather than skipping silently.
                pass

    import git  # noqa: E402 — lazy import: git CLI not available in slim API container
    stats = {
        "total_modules": 0,
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "quarantined": 0,
        "quarantined_paths": [],
        "errors": [],
        "source_rows_repaired": 0,
    }

    logger.info("Starting module catalog sync...")
    catalog_path = MODULE_CATALOG_PATH
    os.makedirs(catalog_path, exist_ok=True)

    try:
        # Get repository configuration from settings
        repo_config = get_module_repo_config(db)
        auth_ctx = GitAuthService.resolve_for_module_library_token_setting(db)
        git_env, cleanup_git_env = GitAuthService.build_git_environment(auth_ctx)

        # Extract repo name from URL dynamically (instead of hardcoding)
        # e.g., "https://github.com/org/my-modules.git" -> "my-modules"
        from urllib.parse import urlparse
        parsed_url = urlparse(repo_config["url"])
        url_path = parsed_url.path.rstrip('/').rstrip('.git')
        repo_name = url_path.split('/')[-1] if url_path else "modules"
        repo_path = os.path.join(catalog_path, repo_name)

        # Clone or pull repository
        git_ref = repo_config["ref"]
        if os.path.exists(repo_path):
            # Check if it's a valid git repository
            try:
                logger.info(f"Pulling latest from {repo_name} (ref: {git_ref})...")
                repo = git.Repo(repo_path)

                # Update remote URL in case credentials (PAT) have changed
                current_url = repo.remotes.origin.url
                new_url = repo_config["url"]
                if current_url != new_url:
                    logger.info("Updating remote URL (credentials changed)")
                    repo.remotes.origin.set_url(new_url)

                # Fetch all branches
                repo.remotes.origin.fetch(env=git_env, kill_after_timeout=CATALOG_GIT_TIMEOUT_SECONDS)

                # Checkout the correct branch
                try:
                    repo.git.checkout(git_ref)
                except git.exc.GitCommandError:
                    # Branch doesn't exist locally, create tracking branch
                    repo.git.checkout('-b', git_ref, f'origin/{git_ref}')

                # Pull latest changes
                repo.remotes.origin.pull(git_ref, env=git_env, kill_after_timeout=CATALOG_GIT_TIMEOUT_SECONDS)

                logger.info(f"Checked out branch '{git_ref}', latest commit: {repo.head.commit.hexsha[:8]}")
            except (git.exc.InvalidGitRepositoryError, git.exc.NoSuchPathError):
                # Directory exists but is not a valid git repo - remove and clone fresh
                logger.warning(f"Invalid git repository at {repo_path}, removing and cloning fresh")
                import shutil
                shutil.rmtree(repo_path)
                logger.info(f"Cloning {repo_name} (ref: {git_ref})...")
                repo = git.Repo.clone_from(
                    repo_config["url"], repo_path, branch=git_ref, env=git_env,
                    kill_after_timeout=CATALOG_GIT_TIMEOUT_SECONDS,
                )
                logger.info(f"Cloned branch '{git_ref}', commit: {repo.head.commit.hexsha[:8]}")
        else:
            logger.info(f"Cloning {repo_name} (ref: {git_ref})...")
            repo = git.Repo.clone_from(
                repo_config["url"], repo_path, branch=git_ref, env=git_env,
                kill_after_timeout=CATALOG_GIT_TIMEOUT_SECONDS,
            )
            logger.info(f"Cloned branch '{git_ref}', commit: {repo.head.commit.hexsha[:8]}")

        # Read VERSION file from cloned repo if it exists
        version_file = os.path.join(repo_path, "VERSION")
        if os.path.exists(version_file):
            with open(version_file) as f:
                module_version = f.read().strip()
            # Store synced version in ApplicationSettings
            version_setting = db.query(ApplicationSetting).filter(
                ApplicationSetting.key == "module_library.synced_version"
            ).first()
            if version_setting:
                version_setting.value = module_version
            else:
                db.add(ApplicationSetting(
                    key="module_library.synced_version",
                    value=module_version,
                    category="module_library"
                ))
            logger.info(f"Module library version: {module_version}")
            stats["synced_version"] = module_version

        # Pre-fetch existing modules to avoid N+1 queries.
        # Two indexes are built:
        #   git_source → module  (primary: exact URL+ref match)
        #   path       → module  (fallback: catches re-syncs where the URL or ref changed
        #                         but the on-disk path is unchanged — prevents duplicate rows)
        existing_modules = db.query(ModuleLibrary).all()
        existing_modules_map = {m.git_source: m for m in existing_modules}
        existing_modules_by_path = {m.path: m for m in existing_modules if m.path}

        official_source = _ensure_official_module_source(
            db,
            clean_url=repo_config["url"],
            repo_name=repo_name,
            repo_ref=repo_config["ref"],
        )

        stats["source_rows_repaired"] = _backfill_official_source_association(
            db,
            clean_url=repo_config["url"],
            repo_ref=repo_config["ref"],
            source_id=official_source.id,
        )

        # Scan for modules in each category directory
        for category in ["infra", "k8s", "bnk", "app"]:
            category_path = os.path.join(repo_path, category)
            if not os.path.exists(category_path):
                logger.warning(f"Category directory '{category}' not found in repo")
                continue

            logger.info(f"Scanning {category} modules...")
            modules = discover_modules(category_path, category)

            # Import/update modules in database
            for module_def in modules:
                module_def["is_official"] = True
                # Use clean URL (without token) for git_source display
                clean_url = GitAuthService.strip_url_credentials(repo_config["url"])
                module_def["git_source"] = f"git::{clean_url}//{category}/{module_def['path']}?ref={repo_config['ref']}"
                # IMPORTANT: Store full path including category to match git_source and dependency references
                module_def["path"] = f"{category}/{module_def['path']}"
                module_def["module_source_id"] = official_source.id
                module_def["source_path"] = module_def["path"]
                module_def["source_version"] = repo_config["ref"]

                # Pass cached object to upsert_module.
                # Primary lookup: by git_source (URL+ref).
                # Path fallback: if git_source changed (e.g. new ref/URL) but the module
                # path is the same, reuse the existing row instead of inserting a duplicate.
                existing_module = (
                    existing_modules_map.get(module_def["git_source"])
                    or existing_modules_by_path.get(module_def["path"])
                )

                result = upsert_module(db, module_def, existing_module)
                if result == "created":
                    stats["created"] += 1
                # Update cache in case of duplicates in same run
                # Note: We can't easily add the new object to the map here as it's not committed/refreshed
                # but discover_modules shouldn't return duplicates ideally
                elif result == "updated":
                    stats["updated"] += 1
                elif result == "skipped":
                    stats["skipped"] += 1
                stats["total_modules"] += 1
                if module_def.get("validation_error"):
                    stats["quarantined"] += 1
                    stats["quarantined_paths"].append(module_def["path"])

        official_source.module_count = db.query(ModuleLibrary).filter(
            ModuleLibrary.module_source_id == official_source.id,
            ModuleLibrary.is_active,
        ).count()
        official_source.last_synced_at = datetime.now(UTC)
        official_source.sync_status = "success"
        official_source.sync_error = None

        # Record the catalog-level last-sync timestamp that the TTL throttle reads.
        # Written on every successful sync — including content repos with no top-level
        # VERSION file — so the throttle engages and boot doesn't re-clone each restart.
        synced_at_setting = db.query(ApplicationSetting).filter(
            ApplicationSetting.key == "module_library.last_synced_at"
        ).first()
        synced_at = datetime.now(UTC).isoformat()
        if synced_at_setting:
            synced_at_setting.value = synced_at
        else:
            db.add(ApplicationSetting(
                key="module_library.last_synced_at",
                value=synced_at,
                category="module_library"
            ))
        logger.info("Module catalog last_synced_at recorded: %s", synced_at)

    except Exception as e:
        # Get clean URL for error message (without token)
        repo_config = get_module_repo_config(db)
        clean_url = GitAuthService.strip_url_credentials(repo_config["url"])

        error_msg = f"Error syncing {clean_url}: {GitAuthService.sanitize_error_text(str(e), secrets=[auth_ctx.secret if 'auth_ctx' in locals() else None])}"
        logger.error(error_msg)
        stats["errors"].append(error_msg)
    finally:
        if 'cleanup_git_env' in locals():
            cleanup_git_env()

    db.flush()

    # Recalculate deployment order based on dependency graph
    try:
        recalculate_deployment_orders(db)
        logger.info("Recalculated deployment orders based on dependencies")
    except Exception as e:
        logger.warning(f"Failed to recalculate deployment orders: {e}")

    if stats["quarantined"]:
        logger.warning(
            "Module catalog sync quarantined %d module(s): %s",
            stats["quarantined"],
            ", ".join(stats["quarantined_paths"]),
        )

    logger.info(f"Module catalog sync complete: {stats}")
    return stats


def discover_modules(repo_path: str, category: str) -> list[dict]:
    """
    Discover module roots in a repository.

    Supports additive discovery for:
    - legacy Terraform modules (directories with main.tf)
    - deployment packs (directories with bnkforge.pack.json)

    Args:
        repo_path: Path to cloned repository
        category: Module category (infra, k8s, bnk)

    Returns:
        List of module definitions
    """
    modules = []

    # Look for legacy Terraform modules and additive pack manifests.
    for root, dirs, files in os.walk(repo_path):
        # Skip .terraform directories and examples
        if ".terraform" in root or ".git" in root or "/examples/" in root or "\\examples\\" in root:
            continue

        has_terraform_marker = "main.tf" in files
        has_pack_manifest = "bnkforge.pack.json" in files
        if not has_terraform_marker and not has_pack_manifest:
            continue

        module_path = os.path.relpath(root, repo_path)
        repo_relative_path = f"{category}/{module_path}"
        module_name = os.path.basename(root)

        try:
            module_def = parse_module_definition(
                root,
                module_name,
                module_path,
                category,
                discovered_repo_relative_path=repo_relative_path,
            )
            module_def["is_active"] = True
            module_def["validation_error"] = None
            modules.append(module_def)
            logger.info(f"Discovered module: {repo_relative_path}")
        except Exception as e:
            error_text = str(e)[:1000]
            logger.warning(
                f"Quarantining module at {repo_relative_path} due to schema failure: {error_text}"
            )
            modules.append(_build_quarantine_stub(
                module_name=module_name,
                module_path=module_path,
                category=category,
                error_text=error_text,
            ))

    return modules


def _build_quarantine_stub(
    *,
    module_name: str,
    module_path: str,
    category: str,
    error_text: str,
) -> dict:
    """Minimal module_def emitted when parse fails — operator-visible quarantine row."""
    return {
        "name": module_name,
        "path": module_path,
        "category": category,
        "description": f"QUARANTINED: {module_name} — see validation_error for details",
        "module_source_kind": "git_catalog",
        "execution_engine": None,
        "deploy_model": None,
        "engine_type": None,
        "variables_schema": {},
        "dependencies": [],
        "workflow_compatibility": [],
        "version": "unknown",
        "provider": None,
        "tags": [category, module_name, "quarantined"],
        "is_active": False,
        "validation_error": error_text,
    }


def parse_module_definition(
    module_dir: str,
    module_name: str,
    module_path: str,
    category: str,
    discovered_repo_relative_path: str | None = None,
) -> dict:
    """
    Parse a Terraform module directory to extract module definition

    Args:
        module_dir: Path to module directory containing main.tf
        module_name: Module name
        module_path: Relative path in repo
        category: Module category

    Returns:
        Module definition dict
    """
    # Basic module definition
    effective_repo_relative_path = discovered_repo_relative_path or module_path

    module_def = {
        "name": module_name,
        "path": module_path,
        "category": category,
        "description": f"{category.upper()} module: {module_name}",
        "module_source_kind": "git_catalog",
        "execution_engine": "opentofu",
        "deploy_model": "tofu_module",
        "variables_schema": {},
        "dependencies": [],
        "workflow_compatibility": ["greenfield"],  # Default
        "version": "latest",
        "provider": None,
        "tags": [category, module_name]
    }

    # Try to read README.md for description
    readme_path = os.path.join(module_dir, "README.md")
    if os.path.exists(readme_path):
        with open(readme_path) as f:
            readme_content = f.read()
            module_def["readme"] = readme_content
            # Extract description from README (look for ## Description section or first # heading)
            lines = readme_content.split('\n')
            in_description = False
            description_lines = []
            for line in lines:
                if line.startswith("## Description"):
                    in_description = True
                    continue
                if in_description:
                    if line.startswith("##"):
                        break
                    if line.strip():
                        description_lines.append(line.strip())
            if description_lines:
                module_def["description"] = ' '.join(description_lines)
            elif lines and lines[0].startswith("#"):
                module_def["description"] = lines[0].lstrip("#").strip()

    # Parse variables.tf to extract variable schema and detect dependencies
    variables_file = os.path.join(module_dir, "variables.tf")
    if os.path.exists(variables_file):
        try:
            from services.variable_parser import VariableParser
            with open(variables_file) as f:
                content = f.read()
                variables = VariableParser.parse_variables_from_hcl(content)
                module_def["variables_schema"] = variables

                # Detect dependencies from variable patterns (pass module name to filter self-deps)
                dependencies = VariableParser.detect_dependencies_from_variables(variables, module_name)
                module_def["dependencies"] = dependencies

                logger.debug(f"Parsed {len(variables)} variables and detected {len(dependencies)} dependencies for {module_name}")
        except Exception as e:
            logger.warning(f"Failed to parse variables.tf for {module_name}: {e}")

    # Infer some basics from path
    if "aws" in module_path.lower():
        module_def["provider"] = "aws"
    elif "azure" in module_path.lower():
        module_def["provider"] = "azure"
    elif "gcp" in module_path.lower():
        module_def["provider"] = "gcp"

    # Infer workflow compatibility
    if category == "infra":
        module_def["workflow_compatibility"] = ["greenfield"]
    elif category == "k8s":
        module_def["workflow_compatibility"] = ["greenfield", "partial"]
    elif category == "bnk":
        module_def["workflow_compatibility"] = ["greenfield", "partial", "minimal"]
    elif category == "app":
        module_def["workflow_compatibility"] = ["greenfield", "partial"]

    # Pack manifest support (DEPLOY-ENGINE-EXT-003b)
    pack_manifest_path = os.path.join(module_dir, "bnkforge.pack.json")
    if os.path.exists(pack_manifest_path):
        try:
            from services.module_metadata import (
                ModuleMetadataValidator,
                normalize_pack_manifest_for_catalog,
            )

            with open(pack_manifest_path) as f:
                pack_manifest = json.load(f)

            validator = ModuleMetadataValidator()
            validator.validate_pack_manifest(pack_manifest)
            normalized = normalize_pack_manifest_for_catalog(pack_manifest)

            declared_path = str(normalized.get("path") or "").strip()
            if declared_path != effective_repo_relative_path:
                raise ValueError(
                    "Pack manifest path mismatch: "
                    f"declared module.path='{declared_path}' does not match discovered path "
                    f"'{effective_repo_relative_path}'"
                )

            # Keep git-source/path enrichment from caller; overlay normalized values
            module_def.update({
                "name": normalized.get("name") or module_def["name"],
                "path": module_def["path"],
                "category": normalized.get("category") or module_def["category"],
                "description": normalized.get("description") or module_def["description"],
                "version": normalized.get("version") or module_def["version"],
                "provider": normalized.get("provider") or module_def.get("provider"),
                "tags": normalized.get("tags", module_def.get("tags", [])),
                "module_source_kind": normalized.get("module_source_kind", module_def.get("module_source_kind")),
                "execution_engine": normalized.get("execution_engine", module_def.get("execution_engine")),
                "deploy_model": normalized.get("deploy_model", module_def.get("deploy_model")),
                "engine_type": normalized.get("engine_type"),
                "pack_manifest": normalized.get("pack_manifest"),
                "inputs_metadata": normalized.get("inputs_metadata"),
                "outputs_metadata": normalized.get("outputs_metadata"),
                "dependencies_metadata": normalized.get("dependencies_metadata"),
                "dependencies": normalized.get("dependencies", []),
            })

            logger.info(f"Loaded metadata from bnkforge.pack.json for {module_name}")
            return module_def
        except Exception as e:
            raise ValueError(f"Failed to parse bnkforge.pack.json for {module_name}: {e}") from e

    # Legacy metadata support: read module.json if it exists to get rich metadata
    module_json_path = os.path.join(module_dir, "module.json")
    if os.path.exists(module_json_path):
        try:
            with open(module_json_path) as f:
                module_metadata = json.load(f)

            # Extract dependencies metadata
            deps = module_metadata.get("dependencies", {})
            module_def["dependencies_metadata"] = {
                "required": deps.get("required", []),
                "optional": deps.get("optional", [])
            }

            # Extract inputs with source mapping + providers metadata
            inputs = module_metadata.get("inputs", {})
            providers = module_metadata.get("providers", {})
            module_info = module_metadata.get("module", {})
            compatibility_metadata = module_metadata.get("compatibility", {})
            module_def["inputs_metadata"] = {
                "required": inputs.get("required", []),
                "optional": inputs.get("optional", []),
                "providers": providers,
                # Additive compatibility metadata (transitional input + capabilities)
                "compatibility": {
                    "supported_platforms": module_info.get("supported_platforms", []),
                    "required_capabilities": compatibility_metadata.get(
                        "required_capabilities",
                        module_info.get("required_capabilities", []),
                    ),
                },
            }

            # Extract outputs metadata
            outputs = module_metadata.get("outputs", {})
            module_def["outputs_metadata"] = outputs.get("key_outputs", [])

            # Extract deployment order
            deployment = module_metadata.get("deployment", {})
            module_def["deployment_order"] = deployment.get("order", 999)

            # Update description from module.json if better
            if module_info.get("description"):
                module_def["description"] = module_info["description"]

            logger.info(f"Loaded metadata from module.json for {module_name}")
        except Exception as e:
            logger.warning(f"Failed to parse module.json for {module_name}: {e}")

    return module_def


def upsert_module(db: Session, module_def: dict, existing_module: ModuleLibrary | None = None) -> str:
    """
    Insert or update a module in the catalog

    Args:
        db: Database session
        module_def: Module definition
        existing_module: Optional pre-fetched module object to avoid DB query

    Returns:
        "created", "updated", or "skipped" (immutable hashed row left untouched)
    """
    # Check if module exists if not provided
    if existing_module is None:
        existing_module = db.query(ModuleLibrary).filter(
            ModuleLibrary.git_source == module_def["git_source"]
        ).first()

    if existing_module is None and module_def.get("is_official") and module_def.get("path"):
        existing_module = db.query(ModuleLibrary).filter(
            ModuleLibrary.is_official,
            ModuleLibrary.path == module_def["path"],
            ModuleLibrary.is_active,
        ).first()

    if existing_module:
        if existing_module.content_sha256 is not None:
            # D-033: content-hashed version rows are structurally immutable and
            # owned by the manifest sync. The official-catalog upsert must not
            # rewrite one (the before_update guard would abort the whole sync);
            # publishing changed content requires a version bump in its source.
            logger.info(
                "Skipping official-catalog upsert of immutable hashed module row %s",
                existing_module.path or existing_module.name,
            )
            existing_module.last_synced = datetime.now(UTC)
            return "skipped"
        # Update existing module
        # JSON columns (pack_manifest, inputs_metadata, outputs_metadata, dependencies_metadata)
        # require explicit flag_modified() because SQLAlchemy doesn't detect in-place dict mutations.
        # Use copy.deepcopy() to ensure a new object reference, then flag the column as modified.
        json_columns = {"pack_manifest", "inputs_metadata", "outputs_metadata", "dependencies_metadata"}
        for key, value in module_def.items():
            if hasattr(existing_module, key):
                if key in json_columns and isinstance(value, (dict, list)):
                    setattr(existing_module, key, copy.deepcopy(value))
                    flag_modified(existing_module, key)
                else:
                    setattr(existing_module, key, value)
        existing_module.last_synced = datetime.now(UTC)
        return "updated"
    else:
        # Create new module
        module = ModuleLibrary(**module_def)
        module.last_synced = datetime.now(UTC)
        db.add(module)
        return "created"


def add_user_module(
    db: Session,
    name: str,
    category: str,
    git_source: str,
    provider: str | None = None,
    description: str | None = None,
    version: str = "latest"
) -> ModuleLibrary:
    """
    Add a user-provided custom module to the catalog

    Args:
        db: Database session
        name: Module name
        category: Module category (infra, k8s, bnk)
        git_source: Git source URL
        provider: Cloud provider (optional)
        description: Module description
        version: Module version/tag

    Returns:
        Created ModuleLibrary instance
    """
    module = ModuleLibrary(
        name=name,
        category=category,
        git_source=git_source,
        module_source_kind="user_module",
        execution_engine="opentofu",
        deploy_model="tofu_module",
        provider=provider,
        description=description or f"User module: {name}",
        version=version,
        is_official=False,
        is_tested=False,
        test_status="not_tested",
        variables_schema={},
        dependencies=[],
        workflow_compatibility=["greenfield", "partial", "minimal"]
    )

    db.add(module)
    db.flush()
    db.refresh(module)

    logger.info(f"Added user module: {name}")
    return module


def get_catalog_modules(
    db: Session,
    category: str | None = None,
    provider: str | None = None,
    official_only: bool = False,
    tested_only: bool = False
) -> list[ModuleLibrary]:
    """
    Query modules from the catalog

    Args:
        db: Database session
        category: Filter by category
        provider: Filter by provider
        official_only: Only return official modules
        tested_only: Only return tested modules

    Returns:
        List of ModuleLibrary instances
    """
    query = db.query(ModuleLibrary).filter(
        ModuleLibrary.is_active,
    )

    if category:
        query = query.filter(ModuleLibrary.category == category)

    if provider:
        query = query.filter(ModuleLibrary.provider == provider)

    if official_only:
        query = query.filter(ModuleLibrary.is_official)

    if tested_only:
        query = query.filter(ModuleLibrary.is_tested)

    return query.order_by(
        ModuleLibrary.is_official.desc(),
        ModuleLibrary.category,
        ModuleLibrary.name
    ).all()


def recalculate_deployment_orders(db: Session):
    """
    Recalculate deployment_order for all modules based on dependency graph.
    Uses topological sort to determine correct ordering based on dependencies.

    Modules with no dependencies get order 1.
    Modules that depend on others get order = max(dependency_orders) + 1.

    Args:
        db: Database session
    """
    from collections import defaultdict, deque

    # Get all modules
    modules = db.query(ModuleLibrary).all()

    if not modules:
        return

    # Build module path -> module mapping
    module_map = {}
    for module in modules:
        # Module paths are like "infra/aws/vpc", construct from category/provider/name
        if module.provider:
            module_path = f"{module.category}/{module.provider}/{module.name}"
        else:
            module_path = f"{module.category}/{module.name}"
        module_map[module_path] = module

    # Build dependency graph (module_id -> list of dependency module_ids)
    graph = defaultdict(list)
    in_degree = defaultdict(int)

    # Initialize in_degree for all modules
    for module in modules:
        in_degree[module.id] = 0

    # Build graph
    for module in modules:
        if not module.dependencies_metadata:
            continue

        required_deps = module.dependencies_metadata.get("required", [])

        for dep in required_deps:
            dep_module_path = dep.get("module")
            if not dep_module_path:
                continue

            # Find dependency module
            dep_module = module_map.get(dep_module_path)
            if dep_module:
                graph[dep_module.id].append(module.id)
                in_degree[module.id] += 1

    # Topological sort using Kahn's algorithm
    queue = deque()
    order_map = {}

    # Start with modules that have no dependencies (in_degree == 0)
    for module_id in in_degree:
        if in_degree[module_id] == 0:
            queue.append(module_id)
            order_map[module_id] = 1

    # Process queue
    while queue:
        current_id = queue.popleft()
        current_order = order_map[current_id]

        # Process modules that depend on current module
        for dependent_id in graph[current_id]:
            in_degree[dependent_id] -= 1

            # Update order (max of all dependencies + 1)
            if dependent_id not in order_map:
                order_map[dependent_id] = current_order + 1
            else:
                order_map[dependent_id] = max(order_map[dependent_id], current_order + 1)

            # If all dependencies processed, add to queue
            if in_degree[dependent_id] == 0:
                queue.append(dependent_id)

    # Check for circular dependencies (if any module still has in_degree > 0)
    remaining = [mid for mid, degree in in_degree.items() if degree > 0]
    if remaining:
        logger.warning(f"Circular dependencies detected for modules: {remaining}")
        # Assign high order to problematic modules
        for module_id in remaining:
            order_map[module_id] = 999

    # Update database
    for module in modules:
        new_order = order_map.get(module.id, 999)
        if module.deployment_order != new_order:
            logger.debug(f"Updating {module.name}: deployment_order {module.deployment_order} -> {new_order}")
            module.deployment_order = new_order

    logger.info(f"Recalculated deployment orders for {len(modules)} modules")
