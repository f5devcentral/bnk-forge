"""BNK version profile CRUD and seed data."""

import logging

from sqlalchemy.orm import Session

from models.bare_metal import BnkVersionProfile
from schemas.bare_metal import BnkVersionProfileListResponse, BnkVersionProfileResponse

logger = logging.getLogger(__name__)


# --- Seed data for BNK 2.1 and 2.2 ---

BNK_21_PROFILE = {
    "name": "bnk-2.1",
    "display_name": "BNK 2.1 (GA)",
    "description": "BNK 2.1 General Availability release",
    "is_default": False,
    "bnk_manifest_version": "2.1.0",
    "bnk_cr_kind": "CNEInstance",
    "flo_version": "0.9.23",
    "k8s_version": "1.29.8",
    "doca_version": "2.7.0",
    "containerd_version": "1.7.12",
    "runc_version": "1.1.12",
    "calico_version": "3.28.0",
    "cert_manager_version": "1.14.5",
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
    "bnk_manifest_version": "2.2.0",
    "bnk_cr_kind": "BNKGatewayClass",
    "flo_version": "0.10.5",
    "k8s_version": "1.30.4",
    "doca_version": "2.9.1",
    "containerd_version": "1.7.20",
    "runc_version": "1.1.13",
    "calico_version": "3.28.1",
    "cert_manager_version": "1.15.3",
    "gateway_api_version": "1.1.0",
    "multus_version": "4.1.0",
    "sriov_version": "1.4.0",
    "storage_class_type": "local-path",
    "storage_provisioner": "rancher.io/local-path",
    "feature_flags": {"ipv6": False, "tmm_node_labels": True},
}

SEED_PROFILES = [BNK_21_PROFILE, BNK_22_PROFILE]


class BnkVersionProfileService:
    """CRUD operations for BNK version profiles."""

    def __init__(self, db: Session):
        self.db = db

    def list_profiles(self) -> BnkVersionProfileListResponse:
        profiles = self.db.query(BnkVersionProfile).order_by(BnkVersionProfile.name).all()
        return BnkVersionProfileListResponse(
            profiles=[self._to_response(p) for p in profiles]
        )

    def get_profile(self, profile_id: int) -> BnkVersionProfileResponse:
        profile = self.db.query(BnkVersionProfile).filter(BnkVersionProfile.id == profile_id).first()
        if not profile:
            from core.errors import NotFoundError
            raise NotFoundError("version_profile", profile_id)
        return self._to_response(profile)

    def get_default_profile(self) -> BnkVersionProfile | None:
        return self.db.query(BnkVersionProfile).filter(BnkVersionProfile.is_default.is_(True)).first()

    def create_profile(self, data: dict) -> BnkVersionProfileResponse:
        profile = BnkVersionProfile(**data)
        self.db.add(profile)
        self.db.flush()
        return self._to_response(profile)

    def seed_profiles(self) -> int:
        """Seed default version profiles if they don't exist. Returns count of profiles seeded."""
        seeded = 0
        for profile_data in SEED_PROFILES:
            existing = self.db.query(BnkVersionProfile).filter(
                BnkVersionProfile.name == profile_data["name"]
            ).first()
            if not existing:
                self.db.add(BnkVersionProfile(**profile_data))
                seeded += 1
                logger.info("Seeded BNK version profile: %s", profile_data["name"])
        if seeded > 0:
            self.db.flush()
        return seeded

    def _to_response(self, profile: BnkVersionProfile) -> BnkVersionProfileResponse:
        return BnkVersionProfileResponse(
            id=profile.id,
            name=profile.name,
            display_name=profile.display_name,
            description=profile.description,
            is_default=profile.is_default,
            bnk_manifest_version=profile.bnk_manifest_version,
            bnk_cr_kind=profile.bnk_cr_kind,
            flo_version=profile.flo_version,
            k8s_version=profile.k8s_version,
            doca_version=profile.doca_version,
            containerd_version=profile.containerd_version,
            runc_version=profile.runc_version,
            calico_version=profile.calico_version,
            cert_manager_version=profile.cert_manager_version,
            gateway_api_version=profile.gateway_api_version,
            multus_version=profile.multus_version,
            sriov_version=profile.sriov_version,
            storage_class_type=profile.storage_class_type,
            storage_provisioner=profile.storage_provisioner,
            feature_flags=profile.feature_flags,
            created_at=profile.created_at,
        )
