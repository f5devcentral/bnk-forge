"""
AI-gateway observability routes (Tier 1: request analytics).

Thin handlers over LlmObservabilityService, which proxies live LogQL to the
in-cluster ``llm-gateway`` Loki store. Every endpoint returns a degradation
envelope (``available``/``reason``/``errors``) instead of raising, so a panel
can render "unavailable" without crashing the UI.

Handlers are ``async`` and offload the blocking Loki roundtrips with
``asyncio.to_thread`` (the codebase pattern for wrapping blocking K8s calls),
so a slow cluster can't tie up an event-loop worker for the full request.

Base path: /api/k8s/clusters/{cluster_id}/llm-observability
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from core.errors import handle_route_errors
from database import get_db
from routes.auth import require_viewer
from schemas.llm_observability import (
    LlmFilterDataResponse,
    LlmHistogramResponse,
    LlmLogsResponse,
    LlmProviderUsageResponse,
    LlmRankingsResponse,
    LlmStatsResponse,
)
from services.llm_observability_service import LlmObservabilityService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["k8s-llm-observability"])

_BASE = "/k8s/clusters/{cluster_id}/llm-observability"
_FLEET_BASE = "/k8s/llm-observability"

# NOTE: the handler arg is `time_range` (avoids shadowing the `range` builtin);
# the query parameter stays `range` via Query(alias="range"), so the API/UI
# contract is unchanged.


@router.get(
    f"{_FLEET_BASE}/stats",
    response_model=LlmStatsResponse,
    dependencies=[Depends(require_viewer)],
)
@router.get(
    f"{_BASE}/stats",
    response_model=LlmStatsResponse,
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("get LLM gateway stats")
async def get_llm_stats(
    cluster_id: int | None = None,
    time_range: str = Query(default="1h", alias="range"),
    model: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    """Summary tiles: total requests, success rate, avg latency, tokens, cost, models."""
    return await asyncio.to_thread(
        LlmObservabilityService(db).stats, cluster_id, time_range, model, status
    )


@router.get(
    f"{_FLEET_BASE}/histogram",
    response_model=LlmHistogramResponse,
    dependencies=[Depends(require_viewer)],
)
@router.get(
    f"{_BASE}/histogram",
    response_model=LlmHistogramResponse,
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("get LLM gateway histogram")
async def get_llm_histogram(
    cluster_id: int | None = None,
    metric: str = Query(default="requests"),
    time_range: str = Query(default="1h", alias="range"),
    model: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    """Time-series for one metric (requests|tokens|cost|models|latency)."""
    return await asyncio.to_thread(
        LlmObservabilityService(db).histogram, cluster_id, time_range, metric, model, status
    )


@router.get(
    f"{_FLEET_BASE}/rankings",
    response_model=LlmRankingsResponse,
    dependencies=[Depends(require_viewer)],
)
@router.get(
    f"{_BASE}/rankings",
    response_model=LlmRankingsResponse,
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("get LLM gateway rankings")
async def get_llm_rankings(
    cluster_id: int | None = None,
    time_range: str = Query(default="1h", alias="range"),
    model: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    """Per-model rows with window-over-window trend deltas."""
    return await asyncio.to_thread(
        LlmObservabilityService(db).rankings, cluster_id, time_range, model, status
    )


@router.get(
    f"{_FLEET_BASE}/provider-usage",
    response_model=LlmProviderUsageResponse,
    dependencies=[Depends(require_viewer)],
)
@router.get(
    f"{_BASE}/provider-usage",
    response_model=LlmProviderUsageResponse,
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("get LLM gateway provider usage")
async def get_llm_provider_usage(
    cluster_id: int | None = None,
    metric: str = Query(default="cost"),
    time_range: str = Query(default="1h", alias="range"),
    model: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    """Time-series folded to inferred provider (cost|tokens|latency)."""
    return await asyncio.to_thread(
        LlmObservabilityService(db).provider_usage, cluster_id, time_range, metric, model, status
    )


@router.get(
    f"{_FLEET_BASE}/logs",
    response_model=LlmLogsResponse,
    dependencies=[Depends(require_viewer)],
)
@router.get(
    f"{_BASE}/logs",
    response_model=LlmLogsResponse,
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("get LLM gateway logs")
async def get_llm_logs(
    cluster_id: int | None = None,
    time_range: str = Query(default="1h", alias="range"),
    model: str | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=1000),
    content_search: str | None = None,
    end: int | None = Query(default=None, description="nanosecond cursor for load-older"),
    db: Session = Depends(get_db),
):
    """Per-request log rows, newest first; ``next_end`` is the load-older cursor."""
    return await asyncio.to_thread(
        LlmObservabilityService(db).logs,
        cluster_id,
        time_range,
        model,
        status,
        limit,
        content_search,
        end,
    )


@router.get(
    f"{_FLEET_BASE}/filterdata",
    response_model=LlmFilterDataResponse,
    dependencies=[Depends(require_viewer)],
)
@router.get(
    f"{_BASE}/filterdata",
    response_model=LlmFilterDataResponse,
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("get LLM gateway filter data")
async def get_llm_filterdata(
    cluster_id: int | None = None,
    time_range: str = Query(default="1h", alias="range"),
    db: Session = Depends(get_db),
):
    """Distinct model + status label values for filter dropdowns."""
    return await asyncio.to_thread(
        LlmObservabilityService(db).filterdata, cluster_id, time_range
    )
