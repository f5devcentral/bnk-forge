"""
BNK health analysis — builds the health dashboard response.

Analyses platform components (FLO, controller, CRD installer, analyzer),
data plane (TMM pods, CNE features), networking (gateways, VLANs,
listeners), security (firewall policies, iRules), and AI/intelligent LB.

Pure data transformation: takes the dict from ``fetch_all_bnk_data``
and returns a structured health dashboard dict.
"""

from typing import Any

# Components whose absence (zero pods) is not a failure — they're optional or
# one-shot. Centralised here so FE-visible "show" logic and rollup exclusions
# stay in sync.
_OPTIONAL_COMPONENTS: frozenset[str] = frozenset({"analyzer", "crdInstaller"})

from services.bnk.helpers import (
    calc_severity,
    get_condition_message,
    has_condition,
    resource_name,
    rollup_severity,
    safe_get,
)
from services.bnk_pod_discovery import (
    detect_install_shape,
    pod_is_completed,
    pod_is_healthy,
)

# ---------------------------------------------------------------------------
# Component explanations (user-facing)
# ---------------------------------------------------------------------------

COMPONENT_EXPLANATIONS: dict[str, str] = {
    "flo": "Manages network function lifecycle. Without it, no BNK pods can be created or updated.",
    "controller": "Translates Gateway API resources into data-plane configuration. If unhealthy, config changes won't reach TMM.",
    "crdInstaller": "Installs Custom Resource Definitions required by BNK. If incomplete, some resource types may be unavailable.",
    "analyzer": "Runs traffic analysis for intelligent load balancing. If unhealthy, AI-based traffic distribution is unavailable.",
    "tmm": "Processes all network traffic (Traffic Management Microkernel). If unhealthy, traffic routing stops.",
    "gateways": "Kubernetes Gateway API entry point. If unhealthy, no ingress traffic is processed.",
    "vlans": "Provides L2 network segmentation for BNK traffic. If unhealthy, network isolation is broken.",
    "irules": "Programmable traffic steering rules. If not accepted, custom traffic logic won't execute.",
}


# ---------------------------------------------------------------------------
# Internal helpers (promoted from nested functions for testability)
# ---------------------------------------------------------------------------


def _feature_enabled(spec_dict: Any, key: str) -> bool:
    """Check if a CNE feature flag is enabled in a spec dict."""
    if not isinstance(spec_dict, dict):
        return False
    val = spec_dict.get(key)
    if isinstance(val, dict):
        return bool(val.get("enabled"))
    return bool(val)


def _pod_issue_summary(pod: dict) -> str:
    """Return a human-readable issue string for an unhealthy pod."""
    phase = pod.get("phase", "Unknown")
    for c in pod.get("containers", []):
        state = c.get("state", "unknown")
        name = c.get("name", "?")
        if state == "waiting":
            return f"Container '{name}' waiting"
        if state == "terminated":
            return f"Container '{name}' terminated"
        if not c.get("ready"):
            restarts = c.get("restartCount", 0)
            if restarts > 5:
                return f"CrashLoopBackOff — {restarts} restarts"
            return f"Container '{name}' not ready"
    if phase != "Running":
        return f"Pod phase: {phase}"
    return ""


def _pod_details(pod: dict) -> dict[str, Any]:
    """Extract display-friendly details from a pod dict."""
    containers = pod.get("containers", [])
    total_restarts = sum(c.get("restartCount", 0) for c in containers)
    ready_count = sum(1 for c in containers if c.get("ready"))
    return {
        "podName": pod.get("name", ""),
        "namespace": pod.get("namespace", ""),
        "nodeName": pod.get("nodeName"),
        "hostIP": pod.get("hostIP"),
        "phase": pod.get("phase", "Unknown"),
        "restartCount": total_restarts,
        "containersReady": f"{ready_count}/{len(containers)}",
        "issue": _pod_issue_summary(pod),
    }


