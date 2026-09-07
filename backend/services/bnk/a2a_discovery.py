"""
A2A agent discovery — scans HTTPRoute backends for agent-card endpoints.

Pure data transformation that reuses the topology tree from
``analyze_topology`` plus the ``build_route_ref_map`` helper from
``helpers``. Optionally probes backend services via the K8s service
proxy API to fetch ``/.well-known/agent-card.json``.

The analysis phase (no I/O) produces a list of *candidate* agents —
services behind HTTPRoutes that could be A2A agents. The optional
probe phase (with I/O) attempts to fetch actual agent cards.
"""

import ast
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from services.bnk.helpers import build_route_ref_map

logger = logging.getLogger(__name__)

AGENT_CARD_PATH = "/.well-known/agent-card.json"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def discover_a2a_agents(
    data: dict[str, Any],
    topology: list[dict],
    api_client: Any | None = None,
    probe: bool = False,
) -> list[dict]:
    """
    Discover A2A agent candidates from BNK topology.

    1. Walks the topology to find services behind HTTPRoutes.
    2. Optionally probes each service for ``/.well-known/agent-card.json``.

    Args:
        data: Raw BNK data from ``fetch_all_bnk_data``.
        topology: Topology list from ``analyze_topology``.
        api_client: K8s API client (required if probe=True).
        probe: If True, attempt to fetch agent cards from backend services.

    Returns:
        List of agent dicts with service info, route refs, and optional
        agent card data.
    """
    route_ref_map = build_route_ref_map(topology)
    services = data["resources"].get("service", [])

    # Build a lookup of services that are behind HTTPRoutes
    candidates = _find_http_backend_services(services, route_ref_map)

    if probe and api_client and candidates:
        _probe_agent_cards(candidates, api_client)

    return candidates


# ---------------------------------------------------------------------------
# Candidate extraction (pure, no I/O)
# ---------------------------------------------------------------------------


def _find_http_backend_services(
    services: list[dict],
    route_ref_map: dict[tuple[str, str], list[dict]],
) -> list[dict]:
    """
    Find services that are backends of HTTPRoutes — A2A agent candidates.

    Only includes services referenced by at least one HTTPRoute (not TCP/UDP/etc.)
    since A2A is an HTTP-based protocol (JSON-RPC over HTTP).
    """
    candidates: list[dict] = []

    for svc in services:
        meta = svc.get("metadata", {})
        spec = svc.get("spec", {})
        svc_name = meta.get("name", "")
        svc_ns = meta.get("namespace", "")
        route_refs = route_ref_map.get((svc_ns, svc_name), [])

        # Only include services behind HTTPRoutes (A2A uses HTTP)
        http_refs = [r for r in route_refs if r.get("kind") == "HTTPRoute"]
        if not http_refs:
            continue

        # Deduplicate route refs while preserving uniqueness of (kind, namespace, name, port, gatewayName)
        seen_refs: set[tuple[str, str, str, Any, str]] = set()
        deduped_http_refs: list[dict] = []
        for r in http_refs:
            ref_key = (
                r.get("kind", ""),
                r.get("namespace", ""),
                r.get("name", ""),
                r.get("port"),
                r.get("gatewayName", ""),
            )
            if ref_key not in seen_refs:
                seen_refs.add(ref_key)
                deduped_http_refs.append(r)

        ports = [
            {"port": p.get("port"), "name": p.get("name"), "protocol": p.get("protocol", "TCP")}
            for p in (spec.get("ports") or [])
        ]

        candidates.append({
            "name": svc_name,
            "namespace": svc_ns,
            "ports": ports,
            "clusterIP": spec.get("clusterIP"),
            "routeRefs": deduped_http_refs,
            "gateways": sorted(list({r["gatewayName"] for r in deduped_http_refs if r.get("gatewayName")})),
            "agentCard": None,       # Populated by probe
            "probeStatus": "pending",  # pending | success | error | skipped
        })

    candidates.sort(key=lambda c: (c["namespace"], c["name"]))
    return candidates


# ---------------------------------------------------------------------------
# Live probing (I/O — K8s service proxy API)
# ---------------------------------------------------------------------------


