"""
WAF Security Logs routes.

Resolves the syslog endpoint chain:
  APPolicy / F5VirtualServer → SecPolicy → F5BigLogProfile → F5BigHslPub → host:port

Then reads up to `limit` recent log lines from the syslog TCP stream, parses
NAP's key=value default format, and returns structured entries.
"""

import logging
import re
import socket
from contextlib import suppress
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from kubernetes import client as k8s_client
from kubernetes import stream
from sqlalchemy.orm import Session

from core.errors import handle_route_errors
from database import get_db
from routes.auth import require_viewer
from services.clickhouse import CLICKHOUSE_DB as _CLICKHOUSE_DB, get_clickhouse
from services.kubernetes import KubernetesService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

# NAP default/splunk log fields we parse and surface
_NAP_KV_RE = re.compile(r'(\w+)="([^"]*)"')

# Syslog receiver pod label and log directory (matches our fluentd deployment)
_SYSLOG_POD_LABEL = "app=waf-syslog-receiver"
_SYSLOG_LOG_DIR = "/var/log/waf-syslog"

_SOCKET_TIMEOUT_S = 3.0
_READ_CHUNK = 65536
_MAX_LIMIT = 500


def _parse_nap_entry(raw: str) -> dict:
    """Parse a single NAP syslog line (key=value pairs) into a dict."""
    entry: dict = {"raw": raw.strip()}
    for key, val in _NAP_KV_RE.findall(raw):
        entry[key] = val
    return entry


def _read_logs_from_pod(
    k8s: KubernetesService,
    cluster_id: int,
    namespace: str,
    limit: int,
) -> tuple[list[dict], str | None]:
    """
    Read NAP security logs from the waf-syslog-receiver pod's log files via kubectl exec.
    Uses the k8s Python client directly for core Pod API (not in the CRD registry).
    Returns ([], None) when no receiver pod exists so caller can try TCP fallback.
    """
    try:
        cluster = k8s.get_cluster(cluster_id)
        api_client = k8s.load_kubeconfig(cluster)
        core_v1 = k8s_client.CoreV1Api(api_client)

        pod_list = core_v1.list_namespaced_pod(
            namespace,
            label_selector=_SYSLOG_POD_LABEL,
        )
        if not pod_list.items:
            return [], None  # No receiver pod; caller will try TCP

        pod_name = pod_list.items[0].metadata.name

        # Discover the most recent log file with find (no shell available in container)
        find_resp = stream.stream(
            core_v1.connect_get_namespaced_pod_exec,
            pod_name,
            namespace,
            command=["find", _SYSLOG_LOG_DIR, "-name", "security.*.log", "-type", "f"],
            stderr=True, stdin=False, stdout=True, tty=False,
        )
        # Sort candidates and pick the last (most recent by name, which is date-based)
        candidates = sorted(ln.strip() for ln in find_resp.splitlines() if ln.strip())
        log_file = candidates[-1] if candidates else f"{_SYSLOG_LOG_DIR}/security.log"

        resp = stream.stream(
            core_v1.connect_get_namespaced_pod_exec,
            pod_name,
            namespace,
            command=["tail", f"-n{limit}", log_file],
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False,
        )
        lines = [ln for ln in resp.splitlines() if ln.strip()]
        return [_parse_nap_entry(ln) for ln in lines], None
    except Exception as exc:
        logger.debug("Could not read logs from pod: %s", exc, exc_info=True)
        return [], None  # Caller will try TCP fallback


def _read_syslog_tcp(host: str, port: int, limit: int) -> tuple[list[dict], str | None]:
    """Connect to host:port via TCP, read buffered data, return parsed entries."""
    try:
        with socket.create_connection((host, port), timeout=_SOCKET_TIMEOUT_S) as sock:
            sock.settimeout(_SOCKET_TIMEOUT_S)
            chunks: list[bytes] = []
            with suppress(TimeoutError, OSError):
                while True:
                    chunk = sock.recv(_READ_CHUNK)
                    if not chunk:
                        break
                    chunks.append(chunk)

        raw_data = b"".join(chunks).decode("utf-8", errors="replace")
        lines = [ln for ln in raw_data.splitlines() if ln.strip()]
        lines = lines[-limit:]
        return [_parse_nap_entry(ln) for ln in lines], None
    except ConnectionRefusedError:
        return [], f"Connection refused to {host}:{port} — syslog server may not be running"
    except TimeoutError:
        return [], f"Timed out connecting to {host}:{port}"
    except OSError as exc:
        return [], f"Network error connecting to {host}:{port}: {exc}"


