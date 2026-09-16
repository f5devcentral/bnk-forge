"""
Module Sync Service

Handles cloning Git repositories and parsing Terraform modules.

ENG-006: This service retains its own db.commit() calls because it is used
internally by other services (module_source_service, module_catalog_service)
and manages its own sync lifecycle with intermediate commits.
"""

import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import requests as http_requests
from sqlalchemy.orm import Session
from sqlalchemy.sql import func

from models import ModuleLibrary, ModuleSource
from services.audit_service import create_audit_log
from services.git_auth_service import GitAuthService
from services.module_metadata import (
    ModuleMetadataValidator,
    infer_deploy_model_from_pack_manifest,
    normalize_pack_manifest_for_catalog,
)
from services.module_version_query import recompute_is_latest
from utils.catalog_versioning import canonical_json_sha256, version_sort_key

logger = logging.getLogger(__name__)

_LEGACY_ENGINE_TO_EXECUTION_ENGINE = {
    "kubernetes": "kubernetes-direct",
    "opentofu": "opentofu",
    "ansible": "ansible",
    "script": "script",
}


def _execution_engine_from_legacy_engine(engine_type: str | None) -> str | None:
    if not isinstance(engine_type, str):
        return None
    normalized = engine_type.strip().lower()
    if not normalized:
        return None
    return _LEGACY_ENGINE_TO_EXECUTION_ENGINE.get(normalized, normalized)


def _deploy_model_from_legacy_engine(engine_type: str | None) -> str | None:
    if not isinstance(engine_type, str):
        return None
    normalized = engine_type.strip().lower()
    if not normalized:
        return None
    if normalized == "ansible":
        return "ansible_playbook"
    if normalized == "script":
        return "script"
    if normalized == "opentofu":
        return "tofu_module"
    return None


# Backward-compat alias — the canonical definition lives in utils.catalog_versioning
# and is shared with registry_service so both backends agree on "latest".
_version_sort_key = version_sort_key


def _derive_pack_deploy_model(module_info: dict) -> str | None:
    explicit = module_info.get("deploy_model")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip().lower()

    pack_manifest = module_info.get("pack_manifest")
    if isinstance(pack_manifest, dict):
        inferred = infer_deploy_model_from_pack_manifest(pack_manifest)
        if inferred:
            return inferred

    return _deploy_model_from_legacy_engine(module_info.get("engine_type"))


