"""Pydantic serializer contracts for discovery persistence entities.

These are future-facing serializer contracts for discovery read surfaces.
In DISCOVERY-PORT-001 they are intentionally not wired to API routes yet.
"""

from datetime import datetime
from typing import Any, cast

from pydantic import BaseModel

from models.discovery import DiscoveredNode, DiscoveryJob


class DiscoveredNodeResponse(BaseModel):
    """Secret-safe discovered-node response contract."""

    id: int
    discovery_job_id: int
    ip_address: str
    hostname: str | None
    ssh_credential_id: int | None
    has_ssh_credentials: bool
    status: str
    error_message: str | None
    os_type: str | None
    os_version: str | None
    os_pretty_name: str | None
    kernel_version: str | None
    architecture: str | None
    is_dpu_host: bool
    is_dpu_node: bool
    dpu_count: int
    dpu_details: list[dict] | None
    is_dpu_bmc: bool = False
    bmc_product: str | None = None
    bmc_serial_number: str | None = None
    # Phase 2 BMC capture: whatever the Redfish probe could pull when
    # the auth ladder authenticated. Keys are Redfish-style names
    # (Manufacturer, Model, BiosVersion, BmcFirmwareVersion, NicMode,
    # HostPrivilegeLevel, PowerState, MemoryGiB, …) plus a synthetic
    # `UsesDefaultPassword: true` when the auth that won was Nvidia's
    # default `0penBmc` — the UI shows a warning chip in that case.
    bmc_redfish_payload: dict[str, Any] | None = None
    k8s_installed: bool
    k8s_running: bool
    k8s_version: str | None
    k8s_distribution: str | None
    k8s_role: str | None
    container_runtime: str | None
    network_interfaces: list[dict] | None
    discovered_at: datetime | None
    created_at: datetime

    @classmethod
    def from_model(cls, node: DiscoveredNode) -> "DiscoveredNodeResponse":
        return cls(
            id=cast(int, node.id),
            discovery_job_id=cast(int, node.discovery_job_id),
            ip_address=cast(str, node.ip_address),
            hostname=cast(str | None, node.hostname),
            ssh_credential_id=cast(int | None, node.ssh_credential_id),
            has_ssh_credentials=bool(
                node.ssh_credential_id
                or node.ssh_username
                or node.ssh_auth_type
                or node.ssh_password_encrypted
                or node.ssh_key_encrypted
            ),
            status=cast(str, node.status),
            error_message=cast(str | None, node.error_message),
            os_type=cast(str | None, node.os_type),
            os_version=cast(str | None, node.os_version),
            os_pretty_name=cast(str | None, node.os_pretty_name),
            kernel_version=cast(str | None, node.kernel_version),
            architecture=cast(str | None, node.architecture),
            is_dpu_host=bool(node.is_dpu_host),
            is_dpu_node=bool(node.is_dpu_node),
            dpu_count=cast(int, node.dpu_count or 0),
            dpu_details=cast(list[dict[str, Any]] | None, node.dpu_details),
            is_dpu_bmc=bool(node.is_dpu_bmc),
            bmc_product=cast(str | None, node.bmc_product),
            bmc_serial_number=cast(str | None, node.bmc_serial_number),
            bmc_redfish_payload=cast(
                dict[str, Any] | None, node.bmc_redfish_payload,
            ),
            k8s_installed=bool(node.k8s_installed),
            k8s_running=bool(node.k8s_running),
            k8s_version=cast(str | None, node.k8s_version),
            k8s_distribution=cast(str | None, node.k8s_distribution),
            k8s_role=cast(str | None, node.k8s_role),
            container_runtime=cast(str | None, node.container_runtime),
            network_interfaces=cast(list[dict[str, Any]] | None, node.network_interfaces),
            discovered_at=cast(datetime | None, node.discovered_at),
            created_at=cast(datetime, node.created_at),
        )


class DiscoveryJobResponse(BaseModel):
    """Secret-safe discovery-job serializer contract."""

    id: int
    project_id: int
    ssh_credential_id: int | None
    has_shared_credentials: bool
    status: str
    total_nodes: int
    completed_nodes: int
    failed_nodes: int
    celery_task_id: str | None
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    created_by: str | None
    connectivity_status: str | None
    connectivity_matrix: dict | None
    nodes: list[DiscoveredNodeResponse]

    @classmethod
    def from_model(cls, job: DiscoveryJob, *, include_nodes: bool = True) -> "DiscoveryJobResponse":
        return cls(
            id=cast(int, job.id),
            project_id=cast(int, job.project_id),
            ssh_credential_id=cast(int | None, job.ssh_credential_id),
            has_shared_credentials=bool(
                job.ssh_credential_id
                or job.ssh_username
                or job.ssh_auth_type
                or job.ssh_password_encrypted
                or job.ssh_key_encrypted
            ),
            status=cast(str, job.status),
            total_nodes=cast(int, job.total_nodes or 0),
            completed_nodes=cast(int, job.completed_nodes or 0),
            failed_nodes=cast(int, job.failed_nodes or 0),
            celery_task_id=cast(str | None, job.celery_task_id),
            error_message=cast(str | None, job.error_message),
            started_at=cast(datetime | None, job.started_at),
            completed_at=cast(datetime | None, job.completed_at),
            created_at=cast(datetime, job.created_at),
            created_by=cast(str | None, job.created_by),
            connectivity_status=cast(str | None, job.connectivity_status),
            connectivity_matrix=cast(dict | None, job.connectivity_matrix) if include_nodes else None,
            nodes=[DiscoveredNodeResponse.from_model(node) for node in (job.nodes or [])] if include_nodes else [],
        )
