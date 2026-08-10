"""Connectivity / reachability routes.

Three endpoints:
- ``GET  /api/connectivity/state``           — current snapshot of every target
- ``GET  /api/connectivity/watch``           — SSE stream of state-change events
- ``POST /api/connectivity/probe/{type}/{id}`` — force-immediate probe (retry button)

Auth: requires an authenticated user (any role). State data isn't sensitive
on its own, but it does enumerate a tenant's targets so we keep it gated.

SSE notes:
- Backend emits ``X-Accel-Buffering: no`` so nginx + ingress controllers don't
  buffer (otherwise the browser sees nothing until 8KB or the response ends).
- Heartbeat comments (``: keepalive\\n\\n``) every ``SSE_HEARTBEAT_SECONDS`` keep
  the connection alive across nginx idle timeouts.
- Disconnect detection via ``request.is_disconnected()`` keeps the iteration
  from leaking when the client navigates away.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core.errors import BadRequestError, handle_route_errors
from routes.auth import require_viewer
from services.reachability import registry
from services.reachability.registry import SSE_HEARTBEAT_SECONDS

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/connectivity", tags=["connectivity"], dependencies=[Depends(require_viewer)])


# ============================================================
# Response models
# ============================================================

class ReachabilityStateResponse(BaseModel):
    target_type: str
    target_id: int
    target_name: str | None = None
    state: str
    latency_ms: float | None = None
    error_category: str | None = None
    error_context: dict[str, Any] = {}
    checked_at: str | None = None


class StateListResponse(BaseModel):
    states: list[ReachabilityStateResponse]


# ============================================================
# Routes
# ============================================================

@router.get("/state", response_model=StateListResponse)
@handle_route_errors("get connectivity state")
def get_state() -> StateListResponse:
    """Return the latest known state for every target the registry has seen."""
    return StateListResponse(
        states=[ReachabilityStateResponse(**s) for s in registry.all_states()]
    )


@router.post(
    "/probe/{target_type}/{target_id}",
    response_model=ReachabilityStateResponse,
)
@handle_route_errors("force connectivity probe")
async def force_probe(target_type: str, target_id: int) -> ReachabilityStateResponse:
    """Run an immediate probe (bypassing the breaker) and return the new state.

    Used by the frontend "retry" button so the user gets fast feedback rather
    than waiting up to ``probe_interval_seconds`` for the next scheduled tick.
    """
    try:
        snapshot = await registry.force_probe(target_type, target_id)
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc
    return ReachabilityStateResponse(**snapshot)


@router.get("/watch")
async def watch(request: Request) -> StreamingResponse:
    """SSE stream of state-change events.

    Replays current snapshot on connect, then yields one ``data: <json>`` event
    per state transition. Heartbeats keep the connection open through proxies.
    """

    async def _event_source():
        # SSE event format: ``data: <line>\n\n`` (one JSON object per event).
        # Comments (``: ...\n\n``) are sent as keepalives — clients ignore them.
        try:
            agen = registry.subscribe()
            iterator = agen.__aiter__()
            while True:
                if await request.is_disconnected():
                    break
                try:
                    snapshot = await asyncio.wait_for(
                        iterator.__anext__(),
                        timeout=SSE_HEARTBEAT_SECONDS,
                    )
                    yield f"data: {json.dumps(snapshot)}\n\n"
                except TimeoutError:
                    # Send keepalive comment, loop back to check disconnect.
                    yield ": keepalive\n\n"
                except StopAsyncIteration:
                    break
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("SSE stream error")
            return

    headers = {
        "Cache-Control": "no-cache, no-transform",
        # Disable nginx response buffering for THIS endpoint only — without it,
        # SSE chunks get held in the proxy until 8KB / 60s, defeating the stream.
        "X-Accel-Buffering": "no",
        "Content-Type": "text/event-stream",
    }
    return StreamingResponse(_event_source(), headers=headers, media_type="text/event-stream")
