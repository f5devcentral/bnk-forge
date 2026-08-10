"""Project-scoped SSH discovery trigger/read routes."""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.errors import handle_route_errors
from database import get_db
from models import User
from routes.auth import require_project_owner, require_viewer
from schemas.discovery import DiscoveryJobResponse
from services.discovery_service import DiscoveryService

router = APIRouter(prefix="/api/projects/{project_id}/discovery", tags=["discovery"])


class DiscoveryNodeInput(BaseModel):
    ip_address: str
    ssh_credential_id: int | None = None
    ssh_username: str | None = None
    ssh_port: int | None = None
    ssh_auth_type: str | None = None
    ssh_password: str | None = None
    ssh_private_key: str | None = None


class DiscoveryTriggerRequest(BaseModel):
    nodes: list[DiscoveryNodeInput]
    ssh_credential_id: int | None = None
    ssh_username: str | None = None
    ssh_port: int | None = 22
    ssh_auth_type: str | None = None
    ssh_password: str | None = None
    ssh_private_key: str | None = None


class DiscoveryTriggerResponse(BaseModel):
    job: DiscoveryJobResponse


class DiscoveryJobListResponse(BaseModel):
    jobs: list[DiscoveryJobResponse]


@router.get("", response_model=DiscoveryJobListResponse, dependencies=[Depends(require_viewer)])
@handle_route_errors("list project discovery jobs")
def list_discovery_jobs(
    project_id: int,
    limit: int = 20,
    db: Session = Depends(get_db),
) -> DiscoveryJobListResponse:
    jobs = DiscoveryService(db).list_discovery_jobs(project_id=project_id, limit=limit)
    return DiscoveryJobListResponse(jobs=jobs)


@router.post("", response_model=DiscoveryTriggerResponse)
@handle_route_errors("trigger project discovery")
def trigger_discovery(
    project_id: int,
    data: DiscoveryTriggerRequest,
    request: Request,
    _user: User = Depends(require_project_owner),
    db: Session = Depends(get_db),
) -> DiscoveryTriggerResponse:
    username = getattr(request.state, "username", None)
    service = DiscoveryService(db)
    job = service.trigger_discovery(
        project_id=project_id,
        payload=data.model_dump(),
        created_by=username,
    )
    db.commit()
    DiscoveryService.start_background_execution(job.id)
    # Re-read so any progress that's already happened (especially under inline execution
    # in tests) is reflected in the response.
    return DiscoveryTriggerResponse(job=service.get_discovery_job(project_id=project_id, job_id=job.id))


@router.get("/{job_id}", response_model=DiscoveryJobResponse, dependencies=[Depends(require_viewer)])
@handle_route_errors("get project discovery job")
def get_discovery_job(
    project_id: int,
    job_id: int,
    db: Session = Depends(get_db),
) -> DiscoveryJobResponse:
    return DiscoveryService(db).get_discovery_job(project_id=project_id, job_id=job_id)


@router.post("/{job_id}/rerun", response_model=DiscoveryTriggerResponse)
@handle_route_errors("rerun project discovery job")
def rerun_discovery_job(
    project_id: int,
    job_id: int,
    _user: User = Depends(require_project_owner),
    db: Session = Depends(get_db),
) -> DiscoveryTriggerResponse:
    """Re-run an existing discovery job (completed or failed). Reuses stored credentials."""
    service = DiscoveryService(db)
    job = service.rerun_discovery_job(project_id=project_id, job_id=job_id)
    db.commit()
    DiscoveryService.start_background_execution(job.id)
    return DiscoveryTriggerResponse(job=service.get_discovery_job(project_id=project_id, job_id=job.id))


@router.delete("/{job_id}", status_code=204)
@handle_route_errors("delete project discovery job")
def delete_discovery_job(
    project_id: int,
    job_id: int,
    _user: User = Depends(require_project_owner),
    db: Session = Depends(get_db),
) -> None:
    """Delete a discovery job and all its discovered nodes."""
    DiscoveryService(db).delete_discovery_job(project_id=project_id, job_id=job_id)
    db.commit()
    return None


