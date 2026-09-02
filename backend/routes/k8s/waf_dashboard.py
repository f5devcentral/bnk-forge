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
from services.clickhouse import get_clickhouse, CLICKHOUSE_DB as _DB
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


# Supported time ranges → hours
_RANGE_HOURS: dict[str, int] = {
    "1h": 1,
    "24h": 24,
    "7d": 7 * 24,
    "30d": 30 * 24,
}


def _hours(time_range: str) -> int:
    return _RANGE_HOURS.get(time_range, 24)


def _build_filter_conditions(
    params: dict,
    outcome: str | None = None,
    policy_name: str | None = None,
    vs_name: str | None = None,
    ip_client: str | None = None,
    attack_type: str | None = None,
    method: str | None = None,
) -> list[str]:
    """Return extra WHERE clauses for the global dashboard filter and populate params."""
    conditions: list[str] = []
    if outcome:
        conditions.append("outcome = {g_outcome:String}")
        params["g_outcome"] = outcome.upper()
    if policy_name:
        conditions.append("policy_name = {g_policy:String}")
        params["g_policy"] = policy_name
    if vs_name:
        conditions.append("vs_name = {g_vs:String}")
        params["g_vs"] = vs_name
    if ip_client:
        conditions.append("ip_client = {g_ip:String}")
        params["g_ip"] = ip_client
    if attack_type:
        conditions.append("positionCaseInsensitive(attack_type, {g_atk:String}) > 0")
        params["g_atk"] = attack_type
    if method:
        conditions.append("upper(method) = {g_method:String}")
        params["g_method"] = method.upper()
    return conditions


