"""
Runbook Automation routes.

UX-010: Pre-built diagnostic sequences for common BNK problems.
Provides endpoints to list available runbooks and execute them
against a connected K8s cluster.
"""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.errors import NotFoundError, handle_route_errors
from database import get_db
from routes.auth import require_viewer
from services.kubernetes_service import KubernetesService
from services.runbook_service import execute_runbook, list_runbooks

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["runbooks"])


class RunbookExecuteRequest(BaseModel):
    cluster_id: int


@router.get("/runbooks", dependencies=[Depends(require_viewer)])
def get_runbooks():
    """
    List all available runbooks with their step definitions.

    Returns a list of runbook objects, each with:
      - id, name, description, category
      - step_count
      - steps[] (name, description per step)
    """
    return {"runbooks": list_runbooks()}


@router.post("/runbooks/{runbook_id}/run", dependencies=[Depends(require_viewer)])
@handle_route_errors("execute runbook")
def run_runbook(
    runbook_id: str,
    request: RunbookExecuteRequest,
    db: Session = Depends(get_db),
):
    """
    Execute a runbook against a cluster.

    Runs all steps sequentially and returns the full result.
    Each step includes: name, status (pass/fail/skip), message,
    optional details, and optional resource_link for navigation.
    """
    try:
        k8s_service = KubernetesService(db)
        result = execute_runbook(k8s_service, request.cluster_id, runbook_id)
        return result

    except ValueError:
        raise NotFoundError("runbook", runbook_id)