def _parse_json_or_python_dict(resp: Any) -> dict | None:
    """Safely parse a response payload that may be valid JSON, a Python dict, or str(dict)."""
    if isinstance(resp, dict):
        return resp
    if not isinstance(resp, str):
        return None
    resp_str = resp.strip()
    if not resp_str:
        return None
    try:
        data = json.loads(resp_str)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    try:
        data = ast.literal_eval(resp_str)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return None


def _candidate_probe_ports(candidate: dict) -> list[int]:
    """
    Return ordered list of ports to probe for an agent card.
    Prioritizes HTTPRoute backendRef port, then named HTTP ports, then any service port.
    """
    ports_to_try: list[int] = []

    # 1. Ports from routeRefs (what the HTTPRoute actually directs traffic to)
    for ref in candidate.get("routeRefs", []):
        p = ref.get("port")
        if p and p not in ports_to_try:
            ports_to_try.append(p)

    # 2. Preferred named ports from service spec
    preferred_names = {"http", "a2a", "api", "web", "mcp", "app"}
    for p_info in candidate.get("ports", []):
        port_num = p_info.get("port")
        if not port_num or port_num in ports_to_try:
            continue
        if (p_info.get("name") or "").lower() in preferred_names:
            ports_to_try.append(port_num)

    # 3. All other service ports
    for p_info in candidate.get("ports", []):
        port_num = p_info.get("port")
        if port_num and port_num not in ports_to_try:
            ports_to_try.append(port_num)

    return ports_to_try


def _probe_agent_cards(
    candidates: list[dict],
    api_client: Any,
) -> None:
    """
    Probe each candidate service for ``/.well-known/agent-card.json``.

    Uses the K8s API server's service proxy endpoint first:
    ``GET /api/v1/namespaces/{ns}/services/{name}:{port}/proxy/.well-known/agent-card.json``
    If service proxy fails (e.g. 503 Service Unavailable / no endpoints available
    on VPC-native GKE clusters), falls back to direct pod proxy:
    ``GET /api/v1/namespaces/{ns}/pods/{pod_name}:{port}/proxy/.well-known/agent-card.json``

    Mutates candidates in-place, setting ``agentCard`` and ``probeStatus``.
    """
    from kubernetes import client as k8s_client

    core_v1 = k8s_client.CoreV1Api(api_client)

    paths = [
        "/.well-known/agent-card.json",
        ".well-known/agent-card.json",
        "/.well-known/agent.json",
        ".well-known/agent.json",
        "/agent-card.json",
        "agent-card.json",
    ]

    # Cache pods per namespace to avoid repeated list_namespaced_pod calls
    pods_cache: dict[str, list[Any]] = {}

    def get_namespace_pods(ns: str) -> list[Any]:
        if ns not in pods_cache:
            try:
                pods_cache[ns] = core_v1.list_namespaced_pod(namespace=ns, _request_timeout=5).items or []
            except Exception as e:
                logger.debug("Failed to list pods in %s for A2A probe fallback: %s", ns, e)
                pods_cache[ns] = []
        return pods_cache[ns]

    def probe_one(candidate: dict) -> None:
        svc_name = candidate["name"]
        svc_ns = candidate["namespace"]
        ports_to_try = _candidate_probe_ports(candidate)

        if not ports_to_try:
            candidate["probeStatus"] = "skipped"
            return

        card_found = None

        # 1. First attempt: Service proxy
        for port in ports_to_try:
            proxy_targets = [f"{svc_name}:{port}", f"http:{svc_name}:{port}", svc_name]
            for proxy_name in proxy_targets:
                for path in paths:
                    try:
                        resp = core_v1.connect_get_namespaced_service_proxy_with_path(
                            name=proxy_name,
                            namespace=svc_ns,
                            path=path,
                            _request_timeout=5,
                        )
                        card = _parse_json_or_python_dict(resp)
                        normalized = _normalize_agent_card(card)
                        if normalized and (normalized.get("name") or normalized.get("description") or normalized.get("skills")):
                            card_found = normalized
                            break
                    except Exception as exc:
                        logger.debug("A2A service probe %s/%s via %s (%s) — %s", svc_ns, svc_name, proxy_name, path, exc)
                if card_found:
                    break
            if card_found:
                break

        # 2. Second attempt: Pod proxy fallback (e.g. for GKE VPC-native clusters where service proxy 503s)
        if not card_found:
            ns_pods = get_namespace_pods(svc_ns)
            matching_pods = [
                p for p in ns_pods
                if (
                    (getattr(p.metadata, "name", "") or "").startswith(svc_name)
                    or (getattr(p.metadata, "labels", None) and getattr(p.metadata, "labels", {}).get("app") == svc_name)
                )
                and getattr(p.status, "phase", "") == "Running"
            ]

            for pod in matching_pods:
                pod_name = pod.metadata.name
                for port in ports_to_try:
                    for path in paths:
                        try:
                            resp = core_v1.connect_get_namespaced_pod_proxy_with_path(
                                name=f"{pod_name}:{port}",
                                namespace=svc_ns,
                                path=path,
                                _request_timeout=5,
                            )
                            card = _parse_json_or_python_dict(resp)
                            normalized = _normalize_agent_card(card)
                            if normalized and (normalized.get("name") or normalized.get("description") or normalized.get("skills")):
                                card_found = normalized
                                break
                        except Exception as exc:
                            logger.debug("A2A pod probe %s/%s via %s:%s (%s) — %s", svc_ns, svc_name, pod_name, port, path, exc)
                    if card_found:
                        break
                if card_found:
                    break

        if card_found:
            candidate["agentCard"] = card_found
            candidate["probeStatus"] = "success"
        else:
            candidate["probeStatus"] = "error"

    # Probe in parallel (max 10 concurrent to avoid overwhelming the API server)
    max_workers = min(len(candidates), 10)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(probe_one, c): c for c in candidates}
        for future in as_completed(futures):
            future.result()  # propagate exceptions (caught inside)


