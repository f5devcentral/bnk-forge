"""ReleaseSource CRUD and sync service (ADR-494)."""

import logging
from datetime import UTC, datetime

from packaging.version import InvalidVersion, Version
from sqlalchemy.orm import Session

from core.encryption import encrypt_value
from core.errors import ConflictError, DecryptionError, NotFoundError
from models.bnk_deployable_release import BnkDeployableRelease
from models.release_source import ReleaseSource
from schemas.release_source import (
    FailedTag,
    PullTagsSummary,
    ReleaseSourceCreate,
    ReleaseSourceResponse,
    ReleaseSourceTag,
    ReleaseSourceTagList,
    ReleaseSourceUpdate,
)
from services.bare_metal.release_source_oci import registry_session  # module-level for patchability

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tag annotation helpers (module-level for unit-testability)
# ---------------------------------------------------------------------------


def _base_version(tag: str) -> "Version":
    """Extract the leading x.y.z version from an OCI tag (e.g. "2.2.1-3.2226.0-0.0.511" → Version("2.2.1")).

    Returns Version("0.0.0") for unparse-able tags so sort order is stable.
    """
    base = tag.split("-")[0]
    try:
        return Version(base)
    except InvalidVersion:
        return Version("0.0.0")


def _is_prerelease(tag: str) -> bool:
    """Return True if the tag is a prerelease/dev build.

    Real F5 tag grammar (confirmed from live oras repo tags repo.f5.com):
      - Stable builds: the FIRST hyphen-segment after x.y.z starts with a DIGIT
        (e.g. "2.2.1-3.2226.0-0.0.511", "2.4.0-3.2981.1-release-version.17861144").
      - Dev/prerelease: that first segment starts with a LETTER
        (e.g. "2.4.0-laiq", "2.4.0-rc.1", "2.1.0-ready-prod.15573925").

    Also handles PEP 440 pre-release bases (alpha, beta, rc) via packaging.version.
    """
    if _base_version(tag).is_prerelease:
        return True
    if "-" not in tag:
        return False
    first_post = tag.split("-")[1]
    return bool(first_post) and not first_post[0].isdigit()


