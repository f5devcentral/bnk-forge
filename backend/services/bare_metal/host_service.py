"""Bare-metal host CRUD service."""

import logging

from sqlalchemy.orm import Session

from core.encryption import encrypt_value
from models.bare_metal import BareMetalHost
from schemas.bare_metal import (
    BareMetalHostCreate,
    BareMetalHostListResponse,
    BareMetalHostResponse,
    BareMetalHostUpdate,
)

logger = logging.getLogger(__name__)


class BareMetalHostService:
    """CRUD operations for bare-metal hosts."""

    def __init__(self, db: Session):
        self.db = db

    def list_hosts(self, project_id: int) -> BareMetalHostListResponse:
        hosts = (
            self.db.query(BareMetalHost)
            .filter(BareMetalHost.project_id == project_id)
            .order_by(BareMetalHost.name)
            .all()
        )
        return BareMetalHostListResponse(hosts=[self._to_response(h) for h in hosts])

    def get_host(self, project_id: int, host_id: int) -> BareMetalHostResponse:
        host = (
            self.db.query(BareMetalHost)
            .filter(BareMetalHost.id == host_id, BareMetalHost.project_id == project_id)
            .first()
        )
        if not host:
            from core.errors import NotFoundError
            raise NotFoundError("BareMetalHost", str(host_id))
        return self._to_response(host)

    def create_host(self, project_id: int, data: BareMetalHostCreate) -> BareMetalHostResponse:
        host = BareMetalHost(
            project_id=project_id,
            name=data.name,
            description=data.description,
            host_ip=data.host_ip,
            ssh_credential_id=data.ssh_credential_id,
            ssh_port=data.ssh_port,
            jumphost_chain=data.jumphost_chain,
            topology=data.topology,
            network_mode=data.network_mode,
            vlan_id=data.vlan_id,
            gateway_ip=data.gateway_ip,
            host_mgmt_ip=data.host_mgmt_ip,
            dpu_mgmt_ip=data.dpu_mgmt_ip,
            dpu_credential_id=data.dpu_credential_id,
            version_profile_id=data.version_profile_id,
            deploy_dpu_pci_address=data.deploy_dpu_pci_address,
            rshim_source=data.rshim_source,
            bond_mode=data.bond_mode,
        )
        # Encrypt BMC credentials if provided
        if data.bmc_ip:
            host.bmc_ip = data.bmc_ip
        if data.bmc_username:
            host.bmc_username_encrypted = encrypt_value(data.bmc_username)
        if data.bmc_password:
            host.bmc_password_encrypted = encrypt_value(data.bmc_password)
        if data.ipmi_ip:
            host.ipmi_ip = data.ipmi_ip
        if data.ipmi_username:
            host.ipmi_username_encrypted = encrypt_value(data.ipmi_username)
        if data.ipmi_password:
            host.ipmi_password_encrypted = encrypt_value(data.ipmi_password)

        self.db.add(host)
        self.db.flush()
        logger.info("Created bare-metal host '%s' (id=%d) in project %d", host.name, host.id, project_id)

        # D-022: upsert fleet membership for the newly created host.
        try:
            from services.fleet_reconcile_service import reconcile_fleet_member
            reconcile_fleet_member(self.db, "bare-metal-host", host.id)
        except Exception:
            logger.exception("Fleet reconcile failed for new host id=%s", host.id)

        return self._to_response(host)

    def update_host(self, project_id: int, host_id: int, data: BareMetalHostUpdate) -> BareMetalHostResponse:
        host = (
            self.db.query(BareMetalHost)
            .filter(BareMetalHost.id == host_id, BareMetalHost.project_id == project_id)
            .first()
        )
        if not host:
            from core.errors import NotFoundError
            raise NotFoundError("BareMetalHost", str(host_id))

        update_data = data.model_dump(exclude_unset=True)

        # Handle encrypted fields separately
        encrypted_fields = {
            "bmc_username": "bmc_username_encrypted",
            "bmc_password": "bmc_password_encrypted",
            "ipmi_username": "ipmi_username_encrypted",
            "ipmi_password": "ipmi_password_encrypted",
        }
        for plain_field, enc_field in encrypted_fields.items():
            if plain_field in update_data:
                value = update_data.pop(plain_field)
                if value is not None:
                    setattr(host, enc_field, encrypt_value(value))
                else:
                    setattr(host, enc_field, None)

        # Update remaining fields
        for field, value in update_data.items():
            if hasattr(host, field):
                setattr(host, field, value)

        self.db.flush()
        logger.info("Updated bare-metal host %d", host_id)
        return self._to_response(host)

    def delete_host(self, project_id: int, host_id: int) -> None:
        host = (
            self.db.query(BareMetalHost)
            .filter(BareMetalHost.id == host_id, BareMetalHost.project_id == project_id)
            .first()
        )
        if not host:
            from core.errors import NotFoundError
            raise NotFoundError("BareMetalHost", str(host_id))

        self.db.delete(host)
        self.db.flush()
        logger.info("Deleted bare-metal host %d from project %d", host_id, project_id)

    def _to_response(self, host: BareMetalHost) -> BareMetalHostResponse:
        return BareMetalHostResponse(
            id=host.id,
            project_id=host.project_id,
            name=host.name,
            description=host.description,
            hostname=host.hostname,
            host_ip=host.host_ip,
            ssh_credential_id=host.ssh_credential_id,
            ssh_port=host.ssh_port or 22,
            has_jumphost_chain=bool(host.jumphost_chain),
            uses_project_jumphost=not bool(host.jumphost_chain) and bool(host.project and host.project.ssh_credential_id),
            bmc_ip=host.bmc_ip,
            bmc_access_tier=host.bmc_access_tier or "none",
            bmc_vendor=host.bmc_vendor,
            ipmi_ip=host.ipmi_ip,
            has_bmc_credentials=bool(host.bmc_username_encrypted),
            has_ipmi_credentials=bool(host.ipmi_username_encrypted),
            topology=host.topology,
            topology_auto_detected=host.topology_auto_detected or False,
            network_mode=host.network_mode,
            vlan_id=host.vlan_id,
            gateway_ip=host.gateway_ip,
            host_mgmt_ip=host.host_mgmt_ip,
            dpu_mgmt_ip=host.dpu_mgmt_ip,
            dpu_credential_id=host.dpu_credential_id,
            version_profile_id=host.version_profile_id,
            os_info=host.os_info,
            dpu_info=host.dpu_info,
            k8s_info=host.k8s_info,
            last_discovery_at=host.last_discovery_at,
            last_discovery_status=host.last_discovery_status,
            last_discovery_error=host.last_discovery_error,
            nic_mode=host.nic_mode,
            mst_device=host.mst_device,
            rshim_present=host.rshim_present,
            default_route_iface=host.default_route_iface,
            phase_c_deposited=host.phase_c_deposited,
            phase_c_completed=host.phase_c_completed,
            pf_interfaces=host.pf_interfaces,
            vf_count=host.vf_count,
            hugepages_host_gb=host.hugepages_host_gb,
            deploy_dpu_pci_address=host.deploy_dpu_pci_address,
            deploy_dpu_index=host.deploy_dpu_index,
            rshim_source=host.rshim_source,
            bond_mode=host.bond_mode,
            has_discovery_result=bool(host.last_discovery_result),
            kubernetes_cluster_id=host.kubernetes_cluster_id,
            created_at=host.created_at,
            updated_at=host.updated_at,
        )

    def get_host_model(self, project_id: int, host_id: int) -> BareMetalHost:
        """Get the raw ORM model (not schema) for a host."""
        host = (
            self.db.query(BareMetalHost)
            .filter(BareMetalHost.id == host_id, BareMetalHost.project_id == project_id)
            .first()
        )
        if not host:
            from core.errors import NotFoundError
            raise NotFoundError("BareMetalHost", str(host_id))
        return host
