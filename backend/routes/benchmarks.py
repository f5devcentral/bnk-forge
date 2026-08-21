"""
Benchmark API routes (Phase 2 + Phase 4b: LLM Inference Load Testing Dashboard).

Provides:
  - POST /api/benchmarks/results   — receive BenchmarkResult push from aiperf CLI
  - CRUD for runs, configs, agents
  - Comparison and summary endpoints
  - WebSocket for agent connections
  - CRUD for benchmark targets and proxy deployments (Phase 4b)

⚠️  TERMINOLOGY: BenchmarkRun = load test, BenchmarkAgent = test client machine,
    BenchmarkTarget = K8s cluster + LLM endpoint, ProxyDeployment = proxy on a target.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Body, Depends, Query, Request, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from core.auth_context import effective_role
from core.config import settings
from core.errors import BadRequestError, ForbiddenError, NotFoundError, handle_route_errors
from database import get_db
from models.benchmark import BenchmarkAgent
from models.enums import BenchmarkAgentStatus, BenchmarkRunStatus, ProxyDeploymentStatus
from models.project import Project
from models.system import User
from routes.auth import require_operator, require_viewer
from schemas.benchmarks import (
    AgentHostCandidatesResponse,
    AgentHostProvisionResponse,
    AgentHostScanRequest,
    AgentHostScanResponse,
    BenchmarkAgentHostCreate,
    BenchmarkAgentHostResponse,
    BenchmarkAgentRegister,
    BenchmarkAgentResponse,
    BenchmarkCompareRequest,
    BenchmarkCompareResponse,
    BenchmarkConfigCreate,
    BenchmarkConfigResponse,
    BenchmarkConfigUpdate,
    BenchmarkResultPush,
    BenchmarkResultPushResponse,
    BenchmarkRunCreate,
    BenchmarkRunDetailResponse,
    BenchmarkRunListResponse,
    BenchmarkRunResponse,
    BenchmarkSummaryResponse,
    BenchmarkTargetCreate,
    BenchmarkTargetDetailResponse,
    BenchmarkTargetListResponse,
    BenchmarkTargetResponse,
    BenchmarkTargetUpdate,
    BenchmarkTrendsResponse,
    DiscoverTargetsRequest,
    DiscoverTargetsResponse,
    ImportAwsJumphostRequest,
    ProxyDeploymentResponse,
    ProxyDeploymentUpdate,
    ProxyDeployRequest,
    ProxyDiscoveryResponse,
    ProxyTaskStatusResponse,
    RunGroupResponse,
    ScenarioCatalogResponse,
    ScenarioRunRequest,
    ScenarioRunResponse,
    TriggerRunRequest,
    TriggerRunResponse,
)
from services.benchmark_service import BenchmarkService
from services.benchmark_target_service import BenchmarkTargetService

logger = logging.getLogger(__name__)

router = APIRouter()
ws_router = APIRouter()


# ============================================================================
# Agent auth helpers — flag-gated (BENCHMARK_AGENT_AUTH_REQUIRED)
# ============================================================================

# Roles whose tokens may write to the agent-facing endpoints. `agent` is the
# claim _mint_agent_token and the bootstrap token carry; operator/admin keeps
# the documented human curl flow (`curl -X POST .../results/aiperf` with a user
# token) working. A viewer token authenticates a person but grants no write
# intent, so it is rejected here even though it decodes cleanly.
_AGENT_WRITE_ROLES = frozenset({"agent", "operator", "admin"})


def _require_agent_bearer(request: Request) -> dict:
    """Gate the agent-facing mutating endpoints (register / ingest).

    Requires a valid bearer token whose role may write here (#148, the F6 half
    of #41). Previously any valid token was accepted -- a viewer's included --
    and, because BENCHMARK_AGENT_AUTH_REQUIRED defaulted to False, on a default
    deployment no token was required at all.

    Returns the decoded payload so callers can bind on its claims. When the flag
    is OFF this returns {} without checking, preserving the open curl flow for
    trusted networks that opt out explicitly.
    """
    if not settings.BENCHMARK_AGENT_AUTH_REQUIRED:
        return {}
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise BadRequestError("Bearer token required", code="AGENT_AUTH_REQUIRED")
    token = auth_header.split(" ", 1)[1]
    from core.errors import UnauthorizedError
    from services.auth_service import decode_token
    try:
        payload = decode_token(token)
    except UnauthorizedError as exc:
        raise BadRequestError(str(exc), code="AGENT_AUTH_INVALID")
    role = str(payload.get("role") or "")
    if role not in _AGENT_WRITE_ROLES:
        raise BadRequestError(
            f"Token role '{role or 'none'}' may not write to agent endpoints",
            code="AGENT_AUTH_FORBIDDEN",
        )
    # bonnyr-f5 #186 r2: a human (operator/admin) token owed a password change
    # must not write here either -- this path skipped the gate entirely (a
    # must-change admin could create an agent). Agent tokens carry no User row,
    # and this endpoint is role-based by design, so only gate a token that
    # resolves to a REAL user: if that user owes a password change, refuse.
    if role != "agent":
        from core.errors import ForbiddenError
        from services.auth_service import enforce_password_change, token_user_state
        agent_user = token_user_state(token)
        if agent_user is not None:
            try:
                enforce_password_change(request.url.path, agent_user)
            except ForbiddenError as exc:
                raise BadRequestError(str(exc), code="AGENT_AUTH_PASSWORD_CHANGE_REQUIRED")
    return payload


# ============================================================================
# Result Ingestion — called by aiperf CLI or user curl
# ============================================================================

@router.post("/api/benchmarks/results", response_model=BenchmarkResultPushResponse, status_code=201)
@handle_route_errors("ingest benchmark result")
def ingest_benchmark_result(request: Request, data: BenchmarkResultPush, db: Session = Depends(get_db)):
    """Receive a BenchmarkResult JSON push in Forge's canonical format.

    This is the primary data ingestion endpoint. The CLI sends the complete
    BenchmarkResult JSON after a run completes. We extract key fields for
    denormalization and store the full result as-is.
    """
    _require_agent_bearer(request)
    svc = BenchmarkService(db)
    run = svc.ingest_result(data.model_dump())
    db.commit()
    return {
        "id": run.id,
        "run_id": run.id,
        "proxy": run.proxy,
        "model": run.model,
        "status": run.status,
        "target_id": run.target_id,
        "config_id": run.config_id,
        "proxy_deployment_id": run.proxy_deployment_id,
    }


@router.post("/api/benchmarks/results/aiperf", response_model=BenchmarkResultPushResponse, status_code=201)
@handle_route_errors("ingest raw aiperf result")
def ingest_aiperf_result(
    request: Request,
    raw: dict = Body(...),
    proxy: str = Query("nodeport"),
    model: str | None = Query(None),
    url: str | None = Query(None),
    agent_name: str | None = Query(None),
    run_label: str | None = Query(None),
    target_id: int | None = Query(None),
    config_id: int | None = Query(None),
    proxy_deployment_id: int | None = Query(None),
    dataset_name: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """Ingest a raw aiperf profile_export_aiperf.json file directly.

    Users can push results with a single curl command:
        curl -X POST "https://forge/api/benchmarks/results/aiperf?proxy=nodeport&agent_name=my-agent" \\
          -H "Content-Type: application/json" \\
          -H "Authorization: Bearer <token>" \\
          -d @profile_export_aiperf.json

    The server detects the aiperf export format (has `benchmark_id`,
    `request_latency`, `request_throughput` keys) and transforms it into
    Forge's canonical schema before ingestion.

    Optional query params:
      proxy               — proxy type label (default: nodeport)
      model               — override model name (auto-detected from aiperf config if absent)
      url                 — base URL of the LLM endpoint
      agent_name          — associate with a registered agent
      run_label           — human-readable label
      target_id           — link to a BenchmarkTarget row
      config_id           — link to a BenchmarkConfig row
      proxy_deployment_id — link to a ProxyDeployment row
      dataset_name        — dataset label (stored in result_json)
    """
    _require_agent_bearer(request)
    svc = BenchmarkService(db)
    run = svc.ingest_aiperf_result(
        raw,
        proxy=proxy,
        model=model,
        url=url,
        agent_name=agent_name,
        run_label=run_label,
        target_id=target_id,
        config_id=config_id,
        proxy_deployment_id=proxy_deployment_id,
        dataset_name=dataset_name,
    )
    db.commit()
    return {
        "id": run.id,
        "run_id": run.id,
        "proxy": run.proxy,
        "model": run.model,
        "status": run.status,
        "target_id": run.target_id,
        "config_id": run.config_id,
        "proxy_deployment_id": run.proxy_deployment_id,
    }


# ============================================================================
# Config Endpoints — saved RunConfig presets
# ============================================================================

@router.get("/api/benchmarks/configs", response_model=list[BenchmarkConfigResponse], dependencies=[Depends(require_viewer)])
@handle_route_errors("list benchmark configs")
def list_benchmark_configs(
    tool: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """List all saved benchmark configurations."""
    svc = BenchmarkService(db)
    return svc.list_configs(tool=tool)


@router.get("/api/benchmarks/configs/{config_id}", response_model=BenchmarkConfigResponse, dependencies=[Depends(require_viewer)])
@handle_route_errors("get benchmark config")
def get_benchmark_config(config_id: int, db: Session = Depends(get_db)):
    """Get a saved benchmark configuration by ID.

    The config_json field contains the full RunConfig JSON.
    Use it to build aiperf profile CLI commands.
    """
    svc = BenchmarkService(db)
    return svc.get_config(config_id)


@router.post("/api/benchmarks/configs", response_model=BenchmarkConfigResponse, status_code=201)
@handle_route_errors("create benchmark config")
def create_benchmark_config(data: BenchmarkConfigCreate, db: Session = Depends(get_db)):
    """Create a new saved benchmark configuration."""
    svc = BenchmarkService(db)
    result = svc.create_config(data.model_dump())
    db.commit()
    return result


@router.put("/api/benchmarks/configs/{config_id}", response_model=BenchmarkConfigResponse)
@handle_route_errors("update benchmark config")
def update_benchmark_config(config_id: int, data: BenchmarkConfigUpdate, db: Session = Depends(get_db)):
    """Update a saved benchmark configuration."""
    svc = BenchmarkService(db)
    result = svc.update_config(config_id, data.model_dump(exclude_unset=True))
    db.commit()
    return result


@router.delete("/api/benchmarks/configs/{config_id}", status_code=204)
@handle_route_errors("delete benchmark config")
def delete_benchmark_config(config_id: int, db: Session = Depends(get_db)):
    """Delete a saved benchmark configuration."""
    svc = BenchmarkService(db)
    svc.delete_config(config_id)
    db.commit()


# ============================================================================
# Run Endpoints
# ============================================================================

@router.get("/api/benchmarks/runs", response_model=BenchmarkRunListResponse, dependencies=[Depends(require_viewer)])
@handle_route_errors("list benchmark runs")
def list_benchmark_runs(
    proxy: str | None = Query(None),
    tool: str | None = Query(None),
    model: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """List benchmark runs with optional filters."""
    svc = BenchmarkService(db)
    runs, total = svc.list_runs(proxy=proxy, tool=tool, model=model, status=status, limit=limit, offset=offset)
    return {"runs": runs, "total": total, "limit": limit, "offset": offset}


@router.get("/api/benchmarks/runs/{run_id}", response_model=BenchmarkRunDetailResponse, dependencies=[Depends(require_viewer)])
@handle_route_errors("get benchmark run")
def get_benchmark_run(run_id: int, db: Session = Depends(get_db)):
    """Get a benchmark run by ID with full result JSON."""
    svc = BenchmarkService(db)
    return svc.get_run(run_id, with_details=True)


@router.post("/api/benchmarks/runs", response_model=BenchmarkRunResponse, status_code=201, dependencies=[Depends(require_operator)])
@handle_route_errors("create benchmark run")
def create_benchmark_run(data: BenchmarkRunCreate, db: Session = Depends(get_db)):
    """Create a new benchmark run (typically triggered from UI to send to an agent)."""
    svc = BenchmarkService(db)
    result = svc.create_run(data.model_dump())
    db.commit()
    return result


@router.post("/api/benchmarks/runs/{run_id}/cancel", response_model=BenchmarkRunResponse, dependencies=[Depends(require_operator)])
@handle_route_errors("cancel benchmark run")
def cancel_benchmark_run(run_id: int, db: Session = Depends(get_db)):
    """Cancel a benchmark run.

    Standalone run: cancel it and stop its aiperf on the owning agent.
    Group child: cancel-the-whole-group — every non-terminal sibling is cancelled
    and the group is finalized CANCELLED. The cancel command goes to whichever
    agent is running the group's live child (which may be a different run id than
    the one passed), so the in-flight aiperf is actually stopped. We do NOT
    dispatch the next pending child — the sweep is being torn down.
    """
    svc = BenchmarkService(db)
    target = svc.get_run(run_id)
    group_id = target.run_group_id

    # Capture the live child BEFORE cancellation flips statuses, so we know which
    # agent's aiperf to terminate even when a pending (not the running) child was
    # the one cancelled.
    if group_id:
        running_child = svc.find_running_group_child(group_id)
        cancel_agent_id = running_child.agent_id if running_child else None
        cancel_run_id = running_child.id if running_child else None
    else:
        cancel_agent_id = target.agent_id
        cancel_run_id = run_id

    result = svc.cancel_run(run_id)
    db.commit()

    # Marking DB rows cancelled is not enough — the agent's aiperf process keeps
    # running (orphaned load). Tell the owning agent to terminate it. Best-effort:
    # a disconnected agent self-terminates on reconnect; a never-dispatched
    # (pending-only) run has nothing to kill.
    if cancel_agent_id and cancel_run_id:
        dispatch_to_agent(cancel_agent_id, {"type": "cancel", "run_id": cancel_run_id})
    return result


@router.delete("/api/benchmarks/runs/{run_id}", status_code=204, dependencies=[Depends(require_operator)])
@handle_route_errors("delete benchmark run")
def delete_benchmark_run(run_id: int, db: Session = Depends(get_db)):
    """Delete a benchmark run and its result data."""
    svc = BenchmarkService(db)
    svc.delete_run(run_id)
    db.commit()


@router.post(
    "/api/benchmarks/runs/{run_id}/baseline",
    response_model=BenchmarkRunResponse,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("set benchmark run baseline")
def set_benchmark_run_baseline(run_id: int, db: Session = Depends(get_db)):
    """Mark a completed run as the baseline for its (target, scenario/config) context.

    Clears any previous baseline in that same context — one baseline per context.
    """
    svc = BenchmarkService(db)
    result = svc.set_baseline(run_id)
    db.commit()
    return result


@router.delete(
    "/api/benchmarks/runs/{run_id}/baseline",
    response_model=BenchmarkRunResponse,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("unset benchmark run baseline")
def unset_benchmark_run_baseline(run_id: int, db: Session = Depends(get_db)):
    """Clear the baseline flag on a run."""
    svc = BenchmarkService(db)
    result = svc.unset_baseline(run_id)
    db.commit()
    return result


# ============================================================================
# Agent Endpoints — test client machine registration
# ============================================================================

@router.post("/api/benchmarks/agents", response_model=BenchmarkAgentResponse, status_code=201)
@handle_route_errors("register benchmark agent")
def register_benchmark_agent(request: Request, data: BenchmarkAgentRegister, db: Session = Depends(get_db)):
    """Register a test client agent.

    Called via curl or script. If an agent with the same name
    already exists, it updates its info and marks it as connected.
    """
    _require_agent_bearer(request)
    svc = BenchmarkService(db)
    result = svc.register_agent(data.model_dump())
    db.commit()
    return result


@router.get("/api/benchmarks/agents", response_model=list[BenchmarkAgentResponse], dependencies=[Depends(require_viewer)])
@handle_route_errors("list benchmark agents")
def list_benchmark_agents(db: Session = Depends(get_db)):
    """List all registered test client agents."""
    svc = BenchmarkService(db)
    return svc.list_agents()


@router.get("/api/benchmarks/agents/{agent_id}", response_model=BenchmarkAgentResponse, dependencies=[Depends(require_viewer)])
@handle_route_errors("get benchmark agent")
def get_benchmark_agent(agent_id: int, db: Session = Depends(get_db)):
    """Get a registered test client agent by ID."""
    svc = BenchmarkService(db)
    return svc.get_agent(agent_id)


@router.delete("/api/benchmarks/agents/{agent_id}", status_code=204)
@handle_route_errors("deregister benchmark agent")
def delete_benchmark_agent(
    agent_id: int,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Deregister a test client agent.

    Mutation route: requires operator role and, for project-scoped (managed)
    agents, project ownership — same pattern as delete_agent_host. Global /
    self-registered agents (project_id NULL) have no owner, so operator alone
    gates them.
    """
    svc = BenchmarkService(db)
    agent = svc.get_agent(agent_id)
    if agent.project_id:
        _check_project_access(agent.project_id, user, db)
    svc.delete_agent(agent_id)
    db.commit()


