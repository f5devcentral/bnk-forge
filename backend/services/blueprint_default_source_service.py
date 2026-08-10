"""Startup seeding for the default blueprint catalog source."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from models import BlueprintSource
from services.defaults_service import get_default, set_default

logger = logging.getLogger(__name__)


def _build_official_blueprint_source_name(repo_name: str) -> str:
    return f"official-{repo_name}-blueprints"


def ensure_default_blueprint_source(db: Session) -> BlueprintSource | None:
    """Create or update the canonical default blueprint source from system defaults."""
    git_url = get_default(db, "blueprint_library.git_url")
    git_ref = get_default(db, "blueprint_library.git_ref") or "main"

    if not git_url:
        logger.info("Default blueprint source not configured; skipping blueprint source seed")
        return None

    clean_url = str(git_url).strip()
    repo_name = clean_url.rstrip("/").split("/")[-1]
    if repo_name.endswith(".git"):
        repo_name = repo_name[:-4]

    existing = db.query(BlueprintSource).filter(
        BlueprintSource.source_type == "git",
        BlueprintSource.url == clean_url,
        BlueprintSource.is_active,
    ).first()
    if existing:
        existing.branch = git_ref
        existing.git_ref = git_ref
        existing.sync_error = None
        db.flush()
        return existing

    base_name = _build_official_blueprint_source_name(repo_name)
    candidate_name = base_name
    suffix = 2
    while db.query(BlueprintSource).filter(BlueprintSource.name == candidate_name).first() is not None:
        candidate_name = f"{base_name}-{suffix}"
        suffix += 1

    source = BlueprintSource(
        name=candidate_name,
        source_type="git",
        url=clean_url,
        branch=git_ref,
        git_ref=git_ref,
        sync_status="pending",
        sync_error=None,
        is_active=True,
        description="Canonical official blueprint catalog source",
    )
    db.add(source)
    db.flush()
    return source


def is_default_blueprint_source(source: BlueprintSource, db: Session) -> bool:
    """Whether the given source matches the configured canonical default blueprint source."""
    git_url = get_default(db, "blueprint_library.git_url")
    git_ref = get_default(db, "blueprint_library.git_ref") or "main"
    if not git_url:
        return False

    return (
        source.source_type == "git"
        and source.url == str(git_url).strip()
        and (source.git_ref or source.branch or "main") == git_ref
    )


def set_default_blueprint_source(source: BlueprintSource, db: Session) -> None:
    """Point system defaults at the selected blueprint source."""
    if source.source_type != "git":
        raise ValueError("Only git blueprint sources can be configured as default")

    git_ref = source.git_ref or source.branch or "main"
    set_default(db, "blueprint_library.git_url", source.url)
    set_default(db, "blueprint_library.git_ref", git_ref)
