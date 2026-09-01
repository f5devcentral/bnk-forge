"""
BNK topology analysis — builds the gateway topology tree.

Maps gateways → listeners → routes → backends with security policies,
network policies, iRules, data plane resources (VLANs, CNE instances,
static routes, SNAT pools, egresses), and reference grants.

Pure data transformation: takes the dict from ``fetch_all_bnk_data``
and returns a structured topology dict.
"""

import re
from typing import Any

from services.bnk.helpers import (
    get_condition_message,
    has_condition,
    make_resource_map,
    resolve_list_refs,
    resource_name,
    resource_ns,
)

# ---------------------------------------------------------------------------
# Route types — single source of truth for kind labels and resource keys
# ---------------------------------------------------------------------------

_ROUTE_KINDS: list[tuple[str, str]] = [
    ("httproute", "HTTPRoute"),
    ("grpcroute", "GRPCRoute"),
    ("tcproute", "TCPRoute"),
    ("udproute", "UDPRoute"),
    ("tlsroute", "TLSRoute"),
    ("l4route", "L4Route"),
]


# ---------------------------------------------------------------------------
# Operational-state helpers
# ---------------------------------------------------------------------------


def _listener_status(gw_status: dict, listener_name: str) -> dict:
    """Find the Gateway status entry for a listener by name."""
    for entry in gw_status.get("listeners", []) or []:
        if isinstance(entry, dict) and entry.get("name") == listener_name:
            return entry
    return {}


def _route_parent_status(route: dict, gw_name: str, gw_ns: str, listener_name: str) -> dict | None:
    """Find the route's parent status entry matching a gateway/listener."""
    status = route.get("status", {}) or {}
    parents = status.get("parents") or status.get("parentRefs") or []
    for parent in parents:
        if not isinstance(parent, dict):
            continue
        ref = parent.get("parentRef", parent)
        if not isinstance(ref, dict):
            continue
        parent_ns = ref.get("namespace", route.get("metadata", {}).get("namespace", ""))
        if ref.get("name") == gw_name and parent_ns == gw_ns:
            section = ref.get("sectionName")
            if section and section != listener_name:
                continue
            return parent
    # Fallback: legacy controllers may put conditions directly on status
    if status.get("conditions"):
        return status
    return None


def _first_condition_message(conditions: list) -> str:
    """Return the first non-empty condition message, or an empty string."""
    for cond in conditions:
        if isinstance(cond, dict):
            msg = cond.get("message")
            if msg:
                return str(msg)
    return ""


def _policy_status(resource: dict) -> dict[str, Any]:
    """Derive resolved/programmed operational state from a BNK policy resource."""
    return {
        "resolved": has_condition(resource, "Resolved") or has_condition(resource, "Accepted"),
        "programmed": has_condition(resource, "Programmed"),
        "messages": {
            "resolved": get_condition_message(resource, "Resolved") or get_condition_message(resource, "Accepted"),
            "programmed": get_condition_message(resource, "Programmed"),
        },
    }


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------


def analyze_topology(data: dict[str, Any]) -> dict[str, Any]:
    """Build the gateway topology response from raw BNK data."""
    resources = data["resources"]

    gateways = resources.get("gateway", [])
    referencegrants = resources.get("referencegrant", [])
    bnksecpolicies = resources.get("bnksecpolicy", [])
    bnknetpolicies = resources.get("bnknetpolicy", [])

    # Unify all route types for topology matching
    all_routes: list[tuple[dict, str]] = []
    route_counts: dict[str, int] = {}
    for res_key, kind in _ROUTE_KINDS:
        items = resources.get(res_key, [])
        route_counts[res_key] = len(items)
        all_routes.extend((r, kind) for r in items)

    # Build lookup maps
    fw_map = make_resource_map(resources.get("f5bigfwpolicy", []))
    irule_map = make_resource_map(resources.get("f5bigcneirule", []))
    addr_map = make_resource_map(resources.get("f5bigcneaddresslist", []))
    port_map = make_resource_map(resources.get("f5bigcneportlist", []))
    analyzers = resources.get("f5biganalyzer", [])

    # Build topology per gateway
    topology = [
        _build_gateway_node(
            gw, all_routes, analyzers, bnknetpolicies, irule_map,
            bnksecpolicies, fw_map, addr_map, port_map,
        )
        for gw in gateways
    ]

    # Data Plane section
    data_plane = _build_data_plane(resources)

    # ReferenceGrant summary
    ref_grants = [
        {
            "name": resource_name(rg),
            "namespace": resource_ns(rg),
            "from": rg.get("spec", {}).get("from", []),
            "to": rg.get("spec", {}).get("to", []),
        }
        for rg in referencegrants
    ]

    return {
        "topology": topology,
        "dataPlane": data_plane,
        "referenceGrants": ref_grants,
        "counts": _build_counts(resources, gateways, route_counts, referencegrants),
    }