def _resolve_hslpub_endpoints(
    k8s: KubernetesService,
    cluster_id: int,
    namespace: str,
    pub_name: str,
) -> list[str]:
    """Resolve a F5BigLogHslpub name → list of 'host:port' endpoint strings."""
    try:
        pubs = k8s.get_resources(cluster_id, "f5bigloghslpub", namespace)
        for pub in pubs:
            if pub.get("metadata", {}).get("name") == pub_name:
                endpoints: list[str] = []
                for pool in pub.get("spec", {}).get("pool", []):
                    endpoints.extend(pool.get("endpoint", []))
                return endpoints
    except Exception:
        logger.debug("Could not fetch F5BigLogHslpub %s", pub_name, exc_info=True)
    return []


def _resolve_logprofile_endpoints(
    k8s: KubernetesService,
    cluster_id: int,
    namespace: str,
    profile_name: str,
) -> list[str]:
    """Walk F5BigLogProfile → publisher name → F5BigLogHslpub → endpoints."""
    try:
        profiles = k8s.get_resources(cluster_id, "f5biglogprofile", namespace)
        for profile in profiles:
            if profile.get("metadata", {}).get("name") == profile_name:
                spec = profile.get("spec", {})
                # Top-level publisher field (actual CRD schema)
                pub_name = spec.get("publisher") or (
                    spec.get("applicationSecurity", {}).get("publisher")
                    or spec.get("network", {}).get("publisher")
                    or spec.get("botDefense", {}).get("publisher")
                    or ""
                )
                if pub_name:
                    return _resolve_hslpub_endpoints(k8s, cluster_id, namespace, pub_name)
    except Exception:
        logger.debug("Could not fetch F5BigLogProfile %s", profile_name, exc_info=True)
    return []


def _resolve_secpolicy_endpoints(
    k8s: KubernetesService,
    cluster_id: int,
    namespace: str,
    secpolicy_name: str,
) -> list[str]:
    """Walk SecPolicy extensionRefs → F5BigLogProfile → F5BigHslpub → endpoints."""
    # Try both the CRD plural (secpolicies) and the registry key (bnksecpolicy)
    for resource_key in ("secpolicies", "bnksecpolicy"):
        try:
            policies = k8s.get_resources(cluster_id, resource_key, namespace)
            for pol in policies:
                if pol.get("metadata", {}).get("name") == secpolicy_name:
                    # extensionRefs contains F5BigLogProfile references
                    for ref in pol.get("spec", {}).get("extensionRefs", []):
                        if ref.get("kind") == "F5BigLogProfile":
                            profile_ns = ref.get("namespace") or namespace
                            endpoints = _resolve_logprofile_endpoints(
                                k8s, cluster_id, profile_ns, ref["name"]
                            )
                            if endpoints:
                                return endpoints
        except Exception:
            logger.debug("Could not fetch %s %s", resource_key, secpolicy_name, exc_info=True)
    return []


def _resolve_endpoints_for_policy(
    k8s: KubernetesService,
    cluster_id: int,
    namespace: str,
    policy_name: str,
) -> list[str]:
    """
    Given an APPolicy name, find syslog endpoints by walking:
      1. SecPolicies whose targetRefs include this APPolicy → extensionRefs → F5BigLogProfile → F5BigHslpub
      2. All SecPolicies in namespace (fallback when targetRefs use Gateway-level refs)
    """
    endpoints: list[str] = []
    matched_secpolicies: set[str] = set()

    for resource_key in ("secpolicies", "bnksecpolicy"):
        try:
            sec_policies = k8s.get_resources(cluster_id, resource_key, namespace)
            for pol in sec_policies:
                pol_name = pol["metadata"]["name"]
                spec = pol.get("spec", {})
                # Check targetRefs for direct APPolicy reference
                for ref in spec.get("targetRefs", []):
                    if ref.get("kind") == "APPolicy" and ref.get("name") == policy_name:
                        matched_secpolicies.add(pol_name)
                # Also check legacy items[] structure
                for item in spec.get("items", []):
                    if item.get("kind") in ("F5BigWebSecurityProfile", "APPolicy"):
                        ref_name = item.get("name", "")
                        if ref_name == policy_name or not ref_name:
                            matched_secpolicies.add(pol_name)
        except Exception:
            logger.debug("Could not list %s resources", resource_key, exc_info=True)

    # Resolve endpoints from matched SecPolicies
    for pol_name in matched_secpolicies:
        ep = _resolve_secpolicy_endpoints(k8s, cluster_id, namespace, pol_name)
        endpoints.extend(ep)

    if not endpoints:
        # Fallback: scan all SecPolicies in namespace (covers Gateway-level targetRefs)
        for resource_key in ("secpolicies", "bnksecpolicy"):
            try:
                sec_policies = k8s.get_resources(cluster_id, resource_key, namespace)
                for pol in sec_policies:
                    pol_name = pol["metadata"]["name"]
                    ep = _resolve_secpolicy_endpoints(k8s, cluster_id, namespace, pol_name)
                    endpoints.extend(ep)
            except Exception:
                logger.debug("Could not scan %s resources", resource_key, exc_info=True)

    if not endpoints:
        # Last resort: any F5BigLogProfile in namespace with a publisher resolves to an endpoint.
        # This covers deployments where log profile is configured independently of a SecPolicy.
        try:
            profiles = k8s.get_resources(cluster_id, "f5biglogprofile", namespace)
            for profile in profiles:
                pub_name = profile.get("spec", {}).get("publisher", "")
                if pub_name:
                    ep = _resolve_hslpub_endpoints(k8s, cluster_id, namespace, pub_name)
                    endpoints.extend(ep)
        except Exception:
            logger.debug("Could not scan F5BigLogProfile resources", exc_info=True)

    return list(dict.fromkeys(endpoints))  # deduplicate preserving order


