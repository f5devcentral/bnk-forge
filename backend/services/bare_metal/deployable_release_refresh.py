"""Deployable-release OCI refresh service (ADR-478 P1-5).

Parses a BNK manifest YAML from repo.f5.com and upserts rows into
bnk_deployable_release. This service writes ONLY to bnk_deployable_release;
it never touches bnk_releases or the sync_from_oci / resolve_ga behaviour
of ReleaseRegistryService.

The forge backend has no ambient repo.f5.com credential, so the manifest
YAML is accepted as input — network fetch is the caller's responsibility
(admin trigger with cne_pull_secret, CI pipeline, or the bare-metal SSH
prerequisites module which already does the pull on-host).

Component-version parsing reuses parse_component_versions() from
modules/bare_metal/bnk_prerequisites.py (same dict format: chart/image
name → version string, keyed with their "charts/" or "images/" prefix).
"""

import logging
from datetime import UTC, datetime

import yaml
from sqlalchemy.orm import Session

from models.bnk_deployable_release import BnkDeployableRelease
from models.enums import ReleaseSourceType
from modules.bare_metal.bnk_prerequisites import parse_component_versions

logger = logging.getLogger(__name__)

# Mirrors _MANIFEST_CHART in modules/bare_metal/bnk_prerequisites.py.
# The manifest chart pulled from repo.f5.com to resolve component versions.
OCI_MANIFEST_CHART = "oci://repo.f5.com/release/f5-bigip-k8s-manifest"

# Chart name in the manifest that carries the FLO (f5-lifecycle-operator) version.
_FLO_CHART_NAME = "charts/f5-lifecycle-operator"

# Default CR kind when the manifest does not specify one explicitly.
# 2.3.x uses CNEInstance; callers supply overrides for other kinds.
_DEFAULT_CR_KIND = "CNEInstance"

# Placeholder stored for host-substrate fields not present in the BNK manifest
# (k8s_version, doca_version, …). INSERT without overrides stores "" so the
# row is queryable; callers should backfill via overrides or a subsequent edit.
_UNKNOWN_VERSION = ""


def parse_manifest_yaml(yaml_text: str) -> list[dict]:
    """Parse a BNK manifest YAML and return a list of release entry dicts.

    Accepts the ``releases:[{version, helm_charts:[{name,version}],
    docker_images:[{name,version}]}]`` structure shipped in the
    f5-bigip-k8s-manifest Helm chart.

    Each returned dict contains:
      - ``manifest_version`` (str): e.g. ``"2.3.1-3.2598.3-0.0.304"``
      - ``component_versions`` (dict[str, str]): chart/image name → version,
        identical in format to the dict produced by parse_component_versions()
        (e.g. ``{"charts/f5-lifecycle-operator": "v2.21.13-0.0.53", ...}``).

    Uses parse_component_versions() internally so the parsing logic is shared
    with the SSH on-host path in modules/bare_metal/bnk_prerequisites.py.

    Raises ValueError on invalid YAML or missing top-level structure.
    """
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in manifest: {exc}") from exc

    if not isinstance(data, dict) or "releases" not in data:
        raise ValueError("Manifest YAML missing top-level 'releases' key")

    entries: list[dict] = []
    for rel in data.get("releases", []):
        manifest_version = str(rel.get("version", "")).strip()
        if not manifest_version:
            continue

        # Build key=value lines in the same format that parse_component_versions
        # expects (mirrors the awk output from _download_versions on-host).
        kv_lines: list[str] = []
        for chart in rel.get("helm_charts", []):
            name = str(chart.get("name", "")).strip()
            version = str(chart.get("version", "")).strip()
            if name and version:
                kv_lines.append(f"{name}={version}")
        for image in rel.get("docker_images", []):
            name = str(image.get("name", "")).strip()
            version = str(image.get("version", "")).strip()
            if name and version:
                kv_lines.append(f"{name}={version}")

        component_versions = parse_component_versions("\n".join(kv_lines))
        entries.append({
            "manifest_version": manifest_version,
            "component_versions": component_versions,
        })

    return entries


def _derive_name(manifest_version: str) -> str:
    """Derive a row name from the OCI manifest version string.

    "2.3.1-3.2598.3-0.0.304" → "bnk-2.3.1"
    """
    first_segment = manifest_version.split("-")[0]
    return f"bnk-{first_segment}"


