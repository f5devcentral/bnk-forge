"""
Scanner node parsing — convert V1Node to a useful dict.
"""

import re
from typing import Any

_QUANTITY_RE = re.compile(r"^([\d.]+)([EPTGMK]i?)?$")

_QUANTITY_MULTIPLIERS = {
    "": 1,
    "K": 1000, "M": 1000**2, "G": 1000**3, "T": 1000**4, "P": 1000**5, "E": 1000**6,
    "Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4, "Pi": 1024**5, "Ei": 1024**6,
}


def _quantity_is_positive(value: Any) -> bool:
    """Return True if a Kubernetes resource quantity string is non-zero.

    Handles plain integers ("1024"), zero ("0"), and suffixed quantities
    (e.g. "2Gi", "512Ki"). Returns False for None/unparsable values.
    """
    if value is None:
        return False
    text = str(value).strip()
    if not text:
        return False
    match = _QUANTITY_RE.match(text)
    if not match:
        return False
    number, suffix = match.groups()
    try:
        amount = float(number)
    except ValueError:
        return False
    multiplier = _QUANTITY_MULTIPLIERS.get(suffix or "", None)
    if multiplier is None:
        return False
    return amount * multiplier > 0


def parse_node(node) -> dict[str, Any]:
    """
    Parse a kubernetes.client.V1Node into a plain dict.

    Extracts roles, readiness, resource allocatable/capacity, HugePages,
    SR-IOV VFs, provider ID, and high-performance node labels.
    """
    meta = node.metadata
    status = node.status
    spec = getattr(node, "spec", None)
    info = status.node_info if status else None
    allocatable = (status.allocatable or {}) if status else {}
    capacity = (status.capacity or {}) if status else {}
    labels = dict(meta.labels or {})
    provider_id = getattr(spec, "provider_id", None) if spec else None

    roles = [
        label.split("/")[-1]
        for label, _val in labels.items()
        if label.startswith("node-role.kubernetes.io/")
    ] or ["worker"]

    ready = (
        status is not None
        and status.conditions is not None
        and any(c.type == "Ready" and c.status == "True" for c in status.conditions)
    )

    def _sriov_resources(resources: dict) -> dict[str, str]:
        return {
            k: v
            for k, v in resources.items()
            if "sriov" in k.lower() or "vf" in k.lower() or "netdevice" in k.lower()
        }

    return {
        "name": meta.name,
        "roles": roles,
        "ready": ready,
        "labels": labels,
        "provider_id": provider_id,
        "os_image": info.os_image if info else None,
        "kernel_version": info.kernel_version if info else None,
        "container_runtime": info.container_runtime_version if info else None,
        "kubelet_version": info.kubelet_version if info else None,
        "architecture": info.architecture if info else None,
        "instance_type": (
            labels.get("node.kubernetes.io/instance-type")
            or labels.get("beta.kubernetes.io/instance-type")
        ),
        "zone": (
            labels.get("topology.kubernetes.io/zone")
            or labels.get("failure-domain.beta.kubernetes.io/zone")
        ),
        "allocatable": {
            "cpu": allocatable.get("cpu"),
            "memory": allocatable.get("memory"),
            "pods": allocatable.get("pods"),
            "hugepages_2mi": allocatable.get("hugepages-2Mi"),
            "hugepages_1gi": allocatable.get("hugepages-1Gi"),
            "sriov_resources": _sriov_resources(allocatable),
        },
        "capacity": {
            "cpu": capacity.get("cpu"),
            "memory": capacity.get("memory"),
            "hugepages_2mi": capacity.get("hugepages-2Mi"),
            "hugepages_1gi": capacity.get("hugepages-1Gi"),
            "sriov_resources": _sriov_resources(capacity),
        },
        "is_hp_node": (
            "f5-tmm" in labels.get("app", "")
            or labels.get("node-role.kubernetes.io/f5-tmm") is not None
            or _quantity_is_positive(capacity.get("hugepages-2Mi"))
        ),
    }


def is_kind_node(node: dict[str, Any]) -> bool:
    """Return True if a single parsed node dict looks like a kind (Docker) node.

    Primary signal is ``spec.providerID`` (live proof:
    ``kind://docker/bnkfull/bnkfull-control-plane``). A ``-control-plane``
    name suffix combined with any ``kind``-ish providerID corroborates.
    """
    provider_id = (node.get("provider_id") or "").lower()
    if provider_id.startswith("kind://"):
        return True
    name = (node.get("name") or "").lower()
    return name.endswith("-control-plane") and "kind" in provider_id


def is_kind_cluster(nodes: list[dict[str, Any]]) -> bool:
    """Return True if any node in the cluster is a kind (Docker) node."""
    return any(is_kind_node(n) for n in nodes)


def is_local_cluster(nodes: list[dict[str, Any]]) -> bool:
    """Return True for local/lab clusters (kind, minikube, Docker Desktop).

    Cheap, Node-API-only detection — no privileged probe required. Used to
    steer BNK node-readiness UX toward lab-friendly defaults.
    """
    if is_kind_cluster(nodes):
        return True
    for n in nodes:
        provider_id = (n.get("provider_id") or "").lower()
        name = (n.get("name") or "").lower()
        if "minikube" in provider_id or name in ("docker-desktop", "minikube"):
            return True
    return False
