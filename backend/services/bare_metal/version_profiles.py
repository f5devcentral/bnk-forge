"""BNK deployable release CRUD and seed data (ADR-478)."""

import logging

from sqlalchemy.orm import Session

from models.bnk_deployable_release import BnkDeployableRelease
from schemas.bare_metal import DeployableReleaseListResponse, DeployableReleaseResponse

logger = logging.getLogger(__name__)


# --- Seed data ---
# Exact values from original BnkVersionProfile seed; 2.2 is default.

BNK_21_PROFILE = {
    "name": "bnk-2.1",
    "display_name": "BNK 2.1 (GA)",
    "description": "BNK 2.1 General Availability release",
    "is_default": False,
    "is_active": True,
    "source_type": "manual",
    "bnk_manifest_version": "2.1.0",
    "bnk_cr_kind": "CNEInstance",
    "flo_version": "0.9.23",
    "k8s_version": "1.29.8",
    "doca_version": "2.7.0",
    "containerd_version": "1.7.12",
    "runc_version": "1.1.12",
    "calico_version": "3.28.0",
    "cert_manager_version": "v1.14.5",
    "gateway_api_version": "1.1.0",
    "multus_version": "4.0.2",
    "sriov_version": "1.3.0",
    "storage_class_type": "local-path",
    "storage_provisioner": "rancher.io/local-path",
    "feature_flags": {"ipv6": False, "tmm_node_labels": True},
}

BNK_22_PROFILE = {
    "name": "bnk-2.2",
    "display_name": "BNK 2.2 (GA)",
    "description": "BNK 2.2 General Availability release",
    "is_default": True,
    "is_active": True,
    "source_type": "manual",
    "bnk_manifest_version": "2.2.1-3.2226.0-0.0.511",
    "bnk_cr_kind": "CNEInstance",
    "flo_version": "v2.9.27-0.3.4",
    "k8s_version": "1.30.4",
    "doca_version": "2.9.1",
    "containerd_version": "1.7.20",
    "runc_version": "1.1.13",
    "calico_version": "3.28.1",
    "cert_manager_version": "v1.15.3",
    "gateway_api_version": "1.1.0",
    "multus_version": "4.1.0",
    "sriov_version": "1.4.0",
    "storage_class_type": "local-path",
    "storage_provisioner": "rancher.io/local-path",
    "feature_flags": {"ipv6": False, "tmm_node_labels": True},
}

# BNK 2.3.1 — verified 2026-07-20 against repo.f5.com + dpubnkctl
# Full matrix in .agent-local/BNK-231-VERIFIED-MATRIX.md
# calico/multus/sriov/gateway_api carry forward from 2.2 (P2 live-pin pending)
BNK_231_RELEASE = {
    "name": "bnk-2.3.1",
    "display_name": "BNK 2.3.1 (GA)",
    "description": "BNK 2.3.1 General Availability release",
    "is_default": False,
    "is_active": True,
    "source_type": "manual",
    "bnk_manifest_version": "2.3.1-3.2598.3-0.0.304",
    "bnk_cr_kind": "CNEInstance",
    "flo_version": "v2.21.13-0.0.53",
    "k8s_version": "1.30.14",
    "doca_version": "3.2.0",
    "containerd_version": "1.7.23",
    "runc_version": "1.2.1",
    "calico_version": "3.28.1",
    "cert_manager_version": "v1.16.2",
    "gateway_api_version": "1.1.0",
    "multus_version": "4.1.0",
    "sriov_version": "1.4.0",
    "storage_class_type": "local-path",
    "storage_provisioner": "rancher.io/local-path",
    "feature_flags": {"ipv6": False, "tmm_node_labels": True},
}

SEED_RELEASES = [BNK_21_PROFILE, BNK_22_PROFILE, BNK_231_RELEASE]


