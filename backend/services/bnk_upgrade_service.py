"""
BNK Upgrade Workflow Service — Sprint 8.2

Orchestrates BNK platform upgrades with:
  1. Pre-upgrade validation (health, prerequisites, version compatibility)
  2. Upgrade plan generation (ordered steps with dependency awareness)
  3. Rolling execution (FLO → wait → CNEInstance → components) with health gates
  4. Post-upgrade health verification
  5. Rollback on failure

The upgrade sequence follows BNK's dependency chain:
  FLO (Helm upgrade) → CNEInstance (manifest re-apply) → VLANs/GatewayClass (manifest re-apply)

Plan + validation logic is in bnk_upgrade_plan_service.py (BnkUpgradePlanMixin).
Execution + rollback logic is in bnk_upgrade_execution_service.py (BnkUpgradeExecutionMixin).

ENG-006: This service retains its own db.commit() calls because upgrade
execution is a long-running multi-step process that commits after each step
to persist progress. Intermediate commits are required so that if the process
crashes, the upgrade state accurately reflects how far it got.

FLO is the only Helm chart — upgrading FLO's version deploys new CRDs,
new TMM images, and new controller versions. CNEInstance and downstream
manifests are re-applied to pick up any schema changes in the new CRDs.
"""

import base64
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime

import requests
from sqlalchemy.orm import Session

from core.encryption import decrypt_value
from core.errors import AppError
from models import BnkUpgrade, KubernetesCluster, ProjectSecret
from models.enums import BnkUpgradeStatus
from services.cluster_scanner import ClusterScanner

logger = logging.getLogger(__name__)


# ==============================================================
# Version helpers
# ==============================================================

def parse_version(v: str) -> tuple[int, ...]:
    """
    Parse a FLO version string to a comparable numeric tuple.

    Handles formats like:
      v1.198.4-0.1.36  → (1, 198, 4, 0, 1, 36)
      1.198.4           → (1, 198, 4)
      v2.0.0            → (2, 0, 0)
    """
    v = v.lstrip("v")
    parts = re.split(r"[-.]", v)
    result = []
    for p in parts:
        try:
            result.append(int(p))
        except ValueError:
            break
    return tuple(result) if result else (0,)


def version_gt(a: str, b: str) -> bool:
    """Return True if version a > version b."""
    return parse_version(a) > parse_version(b)


def version_eq(a: str, b: str) -> bool:
    """Return True if version a == version b."""
    return parse_version(a) == parse_version(b)


# ==============================================================
# Version metadata — maps FLO chart version ranges to BNK GA releases.
#
# BNK ≥ 2.2 adopted a new FLO chart versioning scheme: `2.x.x` (e.g.
# 2.9.27 for BNK 2.2, 2.21.13 for BNK 2.3).  BNK ≤ 2.1 used the legacy
# `v1.19x.x` scheme (e.g. v1.198.4-0.1.36 for BNK 2.1).
#
# This is NOT a version picker list.  Versions come from the OCI
# registry at runtime.  This map provides human-friendly labels
# and K8s compatibility info so the UI can show
# "BNK 2.3 GA" instead of "2.21.13-0.0.28".
#
# Sources:
#   BNK 2.3 — IBM-F5 ibmcloud_schematics_bigip_next_for_kubernetes_2_3_flo
#              manifest 2.3.0 + live-observed FLO 2.21.13 on a known-2.3 cluster
#   BNK 2.2 — docs/DPU_DEPLOY_ANALYSIS.md (cites clouddocs BNK 2.2) + live FLO 2.9.27
#   BNK 2.1 — docs/DPU_DEPLOY_ANALYSIS.md + existing tests (FLO v1.198.x)
#   BNK 2.0 — clouddocs 2.0 release notes (FLO v1.197.x / v1.7.8 era)
#
# Add new entries here when F5 publishes a new BNK GA release.
# Keep them sorted newest-first — first match wins.
# ==============================================================

@dataclass
class _VersionRange:
    """Maps a FLO chart version range to a BNK GA label."""
    min_version: tuple[int, ...]   # inclusive lower bound (parsed)
    max_version: tuple[int, ...]   # exclusive upper bound (parsed)
    label: str
    min_k8s: str
    max_k8s: str