@router.post("/nodes/{node_id}/probe-kubeconfig", dependencies=[Depends(require_project_owner)])
@handle_route_errors("probe kubeconfig from discovered node")
def probe_node_kubeconfig(
    project_id: int,
    node_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """SSH to a discovered node and return its kubeconfig contexts for cluster registration."""
    return DiscoveryService(db).probe_node_kubeconfig(project_id=project_id, node_id=node_id)


class RegisterAsDpuRequest(BaseModel):
    """Body for register-as-dpu.

    Required only when the discovered IP is the DPU OS (not the BMC).
    When the node is a BMC, the BMC IP is the node's own IP_address and all
    fields are optional.
    """
    bmc_ip: str | None = None
    bmc_username: str | None = None
    bmc_password: str | None = None
    name: str | None = None


class RegisteredDpuSummary(BaseModel):
    dpu_id: int
    project_id: int
    access_mode: str  # "bmc" | "in-band"
    bmc_ip: str | None
    dpu_os_ip: str | None
    host_node_ip: str | None
    pci_address: str | None


class RegisterAsDpuResponse(BaseModel):
    """Always a list — single-element for BMC/DPU-OS nodes, one-per-DPU for hosts."""

    dpus: list[RegisteredDpuSummary]


@router.post(
    "/nodes/{node_id}/register-as-dpu",
    response_model=RegisterAsDpuResponse,
    status_code=201,
    dependencies=[Depends(require_project_owner)],
)
@handle_route_errors("register discovered node as DPU")
def register_node_as_dpu(
    project_id: int,
    node_id: int,
    data: RegisterAsDpuRequest,
    db: Session = Depends(get_db),
):
    """Register a discovered node as a DPU in the project's DPU tab.

    The endpoint detects automatically whether the node's IP is the DPU
    BMC or the DPU OS, based on the fields populated by discovery:
      - `is_dpu_bmc=True` → IP is the BMC; bmc_ip = node.ip_address.
      - `is_dpu_node=True` → IP is the DPU OS; bmc_ip must be supplied.
      - Neither → 400.
    """
    result = DiscoveryService(db).register_node_as_dpu(
        project_id=project_id,
        node_id=node_id,
        bmc_ip=data.bmc_ip,
        bmc_username=data.bmc_username,
        bmc_password=data.bmc_password,
        name=data.name,
    )
    db.commit()
    return result


class RegisterAsHostRequest(BaseModel):
    """Body for register-as-bare-metal-host. All fields optional."""
    name: str | None = None


class RegisterAsHostResponse(BaseModel):
    """Result of registering one discovered DPU-host as a bare-metal host."""
    host_id: int
    status: str  # "created" | "exists"
    name: str
    host_ip: str


class RegisterAllHostsResponse(BaseModel):
    """Bulk register-all-hosts result. Counts let the UI render a useful toast."""
    created: int
    existing: int
    hosts: list[RegisterAsHostResponse]


@router.post(
    "/nodes/{node_id}/register-as-host",
    response_model=RegisterAsHostResponse,
    status_code=201,
    dependencies=[Depends(require_project_owner)],
)
@handle_route_errors("register discovered node as bare-metal host")
def register_node_as_host(
    project_id: int,
    node_id: int,
    data: RegisterAsHostRequest,
    db: Session = Depends(get_db),
) -> RegisterAsHostResponse:
    """Register a discovered DPU-host node as a bare-metal host.

    Idempotent — re-clicking the action on a host that's already
    registered returns the existing row with `status="exists"` rather
    than failing.
    """
    result = DiscoveryService(db).register_node_as_bare_metal_host(
        project_id=project_id, node_id=node_id, name=data.name,
    )
    db.commit()
    return RegisterAsHostResponse(**result)


@router.post(
    "/{job_id}/register-all-hosts",
    response_model=RegisterAllHostsResponse,
    status_code=201,
    dependencies=[Depends(require_project_owner)],
)
@handle_route_errors("register all discovered hosts as bare-metal hosts")
def register_all_hosts(
    project_id: int,
    job_id: int,
    db: Session = Depends(get_db),
) -> RegisterAllHostsResponse:
    """Register every eligible DPU-host node from a discovery job.

    Eligible = `is_dpu_host=True` with one or more PCIe-detected DPUs.
    Idempotent per-row (already-registered hosts increment the
    `existing` counter rather than failing the whole batch).
    """
    result = DiscoveryService(db).register_all_hosts_in_job(
        project_id=project_id, job_id=job_id,
    )
    db.commit()
    return RegisterAllHostsResponse(
        created=result["created"],
        existing=result["existing"],
        hosts=[RegisterAsHostResponse(**h) for h in result["hosts"]],
    )