# ---------------------------------------------------------------------------
# Gateway node builder
# ---------------------------------------------------------------------------


def _build_gateway_node(
    gw: dict,
    all_routes: list[tuple[dict, str]],
    analyzers: list[dict],
    bnknetpolicies: list[dict],
    irule_map: dict[str, dict],
    bnksecpolicies: list[dict],
    fw_map: dict[str, dict],
    addr_map: dict[str, dict],
    port_map: dict[str, dict],
) -> dict[str, Any]:
    """Build a single gateway topology node with listeners and policies."""
    gw_meta = gw.get("metadata", {})
    gw_spec = gw.get("spec", {})
    gw_status = gw.get("status", {})
    gw_name = gw_meta.get("name", "")
    gw_ns = gw_meta.get("namespace", "")

    listeners_data = [
        _build_listener_node(
            listener, all_routes, analyzers, bnknetpolicies, irule_map,
            gw_name, gw_ns, gw_status,
        )
        for listener in gw_spec.get("listeners", [])
    ]

    sec_policies_data = _match_sec_policies(
        bnksecpolicies, fw_map, addr_map, port_map, gw_name,
    )

    return {
        "name": gw_name,
        "namespace": gw_ns,
        "gatewayClassName": gw_spec.get("gatewayClassName", ""),
        "addresses": [a.get("value", "") for a in gw_status.get("addresses", [])],
        "accepted": has_condition(gw, "Accepted"),
        "programmed": has_condition(gw, "Programmed"),
        "conditions": gw_status.get("conditions", []) or [],
        "listeners": listeners_data,
        "securityPolicies": sec_policies_data,
    }


# ---------------------------------------------------------------------------
# Listener node builder
# ---------------------------------------------------------------------------


def _build_listener_node(
    listener: dict,
    all_routes: list[tuple[dict, str]],
    analyzers: list[dict],
    bnknetpolicies: list[dict],
    irule_map: dict[str, dict],
    gw_name: str,
    gw_ns: str,
    gw_status: dict,
) -> dict[str, Any]:
    """Build a single listener node with routes and network policies."""
    listener_name = listener.get("name", "")
    status = _listener_status(gw_status, listener_name)
    return {
        "name": listener_name,
        "protocol": listener.get("protocol", ""),
        "port": listener.get("port"),
        "attachedRouteCount": status.get("attachedRoutes", 0) or 0,
        "conditions": status.get("conditions", []) or [],
        "routes": _match_routes_to_listener(
            all_routes, analyzers, gw_name, gw_ns, listener_name,
        ),
        "networkPolicies": _match_net_policies(
            bnknetpolicies, irule_map, gw_name, listener_name,
        ),
    }


# ---------------------------------------------------------------------------
# Route matching
# ---------------------------------------------------------------------------


def _match_routes_to_listener(
    all_routes: list[tuple[dict, str]],
    analyzers: list[dict],
    gw_name: str,
    gw_ns: str,
    listener_name: str,
) -> list[dict]:
    """Find all routes (HTTP, GRPC, TCP, UDP, TLS, L4) targeting a gateway/listener."""
    routes_data: list[dict] = []
    for route, route_kind in all_routes:
        route_meta = route.get("metadata", {})
        route_spec = route.get("spec", {})
        route_ns = route_meta.get("namespace", "")

        for parent in route_spec.get("parentRefs", []):
            parent_ns = parent.get("namespace", route_ns)
            if parent.get("name") != gw_name or parent_ns != gw_ns:
                continue
            section = parent.get("sectionName")
            if section and section != listener_name:
                continue

            backends = [
                {
                    "name": br.get("name", ""),
                    "namespace": br.get("namespace"),
                    "port": br.get("port"),
                    "weight": br.get("weight"),
                    "kind": br.get("kind", "Service"),
                    "group": br.get("group", ""),
                }
                for rule in route_spec.get("rules", [])
                for br in rule.get("backendRefs", [])
            ]

            route_name = route_meta.get("name", "")
            parent_status = _route_parent_status(route, gw_name, gw_ns, listener_name)
            parent_conditions = parent_status.get("conditions", []) if parent_status else []
            accepted = has_condition(parent_status, "Accepted") if parent_status else False

            routes_data.append({
                "name": route_name,
                "namespace": route_ns,
                "kind": route_kind,
                "hostnames": route_spec.get("hostnames", []),
                "backends": backends,
                "analyzers": _match_analyzers(
                    analyzers, route_name, route_kind, route_ns, gw_ns,
                ),
                "accepted": accepted,
                "conditions": parent_conditions,
                "conditionMessage": _first_condition_message(parent_conditions) if not accepted else "",
            })

    return routes_data


