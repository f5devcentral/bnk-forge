"""
BNK palette extraction — lightweight resource summaries for the Config Builder.

Extracts gateway classes, services, address/port lists, and iRules
with just enough detail for the Configuration Builder palette UI.

Pure data transformation: consumes the resources dict from
``fetch_all_bnk_data``.
"""

import re
from typing import Any


def extract_palette_data(data: dict[str, Any]) -> dict[str, Any]:
    """
    Extract lightweight summaries of cluster resources for the Configuration
    Builder palette: gateway classes, services, address lists, port lists,
    and iRules.
    """
    resources = data["resources"]

    gateway_classes = [
        {
            "name": gc.get("metadata", {}).get("name", ""),
            "controllerName": gc.get("spec", {}).get("controllerName", ""),
        }
        for gc in resources.get("gatewayclass", [])
    ]

    services = [
        {
            "name": svc.get("metadata", {}).get("name", ""),
            "namespace": svc.get("metadata", {}).get("namespace", ""),
            "ports": [
                {"port": p.get("port"), "name": p.get("name"), "protocol": p.get("protocol", "TCP")}
                for p in (svc.get("spec", {}).get("ports") or [])
            ],
        }
        for svc in resources.get("service", [])
    ]

    address_lists = [
        {
            "name": al.get("metadata", {}).get("name", ""),
            "namespace": al.get("metadata", {}).get("namespace", ""),
            "addresses": al.get("spec", {}).get("addresses") or [],
        }
        for al in resources.get("f5bigcneaddresslist", [])
    ]

    port_lists = [
        {
            "name": pl.get("metadata", {}).get("name", ""),
            "namespace": pl.get("metadata", {}).get("namespace", ""),
            "ports": [str(p) for p in (pl.get("spec", {}).get("ports") or [])],
        }
        for pl in resources.get("f5bigcneportlist", [])
    ]

    irules = [
        _summarize_irule(ir)
        for ir in resources.get("f5bigcneirule", [])
    ]

    return {
        "gatewayClasses": sorted(gateway_classes, key=lambda x: x["name"]),
        "services": sorted(services, key=lambda x: (x["namespace"], x["name"])),
        "addressLists": sorted(address_lists, key=lambda x: (x["namespace"], x["name"])),
        "portLists": sorted(port_lists, key=lambda x: (x["namespace"], x["name"])),
        "iRules": sorted(irules, key=lambda x: (x["namespace"], x["name"])),
    }


def _summarize_irule(ir: dict) -> dict[str, Any]:
    """Extract a lightweight summary of an iRule resource."""
    meta = ir.get("metadata", {})
    code = ir.get("spec", {}).get("iRule", "")
    lines = code.strip().split("\n") if code else []
    handlers = re.findall(r"when\s+(\w+)", code) if code else []
    return {
        "name": meta.get("name", ""),
        "namespace": meta.get("namespace", ""),
        "lineCount": len(lines),
        "eventHandlers": handlers,
    }
