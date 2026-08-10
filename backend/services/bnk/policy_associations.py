"""
BNK policy associations — maps BNKSecPolicy → Gateway → F5BigFwPolicy.

Builds an enriched association list showing which security policies
target which gateways/listeners, with full firewall rule details.

Pure data transformation: consumes the resources dict from
``fetch_all_bnk_data``.
"""

from typing import Any

from services.bnk.helpers import resource_name, resource_ns


def analyze_policy_associations(data: dict[str, Any]) -> dict[str, Any]:
    """Build the policy-gateway associations from raw BNK data."""
    resources = data["resources"]

    bnksecpolicies = resources.get("bnksecpolicy", [])
    gateways = resources.get("gateway", [])
    firewallpolicies = resources.get("f5bigfwpolicy", [])

    # Pre-index gateways and firewall policies by (namespace, name) for O(1) lookups
    gw_index: dict[tuple[str, str], dict] = {
        (resource_ns(gw), resource_name(gw)): gw for gw in gateways
    }
    fw_index: dict[tuple[str, str], dict] = {
        (resource_ns(fw), resource_name(fw)): fw for fw in firewallpolicies
    }

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
                    policy_name, gateway, policy,
                )
                associations.append(association)

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
) -> dict[str, Any]:
    """Build a single policy-gateway association entry."""
    association: dict[str, Any] = {
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
        rules = policy.get("spec", {}).get("rule", [])
        association["rules_count"] = len(rules)
        association["rules"] = [
            {
                "name": rule.get("name", ""),
                "action": rule.get("action", ""),
                "ipProtocol": rule.get("ipProtocol", ""),
                "source": rule.get("source", {}),
                "destination": rule.get("destination", {}),
                "logging": rule.get("logging", False),
            }
            for rule in rules
        ]

    return association
