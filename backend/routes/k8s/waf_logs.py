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
from sqlalchemy.orm import Session

from core.errors import handle_route_errors
from database import get_db
from routes.auth import require_viewer
from services.kubernetes import KubernetesService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

# NAP default/splunk log fields we parse and surface
_NAP_KV_RE = re.compile(r'(\w+)="([^"]*)"')

# Syslog TCP read timeout — we read what's buffered then return
_SOCKET_TIMEOUT_S = 5.0
_READ_CHUNK = 65536
_MAX_LIMIT = 500


def _parse_nap_entry(raw: str) -> dict:
    """Parse a single NAP syslog line (key=value pairs) into a dict."""
    entry: dict = {"raw": raw.strip()}
    for key, val in _NAP_KV_RE.findall(raw):
        entry[key] = val
    return entry


def _read_syslog_tcp(host: str, port: int, limit: int) -> tuple[list[dict], str | None]:
    """
    Connect to host:port via TCP, read buffered data, return parsed entries.
    Returns (entries, error_message).  error_message is None on success.
    """
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
        # Keep the most recent `limit` lines
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
                # publisher field appears under various log type sub-objects
                pub_name = (
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
    """Walk SecPolicy → F5BigLogProfile ref → F5BigLogHslpub → endpoints."""
    try:
        policies = k8s.get_resources(cluster_id, "bnksecpolicy", namespace)
        for pol in policies:
            if pol.get("metadata", {}).get("name") == secpolicy_name:
                for ref in pol.get("spec", {}).get("targetRefs", []):
                    if ref.get("kind") in ("F5BigLogProfile", "F5BigLogprofile"):
                        profile_ns = ref.get("namespace") or namespace
                        endpoints = _resolve_logprofile_endpoints(
                            k8s, cluster_id, profile_ns, ref["name"]
                        )
                        if endpoints:
                            return endpoints
    except Exception:
        logger.debug("Could not fetch SecPolicy %s", secpolicy_name, exc_info=True)
    return []


def _resolve_endpoints_for_policy(
    k8s: KubernetesService,
    cluster_id: int,
    namespace: str,
    policy_name: str,
) -> list[str]:
    """
    Given an APPolicy name, find all SecPolicies that reference it (via
    F5BigWebSecurityProfile annotations) then resolve their HSL endpoints.
    Falls back to checking all SecPolicies in the namespace.
    """
    endpoints: list[str] = []
    try:
        sec_policies = k8s.get_resources(cluster_id, "bnksecpolicy", namespace)
        for pol in sec_policies:
            spec = pol.get("spec", {})
            # SecPolicy embeds WAF policy ref in items[].kind == F5BigWebSecurityProfile
            for item in spec.get("items", []):
                if item.get("kind") in ("F5BigWebSecurityProfile", "APPolicy"):
                    ref_name = item.get("name", "")
                    if ref_name == policy_name or not ref_name:
                        pol_name = pol["metadata"]["name"]
                        ep = _resolve_secpolicy_endpoints(k8s, cluster_id, namespace, pol_name)
                        endpoints.extend(ep)
    except Exception:
        logger.debug("Could not resolve endpoints for APPolicy %s", policy_name, exc_info=True)
    return list(dict.fromkeys(endpoints))  # deduplicate preserving order


@router.get(
    "/k8s/clusters/{cluster_id}/waf/security-logs",
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("fetch WAF security logs")
def get_waf_security_logs(
    cluster_id: int,
    namespace: str,
    cr_kind: Annotated[str, Query(description="'appolicy' or 'f5virtualserver'")],
    cr_name: str,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = 200,
    outcome_filter: str | None = None,
    attack_type_filter: str | None = None,
    vs_name_filter: str | None = None,
    db: Session = Depends(get_db),
):
    """
    Resolve the syslog endpoint for the given CR and return recent security log entries.

    Resolution chain:
      APPolicy  → SecPolicy (items[].kind=F5BigWebSecurityProfile) → F5BigLogProfile → F5BigHslPub
      F5VirtualServer → SecPolicy (targetRef) → F5BigLogProfile → F5BigHslPub
    """
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

    entries, error = _read_syslog_tcp(host, port, limit)

    # Filter by cr_name using the NAP policy_name / vs_name fields
    if cr_kind == "appolicy":
        entries = [e for e in entries if not e.get("policy_name") or cr_name in e.get("policy_name", "")]
    if vs_name_filter:
        entries = [e for e in entries if vs_name_filter in e.get("vs_name", "")]
    if outcome_filter:
        entries = [e for e in entries if e.get("outcome", "").upper() == outcome_filter.upper()]
    if attack_type_filter:
        entries = [e for e in entries if attack_type_filter.lower() in e.get("attack_type", "").lower()]

    return {
        "entries": entries,
        "total": len(entries),
        "source_endpoint": host_port,
        "all_endpoints": endpoints,
        "cr_kind": cr_kind,
        "cr_name": cr_name,
        "error": error,
    }
