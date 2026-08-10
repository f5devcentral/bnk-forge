"""Git sync for Blueprint Catalog sources."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy.sql import func

from models import BlueprintRelease, BlueprintSource, ModuleSource
from services.audit_service import create_audit_log
from services.blueprint_catalog_common import _resolve_category
from services.blueprint_catalog_service import BlueprintCatalogService
from services.blueprint_manifest_service import (
    BlueprintManifestValidationError,
    validate_blueprint_manifest,
)
from services.git_auth_service import GitAuthService
from services.module_sync_service import ModuleSyncService
from utils.catalog_versioning import canonical_json_sha256

logger = logging.getLogger(__name__)


class BlueprintSyncService:
    """Service for discovering and importing blueprint manifests from Git sources."""

    BLUEPRINT_MANIFEST_FILENAME = "forge-blueprint.json"
    TRANSITION_RELEASE_MANIFEST_GLOB = os.path.join("catalog", "releases", "release-*.json")

    def __init__(self, db: Session):
        self.db = db

    def sync_git_source(self, source: BlueprintSource, *, sync_related_modules: bool = True) -> dict[str, Any]:
        if source.source_type != "git":
            raise ValueError(f"Source {source.name} is not a Git source")

        source.sync_status = "syncing"
        source.sync_error = None
        self.db.commit()

        temp_dir: str | None = None
        results = {
            "blueprints_found": 0,
            "releases_created": 0,
            "releases_existing": 0,
            "releases_conflicted": 0,
            "releases_invalid": 0,
            "module_auto_sync": None,
            "errors": [],
        }

        try:
            temp_dir = self._clone_repository(source)
            discovered_blueprints = self._discover_blueprint_manifests(temp_dir, source)
            results["blueprints_found"] = len(discovered_blueprints)

            catalog_service = BlueprintCatalogService(self.db)
            source_ref = source.git_ref or (f"refs/heads/{source.branch}" if source.branch else None)

            for manifest_path, manifest in discovered_blueprints:
                try:
                    blueprint_id, blueprint_version = self._manifest_identity(manifest)
                    if not blueprint_id or not blueprint_version:
                        raise ValueError(
                            "Blueprint manifest must include non-empty blueprint.id and blueprint.version"
                        )

                    manifest_sha = self._content_sha256(self._canonical_manifest(manifest))
                    existing = (
                        self.db.query(BlueprintRelease)
                        .filter(BlueprintRelease.blueprint_source_id == source.id)
                        .filter(BlueprintRelease.blueprint_id == blueprint_id)
                        .filter(BlueprintRelease.blueprint_version == blueprint_version)
                        .first()
                    )

                    if existing is not None:
                        if existing.content_sha256 == manifest_sha:
                            results["releases_existing"] += 1
                            continue

                        results["releases_conflicted"] += 1
                        results["errors"].append(
                            f"{manifest_path}: Blueprint release already exists with different content for {blueprint_id}@{blueprint_version}"
                        )
                        continue

                    created = catalog_service.create_release(
                        SimpleNamespace(
                            blueprint_source_id=source.id,
                            manifest=manifest,
                            source_path=manifest_path,
                            source_ref=source_ref,
                            release_state="discovered",
                            state_reason="discovered from git source sync",
                            is_active=False,
                        )
                    )
                    results["releases_created"] += 1
                    if created.get("validation_state") != "valid":
                        results["releases_invalid"] += 1
                except Exception as e:
                    logger.warning("Blueprint sync skipped manifest %s: %s", manifest_path, e)
                    results["errors"].append(f"{manifest_path}: {e}")

            source.sync_status = "success"
            source.sync_error = None if results["blueprints_found"] > 0 else "No forge-blueprint.json manifests found"
            source.last_synced_at = func.now()
            source.release_count = self._count_active_releases_for_source(source.id)
            self.db.commit()

            if sync_related_modules:
                try:
                    # ModuleSyncService.sync_git_source owns its session and calls
                    # self.db.commit() internally, so this must NOT run inside a
                    # SAVEPOINT: the callee's first commit() closes the nested
                    # transaction, wedging the session (PendingRollbackError on the
                    # next commit) while still persisting partial rows mid-flight
                    # (#428). Instead the blueprint releases and source status are
                    # committed above, BEFORE auto-sync — mirroring the reverse
                    # path in module_source_service — so a DB-level failure here
                    # (e.g. an IntegrityError racing on module_sources.name) can
                    # no longer roll them back; the rollback() below only resets
                    # the session for the audit log.
                    results["module_auto_sync"] = self._auto_sync_modules_for_git_source(source, temp_dir)
                    self.db.commit()
                except Exception as exc:
                    if not self.db.is_active:
                        # A DB error aborted the transaction — reset the session
                        # so the audit log below can still be written.
                        self.db.rollback()
                    logger.warning("Module auto-sync failed for blueprint source %s: %s", source.name, exc)
                    results["module_auto_sync"] = {
                        "sync_status": "failed",
                        "error": str(exc)[:300],
                    }

            create_audit_log(
                self.db,
                action="blueprint_source_sync_succeeded",
                resource_type="blueprint_source",
                resource_id=str(source.id),
                resource_name=source.name,
                status="success",
                details={
                    "source_type": source.source_type,
                    "blueprints_found": results["blueprints_found"],
                    "releases_created": results["releases_created"],
                    "releases_existing": results["releases_existing"],
                    "releases_conflicted": results["releases_conflicted"],
                    "releases_invalid": results["releases_invalid"],
                    "module_auto_sync": (
                        {
                            "source_id": results["module_auto_sync"].get("source_id"),
                            "created": results["module_auto_sync"].get("created"),
                            "sync_status": results["module_auto_sync"].get("sync_status"),
                        }
                        if isinstance(results["module_auto_sync"], dict)
                        else None
                    ),
                },
            )
            return results
        except Exception as e:
            logger.error("Blueprint sync failed for source %s: %s", source.name, e)
            source.sync_status = "failed"
            source.sync_error = f"Sync failed: {e}"
            self.db.commit()
            create_audit_log(
                self.db,
                action="blueprint_source_sync_failed",
                resource_type="blueprint_source",
                resource_id=str(source.id),
                resource_name=source.name,
                status="failed",
                details={
                    "source_type": source.source_type,
                    "error": str(e)[:300],
                },
            )
            raise
        finally:
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)

    def _clone_repository(self, source: BlueprintSource) -> str:
        module_like_source = SimpleNamespace(
            id=source.id,
            name=source.name,
            url=source.url,
            branch=source.branch,
            git_ref=source.git_ref,
            auth_type=None,
            credential_type=None,
            auth_token_encrypted=None,
            credential_metadata=None,
        )
        return ModuleSyncService(self.db)._clone_repository(module_like_source)

    def _auto_sync_modules_for_git_source(self, source: BlueprintSource, repo_path: str) -> dict[str, Any] | None:
        module_sync = ModuleSyncService(self.db)
        pack_paths = module_sync._find_pack_modules(repo_path)
        if not pack_paths:
            return None

        # An IntegrityError racing on the unique module_sources.name (two syncs
        # creating the twin concurrently) aborts the whole transaction at
        # flush(); a SAVEPOINT scopes that to the insert attempt so the session
        # stays usable. The module sync below must stay OUTSIDE the SAVEPOINT:
        # it commits internally, and a commit() inside begin_nested() closes
        # the nested transaction and wedges the session (#428).
        with self.db.begin_nested():
            module_source, created = self._get_or_create_linked_module_source(source)
        if not module_source.is_active:
            logger.info(
                "Skipping module auto-sync for blueprint source %s: linked module source %s is inactive",
                source.name,
                module_source.name,
            )
            return {
                "source_id": module_source.id,
                "source_name": module_source.name,
                "created": created,
                "sync_status": "skipped_inactive",
                "results": None,
            }

        # This intentionally performs a fresh clone inside ModuleSyncService.sync_git_source.
        # We only use the blueprint clone for pack detection here.
        results = module_sync.sync_git_source(module_source)
        self.db.refresh(module_source)
        return {
            "source_id": module_source.id,
            "source_name": module_source.name,
            "created": created,
            "sync_status": module_source.sync_status,
            "results": results,
        }

    def _get_or_create_linked_module_source(self, source: BlueprintSource) -> tuple[ModuleSource, bool]:
        source_key = self._source_key(source.url, source.branch, source.git_ref)
        existing = (
            self.db.query(ModuleSource)
            .filter(ModuleSource.source_type == "git")
            .order_by(ModuleSource.id.asc())
            .all()
        )
        for module_source in existing:
            if self._source_key(module_source.url, module_source.branch, module_source.git_ref) == source_key:
                return module_source, False

        created = ModuleSource(
            name=self._linked_module_source_name(source.name),
            source_type="git",
            url=source.url,
            branch=source.branch,
            git_ref=source.git_ref,
            is_active=source.is_active,
            description=f"Auto-created from blueprint source '{source.name}'",
            sync_status="pending",
        )
        self.db.add(created)
        self.db.flush()
        self.db.refresh(created)
        return created, True

    @staticmethod
    def _source_key(url: str | None, branch: str | None, git_ref: str | None) -> tuple[str, str]:
        normalized_url = GitAuthService.strip_url_credentials((url or "").strip()).rstrip("/").removesuffix(".git")
        normalized_ref = (git_ref or branch or "main").strip() or "main"
        return normalized_url, normalized_ref

    def _linked_module_source_name(self, blueprint_source_name: str) -> str:
        base_name = f"{blueprint_source_name} modules"
        candidate = base_name
        suffix = 2
        while True:
            exists = self.db.query(ModuleSource).filter(ModuleSource.name == candidate).first()
            if exists is None:
                return candidate
            candidate = f"{base_name} {suffix}"
            suffix += 1

    def _find_blueprint_manifests(self, repo_path: str) -> list[str]:
        manifests: list[str] = []

        for root, _dirs, files in os.walk(repo_path):
            if ".git" in root:
                continue

            if self.BLUEPRINT_MANIFEST_FILENAME not in files:
                continue

            rel_path = os.path.relpath(os.path.join(root, self.BLUEPRINT_MANIFEST_FILENAME), repo_path)
            manifests.append(rel_path if rel_path != "." else self.BLUEPRINT_MANIFEST_FILENAME)

        manifests.sort()
        return manifests

    def _discover_blueprint_manifests(self, repo_path: str, source: BlueprintSource) -> list[tuple[str, dict[str, Any]]]:
        discovered: list[tuple[str, dict[str, Any]]] = []

        for manifest_path in self._find_blueprint_manifests(repo_path):
            discovered.append((manifest_path, self._load_manifest(repo_path, manifest_path)))

        if discovered:
            return discovered

        for manifest_path in self._find_transition_release_manifests(repo_path):
            transition_manifests = self._load_transition_release_manifests(repo_path, manifest_path, source)
            discovered.extend(transition_manifests)

        return discovered

    def _find_transition_release_manifests(self, repo_path: str) -> list[str]:
        matches: list[str] = []
        catalog_root = Path(repo_path) / "catalog" / "releases"
        if not catalog_root.exists():
            return matches

        for candidate in sorted(catalog_root.glob("release-*.json")):
            if candidate.is_file():
                matches.append(os.path.relpath(candidate, repo_path))
        return matches

    def _load_transition_release_manifests(
        self,
        repo_path: str,
        manifest_path: str,
        source: BlueprintSource,
    ) -> list[tuple[str, dict[str, Any]]]:
        full_path = Path(repo_path) / manifest_path
        with full_path.open(encoding="utf-8") as f:
            raw_release = json.load(f)

        official_modules = raw_release.get("official_modules") or []
        transition_manifests: list[tuple[str, dict[str, Any]]] = []
        release_meta = raw_release.get("release") or {}
        release_channel = str(release_meta.get("channel") or source.git_ref or source.branch or "main")
        release_name = str(release_meta.get("name") or "Official Blueprint Release")

        for module_entry in official_modules:
            if not isinstance(module_entry, dict):
                continue

            path = str(module_entry.get("path") or "").strip()
            state = str(module_entry.get("state") or "").strip().lower()
            if not path or state not in {"active", "deprecated"}:
                continue

            module_readme_metadata = self._load_module_readme_metadata(repo_path, path)
            blueprint_manifest = self._build_transition_blueprint_manifest(
                path=path,
                state=state,
                release_channel=release_channel,
                release_name=release_name,
                source_manifest_path=manifest_path,
                readme_metadata=module_readme_metadata,
                module_entry=module_entry,
            )
            synthetic_path = f"{manifest_path}#{path}"
            transition_manifests.append((synthetic_path, blueprint_manifest))

        return transition_manifests

    def _load_module_readme_metadata(self, repo_path: str, module_path: str) -> dict[str, Any]:
        readme_path = Path(repo_path) / module_path / "README.md"
        if not readme_path.exists():
            return {}
        return self._parse_blueprint_readme(readme_path.read_text(encoding="utf-8"))

    def _build_transition_blueprint_manifest(
        self,
        *,
        path: str,
        state: str,
        release_channel: str,
        release_name: str,
        source_manifest_path: str,
        readme_metadata: dict[str, Any],
        module_entry: dict[str, Any],
    ) -> dict[str, Any]:
        segments = [segment for segment in path.split("/") if segment]
        provider = self._infer_provider_from_path(path)
        blueprint_id = ".".join([release_channel.replace("/", "-"), *segments])
        display_name = self._display_name_from_path(path)
        description = (
            readme_metadata.get("description")
            or module_entry.get("reason")
            or f"Transition blueprint generated from module path '{path}' in {release_name}."
        )
        tags = [segments[0].lower()] if segments else []
        tags.extend(segment.lower() for segment in segments[1:])

        manifest: dict[str, Any] = {
            "schema_version": 1,
            "blueprint": {
                "id": blueprint_id,
                "version": release_channel,
                "name": display_name,
                "description": description,
            },
            "category": _resolve_category(None),
            "cloud_provider": provider,
            "tags": tags,
            "modules": [
                {
                    "id": segments[-1] if segments else path.replace("/", "-"),
                    "name": display_name,
                    "module": path,
                    "depends_on": [],
                    "inputs": {},
                    "description": description,
                }
            ],
            "compatibility": {
                "supported_platform_profiles": self._supported_platform_profiles_for_path(path, provider),
                "required_capabilities": [],
            },
            "inputs": {"required": [], "optional": []},
            "outcomes": readme_metadata.get("outcomes") or [],
            "prerequisites": readme_metadata.get("prerequisites") or [],
            "input_summary": [{"label": "Transition source", "value": source_manifest_path}],
            "maturity": "reference" if state == "active" else "legacy",
        }

        return manifest

    def _infer_provider_from_path(self, path: str) -> str:
        lowered = path.lower()
        if lowered.startswith("infra/aws"):
            return "aws"
        if lowered.startswith("infra/azure"):
            return "azure"
        if lowered.startswith("infra/gcp"):
            return "gcp"
        if lowered.startswith("infra/ibm") or lowered.startswith("bnk/") or lowered.startswith("k8s/"):
            return "ibm"
        return "other"

    def _supported_platform_profiles_for_path(self, path: str, provider: str) -> list[str]:
        lowered = path.lower()
        if provider == "ibm" and (lowered.startswith("bnk/") or lowered.startswith("k8s/")):
            return ["ibm_roks"]
        if provider == "aws":
            return ["aws_eks"]
        return []

    def _display_name_from_path(self, path: str) -> str:
        segment = path.split("/")[-1]
        return segment.replace("-", " ").replace("_", " ").title()

    def _load_manifest(self, repo_path: str, manifest_path: str) -> dict[str, Any]:
        full_path = Path(repo_path) / manifest_path
        with full_path.open(encoding="utf-8") as f:
            manifest = json.load(f)

        readme_metadata = self._load_readme_metadata(repo_path, manifest_path)
        if readme_metadata:
            manifest = self._merge_readme_metadata(manifest, readme_metadata)

        return manifest

    def _load_readme_metadata(self, repo_path: str, manifest_path: str) -> dict[str, Any]:
        manifest_dir = (Path(repo_path) / manifest_path).parent
        readme_path = manifest_dir / "README.md"
        if not readme_path.exists():
            return {}

        readme_text = readme_path.read_text(encoding="utf-8")
        return self._parse_blueprint_readme(readme_text)

    def _parse_blueprint_readme(self, readme_text: str) -> dict[str, Any]:
        sections: dict[str, str] = {}
        current_heading: str | None = None
        current_lines: list[str] = []

        for line in readme_text.splitlines():
            heading_match = re.match(r"^##\s+(.*)$", line.strip())
            if heading_match:
                if current_heading is not None:
                    sections[current_heading] = "\n".join(current_lines).strip()
                current_heading = heading_match.group(1).strip().lower()
                current_lines = []
                continue

            if current_heading is not None:
                current_lines.append(line)

        if current_heading is not None:
            sections[current_heading] = "\n".join(current_lines).strip()

        return {
            "description": self._first_paragraph(sections.get("solution description", "")),
            "outcomes": self._extract_bullets(sections.get("what you get", "")),
            "prerequisites": self._extract_prerequisites(sections.get("prerequisites", "")),
            "module_descriptions": self._extract_module_descriptions(sections.get("modules", "")),
            "input_notes": self._first_paragraph(sections.get("input variables", "")),
        }

    def _merge_readme_metadata(self, manifest: dict[str, Any], readme_metadata: dict[str, Any]) -> dict[str, Any]:
        merged = dict(manifest)
        blueprint = dict(merged.get("blueprint") or {})
        if not blueprint.get("description") and readme_metadata.get("description"):
            blueprint["description"] = readme_metadata["description"]
        merged["blueprint"] = blueprint

        if not merged.get("outcomes") and readme_metadata.get("outcomes"):
            merged["outcomes"] = readme_metadata["outcomes"]

        if not merged.get("prerequisites") and readme_metadata.get("prerequisites"):
            merged["prerequisites"] = readme_metadata["prerequisites"]

        modules = merged.get("modules") or []
        module_descriptions = readme_metadata.get("module_descriptions") or {}
        if modules and module_descriptions:
            enriched_modules = []
            for module in modules:
                module_copy = dict(module)
                lookup_key = str(module_copy.get("name") or module_copy.get("id") or "").strip().lower()
                if lookup_key in module_descriptions and not module_copy.get("description"):
                    module_copy["description"] = module_descriptions[lookup_key]
                enriched_modules.append(module_copy)
            merged["modules"] = enriched_modules

        input_notes = readme_metadata.get("input_notes")
        if input_notes:
            summary = list(merged.get("input_summary") or [])
            summary.append({"label": "Input guidance", "value": input_notes})
            merged["input_summary"] = summary

        return merged

    def _first_paragraph(self, value: str) -> str:
        parts = [part.strip() for part in value.split("\n\n") if part.strip()]
        return parts[0] if parts else ""

    def _extract_bullets(self, value: str) -> list[str]:
        return [line.strip()[2:].strip() for line in value.splitlines() if line.strip().startswith("- ")]

    def _extract_prerequisites(self, value: str) -> list[dict[str, str]]:
        bullets = self._extract_bullets(value)
        return [{"type": "requirement", "description": bullet} for bullet in bullets]

    def _extract_module_descriptions(self, value: str) -> dict[str, str]:
        sections: dict[str, str] = {}
        current_module: str | None = None
        current_lines: list[str] = []

        for line in value.splitlines():
            match = re.match(r"^###\s+(.*)$", line.strip())
            if match:
                if current_module is not None:
                    sections[current_module] = " ".join(part.strip() for part in current_lines if part.strip()).strip()
                current_module = match.group(1).strip().lower()
                current_lines = []
                continue

            if current_module is not None:
                current_lines.append(line)

        if current_module is not None:
            sections[current_module] = " ".join(part.strip() for part in current_lines if part.strip()).strip()

        return sections

    def _manifest_identity(self, manifest: dict[str, Any]) -> tuple[str | None, str | None]:
        blueprint = manifest.get("blueprint") if isinstance(manifest, dict) else None
        if not isinstance(blueprint, dict):
            return None, None

        blueprint_id = blueprint.get("id")
        blueprint_version = blueprint.get("version")
        if not isinstance(blueprint_id, str) or not blueprint_id.strip():
            blueprint_id = None
        if not isinstance(blueprint_version, str) or not blueprint_version.strip():
            blueprint_version = None

        return blueprint_id, blueprint_version

    def _canonical_manifest(self, manifest: dict[str, Any]) -> dict[str, Any]:
        """Return the manifest in the representation create_release stores.

        BlueprintCatalogService.create_release computes content_sha256 over the
        Pydantic-normalized manifest (model_dump of the validated model) for
        valid manifests, and over the raw manifest otherwise. Hashing a
        different representation here made every re-sync of identical content
        compare a raw-manifest hash against a normalized-manifest hash and
        report a false conflict (#430).
        """
        try:
            return validate_blueprint_manifest(manifest).model_dump(mode="json")
        except BlueprintManifestValidationError:
            return manifest

    def _content_sha256(self, manifest: dict[str, Any]) -> str:
        # Shared helper: one canonicalization for module + blueprint hashing (D-033).
        return canonical_json_sha256(manifest)

    def _count_active_releases_for_source(self, source_id: int) -> int:
        return (
            self.db.query(BlueprintRelease)
            .filter(BlueprintRelease.blueprint_source_id == source_id)
            .filter(BlueprintRelease.is_active)
            .count()
        )