def _normalize_agent_card(card: Any) -> dict | None:
    """
    Normalize an A2A agent card response to a consistent shape.

    Expected fields per A2A spec:
    - name, description, version, url
    - capabilities: {streaming, pushNotifications}
    - skills: [{id, name, description}]
    - defaultInputModes, defaultOutputModes
    - provider: {organization, url}
    - securitySchemes
    - governance
    """
    if not isinstance(card, dict):
        return None

    name = card.get("name") or card.get("agent_name") or card.get("title") or card.get("id") or ""
    description = card.get("description") or card.get("overview") or card.get("summary") or ""
    version = str(card.get("version") or "")

    # Normalize capabilities (list or dict)
    raw_caps = card.get("capabilities")
    if isinstance(raw_caps, list):
        caps_set = {str(c).lower().replace("_", "").replace("-", "") for c in raw_caps}
        capabilities = {
            "streaming": "streaming" in caps_set,
            "pushNotifications": any(k in caps_set for k in ("push", "pushnotifications", "notifications")),
        }
    elif isinstance(raw_caps, dict):
        capabilities = {
            "streaming": bool(raw_caps.get("streaming")),
            "pushNotifications": bool(
                raw_caps.get("pushNotifications")
                or raw_caps.get("push_notifications")
                or raw_caps.get("push")
            ),
        }
    else:
        capabilities = {}

    # Normalize skills / tools
    raw_skills = card.get("skills") or card.get("tools") or card.get("methods") or []
    normalized_skills: list[dict] = []
    if isinstance(raw_skills, list):
        for s in raw_skills:
            if isinstance(s, dict):
                normalized_skills.append({
                    "id": str(s.get("id") or s.get("name") or ""),
                    "name": str(s.get("name") or s.get("id") or s.get("title") or ""),
                    "description": str(s.get("description") or s.get("desc") or ""),
                })
            elif isinstance(s, str) and s.strip():
                normalized_skills.append({
                    "id": s.strip(),
                    "name": s.strip(),
                    "description": "",
                })

    return {
        "name": str(name),
        "description": str(description),
        "version": version,
        "url": str(card.get("url") or ""),
        "capabilities": capabilities,
        "skills": normalized_skills,
        "defaultInputModes": card.get("defaultInputModes") or card.get("default_input_modes") or [],
        "defaultOutputModes": card.get("defaultOutputModes") or card.get("default_output_modes") or [],
        "provider": card.get("provider", {}) if isinstance(card.get("provider"), dict) else {},
        "securitySchemes": card.get("securitySchemes") or card.get("security_schemes") or {},
        "governance": card.get("governance") if isinstance(card.get("governance"), dict) else None,
        "iconUrl": card.get("iconUrl") or card.get("icon_url"),
    }