def _build_component_health(
    component_key: str,
    pods: list[dict],
    healthy_pods: list[dict],
    severity: str,
) -> dict[str, Any]:
    """Build enriched component health with explanation, pod details, and actions."""
    explanation = COMPONENT_EXPLANATIONS.get(component_key, "")
    pod_detail_list = [_pod_details(p) for p in pods]

    # Use set of ids for O(1) membership test instead of O(n) list scan
    healthy_ids = {id(p) for p in healthy_pods}
    unhealthy = [p for p in pods if id(p) not in healthy_ids]

    # Build remediation actions based on severity
    actions: list[dict[str, str]] = []
    target_pod = unhealthy[0] if (severity in ("warning", "critical") and unhealthy) else (pods[0] if pods else None)
    if target_pod:
        pod_name = target_pod.get("name", "")
        pod_ns = target_pod.get("namespace", "")
        actions.append({"label": "View Logs", "action": "view_logs", "target": pod_name, "namespace": pod_ns})
        if severity in ("warning", "critical") and unhealthy:
            actions.append({"label": "Restart Pod", "action": "restart_pod", "target": pod_name, "namespace": pod_ns})
        actions.append({"label": "Describe", "action": "describe", "target": pod_name, "namespace": pod_ns})

    return {
        "explanation": explanation,
        "podDetails": pod_detail_list,
        "remediationActions": actions,
    }


def _derive_spec_feature_flags(spec: dict) -> dict[str, bool]:
    """Derive feature flags from a CNEInstance spec by inspecting live keys.

    A key is treated as a feature flag when its value is a dict that contains
    an 'enabled' sub-key. This is the authoritative derivation — it surfaces
    any feature present in the live spec, including coreCollection and
    envDiscovery which were previously dropped by hardcoded key lists.
    """
    return {
        key: bool(val.get("enabled"))
        for key, val in spec.items()
        if isinstance(val, dict) and "enabled" in val
    }


def _extract_cne_features(cneinstances: list[dict]) -> dict[str, Any]:
    """Extract CNE feature flags from the first CNEInstance (if any)."""
    if not cneinstances:
        return {}
    cne = cneinstances[0]
    spec = cne.get("spec") if isinstance(cne.get("spec"), dict) else {}
    return {
        "name": safe_get(cne, "metadata", "name", default=""),
        **_derive_spec_feature_flags(spec),
    }


# ---------------------------------------------------------------------------
# Section builders (keep analyze_health readable)
# ---------------------------------------------------------------------------


def _crd_installer_severity(
    pods: list[dict],
    job: dict | None,
) -> tuple[str, bool]:
    """
    Determine CRD installer severity and whether it should enter the rollup.

    Three states:
    - Never installed: no Job + no pods → severity "unknown", exclude from rollup.
    - Succeeded (steady state): Job.succeeded > 0 OR pods present and completed →
      severity "healthy", exclude from rollup (one-shot, GC'd pods are normal).
    - Installing (transient): Job.active > 0, succeeded == 0 → severity "unknown"
      (pending), exclude from rollup but mark pending so FE can show a card.
    - Failed: Job.failed > 0, succeeded == 0 → severity "critical", INCLUDE in rollup.

    Returns (severity, include_in_rollup).
    """
    has_completed_pods = any(pod_is_completed(p) for p in pods)
    has_healthy_pods = any(pod_is_healthy(p) for p in pods)

    if job is None:
        # No Job resource exists — truly never installed, or namespace not visible.
        if has_completed_pods or has_healthy_pods:
            # Pods visible without a Job: treat as succeeded (legacy/operator-managed).
            return "healthy", False
        return "unknown", False

    succeeded = job.get("succeeded", 0)
    failed = job.get("failed", 0)
    active = job.get("active", 0)

    if succeeded > 0 or has_completed_pods:
        # Job completed successfully; pods may be GC'd.
        return "healthy", False

    if failed > 0:
        # Job exists but has recorded failures and no success — real failure.
        return "critical", True

    if active > 0:
        # Job is actively running — install in progress.
        return "unknown", False

    # Job exists but all counters are 0 (very brief post-create window or
    # completed before kube controller wrote status). Treat as pending.
    return "unknown", False


