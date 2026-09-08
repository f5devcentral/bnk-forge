"""
BNK policy associations — maps security policies to their enforcement points.

Two distinct association paths:
- Ingress: BNKSecPolicy → Gateway → F5BigFwPolicy
- Egress: F5SPKEgress.spec.firewallEnforcedPolicy → F5BigFwPolicy

Builds an enriched association list showing which firewall policies
are enforced where, with full firewall rule details.

Pure data transformation: consumes the resources dict from
``fetch_all_bnk_data``.
"""

from typing import Any

from services.bnk.helpers import make_resource_map, resolve_list_refs, resource_name, resource_ns


def analyze_policy_associations(data: dict[str, Any]) -> dict[str, Any]:
    """Build the policy-gateway and policy-egress associations from raw BNK data."""
    resources = data["resources"]

    bnksecpolicies = resources.get("bnksecpolicy", [])
    gateways = resources.get("gateway", [])
    firewallpolicies = resources.get("f5bigfwpolicy", [])
    egresses = resources.get("f5spkegress", [])

    # Pre-index gateways and firewall policies by (namespace, name) for O(1) lookups
    gw_index: dict[tuple[str, str], dict] = {
        (resource_ns(gw), resource_name(gw)): gw for gw in gateways
    }
    fw_index: dict[tuple[str, str], dict] = {
        (resource_ns(fw), resource_name(fw)): fw for fw in firewallpolicies
    }

    addr_map = make_resource_map(resources.get("f5bigcneaddresslist", []))
    port_map = make_resource_map(resources.get("f5bigcneportlist", []))

    associations: list[dict[str, Any]] = []
    for bnk in bnksecpolicies:
        bnk_ns = resource_ns(bnk)

        for target in bnk.get("spec", {}).get("targetRefs", []):
            if target.get("kind") != "Gateway":
                continue

            gateway_name = target.get("name")
            listener_name = target.get("sectionName")
            gateway = gw_index.get((bnk_ns, gateway_name))

            for ext in bnk.get("spec", {}).get("extensionRefs", []):
                if ext.get("kind") != "F5BigFwPolicy":
                    continue

                policy_name = ext.get("name")
                policy = fw_index.get((bnk_ns, policy_name))

                association = _build_association(
                    bnk, bnk_ns, gateway_name, listener_name,
                    policy_name, gateway, policy, addr_map, port_map,
                )
                associations.append(association)

    for egress in egresses:
        egress_ns = resource_ns(egress)
        egress_spec = egress.get("spec", {})
        policy_name = egress_spec.get("firewallEnforcedPolicy")
        if not policy_name:
            continue

        policy = fw_index.get((egress_ns, policy_name))
        associations.append(
            _build_egress_association(egress, egress_ns, policy_name, policy, addr_map, port_map)
        )

    return {
        "associations": associations,
        "count": len(associations),
    }


def _build_association(
    bnk: dict,
    bnk_ns: str,
    gateway_name: str | None,
    listener_name: str | None,
    policy_name: str | None,
    gateway: dict | None,
    policy: dict | None,
    addr_map: dict[str, dict] | None = None,
    port_map: dict[str, dict] | None = None,
) -> dict[str, Any]:
    """Build a single policy-gateway association entry."""
    association: dict[str, Any] = {
        "kind": "gateway",
        "bnk_policy_name": resource_name(bnk),
        "namespace": bnk_ns,
        "gateway_name": gateway_name,
        "listener_name": listener_name,
        "firewall_policy_name": policy_name,
    }

    if gateway:
        addresses = gateway.get("status", {}).get("addresses", [])
        association["gateway_ip"] = addresses[0].get("value", "") if addresses else ""

        listeners = gateway.get("spec", {}).get("listeners", [])
        listener = next(
            (li for li in listeners if li.get("name") == listener_name),
            None,
        )
        if listener:
            association["port"] = listener.get("port")
            association["protocol"] = listener.get("protocol")

    if policy:
        association["rules_count"], association["rules"] = _extract_fw_rules(
            policy, addr_map or {}, port_map or {}, bnk_ns,
        )

    return association


def _extract_fw_rules(
    policy: dict,
    addr_map: dict[str, dict],
    port_map: dict[str, dict],
    policy_ns: str,
) -> tuple[int, list[dict[str, Any]]]:
    """Extract the rule count and rule details from an F5BigFwPolicy.

    Source/destination are enriched with resolved addresses/ports (direct
    values plus members of any referenced address/port lists), alongside
    the raw list names for provenance.
    """
    rules = (policy.get("spec") or {}).get("rule") or []
    extracted = [
        {
            "name": rule.get("name", "") if rule else "",
            "action": rule.get("action", "") if rule else "",
            "ipProtocol": rule.get("ipProtocol", "") if rule else "",
            "source": _resolve_rule_direction((rule or {}).get("source"), addr_map, port_map, policy_ns),
            "destination": _resolve_rule_direction((rule or {}).get("destination"), addr_map, port_map, policy_ns),
            "logging": rule.get("logging", False) if rule else False,
        }
        for rule in rules
    ]
    return len(extracted), extracted


def _resolve_rule_direction(
    direction: dict | None,
    addr_map: dict[str, dict],
    port_map: dict[str, dict],
    policy_ns: str,
) -> dict[str, Any]:
    """Resolve a rule's source/destination addressLists/portLists into inline addresses/ports."""
    dir_dict = direction or {}
    address_lists = dir_dict.get("addressLists") or []
    port_lists = dir_dict.get("portLists") or []

    addresses = list(dir_dict.get("addresses") or [])
    for resolved in resolve_list_refs(address_lists, addr_map, policy_ns, "addresses"):
        for addr in (resolved.get("addresses") or []):
            if addr and addr not in addresses:
                addresses.append(addr)

    raw_ports = dir_dict.get("ports") or []
    ports = [str(p) for p in raw_ports if p is not None]
    for resolved in resolve_list_refs(port_lists, port_map, policy_ns, "ports"):
        for port in (resolved.get("ports") or []):
            if port is not None:
                str_port = str(port)
                if str_port not in ports:
                    ports.append(str_port)

    return {
        "addresses": addresses,
        "ports": ports,
        "addressLists": list(address_lists),
        "portLists": list(port_lists),
    }


def _build_egress_association(
    egress: dict,
    egress_ns: str,
    policy_name: str,
    policy: dict | None,
    addr_map: dict[str, dict] | None = None,
    port_map: dict[str, dict] | None = None,
) -> dict[str, Any]:
    """Build a single policy-egress association entry."""
    egress_spec = egress.get("spec") or {}
    cni_config = egress_spec.get("pseudoCNIConfig") or {}

    association: dict[str, Any] = {
        "kind": "egress",
        "egress_name": resource_name(egress),
        "namespace": egress_ns,
        "captured_namespaces": cni_config.get("namespaces") or [],
        "snat_type": egress_spec.get("snatType"),
        "firewall_policy_name": policy_name,
    }

    if policy:
        association["rules_count"], association["rules"] = _extract_fw_rules(
            policy, addr_map or {}, port_map or {}, egress_ns,
        )

    return association
