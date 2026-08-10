"""ReachabilityRegistry — singleton coordinating probes, breakers, state, SSE.

Responsibilities:
- Hold one registered ``Probe`` per ``target_type``.
- Run an asyncio task per probe class that polls every ``probe_interval_seconds``
  with ±10% jitter (anti-synchronization, blackbox_exporter pattern).
- Maintain in-process breaker state per (target_type, target_id), keyed by
  the asyncio event loop currently running the API.
- Cache the latest ``ProbeResult`` per target in Redis (and locally) so
  ``GET /api/connectivity/state`` is O(1).
- Publish state-change deltas to in-process SSE subscribers via asyncio.Queue.
- Accept passive observation of real call success/failure from the
  ``with_breaker`` decorator (Envoy-style hybrid passive+active).

Storage notes
-------------
Breaker state is in-process (cheap; resets to CLOSED on restart by design —
fail-open avoids phantom outages). Redis stores only the latest probe
result per target so cross-process readers (workers, multiple SSE
subscribers across the same backend process) see a coherent snapshot.

Single-process today: forge runs one backend container in both deploy
modes. If the topology grows, a Redis pub/sub fan-out would be the
straight-line upgrade — kept minimal here per RFC scope.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from services.reachability.breaker import _Breaker
from services.reachability.probe import (
    ErrorCategory,
    Probe,
    ProbeResult,
    ReachabilityState,
)

logger = logging.getLogger(__name__)

REDIS_KEY_PREFIX = "reachability:state:"
SSE_HEARTBEAT_SECONDS = 15  # below typical 30-60s nginx idle timeout


def _state_key(target_type: str, target_id: int) -> str:
    return f"{REDIS_KEY_PREFIX}{target_type}:{target_id}"


def _serialize_result(result: ProbeResult, target_type: str, target_id: int, target_name: str | None) -> dict[str, Any]:
    return {
        "target_type": target_type,
        "target_id": target_id,
        "target_name": target_name,
        "state": result.state.value,
        "latency_ms": result.latency_ms,
        "error_category": result.error_category.value if result.error_category else None,
        "error_context": result.error_context,
        "checked_at": result.checked_at.astimezone(UTC).isoformat(),
    }


class ReachabilityRegistry:
    """Singleton — get the module-level ``registry`` instance, don't instantiate."""

    def __init__(self) -> None:
        self._probes: dict[str, Probe] = {}
        self._breakers: dict[tuple[str, int], _Breaker] = {}
        self._latest: dict[tuple[str, int], dict[str, Any]] = {}
        self._target_names: dict[tuple[str, int], str] = {}
        # last-success epoch (monotonic-ish wall time) for friendly error rendering
        self._last_success_wall: dict[tuple[str, int], datetime] = {}
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._scheduler_tasks: dict[str, asyncio.Task[None]] = {}
        self._db_session_factory: Callable[[], Session] | None = None
        self._redis: Any = None  # populated lazily, optional
        self._started = False

    # ------------------------------------------------------------------
    # Registration / lifecycle
    # ------------------------------------------------------------------

    def register(self, probe: Probe) -> None:
        if not probe.target_type:
            raise ValueError("Probe.target_type must be set")
        self._probes[probe.target_type] = probe
        logger.info("Registered probe target_type=%s interval=%ds", probe.target_type, probe.probe_interval_seconds)

    def configure(self, *, db_session_factory: Callable[[], Session]) -> None:
        """Wire up a SQLAlchemy session factory for probe scheduling.

        Optional Redis connection is picked up lazily on first use.
        """
        self._db_session_factory = db_session_factory

    async def start(self) -> None:
        """Spin up one asyncio task per registered probe class."""
        if self._started:
            return
        self._started = True
        for target_type, probe in self._probes.items():
            self._scheduler_tasks[target_type] = asyncio.create_task(
                self._scheduler_loop(probe), name=f"reachability-{target_type}"
            )
        logger.info("Reachability scheduler started for: %s", list(self._scheduler_tasks))

    async def stop(self) -> None:
        for task in self._scheduler_tasks.values():
            task.cancel()
        for task in self._scheduler_tasks.values():
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._scheduler_tasks.clear()
        self._started = False

    # ------------------------------------------------------------------
    # Read API (used by routes/connectivity.py)
    # ------------------------------------------------------------------

    def all_states(self) -> list[dict[str, Any]]:
        """Return latest known state for every target the registry has seen.

        Backed by in-process cache; Redis is best-effort and only used as a
        warm-cache for fresh subscribers.
        """
        return list(self._latest.values())

    def get_state(self, target_type: str, target_id: int) -> dict[str, Any] | None:
        return self._latest.get((target_type, target_id))

    def get_target_name(self, target_type: str, target_id: int) -> str | None:
        return self._target_names.get((target_type, target_id))

    def get_last_success_iso(self, target_type: str, target_id: int) -> str | None:
        ts = self._last_success_wall.get((target_type, target_id))
        return ts.astimezone(UTC).isoformat() if ts else None

    # ------------------------------------------------------------------
    # Breaker integration (called by with_breaker decorator)
    # ------------------------------------------------------------------

    def _breaker_for(self, target_type: str, target_id: int) -> _Breaker:
        key = (target_type, target_id)
        b = self._breakers.get(key)
        if b is None:
            probe = self._probes.get(target_type)
            b = _Breaker(
                failure_threshold=probe.breaker_failure_threshold if probe else 5,
                sleep_window_seconds=probe.breaker_sleep_window_seconds if probe else 30,
                target_name=self._target_names.get(key),
            )
            self._breakers[key] = b
        return b

    def allow_call(self, target_type: str, target_id: int) -> bool:
        return self._breaker_for(target_type, target_id).can_attempt()

    def record_real_call(
        self,
        target_type: str,
        target_id: int,
        *,
        success: bool,
        error_category: ErrorCategory | None = None,
        latency_ms: float = 0.0,
    ) -> None:
        """Passive observation hook — feed real-traffic outcome into the breaker.

        Slow-call detection: a "successful" response above slow_threshold_ms is
        treated as a TARGET failure (resilience4j slowCallDurationThreshold).
        """
        b = self._breaker_for(target_type, target_id)
        probe = self._probes.get(target_type)
        slow_threshold = probe.slow_threshold_ms if probe else 5000
        if success and latency_ms >= slow_threshold:
            b.record_failure(ErrorCategory.SLOW)
            return
        if success:
            b.record_success()
            self._last_success_wall[(target_type, target_id)] = datetime.now(UTC)
        else:
            b.record_failure(error_category)

    # ------------------------------------------------------------------
    # SSE subscription
    # ------------------------------------------------------------------

    async def subscribe(self) -> AsyncIterator[dict[str, Any]]:
        """Yield state-change events. Caller iterates with ``async for``.

        On subscription the registry first replays current state so a fresh
        subscriber doesn't have to wait for the next probe to populate the UI.
        Subsequent yields are deltas published by ``_publish``.
        """
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        # Replay current snapshot first
        for snapshot in self.all_states():
            await queue.put(snapshot)
        self._subscribers.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)

    def _publish(self, snapshot: dict[str, Any]) -> None:
        for q in list(self._subscribers):
            # Best-effort fan-out; if a subscriber is gone, queue.put_nowait still works
            try:
                q.put_nowait(snapshot)
            except asyncio.QueueFull:
                logger.warning("SSE subscriber queue full, dropping event")

    # ------------------------------------------------------------------
    # Force-probe (retry button)
    # ------------------------------------------------------------------

    async def force_probe(self, target_type: str, target_id: int) -> dict[str, Any]:
        probe = self._probes.get(target_type)
        if probe is None:
            raise ValueError(f"Unknown target_type: {target_type}")
        # Force-probe overrides breaker — user explicitly asked for this.
        result = await probe.probe(target_id)
        await self._record_probe_result(probe, target_id, result)
        return self.get_state(target_type, target_id) or {}

    # ------------------------------------------------------------------
    # Internal scheduling
    # ------------------------------------------------------------------

    async def _scheduler_loop(self, probe: Probe) -> None:
        """One task per probe class — polls list_targets() each cycle."""
        logger.info("Probe scheduler tick start: %s", probe.target_type)
        while True:
            try:
                target_ids = self._list_targets_safe(probe)
                # Run probes for all targets concurrently per cycle.
                if target_ids:
                    await asyncio.gather(
                        *(self._probe_one(probe, tid) for tid in target_ids),
                        return_exceptions=True,
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Scheduler loop error for %s", probe.target_type)
            # Jittered sleep ±10% (Envoy / blackbox_exporter convention)
            base = probe.probe_interval_seconds
            sleep_for = base * (1.0 + random.uniform(-0.1, 0.1))
            await asyncio.sleep(max(1.0, sleep_for))

    def _list_targets_safe(self, probe: Probe) -> list[int]:
        if not self._db_session_factory:
            return []
        db = self._db_session_factory()
        try:
            return probe.list_targets(db)
        except Exception:
            logger.exception("list_targets failed for %s", probe.target_type)
            return []
        finally:
            try:
                db.close()
            except Exception:
                pass

    async def _probe_one(self, probe: Probe, target_id: int) -> None:
        try:
            result = await probe.probe(target_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Probe raised unexpectedly for %s:%d", probe.target_type, target_id)
            result = ProbeResult(
                state=ReachabilityState.UNREACHABLE,
                checked_at=datetime.now(UTC),
                error_category=ErrorCategory.TARGET,
                error_context={"suggested_action": f"Probe failed unexpectedly: {exc}"},
            )
        await self._record_probe_result(probe, target_id, result)

    async def _record_probe_result(self, probe: Probe, target_id: int, result: ProbeResult) -> None:
        target_name = result.error_context.get("target_name") or self._target_names.get(
            (probe.target_type, target_id)
        )
        if target_name:
            self._target_names[(probe.target_type, target_id)] = target_name
            b = self._breakers.get((probe.target_type, target_id))
            if b is not None and not b.target_name:
                b.target_name = target_name
        snapshot = _serialize_result(result, probe.target_type, target_id, target_name)
        key = (probe.target_type, target_id)
        prev = self._latest.get(key)
        self._latest[key] = snapshot

        # Feed probe result into the breaker (active observation).
        if result.state is ReachabilityState.REACHABLE:
            self.record_real_call(
                probe.target_type, target_id,
                success=True,
                latency_ms=result.latency_ms or 0.0,
            )
        elif result.state is ReachabilityState.UNREACHABLE:
            self.record_real_call(
                probe.target_type, target_id,
                success=False,
                error_category=result.error_category,
                latency_ms=result.latency_ms or 0.0,
            )

        # Persist to Redis (best-effort).
        await self._persist_redis(snapshot)

        # Publish only on state transition or first observation; otherwise the
        # SSE stream becomes a polling stream.
        if not prev or prev.get("state") != snapshot["state"] or prev.get("error_context") != snapshot["error_context"]:
            self._publish(snapshot)
            logger.info(
                "Reachability transition: %s:%d %s -> %s",
                probe.target_type, target_id,
                (prev or {}).get("state", "<new>"),
                snapshot["state"],
            )

    # ------------------------------------------------------------------
    # Redis persistence (best-effort, optional)
    # ------------------------------------------------------------------

    def _get_redis(self) -> Any:
        if self._redis is not None:
            return self._redis
        try:
            import redis as _redis

            from core.cache import DEFAULT_REDIS_URL
            self._redis = _redis.from_url(DEFAULT_REDIS_URL, decode_responses=True)
            self._redis.ping()
            return self._redis
        except Exception as exc:
            logger.warning("Reachability Redis state cache unavailable: %s", exc)
            self._redis = False  # sentinel: don't keep retrying every probe
            return None

    async def _persist_redis(self, snapshot: dict[str, Any]) -> None:
        r = self._get_redis()
        if not r:
            return
        try:
            # Run the (sync) redis SET off the event loop so we don't block.
            await asyncio.to_thread(
                r.setex,
                _state_key(snapshot["target_type"], snapshot["target_id"]),
                # 5 minute TTL — every cluster probes well within that
                300,
                json.dumps(snapshot),
            )
        except Exception as exc:
            logger.debug("Redis persist failed (continuing): %s", exc)


# Module-level singleton. Imported widely.
registry = ReachabilityRegistry()
