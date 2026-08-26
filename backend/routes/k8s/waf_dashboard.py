"""
WAF Dashboard analytics routes.

Queries ClickHouse for aggregated WAF event data and returns it in
recharts-friendly shapes. All queries are scoped to cluster_id and
an optional time range.

Endpoints:
  GET /api/k8s/clusters/{id}/waf/dashboard/status   — availability check
  GET /api/k8s/clusters/{id}/waf/dashboard/summary  — KPI numbers
  GET /api/k8s/clusters/{id}/waf/dashboard/trend     — time-bucketed event counts
  GET /api/k8s/clusters/{id}/waf/dashboard/top-attacks
  GET /api/k8s/clusters/{id}/waf/dashboard/top-ips
  GET /api/k8s/clusters/{id}/waf/dashboard/top-uris
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from core.errors import handle_route_errors
from routes.auth import require_viewer
from services.clickhouse import get_clickhouse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

_DB = "bnkforge"

# Supported time ranges → hours
_RANGE_HOURS: dict[str, int] = {
    "1h": 1,
    "24h": 24,
    "7d": 7 * 24,
    "30d": 30 * 24,
}


def _hours(time_range: str) -> int:
    return _RANGE_HOURS.get(time_range, 24)


def _unavailable() -> dict:
    return {
        "available": False,
        "reason": (
            "ClickHouse is not configured. Set CLICKHOUSE_URL in the environment "
            "and ensure ClickHouse is running."
        ),
    }


@router.get(
    "/k8s/clusters/{cluster_id}/waf/dashboard/status",
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("check WAF dashboard status")
def dashboard_status(cluster_id: int):
    """Return whether ClickHouse is reachable and has data for this cluster."""
    ch = get_clickhouse()
    if not ch.available:
        return _unavailable()

    count = ch.count_events(cluster_id, hours=30 * 24)
    return {"available": True, "total_events_30d": count}


@router.get(
    "/k8s/clusters/{cluster_id}/waf/dashboard/summary",
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("fetch WAF dashboard summary")
def dashboard_summary(
    cluster_id: int,
    time_range: Annotated[str, Query()] = "24h",
):
    """Return KPI numbers: total, rejected_pct, top_attack_type, unique_ips."""
    ch = get_clickhouse()
    if not ch.available:
        return _unavailable()

    h = _hours(time_range)

    rows = ch.query(
        f"""
        SELECT
            count()                                          AS total,
            countIf(outcome = 'REJECTED')                   AS rejected,
            countIf(outcome = 'ALERTED')                    AS alerted,
            countIf(outcome = 'PASSED')                     AS passed,
            uniqExact(ip_client)                            AS unique_ips,
            topK(1)(attack_type)[1]                         AS top_attack_type
        FROM {_DB}.waf_events
        WHERE cluster_id = {{cid:UInt32}}
          AND ts >= now() - INTERVAL {{h:UInt32}} HOUR
          AND attack_type != 'N/A'
          OR (cluster_id = {{cid:UInt32}} AND ts >= now() - INTERVAL {{h:UInt32}} HOUR AND outcome != 'PASSED')
        """,
        {"cid": cluster_id, "h": h},
    )

    # Simpler, correct query
    rows = ch.query(
        f"""
        SELECT
            count()                        AS total,
            countIf(outcome = 'REJECTED')  AS rejected,
            countIf(outcome = 'ALERTED')   AS alerted,
            countIf(outcome = 'PASSED')    AS passed,
            uniqExact(ip_client)           AS unique_ips
        FROM {_DB}.waf_events
        WHERE cluster_id = {{cid:UInt32}}
          AND ts >= now() - INTERVAL {{h:UInt32}} HOUR
        """,
        {"cid": cluster_id, "h": h},
    )

    top_attack_rows = ch.query(
        f"""
        SELECT attack_type, count() AS cnt
        FROM {_DB}.waf_events
        WHERE cluster_id = {{cid:UInt32}}
          AND ts >= now() - INTERVAL {{h:UInt32}} HOUR
          AND attack_type != 'N/A'
          AND outcome = 'REJECTED'
        GROUP BY attack_type
        ORDER BY cnt DESC
        LIMIT 1
        """,
        {"cid": cluster_id, "h": h},
    )

    row = rows[0] if rows else {}
    total = int(row.get("total", 0))
    rejected = int(row.get("rejected", 0))
    return {
        "available": True,
        "time_range": time_range,
        "total": total,
        "rejected": rejected,
        "alerted": int(row.get("alerted", 0)),
        "passed": int(row.get("passed", 0)),
        "rejected_pct": round(rejected / total * 100, 1) if total else 0.0,
        "unique_ips": int(row.get("unique_ips", 0)),
        "top_attack_type": top_attack_rows[0]["attack_type"] if top_attack_rows else "—",
        "top_attack_count": int(top_attack_rows[0]["cnt"]) if top_attack_rows else 0,
    }


@router.get(
    "/k8s/clusters/{cluster_id}/waf/dashboard/trend",
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("fetch WAF dashboard trend")
def dashboard_trend(
    cluster_id: int,
    time_range: Annotated[str, Query()] = "24h",
):
    """
    Return time-bucketed event counts split by outcome.
    Bucket size: 1h for ≤7d ranges, 6h for 30d.
    Shape: [{ ts, REJECTED, PASSED, ALERTED }, ...]
    """
    ch = get_clickhouse()
    if not ch.available:
        return _unavailable()

    h = _hours(time_range)
    # Use 6-hour buckets for 30-day view to keep the series manageable
    bucket_hours = 6 if h > 7 * 24 else 1

    rows = ch.query(
        f"""
        SELECT
            toStartOfInterval(ts, INTERVAL {{bucket:UInt32}} HOUR) AS bucket,
            countIf(outcome = 'REJECTED') AS REJECTED,
            countIf(outcome = 'PASSED')   AS PASSED,
            countIf(outcome = 'ALERTED')  AS ALERTED
        FROM {_DB}.waf_events
        WHERE cluster_id = {{cid:UInt32}}
          AND ts >= now() - INTERVAL {{h:UInt32}} HOUR
        GROUP BY bucket
        ORDER BY bucket
        """,
        {"cid": cluster_id, "h": h, "bucket": bucket_hours},
    )

    return {
        "available": True,
        "time_range": time_range,
        "bucket_hours": bucket_hours,
        "series": [
            {
                # ISO 8601 format so JS new Date() parses it correctly
                "ts": str(r["bucket"]).replace(" ", "T"),
                "REJECTED": int(r.get("REJECTED", 0)),
                "PASSED": int(r.get("PASSED", 0)),
                "ALERTED": int(r.get("ALERTED", 0)),
            }
            for r in rows
        ],
    }


@router.get(
    "/k8s/clusters/{cluster_id}/waf/dashboard/top-attacks",
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("fetch WAF top attack types")
def dashboard_top_attacks(
    cluster_id: int,
    time_range: Annotated[str, Query()] = "24h",
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
):
    """Top attack types by event count."""
    ch = get_clickhouse()
    if not ch.available:
        return _unavailable()

    h = _hours(time_range)
    rows = ch.query(
        f"""
        SELECT
            attack_type,
            count() AS cnt,
            countIf(outcome = 'REJECTED') AS rejected
        FROM {_DB}.waf_events
        WHERE cluster_id = {{cid:UInt32}}
          AND ts >= now() - INTERVAL {{h:UInt32}} HOUR
          AND attack_type != 'N/A'
        GROUP BY attack_type
        ORDER BY cnt DESC
        LIMIT {{lim:UInt32}}
        """,
        {"cid": cluster_id, "h": h, "lim": limit},
    )

    total = sum(int(r["cnt"]) for r in rows)
    return {
        "available": True,
        "time_range": time_range,
        "items": [
            {
                "attack_type": r["attack_type"],
                "count": int(r["cnt"]),
                "rejected": int(r.get("rejected", 0)),
                "pct": round(int(r["cnt"]) / total * 100, 1) if total else 0.0,
            }
            for r in rows
        ],
    }


@router.get(
    "/k8s/clusters/{cluster_id}/waf/dashboard/top-ips",
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("fetch WAF top source IPs")
def dashboard_top_ips(
    cluster_id: int,
    time_range: Annotated[str, Query()] = "24h",
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
):
    """Top source IPs by blocked event count."""
    ch = get_clickhouse()
    if not ch.available:
        return _unavailable()

    h = _hours(time_range)
    rows = ch.query(
        f"""
        SELECT
            ip_client,
            count() AS total_hits,
            countIf(outcome = 'REJECTED') AS blocked_hits,
            max(ts) AS last_seen
        FROM {_DB}.waf_events
        WHERE cluster_id = {{cid:UInt32}}
          AND ts >= now() - INTERVAL {{h:UInt32}} HOUR
          AND ip_client != ''
        GROUP BY ip_client
        ORDER BY blocked_hits DESC, total_hits DESC
        LIMIT {{lim:UInt32}}
        """,
        {"cid": cluster_id, "h": h, "lim": limit},
    )

    return {
        "available": True,
        "time_range": time_range,
        "items": [
            {
                "ip": r["ip_client"],
                "total_hits": int(r["total_hits"]),
                "blocked_hits": int(r.get("blocked_hits", 0)),
                "last_seen": str(r.get("last_seen", "")).replace(" ", "T"),
            }
            for r in rows
        ],
    }


@router.get(
    "/k8s/clusters/{cluster_id}/waf/dashboard/top-uris",
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("fetch WAF top URIs")
def dashboard_top_uris(
    cluster_id: int,
    time_range: Annotated[str, Query()] = "24h",
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
):
    """Top attacked URIs."""
    ch = get_clickhouse()
    if not ch.available:
        return _unavailable()

    h = _hours(time_range)
    rows = ch.query(
        f"""
        SELECT
            uri,
            count() AS cnt,
            countIf(outcome = 'REJECTED') AS rejected,
            groupUniqArray(3)(attack_type) AS attack_types,
            max(ts) AS last_seen
        FROM {_DB}.waf_events
        WHERE cluster_id = {{cid:UInt32}}
          AND ts >= now() - INTERVAL {{h:UInt32}} HOUR
          AND uri != ''
          AND outcome = 'REJECTED'
        GROUP BY uri
        ORDER BY cnt DESC
        LIMIT {{lim:UInt32}}
        """,
        {"cid": cluster_id, "h": h, "lim": limit},
    )

    return {
        "available": True,
        "time_range": time_range,
        "items": [
            {
                "uri": r["uri"],
                "count": int(r["cnt"]),
                "rejected": int(r.get("rejected", 0)),
                "attack_types": list(r.get("attack_types", [])),
                "last_seen": str(r.get("last_seen", "")).replace(" ", "T"),
            }
            for r in rows
        ],
    }