def _match_analyzers(
    analyzers: list[dict],
    route_name: str,
    route_kind: str,
    route_ns: str,
    gw_ns: str,
) -> list[dict]:
    """Check if any analyzer monitors a specific route."""
    result: list[dict] = []
    for analyzer in analyzers:
        a_spec = analyzer.get("spec", {})
        for app in a_spec.get("applications", []):
            if (app.get("name") == route_name
                    and app.get("kind") == route_kind
                    and app.get("namespace", gw_ns) == route_ns):
                script = a_spec.get("script", {})
                params = script.get("custom", {}).get("parameters", [])
                result.append({
                    "name": resource_name(analyzer),
                    "schedule": a_spec.get("schedule", ""),
                    "scriptType": script.get("type", ""),
                    "dataSources": [ds.get("name", "") for ds in a_spec.get("dataSources", [])],
                    "parameters": {p.get("key", ""): p.get("value", "") for p in params},
                })
    return result


# ---------------------------------------------------------------------------
# Policy matching
# ---------------------------------------------------------------------------


def _match_net_policies(
    bnknetpolicies: list[dict],
    irule_map: dict[str, dict],
    gw_name: str,
    listener_name: str,
) -> list[dict]:
    """Find BNKNetPolicies targeting a specific gateway/listener."""
    result: list[dict] = []
    for np_item in bnknetpolicies:
        np_spec = np_item.get("spec", {})
        np_ns = resource_ns(np_item)

        for target in np_spec.get("targetRefs", []):
            if not (target.get("name") == gw_name
                    and target.get("sectionName") == listener_name
                    and target.get("kind") == "Gateway"):
                continue

            extensions = _build_extensions(np_spec.get("extensionRefs", []), np_ns, irule_map)
            descendants = np_item.get("status", {}).get("descendants", [])
            resolved_count = sum(
                1 for d in descendants
                if any(c.get("status") == "True" for c in d.get("conditions", []))
            )

            status = _policy_status(np_item)
            result.append({
                "name": resource_name(np_item),
                "namespace": np_ns,
                "extensions": extensions,
                "resolvedCount": resolved_count,
                "totalExtensions": len(extensions),
                "resolved": status["resolved"],
                "programmed": status["programmed"],
                "messages": status["messages"],
            })

    return result


def _build_extensions(
    extension_refs: list[dict],
    policy_ns: str,
    irule_map: dict[str, dict],
) -> list[dict[str, Any]]:
    """Build extension data with optional iRule enrichment."""
    extensions: list[dict[str, Any]] = []
    for ext in extension_refs:
        ext_data: dict[str, Any] = {
            "kind": ext.get("kind", ""),
            "name": ext.get("name", ""),
            "group": ext.get("group", ""),
        }
        if ext.get("kind") == "F5BigCneIrule":
            irule_obj = irule_map.get(f"{policy_ns}/{ext.get('name', '')}")
            if irule_obj:
                code = irule_obj.get("spec", {}).get("iRule", "")
                ext_data["lineCount"] = len(code.split("\n")) if code else 0
                ext_data["eventHandlers"] = re.findall(r"when\s+(\w+)", code)
        extensions.append(ext_data)
    return extensions