# Ranges are checked in order — first match wins.
# Keep them sorted newest-first so the most recent match is found first.
_VERSION_RANGES: list[_VersionRange] = [
    # BNK 2.3 — FLO chart 2.21.x (new 2.x.x scheme)
    # Source: IBM-F5 manifest 2.3.0 + live FLO 2.21.13 on a known-2.3 cluster
    _VersionRange(
        min_version=parse_version("2.20.0"),
        max_version=parse_version("3.0.0"),
        label="BNK 2.3 GA",
        min_k8s="1.30",
        # Upstream-validated through 1.31; widened to 1.33 to cover real-world
        # EKS/GKE/kind clusters (#390 — k8s is backward-compatible for BNK's
        # purposes). Anything newer than max_k8s is a warning, not a hard fail
        # (see bnk_upgrade_plan_service._run_pre_checks k8s_compat check).
        max_k8s="1.33",
    ),
    # BNK 2.2 — FLO chart 2.9.x (new 2.x.x scheme)
    # Source: docs/DPU_DEPLOY_ANALYSIS.md + live FLO 2.9.27
    _VersionRange(
        min_version=parse_version("2.0.0"),
        max_version=parse_version("2.20.0"),
        label="BNK 2.2 GA",
        min_k8s="1.30",
        max_k8s="1.33",  # widened per #390, see BNK 2.3 GA comment above
    ),
    # BNK 2.1.x — FLO chart v1.198.x (legacy v1.19x.x scheme)
    # Source: docs/DPU_DEPLOY_ANALYSIS.md + existing tests
    _VersionRange(
        min_version=parse_version("v1.198.0"),
        max_version=parse_version("v1.199.0"),
        label="BNK 2.1 GA",
        min_k8s="1.26",
        max_k8s="1.29",
    ),
    # BNK 2.1.0 / 2.1.1 legacy FLO v1.199.x was mis-assigned in earlier code;
    # clouddocs lists 2.1.0 and 2.1.1 as separate GAs, both under v1.198.x.
    # BNK 2.0 (SPK era) — FLO chart v1.197.x and below
    # Source: clouddocs 2.0 release notes
    _VersionRange(
        min_version=parse_version("v1.0.0"),
        max_version=parse_version("v1.198.0"),
        label="BNK 2.0",
        min_k8s="1.25",
        max_k8s="1.28",
    ),
]


def detect_current_bnk_version(bnk_install: dict) -> str | None:
    """
    Detect the currently-installed BNK version, install-shape-aware (#389).

    Resolution order (first hit wins):
      1. FLO pod image version (the FLO/deploy-flow install).
      2. Helm release chart version string, e.g. ``"f5ingress-2.21.13"`` ->
         ``"2.21.13"`` (``"{chart_name}-{chart_version}"`` per
         scanner/fetch.py:_fetch_helm_releases).
      3. Discovered controller (f5ingress) pod image tag — the robust signal
         on a helm/manual install, where the Helm release secret often carries
         only a revision (``version: "4"``) and no chart version.
      4. Discovered TMM pod image tag (secondary fallback).

    Both (3) and (4) come from the scanner's controller/tmm image extraction
    (scanner/bnk_install.py:_extract_image_version).
    """
    flo_info = bnk_install.get("flo", {})
    flo_version = flo_info.get("version")
    if flo_version:
        return flo_version

    helm_release = flo_info.get("helm_release") or {}
    chart = helm_release.get("chart") or ""
    match = re.search(r"-(\d+\.\d+\.\d+(?:-[\d.]+)?)$", chart)
    if match:
        return match.group(1)

    # controller/tmm .version fields carry raw image tags (e.g. v14.59.1-0.0.70),
    # not chart versions — they cannot be reliably compared to BNK release versions.
    # Return None here; callers should treat None as "version unknown".
    return None


