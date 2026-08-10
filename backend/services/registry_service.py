"""Registry/public-git module importer.

Orchestrates fetcher → catalog writer → variable scraper → pack synthesizer →
ModuleLibrary row → smoke test dispatch. The HTTP search endpoints stay close
to the original implementation (tested by the existing UI) — only the import
path is the substantial rewrite.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import requests
from sqlalchemy.orm import Session

from core.cache import invalidate_cache
from core.encryption import decrypt_value
from models import CloudCredentialTemplate, ModuleLibrary, ModuleSource
from services import module_fetcher
from services.module_metadata import ModuleMetadataValidator, normalize_pack_manifest_for_catalog
from services.registry_catalog_writer import remove_module, write_module
from services.registry_pack_synthesizer import ModuleIdentity, synthesize
from services.registry_variable_scraper import scrape
from utils.catalog_versioning import version_sort_key

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

OPENTOFU_REGISTRY = module_fetcher.OPENTOFU_REGISTRY
TERRAFORM_REGISTRY = module_fetcher.TERRAFORM_REGISTRY


# Unified source-type map: display_name, url, and dir_short per import-source type.
# Graceful-degrade: callers use .get(source_type, {}).get(key, fallback) so a new
# source-type cannot raise KeyError.
_SOURCE_TYPES: dict[str, dict[str, str]] = {
    "opentofu_registry": {
        "display_name": "OpenTofu Registry",
        "url": "",  # resolved at runtime from OPENTOFU_REGISTRY
        "dir_short": "opentofu",
    },
    "terraform_registry": {
        "display_name": "Terraform Registry",
        "url": "",  # resolved at runtime from TERRAFORM_REGISTRY
        "dir_short": "terraform",
    },
    "tfc_private": {
        "display_name": "Terraform Cloud (private)",
        "url": "https://app.terraform.io",
        "dir_short": "tfc",
    },
    "public_git": {
        "display_name": "Public Git",
        "url": "git+https://",
        "dir_short": "git",
    },
}


class RegistryService:
    """Search public registries and import modules into the catalog."""

    def __init__(self, db: Session):
        self.db = db
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "BNK-Forge/2.0"})

    # ------------------------------------------------------------------
    # Search / metadata (unchanged shape — used by existing UI)
    # ------------------------------------------------------------------

    def search_modules(
        self,
        query: str = "",
        provider: str | None = None,
        verified: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        for base, label in ((OPENTOFU_REGISTRY, "opentofu"), (TERRAFORM_REGISTRY, "terraform")):
            try:
                result = self._search_registry(
                    base, query=query, provider=provider, verified=verified, limit=limit, offset=offset
                )
                if result.get("modules"):
                    result["source"] = label
                    return result
            except Exception as exc:  # noqa: BLE001
                logger.warning("registry search via %s failed: %s", base, exc)
        return {"modules": [], "meta": {"limit": limit, "offset": offset, "total_count": 0}, "source": "none"}

    def _search_registry(self, base_url: str, **kwargs) -> dict:
        params: dict[str, object] = {"limit": min(kwargs.get("limit", 50), 100), "offset": kwargs.get("offset", 0)}
        if kwargs.get("query"):
            params["q"] = kwargs["query"]
        if kwargs.get("provider"):
            params["provider"] = kwargs["provider"]
        if kwargs.get("verified") is not None:
            params["verified"] = str(kwargs["verified"]).lower()
        resp = self.session.get(f"{base_url}/modules", params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def get_module_details(
        self, namespace: str, name: str, provider: str, use_terraform_registry: bool = False
    ) -> dict | None:
        base = TERRAFORM_REGISTRY if use_terraform_registry else OPENTOFU_REGISTRY
        try:
            resp = self.session.get(f"{base}/modules/{namespace}/{name}/{provider}", timeout=10)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as exc:
            logger.error("get_module_details failed: %s", exc)
            return None

    def get_module_versions(
        self, namespace: str, name: str, provider: str, use_terraform_registry: bool = False
    ) -> list[dict]:
        base = TERRAFORM_REGISTRY if use_terraform_registry else OPENTOFU_REGISTRY
        try:
            resp = self.session.get(f"{base}/modules/{namespace}/{name}/{provider}/versions", timeout=10)
            resp.raise_for_status()
            data = resp.json()
            return data.get("modules", [{}])[0].get("versions", [])
        except requests.exceptions.RequestException as exc:
            logger.error("get_module_versions failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Import — public registries
    # ------------------------------------------------------------------

    def import_module_from_registry(
        self,
        namespace: str,
        name: str,
        provider: str,
        version: str | None = None,
        use_terraform_registry: bool = False,
    ) -> ModuleLibrary | None:
        """Fetch + extract + scrape + synthesize + write a public-registry module."""
        details = self.get_module_details(namespace, name, provider, use_terraform_registry)
        if not details:
            logger.error("module not found: %s/%s/%s", namespace, name, provider)
            return None

        versions = self.get_module_versions(namespace, name, provider, use_terraform_registry)
        latest = _pick_latest_version(versions, fallback=details.get("version"))
        target_version = version or latest

        if use_terraform_registry:
            source_type = "terraform_registry"
            fetch = module_fetcher.fetch_terraform_registry(namespace, name, provider, target_version)
        else:
            source_type = "opentofu_registry"
            fetch = module_fetcher.fetch_opentofu_registry(namespace, name, provider, target_version)

        return self._finalize_import(
            source_type=source_type,
            namespace=namespace,
            name=name,
            provider=provider,
            version=target_version,
            latest_version=latest,
            description=details.get("description") or "",
            fetch_result=fetch,
        )

    def import_module_from_git(
        self,
        url: str,
        ref: str,
        subpath: str | None = None,
        version_label: str | None = None,
        display_name: str | None = None,
    ) -> ModuleLibrary:
        """Import a module pinned to a public git URL + ref."""
        fetch = module_fetcher.fetch_public_git(url=url, ref=ref, subpath=subpath)

        # Synthesize a namespace from the URL host + path so multiple repos don't collide.
        namespace, repo_name = _derive_git_identity(url, subpath, display_name)
        return self._finalize_import(
            source_type="public_git",
            namespace=namespace,
            name=repo_name,
            provider="git",
            version=version_label or ref,
            latest_version=version_label or ref,
            description=f"Imported from {url} @ {ref}",
            fetch_result=fetch,
            upstream_subpath=subpath,
        )

    def import_module_from_tfc(
        self,
        hostname: str,
        namespace: str,
        name: str,
        provider: str,
        version: str,
        credential_template_id: int,
    ) -> ModuleLibrary:
        """Import a Terraform Cloud private module (token-auth)."""
        template = (
            self.db.query(CloudCredentialTemplate)
            .filter(CloudCredentialTemplate.id == credential_template_id)
            .first()
        )
        if template is None or not template.tfc_api_token_encrypted:
            raise ValueError("Credential template lacks a TFC API token")
        token = decrypt_value(template.tfc_api_token_encrypted)
        if not token:
            raise ValueError("Failed to decrypt TFC API token")

        fetch = module_fetcher.fetch_tfc_private(
            hostname=hostname,
            namespace=namespace,
            name=name,
            provider=provider,
            version=version,
            token=token,
        )
        return self._finalize_import(
            source_type="tfc_private",
            namespace=namespace,
            name=name,
            provider=provider,
            version=version,
            latest_version=version,
            description=f"Imported from {hostname}/{namespace}/{name}/{provider}",
            fetch_result=fetch,
        )

    # ------------------------------------------------------------------
    # Internal: shared finalization
    # ------------------------------------------------------------------

    def _finalize_import(
        self,
        *,
        source_type: str,
        namespace: str,
        name: str,
        provider: str,
        version: str,
        latest_version: str,
        description: str,
        fetch_result: module_fetcher.FetchResult,
        upstream_subpath: str | None = None,
    ) -> ModuleLibrary:
        source = self._get_or_create_source(source_type)

        dir_short = _SOURCE_TYPES.get(source_type, {}).get("dir_short", source_type)
        module_id_path = f"registry/{dir_short}/{namespace}/{name}"

        # Preserve overrides from prior import if present.
        existing = (
            self.db.query(ModuleLibrary)
            .filter(
                ModuleLibrary.module_source_id == source.id,
                ModuleLibrary.path == module_id_path,
            )
            .first()
        )
        overrides = (existing.metadata_overrides if existing else None) or {}

        # Stage 1: write the tarball to a tempdir-style scratch under MODULE_CATALOG_PATH
        # via the catalog writer. We synthesize a SKELETON manifest first so the directory
        # is created, then re-write the manifest with real scraped data.
        skeleton_manifest: dict = {
            "schema_version": 1,
            "module": {
                "name": name,
                "path": module_id_path,
                "version": version,
                "category": "infra",
                "provider": provider,
                "description": description or name,
                "supported_platforms": ["any"],
            },
            "deployment_pack": {
                "engine": "opentofu",
                "runner_profile": "opentofu-default",
                "working_directory": ".",
                "lifecycle": {
                    "supports_init": True, "supports_plan": True, "supports_apply": True,
                    "supports_destroy": True, "supports_refresh": True, "supports_drift": True,
                },
                "entrypoints": {"module_root": "."},
            },
            "dependencies": {"required": [], "optional": []},
            "inputs": {"required": [], "optional": []},
            "outputs": {"key_outputs": []},
        }
        target_dir = write_module(
            tarball_bytes=fetch_result.tarball_bytes,
            source_type=source_type,
            namespace=namespace,
            name=name,
            pack_manifest=skeleton_manifest,
            extracted_root=fetch_result.extracted_root,
        )

        # Stage 2: scrape variables/outputs from the extracted source.
        scraped = scrape(target_dir)

        identity: ModuleIdentity = {
            "name": name,
            "catalog_path": module_id_path,
            "version": version,
            "provider": provider,
            "description": description or name,
        }
        full_manifest = synthesize(
            identity=identity,
            scraped=scraped,
            sha256=fetch_result.sha256,
            source_type=source_type,
            upstream_url=fetch_result.canonical_url,
            overrides=overrides,
            module_dir=target_dir,
        )

        try:
            ModuleMetadataValidator().validate_pack_manifest(full_manifest)
        except Exception:
            logger.exception("synthesized manifest failed validation; cleaning up %s", target_dir)
            remove_module(source_type=source_type, namespace=namespace, name=name)
            raise

        # Stage 3: re-write manifest with real data.
        manifest_path = target_dir / "bnkforge.pack.json"
        import json as _json
        with manifest_path.open("w", encoding="utf-8") as fh:
            _json.dump(full_manifest, fh, indent=2)

        normalized = normalize_pack_manifest_for_catalog(full_manifest)

        if existing:
            module = existing
        else:
            module = ModuleLibrary(
                name=normalized["name"] or name,
                category=normalized["category"] or "infra",
                path=module_id_path,
                provider=provider,
                git_source=fetch_result.canonical_url,
                module_source_id=source.id,
                source_path=module_id_path,
                is_active=True,
            )
            self.db.add(module)

        module.description = normalized["description"] or description
        module.version = normalized["version"] or version
        module.module_source_kind = "git_catalog"
        module.execution_engine = normalized["execution_engine"]
        module.deploy_model = normalized["deploy_model"]
        module.engine_type = normalized["engine_type"]
        module.pack_manifest = full_manifest
        module.inputs_metadata = normalized["inputs_metadata"]
        module.outputs_metadata = normalized["outputs_metadata"]
        module.dependencies_metadata = normalized["dependencies_metadata"]
        module.dependencies = normalized["dependencies"]
        module.variables_schema = (
            full_manifest["inputs"]["required"] + full_manifest["inputs"]["optional"]
        )
        module.tags = normalized.get("tags") or []
        module.source_version = version
        module.latest_version = latest_version
        module.update_available = (version != latest_version)
        module.local_path = str(target_dir)
        module.tarball_sha256 = fetch_result.sha256
        module.upstream_source_url = fetch_result.canonical_url
        module.upstream_subpath = upstream_subpath
        module.last_smoke_test_status = "pending"
        module.last_smoke_test_output = None
        module.last_smoke_test_at = None
        module.last_synced = datetime.now(UTC)

        self.db.flush()
        self.db.refresh(module)

        # Update source module count.
        source.module_count = (
            self.db.query(ModuleLibrary)
            .filter(ModuleLibrary.module_source_id == source.id)
            .count()
        )

        # Catalog list endpoint caches results for 15min; invalidate so the
        # newly-imported module is visible immediately.
        invalidate_cache("module_library:")

        # Dispatch smoke test on the worker. Best-effort — any broker error is
        # logged but doesn't block the import.
        try:
            from tasks.registry_smoke_test import smoke_test_module
            smoke_test_module.delay(module.id)
        except Exception:  # noqa: BLE001
            logger.exception("failed to dispatch smoke test for module %s", module.id)

        logger.info("imported registry module %s (id=%s, sha=%s)", module_id_path, module.id, fetch_result.sha256[:12])
        return module

    def _get_or_create_source(self, source_type: str) -> ModuleSource:
        meta = _SOURCE_TYPES.get(source_type, {})
        registry_name = meta.get("display_name", source_type.replace("_", " ").title())
        source = self.db.query(ModuleSource).filter(ModuleSource.name == registry_name).first()
        if source is not None:
            if source.source_type != source_type:
                source.source_type = source_type
            return source

        # Resolve runtime URLs for registry types (constants not available at map definition time).
        runtime_url_overrides = {
            "terraform_registry": TERRAFORM_REGISTRY,
            "opentofu_registry": OPENTOFU_REGISTRY,
        }
        url = runtime_url_overrides.get(source_type, meta.get("url", ""))
        source = ModuleSource(
            name=registry_name,
            source_type=source_type,
            url=url,
            sync_status="success",
            is_active=True,
            description=f"{registry_name} (auto-created on first import)",
        )
        self.db.add(source)
        self.db.flush()
        self.db.refresh(source)
        return source

    # ------------------------------------------------------------------
    # Updates / upgrades — used by routes + poller
    # ------------------------------------------------------------------

    def check_for_updates(self, module_id: int) -> dict:
        module = self.db.query(ModuleLibrary).filter(ModuleLibrary.id == module_id).first()
        if not module:
            raise ValueError(f"Module {module_id} not found")

        source = module.source
        if not source or source.source_type not in {"terraform_registry", "opentofu_registry", "tfc_private"}:
            return {
                "current_version": module.source_version,
                "latest_version": module.source_version,
                "update_available": False,
                "message": "Not a registry-tracked module",
            }

        ns, repo_name, prov = _split_registry_path(module.path)
        use_terraform = source.source_type == "terraform_registry"
        versions = self.get_module_versions(ns, repo_name, prov, use_terraform)
        latest = _pick_latest_version(versions, fallback=module.source_version)

        module.latest_version = latest
        module.update_available = module.source_version != latest

        return {
            "current_version": module.source_version,
            "latest_version": latest,
            "update_available": module.update_available,
            "versions": versions,
        }

    def upgrade_module(self, module_id: int, target_version: str | None = None) -> ModuleLibrary:
        module = self.db.query(ModuleLibrary).filter(ModuleLibrary.id == module_id).first()
        if not module:
            raise ValueError(f"Module {module_id} not found")
        source = module.source
        if not source or source.source_type not in {"terraform_registry", "opentofu_registry", "tfc_private"}:
            raise ValueError("Can only upgrade registry-tracked modules")

        if not target_version:
            info = self.check_for_updates(module_id)
            target_version = info["latest_version"]

        ns, repo_name, prov = _split_registry_path(module.path)
        if source.source_type == "terraform_registry":
            return self.import_module_from_registry(ns, repo_name, prov, target_version, use_terraform_registry=True)
        if source.source_type == "opentofu_registry":
            return self.import_module_from_registry(ns, repo_name, prov, target_version, use_terraform_registry=False)
        # tfc_private — caller must re-pass credential template; for now reject.
        raise ValueError("TFC private upgrades require re-import via /api/registry/import-tfc")


def _semver_key(version: str) -> tuple:
    """Sort key: delegates to the shared catalog ordering (D-033).

    One definition of "latest" for the whole backend — the previous local key
    ranked '2.3.0-pre' ABOVE '2.3.0' (raw-string tiebreak), disagreeing with
    the module-catalog is_latest recompute.
    """
    return version_sort_key(version)


def _pick_latest_version(versions: list[dict], *, fallback: str | None = None) -> str:
    """Choose the highest semver from a registry versions response.

    Different registries return ascending or descending lists; sort explicitly
    so we don't depend on order. Falls back to the registry's `version` field on
    the module-details response (which usually points at the latest), and
    finally to the literal "latest" if neither is available.
    """
    candidates = [v.get("version") for v in versions or [] if v.get("version")]
    if candidates:
        candidates.sort(key=_semver_key)
        return candidates[-1]
    if fallback:
        return fallback
    return "latest"


def _split_registry_path(path: str | None) -> tuple[str, str, str]:
    """Parse 'registry/<short>/<ns>/<name>' produced by _finalize_import."""
    if not path:
        raise ValueError("Module has no catalog path")
    parts = path.split("/")
    if len(parts) >= 4 and parts[0] == "registry":
        return parts[2], parts[3], parts[3]  # (namespace, name, provider) — provider stored on row
    # Legacy 'namespace/name/provider' shape
    if len(parts) == 3:
        return tuple(parts)  # type: ignore[return-value]
    raise ValueError(f"Unrecognized catalog path: {path!r}")


def _derive_git_identity(url: str, subpath: str | None, display_name: str | None) -> tuple[str, str]:
    """Pick a (namespace, name) pair for a public-git module so on-disk paths are stable."""
    cleaned = url.rstrip("/")
    if cleaned.endswith(".git"):
        cleaned = cleaned[: -len(".git")]
    parts = [p for p in cleaned.split("/") if p]
    if len(parts) >= 2:
        repo_name = parts[-1]
        namespace = parts[-2]
    else:
        repo_name = display_name or "repo"
        namespace = "git"
    if subpath:
        repo_name = f"{repo_name}-{Path(subpath).name}"
    return namespace, (display_name or repo_name)