class ReleaseSourceService:
    """CRUD operations and sync for first-class BNK release sources (ADR-494)."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def list_sources(self, *, active_only: bool = False) -> list[ReleaseSource]:
        """Return all release sources, optionally filtered to active only."""
        query = self.db.query(ReleaseSource)
        if active_only:
            query = query.filter(ReleaseSource.is_active.is_(True))
        return query.order_by(ReleaseSource.name).all()

    def get_source(self, source_id: int) -> ReleaseSource:
        """Return a single release source by id; raise NotFoundError if absent."""
        source = self.db.query(ReleaseSource).filter(ReleaseSource.id == source_id).first()
        if source is None:
            raise NotFoundError("release_source", source_id)
        return source

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def create_source(self, data: ReleaseSourceCreate) -> ReleaseSource:
        """Create a new release source. Credential is encrypted before storage."""
        existing = self.db.query(ReleaseSource).filter(ReleaseSource.name == data.name).first()
        if existing:
            raise ConflictError("release_source", f"Release source '{data.name}' already exists")

        credential_encrypted = encrypt_value(data.credential) if data.credential else None

        source = ReleaseSource(
            name=data.name,
            kind=data.kind,
            url=data.url,
            credential_encrypted=credential_encrypted,
            is_active=data.is_active,
            auto_sync=data.auto_sync,
            sync_interval_hours=data.sync_interval_hours,
            description=data.description,
        )
        self.db.add(source)
        self.db.flush()
        return source

    def update_source(self, source_id: int, data: ReleaseSourceUpdate) -> ReleaseSource:
        """Partial update. Re-encrypts credential only if a new value is provided; clears it if set to None explicitly."""
        source = self.get_source(source_id)

        updated_fields = data.model_dump(exclude_unset=True)

        # credential is never stored as plaintext — handle it separately
        if "credential" in updated_fields:
            cred = updated_fields.pop("credential")
            source.credential_encrypted = encrypt_value(cred) if cred else None

        # unique-name check if the name is changing
        new_name = updated_fields.get("name")
        if new_name is not None and new_name != source.name:
            conflict = self.db.query(ReleaseSource).filter(ReleaseSource.name == new_name).first()
            if conflict:
                raise ConflictError("release_source", f"Release source '{new_name}' already exists")

        for field, value in updated_fields.items():
            setattr(source, field, value)

        self.db.flush()
        return source

    def delete_source(self, source_id: int) -> None:
        """Delete a release source.

        Catalog rows keep their source_id → NULL via FK ON DELETE SET NULL;
        no cascade delete of catalog rows is performed.
        """
        source = self.get_source(source_id)
        self.db.delete(source)
        self.db.flush()

    # ------------------------------------------------------------------
    # Sync
    # ------------------------------------------------------------------

    def sync_source(self, source_id: int, manifest_yaml: str) -> dict[str, int]:
        """Sync catalog releases from a manifest YAML.

        Sets sync_status="syncing", calls DeployableReleaseRefreshService with
        source_id stamping, then updates last_synced_at / release_count /
        sync_status on the source row.

        The refresh runs inside a SAVEPOINT (begin_nested) so that any DB error
        during the catalog flush (e.g. IntegrityError) is isolated: only the
        savepoint is rolled back, the outer session remains clean, and the
        original exception is re-raised without being masked by a secondary
        PendingRollbackError.  The "syncing" flush outside the savepoint is
        preserved on success or overwritten with "error" on failure.
        """
        source = self.get_source(source_id)
        source.sync_status = "syncing"
        self.db.flush()

        try:
            from services.bare_metal.deployable_release_refresh import DeployableReleaseRefreshService

            with self.db.begin_nested():
                result = DeployableReleaseRefreshService(self.db).refresh_deployable_releases_from_oci(
                    manifest_yaml, source_id=source_id
                )
        except Exception as exc:
            # The savepoint was automatically rolled back when the nested block
            # exited with an exception.  The session is clean; the source object
            # is still valid (its "syncing" update is outside the savepoint).
            logger.error("sync_source: manifest sync failed for source %d: %s", source_id, exc)
            source.sync_status = "error"
            source.sync_error = "Manifest sync failed"
            self.db.flush()
            raise

        now = datetime.now(UTC)
        source.last_synced_at = now
        source.sync_status = "success"
        source.sync_error = None
        source.release_count = (
            self.db.query(BnkDeployableRelease)
            .filter(BnkDeployableRelease.source_id == source_id)
            .count()
        )
        self.db.flush()
        return result

    # ------------------------------------------------------------------
    # Live-fetch: list tags + pull tags (ADR-494 Phase A)
    # ------------------------------------------------------------------

    def list_available_tags(self, source_id: int) -> ReleaseSourceTagList:
        """List manifest tags from the OCI/mirror registry.

        Best-effort: on any listing failure returns an empty tag list with
        list_error set (never raises / never 500s from the route).

        Tags are returned semver-desc by the leading x.y.z segment.
        Each tag is annotated with in_catalog (bnk_manifest_version match)
        and prerelease (packaging.version pre-release flag on the base segment).
        """
        source = self.get_source(source_id)

        try:
            with registry_session(source) as sess:
                raw_tags = sess.list_tags()
        except Exception as exc:
            logger.exception("list_available_tags failed for source %d", source_id)
            if isinstance(exc, DecryptionError):
                list_error = "credential decryption failed"
            else:
                list_error = "Failed to list tags from registry"
            return ReleaseSourceTagList(tags=[], list_error=list_error)

        # Cross-reference existing catalog rows.
        existing_versions: set[str] = {
            row.bnk_manifest_version
            for row in self.db.query(BnkDeployableRelease.bnk_manifest_version).all()
            if row.bnk_manifest_version
        }

        sorted_tags = sorted(raw_tags, key=_base_version, reverse=True)
        annotated = [
            ReleaseSourceTag(
                tag=t,
                in_catalog=t in existing_versions,
                prerelease=_is_prerelease(t),
            )
            for t in sorted_tags
        ]
        return ReleaseSourceTagList(tags=annotated)

    def pull_tags(self, source_id: int, tags: list[str]) -> PullTagsSummary:
        """Pull each requested tag from the OCI/mirror registry and upsert Catalog rows.

        Processing:
          - One login per call (registry_session context manager).
          - For each tag: helm pull + YAML extraction is done outside the savepoint
            (network; failure → failed[]).
          - The DB upsert runs inside begin_nested() so a DB error on one tag
            does not invalidate the outer session for subsequent tags.
          - Upsert mapping (see deployable_release_refresh._upsert_entry):
              inserted → added (new Catalog entry)
              updated (bnk_manifest_version exists) → skipped (already present)
              service-skipped (FLO missing) → failed (reason: missing f5-lifecycle-operator)
          - sync_status=success after the loop even on partial tag failure;
            only a whole-operation failure (login / credential error) sets sync_status=error.
          - Source stats (last_synced_at, sync_status, release_count) are updated
            at the end, mirroring sync_source().

        Returns PullTagsSummary with added / skipped / failed lists.
        """
        source = self.get_source(source_id)
        source.sync_status = "syncing"
        self.db.flush()

        added: list[str] = []
        skipped: list[str] = []
        failed: list[FailedTag] = []

        from services.bare_metal.deployable_release_refresh import DeployableReleaseRefreshService

        try:
            with registry_session(source) as sess:
                for tag in tags:
                    # Network I/O outside savepoint — a pull failure is recorded in failed[].
                    try:
                        manifest_yaml = sess.pull_manifest_yaml(tag)
                    except Exception as pull_exc:
                        logger.warning(
                            "pull_tags: helm pull failed for tag %r (source %d): %s",
                            tag,
                            source_id,
                            pull_exc,
                        )
                        failed.append(FailedTag(tag=tag, reason="helm/oras pull failed"))
                        continue

                    # DB upsert inside savepoint for isolation.
                    try:
                        with self.db.begin_nested():
                            result = DeployableReleaseRefreshService(
                                self.db
                            ).refresh_deployable_releases_from_oci(
                                manifest_yaml, source_id=source_id
                            )
                    except Exception as db_exc:
                        logger.warning(
                            "pull_tags: upsert failed for tag %r (source %d): %s",
                            tag,
                            source_id,
                            db_exc,
                        )
                        failed.append(FailedTag(tag=tag, reason="manifest processing failed"))
                        continue

                    # Strict partition: each tag lands in exactly ONE bucket.
                    # Precedence: added > skipped > failed (service-skipped = FLO missing).
                    # NOTE: assumes one release entry per pulled tag manifest (F5 tag==internal-
                    # version convention). Counts would be approximate if a manifest carried
                    # multiple releases.
                    if result.get("inserted", 0) > 0:
                        added.append(tag)
                    elif result.get("updated", 0) > 0:
                        skipped.append(tag)
                    elif result.get("skipped", 0) > 0:
                        failed.append(
                            FailedTag(tag=tag, reason="missing f5-lifecycle-operator")
                        )
                    else:
                        failed.append(
                            FailedTag(tag=tag, reason="no releases found in manifest")
                        )

        except Exception as login_exc:
            # Whole-operation failure (login / credential error).
            logger.error(
                "pull_tags: registry login failed for source %d: %s", source_id, login_exc
            )
            source.sync_status = "error"
            if isinstance(login_exc, DecryptionError):
                source.sync_error = "credential decryption failed"
            else:
                source.sync_error = "Registry login failed"
            self.db.flush()
            raise

        # Update source stats — mirrors sync_source() tail (release_source_service.py:139-148).
        now = datetime.now(UTC)
        source.last_synced_at = now
        source.sync_status = "success"
        source.sync_error = None
        source.release_count = (
            self.db.query(BnkDeployableRelease)
            .filter(BnkDeployableRelease.source_id == source_id)
            .count()
        )
        self.db.flush()

        return PullTagsSummary(added=added, skipped=skipped, failed=failed)

    # ------------------------------------------------------------------
    # Response builder
    # ------------------------------------------------------------------

    @staticmethod
    def to_response(source: ReleaseSource) -> ReleaseSourceResponse:
        """Build a ReleaseSourceResponse from an ORM row.

        Constructed explicitly because has_credential derives from the
        encrypted column rather than being stored directly.
        """
        return ReleaseSourceResponse(
            id=source.id,
            name=source.name,
            kind=source.kind,
            url=source.url,
            has_credential=bool(source.credential_encrypted),
            is_active=source.is_active,
            auto_sync=source.auto_sync,
            sync_interval_hours=source.sync_interval_hours,
            last_synced_at=source.last_synced_at,
            sync_status=source.sync_status,
            sync_error=source.sync_error,
            release_count=source.release_count,
            description=source.description,
            created_at=source.created_at,
            updated_at=source.updated_at,
        )