def get_known_version_info(version: str) -> dict | None:
    """
    Lookup BNK GA metadata for a FLO chart version using range-based matching.

    Examples:
        v1.198.4-0.1.36  → {"label": "BNK 2.1 GA", ...}
        v1.199.0-0.1.0   → {"label": "BNK 2.2 GA", ...}
        v1.197.2-0.0.1   → {"label": "BNK 2.0", ...}
        v99.0.0           → None  (unknown)
    """
    parsed = parse_version(version)
    for vr in _VERSION_RANGES:
        if vr.min_version <= parsed < vr.max_version:
            return {
                "label": vr.label,
                "min_k8s": vr.min_k8s,
                "max_k8s": vr.max_k8s,
            }
    return None


# ==============================================================
# Upgrade step types
# ==============================================================

STEP_HELM_UPGRADE = "helm_upgrade"
STEP_MANIFEST_APPLY = "manifest_apply"
STEP_HEALTH_GATE = "health_gate"
STEP_CRD_WAIT = "crd_wait"


# ==============================================================
# BnkUpgradeService
# ==============================================================

# Import mixins — done here (after module-level constants) to avoid circular imports
from services.bnk_upgrade_execution_service import BnkUpgradeExecutionMixin
from services.bnk_upgrade_plan_service import BnkUpgradePlanMixin