def _build_platform_health(
    classified: dict[str, list],
    crd_installer_job: dict | None = None,
    install_shape: str = "unknown",
) -> dict[str, Any]:
    """Build the platform health section (FLO, controller, CRD installer, analyzer)."""
    sections = {
        "flo": ("flo", pod_is_healthy),
        "controller": ("controller", pod_is_healthy),
        "crdInstaller": ("crd_installer", lambda p: pod_is_completed(p) or pod_is_healthy(p)),
        "analyzer": ("analyzer", pod_is_healthy),
    }
    platform: dict[str, Any] = {"severity": "unknown"}
    rollup_inputs: list[str] = []
    for key, (pod_key, health_fn) in sections.items():
        pods = classified[pod_key]
        healthy = [p for p in pods if health_fn(p)]
        count_key = "completed" if key == "crdInstaller" else "running"

        if key == "crdInstaller":
            sev, include_in_rollup = _crd_installer_severity(pods, crd_installer_job)
            platform[key] = {
                "total": len(pods),
                count_key: len(healthy),
                "severity": sev,
                **_build_component_health(key, pods, healthy, sev),
            }
            if include_in_rollup:
                rollup_inputs.append(sev)
            continue

        sev = calc_severity(len(healthy), len(pods))
        platform[key] = {
            "total": len(pods),
            count_key: len(healthy),
            "severity": sev,
            **_build_component_health(key, pods, healthy, sev),
        }

        # Analyzer is optional; its absence shouldn't degrade rollup.
        if key in _OPTIONAL_COMPONENTS and len(pods) == 0:
            continue
        # FLO is optional when the install shape isn't FLO-based (e.g. direct
        # helm install) — a genuinely absent FLO isn't a failure there, mirror
        # the analyzer/crdInstaller optional-when-absent guard above.
        if key == "flo" and install_shape != "flo" and len(pods) == 0:
            continue
        rollup_inputs.append(sev)

    platform["severity"] = rollup_severity(rollup_inputs) if rollup_inputs else "unknown"
    return platform


def _build_data_plane_health(
    classified: dict[str, list],
    cneinstances: list[dict],
) -> dict[str, Any]:
    """Build the data plane health section (TMM pods, CNE features)."""
    tmm_pods = classified["tmm"]
    tmm_running = [p for p in tmm_pods if pod_is_healthy(p)]
    tmm_containers_total = sum(len(p.get("containers", [])) for p in tmm_pods)
    tmm_containers_ready = sum(
        sum(1 for c in p.get("containers", []) if c.get("ready"))
        for p in tmm_pods
    )
    tmm_restarts = sum(
        sum(c.get("restartCount", 0) for c in p.get("containers", []))
        for p in tmm_pods
    )

    tmm_sev = calc_severity(len(tmm_running), len(tmm_pods))
    return {
        "severity": tmm_sev,
        "tmm": {
            "pods": len(tmm_pods),
            "running": len(tmm_running),
            "containersTotal": tmm_containers_total,
            "containersReady": tmm_containers_ready,
            "totalRestarts": tmm_restarts,
            "severity": tmm_sev,
            **_build_component_health("tmm", tmm_pods, tmm_running, tmm_sev),
        },
        "cneInstance": _extract_cne_features(cneinstances),
    }