def _match_sec_policies(
    bnksecpolicies: list[dict],
    fw_map: dict[str, dict],
    addr_map: dict[str, dict],
    port_map: dict[str, dict],
    gw_name: str,
) -> list[dict]:
    """Find BNKSecPolicies targeting a specific gateway."""
    result: list[dict] = []
    for sp in bnksecpolicies:
        sp_spec = sp.get("spec", {})
        sp_ns = resource_ns(sp)

        for target in sp_spec.get("targetRefs", []):
            if not (target.get("name") == gw_name and target.get("kind") == "Gateway"):
                continue

            fw_refs = _build_firewall_refs(
                sp_spec.get("extensionRefs", []), fw_map, addr_map, port_map, sp_ns,
            )
            status = _policy_status(sp)
            result.append({
                "name": resource_name(sp),
                "namespace": sp_ns,
                "targetListener": target.get("sectionName", ""),
                "firewallPolicies": fw_refs,
                "resolved": status["resolved"],
                "programmed": status["programmed"],
                "messages": status["messages"],
            })

    return result


def _build_firewall_refs(
    extension_refs: list[dict],
    fw_map: dict[str, dict],
    addr_map: dict[str, dict],
    port_map: dict[str, dict],
    policy_ns: str,
) -> list[dict[str, Any]]:
    """Build firewall policy references with enriched rule details."""
    fw_refs: list[dict[str, Any]] = []
    for ext in extension_refs:
        if ext.get("kind") != "F5BigFwPolicy":
            continue

        fw_obj = fw_map.get(f"{policy_ns}/{ext.get('name', '')}")
        rules: list[dict] = []
        referenced_addr_lists: set[str] = set()
        referenced_port_lists: set[str] = set()

        if fw_obj:
            for rule in fw_obj.get("spec", {}).get("rule", []):
                rules.append({
                    "name": rule.get("name", ""),
                    "action": rule.get("action", ""),
                    "ipProtocol": rule.get("ipProtocol", ""),
                    "logging": rule.get("logging", False),
                })
                # Collect referenced address/port lists from both source and destination
                for direction in ("source", "destination"):
                    dir_spec = rule.get(direction, {})
                    referenced_addr_lists.update(dir_spec.get("addressLists", []))
                    referenced_port_lists.update(dir_spec.get("portLists", []))

        fw_refs.append({
            "name": ext.get("name", ""),
            "rules": rules,
            "addressLists": resolve_list_refs(referenced_addr_lists, addr_map, policy_ns, "addresses"),
            "portLists": resolve_list_refs(referenced_port_lists, port_map, policy_ns, "ports"),
        })

    return fw_refs


# ---------------------------------------------------------------------------
# Data plane builder
# ---------------------------------------------------------------------------


def _build_data_plane(resources: dict[str, list]) -> dict[str, Any]:
    """Build the data plane section of the topology response."""
    vlans = resources.get("f5spkvlan", [])
    cneinstances = resources.get("cneinstance", [])
    staticroutes = resources.get("f5spkstaticroute", [])
    snatpools = resources.get("f5spksnatpool", [])
    egresses = resources.get("f5spkegress", [])
    hslpubs = resources.get("f5bigloghslpub", [])
    logprofiles = resources.get("f5biglogprofile", [])

    return {
        "vlans": [_build_vlan(v) for v in vlans],
        "cneInstances": [_build_cne_instance(c) for c in cneinstances],
        "staticRoutes": [
            {
                "name": resource_name(sr),
                "namespace": resource_ns(sr),
                "destination": sr.get("spec", {}).get("destination", ""),
                "gateway": sr.get("spec", {}).get("gateway", ""),
            }
            for sr in staticroutes
        ],
        "snatPools": [
            {
                "name": resource_name(sp),
                "namespace": resource_ns(sp),
                "addresses": sp.get("spec", {}).get("addresses", sp.get("spec", {}).get("members", [])),
            }
            for sp in snatpools
        ],
        "egresses": [_build_egress(eg) for eg in egresses],
        "logging": {
            "hslPublishers": [
                {
                    "name": resource_name(hsl),
                    "namespace": resource_ns(hsl),
                    "pool": hsl.get("spec", {}).get("pool", {}),
                    "protocol": hsl.get("spec", {}).get("protocol", ""),
                }
                for hsl in hslpubs
            ],
            "logProfiles": [
                {
                    "name": resource_name(lp),
                    "namespace": resource_ns(lp),
                    "publishers": lp.get("spec", {}).get("publishers", []),
                }
                for lp in logprofiles
            ],
        },
    }


