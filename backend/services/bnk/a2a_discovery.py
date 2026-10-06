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

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Any

from services.bnk.helpers import build_route_ref_map

logger = logging.getLogger(__name__)

AGENT_CARD_PATH = "/.well-known/agent-card.json"
# Agent-card locations tried per port, most specific first.
_AGENT_CARD_PATHS = (".well-known/agent-card.json", ".well-known/agent.json", "agent-card.json")
# Per-candidate probe bounds: attempts and wall-clock seconds.
_MAX_PROBES_PER_CANDIDATE = 12
_PROBE_BUDGET_SECONDS = 15.0
_AGGREGATE_PROBE_DEADLINE_SECONDS = 30.0
_PLAUSIBLE_HTTP_PORTS = {80, 443, 3000, 5000, 8000, 8080, 8443, 8888, 9000, 9090}


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
            {
                "port": p.get("port"),
                "targetPort": p.get("targetPort") or p.get("target_port") or p.get("port"),
                "name": p.get("name"),
                "protocol": p.get("protocol", "TCP"),
            }
            for p in (spec.get("ports") or [])
        ]

        candidates.append(
            {
                "name": svc_name,
                "namespace": svc_ns,
                "ports": ports,
                "clusterIP": spec.get("clusterIP"),
                "selector": spec.get("selector") or {},
                "routeRefs": deduped_http_refs,
                "gateways": sorted(list({r["gatewayName"] for r in deduped_http_refs if r.get("gatewayName")})),
                "agentCard": None,  # Populated by probe
                "probeStatus": "pending",  # pending | success | error | skipped
            }
        )

    candidates.sort(key=lambda c: (c["namespace"], c["name"]))
    return candidates


# ---------------------------------------------------------------------------
# Live probing (I/O — K8s service proxy API)
# ---------------------------------------------------------------------------