class ModuleSyncService:
    """Service for syncing module sources and parsing Terraform files"""

    def __init__(self, db: Session):
        self.db = db

    PACK_MANIFEST_FILENAME = "bnkforge.pack.json"
    ARTIFACT_MANIFEST_FILENAME = "bnkforge.artifact.json"

    def sync_git_source(self, source: ModuleSource) -> dict:
        """
        Sync a Git module source.

        Steps:
        1. Clone repository to temp directory
        2. Find all Terraform modules (directories with .tf files)
        3. Parse variables.tf and outputs.tf for each module
        4. Create/update ModuleLibrary entries
        5. Clean up temp directory

        Returns:
            Dict with sync results (modules_found, modules_created, modules_updated, errors)
        """
        if source.source_type != 'git':
            raise ValueError(f"Source {source.name} is not a Git source")

        logger.info(f"Syncing Git source: {source.name} ({source.url})")

        # Update sync status
        source.sync_status = 'syncing'
        source.sync_error = None
        self.db.commit()

        temp_dir = None
        results = {
            'modules_found': 0,
            'modules_created': 0,
            'modules_updated': 0,
            'modules_unchanged': 0,
            'modules_skipped': 0,
            'errors': [],
            'sync_mode': 'unknown',
            'manifest_sync_used': False,
            'pack_manifests_discovered': 0,
            'stale_modules_inactivated': 0,
            'pinned_versions_inactivated': [],
            'pack_errors': [],
            'version_conflicts': [],
        }

        try:
            # Clone repository
            temp_dir = self._clone_repository(source)
            logger.info(f"Cloned repository to: {temp_dir}")

            # Manifest-first discovery for external deployment packs (DEPLOY-ENGINE-EXT-004a).
            pack_paths = self._find_pack_modules(temp_dir)
            if pack_paths:
                results['modules_found'] = len(pack_paths)
                results['sync_mode'] = 'manifest'
                results['manifest_sync_used'] = True
                results['pack_manifests_discovered'] = len(pack_paths)
                logger.info(f"Found {len(pack_paths)} deployment packs via {self.PACK_MANIFEST_FILENAME}")

                for module_path in pack_paths:
                    try:
                        module_info = self._parse_pack_module(module_path, temp_dir)
                    except Exception as e:
                        self._append_pack_error(
                            results=results,
                            module_path=module_path,
                            stage='manifest_parse_validate',
                            error=e,
                        )
                        continue

                    try:
                        outcome = self._import_pack_module(source, module_info, module_path)
                        if outcome == 'created':
                            results['modules_created'] += 1
                        elif outcome == 'updated':
                            results['modules_updated'] += 1
                        elif outcome == 'unchanged':
                            results['modules_unchanged'] += 1
                        elif outcome == 'skipped':
                            results['modules_skipped'] += 1
                        elif outcome == 'conflict':
                            conflict_msg = (
                                f"{module_path}: module version "
                                f"{module_info.get('version')} already cataloged with different content"
                            )
                            results['version_conflicts'].append(conflict_msg)
                            results['errors'].append(conflict_msg)
                    except Exception as e:
                        # A commit-time failure (e.g. IntegrityError on the
                        # version unique constraint from a concurrent sync)
                        # leaves the session aborted — roll back so the
                        # remaining modules and the final status commit succeed.
                        self.db.rollback()
                        self._append_pack_error(
                            results=results,
                            module_path=module_path,
                            stage='catalog_import',
                            error=e,
                        )

                stale_count, pinned_warnings = self._inactivate_stale_manifest_modules(
                    source_id=source.id,
                    discovered_pack_paths=set(pack_paths),
                )
                results['stale_modules_inactivated'] = stale_count
                # Prominent, per-module, naming the projects (#91). A distinct
                # key, not folded into 'errors': the sync SUCCEEDED; this is a
                # consequence the operator must see, not a failure.
                results['pinned_versions_inactivated'] = pinned_warnings
            else:
                # Legacy fallback: only when no manifests exist in source.
                modules = self._find_terraform_modules(temp_dir)
                results['modules_found'] = len(modules)
                results['sync_mode'] = 'terraform_fallback'
                logger.info(f"No deployment packs found; falling back to {len(modules)} Terraform modules")

                # Parse and import each module
                for module_path in modules:
                    try:
                        module_info = self._parse_terraform_module(module_path, temp_dir)
                        if module_info:
                            created = self._import_module(source, module_info, module_path, temp_dir)
                            if created:
                                results['modules_created'] += 1
                            else:
                                results['modules_updated'] += 1
                    except Exception as e:
                        self.db.rollback()
                        error_msg = f"Error parsing {module_path}: {str(e)}"
                        logger.error(error_msg)
                        results['errors'].append(error_msg)

            # Update source metadata
            source.sync_status = 'success'
            source.last_synced_at = func.now()
            source.module_count = self._count_active_modules_for_source(source.id)
            self.db.commit()

            logger.info(f"Sync complete: {results}")
            create_audit_log(
                self.db,
                action="module_source_sync_succeeded",
                resource_type="module_source",
                resource_id=str(source.id),
                resource_name=source.name,
                status="success",
                details={
                    "source_type": source.source_type,
                    "credential_type": source.credential_type or source.auth_type or "none",
                    "sync_mode": results.get("sync_mode", "unknown"),
                    "modules_found": results.get("modules_found", 0),
                    "modules_created": results.get("modules_created", 0),
                    "modules_updated": results.get("modules_updated", 0),
                    "modules_unchanged": results.get("modules_unchanged", 0),
                    "modules_skipped": results.get("modules_skipped", 0),
                    "version_conflicts": len(results.get("version_conflicts") or []),
                },
            )
            return results

        except Exception as e:
            error_msg = f"Sync failed: {str(e)}"
            logger.error(error_msg)
            source.sync_status = 'failed'
            source.sync_error = error_msg
            self.db.commit()
            create_audit_log(
                self.db,
                action="module_source_sync_failed",
                resource_type="module_source",
                resource_id=str(source.id),
                resource_name=source.name,
                status="failed",
                details={
                    "source_type": source.source_type,
                    "credential_type": source.credential_type or source.auth_type or "none",
                    "error": str(e)[:300],
                },
            )
            raise

        finally:
            # Clean up temp directory
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)

    def sync_registry_source(self, source: ModuleSource) -> dict:
        """
        Sync a Terraform registry module source.

        Queries the Terraform Registry API to discover modules under the
        configured namespace/URL, then creates/updates ModuleLibrary entries.

        Supported URL formats:
        - https://registry.terraform.io  (public registry)
        - https://app.terraform.io       (Terraform Cloud private registry)
        - https://custom.registry.example (any registry implementing the Module Registry Protocol)

        The source.url should be: <registry_base>/<namespace>
        e.g. "https://registry.terraform.io/modules/hashicorp"

        Returns:
            Dict with sync results (modules_found, modules_created, modules_updated, errors)
        """
        if source.source_type != 'registry':
            raise ValueError(f"Source {source.name} is not a registry source")

        logger.info(f"Syncing registry source: {source.name} ({source.url})")

        source.sync_status = 'syncing'
        source.sync_error = None
        self.db.commit()

        results = {
            'modules_found': 0,
            'modules_created': 0,
            'modules_updated': 0,
            'errors': []
        }

        try:
            # Parse registry URL into base + namespace
            url = source.url.rstrip('/')
            parts = url.rsplit('/', 1)
            if len(parts) < 2 or not parts[1]:
                raise ValueError(
                    f"Registry URL must include a namespace, e.g. "
                    f"'https://registry.terraform.io/modules/hashicorp'. Got: {source.url}"
                )

            # Determine if public or private registry
            base_url = parts[0]
            namespace = parts[1]

            # Build headers (auth token for private registries)
            headers = {"Accept": "application/json"}
            if source.auth_token_encrypted:
                from core.encryption import decrypt_value
                token = decrypt_value(source.auth_token_encrypted)
                if token:
                    headers["Authorization"] = f"Bearer {token}"

            # Terraform Registry Module List API
            # GET /v1/modules/:namespace
            api_url = f"{base_url}/v1/modules/{namespace}"
            offset = 0
            limit = 20  # Registry default page size

            while True:
                resp = http_requests.get(
                    api_url,
                    params={"offset": offset, "limit": limit},
                    headers=headers,
                    timeout=30,
                )

                if resp.status_code == 404:
                    raise ValueError(f"Namespace '{namespace}' not found on registry {base_url}")
                resp.raise_for_status()

                data = resp.json()
                modules = data.get("modules", [])
                results['modules_found'] += len(modules)

                for mod in modules:
                    try:
                        created = self._import_registry_module(source, mod, base_url, headers)
                        if created:
                            results['modules_created'] += 1
                        else:
                            results['modules_updated'] += 1
                    except Exception as e:
                        error_msg = f"Error importing {mod.get('id', 'unknown')}: {str(e)}"
                        logger.error(error_msg)
                        results['errors'].append(error_msg)

                # Pagination: check for next page
                meta = data.get("meta", {})
                next_offset = meta.get("next_offset")
                if next_offset is None or not modules:
                    break
                offset = next_offset

            # Update source metadata
            source.sync_status = 'success'
            from sqlalchemy.sql import func
            source.last_synced_at = func.now()
            source.module_count = results['modules_created'] + results['modules_updated']
            self.db.commit()

            logger.info(f"Registry sync complete: {results}")
            return results

        except Exception as e:
            error_msg = f"Registry sync failed: {str(e)}"
            logger.error(error_msg)
            source.sync_status = 'failed'
            source.sync_error = error_msg
            self.db.commit()
            raise

    def _import_registry_module(
        self,
        source: ModuleSource,
        mod_data: dict,
        base_url: str,
        headers: dict,
    ) -> bool:
        """
        Import a single module from the Terraform Registry API response.

        Args:
            source: The ModuleSource record
            mod_data: Module data from the registry list response
            base_url: Registry base URL for detail lookups
            headers: Auth headers

        Returns:
            True if created, False if updated
        """
        # Module identity: namespace/name/provider
        namespace = mod_data.get("namespace", "")
        name = mod_data.get("name", "unknown")
        provider = mod_data.get("provider", "generic")
        mod_id = f"{namespace}/{name}/{provider}"
        version = mod_data.get("version") or (
            mod_data.get("versions", [""])[0] if mod_data.get("versions") else ""
        )

        # Check if already imported
        existing = self.db.query(ModuleLibrary).filter(
            ModuleLibrary.module_source_id == source.id,
            ModuleLibrary.path == mod_id,
        ).first()

        # Fetch module detail for variables/outputs (optional, best-effort)
        variables = []
        outputs = []
        description = mod_data.get("description", "")

        if version:
            try:
                detail_url = f"{base_url}/v1/modules/{mod_id}/{version}"
                detail_resp = http_requests.get(detail_url, headers=headers, timeout=15)
                if detail_resp.ok:
                    detail = detail_resp.json()
                    root = detail.get("root", {})
                    variables = [
                        {
                            "name": v.get("name"),
                            "type": v.get("type", "string"),
                            "description": v.get("description"),
                            "default": v.get("default"),
                            "required": v.get("required", True),
                        }
                        for v in root.get("inputs", [])
                    ]
                    outputs = [
                        {"name": o.get("name"), "description": o.get("description")}
                        for o in root.get("outputs", [])
                    ]
                    description = description or detail.get("description", "")
            except Exception as e:
                logger.debug(f"Could not fetch detail for {mod_id}: {e}")

        category = self._guess_category(mod_id, name)
        git_source = f"registry://{mod_id}"

        if existing:
            # Protect Python SSH modules from catalog overwrite — they have no
            # catalog counterpart and must stay as execution_engine="ssh".
            if existing.module_source_kind == "builtin" and existing.execution_engine == "ssh":
                logger.debug("Skipping SSH builtin module %s (protected from catalog overwrite)", mod_id)
                return False

            # Never downgrade a known-good schema to empty: the registry detail fetch
            # is best-effort and may return [] on transient failures or auth gaps.
            if variables:
                existing.variables_schema = variables
            elif existing.variables_schema:
                logger.warning(
                    "Registry module %s: detail fetch returned empty variables — "
                    "keeping existing schema to avoid data loss.",
                    mod_id,
                )
            else:
                existing.variables_schema = variables  # both empty: legitimately no inputs
            existing.outputs_metadata = outputs or existing.outputs_metadata
            existing.description = description or existing.description
            existing.provider = provider
            existing.category = category
            existing.source_version = version
            existing.module_source_kind = "git_catalog"
            # Preserve kubernetes-direct engine for k8s builtin modules —
            # catalog sync enriches them with pack_manifest/deploy_model but
            # must not reclassify their execution engine as opentofu.
            if existing.execution_engine != "kubernetes-direct":
                existing.execution_engine = "opentofu"
                existing.deploy_model = "tofu_module"
                existing.engine_type = existing.engine_type or "opentofu"
            if version and existing.latest_version != version:
                existing.latest_version = version
                existing.update_available = True
            self.db.commit()
            logger.info(f"Updated registry module: {mod_id}")
            return False
        else:
            new_module = ModuleLibrary(
                name=name,
                category=category,
                path=mod_id,
                provider=provider,
                description=description,
                git_source=git_source,
                variables_schema=variables,
                outputs_metadata=outputs,
                module_source_kind="git_catalog",
                execution_engine="opentofu",
                deploy_model="tofu_module",
                engine_type="opentofu",
                module_source_id=source.id,
                source_path=mod_id,
                source_version=version,
                latest_version=version,
                is_official=True,
                is_active=True,
                is_custom=False,
            )
            self.db.add(new_module)
            self.db.commit()
            logger.info(f"Created registry module: {mod_id}")
            return True

    def _clone_repository(self, source: ModuleSource) -> str:
        """
        Clone Git repository to a temporary directory.

        Returns:
            Path to cloned repository
        """
        temp_dir = tempfile.mkdtemp(prefix='module-sync-')
        try:
            auth_ctx = GitAuthService.resolve_for_module_source(source, db=self.db)
        except Exception as e:
            create_audit_log(
                self.db,
                action="module_source_auth_failed",
                resource_type="module_source",
                resource_id=str(source.id),
                resource_name=source.name,
                status="failed",
                details={
                    "phase": "auth_resolution",
                    "credential_type": source.credential_type or source.auth_type or "none",
                    "error": str(e)[:300],
                },
            )
            raise
        safe_source_url = GitAuthService.strip_url_credentials(source.url)

        # Build git clone command
        git_cmd = ['git', 'clone']

        # Add depth 1 for faster cloning (shallow clone)
        git_cmd.extend(['--depth', '1'])

        # Prefer git_ref over branch for the clone target: `git clone --branch`
        # accepts branch names and tag names, and the resulting working tree is
        # already checked out at that ref. Using a shallow branch clone followed
        # by `git checkout <tag>` fails because `--depth 1` does not fetch tags.
        clone_ref = source.git_ref or source.branch
        if clone_ref:
            git_cmd.extend(['--branch', clone_ref])

        # Handle authentication
        git_cmd.append(safe_source_url)
        git_cmd.append(temp_dir)

        try:
            env, cleanup_env = GitAuthService.build_git_environment(auth_ctx)
        except Exception as e:
            create_audit_log(
                self.db,
                action="module_source_auth_failed",
                resource_type="module_source",
                resource_id=str(source.id),
                resource_name=source.name,
                status="failed",
                details={
                    "phase": "runtime_auth_transport",
                    "credential_type": auth_ctx.credential_type,
                    "error": str(e)[:300],
                },
            )
            raise

        try:
            # Run git clone
            result = subprocess.run(
                git_cmd,
                env=env,
                capture_output=True,
                text=True,
                timeout=300  # 5 minute timeout
            )

            if result.returncode != 0:
                error_kind, guidance = GitAuthService.classify_git_failure(result.stderr)
                sanitized = GitAuthService.sanitize_error_text(result.stderr, secrets=[auth_ctx.secret])
                if error_kind in {"auth_failure", "ssh_auth"}:
                    create_audit_log(
                        self.db,
                        action="module_source_auth_failed",
                        resource_type="module_source",
                        resource_id=str(source.id),
                        resource_name=source.name,
                        status="failed",
                        details={
                            "phase": "git_clone",
                            "credential_type": auth_ctx.credential_type,
                            "error_kind": error_kind,
                            "guidance": guidance,
                        },
                    )
                raise RuntimeError(f"Git clone failed [{error_kind}] {guidance}: {sanitized}")

            # No separate checkout is needed: git clone --branch already checked
            # out the requested ref (branch or tag).
        finally:
            cleanup_env()

        return temp_dir

    def _find_terraform_modules(self, repo_path: str) -> list[str]:
        """
        Find all directories containing Terraform files.

        Returns:
            List of paths to directories with .tf files
        """
        modules = []
        Path(repo_path)

        for root, dirs, files in os.walk(repo_path):
            # Skip .git directory
            if '.git' in root:
                continue

            # Check if directory has .tf files
            tf_files = [f for f in files if f.endswith('.tf')]
            if tf_files:
                # Get relative path from repo root
                rel_path = os.path.relpath(root, repo_path)
                modules.append(rel_path if rel_path != '.' else '')

        return modules

    def _find_pack_modules(self, repo_path: str) -> list[str]:
        """Find all directories containing bnkforge.pack.json manifests."""
        modules = []

        for root, _dirs, files in os.walk(repo_path):
            if '.git' in root:
                continue

            if self.PACK_MANIFEST_FILENAME not in files:
                continue

            rel_path = os.path.relpath(root, repo_path)
            modules.append(rel_path if rel_path != '.' else '')

        return modules

    def _parse_pack_module(self, module_path: str, repo_path: str) -> dict:
        """
        Parse and validate a bnkforge.pack.json manifest and return normalized metadata.
        """
        manifest_path = os.path.join(repo_path, module_path, self.PACK_MANIFEST_FILENAME) if module_path else os.path.join(
            repo_path, self.PACK_MANIFEST_FILENAME
        )
        with open(manifest_path, encoding='utf-8') as f:
            manifest = json.load(f)

        validator = ModuleMetadataValidator()
        validator.validate_pack_manifest(manifest)

        normalized = normalize_pack_manifest_for_catalog(manifest)
        declared_path = str(normalized.get('path') or '').strip()
        discovered_path = module_path
        if declared_path != discovered_path:
            raise ValueError(
                "Pack manifest path mismatch: "
                f"declared module.path='{declared_path}' does not match discovered path '{discovered_path}'"
            )

        # #324: pack manifests carry inputs_metadata but not variables_schema. The deploy
        # path (_strip_global_tfvar_secrets + undeclared-variable filtering) keys off
        # variables_schema = the variables the module actually declares in its .tf files.
        # Parse them here exactly like the terraform-only sync path does, otherwise pack
        # modules (e.g. cneinstall) get an empty schema and jwt_token/manifest_version get
        # stripped or rejected at apply.
        full_path = os.path.join(repo_path, module_path) if module_path else repo_path
        normalized['variables_schema'] = self._extract_tf_variables(full_path)

        # DEPLOY-ENGINE-EXT-007: a container_image artifact ships its runtime spec
        # (image, steps, state, execution, references) in a co-located
        # bnkforge.artifact.json. The pack manifest only declares engine=container
        # + the form inputs, so merge the artifact's runtime blocks into the stored
        # pack_manifest — without them the container engine has no container_image
        # or steps to run. Validate the artifact manifest before merging.
        artifact_path = (
            os.path.join(repo_path, module_path, self.ARTIFACT_MANIFEST_FILENAME)
            if module_path
            else os.path.join(repo_path, self.ARTIFACT_MANIFEST_FILENAME)
        )
        if os.path.exists(artifact_path):
            with open(artifact_path, encoding='utf-8') as f:
                artifact = json.load(f)
            graph = validator.validate_artifact_manifest(
                artifact, registry_host_allowlist=self._registry_host_allowlist()
            )
            pack_manifest = normalized.get('pack_manifest') or {}
            for key in ('container_image', 'helm_chart', 'manifest', 'steps', 'actions', 'state', 'execution', 'references', 'cluster', 'secret_files', 'reports'):
                if key in artifact:
                    pack_manifest[key] = artifact[key]
            normalized['pack_manifest'] = pack_manifest
            normalized['artifact_references'] = graph

        return normalized

    def _registry_host_allowlist(self) -> list[str]:
        """Registry-host allowlist for artifact validation (fail-closed).

        Prefers the operator-configured ``container.registry_host_allowlist``
        default (CSV); falls back to the built-in safe default so the
        supply-chain host check is *always* enforced — even before an operator
        sets a policy, if the setting row is missing, or if the lookup raises.
        The result is never None, so the validator never skips the host check
        on artifact ingest.
        """
        # One resolver shared with the runtime pull-auth path
        # (supply_chain.enforce_host_allowlist), so ingest and runtime can never
        # again disagree on what an empty allowlist means (#79).
        from services.execution.supply_chain import resolve_registry_host_allowlist

        return sorted(resolve_registry_host_allowlist(self.db))

    def _parse_terraform_module(self, module_path: str, repo_path: str) -> dict | None:
        """
        Parse a Terraform module directory.

        Extracts variables, outputs, and metadata from .tf files.

        Returns:
            Dict with module metadata or None if parsing fails
        """
        full_path = os.path.join(repo_path, module_path) if module_path else repo_path

        # Initialize module info
        module_info = {
            'name': os.path.basename(module_path) if module_path else 'root',
            'path': module_path,
            'variables': [],
            'outputs': [],
            'description': None,
            'provider': None,
            'category': None,
        }

        # Try to parse using terraform-config-inspect (if available)
        # This is a more robust approach than parsing HCL directly
        try:
            result = subprocess.run(
                ['terraform-config-inspect', '--json', full_path],
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode == 0:
                config = json.loads(result.stdout)
                module_info.update(self._extract_from_inspect(config))
                return module_info
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
            # terraform-config-inspect not available or failed, fall back to basic parsing
            pass

        # Fallback: Basic .tf file parsing
        module_info.update(self._parse_tf_files_basic(full_path))

        return module_info

    def _extract_tf_variables(self, full_path: str) -> list[dict]:
        """Extract declared variable schema from a module's .tf files.

        Shared by the terraform-only and pack sync paths. Prefers
        terraform-config-inspect; falls back to basic regex parsing. Returns a list
        of {name, type, description, default, required} dicts (possibly empty).
        """
        try:
            result = subprocess.run(
                ['terraform-config-inspect', '--json', full_path],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                return self._extract_from_inspect(json.loads(result.stdout)).get('variables', [])
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
            pass
        return self._parse_tf_files_basic(full_path).get('variables', [])

    def _extract_from_inspect(self, config: dict) -> dict:
        """Extract module info from terraform-config-inspect output"""
        info = {}

        # Extract variables
        if 'variables' in config:
            info['variables'] = [
                {
                    'name': name,
                    'type': var.get('type', 'string'),
                    'description': var.get('description'),
                    'default': var.get('default'),
                    'required': var.get('required', True)
                }
                for name, var in config['variables'].items()
            ]

        # Extract outputs
        if 'outputs' in config:
            info['outputs'] = [
                {
                    'name': name,
                    'description': out.get('description')
                }
                for name, out in config['outputs'].items()
            ]

        # Extract provider from required_providers
        if 'required_providers' in config:
            providers = list(config['required_providers'].keys())
            if providers:
                info['provider'] = providers[0]  # Use first provider

        return info

    def _parse_tf_files_basic(self, module_path: str) -> dict:
        """
        Basic parsing of .tf files (fallback when terraform-config-inspect unavailable).

        This is a simple line-by-line parser, not a full HCL parser.
        """
        info = {
            'variables': [],
            'outputs': [],
            'provider': None
        }

        # Read all .tf files
        tf_files = list(Path(module_path).glob('*.tf'))

        for tf_file in tf_files:
            try:
                with open(tf_file, encoding='utf-8') as f:
                    content = f.read()

                    # Very basic variable extraction
                    # This is NOT a proper HCL parser, just simple regex matching
                    import re

                    # Find variable blocks: variable "name" { ... }
                    var_pattern = r'variable\s+"([^"]+)"\s*\{'
                    for match in re.finditer(var_pattern, content):
                        var_name = match.group(1)
                        info['variables'].append({
                            'name': var_name,
                            'type': 'string',  # Default
                            'description': None,
                            'required': True
                        })

                    # Find output blocks: output "name" { ... }
                    out_pattern = r'output\s+"([^"]+)"\s*\{'
                    for match in re.finditer(out_pattern, content):
                        out_name = match.group(1)
                        info['outputs'].append({
                            'name': out_name,
                            'description': None
                        })

            except Exception as e:
                logger.warning(f"Error reading {tf_file}: {e}")

        return info

    @staticmethod
    def _module_dir_has_variable_declarations(full_path: str) -> bool:
        """
        Return True if any .tf file in *full_path* contains a variable block.

        Used to distinguish "parsed empty because no variables exist" (legit) from
        "parsed empty despite variable declarations" (parse failure).  Cheap: only
        reads .tf files, no subprocess.
        """
        import re
        var_pattern = re.compile(r'variable\s+"[^"]+"')
        try:
            for tf_file in Path(full_path).glob("*.tf"):
                try:
                    if var_pattern.search(tf_file.read_text(encoding="utf-8", errors="replace")):
                        return True
                except Exception:
                    pass
        except Exception:
            pass
        return False

    def _import_module(
        self,
        source: ModuleSource,
        module_info: dict,
        module_path: str,
        repo_path: str
    ) -> bool:
        """
        Import a module into the library.

        Returns:
            True if created, False if updated
        """
        # Generate git source URL with path
        git_source = self._build_git_source(source, module_path)

        # Check if module already exists
        existing = self.db.query(ModuleLibrary).filter(
            ModuleLibrary.module_source_id == source.id,
            ModuleLibrary.path == module_path
        ).first()

        # Determine category from path (heuristic)
        category = self._guess_category(module_path, module_info['name'])

        # Determine provider from path or metadata
        provider = module_info.get('provider') or self._guess_provider(module_path)

        # Guard: if the parse yielded an empty variables list, check whether the
        # module source actually declares variables.  An empty result with declared
        # variables means the parser failed silently — do not persist that empty list.
        parsed_variables: list = module_info.get('variables') or []
        parsed_empty = not parsed_variables
        if parsed_empty:
            full_module_path = os.path.join(repo_path, module_path) if module_path else repo_path
            has_declarations = self._module_dir_has_variable_declarations(full_module_path)
        else:
            has_declarations = False  # irrelevant when parse succeeded

        if existing:
            # Protect Python SSH modules from catalog overwrite
            if existing.module_source_kind == "builtin" and existing.execution_engine == "ssh":
                logger.debug("Skipping SSH builtin module %s (protected from catalog overwrite)", module_path)
                return False

            if existing.content_sha256 is not None:
                # D-033: an immutable content-hashed version row imported via the
                # pack-manifest path. The terraform fallback (which runs when a
                # repo presents no bnkforge.pack.json) must not mutate it — the
                # before_update guard would abort the whole sync.
                logger.warning(
                    "Skipping terraform-fallback update of hashed module row %s (immutable)",
                    module_path,
                )
                return False

            # Resolve variables_schema to persist: never downgrade a known-good schema
            # to empty when the module source still declares variables (parse failure).
            if parsed_empty and has_declarations:
                logger.warning(
                    "Module %s: parse yielded empty variables_schema but variable declarations exist "
                    "in source — keeping existing schema to avoid data loss.",
                    module_path,
                )
                resolved_variables = existing.variables_schema
            else:
                resolved_variables = parsed_variables

            # Update existing module
            existing.variables_schema = resolved_variables
            existing.outputs_metadata = module_info['outputs']
            existing.description = module_info.get('description')
            existing.category = category
            existing.provider = provider
            existing.module_source_kind = "git_catalog"
            # Preserve kubernetes-direct engine for k8s builtin modules
            if existing.execution_engine != "kubernetes-direct":
                existing.execution_engine = "opentofu"
                existing.deploy_model = "tofu_module"
                existing.engine_type = existing.engine_type or "opentofu"
            existing.source_path = module_path
            existing.source_version = source.git_ref or source.branch
            existing.git_source = git_source
            self.db.commit()
            logger.info(f"Updated module: {module_info['name']}")
            return False
        else:
            # Create new module.
            # For a brand-new module: if parse yielded empty but declarations exist,
            # log a warning so operators know the schema is unresolved.  We still
            # create the record (to register the module) but the empty schema will
            # surface as a resync candidate on next successful parse.
            if parsed_empty and has_declarations:
                logger.warning(
                    "Module %s: parse yielded empty variables_schema but variable declarations exist "
                    "in source — new module will be created with unresolved (empty) schema.",
                    module_path,
                )
            new_module = ModuleLibrary(
                name=module_info['name'],
                category=category,
                path=module_path,
                provider=provider,
                description=module_info.get('description'),
                git_source=git_source,
                variables_schema=parsed_variables,
                outputs_metadata=module_info['outputs'],
                module_source_kind="git_catalog",
                execution_engine="opentofu",
                deploy_model="tofu_module",
                engine_type="opentofu",
                module_source_id=source.id,
                source_path=module_path,
                source_version=source.git_ref or source.branch,
                is_official=False,
                is_active=True
            )
            self.db.add(new_module)
            self.db.commit()
            logger.info(f"Created module: {module_info['name']}")
            return True

    def _import_pack_module(self, source: ModuleSource, module_info: dict, module_path: str) -> str:
        """
        Import a normalized pack manifest into ModuleLibrary.

        D-033: identity is (module_source_id, path, version) — one immutable row
        per version. A version bump creates a NEW row and leaves prior versions
        untouched; is_latest is recomputed per (source, path) afterwards.

        Returns an outcome:
          'created'   — new version row created
          'updated'   — legacy (NULL content_sha256) row grandfather-updated in
                        place; the hash is set, freezing it thereafter
          'unchanged' — this exact version+content is already cataloged (no-op)
          'conflict'  — this version exists with DIFFERENT content; the stored
                        row is immutable, nothing is written
          'skipped'   — protected builtin row; not touched
        """
        git_source = self._build_git_source(source, module_path)
        source_version = source.git_ref or source.branch
        incoming_version = module_info.get('version')

        existing = self.db.query(ModuleLibrary).filter(
            ModuleLibrary.module_source_id == source.id,
            ModuleLibrary.source_path == module_path,
            ModuleLibrary.version == incoming_version,
        ).first()
        if existing is None:
            # Backward-compatible lookup: a pre-D-033 row (NULL content hash) is
            # grandfather-updated in place only when it represents the SAME
            # version (or has no recorded version — including rows created
            # before source_path identity use). A version bump over a legacy
            # row creates a new row and leaves the legacy row untouched, so
            # project modules pinned to it keep the manifest they deployed with.
            legacy_version_matches = (ModuleLibrary.version == incoming_version) | ModuleLibrary.version.is_(None)
            existing = self.db.query(ModuleLibrary).filter(
                ModuleLibrary.module_source_id == source.id,
                ModuleLibrary.source_path == module_path,
                ModuleLibrary.content_sha256.is_(None),
                legacy_version_matches,
            ).first() or self.db.query(ModuleLibrary).filter(
                ModuleLibrary.module_source_id == source.id,
                ModuleLibrary.path == module_path,
                ModuleLibrary.content_sha256.is_(None),
                legacy_version_matches,
            ).first()

        if existing:
            # Protect Python SSH modules from catalog overwrite (checked before
            # hashing so skipped rows don't pay serialization cost every sync).
            if existing.module_source_kind == "builtin" and existing.execution_engine == "ssh":
                logger.debug("Skipping SSH builtin module %s (protected from catalog overwrite)", module_path)
                return 'skipped'

            content_sha = self._module_content_sha256(module_info)
            if existing.content_sha256 is not None:
                if not existing.is_active:
                    # The row's path is present in the repo again — reactivate it
                    # (its immutable content IS this version) regardless of the
                    # content comparison below; a conflict is still reported.
                    existing.is_active = True
                    recompute_is_latest(self.db, source.id, existing.path or module_path)
                    self.db.commit()
                if existing.content_sha256 == content_sha:
                    return 'unchanged'
                logger.warning(
                    "Module version conflict: %s@%s already cataloged with different content — not overwritten",
                    module_path,
                    incoming_version,
                )
                return 'conflict'

            existing.name = module_info.get('name') or existing.name
            existing.path = module_info.get('path') or existing.path
            existing.category = module_info.get('category') or existing.category
            existing.provider = module_info.get('provider') or existing.provider
            existing.description = module_info.get('description') or existing.description
            existing.version = module_info.get('version') or existing.version
            existing.tags = module_info.get('tags', [])

            existing.module_source_kind = module_info.get('module_source_kind') or "git_catalog"
            existing.execution_engine = (
                module_info.get('execution_engine')
                or _execution_engine_from_legacy_engine(module_info.get('engine_type'))
                or existing.execution_engine
            )
            existing.deploy_model = _derive_pack_deploy_model(module_info) or existing.deploy_model
            existing.engine_type = module_info.get('engine_type')
            existing.pack_manifest = module_info.get('pack_manifest')
            existing.artifact_references = module_info.get('artifact_references')
            existing.inputs_metadata = module_info.get('inputs_metadata')
            # #324: don't wipe a good schema if a re-parse comes back empty.
            existing.variables_schema = module_info.get('variables_schema') or existing.variables_schema
            existing.outputs_metadata = module_info.get('outputs_metadata')
            existing.dependencies_metadata = module_info.get('dependencies_metadata')
            existing.dependencies = module_info.get('dependencies', [])

            existing.module_source_id = source.id
            existing.source_path = module_path
            existing.source_version = source_version
            existing.git_source = git_source
            existing.is_official = False
            existing.is_active = True
            # Grandfather transition: setting the hash freezes this row (D-033).
            existing.content_sha256 = content_sha

            recompute_is_latest(self.db, source.id, existing.path or module_path)
            self.db.commit()
            logger.info(f"Updated pack module: {module_info.get('name')}")
            return 'updated'

        # An SSH builtin can own this path without matching the source-scoped
        # lookups above (builtin rows may carry no/another module_source_id, or a
        # different version). Never create a parallel catalog row at its path —
        # is_latest recompute would demote the builtin and every path-based
        # resolver would silently prefer the catalog row.
        builtin_owner = self.db.query(ModuleLibrary).filter(
            ModuleLibrary.path == module_path,
            ModuleLibrary.execution_engine == "ssh",
            ModuleLibrary.git_source.like("builtin://%"),
        ).first()
        if builtin_owner is not None:
            logger.debug("Skipping %s: path owned by SSH builtin module", module_path)
            return 'skipped'

        content_sha = self._module_content_sha256(module_info)
        new_module = ModuleLibrary(
            name=module_info.get('name') or (os.path.basename(module_path) if module_path else 'root'),
            category=module_info.get('category') or self._guess_category(module_path, module_info.get('name', '')),
            path=module_info.get('path') or module_path,
            provider=module_info.get('provider') or self._guess_provider(module_path),
            description=module_info.get('description'),
            git_source=git_source,
            version=module_info.get('version'),
            tags=module_info.get('tags', []),
            module_source_kind=module_info.get('module_source_kind') or "git_catalog",
            execution_engine=(
                module_info.get('execution_engine')
                or _execution_engine_from_legacy_engine(module_info.get('engine_type'))
                or "opentofu"
            ),
            deploy_model=_derive_pack_deploy_model(module_info),
            engine_type=module_info.get('engine_type'),
            pack_manifest=module_info.get('pack_manifest'),
            artifact_references=module_info.get('artifact_references'),
            inputs_metadata=module_info.get('inputs_metadata'),
            variables_schema=module_info.get('variables_schema'),
            outputs_metadata=module_info.get('outputs_metadata'),
            dependencies_metadata=module_info.get('dependencies_metadata'),
            dependencies=module_info.get('dependencies', []),
            module_source_id=source.id,
            source_path=module_path,
            source_version=source_version,
            is_official=False,
            is_active=True,
            content_sha256=content_sha,
        )
        self.db.add(new_module)
        self.db.flush()
        recompute_is_latest(self.db, source.id, new_module.path or module_path)
        self.db.commit()
        logger.info(f"Created pack module: {new_module.name}")
        return 'created'

    def _module_content_sha256(self, module_info: dict) -> str:
        """Canonical content hash of a normalized pack-module import (D-033).

        Both the stored hash (at create/grandfather time) and the compare hash
        (at re-sync) are computed from the same normalized module_info shape,
        so raw-vs-normalized drift cannot produce false conflicts (#430).
        """
        return canonical_json_sha256(module_info)

    def _build_git_source(self, source: ModuleSource, module_path: str) -> str:
        """Build deterministic git_source URL including source path."""
        return f"{source.url}//{module_path}" if module_path else source.url

    def _count_active_modules_for_source(self, source_id: int) -> int:
        """Count DISTINCT active module paths for a source.

        D-033 retains one row per version, so a raw row count would inflate
        with every version bump; the user-facing "module count" means distinct
        modules.
        """
        return (
            self.db.query(func.count(func.distinct(ModuleLibrary.path)))
            .filter(
                ModuleLibrary.module_source_id == source_id,
                ModuleLibrary.is_active,
            )
            .scalar()
            or 0
        )

    def _append_pack_error(self, results: dict, module_path: str, stage: str, error: Exception) -> None:
        """Append structured pack-level error while preserving legacy flat errors list."""
        error_text = str(error)
        error_msg = f"Error [{stage}] {module_path}/{self.PACK_MANIFEST_FILENAME}: {error_text}"
        logger.error(error_msg)
        results['errors'].append(error_msg)
        results['pack_errors'].append({
            'path': module_path,
            'stage': stage,
            'message': error_text,
        })

    def _inactivate_stale_manifest_modules(
        self, source_id: int, discovered_pack_paths: set[str]
    ) -> tuple[int, list[dict]]:
        """
        Mark missing manifest-backed source modules inactive after successful manifest sync.

        Legacy Terraform-only imports are intentionally excluded from this reconciliation.

        Returns ``(stale_count, pinned_warnings)``. A version row still
        referenced by project modules is deactivated anyway -- the FK keeps
        deployments working and the destroy hazard stays fixed -- but it
        disappears from every active-filtered surface (catalog list,
        available_module_versions, re-pin targets) with no signal. ADR D-033
        Decision 4 promised a guard or a loud warning here and neither existed
        (#91). Each such row is reported with the projects that pin it, so the
        sync result says exactly what was taken out from under whom.
        """
        from models import Project, ProjectModule

        stale_count = 0
        source_rows = self.db.query(ModuleLibrary).filter(
            ModuleLibrary.module_source_id == source_id,
            ModuleLibrary.is_active,
        ).all()

        stale_paths: set[str] = set()
        pinned_warnings: list[dict] = []
        for module in source_rows:
            if not self._is_manifest_backed_module(module):
                continue
            module_identity = module.source_path if module.source_path is not None else (module.path or '')
            if module_identity in discovered_pack_paths:
                continue

            pinning = (
                self.db.query(ProjectModule.project_id, Project.name)
                .join(Project, Project.id == ProjectModule.project_id)
                .filter(ProjectModule.module_library_id == module.id)
                .distinct()
                .all()
            )
            if pinning:
                warning = {
                    "module_id": module.id,
                    "path": module.path,
                    "version": module.version,
                    "pinned_by": [
                        {"project_id": pid, "project_name": pname} for pid, pname in pinning
                    ],
                    "message": (
                        f"Deactivated {module.path}@{module.version}: no longer published by "
                        f"the source but still pinned by {len(pinning)} project(s). Existing "
                        f"deployments keep working; the version is hidden from catalog and re-pin "
                        f"surfaces. Re-publish it, or change those modules' version."
                    ),
                }
                pinned_warnings.append(warning)
                logger.warning("module_sync: %s", warning["message"])

            module.is_active = False
            stale_count += 1
            if module.path:
                stale_paths.add(module.path)

        # An inactivated row may have held is_latest — hand the flag to the
        # newest remaining ACTIVE version so the path doesn't vanish from
        # latest-filtered views.
        for path in stale_paths:
            recompute_is_latest(self.db, source_id, path)

        return stale_count, pinned_warnings

    def _is_manifest_backed_module(self, module: ModuleLibrary) -> bool:
        """Return True when module row represents manifest-backed import data."""
        if module.engine_type:
            return True
        return isinstance(module.pack_manifest, dict) and len(module.pack_manifest) > 0

    def _guess_category(self, path: str, name: str) -> str:
        """Guess module category from path or name"""
        path_lower = path.lower()
        name_lower = name.lower()

        if 'infra' in path_lower or 'vpc' in name_lower or 'network' in name_lower:
            return 'infra'
        elif 'k8s' in path_lower or 'kubernetes' in path_lower or 'eks' in name_lower:
            return 'k8s'
        elif 'bnk' in path_lower or 'f5' in name_lower:
            return 'bnk'
        else:
            return 'other'

    def _guess_provider(self, path: str) -> str:
        """Guess provider from path"""
        path_lower = path.lower()

        if 'aws' in path_lower:
            return 'aws'
        elif 'azure' in path_lower:
            return 'azure'
        elif 'gcp' in path_lower or 'google' in path_lower:
            return 'gcp'
        else:
            return 'generic'