# Shared Query annotation for the six global dashboard filter params (no default in Query — use = None at call site)
_G_OUTCOME     = Query(description="Filter by WAF outcome (REJECTED|ALERTED|PASSED)")
_G_POLICY      = Query(description="Filter by policy name (exact)")
_G_VS          = Query(description="Filter by virtual server / instance name (exact)")
_G_IP          = Query(description="Filter by client IP address (exact)")
_G_ATTACK_TYPE = Query(description="Filter by attack type (substring)")
_G_METHOD      = Query(description="Filter by HTTP method (GET|POST|etc., case-insensitive)")


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
    outcome:     Annotated[str | None, _G_OUTCOME]     = None,
    policy_name: Annotated[str | None, _G_POLICY]      = None,
    vs_name:     Annotated[str | None, _G_VS]          = None,
    ip_client:   Annotated[str | None, _G_IP]          = None,
    attack_type: Annotated[str | None, _G_ATTACK_TYPE] = None,
    method:      Annotated[str | None, _G_METHOD]      = None,
):
    """Return KPI numbers: total, rejected_pct, top_attack_type, unique_ips."""
    ch = get_clickhouse()
    if not ch.available:
        return _unavailable()

    h = _hours(time_range)
    gf_params: dict = {"cid": cluster_id, "h": h}
    gf_conds = _build_filter_conditions(
        gf_params, outcome, policy_name, vs_name, ip_client, attack_type, method
    )
    gf_extra = (" AND " + " AND ".join(gf_conds)) if gf_conds else ""

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
          AND ts >= now() - INTERVAL {{h:UInt32}} HOUR{gf_extra}
          AND attack_type != 'N/A'
          OR (cluster_id = {{cid:UInt32}} AND ts >= now() - INTERVAL {{h:UInt32}} HOUR AND outcome != 'PASSED')
        """,
        gf_params,
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
          AND ts >= now() - INTERVAL {{h:UInt32}} HOUR{gf_extra}
        """,
        gf_params,
    )

    top_attack_rows = ch.query(
        f"""
        SELECT attack_type, count() AS cnt
        FROM {_DB}.waf_events
        WHERE cluster_id = {{cid:UInt32}}
          AND ts >= now() - INTERVAL {{h:UInt32}} HOUR{gf_extra}
          AND attack_type != 'N/A'
          AND outcome = 'REJECTED'
        GROUP BY attack_type
        ORDER BY cnt DESC
        LIMIT 1
        """,
        gf_params,
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
    outcome:     Annotated[str | None, _G_OUTCOME]     = None,
    policy_name: Annotated[str | None, _G_POLICY]      = None,
    vs_name:     Annotated[str | None, _G_VS]          = None,
    ip_client:   Annotated[str | None, _G_IP]          = None,
    attack_type: Annotated[str | None, _G_ATTACK_TYPE] = None,
    method:      Annotated[str | None, _G_METHOD]      = None,
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
    gf_params: dict = {"cid": cluster_id, "h": h}
    gf_conds = _build_filter_conditions(
        gf_params, outcome, policy_name, vs_name, ip_client, attack_type, method
    )
    gf_extra = (" AND " + " AND ".join(gf_conds)) if gf_conds else ""
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
          AND ts >= now() - INTERVAL {{h:UInt32}} HOUR{gf_extra}
        GROUP BY bucket
        ORDER BY bucket
        """,
        {**gf_params, "bucket": bucket_hours},
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
    outcome:     Annotated[str | None, _G_OUTCOME]     = None,
    policy_name: Annotated[str | None, _G_POLICY]      = None,
    vs_name:     Annotated[str | None, _G_VS]          = None,
    ip_client:   Annotated[str | None, _G_IP]          = None,
    attack_type: Annotated[str | None, _G_ATTACK_TYPE] = None,
    method:      Annotated[str | None, _G_METHOD]      = None,
):
    """Top attack types by event count."""
    ch = get_clickhouse()
    if not ch.available:
        return _unavailable()

    h = _hours(time_range)
    gf_params: dict = {"cid": cluster_id, "h": h}
    gf_conds = _build_filter_conditions(
        gf_params, outcome, policy_name, vs_name, ip_client, attack_type, method
    )
    gf_extra = (" AND " + " AND ".join(gf_conds)) if gf_conds else ""
    rows = ch.query(
        f"""
        SELECT
            attack_type,
            count() AS cnt,
            countIf(outcome = 'REJECTED') AS rejected
        FROM {_DB}.waf_events
        WHERE cluster_id = {{cid:UInt32}}
          AND ts >= now() - INTERVAL {{h:UInt32}} HOUR{gf_extra}
          AND attack_type != 'N/A'
        GROUP BY attack_type
        ORDER BY cnt DESC
        LIMIT {{lim:UInt32}}
        """,
        {**gf_params, "lim": limit},
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
    outcome:     Annotated[str | None, _G_OUTCOME]     = None,
    policy_name: Annotated[str | None, _G_POLICY]      = None,
    vs_name:     Annotated[str | None, _G_VS]          = None,
    ip_client:   Annotated[str | None, _G_IP]          = None,
    attack_type: Annotated[str | None, _G_ATTACK_TYPE] = None,
    method:      Annotated[str | None, _G_METHOD]      = None,
):
    """Top source IPs by blocked event count."""
    ch = get_clickhouse()
    if not ch.available:
        return _unavailable()

    h = _hours(time_range)
    gf_params: dict = {"cid": cluster_id, "h": h}
    gf_conds = _build_filter_conditions(
        gf_params, outcome, policy_name, vs_name, ip_client, attack_type, method
    )
    gf_extra = (" AND " + " AND ".join(gf_conds)) if gf_conds else ""
    rows = ch.query(
        f"""
        SELECT
            ip_client,
            count() AS total_hits,
            countIf(outcome = 'REJECTED') AS blocked_hits,
            max(ts) AS last_seen
        FROM {_DB}.waf_events
        WHERE cluster_id = {{cid:UInt32}}
          AND ts >= now() - INTERVAL {{h:UInt32}} HOUR{gf_extra}
          AND ip_client != ''
        GROUP BY ip_client
        ORDER BY blocked_hits DESC, total_hits DESC
        LIMIT {{lim:UInt32}}
        """,
        {**gf_params, "lim": limit},
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
    outcome:     Annotated[str | None, _G_OUTCOME]     = None,
    policy_name: Annotated[str | None, _G_POLICY]      = None,
    vs_name:     Annotated[str | None, _G_VS]          = None,
    ip_client:   Annotated[str | None, _G_IP]          = None,
    attack_type: Annotated[str | None, _G_ATTACK_TYPE] = None,
    method:      Annotated[str | None, _G_METHOD]      = None,
):
    """Top attacked URIs."""
    ch = get_clickhouse()
    if not ch.available:
        return _unavailable()

    h = _hours(time_range)
    gf_params: dict = {"cid": cluster_id, "h": h}
    gf_conds = _build_filter_conditions(
        gf_params, outcome, policy_name, vs_name, ip_client, attack_type, method
    )
    gf_extra = (" AND " + " AND ".join(gf_conds)) if gf_conds else ""
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
          AND ts >= now() - INTERVAL {{h:UInt32}} HOUR{gf_extra}
          AND uri != ''
          AND outcome = 'REJECTED'
        GROUP BY uri
        ORDER BY cnt DESC
        LIMIT {{lim:UInt32}}
        """,
        {**gf_params, "lim": limit},
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


@router.get(
    "/k8s/clusters/{cluster_id}/waf/dashboard/top-policies",
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("fetch WAF top policies")
def dashboard_top_policies(
    cluster_id: int,
    time_range: Annotated[str, Query()] = "24h",
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    outcome:     Annotated[str | None, _G_OUTCOME]     = None,
    policy_name: Annotated[str | None, _G_POLICY]      = None,
    vs_name:     Annotated[str | None, _G_VS]          = None,
    ip_client:   Annotated[str | None, _G_IP]          = None,
    attack_type: Annotated[str | None, _G_ATTACK_TYPE] = None,
    method:      Annotated[str | None, _G_METHOD]      = None,
):
    """Top policies by hit count — mirrors NIM's 'Top WAF Policies' panel."""
    ch = get_clickhouse()
    if not ch.available:
        return _unavailable()

    h = _hours(time_range)
    gf_params: dict = {"cid": cluster_id, "h": h}
    gf_conds = _build_filter_conditions(
        gf_params, outcome, policy_name, vs_name, ip_client, attack_type, method
    )
    gf_extra = (" AND " + " AND ".join(gf_conds)) if gf_conds else ""
    rows = ch.query(
        f"""
        SELECT
            policy_name,
            count()                        AS hits,
            countIf(outcome = 'REJECTED')  AS blocked,
            uniqExact(uri)                 AS unique_uris,
            uniqExact(ip_client)           AS unique_ips
        FROM {_DB}.waf_events
        WHERE cluster_id = {{cid:UInt32}}
          AND ts >= now() - INTERVAL {{h:UInt32}} HOUR{gf_extra}
          AND policy_name != ''
        GROUP BY policy_name
        ORDER BY hits DESC
        LIMIT {{lim:UInt32}}
        """,
        {**gf_params, "lim": limit},
    )

    return {
        "available": True,
        "time_range": time_range,
        "items": [
            {
                "policy_name": r["policy_name"],
                "hits": int(r["hits"]),
                "blocked": int(r.get("blocked", 0)),
                "unique_uris": int(r.get("unique_uris", 0)),
                "unique_ips": int(r.get("unique_ips", 0)),
            }
            for r in rows
        ],
    }


@router.get(
    "/k8s/clusters/{cluster_id}/waf/dashboard/request-methods",
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("fetch WAF request methods")
def dashboard_request_methods(
    cluster_id: int,
    time_range: Annotated[str, Query()] = "24h",
    outcome:     Annotated[str | None, _G_OUTCOME]     = None,
    policy_name: Annotated[str | None, _G_POLICY]      = None,
    vs_name:     Annotated[str | None, _G_VS]          = None,
    ip_client:   Annotated[str | None, _G_IP]          = None,
    attack_type: Annotated[str | None, _G_ATTACK_TYPE] = None,
    method:      Annotated[str | None, _G_METHOD]      = None,
):
    """HTTP method distribution — mirrors NIM's 'Request Methods' panel."""
    ch = get_clickhouse()
    if not ch.available:
        return _unavailable()

    h = _hours(time_range)
    gf_params: dict = {"cid": cluster_id, "h": h}
    gf_conds = _build_filter_conditions(
        gf_params, outcome, policy_name, vs_name, ip_client, attack_type, method
    )
    gf_extra = (" AND " + " AND ".join(gf_conds)) if gf_conds else ""
    rows = ch.query(
        f"""
        SELECT method, count() AS cnt
        FROM {_DB}.waf_events
        WHERE cluster_id = {{cid:UInt32}}
          AND ts >= now() - INTERVAL {{h:UInt32}} HOUR{gf_extra}
          AND method != ''
        GROUP BY method
        ORDER BY cnt DESC
        LIMIT 10
        """,
        gf_params,
    )

    return {
        "available": True,
        "time_range": time_range,
        "items": [{"method": r["method"], "count": int(r["cnt"])} for r in rows],
    }


@router.get(
    "/k8s/clusters/{cluster_id}/waf/dashboard/severity",
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("fetch WAF severity distribution")
def dashboard_severity(
    cluster_id: int,
    time_range: Annotated[str, Query()] = "24h",
    outcome:     Annotated[str | None, _G_OUTCOME]     = None,
    policy_name: Annotated[str | None, _G_POLICY]      = None,
    vs_name:     Annotated[str | None, _G_VS]          = None,
    ip_client:   Annotated[str | None, _G_IP]          = None,
    attack_type: Annotated[str | None, _G_ATTACK_TYPE] = None,
    method:      Annotated[str | None, _G_METHOD]      = None,
):
    """Violation-rating distribution — mirrors NIM's 'Severity' panel."""
    ch = get_clickhouse()
    if not ch.available:
        return _unavailable()

    h = _hours(time_range)
    gf_params: dict = {"cid": cluster_id, "h": h}
    gf_conds = _build_filter_conditions(
        gf_params, outcome, policy_name, vs_name, ip_client, attack_type, method
    )
    gf_extra = (" AND " + " AND ".join(gf_conds)) if gf_conds else ""
    rows = ch.query(
        f"""
        SELECT
            violation_rating,
            count() AS cnt
        FROM {_DB}.waf_events
        WHERE cluster_id = {{cid:UInt32}}
          AND ts >= now() - INTERVAL {{h:UInt32}} HOUR{gf_extra}
        GROUP BY violation_rating
        ORDER BY violation_rating DESC
        LIMIT 10
        """,
        gf_params,
    )

    # Map numeric rating to severity label matching BIG-IP NAP conventions
    def _label(rating: int) -> str:
        if rating >= 5: return "Critical"
        if rating >= 4: return "Error"
        if rating >= 2: return "Warning"
        return "Info"

    return {
        "available": True,
        "time_range": time_range,
        "items": [
            {
                "rating": int(r["violation_rating"]),
                "label": _label(int(r["violation_rating"])),
                "count": int(r["cnt"]),
            }
            for r in rows
        ],
    }


@router.get(
    "/k8s/clusters/{cluster_id}/waf/dashboard/top-signatures",
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("fetch WAF top signatures")
def dashboard_top_signatures(
    cluster_id: int,
    time_range: Annotated[str, Query()] = "24h",
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    outcome:     Annotated[str | None, _G_OUTCOME]     = None,
    policy_name: Annotated[str | None, _G_POLICY]      = None,
    vs_name:     Annotated[str | None, _G_VS]          = None,
    ip_client:   Annotated[str | None, _G_IP]          = None,
    attack_type: Annotated[str | None, _G_ATTACK_TYPE] = None,
    method:      Annotated[str | None, _G_METHOD]      = None,
):
    """Top triggered signature names — mirrors NIM's 'Top Signatures' panel."""
    ch = get_clickhouse()
    if not ch.available:
        return _unavailable()

    h = _hours(time_range)
    gf_params: dict = {"cid": cluster_id, "h": h}
    gf_conds = _build_filter_conditions(
        gf_params, outcome, policy_name, vs_name, ip_client, attack_type, method
    )
    gf_extra = (" AND " + " AND ".join(gf_conds)) if gf_conds else ""
    rows = ch.query(
        f"""
        SELECT
            sig_names,
            count()                        AS hits,
            uniqExact(ip_client)           AS unique_ips,
            uniqExact(uri)                 AS unique_uris,
            countIf(outcome = 'REJECTED')  AS blocked
        FROM {_DB}.waf_events
        WHERE cluster_id = {{cid:UInt32}}
          AND ts >= now() - INTERVAL {{h:UInt32}} HOUR{gf_extra}
          AND sig_names != ''
          AND sig_names != 'N/A'
        GROUP BY sig_names
        ORDER BY hits DESC
        LIMIT {{lim:UInt32}}
        """,
        {**gf_params, "lim": limit},
    )

    return {
        "available": True,
        "time_range": time_range,
        "items": [
            {
                "sig_name": r["sig_names"],
                "hits": int(r["hits"]),
                "unique_ips": int(r.get("unique_ips", 0)),
                "unique_uris": int(r.get("unique_uris", 0)),
                "blocked": int(r.get("blocked", 0)),
            }
            for r in rows
        ],
    }


@router.get(
    "/k8s/clusters/{cluster_id}/waf/dashboard/top-instances",
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("fetch WAF top attacked instances")
def dashboard_top_instances(
    cluster_id: int,
    time_range: Annotated[str, Query()] = "24h",
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    outcome:     Annotated[str | None, _G_OUTCOME]     = None,
    policy_name: Annotated[str | None, _G_POLICY]      = None,
    vs_name:     Annotated[str | None, _G_VS]          = None,
    ip_client:   Annotated[str | None, _G_IP]          = None,
    attack_type: Annotated[str | None, _G_ATTACK_TYPE] = None,
    method:      Annotated[str | None, _G_METHOD]      = None,
):
    """Top virtual servers (instances) by hit count — mirrors NIM's 'Top Attacked Instances' panel."""
    ch = get_clickhouse()
    if not ch.available:
        return _unavailable()

    h = _hours(time_range)
    gf_params: dict = {"cid": cluster_id, "h": h}
    gf_conds = _build_filter_conditions(
        gf_params, outcome, policy_name, vs_name, ip_client, attack_type, method
    )
    gf_extra = (" AND " + " AND ".join(gf_conds)) if gf_conds else ""
    rows = ch.query(
        f"""
        SELECT
            vs_name,
            count()                        AS hits,
            countIf(outcome = 'REJECTED')  AS blocked,
            uniqExact(uri)                 AS unique_uris,
            uniqExact(ip_client)           AS unique_ips
        FROM {_DB}.waf_events
        WHERE cluster_id = {{cid:UInt32}}
          AND ts >= now() - INTERVAL {{h:UInt32}} HOUR{gf_extra}
          AND vs_name != ''
        GROUP BY vs_name
        ORDER BY hits DESC
        LIMIT {{lim:UInt32}}
        """,
        {**gf_params, "lim": limit},
    )

    return {
        "available": True,
        "time_range": time_range,
        "items": [
            {
                "vs_name": r["vs_name"],
                "hits": int(r["hits"]),
                "blocked": int(r.get("blocked", 0)),
                "unique_uris": int(r.get("unique_uris", 0)),
                "unique_ips": int(r.get("unique_ips", 0)),
            }
            for r in rows
        ],
    }


@router.get(
    "/k8s/clusters/{cluster_id}/waf/dashboard/top-subviolations",
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("fetch WAF top sub-violations")
def dashboard_top_subviolations(
    cluster_id: int,
    namespace: str,
    hours: int = 24,
    limit: int = 10,
):
    """Top sub-violations parsed from NAP events (e.g. 'Host header contains IP address')."""
    ch = get_clickhouse()
    if not ch.available:
        return _unavailable()

    rows = ch.query(
        f"""
        SELECT
            arrayJoin(splitByString(',', sub_violations)) AS sub_violation,
            count() AS hits,
            countIf(outcome = 'REJECTED') AS blocked
        FROM {_DB}.waf_events
        WHERE cluster_id = {{cid:UInt32}}
          AND ts >= now() - INTERVAL {{h:UInt32}} HOUR
          AND sub_violations != ''
        GROUP BY sub_violation
        HAVING trimBoth(sub_violation) != ''
        ORDER BY hits DESC
        LIMIT {{lim:UInt32}}
        """,
        {"cid": cluster_id, "h": hours, "lim": limit},
    )
    return {
        "available": True,
        "items": [
            {"sub_violation": r["sub_violation"].strip(), "hits": int(r["hits"]), "blocked": int(r["blocked"])}
            for r in rows if r["sub_violation"].strip()
        ],
    }


# Prefix-to-country map for attacker IPs generated by the traffic generator
# Covers the pool in traffic-gen.py; extended with common cloud/hosting prefixes.
_GEO_MAP = {
    "185.234": ("Germany", "DE", 51.2, 10.5),
    "45.142": ("Netherlands", "NL", 52.4, 4.9),
    "91.108": ("Russia", "RU", 61.5, 105.3),
    "5.188": ("Russia", "RU", 61.5, 105.3),
    "31.184": ("Russia", "RU", 61.5, 105.3),
    "198.235": ("United States", "US", 38.0, -97.0),
    "212.102": ("United Kingdom", "GB", 55.4, -3.4),
    "162.55": ("Germany", "DE", 51.2, 10.5),
    "195.123": ("Luxembourg", "LU", 49.6, 6.1),
    "89.248": ("Netherlands", "NL", 52.4, 4.9),
    "103.21": ("China", "CN", 35.9, 104.2),
    "104.16": ("United States", "US", 38.0, -97.0),
    "172.67": ("United States", "US", 38.0, -97.0),
    "10.244": ("Private", "–", 0.0, 0.0),
    "11.11": ("Private", "–", 0.0, 0.0),
    "98.234": ("United States", "US", 38.0, -97.0),
    "76.120": ("United States", "US", 38.0, -97.0),
    "71.198": ("United States", "US", 38.0, -97.0),
    "108.14": ("United States", "US", 38.0, -97.0),
    "67.189": ("United States", "US", 38.0, -97.0),
    "50.77": ("United States", "US", 38.0, -97.0),
}


def _ip_to_geo(ip: str):
    for prefix, geo in _GEO_MAP.items():
        if ip.startswith(prefix + "."):
            return geo
    return ("Unknown", "–", 0.0, 0.0)


@router.get(
    "/k8s/clusters/{cluster_id}/waf/dashboard/top-geolocations",
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("fetch WAF top geolocations")
def dashboard_top_geolocations(
    cluster_id: int,
    namespace: str,
    hours: int = 24,
    limit: int = 15,
):
    """Top attacker geolocations with lat/lon for world-map rendering."""
    ch = get_clickhouse()
    if not ch.available:
        return _unavailable()

    rows = ch.query(
        f"""
        SELECT
            coalesce(nullIf(x_forwarded_for, ''), ip_client) AS ip,
            count()                AS hits,
            countIf(outcome = 'REJECTED') AS blocked
        FROM {_DB}.waf_events
        WHERE cluster_id = {{cid:UInt32}}
          AND ts >= now() - INTERVAL {{h:UInt32}} HOUR
        GROUP BY ip
        ORDER BY hits DESC
        LIMIT {{lim:UInt32}}
        """,
        {"cid": cluster_id, "h": hours, "lim": limit},
    )

    items = []
    by_country: dict = {}
    for r in rows:
        ip = r["ip"]
        country, code, lat, lon = _ip_to_geo(ip)
        if code == "–":
            continue
        key = code
        if key in by_country:
            by_country[key]["hits"] += int(r["hits"])
            by_country[key]["blocked"] += int(r["blocked"])
        else:
            by_country[key] = {
                "country": country, "code": code,
                "lat": lat, "lon": lon,
                "hits": int(r["hits"]), "blocked": int(r["blocked"]),
            }

    return {
        "available": True,
        "items": sorted(by_country.values(), key=lambda x: -x["hits"])[:limit],
    }


@router.get(
    "/k8s/clusters/{cluster_id}/waf/dashboard/support-id",
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("fetch WAF event by support ID")
def dashboard_support_id(
    cluster_id: int,
    support_id: Annotated[str, Query(min_length=1)],
):
    """Look up a specific WAF event by support ID."""
    ch = get_clickhouse()
    if not ch.available:
        return _unavailable()

    rows = ch.query(
        f"""
        SELECT *
        FROM {_DB}.waf_events
        WHERE cluster_id = {{cid:UInt32}}
          AND support_id = {{sid:String}}
        LIMIT 1
        """,
        {"cid": cluster_id, "sid": support_id},
    )

    if not rows:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"No event found for support_id '{support_id}'")

    r = rows[0]
    return {
        "available": True,
        "ts": str(r.get("ts", "")).replace(" ", "T"),
        "outcome": r.get("outcome", ""),
        "attack_type": r.get("attack_type", ""),
        "ip_client": r.get("ip_client", ""),
        "method": r.get("method", ""),
        "uri": r.get("uri", ""),
        "policy_name": r.get("policy_name", ""),
        "vs_name": r.get("vs_name", ""),
        "violation_rating": int(r.get("violation_rating", 0)),
        "sig_ids": r.get("sig_ids", ""),
        "sig_names": r.get("sig_names", ""),
        "support_id": r.get("support_id", ""),
        "namespace": r.get("namespace", ""),
        "ingest_source": r.get("ingest_source", ""),
        "raw_message": r.get("raw_message", ""),
    }
