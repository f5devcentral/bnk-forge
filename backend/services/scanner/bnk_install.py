"""
Scanner BNK install analyzer — detect existing F5 BNK installation.

Pure function (dicts in → dict out) with no I/O.
"""

import re
from typing import Any

from services.bnk_pod_discovery import classify_f5_pods, detect_install_shape
from services.scanner.constants import F5_CRD_GROUPS


def analyze_bnk_install(
    crds: list[dict],
    crd_names: set[str],
    crd_groups: set[str],
    f5_tenant_pods: list[dict],
    f5_utils_pods: list[dict],
    helm_releases: list[dict],
    cneinstances: list,
    vlans: list,
    namespaces: list[str],
) -> dict[str, Any]:
    """
    Detect an existing F5 BNK installation.

    Analyzes F5 CRDs, pod classification, FLO/TMM versions, CNEInstance
    features, VLANs, and determines overall installation status/health.
    """
    # F5 CRDs
    f5_crds = [c for c in crds if c.get("group") in F5_CRD_GROUPS]
    has_f5_crds = len(f5_crds) > 0
    f5_crd_groups = {c["group"] for c in f5_crds}

    # F5 namespaces
    ns_set = set(namespaces)
    ns_info = {
        "f5_operator": "f5-operator" in ns_set,
        "f5_bnk": "f5-bnk" in ns_set,
        "f5_utils": "f5-utils" in ns_set,
        "f5_cne_core": "f5-cne-core" in ns_set,
    }

    # Classify pods
    classified = classify_f5_pods(f5_tenant_pods, f5_utils_pods)
    tmm_pods = classified["tmm"]
    flo_pods = classified["flo"]
    controller_pods = classified["controller"]
    analyzer_pods = classified["analyzer"]
    crd_installer_pods = classified["crd_installer"]

    tmm_running = [p for p in tmm_pods if p.get("phase") == "Running"]
    flo_running = [p for p in flo_pods if p.get("phase") == "Running"]
    controller_running = [p for p in controller_pods if p.get("phase") == "Running"]

    install_shape = detect_install_shape(classified)

    # Extract versions + Helm info
    flo_version = _extract_flo_version(flo_pods)
    flo_helm = _find_flo_helm_release(helm_releases)
    tmm_container_info = _tmm_container_summary(tmm_pods)
    # Controller/TMM image tags — the robust version signal on non-FLO
    # (helm/manual) installs where FLO is absent and the Helm release secret
    # carries only a revision, not a chart version (#389).
    controller_version = _extract_image_version(controller_pods, image_hint="f5ingress")
    tmm_version = _extract_image_version(tmm_pods, image_hint="tmm")

    # Parse CNEInstance and VLANs
    cne_info = _parse_cneinstance(cneinstances)
    vlan_info = _parse_vlans(vlans)

    # CRD installer status
    crd_installer_done = any(
        p.get("phase") in ("Succeeded", "Completed") for p in crd_installer_pods
    )

    # Overall status
    status, health = _determine_status(
        tmm_running, flo_running, has_f5_crds, tmm_container_info,
        f5_crds, flo_pods, ns_info,
        controller_running=controller_running,
        cne_instance_present=cne_info is not None,
    )

    return {
        "status": status,
        "health": health,
        "install_shape": install_shape,
        "namespaces": ns_info,
        "crds": {
            "total": len(f5_crds),
            "groups": sorted(f5_crd_groups),
            "has_data_plane": "k8s.f5net.com" in f5_crd_groups,
            "has_flo": "k8s.f5.com" in f5_crd_groups,
            "has_gateway_ext": "gateway.k8s.f5net.com" in f5_crd_groups,
        },
        "flo": {
            "version": flo_version,
            "pods": len(flo_pods),
            "running": len(flo_running),
            "helm_release": flo_helm,
        },
        "tmm": {
            "pods": len(tmm_pods),
            "running": len(tmm_running),
            "containers": tmm_container_info,
            "version": tmm_version,
        },
        "controller": {
            "pods": len(controller_pods),
            "running": len(controller_running),
            "version": controller_version,
        },
        "analyzer": {
            "pods": len(analyzer_pods),
            "running": len([p for p in analyzer_pods if p.get("phase") == "Running"]),
        },
        "crd_installer": {
            "completed": crd_installer_done,
            "pods": len(crd_installer_pods),
        },
        "cne_instance": cne_info,
        "vlans": vlan_info,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_flo_version(flo_pods: list[dict]) -> str | None:
    """Extract FLO version from pod container images."""
    for pod in flo_pods:
        for container in pod.get("containers", []):
            img = container.get("image", "")
            match = re.search(r"f5-lifecycle.*?:v?([\d][\d.-]+)", img)
            if not match:
                match = re.search(r":v?([\d][\d.-]+)", img)
            if match:
                return match.group(1)
    return None


def _extract_image_version(pods: list[dict], image_hint: str | None = None) -> str | None:
    """Extract the image tag (everything after the final ``:``) from a pod's
    container image.

    Used to derive a version on non-FLO installs from the discovered
    controller (f5ingress) / TMM pod images, e.g.
    ``repo.f5.com/images/f5ingress:v14.59.1-0.0.70`` -> ``v14.59.1-0.0.70``.
    The trailing-anchor regex ignores a registry port (``host:5000/img:tag``).
    """
    def _tag(img: str) -> str | None:
        m = re.search(r":(v?\d[\w.\-]*)$", img)
        return m.group(1) if m else None

    # Prefer a container whose image matches the hint (e.g. "f5ingress",
    # "tmm") so a sidecar image doesn't shadow the primary component.
    if image_hint:
        for pod in pods:
            for c in pod.get("containers", []):
                img = c.get("image", "")
                if image_hint in img:
                    tag = _tag(img)
                    if tag:
                        return tag

    # Fall back to the first container carrying a version-like tag.
    for pod in pods:
        for c in pod.get("containers", []):
            tag = _tag(c.get("image", ""))
            if tag:
                return tag
    return None


def _find_flo_helm_release(helm_releases: list[dict]) -> dict | None:
    """Find the FLO (or direct-helm BNK/CNE) Helm release from the releases list.

    Matches the FLO/lifecycle-operator release first, then broadens to any
    release whose name looks like a direct-helm BNK/CNE install (e.g.
    ``f5ingress``, ``cne``, ``bnk``) so non-FLO installs still surface a
    Helm release in the scan.
    """
    release = next(
        (
            r
            for r in helm_releases
            if "f5" in r.get("name", "").lower()
            and "lifecycle" in r.get("name", "").lower()
        ),
        None,
    )
    if not release:
        release = next(
            (
                r
                for r in helm_releases
                if any(
                    token in r.get("name", "").lower()
                    for token in ("f5ingress", "cne")
                )
            ),
            None,
        )
    if not release:
        release = next(
            (r for r in helm_releases if r.get("namespace") == "f5-operator"),
            None,
        )
    return release


def _tmm_container_summary(tmm_pods: list[dict]) -> dict[str, Any] | None:
    """Build TMM container readiness summary from the first TMM pod."""
    if not tmm_pods:
        return None
    containers = tmm_pods[0].get("containers", [])
    ready_count = sum(1 for c in containers if c.get("ready"))
    return {
        "total_containers": len(containers),
        "ready_containers": ready_count,
        "containers": f"{ready_count}/{len(containers)}",
    }


def _parse_cneinstance(cneinstances: list) -> dict[str, Any] | None:
    """Parse the first CNEInstance resource into a feature summary."""
    if not cneinstances:
        return None
    cne = cneinstances[0]
    spec = cne.get("spec", {})
    # Derive feature flags from the live spec rather than a hardcoded key list.
    # Any spec key whose value is a dict containing 'enabled' is a feature flag.
    # This surfaces coreCollection, envDiscovery, and any future additions.
    features = {
        key: bool(val.get("enabled"))
        for key, val in spec.items()
        if isinstance(val, dict) and "enabled" in val
    }
    return {
        "name": cne.get("metadata", {}).get("name", ""),
        "features": features,
    }


def _parse_vlans(vlans: list) -> list[dict[str, Any]]:
    """Parse VLAN resources into interface/IP summaries."""
    result = []
    for v in vlans:
        meta = v.get("metadata", {})
        spec = v.get("spec", {})
        conditions = v.get("status", {}).get("conditions", [])
        programmed = any(
            c.get("type") == "Programmed" and c.get("status") == "True"
            for c in conditions
        )
        result.append(
            {
                "name": meta.get("name", ""),
                "interfaces": spec.get("interfaces", []),
                "self_ips": spec.get("selfip_v4s", []),
                "mtu": spec.get("mtu"),
                "programmed": programmed,
            }
        )
    return result


def _determine_status(
    tmm_running: list,
    flo_running: list,
    has_f5_crds: bool,
    tmm_container_info: dict | None,
    f5_crds: list,
    flo_pods: list,
    ns_info: dict,
    controller_running: list | None = None,
    cne_instance_present: bool = False,
) -> tuple[str, str | None]:
    """Determine overall BNK installation status and health.

    "installed" no longer requires FLO specifically — a direct-helm BNK
    install (TMM + controller + CRDs, no FLO/lifecycle-operator) is a
    legitimate install shape. Any one of FLO, the f5ingress controller, or
    a live CNEInstance is accepted as evidence the data plane is wired up.
    """
    controller_running = controller_running or []
    if tmm_running and has_f5_crds and (flo_running or controller_running or cne_instance_present):
        status = "installed"
        if (
            tmm_container_info
            and tmm_container_info["ready_containers"] == tmm_container_info["total_containers"]
        ):
            health = "healthy"
        else:
            health = "degraded"
    elif has_f5_crds or flo_running or ns_info.get("f5_operator") or ns_info.get("f5_bnk"):
        status = "partial"
        health = "degraded"
    else:
        status = "not_installed"
        health = None
    return status, health