class DeployableReleaseRefreshService:
    """
    OCI manifest → bnk_deployable_release upsert (ADR-478 P1-5).

    Usage::

        svc = DeployableReleaseRefreshService(db)
        result = svc.refresh_deployable_releases_from_oci(manifest_yaml=yaml_text)
        # → {"inserted": 1, "updated": 0, "skipped": 0}

    Isolation guarantee:
      This service NEVER writes to bnk_releases and NEVER calls
      sync_from_oci or any write method on ReleaseRegistryService.
      The only cross-service call is a read-only resolve_ga() to resolve
      the bnk_release_id FK for display purposes.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def refresh_deployable_releases_from_oci(
        self,
        manifest_yaml: str,
        *,
        is_active: bool = True,
        overrides: dict | None = None,
        source_id: int | None = None,
    ) -> dict[str, int]:
        """Parse manifest_yaml and upsert each release entry.

        Args:
            manifest_yaml: Raw YAML text from the BNK manifest chart
                (e.g. the content of bigip-k8s-manifest-*.yaml extracted
                from the Helm tgz pulled via ``helm pull OCI_MANIFEST_CHART``).
            is_active: Mark new/updated rows active or inactive.
            overrides: Optional per-entry overrides applied to INSERT only.
                Accepts any BnkDeployableRelease field; useful for supplying
                host-substrate versions (k8s_version, doca_version, etc.) that
                are NOT present in the BNK manifest. On UPDATE these are ignored
                — existing DB values are preserved.
            source_id: When set, stamps source_id and last_synced on every
                upserted row. Default None preserves existing ADR-478 behaviour
                (no provenance stamping).

        Returns:
            ``{"inserted": N, "updated": N, "skipped": N}``
        """
        entries = parse_manifest_yaml(manifest_yaml)
        inserted = updated = skipped = 0

        for entry in entries:
            result = self._upsert_entry(
                entry, is_active=is_active, overrides=overrides or {}, source_id=source_id
            )
            if result == "inserted":
                inserted += 1
            elif result == "updated":
                updated += 1
            else:
                skipped += 1

        if inserted + updated > 0:
            self.db.flush()

        logger.info(
            "deployable_release OCI refresh complete: inserted=%d updated=%d skipped=%d",
            inserted,
            updated,
            skipped,
        )
        return {"inserted": inserted, "updated": updated, "skipped": skipped}

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _upsert_entry(
        self, entry: dict, *, is_active: bool, overrides: dict, source_id: int | None = None
    ) -> str:
        """Upsert a single parsed manifest entry.

        Returns "inserted", "updated", or "skipped".
        Skipped only when flo_version is absent from the entry (cannot link
        to the GA-label registry and cannot safely deploy without FLO chart).

        When source_id is provided, stamps source_id and last_synced on the row
        for both INSERT and UPDATE paths.
        """
        manifest_version: str = entry["manifest_version"]
        component_versions: dict[str, str] = entry["component_versions"]

        flo_version = component_versions.get(_FLO_CHART_NAME, "")
        if not flo_version:
            logger.warning(
                "manifest entry %r missing %r — skipped",
                manifest_version,
                _FLO_CHART_NAME,
            )
            return "skipped"

        existing = (
            self.db.query(BnkDeployableRelease)
            .filter(BnkDeployableRelease.bnk_manifest_version == manifest_version)
            .first()
        )

        if existing:
            # UPDATE — refresh BNK-layer versions; preserve all host-substrate
            # fields (k8s_version, doca_version, etc.) already in the DB.
            existing.flo_version = flo_version
            existing.is_active = is_active
            existing.source_type = ReleaseSourceType.OCI
            existing.full_manifest = component_versions
            # Backfill bnk_release_id if it was previously unresolved.
            if existing.bnk_release_id is None:
                existing.bnk_release_id = self._resolve_bnk_release_id(flo_version)
            if source_id is not None:
                existing.source_id = source_id
                existing.last_synced = datetime.now(UTC)
            return "updated"

        # INSERT — resolve FK and build a complete row.
        bnk_release_id = self._resolve_bnk_release_id(flo_version)
        name = overrides.get("name") or _derive_name(manifest_version)
        display_name = (
            overrides.get("display_name")
            or f"BNK {manifest_version.split('-')[0]} (OCI)"
        )

        row_data: dict = {
            "name": name,
            "display_name": display_name,
            "description": overrides.get("description", ""),
            "is_default": False,
            "is_active": is_active,
            "source_type": ReleaseSourceType.OCI,
            "bnk_release_id": bnk_release_id,
            "bnk_manifest_version": manifest_version,
            "bnk_cr_kind": overrides.get("bnk_cr_kind", _DEFAULT_CR_KIND),
            "flo_version": flo_version,
            # Host-substrate fields — not in the manifest; supply via overrides
            # or backfill later. Empty strings satisfy the nullable=False constraint.
            "k8s_version": overrides.get("k8s_version", _UNKNOWN_VERSION),
            "doca_version": overrides.get("doca_version", _UNKNOWN_VERSION),
            "containerd_version": overrides.get("containerd_version", _UNKNOWN_VERSION),
            "runc_version": overrides.get("runc_version", _UNKNOWN_VERSION),
            "calico_version": overrides.get("calico_version", _UNKNOWN_VERSION),
            "cert_manager_version": overrides.get("cert_manager_version", _UNKNOWN_VERSION),
            "gateway_api_version": overrides.get("gateway_api_version", _UNKNOWN_VERSION),
            "multus_version": overrides.get("multus_version", _UNKNOWN_VERSION),
            "sriov_version": overrides.get("sriov_version", _UNKNOWN_VERSION),
            "storage_class_type": overrides.get("storage_class_type", "local-path"),
            "storage_provisioner": overrides.get(
                "storage_provisioner", "rancher.io/local-path"
            ),
            "feature_flags": overrides.get("feature_flags", {}),
            "full_manifest": component_versions,
        }

        if source_id is not None:
            row_data["source_id"] = source_id
            row_data["last_synced"] = datetime.now(UTC)

        self.db.add(BnkDeployableRelease(**row_data))
        return "inserted"

    def _resolve_bnk_release_id(self, flo_version: str) -> int | None:
        """Read-only lookup of BnkRelease.id via ReleaseRegistryService.resolve_ga.

        Returns the FK to link the deployable release to its GA-label row.
        Returns None if no matching active registry row exists (non-fatal).
        Never writes to bnk_releases.
        """
        try:
            from services.release_registry_service import ReleaseRegistryService

            info = ReleaseRegistryService(self.db).resolve_ga(flo_version=flo_version)
            return info.release_id if info else None
        except Exception:
            return None