def _parse_json_or_python_dict(resp: Any) -> dict | None:
    """Safely parse a response payload that may be valid JSON or YAML/dict."""
    if isinstance(resp, dict):
        return resp
    if not isinstance(resp, str):
        return None
    resp_str = resp.strip()
    if not resp_str or len(resp_str) > 1_000_000:
        return None
    try:
        data = json.loads(resp_str)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    try:
        import yaml

        data = yaml.safe_load(resp_str)
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

    # 3. Plausibly-HTTP service ports
    for p_info in candidate.get("ports", []):
        port_num = p_info.get("port")
        if not port_num or port_num in ports_to_try:
            continue
        app_proto = (p_info.get("appProtocol") or "").lower()
        if app_proto in {"http", "https"} or port_num in _PLAUSIBLE_HTTP_PORTS:
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
    ``GET /api/v1/namespaces/{ns}/pods/{pod_name}:{targetPort}/proxy/.well-known/agent-card.json``

    Mutates candidates in-place, setting ``agentCard`` and ``probeStatus``.
    """
    from kubernetes import client as k8s_client

    core_v1 = k8s_client.CoreV1Api(api_client)

    # Cache pods per (namespace, label_selector) to avoid repeated list_namespaced_pod calls
    pods_cache: dict[tuple[str, str], list[Any]] = {}

    def backing_pod(candidate: dict) -> Any | None:
        """A Running pod behind the Service, matched by its label selector."""
        selector = candidate.get("selector") or {}
        if not selector:
            return None
        ns = candidate["namespace"]
        label_selector = ",".join(f"{k}={v}" for k, v in sorted(selector.items()))
        cache_key = (ns, label_selector)
        if cache_key not in pods_cache:
            try:
                pods_cache[cache_key] = (
                    core_v1.list_namespaced_pod(
                        namespace=ns,
                        label_selector=label_selector,
                        limit=5,
                        _request_timeout=5,
                    ).items
                    or []
                )
            except Exception as e:
                logger.debug("Failed to list pods in %s for A2A probe fallback: %s", ns, e)
                pods_cache[cache_key] = []
        for p in pods_cache[cache_key]:
            labels = getattr(p.metadata, "labels", None) or {}
            if getattr(p.status, "phase", "") == "Running" and all(labels.get(k) == v for k, v in selector.items()):
                return p
        return None

    def pod_port(pod: Any, target: Any) -> int | None:
        """The container port a Service targetPort maps to on the pod; None if a named port is not found."""
        if isinstance(target, int):
            return target
        for container in getattr(pod.spec, "containers", None) or []:
            for cp in container.ports or []:
                if cp.name == target:
                    return cp.container_port
        return None

    def probe_one(candidate: dict) -> None:
        svc_name = candidate["name"]
        svc_ns = candidate["namespace"]
        ports_to_try = _candidate_probe_ports(candidate)
        target_ports = {p.get("port"): p.get("targetPort") or p.get("port") for p in candidate.get("ports", [])}

        if not ports_to_try:
            candidate["probeStatus"] = "skipped"
            return

        deadline = time.monotonic() + _PROBE_BUDGET_SECONDS
        attempts = 0

        def budget_left() -> bool:
            return attempts < _MAX_PROBES_PER_CANDIDATE and time.monotonic() < deadline

        def attempt(fetch: Any, target: str, path: str) -> dict | None:
            nonlocal attempts
            attempts += 1
            try:
                resp = fetch(name=target, namespace=svc_ns, path=path, _request_timeout=5)
            except Exception as exc:
                logger.debug("A2A probe %s/%s via %s (%s) — %s", svc_ns, svc_name, target, path, exc)
                return None
            normalized = _normalize_agent_card(_parse_json_or_python_dict(resp))
            if normalized and (normalized.get("name") or normalized.get("description") or normalized.get("skills")):
                return normalized
            return None

        # For each port and path: the service proxy, then one backing pod (GKE
        # VPC-native clusters 503 the service proxy). Interleaving keeps a slow
        # or failing service proxy from spending the budget before the pod
        # fallback runs. The pod is looked up only once the service proxy fails.
        card_found = None
        pod: list[Any] = []
        for port in ports_to_try:
            for path in _AGENT_CARD_PATHS:
                if not budget_left():
                    break
                card_found = attempt(core_v1.connect_get_namespaced_service_proxy_with_path, f"{svc_name}:{port}", path)
                if card_found:
                    break
                if not pod:
                    pod.append(backing_pod(candidate))
                pod_target = pod_port(pod[0], target_ports.get(port, port)) if pod[0] else None
                if pod_target and budget_left():
                    card_found = attempt(
                        core_v1.connect_get_namespaced_pod_proxy_with_path, f"{pod[0].metadata.name}:{pod_target}", path
                    )
                    if card_found:
                        break
            if card_found or not budget_left():
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
        done, not_done = wait(futures.keys(), timeout=_AGGREGATE_PROBE_DEADLINE_SECONDS)
        for f in not_done:
            f.cancel()
            c = futures[f]
            if not c.get("probeStatus") or c.get("probeStatus") == "pending":
                c["probeStatus"] = "error"
        for f in done:
            try:
                f.result()
            except Exception:
                pass


def _scalar_dict(value: Any) -> dict | None:
    """Keep only scalar entries of an untrusted dict (rendered as text by the UI)."""
    if not isinstance(value, dict):
        return None
    return {str(k): v for k, v in value.items() if isinstance(v, str | int | float | bool)}


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
                raw_caps.get("pushNotifications") or raw_caps.get("push_notifications") or raw_caps.get("push")
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
                normalized_skills.append(
                    {
                        "id": str(s.get("id") or s.get("name") or ""),
                        "name": str(s.get("name") or s.get("id") or s.get("title") or ""),
                        "description": str(s.get("description") or s.get("desc") or ""),
                    }
                )
            elif isinstance(s, str) and s.strip():
                normalized_skills.append(
                    {
                        "id": s.strip(),
                        "name": s.strip(),
                        "description": "",
                    }
                )

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
        "governance": _scalar_dict(card.get("governance")),
        "iconUrl": card.get("iconUrl") or card.get("icon_url"),
    }