def _read_logs_from_clickhouse(
    ch,
    cluster_id: int,
    cr_kind: str,
    cr_name: str,
    limit: int,
    outcome_filter: str | None,
    attack_type_filter: str | None,
    vs_name_filter: str | None,
    ip_filter: str | None = None,
    uri_filter: str | None = None,
) -> tuple[list[dict], str | None]:
    """Query ClickHouse for security logs and return entries in NAP-compatible shape.

    Returns raw_message verbatim so the UI shows the original log format the user
    configured — whether NAP, custom, or OTEL-normalised.
    """
    conditions = ["cluster_id = {cid:UInt32}"]
    params: dict = {"cid": cluster_id, "limit": limit}

    if cr_kind == "appolicy":
        conditions.append("policy_name = {policy:String}")
        params["policy"] = cr_name
    if outcome_filter:
        conditions.append("outcome = {outcome:String}")
        params["outcome"] = outcome_filter.upper()
    if attack_type_filter:
        conditions.append("positionCaseInsensitive(attack_type, {atf:String}) > 0")
        params["atf"] = attack_type_filter
    if vs_name_filter:
        conditions.append("positionCaseInsensitive(vs_name, {vsf:String}) > 0")
        params["vsf"] = vs_name_filter
    if ip_filter:
        conditions.append("ip_client = {ipf:String}")
        params["ipf"] = ip_filter
    if uri_filter:
        conditions.append("positionCaseInsensitive(uri, {urif:String}) > 0")
        params["urif"] = uri_filter

    where = " AND ".join(conditions)
    # Use string concatenation — where contains ClickHouse {param:Type} placeholders
    # that Python f-string would try to evaluate, causing a silent ValueError → empty results.
    # raw_message/ingest_source live in waf_events_otel; waf_events has namespace/ingest_ts.
    sql = (
        "SELECT ts, outcome, attack_type, ip_client, method, uri,"
        " policy_name, vs_name, violation_rating, support_id,"
        " sig_ids, sig_names,"
        " '' AS raw_message, 'clickhouse' AS ingest_source"
        f" FROM {_CLICKHOUSE_DB}.waf_events"
        f" WHERE {where}"
        " ORDER BY ts DESC"
        " LIMIT {limit:UInt32}"
    )

    try:
        rows = ch.query(sql, params)
        entries = []
        for r in rows:
            # raw_message = original syslog line as the WAF emitted it
            # If empty (legacy celery event), synthesise from parsed fields
            raw = r.get("raw_message") or ""
            if not raw:
                raw = (
                    f'attack_type="{r.get("attack_type","")}",'
                    f'date_time="{r.get("ts","")}",ip_client="{r.get("ip_client","")}",method="{r.get("method","")}",policy_name="{r.get("policy_name","")}",outcome="{r.get("outcome","")}",'
                    f'support_id="{r.get("support_id","")}"'
                )
            entry = {
                "raw":              raw,
                "date_time":        str(r.get("ts", "")),
                "outcome":          r.get("outcome", ""),
                "attack_type":      r.get("attack_type", ""),
                "ip_client":        r.get("ip_client", ""),
                "method":           r.get("method", ""),
                "uri":              r.get("uri", ""),
                "policy_name":      r.get("policy_name", ""),
                "vs_name":          r.get("vs_name", ""),
                "violation_rating": str(r.get("violation_rating", "")),
                "support_id":       r.get("support_id", ""),
                "sig_ids":          r.get("sig_ids", ""),
                "sig_names":        r.get("sig_names", ""),
                "request_status":   r.get("outcome", "").lower(),
                "ingest_source":    r.get("ingest_source", ""),
            }
            entries.append(entry)
        return entries, None
    except Exception as exc:
        logger.error("ClickHouse security-logs query failed: %s", exc)
        return [], str(exc)


