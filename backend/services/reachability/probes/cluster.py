"""ClusterProbe — mode-dependent reachability probe for Kubernetes clusters.

Mode selection is data-plane-aligned (Argo CD / Rancher / SRE Book Ch.6:
"probe the path you serve traffic on"):

- **KubernetesEngine** (no connected operator) — direct ``GET /version``
  against the API server. Wraps the existing ``connectivity_probe_service``
  rather than reimplementing the socket dance. Fast, doesn't need full
  kubeconfig load.

- **OperatorEngine** (cluster has a connected operator record) — the
  operator's heartbeat *plus* the operator-reported "last successful
  in-cluster API call" timestamp. Operator-alive ≠ K8s-API-reachable-from-
  operator: the operator can be heartbeating happily while its in-cluster
  API client is failing. Both signals must look healthy to mark REACHABLE.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from models import ConnectedOperator, KubernetesCluster

logger = logging.getLogger(__name__)
from services.reachability.probe import (
    ErrorCategory,
    Probe,
    ProbeResult,
    ReachabilityState,
)

# Operator engine: operator considered alive only if heartbeat is fresher than this.
OPERATOR_HEARTBEAT_FRESH_SECONDS = 90
# Operator engine: operator's reported in-cluster API success must be this fresh
# (operator self-reports in its health payload; we read last_health_at as proxy).
OPERATOR_HEALTH_FRESH_SECONDS = 180


class ClusterProbe(Probe):
    target_type = "cluster"
    probe_interval_seconds = 30
    slow_threshold_ms = 5000
    breaker_failure_threshold = 5
    breaker_sleep_window_seconds = 30

    def __init__(self, db_session_factory: Any) -> None:
        # Stored as a callable so we can open a fresh short-lived session per probe.
        self._session_factory = db_session_factory

    # ------------------------------------------------------------------
    # Probe ABC
    # ------------------------------------------------------------------

    def list_targets(self, db: Session) -> list[int]:
        rows = db.query(KubernetesCluster.id).all()
        return [r[0] for r in rows]

    async def probe(self, target_id: int) -> ProbeResult:
        # Use a fresh session for each probe — long-running sessions across
        # 30s ticks will rot and lose connection on idle DBs.
        db = self._session_factory()
        try:
            cluster = db.query(KubernetesCluster).filter(KubernetesCluster.id == target_id).first()
            if cluster is None:
                return ProbeResult(
                    state=ReachabilityState.UNKNOWN,
                    checked_at=datetime.now(UTC),
                    error_context={
                        "target_name": f"cluster:{target_id}",
                        "suggested_action": "Cluster row was deleted; will stop probing on next cycle.",
                    },
                )
            if self._is_operator_engine(db, cluster):
                return await self._probe_via_operator(db, cluster)
            return await self._probe_direct(cluster)
        finally:
            try:
                db.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Mode detection
    # ------------------------------------------------------------------

    def _is_operator_engine(self, db: Session, cluster: KubernetesCluster) -> bool:
        """A cluster is in OperatorEngine mode if any ConnectedOperator row
        is bound to it (alive or not — the probe still needs to attribute
        unreachable to the right cause)."""
        return (
            db.query(ConnectedOperator.id)
            .filter(ConnectedOperator.cluster_id == cluster.id)
            .first()
            is not None
        )

    # ------------------------------------------------------------------
    # Direct probe (KubernetesEngine)
    # ------------------------------------------------------------------

    async def _probe_direct(self, cluster: KubernetesCluster) -> ProbeResult:
        """Wrap connectivity_probe_service for the synchronous path.

        That service already does ICMP/TCP/GET-/version and produces a rich
        diagnostic. We map its status into our 3-state model and re-shape
        the message into ``error_context``.

        Tighter timeouts than the underlying service's defaults (3s TCP,
        5s K8s read) — RFC mandates fast probes; the broader 5s/8s is
        only used at higher levels.
        """
        from services.connectivity_probe_service import _parse_api_server, _probe_k8s_api, _probe_tcp

        host, port = _parse_api_server(cluster.api_server)
        if not host:
            return ProbeResult(
                state=ReachabilityState.UNKNOWN,
                checked_at=datetime.now(UTC),
                error_context={
                    "target_name": cluster.name,
                    "suggested_action": "No API server URL configured for this cluster.",
                },
            )

        # For SSH-tunneled clusters (typical bare-metal / dual_dpu_obmc),
        # the cluster.api_server IP is intentionally NOT routable from the
        # backend container — that's why we have a tunnel. Probing it
        # directly would always say "unreachable". Instead, open / reuse
        # the tunnel and probe 127.0.0.1:<tunnel_port>, which is the
        # actual path every K8s call in the system uses.
        if cluster.ssh_tunnel_enabled:
            try:
                from services.cluster_utils import _maybe_open_ssh_tunnel

                tunnel_port = _maybe_open_ssh_tunnel(cluster)
                if tunnel_port:
                    host = "127.0.0.1"
                    port = tunnel_port
            except Exception as exc:
                # Tunnel failed to open — fall through to direct probe (it
                # will fail and surface a clear "cannot reach" message);
                # we record the tunnel failure as context.
                logger.debug(
                    "Reachability probe: SSH tunnel for cluster %s failed: %s",
                    cluster.name, exc,
                )

        # Run the (sync) probes off the event loop so we don't block other probes.
        def _do_probe() -> tuple[dict[str, Any], dict[str, Any], float]:
            import time as _t
            start = _t.monotonic()
            tcp = _probe_tcp(host, port, timeout=3)
            k8s_api: dict[str, Any] = {"accessible": False, "version": None, "status_code": None}
            if tcp.get("open"):
                k8s_api = _probe_k8s_api(host, port, timeout=5)
            latency_ms = (_t.monotonic() - start) * 1000
            return tcp, k8s_api, latency_ms

        tcp, k8s_api, latency_ms = await asyncio.to_thread(_do_probe)
        now = datetime.now(UTC)

        if k8s_api.get("accessible"):
            return ProbeResult(
                state=ReachabilityState.REACHABLE,
                checked_at=now,
                latency_ms=latency_ms,
                error_context={"target_name": cluster.name},
            )
        if tcp.get("open"):
            # Port open but K8s API not responding — likely auth/wrong port,
            # not a network problem. Don't trip the breaker hard.
            return ProbeResult(
                state=ReachabilityState.UNREACHABLE,
                checked_at=now,
                latency_ms=latency_ms,
                error_category=ErrorCategory.TARGET,
                error_context={
                    "target_name": cluster.name,
                    "suggested_action": (
                        f"Port {port} on {host} accepts connections but the Kubernetes "
                        f"API did not respond. Verify the API server URL and port."
                    ),
                },
            )
        return ProbeResult(
            state=ReachabilityState.UNREACHABLE,
            checked_at=now,
            latency_ms=latency_ms,
            error_category=ErrorCategory.NETWORK,
            error_context={
                "target_name": cluster.name,
                "suggested_action": (
                    f"Cannot reach {host}:{port}. "
                    f"Check VPN connection or whether the API server is online."
                ),
            },
        )

    # ------------------------------------------------------------------
    # Operator probe (OperatorEngine)
    # ------------------------------------------------------------------

    async def _probe_via_operator(self, db: Session, cluster: KubernetesCluster) -> ProbeResult:
        op = (
            db.query(ConnectedOperator)
            .filter(ConnectedOperator.cluster_id == cluster.id)
            .order_by(ConnectedOperator.last_heartbeat_at.desc().nullslast())
            .first()
        )
        now = datetime.now(UTC)
        if op is None:
            return ProbeResult(
                state=ReachabilityState.UNREACHABLE,
                checked_at=now,
                error_category=ErrorCategory.NETWORK,
                error_context={
                    "target_name": cluster.name,
                    "suggested_action": (
                        f"No operator is registered for cluster '{cluster.name}'. "
                        f"Install the bnk-operator into the cluster, or switch the "
                        f"cluster to direct kubeconfig mode."
                    ),
                },
            )

        heartbeat_age = self._age_seconds(op.last_heartbeat_at, now)
        health_age = self._age_seconds(op.last_health_at, now)

        if heartbeat_age is None or heartbeat_age > OPERATOR_HEARTBEAT_FRESH_SECONDS:
            return ProbeResult(
                state=ReachabilityState.UNREACHABLE,
                checked_at=now,
                error_category=ErrorCategory.NETWORK,
                error_context={
                    "target_name": cluster.name,
                    "suggested_action": (
                        f"Operator for '{cluster.name}' last heartbeat "
                        f"{int(heartbeat_age)}s ago — operator process or its WebSocket "
                        f"connection back to forge is down."
                        if heartbeat_age is not None
                        else f"Operator for '{cluster.name}' has never sent a heartbeat."
                    ),
                },
            )
        # Operator-alive ≠ K8s-API-reachable-from-operator. If the operator's
        # most recent health report is stale, the operator is up but its
        # in-cluster API client is silently failing — UI should still surface
        # this as unreachable so users don't infinite-spinner.
        if health_age is None or health_age > OPERATOR_HEALTH_FRESH_SECONDS:
            return ProbeResult(
                state=ReachabilityState.UNREACHABLE,
                checked_at=now,
                error_category=ErrorCategory.TARGET,
                error_context={
                    "target_name": cluster.name,
                    "suggested_action": (
                        f"Operator for '{cluster.name}' is heartbeating but has not "
                        f"reported a successful in-cluster API call recently — its "
                        f"K8s client may be unable to reach the API server. Check "
                        f"operator logs in the cluster."
                    ),
                },
            )
        return ProbeResult(
            state=ReachabilityState.REACHABLE,
            checked_at=now,
            error_context={"target_name": cluster.name},
        )

    @staticmethod
    def _age_seconds(ts: datetime | None, now: datetime) -> float | None:
        if ts is None:
            return None
        # SQLite strips tzinfo; treat naive timestamps as UTC.
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        delta: timedelta = now - ts
        return max(0.0, delta.total_seconds())