class BnkUpgradeService(BnkUpgradePlanMixin, BnkUpgradeExecutionMixin):
    """
    Orchestrates BNK platform upgrades.

    Plan + validation provided by BnkUpgradePlanMixin.
    Execution + rollback provided by BnkUpgradeExecutionMixin.

    Usage:
        service = BnkUpgradeService(db)
        upgrade = service.create_upgrade(cluster_id, target_version, user="admin")
        plan = service.generate_plan(upgrade.id)
        # (async) service.execute_upgrade(upgrade.id, on_output=callback)
    """

    def __init__(self, db: Session):
        self.db = db

    # ----------------------------------------------------------
    # Create + Pre-validate
    # ----------------------------------------------------------

    def create_upgrade(
        self,
        cluster_id: int,
        target_version: str,
        user: str = "user",
    ) -> BnkUpgrade:
        """
        Create a new upgrade record and run pre-upgrade validation.

        Returns the BnkUpgrade with pre_checks populated.
        Status will be 'ready' if all critical checks pass, 'failed' otherwise.
        """
        cluster = self.db.query(KubernetesCluster).filter(
            KubernetesCluster.id == cluster_id
        ).first()
        if not cluster:
            raise ValueError(f"Cluster {cluster_id} not found")

        # Check for an existing non-terminal upgrade.
        # - Planned-but-not-started (PLANNING / READY): supersede by cancelling the old one,
        #   then proceed to create the new plan. Re-clicking "Validate & Plan" just works.
        # - Actively executing (IN_PROGRESS / HEALTH_CHECK / ROLLING_BACK): cannot clobber —
        #   raise 409 with upgrade_id + status so the FE can offer a Cancel action.
        _SUPERSEDABLE = {BnkUpgradeStatus.PLANNING, BnkUpgradeStatus.READY}
        _EXECUTING = {BnkUpgradeStatus.IN_PROGRESS, BnkUpgradeStatus.HEALTH_CHECK, BnkUpgradeStatus.ROLLING_BACK}
        existing = self.db.query(BnkUpgrade).filter(
            BnkUpgrade.cluster_id == cluster_id,
            BnkUpgrade.status.in_([*_SUPERSEDABLE, *_EXECUTING]),
        ).first()
        if existing:
            if existing.status in _EXECUTING:
                raise AppError(
                    code="UPGRADE_IN_PROGRESS",
                    message="An upgrade is currently running. Cancel it before starting a new plan.",
                    status_code=409,
                    details={"upgrade_id": existing.id, "status": str(existing.status)},
                )
            # Supersedable — mark the old plan cancelled and continue.
            existing.status = BnkUpgradeStatus.CANCELLED
            existing.completed_at = datetime.now(UTC)
            self.db.flush()

        # Scan cluster for current BNK state
        scanner = ClusterScanner(self.db)
        try:
            scan_data = scanner.scan(cluster_id)
        except Exception as e:
            raise RuntimeError(f"Failed to scan cluster: {e}")

        bnk_install = scan_data.get("bnk_install", {})
        current_version = detect_current_bnk_version(bnk_install)
        cluster_info = scan_data.get("cluster_info", {})

        # Create upgrade record
        upgrade = BnkUpgrade(
            cluster_id=cluster_id,
            project_id=cluster.project_id,
            from_version=current_version,
            to_version=target_version,
            from_bnk_info=bnk_install,
            status=BnkUpgradeStatus.PLANNING,
            triggered_by=user,
        )
        self.db.add(upgrade)
        self.db.flush()  # Get ID

        # Run pre-upgrade checks
        checks = self._run_pre_checks(
            bnk_install=bnk_install,
            cluster_info=cluster_info,
            current_version=current_version,
            target_version=target_version,
        )
        upgrade.pre_checks = checks

        # Determine if all critical checks passed
        critical_failures = [c for c in checks if c["status"] == "fail" and c.get("critical", True)]
        upgrade.pre_check_passed = len(critical_failures) == 0

        if upgrade.pre_check_passed:
            # Generate upgrade plan
            plan = self._build_plan(bnk_install, target_version)
            upgrade.plan = plan
            upgrade.total_steps = len(plan)
            upgrade.status = BnkUpgradeStatus.READY
        else:
            upgrade.status = BnkUpgradeStatus.FAILED
            upgrade.error_message = "; ".join(c["detail"] for c in critical_failures)

        self.db.commit()
        self.db.refresh(upgrade)
        return upgrade

    # Pre-checks, plan building, execution, and rollback provided by mixins:
    #   BnkUpgradePlanMixin: _run_pre_checks(), _build_plan()
    #   BnkUpgradeExecutionMixin: execute_upgrade(), _execute_*, rollback()

    # ----------------------------------------------------------
    # Queries
    # ----------------------------------------------------------

    def get_upgrade(self, upgrade_id: int) -> BnkUpgrade | None:
        """Get a single upgrade record."""
        return self.db.query(BnkUpgrade).filter(BnkUpgrade.id == upgrade_id).first()

    def list_upgrades(
        self,
        cluster_id: int,
        limit: int = 20,
    ) -> list[BnkUpgrade]:
        """List upgrade history for a cluster."""
        return (
            self.db.query(BnkUpgrade)
            .filter(BnkUpgrade.cluster_id == cluster_id)
            .order_by(BnkUpgrade.created_at.desc())
            .limit(limit)
            .all()
        )

    def cancel_upgrade(self, upgrade_id: int) -> BnkUpgrade:
        """Cancel a pending/ready upgrade."""
        upgrade = self.db.query(BnkUpgrade).filter(BnkUpgrade.id == upgrade_id).first()
        if not upgrade:
            raise ValueError(f"Upgrade {upgrade_id} not found")

        if upgrade.status not in (BnkUpgradeStatus.PLANNING, BnkUpgradeStatus.READY):
            raise ValueError(f"Cannot cancel upgrade in status '{upgrade.status}'")

        upgrade.status = BnkUpgradeStatus.CANCELLED
        upgrade.completed_at = datetime.now(UTC)
        self.db.commit()
        return upgrade

    def get_available_versions(self, cluster_id: int | None = None) -> tuple[list[dict], str | None]:
        """
        Return available FLO chart versions from the F5 OCI registry,
        falling back to known BNK GA versions when the registry is unavailable.

        Queries repo.f5.com/v2/charts/f5-lifecycle-operator/tags/list using
        the cne_pull_secret credentials from the cluster's project.

        Returns:
            (versions, registry_error) — versions is a list of version dicts,
            registry_error is None on success or a user-facing message on failure.
        """
        if cluster_id is None:
            return [], "No cluster specified — cannot query OCI registry"

        try:
            versions = self._fetch_oci_versions(cluster_id)
            if versions:
                return versions, None
        except Exception as e:
            logger.warning(f"Failed to fetch versions from OCI registry: {e}")

        # Registry unavailable or returned nothing — fall back to known versions
        fallback = self._get_fallback_versions()
        if fallback:
            logger.info(f"Using {len(fallback)} known BNK versions as fallback (OCI registry unavailable)")
            return fallback, "OCI registry unavailable — showing known BNK versions. Configure cne_pull_secret for exact chart versions."
        return [], "No versions available — OCI registry unreachable and no known versions defined"

    @staticmethod
    def _get_fallback_versions() -> list[dict]:
        """
        Generate fallback version entries from the known _VERSION_RANGES.

        Each range's min_version is used as a representative GA version tag.
        These are marked with source="known" so the UI can distinguish them
        from registry-verified versions.

        Returns versions sorted newest-first.
        """
        fallback = []
        for vr in _VERSION_RANGES:
            # Reconstruct the version tag from the parsed min_version tuple
            # Format: v<major>.<minor>.<patch> (e.g., v1.199.0)
            parts = vr.min_version
            if len(parts) >= 3:
                tag = f"v{parts[0]}.{parts[1]}.{parts[2]}"
            elif len(parts) >= 2:
                tag = f"v{parts[0]}.{parts[1]}.0"
            else:
                continue

            fallback.append({
                "version": tag,
                "label": vr.label,
                "release_date": None,
                "notes": f"Known GA release ({tag}). Exact patch versions available with OCI registry access.",
                "min_k8s": vr.min_k8s,
                "max_k8s": vr.max_k8s,
                "source": "known",
            })

        # Already sorted newest-first in _VERSION_RANGES
        return fallback

    def _get_registry_credentials(self, cluster_id: int) -> tuple[str, str] | None:
        """
        Extract username/password for repo.f5.com from the project's cne_pull_secret.

        The cne_pull_secret is a base64-encoded Docker config JSON:
          {"auths": {"repo.f5.com": {"auth": "<base64 user:pass>"}}}

        Returns (username, password) or None if not available.
        """
        cluster = self.db.query(KubernetesCluster).filter(
            KubernetesCluster.id == cluster_id
        ).first()
        if not cluster or not cluster.project_id:
            return None

        # Find the cne_pull_secret in project secrets
        secret = self.db.query(ProjectSecret).filter(
            ProjectSecret.project_id == cluster.project_id,
            ProjectSecret.name == "cne_pull_secret",
            ProjectSecret.is_active,
        ).first()

        if not secret:
            # Also try by target_variable_name
            secret = self.db.query(ProjectSecret).filter(
                ProjectSecret.project_id == cluster.project_id,
                ProjectSecret.target_variable_name == "cne_pull_secret",
                ProjectSecret.is_active,
            ).first()

        if not secret:
            logger.debug("No cne_pull_secret found in project secrets")
            return None

        try:
            # Decrypt the secret value
            encrypted = secret.value_encrypted or secret.file_content_encrypted
            if not encrypted:
                return None

            raw_value = decrypt_value(encrypted)
            if not raw_value:
                return None

            # Decode base64 Docker config JSON
            decoded = base64.b64decode(raw_value).decode("utf-8")
            config = json.loads(decoded)
            auths = config.get("auths", {})

            # Look for repo.f5.com entry
            for host, auth_data in auths.items():
                if "repo.f5.com" in host:
                    auth_b64 = auth_data.get("auth", "")
                    if auth_b64:
                        auth_decoded = base64.b64decode(auth_b64).decode("utf-8")
                        if ":" in auth_decoded:
                            username, password = auth_decoded.split(":", 1)
                            return (username, password)

            logger.debug(f"No repo.f5.com entry in cne_pull_secret auths: {list(auths.keys())}")
            return None

        except Exception as e:
            logger.warning(f"Failed to extract registry credentials: {e}")
            return None

    def _fetch_oci_versions(self, cluster_id: int) -> list[dict]:
        """
        Fetch available FLO chart versions from the F5 OCI registry.

        Uses the OCI Distribution API:
          GET https://repo.f5.com/v2/charts/f5-lifecycle-operator/tags/list

        Returns a list of version dicts sorted newest first.
        """
        creds = self._get_registry_credentials(cluster_id)
        if not creds:
            logger.info("No registry credentials available — cannot query OCI registry")
            return []

        username, password = creds
        registry_url = "https://repo.f5.com/v2/charts/f5-lifecycle-operator/tags/list"

        try:
            resp = requests.get(
                registry_url,
                auth=(username, password),
                timeout=15,
                headers={"Accept": "application/json"},
            )

            if resp.status_code == 401:
                logger.warning("OCI registry returned 401 — cne_pull_secret may be expired")
                return []

            if resp.status_code != 200:
                logger.warning(f"OCI registry returned {resp.status_code}: {resp.text[:200]}")
                return []

            data = resp.json()
            tags = data.get("tags", [])

            if not tags:
                logger.info("OCI registry returned empty tags list")
                return []

            # Filter and sort: only version-like tags (start with v or digit)
            version_tags = [t for t in tags if re.match(r'^v?\d+\.', t)]
            version_tags.sort(key=lambda t: parse_version(t), reverse=True)

            # Build version list — prefer registry GA labels, fall back to in-code ranges
            from services.release_registry_service import ReleaseRegistryService
            registry_svc: ReleaseRegistryService | None = None
            try:
                registry_svc = ReleaseRegistryService(self.db)
            except Exception:
                pass  # DB unavailable; fall back to in-code ranges entirely

            versions = []
            for tag in version_tags:
                ga_label: str | None = None
                min_k8s: str | None = None
                max_k8s: str | None = None

                if registry_svc:
                    try:
                        ga_info = registry_svc.resolve_ga(flo_version=tag)
                        if ga_info:
                            ga_label = ga_info.label
                            min_k8s = ga_info.min_k8s
                            max_k8s = ga_info.max_k8s
                    except Exception:
                        pass

                if ga_label is None:
                    known = get_known_version_info(tag)
                    if known:
                        ga_label = known["label"]
                        min_k8s = known.get("min_k8s")
                        max_k8s = known.get("max_k8s")

                versions.append({
                    "version": tag,
                    "label": ga_label or tag,
                    "release_date": None,
                    "notes": None,
                    "min_k8s": min_k8s,
                    "max_k8s": max_k8s,
                    "source": "registry",
                })

            logger.info(f"Fetched {len(versions)} FLO versions from OCI registry")
            return versions

        except requests.exceptions.Timeout:
            logger.warning("OCI registry request timed out")
            return []
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"Cannot reach OCI registry: {e}")
            return []
        except Exception as e:
            logger.warning(f"OCI registry query failed: {e}")
            return []

    def get_cluster_bnk_version(self, cluster_id: int) -> dict:
        """Get current BNK version info from cluster."""
        from services.release_registry_service import ReleaseRegistryService

        scanner = ClusterScanner(self.db)
        scan = scanner.scan(cluster_id)
        bnk = scan.get("bnk_install", {})
        flo = bnk.get("flo", {})
        flo_version = detect_current_bnk_version(bnk)

        # Prefer registry-driven GA label; fall back to in-code ranges for robustness
        ga_label: str | None = None
        min_k8s: str | None = None
        max_k8s: str | None = None
        if flo_version:
            try:
                registry_svc = ReleaseRegistryService(self.db)
                ga_info = registry_svc.resolve_ga(flo_version=flo_version)
                if ga_info:
                    ga_label = ga_info.label
                    min_k8s = ga_info.min_k8s
                    max_k8s = ga_info.max_k8s
            except Exception as e:
                logger.debug(f"Registry GA lookup failed, falling back to in-code ranges: {e}")

            if ga_label is None:
                known = get_known_version_info(flo_version)
                if known:
                    ga_label = known["label"]
                    min_k8s = known.get("min_k8s")
                    max_k8s = known.get("max_k8s")

        return {
            "status": bnk.get("status", "not_installed"),
            "health": bnk.get("health"),
            "flo_version": flo_version,
            "ga_label": ga_label,
            "min_k8s": min_k8s,
            "max_k8s": max_k8s,
            "helm_release": flo.get("helm_release"),
            "tmm_pods": bnk.get("tmm", {}),
            "vlans": bnk.get("vlans", []),
        }