@router.get(
    "/k8s/clusters/{cluster_id}/waf/security-logs",
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("fetch WAF security logs")
def get_waf_security_logs(
    cluster_id: int,
    namespace: str,
    cr_kind: Annotated[str | None, Query(description="'appolicy' or 'f5virtualserver'; omit to query all policies")] = None,
    cr_name: str | None = None,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = 200,
    outcome_filter: str | None = None,
    attack_type_filter: str | None = None,
    vs_name_filter: str | None = None,
    ip_filter: str | None = None,
    uri_filter: str | None = None,
    db: Session = Depends(get_db),
):
    """
    Resolve the syslog endpoint for the given CR and return recent security log entries.

    Resolution chain:
      APPolicy  → SecPolicy (items[].kind=F5BigWebSecurityProfile) → F5BigLogProfile → F5BigHslPub
      F5VirtualServer → SecPolicy (targetRef) → F5BigLogProfile → F5BigHslPub
    """
    # When ClickHouse is available, query it directly — no syslog endpoint resolution needed.
    # raw_message column preserves the original log line in the user's chosen format.
    ch = get_clickhouse()
    if ch.available:
        entries, error = _read_logs_from_clickhouse(
            ch, cluster_id, cr_kind, cr_name,
            limit, outcome_filter, attack_type_filter, vs_name_filter,
            ip_filter, uri_filter,
        )
        return {
            "entries": entries,
            "total": len(entries),
            "source_endpoint": "clickhouse",
            "cr_kind": cr_kind,
            "cr_name": cr_name,
            "error": error,
            "source": "clickhouse",
        }

    # Fallback path: resolve syslog endpoint and read from pod/TCP
    k8s = KubernetesService(db)
    endpoints: list[str] = []

    if cr_kind == "appolicy":
        endpoints = _resolve_endpoints_for_policy(k8s, cluster_id, namespace, cr_name)
    elif cr_kind == "f5virtualserver":
        # F5VirtualServer references a SecPolicy by name in its spec
        try:
            vss = k8s.get_resources(cluster_id, "f5virtualserver", namespace)
            for vs in vss:
                if vs.get("metadata", {}).get("name") == cr_name:
                    sec_pol_ref = (
                        vs.get("spec", {}).get("securityPolicyRef", {}).get("name")
                        or vs.get("spec", {}).get("secPolicy")
                        or ""
                    )
                    if sec_pol_ref:
                        ep = _resolve_secpolicy_endpoints(k8s, cluster_id, namespace, sec_pol_ref)
                        endpoints.extend(ep)
        except Exception:
            logger.debug("Could not resolve F5VirtualServer %s", cr_name, exc_info=True)

    if not endpoints:
        return {
            "entries": [],
            "total": 0,
            "source_endpoint": None,
            "cr_kind": cr_kind,
            "cr_name": cr_name,
            "warning": (
                "No syslog endpoint found. Ensure a SecPolicy with a F5BigLogProfile "
                "and F5BigLogHslpub is attached to this resource."
            ),
        }

    # Use the first resolved endpoint (pool members share the same log stream)
    host_port = endpoints[0]
    try:
        host, port_str = host_port.rsplit(":", 1)
        host = host.strip("[]")  # strip IPv6 brackets
        port = int(port_str)
    except ValueError:
        return {
            "entries": [],
            "total": 0,
            "source_endpoint": host_port,
            "cr_kind": cr_kind,
            "cr_name": cr_name,
            "warning": f"Could not parse syslog endpoint address: {host_port!r}",
        }

    # (ClickHouse already handled above — this is the pure syslog fallback path)
    entries, error = _read_logs_from_pod(k8s, cluster_id, namespace, limit)
    if not entries:
        entries, error = _read_syslog_tcp(host, port, limit)

    if cr_kind == "appolicy":
        entries = [e for e in entries if not e.get("policy_name") or cr_name in e.get("policy_name", "")]
    if vs_name_filter:
        entries = [e for e in entries if vs_name_filter in e.get("vs_name", "")]
    if outcome_filter:
        entries = [e for e in entries if e.get("outcome", "").upper() == outcome_filter.upper()]
    if attack_type_filter:
        entries = [e for e in entries if attack_type_filter.lower() in e.get("attack_type", "").lower()]
    if ip_filter:
        entries = [e for e in entries if e.get("ip_client", "") == ip_filter]
    if uri_filter:
        entries = [e for e in entries if uri_filter.lower() in e.get("uri", "").lower()]

    return {
        "entries": entries,
        "total": len(entries),
        "source_endpoint": host_port,
        "all_endpoints": endpoints,
        "cr_kind": cr_kind,
        "cr_name": cr_name,
        "error": error,
        "source": "syslog",
    }