def _build_vlan(vlan: dict) -> dict[str, Any]:
    """Build a single VLAN entry for the data plane section."""
    v_spec = vlan.get("spec", {})
    return {
        "name": resource_name(vlan),
        "namespace": resource_ns(vlan),
        "interfaces": v_spec.get("interfaces", []),
        "selfipV4s": v_spec.get("selfip_v4s", v_spec.get("selfipV4s", [])),
        "prefixLen": v_spec.get("prefixlen_v4", v_spec.get("prefix_len", v_spec.get("prefixLen"))),
        "mtu": v_spec.get("mtu"),
        "internal": v_spec.get("internal", False),
        "autoLasthop": v_spec.get("auto_lasthop", v_spec.get("autoLasthop", "")),
        "ready": has_condition(vlan, "Ready") or has_condition(vlan, "Programmed"),
    }


def _build_cne_instance(cne: dict) -> dict[str, Any]:
    """Build a single CNE instance entry for the data plane section."""
    c_spec = cne.get("spec", {})
    # Derive feature flags from live spec — any key whose value is a dict
    # containing 'enabled' is a feature flag. This avoids a hardcoded key list
    # that would drop newly-added features (e.g. coreCollection, envDiscovery).
    features: dict[str, bool] = {
        key: bool(val.get("enabled"))
        for key, val in c_spec.items()
        if isinstance(val, dict) and "enabled" in val
    }
    return {
        "name": resource_name(cne),
        "namespace": resource_ns(cne),
        "features": features,
        "networkAttachments": c_spec.get("networkAttachments", []),
        "containerPlatform": c_spec.get("containerPlatform", ""),
        "phase": cne.get("status", {}).get("phase", ""),
        "ready": has_condition(cne, "Programmed") or has_condition(cne, "Ready"),
    }


# ---------------------------------------------------------------------------
# Counts builder
# ---------------------------------------------------------------------------


def _build_egress(egress: dict) -> dict[str, Any]:
    """Build a single F5SPKEgress entry for the data plane section."""
    eg_spec = egress.get("spec") or {}
    cni_config = eg_spec.get("pseudoCNIConfig") or {}
    vxlan_config = cni_config.get("vxlan")
    return {
        "name": resource_name(egress),
        "namespace": resource_ns(egress),
        "snatType": eg_spec.get("snatType", ""),
        "egressSnatpool": eg_spec.get("egressSnatpool"),
        "firewallEnforcedPolicy": eg_spec.get("firewallEnforcedPolicy"),
        "logProfile": eg_spec.get("logProfile"),
        "capturedNamespaces": cni_config.get("namespaces") or [],
        "vxlan": {
            "tmmInterfaceName": vxlan_config.get("tmmInterfaceName", "") or "",
            "nodeInterfaceName": vxlan_config.get("nodeInterfaceName", "") or "",
        } if isinstance(vxlan_config, dict) and vxlan_config else None,
        "ready": has_condition(egress, "Programmed"),
    }


def _build_counts(
    resources: dict[str, list],
    gateways: list[dict],
    route_counts: dict[str, int],
    referencegrants: list[dict],
) -> dict[str, int]:
    """Build the counts summary for the topology response."""
    return {
        "gateways": len(gateways),
        "listeners": sum(len(gw.get("spec", {}).get("listeners", [])) for gw in gateways),
        "httpRoutes": route_counts.get("httproute", 0),
        "grpcRoutes": route_counts.get("grpcroute", 0),
        "tcpRoutes": route_counts.get("tcproute", 0),
        "udpRoutes": route_counts.get("udproute", 0),
        "tlsRoutes": route_counts.get("tlsroute", 0),
        "l4Routes": route_counts.get("l4route", 0),
        "totalRoutes": sum(route_counts.values()),
        "referenceGrants": len(referencegrants),
        "securityPolicies": len(resources.get("bnksecpolicy", [])),
        "networkPolicies": len(resources.get("bnknetpolicy", [])),
        "firewallPolicies": len(resources.get("f5bigfwpolicy", [])),
        "iRules": len(resources.get("f5bigcneirule", [])),
        "analyzers": len(resources.get("f5biganalyzer", [])),
        "vlans": len(resources.get("f5spkvlan", [])),
        "cneInstances": len(resources.get("cneinstance", [])),
        "staticRoutes": len(resources.get("f5spkstaticroute", [])),
        "snatPools": len(resources.get("f5spksnatpool", [])),
        "egresses": len(resources.get("f5spkegress", [])),
        "hslPublishers": len(resources.get("f5bigloghslpub", [])),
        "logProfiles": len(resources.get("f5biglogprofile", [])),
    }
