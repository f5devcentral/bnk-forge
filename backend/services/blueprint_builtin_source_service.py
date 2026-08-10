"""Startup seeding for the builtin blueprint catalog source."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy.sql import func

from models import BlueprintRelease, BlueprintSource
from models.enums import BlueprintReleaseState
from schemas.blueprints import BlueprintManifest
from services.blueprint_catalog_service import BlueprintCatalogService

logger = logging.getLogger(__name__)

_BUILTIN_SOURCE_NAME = "builtin-blueprints"
_BUILTIN_BLUEPRINTS_DIR = Path(__file__).parent.parent / "data" / "blueprints"
_BLUEPRINT_MANIFEST_FILENAME = "forge-blueprint.json"


def ensure_default_builtin_blueprint_source(db: Session) -> BlueprintSource:
    """Ensure a single active builtin BlueprintSource row exists. Idempotent."""
    existing = (
        db.query(BlueprintSource)
        .filter(BlueprintSource.source_type == "builtin")
        .filter(BlueprintSource.name == _BUILTIN_SOURCE_NAME)
        .first()
    )
    if existing:
        if not existing.is_active:
            existing.is_active = True
            db.flush()
        return existing

    source = BlueprintSource(
        name=_BUILTIN_SOURCE_NAME,
        source_type="builtin",
        url="builtin://bundled",
        branch=None,
        git_ref=None,
        sync_status="pending",
        sync_error=None,
        is_active=True,
        description="Bundled builtin blueprint catalog (shipped with BNK Forge)",
    )
    db.add(source)
    db.flush()
    logger.info("Created builtin blueprint source: %s", _BUILTIN_SOURCE_NAME)
    return source


def sync_builtin_source(db: Session, source: BlueprintSource) -> dict[str, Any]:
    """Scan bundled blueprint manifests and upsert BlueprintRelease rows.

    State choices:
    - release_state=IMPORTED: builtins are already approved for use; they skip
      the DISCOVERED staging state used by git sync (where human review is needed).
    - validation_state=VALID: manifests are validated via BlueprintManifest before
      upsert; only valid manifests are persisted.
    - is_active=True: builtins are immediately visible on the /stacks page which
      filters for imported + valid + active releases.

    Immutability: existing releases whose content_sha256 matches are skipped
    (idempotent). An existing release with a different sha256 for the same
    id+version is treated as a conflict and logged (no overwrite, to preserve
    the structural-immutability contract on BlueprintRelease).
    """
    results: dict[str, Any] = {
        "blueprints_found": 0,
        "releases_created": 0,
        "releases_existing": 0,
        "releases_conflicted": 0,
        "releases_invalid": 0,
        "errors": [],
    }

    manifests = _discover_builtin_manifests()
    results["blueprints_found"] = len(manifests)

    catalog_service = BlueprintCatalogService(db)

    for manifest_path, manifest_dict in manifests:
        try:
            blueprint = manifest_dict.get("blueprint") or {}
            blueprint_id = str(blueprint.get("id") or "").strip()
            blueprint_version = str(blueprint.get("version") or "").strip()
            if not blueprint_id or not blueprint_version:
                raise ValueError("Manifest missing blueprint.id or blueprint.version")

            # Normalize via the schema so the sha256 matches what create_release stores.
            # create_release calls validate_blueprint_manifest → model_dump(mode="json")
            # before computing content_sha256, so we must use the same normalized form.
            validated = BlueprintManifest.model_validate(manifest_dict)
            normalized_dict = validated.model_dump(mode="json")
            content_sha = _content_sha256(normalized_dict)

            existing = (
                db.query(BlueprintRelease)
                .filter(BlueprintRelease.blueprint_source_id == source.id)
                .filter(BlueprintRelease.blueprint_id == blueprint_id)
                .filter(BlueprintRelease.blueprint_version == blueprint_version)
                .first()
            )

            if existing is not None:
                if existing.content_sha256 == content_sha:
                    results["releases_existing"] += 1
                    continue

                results["releases_conflicted"] += 1
                results["errors"].append(
                    f"{manifest_path}: builtin release already exists with different content "
                    f"for {blueprint_id}@{blueprint_version} — skipping"
                )
                continue

            catalog_service.create_release(
                SimpleNamespace(
                    blueprint_source_id=source.id,
                    manifest=normalized_dict,
                    source_path=manifest_path,
                    source_ref=None,
                    release_state=BlueprintReleaseState.IMPORTED.value,
                    state_reason="bundled builtin",
                    is_active=True,
                )
            )
            results["releases_created"] += 1

        except Exception as exc:
            logger.warning("Builtin blueprint sync skipped %s: %s", manifest_path, exc)
            results["errors"].append(f"{manifest_path}: {exc}")
            results["releases_invalid"] += 1

    source.sync_status = "success"
    source.sync_error = None if results["blueprints_found"] > 0 else "No forge-blueprint.json manifests found"
    source.last_synced_at = func.now()
    source.release_count = (
        db.query(BlueprintRelease)
        .filter(BlueprintRelease.blueprint_source_id == source.id)
        .filter(BlueprintRelease.is_active)
        .count()
    )
    db.flush()

    logger.info(
        "Builtin blueprint sync complete: found=%d created=%d existing=%d errors=%d",
        results["blueprints_found"],
        results["releases_created"],
        results["releases_existing"],
        len(results["errors"]),
    )
    return results


def _discover_builtin_manifests() -> list[tuple[str, dict[str, Any]]]:
    """Return list of (relative_path, manifest_dict) for all bundled blueprints."""
    discovered: list[tuple[str, dict[str, Any]]] = []

    if not _BUILTIN_BLUEPRINTS_DIR.exists():
        logger.warning("Builtin blueprints directory not found: %s", _BUILTIN_BLUEPRINTS_DIR)
        return discovered

    for manifest_file in sorted(_BUILTIN_BLUEPRINTS_DIR.rglob(_BLUEPRINT_MANIFEST_FILENAME)):
        rel_path = str(manifest_file.relative_to(_BUILTIN_BLUEPRINTS_DIR.parent.parent))
        try:
            with manifest_file.open(encoding="utf-8") as f:
                manifest_dict = json.load(f)
            discovered.append((rel_path, manifest_dict))
        except Exception as exc:
            logger.warning("Failed to load builtin manifest %s: %s", rel_path, exc)

    return discovered


def _content_sha256(manifest: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