# ============================================================================
# Forge-managed remote benchmark agent hosts (Slice 1) — SSH-host registration
#
# RBAC decision: mirrors other project-scoped mutation routes.
#   - POST / DELETE: require_operator + project ownership check
#     (same pattern as bare_metal_deployments, discovery routes).
#   - GET list / GET detail: require_viewer (read-only, viewer can see).
#   The project ownership check is inlined here since project_id lives in
#   the request body (POST) or on the host row (DELETE), not in the path.
# ============================================================================

def _check_project_access(project_id: int, user: User, db: Session) -> Project:
    """Raise NotFoundError/ForbiddenError if the user cannot access the project."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise NotFoundError("project", project_id)
    if effective_role(user) == "admin":
        return project
    if project.user_id is not None and project.user_id != user.id:
        raise ForbiddenError("You don't have permission to access this project")
    return project


@router.post("/api/benchmarks/agent-hosts", response_model=BenchmarkAgentHostResponse, status_code=201)
@handle_route_errors("create benchmark agent host")
def create_agent_host(
    data: BenchmarkAgentHostCreate,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Register a remote server as a Forge-managed benchmark agent host.

    Creates a BenchmarkAgent row with managed=True and provision_status='unprovisioned'.
    SSH provisioning (install forge_agent.py + deps) is handled in a later slice.
    """
    _check_project_access(data.project_id, user, db)

    # Uniqueness: BenchmarkAgent.name has a unique constraint
    existing = db.query(BenchmarkAgent).filter_by(name=data.name).first()
    if existing:
        raise BadRequestError(f"Agent host with name '{data.name}' already exists", code="AGENT_HOST_EXISTS")

    agent = BenchmarkAgent(
        name=data.name,
        managed=True,
        project_id=data.project_id,
        host_ip=data.host_ip,
        ssh_credential_id=data.ssh_credential_id,
        ssh_port=data.ssh_port,
        jumphost_chain=data.jumphost_chain,
        provision_status="unprovisioned",
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return agent


@router.get("/api/benchmarks/agent-hosts", response_model=list[BenchmarkAgentHostResponse], dependencies=[Depends(require_viewer)])
@handle_route_errors("list benchmark agent hosts")
def list_agent_hosts(
    project_id: int | None = Query(None, description="Filter by project. Required for non-admin users."),
    db: Session = Depends(get_db),
):
    """List Forge-managed remote benchmark agent hosts, filtered by project."""
    query = db.query(BenchmarkAgent).filter(BenchmarkAgent.managed.is_(True))
    if project_id is not None:
        query = query.filter(BenchmarkAgent.project_id == project_id)
    return query.order_by(BenchmarkAgent.created_at.desc()).all()


@router.get("/api/benchmarks/agent-hosts/{host_id}", response_model=BenchmarkAgentHostResponse, dependencies=[Depends(require_viewer)])
@handle_route_errors("get benchmark agent host")
def get_agent_host(host_id: int, db: Session = Depends(get_db)):
    """Get a Forge-managed remote benchmark agent host by ID."""
    agent = db.query(BenchmarkAgent).filter(
        BenchmarkAgent.id == host_id,
        BenchmarkAgent.managed.is_(True),
    ).first()
    if not agent:
        raise NotFoundError("agent host", host_id)
    return agent


@router.delete("/api/benchmarks/agent-hosts/{host_id}", status_code=204)
@handle_route_errors("delete benchmark agent host")
def delete_agent_host(
    host_id: int,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Remove a Forge-managed remote benchmark agent host registration.

    Best-effort SSH cleanup is dispatched to Celery (never opened in the request
    thread) so an unreachable host can't stall the HTTP DELETE. The row is
    deleted immediately; the async task disables the forge-agent service.
    """
    agent = db.query(BenchmarkAgent).filter(
        BenchmarkAgent.id == host_id,
        BenchmarkAgent.managed.is_(True),
    ).first()
    if not agent:
        raise NotFoundError("agent host", host_id)
    if agent.project_id:
        _check_project_access(agent.project_id, user, db)

    # Capture connection params before the row is gone, then dispatch the
    # best-effort service teardown to Celery so a dead host never blocks delete.
    needs_cleanup = bool(
        agent.provision_status == "provisioned" and agent.host_ip and agent.ssh_credential_id
    )
    cleanup_args = (
        (agent.ssh_credential_id, agent.host_ip, agent.ssh_port, agent.jumphost_chain)
        if needs_cleanup
        else None
    )

    db.delete(agent)
    db.commit()

    if cleanup_args is not None:
        from tasks.benchmark_agent_tasks import cleanup_benchmark_agent_host
        cleanup_benchmark_agent_host.delay(*cleanup_args)
        logger.info("Dispatched forge-agent cleanup for removed host %d (%s)", host_id, cleanup_args[1])


@router.post(
    "/api/benchmarks/agent-hosts/{host_id}/scan",
    response_model=AgentHostScanResponse,
    status_code=202,
)
@handle_route_errors("scan benchmark agent host")
def scan_agent_host(
    host_id: int,
    data: AgentHostScanRequest | None = None,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Dispatch an SSH suitability scan for a Forge-managed benchmark agent host.

    Returns 202 Accepted immediately. The Celery task:
      1. SSH-connects to the host.
      2. Probes OS / CPU / memory / tool presence.
      3. Tests network reachability to the project's BenchmarkTargets.
      4. Writes a 'readiness' JSON + provision_status back to the DB row.

    Poll GET /api/benchmarks/agent-hosts/{id} for results.
    """
    agent = db.query(BenchmarkAgent).filter(
        BenchmarkAgent.id == host_id,
        BenchmarkAgent.managed.is_(True),
    ).first()
    if not agent:
        raise NotFoundError("agent host", host_id)
    if agent.project_id:
        _check_project_access(agent.project_id, user, db)

    target_ids = (data.target_ids if data else None)

    from tasks.benchmark_agent_tasks import scan_benchmark_agent_host
    task = scan_benchmark_agent_host.delay(host_id, target_ids)

    return AgentHostScanResponse(
        host_id=host_id,
        message="SSH scan dispatched — poll GET /api/benchmarks/agent-hosts/{id} for readiness",
        celery_task_id=task.id,
    )


@router.post(
    "/api/benchmarks/agent-hosts/{host_id}/provision",
    response_model=AgentHostProvisionResponse,
    status_code=202,
)
@handle_route_errors("provision benchmark agent host")
def provision_agent_host(
    host_id: int,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Dispatch SSH provisioning for a Forge-managed benchmark agent host.

    Returns 202 Accepted immediately. The Celery task:
      1. Waits for SSH availability.
      2. Installs build-essential, python3 venv, aiperf, websockets, requests.
      3. Uploads forge_agent.py to /opt/forge/forge_agent.py.
      4. Mints a 365-day per-agent JWT token.
      5. Writes /etc/forge/agent.env (EnvironmentFile, mode 600).
      6. Installs and starts the forge-agent.service systemd unit
         (falls back to nohup when systemctl is absent).

    Prerequisite: FORGE_EXTERNAL_URL must be set in Forge's config — it is the
    address the remote host uses to dial back to Forge. The host must already
    have Python 3.10+ available.

    Poll GET /api/benchmarks/agent-hosts/{id} for provision_status / provision_message.
    """
    agent = db.query(BenchmarkAgent).filter(
        BenchmarkAgent.id == host_id,
        BenchmarkAgent.managed.is_(True),
    ).first()
    if not agent:
        raise NotFoundError("agent host", host_id)
    if agent.project_id:
        _check_project_access(agent.project_id, user, db)

    from tasks.benchmark_agent_tasks import provision_benchmark_agent_host
    task = provision_benchmark_agent_host.delay(host_id)

    return AgentHostProvisionResponse(
        host_id=host_id,
        message="SSH provisioning dispatched — poll GET /api/benchmarks/agent-hosts/{id} for progress",
        celery_task_id=task.id,
    )


# ============================================================================
# Agent Host Candidates (Slice 5) — project-sourced host/jumphost picker
# ============================================================================

@router.get(
    "/api/benchmarks/agent-host-candidates",
    response_model=AgentHostCandidatesResponse,
)
@handle_route_errors("list agent host candidates")
def list_agent_host_candidates(
    project_id: int = Query(..., description="Project to gather candidates for"),
    user: User = Depends(require_viewer),
    db: Session = Depends(get_db),
):
    """Aggregate host/jumphost candidates from Forge-tracked resources.

    Returns bare-metal hosts, cluster bastions, global SSH credentials,
    and AWS deployment jumphosts for the given project. Each candidate
    carries enough info to pre-fill the RegisterRemoteHostDialog form.
    """
    _check_project_access(project_id, user, db)
    from services.agent_host_candidates_service import AgentHostCandidatesService
    candidates = AgentHostCandidatesService(db).list_candidates(project_id)
    return AgentHostCandidatesResponse(candidates=candidates, project_id=project_id)


@router.post(
    "/api/benchmarks/agent-host-candidates/import-aws-jumphost",
    response_model=dict,
    status_code=201,
)
@handle_route_errors("import AWS jumphost credential")
def import_aws_jumphost(
    data: ImportAwsJumphostRequest,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Import an AWS deployment's jumphost/bastion as an SSHCredential.

    Reads the durable PEM written by infrastructure_access_service + the
    bastion address from jumphost_ssh_command, and creates (or returns an
    existing) SSHCredential so the host can be registered.

    Returns: {"ssh_credential_id": <id>}
    """
    _check_project_access(data.project_id, user, db)
    from services.agent_host_candidates_service import AgentHostCandidatesService
    cred_id = AgentHostCandidatesService(db).import_aws_jumphost(data.project_id, data.module_id)
    db.commit()
    return {"ssh_credential_id": cred_id}


# ============================================================================
# Comparison & Summary
# ============================================================================

@router.post("/api/benchmarks/compare", response_model=BenchmarkCompareResponse, dependencies=[Depends(require_viewer)])
@handle_route_errors("compare benchmark runs")
def compare_benchmark_runs(data: BenchmarkCompareRequest, db: Session = Depends(get_db)):
    """Compare multiple benchmark runs side-by-side (proxy comparison)."""
    svc = BenchmarkService(db)
    return svc.compare_runs(data.run_ids)


@router.get("/api/benchmarks/summary", response_model=BenchmarkSummaryResponse, dependencies=[Depends(require_viewer)])
@handle_route_errors("get benchmark summary")
def get_benchmark_summary(db: Session = Depends(get_db)):
    """Get dashboard summary of benchmark activity."""
    svc = BenchmarkService(db)
    return svc.get_summary()


@router.get("/api/benchmarks/trends", response_model=BenchmarkTrendsResponse, dependencies=[Depends(require_viewer)])
@handle_route_errors("get benchmark trends")
def get_benchmark_trends(
    target_id: int | None = Query(None),
    proxy: str | None = Query(None),
    scenario_key: str | None = Query(None),
    config_id: int | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Time-ordered completed-run metrics for a target/proxy/scenario/config context,
    with the current baseline (if any) always included."""
    svc = BenchmarkService(db)
    return svc.get_trends(target_id=target_id, proxy=proxy, scenario_key=scenario_key, config_id=config_id, limit=limit)


# ============================================================================
# Benchmark Target Endpoints (Phase 4b)
# ============================================================================

@router.get("/api/benchmarks/targets", response_model=BenchmarkTargetListResponse, dependencies=[Depends(require_viewer)])
@handle_route_errors("list benchmark targets")
def list_benchmark_targets(
    status: str | None = Query(None),
    cluster_id: int | None = Query(None),
    db: Session = Depends(get_db),
):
    """List all benchmark targets."""
    svc = BenchmarkTargetService(db)
    targets, total = svc.list_targets(status=status, cluster_id=cluster_id)
    return {"targets": targets, "total": total}


@router.get("/api/benchmarks/targets/{target_id}", response_model=BenchmarkTargetDetailResponse, dependencies=[Depends(require_viewer)])
@handle_route_errors("get benchmark target")
def get_benchmark_target(target_id: int, db: Session = Depends(get_db)):
    """Get a benchmark target by ID with proxy deployments."""
    svc = BenchmarkTargetService(db)
    return svc.get_target(target_id, with_details=True)


@router.post("/api/benchmarks/targets", response_model=BenchmarkTargetResponse, status_code=201, dependencies=[Depends(require_operator)])
@handle_route_errors("create benchmark target")
def create_benchmark_target(data: BenchmarkTargetCreate, db: Session = Depends(get_db)):
    """Create a new benchmark target."""
    svc = BenchmarkTargetService(db)
    result = svc.create_target(data.model_dump())
    db.commit()
    return result


@router.put("/api/benchmarks/targets/{target_id}", response_model=BenchmarkTargetResponse, dependencies=[Depends(require_operator)])
@handle_route_errors("update benchmark target")
def update_benchmark_target(target_id: int, data: BenchmarkTargetUpdate, db: Session = Depends(get_db)):
    """Update a benchmark target."""
    svc = BenchmarkTargetService(db)
    result = svc.update_target(target_id, data.model_dump(exclude_unset=True))
    db.commit()
    return result


@router.delete("/api/benchmarks/targets/{target_id}", status_code=204, dependencies=[Depends(require_operator)])
@handle_route_errors("delete benchmark target")
def delete_benchmark_target(target_id: int, db: Session = Depends(get_db)):
    """Delete a benchmark target (cascades to proxy deployments)."""
    svc = BenchmarkTargetService(db)
    svc.delete_target(target_id)
    db.commit()


@router.post("/api/benchmarks/targets/{target_id}/validate", response_model=BenchmarkTargetResponse)
@handle_route_errors("validate benchmark target")
def validate_benchmark_target(target_id: int, db: Session = Depends(get_db)):
    """Test connectivity to the target's LLM endpoint."""
    svc = BenchmarkTargetService(db)
    result = svc.validate_target(target_id)
    db.commit()
    return result


# ============================================================================
# Target Discovery (Phase 5b) — scan cluster for LLM services + auto-create targets
# ============================================================================

@router.post(
    "/api/benchmarks/discover-targets",
    response_model=DiscoverTargetsResponse,
)
@handle_route_errors("discover benchmark targets on cluster")
def discover_targets(data: DiscoverTargetsRequest, db: Session = Depends(get_db)):
    """Scan a K8s cluster for LLM inference services and auto-create benchmark targets.

    One-click cluster scan that:
    1. Discovers LLM services (vLLM, TGI, LiteLLM, Ollama, etc.) by name, labels, ports, images
    2. Auto-creates BenchmarkTarget records for each discovered service
    3. Runs proxy discovery on each new target (envoy, nginx, haproxy, f5-bnk, nodeport)

    Skips services that already have a matching BenchmarkTarget (same cluster + URL).
    """
    from services.target_discovery_service import TargetDiscoveryService

    svc = TargetDiscoveryService(db)
    result = svc.discover_targets(
        data.cluster_id,
        auto_create=data.auto_create,
        run_proxy_discovery=data.auto_create,
        selected_urls=data.selected_services,
    )
    db.commit()

    return result


# ============================================================================
# Proxy Discovery (Phase 5) — scan cluster for existing proxies
# ============================================================================

@router.post(
    "/api/benchmarks/targets/{target_id}/discover-proxies",
    response_model=ProxyDiscoveryResponse,
)
@handle_route_errors("discover proxies on cluster")
def discover_proxies(target_id: int, db: Session = Depends(get_db)):
    """Scan the target's K8s cluster for existing proxy deployments.

    Discovers envoy, nginx, haproxy, f5-bnk, and nodeport (direct LLM access)
    in parallel. Auto-creates ProxyDeployment records with status='discovered'
    for any proxies found on the cluster.

    This is the primary entry point for the discovery-first architecture:
    users create a target, then discover what's already running on the cluster.
    """
    from services.proxy_discovery_service import ProxyDiscoveryService

    target_svc = BenchmarkTargetService(db)
    target = target_svc.get_target(target_id)

    discovery = ProxyDiscoveryService(db)
    results = discovery.discover_all(target, auto_create=True)
    db.commit()

    discovered_count = sum(1 for r in results if r["found"])

    return ProxyDiscoveryResponse(
        target_id=target.id,
        target_name=target.name,
        cluster_id=target.cluster_id,
        results=results,
        discovered_count=discovered_count,
        total_scanned=len(results),
    )


# ============================================================================
# Proxy Deployment Endpoints (Phase 4b)
# ============================================================================

@router.get("/api/benchmarks/targets/{target_id}/proxies", response_model=list[ProxyDeploymentResponse], dependencies=[Depends(require_viewer)])
@handle_route_errors("list proxy deployments")
def list_proxy_deployments(target_id: int, db: Session = Depends(get_db)):
    """List all proxy deployments for a target."""
    svc = BenchmarkTargetService(db)
    return svc.list_proxy_deployments(target_id)


@router.get("/api/benchmarks/targets/{target_id}/proxies/{proxy_id}", response_model=ProxyDeploymentResponse, dependencies=[Depends(require_viewer)])
@handle_route_errors("get proxy deployment")
def get_proxy_deployment(target_id: int, proxy_id: int, db: Session = Depends(get_db)):
    """Get a proxy deployment by ID."""
    svc = BenchmarkTargetService(db)
    return svc.get_proxy_deployment(target_id, proxy_id)


@router.post("/api/benchmarks/targets/{target_id}/proxies", response_model=ProxyDeploymentResponse, status_code=201, dependencies=[Depends(require_operator)])
@handle_route_errors("deploy proxy to target")
def deploy_proxy(target_id: int, data: ProxyDeployRequest, db: Session = Depends(get_db)):
    """Deploy a proxy to a target cluster.

    Creates the ProxyDeployment record, then dispatches a Celery task
    for async Helm install. Returns the record immediately with status='pending'.
    The frontend polls or subscribes to WebSocket for deploy progress.
    """
    svc = BenchmarkTargetService(db)
    result = svc.deploy_proxy(target_id, data.model_dump())
    db.commit()

    # Dispatch async Helm install via Celery
    from tasks.proxy_deploy_tasks import deploy_proxy_task
    task = deploy_proxy_task.delay(result.id)

    # Store task ID so the frontend can poll Celery status
    result.celery_task_id = task.id
    db.commit()

    return result


@router.put("/api/benchmarks/targets/{target_id}/proxies/{proxy_id}", response_model=ProxyDeploymentResponse)
@handle_route_errors("update proxy deployment")
def update_proxy_deployment(target_id: int, proxy_id: int, data: ProxyDeploymentUpdate, db: Session = Depends(get_db)):
    """Update a proxy deployment."""
    svc = BenchmarkTargetService(db)
    result = svc.update_proxy_deployment(target_id, proxy_id, data.model_dump(exclude_unset=True))
    db.commit()
    return result


@router.delete("/api/benchmarks/targets/{target_id}/proxies/{proxy_id}", status_code=202, dependencies=[Depends(require_operator)])
@handle_route_errors("undeploy proxy")
def delete_proxy_deployment(target_id: int, proxy_id: int, db: Session = Depends(get_db)):
    """Undeploy (Helm uninstall) a proxy, then delete the record.

    Dispatches a Celery task for async Helm uninstall. Returns 202 Accepted.
    The record is kept until uninstall completes (frontend can poll status).
    """
    svc = BenchmarkTargetService(db)
    deploy = svc.get_proxy_deployment(target_id, proxy_id)

    # If the proxy was never actually deployed, just delete the record
    if deploy.status in (ProxyDeploymentStatus.PENDING, ProxyDeploymentStatus.UNINSTALLED):
        svc.delete_proxy_deployment(target_id, proxy_id)
        db.commit()
        return

    # Dispatch async Helm uninstall
    from tasks.proxy_deploy_tasks import undeploy_proxy_task
    task = undeploy_proxy_task.delay(proxy_id)
    deploy.celery_task_id = task.id
    db.commit()


@router.post("/api/benchmarks/targets/{target_id}/proxies/{proxy_id}/redeploy", response_model=ProxyDeploymentResponse)
@handle_route_errors("redeploy proxy")
def redeploy_proxy(target_id: int, proxy_id: int, db: Session = Depends(get_db)):
    """Redeploy a proxy after config change.

    Resets status to 'pending' and dispatches a fresh Celery deploy task.
    """
    svc = BenchmarkTargetService(db)
    result = svc.redeploy_proxy(target_id, proxy_id)
    db.commit()

    # Dispatch async Helm install
    from tasks.proxy_deploy_tasks import deploy_proxy_task
    task = deploy_proxy_task.delay(proxy_id)
    result.celery_task_id = task.id
    db.commit()

    return result


@router.get(
    "/api/benchmarks/targets/{target_id}/proxies/{proxy_id}/task-status",
    response_model=ProxyTaskStatusResponse,
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("get proxy deploy task status")
def get_proxy_task_status(target_id: int, proxy_id: int, db: Session = Depends(get_db)):
    """Poll the Celery task status for a proxy deploy/undeploy operation.

    Returns the ProxyDeployment with up-to-date status (the Celery task
    writes directly to the DB, so a simple DB read is sufficient).
    Also includes the Celery task state for richer progress info.
    """
    svc = BenchmarkTargetService(db)
    deploy = svc.get_proxy_deployment(target_id, proxy_id)

    response: dict = {
        "proxy_id": deploy.id,
        "status": deploy.status,
        "status_message": deploy.status_message,
        "proxy_url": deploy.proxy_url,
        "celery_task_id": deploy.celery_task_id,
        "celery_state": None,
    }

    # If there's a Celery task, also fetch its state
    if deploy.celery_task_id:
        from celery_app import celery_app as _celery
        async_result = _celery.AsyncResult(deploy.celery_task_id)
        response["celery_state"] = async_result.state  # PENDING, STARTED, SUCCESS, FAILURE

    return response


# ============================================================================
# Run Orchestration (Phase 4d) — trigger benchmark against a deployed proxy
# ============================================================================

@router.post(
    "/api/benchmarks/targets/{target_id}/proxies/{proxy_id}/run",
    response_model=TriggerRunResponse,
    status_code=201,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("trigger benchmark run")
def trigger_benchmark_run(
    target_id: int,
    proxy_id: int,
    data: TriggerRunRequest,
    db: Session = Depends(get_db),
):
    """Trigger a benchmark run against a deployed proxy.

    Builds a RunConfig from the proxy's target info + config preset,
    creates a BenchmarkRun row, and sends the run command to a
    connected agent via WebSocket.

    Phase 4d: Full run orchestration flow.
    """
    target_svc = BenchmarkTargetService(db)
    bench_svc = BenchmarkService(db)

    # 1. Validate proxy is ready (or discovered — already on cluster)
    deploy = target_svc.get_proxy_deployment(target_id, proxy_id)
    if deploy.status not in (ProxyDeploymentStatus.READY, ProxyDeploymentStatus.DISCOVERED):
        raise BadRequestError(
            f"Proxy is not ready (status={deploy.status}). Deploy or discover it first.",
            code="PROXY_NOT_READY",
        )

    target = target_svc.get_target(target_id)

    # 2. Resolve agent
    agent_id = data.agent_id
    if not agent_id:
        # Pick the first connected agent
        agents = bench_svc.list_agents()
        connected = [a for a in agents if a.status == BenchmarkAgentStatus.CONNECTED]
        if not connected:
            raise BadRequestError(
                "No connected agents. Register one via POST /api/benchmarks/agents first.",
                code="NO_AGENT",
            )
        agent_id = connected[0].id

    agent = bench_svc.get_agent(agent_id)
    if agent.status != BenchmarkAgentStatus.CONNECTED:
        raise BadRequestError(
            f"Agent '{agent.name}' is not connected (status={agent.status})",
            code="AGENT_NOT_CONNECTED",
        )

    # 3. Build RunConfig — keys map directly to aiperf CLI flags
    #    See: https://github.com/ai-dynamo/aiperf/blob/main/docs/cli-options.md
    base_url = deploy.proxy_url or target.llm_base_url

    config_json: dict = {
        "url": base_url,
        "model": target.llm_model,
        "endpoint_type": "chat",
        "endpoint": target.llm_endpoint or "/v1/chat/completions",
        "streaming": True,
        "request_timeout_seconds": data.timeout or 600,
        # Sane defaults so a quick "Run Test" is a meaningful benchmark, not
        # aiperf's bare defaults (concurrency 1, ~10 requests, UNBOUNDED output)
        # which yield ~20s/req throughput that reads as a catastrophic proxy
        # regression. Mirrors the `baseline` scenario's single operating point.
        "concurrency": 50,
        "request_count": 250,
        "synthetic_input_tokens_mean": 500,
        "output_tokens_mean": 128,
        "extra_inputs": ["ignore_eos:true"],
        "ui": "none",
        # Forge metadata (not aiperf flags)
        "_proxy_type": deploy.proxy_type,
        "_target_name": target.name,
    }

    if data.total_requests:
        config_json["request_count"] = data.total_requests
    if data.max_tokens:
        config_json["output_tokens_mean"] = data.max_tokens

    # Merge saved config if provided
    if data.config_id:
        config = bench_svc.get_config(data.config_id)
        saved = config.config_json or {}
        # Saved config provides defaults, but target/proxy info overrides
        merged = {**saved, **config_json}
        config_json = merged

    # 4. Create BenchmarkRun
    run = bench_svc.create_run({
        "config_id": data.config_id,
        "agent_id": agent_id,
        "target_id": target_id,
        "proxy_deployment_id": proxy_id,
        "tool": config_json.get("tool", "aiperf"),
        "proxy": deploy.proxy_type,
        "model": target.llm_model,
        "base_url": base_url,
        "run_label": data.run_label or f"{deploy.proxy_type}-{target.name}",
        "tags": data.tags,
        "config_snapshot": config_json,
        "status": BenchmarkRunStatus.PENDING,
    })
    db.commit()

    # 5. Send command to agent via WebSocket (scheduled on the WS-owning loop)
    command = {
        "type": "run",
        "run_id": run.id,
        "config": config_json,
    }

    # If the agent isn't connected via WS, the run stays pending and the agent
    # can pick it up when it reconnects.
    sent = dispatch_to_agent(agent_id, command)

    if sent:
        run.status = BenchmarkRunStatus.RUNNING
        run.started_at = datetime.now(UTC)
        db.commit()
        msg = f"Run #{run.id} dispatched to agent '{agent.name}'"
    else:
        msg = f"Run #{run.id} created but agent '{agent.name}' not connected via WS — run is pending"

    return TriggerRunResponse(
        run_id=run.id,
        agent_id=agent_id,
        proxy_id=proxy_id,
        target_id=target_id,
        status=run.status,
        message=msg,
    )


# ============================================================================
# Scenario Orchestration (Phase 6) — scenario → run-group + child runs
# ============================================================================

@router.get(
    "/api/benchmarks/scenarios",
    response_model=ScenarioCatalogResponse,
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("list benchmark scenarios")
def list_benchmark_scenarios():
    """List all available benchmark scenario presets and how many child runs each expands into."""
    from services.benchmark_scenarios import list_scenarios

    scenarios = list_scenarios()
    return ScenarioCatalogResponse(scenarios=scenarios, total=len(scenarios))


@router.get(
    "/api/benchmarks/run-groups/{group_id}",
    response_model=RunGroupResponse,
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("get benchmark run-group")
def get_benchmark_run_group(group_id: int, db: Session = Depends(get_db)):
    """Get a run-group with its child runs and aggregate metrics."""
    svc = BenchmarkService(db)
    group = svc.get_run_group(group_id)
    return _serialize_run_group(group)


@router.post(
    "/api/benchmarks/targets/{target_id}/proxies/{proxy_id}/run-scenario",
    response_model=ScenarioRunResponse,
    status_code=201,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("run benchmark scenario")
def run_benchmark_scenario(
    target_id: int,
    proxy_id: int,
    data: ScenarioRunRequest,
    db: Session = Depends(get_db),
):
    """Run a SCENARIO against a deployed proxy.

    Expands ``scenario_key`` into a parent run-group plus N child runs (one per
    concurrency point and/or phase), then dispatches each child run to a connected
    agent over the existing WebSocket protocol (one aiperf invocation per child).
    """
    target_svc = BenchmarkTargetService(db)
    bench_svc = BenchmarkService(db)

    # 1. Validate proxy is ready (or discovered — already on cluster)
    deploy = target_svc.get_proxy_deployment(target_id, proxy_id)
    if deploy.status not in (ProxyDeploymentStatus.READY, ProxyDeploymentStatus.DISCOVERED):
        raise BadRequestError(
            f"Proxy is not ready (status={deploy.status}). Deploy or discover it first.",
            code="PROXY_NOT_READY",
        )

    target = target_svc.get_target(target_id)

    # 2. Resolve agent
    agent_id = data.agent_id
    if not agent_id:
        agents = bench_svc.list_agents()
        connected = [a for a in agents if a.status == BenchmarkAgentStatus.CONNECTED]
        if not connected:
            raise BadRequestError(
                "No connected agents. Register one via POST /api/benchmarks/agents first.",
                code="NO_AGENT",
            )
        agent_id = connected[0].id

    agent = bench_svc.get_agent(agent_id)
    if agent.status != BenchmarkAgentStatus.CONNECTED:
        raise BadRequestError(
            f"Agent '{agent.name}' is not connected (status={agent.status})",
            code="AGENT_NOT_CONNECTED",
        )

    base_url = deploy.proxy_url or target.llm_base_url

    # 3. Expand scenario into a run-group + child runs
    group, runs = bench_svc.create_run_group_from_scenario(
        scenario_key=data.scenario_key,
        base_url=base_url,
        endpoint=target.llm_endpoint or "/v1/chat/completions",
        model=target.llm_model,
        target_id=target_id,
        proxy_id=proxy_id,
        proxy_type=deploy.proxy_type,
        agent_id=agent_id,
        run_label=data.run_label,
        tags=data.tags,
        overrides=data.overrides,
    )
    db.commit()

    # 4. GATED dispatch: send ONLY the first child now. The remaining children stay
    #    PENDING and are dispatched one at a time as each completes (see the
    #    run_completed/run_failed WS handlers). This guarantees runs execute strictly
    #    sequentially — never concurrently — at the dispatch layer, independent of the
    #    agent's own serialization, and keeps the group status truthful.
    dispatched = 0
    if runs:
        first = runs[0]
        command = {"type": "run", "run_id": first.id, "config": first.config_snapshot}
        if dispatch_to_agent(agent_id, command):
            first.status = BenchmarkRunStatus.RUNNING
            first.started_at = datetime.now(UTC)
            dispatched = 1

    if dispatched:
        group.status = BenchmarkRunStatus.RUNNING
        group.started_at = datetime.now(UTC)
    db.commit()

    msg = (
        f"Scenario '{data.scenario_key}' run-group #{group.id}: {len(runs)} child runs "
        f"queued, running sequentially on agent '{agent.name}' (1 at a time)"
    )
    return ScenarioRunResponse(
        run_group_id=group.id,
        scenario_key=data.scenario_key,
        agent_id=agent_id,
        proxy_id=proxy_id,
        target_id=target_id,
        status=group.status,
        total_runs=len(runs),
        dispatched_runs=dispatched,
        message=msg,
    )


def _serialize_run_group(group) -> RunGroupResponse:
    """Build a RunGroupResponse from a BenchmarkRunGroup ORM row + its child runs."""
    return RunGroupResponse(
        id=group.id,
        scenario_key=group.scenario_key,
        scenario_name=group.scenario_name,
        run_label=group.run_label,
        status=group.status,
        target_id=group.target_id,
        proxy=group.proxy,
        model=group.model,
        total_runs=group.total_runs,
        completed_runs=group.completed_runs,
        failed_runs=group.failed_runs,
        created_at=group.created_at,
        agent_id=group.agent_id,
        base_url=group.base_url,
        tags=group.tags,
        error_message=group.error_message,
        aggregate_json=group.aggregate_json,
        avg_latency_p50=group.avg_latency_p50,
        avg_latency_p99=group.avg_latency_p99,
        peak_rps=group.peak_rps,
        total_output_tokens=group.total_output_tokens,
        started_at=group.started_at,
        completed_at=group.completed_at,
        updated_at=group.updated_at,
        runs=[
            {
                "id": r.id,
                "variant_label": r.variant_label,
                "status": r.status,
                "concurrency": (r.config_snapshot or {}).get("concurrency"),
                "latency_p50": r.latency_p50,
                "latency_p99": r.latency_p99,
                "overall_rps": r.overall_rps,
                "tokens_per_sec": r.tokens_per_sec,
            }
            for r in group.runs
        ],
    )


# ============================================================================
# WebSocket — Agent connections (test clients connect here)
# ============================================================================

# Active WebSocket connections per agent_id
_agent_ws_connections: dict[int, WebSocket] = {}
# The event loop that owns the agent WebSockets (uvicorn's main loop). Captured
# when an agent connects so SYNC route handlers can schedule sends ON THAT LOOP
# via run_coroutine_threadsafe — sending on a WS from a freshly-created loop is
# undefined and silently drops commands (the cause of stuck-pending runs).
_main_loop: asyncio.AbstractEventLoop | None = None


def _status_for_heartbeat(reported_status: str | None) -> str:
    """Map an agent's self-reported heartbeat status to a connection status.

    A heartbeat is proof the agent is alive, so anything other than an explicit
    "running" collapses to "connected". This lets a stale "disconnected" (left by
    a prior connection's teardown racing a reconnect) self-heal on the next
    heartbeat instead of wedging dispatch with AGENT_NOT_CONNECTED while
    last_heartbeat keeps advancing.
    """
    return "running" if reported_status == "running" else "connected"


def _owns_agent_connection(agent_id: int, websocket: WebSocket) -> bool:
    """True if `websocket` is still the registered connection for `agent_id`.

    Guards teardown against an agent pod restart: the new connection overwrites
    _agent_ws_connections[agent_id] before the old handler's finally runs, so the
    old handler must not pop the new ws or flip a live agent to "disconnected".
    """
    return _agent_ws_connections.get(agent_id) is websocket


def _agent_owns_run(svc: "BenchmarkService", agent_id: int, run_id: int) -> bool:
    """Ownership guard (M2): only the agent a run was dispatched to may report it.

    Without this an authenticated-but-arbitrary peer could spoof results for any
    run_id. Returns False (and logs) on a missing run or an agent mismatch so the
    caller skips the mutation entirely.
    """
    try:
        run = svc.get_run(run_id)
    except Exception:
        logger.warning("Agent %d reported result for unknown run #%d — skipping", agent_id, run_id)
        return False
    if run.agent_id != agent_id:
        logger.warning(
            "Agent %d reported result for run #%d owned by agent %s — skipping (spoof guard)",
            agent_id, run_id, run.agent_id,
        )
        return False
    return True


def _agent_ws_authorized(websocket: WebSocket, agent_id: int) -> int | None:
    """Validate the agent WS handshake JWT (M2 + agent-auth layer).

    Reads ``?token=<JWT>`` and verifies it with the same util the HTTP auth path
    uses (services.auth_service.decode_token).

    Two orthogonal layers are honored:
      - BENCHMARK_AGENT_AUTH_REQUIRED (agent-specific, second layer): when ON a
        token is mandatory, must be valid, and must carry an ``agent_id`` claim
        matching the path agent_id — rejection closes 4401. A token with no
        agent_id claim is rejected: it authenticates a caller but does not
        identify an agent.
      - REQUIRE_AUTH (global JWT, M2): when ON a valid token is required —
        rejection closes 4001. When OFF the connection is accepted (local
        no-auth deployments), mirroring AuthMiddleware.

    When the agent flag is OFF but a token is present it is validated anyway (so
    the built-in agent works in both modes), but an invalid token does not reject.

    Returns the WS close code to reject with, or ``None`` if authorized.
    """
    from core.config import settings

    token = websocket.query_params.get("token")

    # Layer 1 — agent-specific auth (close 4401)
    if settings.BENCHMARK_AGENT_AUTH_REQUIRED:
        if not token:
            logger.warning("Agent %d WS rejected: no token (agent auth required)", agent_id)
            return 4401
        from core.errors import UnauthorizedError
        from services.auth_service import decode_token

        try:
            payload = decode_token(token)
        except UnauthorizedError:
            logger.warning("Agent %d WS rejected: invalid token", agent_id)
            return 4401
        # Identity binding (#41 F5). A token WITHOUT an agent_id claim used to
        # skip this check entirely, so any valid token -- a viewer's included --
        # could connect as any agent_id and send heartbeat/progress or flip agent
        # status. Authentication is not identity: when agent auth is required the
        # claim is mandatory, not merely honoured when present.
        token_agent_id = payload.get("agent_id")
        if token_agent_id is None:
            logger.warning(
                "Agent %d WS rejected: token carries no agent_id claim (agent auth required)",
                agent_id,
            )
            return 4401
        try:
            claim_matches = int(token_agent_id) == agent_id
        except (TypeError, ValueError):
            logger.warning(
                "Agent %d WS rejected: non-numeric agent_id claim %r", agent_id, token_agent_id
            )
            return 4401
        if not claim_matches:
            logger.warning(
                "Agent %d WS rejected: token agent_id=%s does not match path", agent_id, token_agent_id
            )
            return 4401
        return None

    # Agent flag OFF but token present — validate but never reject on failure.
    if token:
        try:
            from services.auth_service import decode_token

            decode_token(token)
        except Exception:
            logger.warning("Agent %d WS: invalid token provided (flag off, continuing anyway)", agent_id)

    # Layer 2 — global JWT auth (close 4001)
    if not settings.REQUIRE_AUTH:
        return None
    if not token:
        return 4001
    try:
        from services.auth_service import decode_token

        decode_token(token)
        return None
    except Exception:
        return 4001


@ws_router.websocket("/ws/benchmarks/agents/{agent_id}")
async def agent_websocket(websocket: WebSocket, agent_id: int):
    """WebSocket endpoint for test client agent connections.

    The agent connects here after registering via POST /api/benchmarks/agents.
    Forge can send commands (run, cancel, status, ping) down this connection.
    The agent sends heartbeats and progress updates back.

    Auth: the agent connects with ``?token=<JWT>``. The WS path is exempt from
    the HTTP AuthMiddleware, so this endpoint validates the token itself.
      - BENCHMARK_AGENT_AUTH_REQUIRED ON  → token + agent_id claim match (close 4401).
      - REQUIRE_AUTH ON (global JWT, M2)  → valid token required (close 4001).
    """
    close_code = _agent_ws_authorized(websocket, agent_id)
    if close_code is not None:
        await websocket.close(code=close_code)
        logger.warning("Agent %d WS rejected: missing/invalid token", agent_id)
        return

    await websocket.accept()
    global _main_loop
    _main_loop = asyncio.get_running_loop()
    _agent_ws_connections[agent_id] = websocket
    logger.info("Agent %d connected via WebSocket", agent_id)

    # Mark agent as connected
    db = next(get_db())
    try:
        svc = BenchmarkService(db)
        svc.update_agent_status(agent_id, "connected")
        db.commit()
    except Exception:
        pass
    finally:
        db.close()

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type", "")

            if msg_type == "heartbeat":
                # Update heartbeat timestamp
                db = next(get_db())
                try:
                    svc = BenchmarkService(db)
                    svc.update_agent_heartbeat(agent_id)
                    svc.update_agent_status(agent_id, _status_for_heartbeat(msg.get("status")))
                    db.commit()
                except Exception:
                    pass
                finally:
                    db.close()

            elif msg_type == "progress":
                # Broadcast to any dashboard WebSocket watchers
                run_id = msg.get("run_id")
                if run_id:
                    await broadcast_run_update(int(run_id), msg)

            elif msg_type == "run_completed":
                # Agent finished a run — update run with result data
                run_id = msg.get("run_id")
                result_data = msg.get("result")
                db = next(get_db())
                try:
                    svc = BenchmarkService(db)
                    svc.update_agent_status(agent_id, "connected")
                    if run_id and result_data and _agent_owns_run(svc, agent_id, int(run_id)):
                        svc.complete_run_with_aiperf_result(int(run_id), result_data)
                        logger.info("Run #%d completed by agent %d — result ingested", run_id, agent_id)
                        # Gated dispatch: now that this child is done, send the next pending
                        # child of its run-group (one aiperf at a time, strictly sequential).
                        done = svc.get_run(int(run_id))
                        if done.run_group_id:
                            await _dispatch_next_group_child(svc, agent_id, done.run_group_id)
                    db.commit()
                except Exception as e:
                    logger.error("Error ingesting run_completed for run #%s: %s", run_id, e)
                    db.rollback()
                finally:
                    db.close()
                # Broadcast completion to dashboard watchers
                if run_id:
                    await broadcast_run_update(int(run_id), msg)

            elif msg_type == "run_failed":
                # Agent reported a failed run
                run_id = msg.get("run_id")
                error_msg = msg.get("error", "Agent reported failure")
                db = next(get_db())
                try:
                    svc = BenchmarkService(db)
                    svc.update_agent_status(agent_id, "connected")
                    if run_id and _agent_owns_run(svc, agent_id, int(run_id)):
                        run = svc.get_run(int(run_id))
                        if run and run.status in ("pending", "running"):
                            run.status = BenchmarkRunStatus.FAILED
                            run.error_message = str(error_msg)[:1000]
                            run.completed_at = datetime.now(UTC)
                            logger.warning("Run #%d failed by agent %d: %s", run_id, agent_id, error_msg)
                            # Roll up the parent run-group when a child fails.
                            if run.run_group_id:
                                svc.maybe_finalize_run_group(run.run_group_id)
                                # Gated dispatch: continue the sweep with the next child.
                                await _dispatch_next_group_child(svc, agent_id, run.run_group_id)
                    db.commit()
                except Exception as e:
                    logger.error("Error handling run_failed for run #%s: %s", run_id, e)
                    db.rollback()
                finally:
                    db.close()
                # Broadcast failure to dashboard watchers
                if run_id:
                    await broadcast_run_update(int(run_id), msg)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error("Agent WebSocket error for agent %d: %s", agent_id, e)
    finally:
        # Only tear down if THIS connection is still the registered one (see
        # _owns_agent_connection): an agent pod restart replaces the registry entry
        # before this old handler's finally runs, and clobbering would pop the new
        # ws from the dispatch registry / flip a live agent to "disconnected".
        if _owns_agent_connection(agent_id, websocket):
            _agent_ws_connections.pop(agent_id, None)
            db = next(get_db())
            try:
                svc = BenchmarkService(db)
                svc.update_agent_status(agent_id, "disconnected")
                db.commit()
            except Exception:
                pass
            finally:
                db.close()
        logger.info("Agent %d disconnected from WebSocket", agent_id)


async def _dispatch_next_group_child(svc: "BenchmarkService", agent_id: int, group_id: int) -> None:
    """Atomically claim and dispatch the next pending child of a run-group.

    Closes the gated-dispatch double-dispatch race (H1): two near-simultaneous
    terminal WS messages can both pick the same lowest-id PENDING child. We claim
    the child with a conditional UPDATE (PENDING→RUNNING) and only dispatch if this
    handler won the claim (rowcount == 1) — so aiperf is invoked exactly once. If
    the WS send fails after a winning claim, revert the child to PENDING so a later
    terminal event (or reconnect) can re-dispatch it; otherwise it would wedge as a
    RUNNING child no agent is executing.
    """
    nxt = svc.get_next_pending_group_run(group_id)
    if not nxt:
        return
    nxt_id = nxt.id
    nxt_config = nxt.config_snapshot
    if not svc.claim_pending_run(nxt_id):
        # Lost the race — another handler already claimed and dispatched this child.
        return
    sent = await send_command_to_agent(
        agent_id, {"type": "run", "run_id": nxt_id, "config": nxt_config}
    )
    if sent:
        logger.info("Gated dispatch: claimed+sent next run #%d of group %d", nxt_id, group_id)
    else:
        # Undo the claim so the child can be re-dispatched later.
        svc.release_claimed_run(nxt_id)
        logger.warning("Gated dispatch: send failed for run #%d, reverted to pending", nxt_id)


async def send_command_to_agent(agent_id: int, command: dict) -> bool:
    """Send a command to a connected agent. Returns True if sent successfully."""
    ws = _agent_ws_connections.get(agent_id)
    if not ws:
        return False
    try:
        await ws.send_text(json.dumps(command))
        return True
    except Exception:
        return False


def dispatch_to_agent(agent_id: int, command: dict) -> bool:
    """Dispatch a command to an agent from a SYNC route handler, reliably.

    The agent's WebSocket lives in uvicorn's main event loop. Running
    ``send_command_to_agent`` in a freshly-created loop (the old approach) is
    undefined — it worked intermittently and otherwise dropped the command,
    leaving runs stuck in 'pending'. Schedule the send on the loop that owns the
    socket and wait for the result.
    """
    loop = _main_loop
    if loop is not None and loop.is_running():
        try:
            fut = asyncio.run_coroutine_threadsafe(send_command_to_agent(agent_id, command), loop)
            return bool(fut.result(timeout=15))
        except Exception:
            return False
    # No agent has connected yet (no loop captured) → nothing to send to.
    return False


# ============================================================================
# WebSocket — Dashboard run progress watchers
# ============================================================================

_run_ws_connections: dict[int, set[WebSocket]] = {}


@ws_router.websocket("/ws/benchmarks/runs/{run_id}")
async def run_progress_ws(websocket: WebSocket, run_id: int):
    """WebSocket for dashboard clients watching a benchmark run's progress."""
    await websocket.accept()
    if run_id not in _run_ws_connections:
        _run_ws_connections[run_id] = set()
    _run_ws_connections[run_id].add(websocket)

    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        pass
    finally:
        _run_ws_connections.get(run_id, set()).discard(websocket)
        if run_id in _run_ws_connections and not _run_ws_connections[run_id]:
            del _run_ws_connections[run_id]


async def broadcast_run_update(run_id: int, message: dict) -> None:
    """Broadcast an update to all dashboard clients watching a run."""
    connections = _run_ws_connections.get(run_id, set())
    if not connections:
        return
    payload = json.dumps(message)
    disconnected = set()
    for ws in connections:
        try:
            await ws.send_text(payload)
        except Exception:
            disconnected.add(ws)
    for ws in disconnected:
        connections.discard(ws)
