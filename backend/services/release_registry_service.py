"""
Release Registry Service — BNK GA resolution (issue #217).

Provides registry-driven GA label resolution for FLO chart versions,
replacing the hardcoded _VERSION_RANGES in bnk_upgrade_service.py.

The registry (bnk_releases table) is the single source of truth for:
  - GA label (e.g. "BNK 2.3 GA")
  - FLO version prefix / range bounds
  - Kubernetes compatibility
  - Provenance / source citations

Resolution priority:
  1. flo_version_prefix match (installed_flo.startswith(prefix + "."))
  2. flo_version_min/max range match (parsed tuple comparison)
  3. manifest_version match (exact string — less commonly available)

The service also provides sync_from_oci() which annotates OCI-observed tags
against registry rows without overwriting curated clouddocs rows.
"""

import logging
import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from models.bnk_release import BnkRelease
from models.enums import ReleaseSourceType
from services.bnk_upgrade_service import parse_version

logger = logging.getLogger(__name__)


@dataclass
class GaInfo:
    """Resolved GA metadata for a FLO chart version."""

    label: str
    manifest_version: str | None
    min_k8s: str | None
    max_k8s: str | None
    source_type: str
    source_url: str | None
    release_id: int


class ReleaseRegistryService:
    """
    Registry-driven BNK GA resolution service.

    Usage:
        service = ReleaseRegistryService(db)
        info = service.resolve_ga(flo_version="2.21.13-0.0.28")
        # → GaInfo(label="BNK 2.3 GA", ...)
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve_ga(
        self,
        flo_version: str | None = None,
        manifest_version: str | None = None,
    ) -> GaInfo | None:
        """
        Resolve BNK GA metadata from a FLO chart version or manifest version.

        Tries in order:
          1. flo_version_prefix match  (primary — most reliable)
          2. flo_version_min/max range (secondary — handles edge cases)
          3. manifest_version exact    (tertiary — often unavailable from scan)

        Returns None if no active registry row matches.
        """
        active_releases = (
            self.db.query(BnkRelease)
            .filter(BnkRelease.is_active == True)  # noqa: E712
            .all()
        )

        if flo_version:
            fv = flo_version.lstrip("v")

            # Pass 1: prefix match
            for rel in active_releases:
                if rel.flo_version_prefix and self._prefix_matches(fv, rel.flo_version_prefix):
                    return self._to_ga_info(rel)

            # Pass 2: range match (flo_version_min / flo_version_max)
            parsed = parse_version(fv)
            for rel in active_releases:
                if rel.flo_version_min and rel.flo_version_max:
                    lo = parse_version(rel.flo_version_min)
                    hi = parse_version(rel.flo_version_max)
                    if lo <= parsed < hi:
                        return self._to_ga_info(rel)

        # Pass 3: manifest_version exact match
        if manifest_version:
            for rel in active_releases:
                if rel.manifest_version and rel.manifest_version == manifest_version:
                    return self._to_ga_info(rel)

        return None

    def get_or_create_observed(self, flo_version: str) -> int:
        """
        Return the id of an observed BnkRelease row for this exact FLO chart version.

        Dedup-guarded: if an observed row with flo_version_min == flo_version already
        exists it is returned as-is; otherwise a new row is inserted.  The new row is
        inactive (is_active=False) with no prefix or manifest so it never matches
        resolve_ga() (which filters is_active=True).

        Call this only after resolve_ga() returned None — i.e. the version is not
        covered by any known active release line.  Source type = OBSERVED (not OCI).
        """
        existing = (
            self.db.query(BnkRelease)
            .filter(
                BnkRelease.source_type == ReleaseSourceType.OBSERVED,
                BnkRelease.flo_version_min == flo_version,
            )
            .first()
        )
        if existing:
            return existing.id

        row = BnkRelease(
            ga_label=f"Observed FLO {flo_version}",
            product_line="BNK",
            flo_version_prefix=None,
            flo_version_min=flo_version,
            flo_version_max=None,
            manifest_version=None,
            source_type=ReleaseSourceType.OBSERVED,
            notes=f"Auto-observed on cluster scan: FLO chart version {flo_version}",
            is_active=False,
        )
        self.db.add(row)
        self.db.flush()
        return row.id

    def list_releases(self, active_only: bool = True) -> list[BnkRelease]:
        """Return all (or active-only) release rows, newest-first by ga_label."""
        q = self.db.query(BnkRelease)
        if active_only:
            q = q.filter(BnkRelease.is_active == True)  # noqa: E712
        return q.order_by(BnkRelease.ga_label.desc()).all()

    def sync_from_oci(self, oci_tags: list[str]) -> dict:
        """
        Annotate OCI-observed FLO tags against the registry.

        For each tag that matches an active registry row, ensures there is at
        most one OCI-source row recording when it was observed.  Curated
        clouddocs rows are never overwritten.

        Returns a summary dict: {"matched": int, "unmatched": int, "upserted": int}.
        """
        matched = 0
        unmatched = 0
        upserted = 0

        for tag in oci_tags:
            info = self.resolve_ga(flo_version=tag)
            if info is None:
                unmatched += 1
                logger.debug(f"OCI tag {tag!r} does not match any registry row")
                continue

            matched += 1
            # Check if an OCI row already records this exact tag
            existing = (
                self.db.query(BnkRelease)
                .filter(
                    BnkRelease.source_type == ReleaseSourceType.OCI,
                    BnkRelease.flo_version_min == tag,
                )
                .first()
            )
            if existing:
                continue  # already recorded

            # Record this observed tag as an OCI-source row (non-curated)
            oci_row = BnkRelease(
                ga_label=info.label,
                product_line="BNK",
                flo_version_prefix=None,
                flo_version_min=tag,
                flo_version_max=None,
                source_type=ReleaseSourceType.OCI,
                notes=f"OCI-observed tag from repo.f5.com/charts/f5-lifecycle-operator: {tag}",
                is_active=False,  # observed rows are inactive by default
            )
            self.db.add(oci_row)
            upserted += 1

        self.db.flush()
        return {"matched": matched, "unmatched": unmatched, "upserted": upserted}

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _prefix_matches(flo_version: str, prefix: str) -> bool:
        """
        Return True if flo_version starts with prefix + ".".

        Handles both "v"-stripped and raw versions.
        E.g.: "2.21.13-0.0.28" matches prefix "2.21"
              "1.198.4-0.1.36"  matches prefix "1.198"
        """
        # Strip any leading 'v' from both sides for comparison
        fv = flo_version.lstrip("v")
        pfx = prefix.lstrip("v")
        return bool(re.match(r"^" + re.escape(pfx) + r"\.", fv))

    @staticmethod
    def _to_ga_info(rel: BnkRelease) -> GaInfo:
        return GaInfo(
            label=rel.ga_label,
            manifest_version=rel.manifest_version,
            min_k8s=rel.min_k8s,
            max_k8s=rel.max_k8s,
            source_type=rel.source_type,
            source_url=rel.source_url,
            release_id=rel.id,
        )