def _build_networking_health(resources: dict[str, list]) -> dict[str, Any]:
    """Build the networking health section (gateways, VLANs, listeners, routes)."""
    gateways = resources.get("gateway", [])
    vlans = resources.get("f5spkvlan", [])

    gw_programmed = [g for g in gateways if has_condition(g, "Programmed")]
    gw_accepted = [g for g in gateways if has_condition(g, "Accepted")]
    vlans_programmed = [v for v in vlans if has_condition(v, "Programmed")]
    total_listeners = sum(
        len(safe_get(g, "spec", "listeners", default=[]) or [])
        for g in gateways
    )

    gw_sev = calc_severity(len(gw_programmed), len(gateways))
    vlan_sev = calc_severity(len(vlans_programmed), len(vlans))

    networking: dict[str, Any] = {
        "severity": "unknown",
        "gateways": {
            "total": len(gateways),
            "programmed": len(gw_programmed),
            "accepted": len(gw_accepted),
            "severity": gw_sev,
            "explanation": COMPONENT_EXPLANATIONS.get("gateways", ""),
            "addresses": [
                a.get("value", "") if isinstance(a, dict) else str(a)
                for g in gateways
                for a in (safe_get(g, "status", "addresses", default=[]) or [])
            ],
        },
        "vlans": {
            "total": len(vlans),
            "programmed": len(vlans_programmed),
            "severity": vlan_sev,
            "explanation": COMPONENT_EXPLANATIONS.get("vlans", ""),
            "details": [
                {
                    "name": resource_name(v),
                    "programmed": has_condition(v, "Programmed"),
                    "interfaces": safe_get(v, "spec", "interfaces", default=[]) or [],
                    "selfIPs": safe_get(v, "spec", "selfip_v4s", default=[]) or [],
                    "mtu": safe_get(v, "spec", "mtu"),
                }
                for v in vlans
            ],
        },
        "listeners": total_listeners,
        "httpRoutes": len(resources.get("httproute", [])),
        "staticRoutes": len(resources.get("f5spkstaticroute", [])),
        "snatPools": len(resources.get("f5spksnatpool", [])),
    }
    # Only include sub-components that have resources — a component with
    # zero resources reports "unknown" via calc_severity(0, 0), and "unknown"
    # sorts below "healthy" in _SEVERITY_ORDER, so including it would mask
    # a healthy sub-component (e.g. VLANs green + 0 gateways → unknown).
    # The outer rollup already gates on has_networking_resources at the
    # cluster level, so reaching here means at least one networking
    # sub-component is present.
    rollup_inputs: list[str] = []
    if gateways:
        rollup_inputs.append(gw_sev)
    if vlans:
        rollup_inputs.append(vlan_sev)
    networking["severity"] = rollup_severity(rollup_inputs) if rollup_inputs else "healthy"
    return networking


def _build_security_health(resources: dict[str, list]) -> dict[str, Any]:
    """Build the security health section (firewall, iRules, policies)."""
    irules = resources.get("f5bigcneirule", [])
    fwpolicies = resources.get("f5bigfwpolicy", [])
    bnksecpolicies = resources.get("bnksecpolicy", [])
    bnknetpolicies = resources.get("bnknetpolicy", [])

    irules_accepted = [ir for ir in irules if has_condition(ir, "Accepted")]
    irules_programmed = [ir for ir in irules if has_condition(ir, "Programmed")]

    security_h: dict[str, Any] = {
        "severity": "unknown",
        "firewallPolicies": len(fwpolicies),
        "securityPolicies": len(bnksecpolicies),
        "networkPolicies": len(bnknetpolicies),
        "addressLists": len(resources.get("f5bigcneaddresslist", [])),
        "portLists": len(resources.get("f5bigcneportlist", [])),
        "irules": {
            "total": len(irules),
            "accepted": len(irules_accepted),
            "programmed": len(irules_programmed),
            "severity": calc_severity(len(irules_accepted), len(irules)),
            "explanation": COMPONENT_EXPLANATIONS.get("irules", ""),
            "details": [
                {
                    "name": resource_name(ir),
                    "accepted": has_condition(ir, "Accepted"),
                    "programmed": has_condition(ir, "Programmed"),
                    "error": get_condition_message(ir, "Accepted") if not has_condition(ir, "Accepted") else None,
                }
                for ir in irules
            ],
        },
    }
    if irules:
        security_h["severity"] = security_h["irules"]["severity"]
    elif fwpolicies or bnksecpolicies or bnknetpolicies:
        security_h["severity"] = "healthy"

    return security_h


