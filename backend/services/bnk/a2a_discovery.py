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

        ports = [
            {"port": p.get("port"), "name": p.get("name"), "protocol": p.get("protocol", "TCP")}
            for p in (spec.get("ports") or [])
        ]

        candidates.append({
            "name": svc_name,
            "namespace": svc_ns,
            "ports": ports,
            "clusterIP": spec.get("clusterIP"),
            "routeRefs": http_refs,
            "gateways": list({r["gatewayName"] for r in http_refs}),
            "agentCard": None,       # Populated by probe
            "probeStatus": "pending",  # pending | success | error | skipped
        })

    candidates.sort(key=lambda c: (c["namespace"], c["name"]))
    return candidates


# ---------------------------------------------------------------------------
# Live probing (I/O — K8s service proxy API)
# ---------------------------------------------------------------------------


def _probe_agent_cards(
    candidates: list[dict],
    api_client: Any,
) -> None:
    """
    Probe each candidate service for ``/.well-known/agent-card.json``.

    Uses the K8s API server's service proxy endpoint:
    ``GET /api/v1/namespaces/{ns}/services/{name}:{port}/proxy/.well-known/agent-card.json``

    Mutates candidates in-place, setting ``agentCard`` and ``probeStatus``.
    """
    from kubernetes import client as k8s_client

    core_v1 = k8s_client.CoreV1Api(api_client)

    def probe_one(candidate: dict) -> None:
        svc_name = candidate["name"]
        svc_ns = candidate["namespace"]

        # Pick the first HTTP-ish port (prefer named "http"/"a2a", then first port)
        port = _pick_probe_port(candidate["ports"])
        if port is None:
            candidate["probeStatus"] = "skipped"
            return

        proxy_name = f"{svc_name}:{port}"
        try:
            # K8s service proxy: GET /api/v1/namespaces/{ns}/services/{name}:{port}/proxy/{path}
            resp = core_v1.connect_get_namespaced_service_proxy_with_path(
                name=proxy_name,
                namespace=svc_ns,
                path=".well-known/agent-card.json",
            )
            # resp is a JSON string when content-type is application/json
            import json
            card = json.loads(resp) if isinstance(resp, str) else resp
            candidate["agentCard"] = _normalize_agent_card(card)
            candidate["probeStatus"] = "success"
        except Exception as exc:
            # Expected for non-A2A services — not an error, just not an agent
            logger.debug("A2A probe %s/%s:%s — %s", svc_ns, svc_name, port, exc)
            candidate["probeStatus"] = "error"

    # Probe in parallel (max 10 concurrent to avoid overwhelming the API server)
    max_workers = min(len(candidates), 10)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(probe_one, c): c for c in candidates}
        for future in as_completed(futures):
            future.result()  # propagate exceptions (shouldn't happen — caught inside)


def _pick_probe_port(ports: list[dict]) -> int | None:
    """Pick the best port to probe for an agent card."""
    if not ports:
        return None
    # Prefer ports named "http", "a2a", "api", "web"
    preferred = {"http", "a2a", "api", "web"}
    for p in ports:
        if (p.get("name") or "").lower() in preferred:
            return p["port"]
    # Fall back to first port
    return ports[0]["port"]


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
    """
    if not isinstance(card, dict):
        return None
    return {
        "name": card.get("name", ""),
        "description": card.get("description", ""),
        "version": card.get("version", ""),
        "url": card.get("url", ""),
        "capabilities": card.get("capabilities", {}),
        "skills": card.get("skills", []),
        "defaultInputModes": card.get("defaultInputModes", []),
        "defaultOutputModes": card.get("defaultOutputModes", []),
        "provider": card.get("provider", {}),
        "securitySchemes": card.get("securitySchemes", {}),
        "iconUrl": card.get("iconUrl"),
    }