class BnkDeployableReleaseService:
    """CRUD operations for BNK deployable releases."""

    def __init__(self, db: Session):
        self.db = db

    def list_profiles(self) -> DeployableReleaseListResponse:
        releases = self.db.query(BnkDeployableRelease).order_by(BnkDeployableRelease.name).all()
        return DeployableReleaseListResponse(
            releases=[self._to_response(r) for r in releases]
        )

    def get_profile(self, profile_id: int) -> DeployableReleaseResponse:
        release = self.db.query(BnkDeployableRelease).filter(BnkDeployableRelease.id == profile_id).first()
        if not release:
            from core.errors import NotFoundError
            raise NotFoundError("deployable_release", profile_id)
        return self._to_response(release)

    def get_default_profile(self) -> BnkDeployableRelease | None:
        return self.db.query(BnkDeployableRelease).filter(BnkDeployableRelease.is_default.is_(True)).first()

    def create_profile(self, data: dict) -> DeployableReleaseResponse:
        release = BnkDeployableRelease(**data)
        self.db.add(release)
        self.db.flush()
        return self._to_response(release)

    def seed_profiles(self) -> int:
        """Seed default deployable releases if they don't exist. Returns count seeded."""
        # Resolve bnk_release_id for 2.3.1 if the bnk_releases table is populated
        bnk_release_id_231 = self._resolve_bnk_release_id("2.21")

        seeded = 0
        for release_data in SEED_RELEASES:
            existing = self.db.query(BnkDeployableRelease).filter(
                BnkDeployableRelease.name == release_data["name"]
            ).first()
            if not existing:
                row_data = dict(release_data)
                if release_data["name"] == "bnk-2.3.1" and bnk_release_id_231 is not None:
                    row_data["bnk_release_id"] = bnk_release_id_231
                self.db.add(BnkDeployableRelease(**row_data))
                seeded += 1
                logger.info("Seeded BNK deployable release: %s", release_data["name"])
        if seeded > 0:
            self.db.flush()
        return seeded

    def _resolve_bnk_release_id(self, flo_version_prefix: str) -> int | None:
        """Look up BnkRelease.id by flo_version_prefix; returns None if not found."""
        try:
            from models.bnk_release import BnkRelease
            row = self.db.query(BnkRelease).filter(
                BnkRelease.flo_version_prefix == flo_version_prefix
            ).first()
            return row.id if row else None
        except Exception:
            return None

    def set_active(self, release_id: int, is_active: bool) -> DeployableReleaseResponse:
        release = self.db.query(BnkDeployableRelease).filter(BnkDeployableRelease.id == release_id).first()
        if not release:
            from core.errors import NotFoundError
            raise NotFoundError("deployable_release", release_id)
        release.is_active = is_active
        self.db.flush()
        return self._to_response(release)

    def set_default(self, release_id: int) -> DeployableReleaseResponse:
        """Set this release as default, clearing is_default on all others (single-default invariant)."""
        release = self.db.query(BnkDeployableRelease).filter(BnkDeployableRelease.id == release_id).first()
        if not release:
            from core.errors import NotFoundError
            raise NotFoundError("deployable_release", release_id)
        # Clear existing default(s) then set the target.
        self.db.query(BnkDeployableRelease).filter(BnkDeployableRelease.is_default.is_(True)).update(
            {"is_default": False}, synchronize_session="fetch"
        )
        release.is_default = True
        self.db.flush()
        return self._to_response(release)

    def _to_response(self, release: BnkDeployableRelease) -> DeployableReleaseResponse:
        return DeployableReleaseResponse(
            id=release.id,
            name=release.name,
            display_name=release.display_name,
            description=release.description,
            is_default=release.is_default,
            is_active=release.is_active,
            source_type=release.source_type,
            bnk_release_id=release.bnk_release_id,
            bnk_manifest_version=release.bnk_manifest_version,
            bnk_cr_kind=release.bnk_cr_kind,
            flo_version=release.flo_version,
            k8s_version=release.k8s_version,
            doca_version=release.doca_version,
            containerd_version=release.containerd_version,
            runc_version=release.runc_version,
            calico_version=release.calico_version,
            cert_manager_version=release.cert_manager_version,
            gateway_api_version=release.gateway_api_version,
            multus_version=release.multus_version,
            sriov_version=release.sriov_version,
            storage_class_type=release.storage_class_type,
            storage_provisioner=release.storage_provisioner,
            feature_flags=release.feature_flags,
            source_id=release.source_id,
            last_synced=release.last_synced,
            created_at=release.created_at,
        )