def _build_ai_health(
    resources: dict[str, list],
    _cne_features: dict[str, Any],
) -> dict[str, Any]:
    """Build the AI / intelligent LB health section."""
    analyzers = resources.get("f5biganalyzer", [])
    ai_health: dict[str, Any] = {
        # Informational-only section. Optional AI config presence must not drive severity.
        "severity": "unknown",
        "analyzers": len(analyzers),
        "analyzerDetails": [
            {
                "name": safe_get(a, "metadata", "name", default=""),
                "namespace": safe_get(a, "metadata", "namespace", default=""),
                "schedule": safe_get(a, "spec", "schedule", "every", default=""),
            }
            for a in analyzers
        ],
    }
    return ai_health


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------


def analyze_health(data: dict[str, Any]) -> dict[str, Any]:
    """Build the health dashboard response from raw BNK data."""
    resources = data["resources"]
    classified = data["classified_pods"]
    cneinstances = resources.get("cneinstance", [])
    crd_installer_job: dict | None = data.get("crd_installer_job")
    install_shape = detect_install_shape(classified)

    platform = _build_platform_health(classified, crd_installer_job, install_shape)
    data_plane = _build_data_plane_health(classified, cneinstances)
    networking = _build_networking_health(resources)
    security = _build_security_health(resources)
    cne_features = data_plane["cneInstance"]
    ai = _build_ai_health(resources, cne_features)

    # Readiness-aware overall severity:
    # - Always include platform + data-plane readiness.
    # - Include networking/security only when those resources are present.
    # This keeps optional/absent config from surfacing healthy reachable clusters
    # as "unknown" on Fleet/Command surfaces.
    overall_inputs: list[str] = [platform["severity"], data_plane["severity"]]

    has_networking_resources = any(
        len(resources.get(key, [])) > 0
        for key in ("gateway", "f5spkvlan", "httproute", "f5spkstaticroute", "f5spksnatpool")
    )
    if has_networking_resources:
        overall_inputs.append(networking["severity"])

    has_security_resources = any(
        len(resources.get(key, [])) > 0
        for key in ("f5bigcneirule", "f5bigfwpolicy", "bnksecpolicy", "bnknetpolicy")
    )
    if has_security_resources:
        overall_inputs.append(security["severity"])

    overall = rollup_severity(overall_inputs)

    # Summary counts
    gateways = resources.get("gateway", [])
    tmm_pods = classified["tmm"]
    tmm_running = [p for p in tmm_pods if pod_is_healthy(p)]
    tmm_containers_total = sum(len(p.get("containers", [])) for p in tmm_pods)
    tmm_containers_ready = sum(
        sum(1 for c in p.get("containers", []) if c.get("ready"))
        for p in tmm_pods
    )
    total_listeners = sum(
        len(safe_get(g, "spec", "listeners", default=[]) or [])
        for g in gateways
    )

    return {
        "overall": overall,
        "installShape": install_shape,
        "installMethod": {
            "flo": "FLO deploy flow",
            "helm": "Helm / manual",
            "unknown": "Unknown",
        }[install_shape],
        "platform": platform,
        "dataPlane": data_plane,
        "networking": networking,
        "security": security,
        "ai": ai,
        "counts": {
            "gateways": len(gateways),
            "listeners": total_listeners,
            "httpRoutes": len(resources.get("httproute", [])),
            "vlans": len(resources.get("f5spkvlan", [])),
            "firewallPolicies": len(resources.get("f5bigfwpolicy", [])),
            "irules": len(resources.get("f5bigcneirule", [])),
            "analyzers": len(resources.get("f5biganalyzer", [])),
            "cneInstances": len(cneinstances),
            "tmm_pods": len(tmm_pods),
            "tmm_running": len(tmm_running),
            "tmm_containers": f"{tmm_containers_ready}/{tmm_containers_total}",
        },
    }
