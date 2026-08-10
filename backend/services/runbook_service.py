"""
Runbook Service — pre-built diagnostic sequences for common BNK problems.

UX-010: Each runbook is a sequence of diagnostic steps that check specific
K8s resources and report pass/fail with actionable messages. Runbooks
use the existing KubernetesService to query cluster state.

Three built-in runbooks:
  1. Gateway Not Routing Traffic
  2. BNK Pods Not Starting
  3. BNK Upgrade Failed
"""

import logging
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC
from typing import Any

from kubernetes import client as k8s_client

from services.bnk.helpers import get_condition_message as _get_condition_message
from services.bnk.helpers import has_condition as _has_condition
from services.bnk_pod_discovery import (
    BNK_NAMESPACES,
    classify_f5_pods,
    detect_install_shape,
    discover_f5_pods,
    pod_is_healthy,
)
from services.kubernetes._resources import resolve_resource_type
from services.kubernetes_service import KubernetesService
from services.scanner.bnk_install import _extract_image_version as _first_image_tag

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class RunbookStepResult:
    """Result of executing a single runbook step."""
    step_index: int
    name: str
    status: str  # 'pass' | 'fail' | 'skip'
    message: str
    details: dict[str, Any] | None = None
    resource_link: dict[str, str] | None = None  # e.g. {"type": "pod", "name": "...", "namespace": "..."}


@dataclass
class RunbookStep:
    """Definition of a single diagnostic step."""
    name: str
    description: str
    check_fn_name: str  # name of the check function to call
    pass_message: str
    fail_message: str


@dataclass
class RunbookDefinition:
    """Definition of a complete runbook."""
    id: str
    name: str
    description: str
    category: str
    steps: list[RunbookStep] = field(default_factory=list)


@dataclass
class RunbookExecutionResult:
    """Full result of executing a runbook."""
    runbook_id: str
    runbook_name: str
    status: str  # 'pass' | 'fail' | 'partial'
    steps: list[RunbookStepResult] = field(default_factory=list)
    summary: str = ""


# ---------------------------------------------------------------------------
# Built-in Runbook Definitions
# ---------------------------------------------------------------------------

RUNBOOKS: list[RunbookDefinition] = [
    RunbookDefinition(
        id="gateway-not-routing",
        name="Gateway Not Routing Traffic",
        description="Diagnoses why a BNK gateway isn't processing or routing traffic. Checks gateway status, routes, backends, TMM pods, and listener configuration.",
        category="networking",
        steps=[
            RunbookStep(
                name="Check Gateway Status",
                description="Verify that Gateway resources are Programmed and Accepted",
                check_fn_name="check_gateway_status",
                pass_message="All gateways are Programmed and Accepted",
                fail_message="One or more gateways are not Programmed/Accepted",
            ),
            RunbookStep(
                name="Check HTTPRoutes",
                description="Verify HTTPRoutes are attached to gateways with valid backends",
                check_fn_name="check_httproutes",
                pass_message="HTTPRoutes are attached and have valid backend references",
                fail_message="HTTPRoutes are missing, unattached, or have invalid backends",
            ),
            RunbookStep(
                name="Check Backend Services",
                description="Verify backend Services exist and have healthy endpoints",
                check_fn_name="check_backend_services",
                pass_message="Backend services have healthy endpoints",
                fail_message="Backend services are missing endpoints or have unhealthy pods",
            ),
            RunbookStep(
                name="Check TMM Pods",
                description="Verify TMM (Traffic Management Microkernel) pods are Running and Ready",
                check_fn_name="check_tmm_pods",
                pass_message="All TMM pods are Running and Ready",
                fail_message="TMM pods are not healthy — traffic processing is impacted",
            ),
            RunbookStep(
                name="Check Listeners & VLANs",
                description="Verify gateway listeners are configured and VLANs are programmed",
                check_fn_name="check_listeners_vlans",
                pass_message="Listeners are configured and VLANs are programmed",
                fail_message="Listeners or VLANs are misconfigured",
            ),
        ],
    ),
    RunbookDefinition(
        id="pods-not-starting",
        name="BNK Pods Not Starting",
        description="Diagnoses why BNK pods aren't starting. Checks the FLO operator, namespace existence, image pull secrets, pod events, and node resources.",
        category="platform",
        steps=[
            RunbookStep(
                name="Check FLO Operator",
                description="Verify the FLO lifecycle operator is running",
                check_fn_name="check_flo_operator",
                pass_message="FLO operator is running and healthy",
                fail_message="FLO operator is not running — it manages all BNK pod lifecycles",
            ),
            RunbookStep(
                name="Check BNK Namespace",
                description="Verify BNK namespaces exist (f5-bnk, f5-operator, f5-utils)",
                check_fn_name="check_bnk_namespaces",
                pass_message="All required BNK namespaces exist",
                fail_message="One or more BNK namespaces are missing",
            ),
            RunbookStep(
                name="Check Image Pull Secrets",
                description="Verify image pull secrets are configured in BNK namespaces",
                check_fn_name="check_image_pull_secrets",
                pass_message="Image pull secrets are configured",
                fail_message="Image pull secrets may be missing — pods can't pull F5 container images",
            ),
            RunbookStep(
                name="Check Pod Events",
                description="Look for ImagePullBackOff, CrashLoopBackOff, or other error events",
                check_fn_name="check_pod_events",
                pass_message="No error events found in BNK namespaces",
                fail_message="Error events detected — pods are failing to start",
            ),
            RunbookStep(
                name="Check Node Resources",
                description="Verify nodes have sufficient CPU and memory (no pressure conditions)",
                check_fn_name="check_node_resources",
                pass_message="Nodes have sufficient resources, no pressure conditions",
                fail_message="Node resource pressure detected — pods may be unschedulable",
            ),
        ],
    ),
    RunbookDefinition(
        id="upgrade-failed",
        name="BNK Upgrade Failed",
        description="Diagnoses issues after a BNK upgrade failure. Checks current versions, operator compatibility, stuck pods, PVC status, and recent error events.",
        category="upgrade",
        steps=[
            RunbookStep(
                name="Check BNK Version",
                description="Determine what BNK version is currently running",
                check_fn_name="check_bnk_version",
                pass_message="BNK version identified successfully",
                fail_message="Could not determine running BNK version",
            ),
            RunbookStep(
                name="Check FLO Operator Version",
                description="Verify the FLO operator version and readiness",
                check_fn_name="check_flo_version",
                pass_message="FLO operator is running with identifiable version",
                fail_message="FLO operator version could not be determined or operator is unhealthy",
            ),
            RunbookStep(
                name="Check Pending Pods",
                description="Look for pods stuck in Pending or ContainerCreating state",
                check_fn_name="check_pending_pods",
                pass_message="No stuck pods found",
                fail_message="Pods are stuck in Pending/ContainerCreating — upgrade may be incomplete",
            ),
            RunbookStep(
                name="Check PVC Status",
                description="Verify PersistentVolumeClaims are Bound and storage is available",
                check_fn_name="check_pvc_status",
                pass_message="All PVCs are Bound",
                fail_message="PVCs are not Bound — storage issues may block the upgrade",
            ),
            RunbookStep(
                name="Check Recent Events",
                description="Look for error events in the last 10 minutes across BNK namespaces",
                check_fn_name="check_recent_events",
                pass_message="No recent error events",
                fail_message="Recent error events detected — may indicate upgrade issues",
            ),
        ],
    ),
]


# ---------------------------------------------------------------------------
# Runbook Service
# ---------------------------------------------------------------------------

def list_runbooks() -> list[dict[str, Any]]:
    """Return all available runbook definitions (without check function internals)."""
    return [
        {
            "id": rb.id,
            "name": rb.name,
            "description": rb.description,
            "category": rb.category,
            "step_count": len(rb.steps),
            "steps": [
                {
                    "name": step.name,
                    "description": step.description,
                }
                for step in rb.steps
            ],
        }
        for rb in RUNBOOKS
    ]


def get_runbook(runbook_id: str) -> RunbookDefinition | None:
    """Get a runbook definition by ID."""
    for rb in RUNBOOKS:
        if rb.id == runbook_id:
            return rb
    return None


def execute_runbook(
    k8s_service: KubernetesService,
    cluster_id: int,
    runbook_id: str,
) -> dict[str, Any]:
    """
    Execute a runbook against a cluster.

    Runs each step sequentially, collecting results. A step failure doesn't
    stop execution — all steps run so the user gets a complete picture.
    """
    runbook = get_runbook(runbook_id)
    if not runbook:
        raise ValueError(f"Runbook '{runbook_id}' not found")

    # Load cluster connection once
    cluster = k8s_service.get_cluster(cluster_id)
    api_client = k8s_service.load_kubeconfig(cluster)

    # Build check context
    ctx = _RunbookContext(api_client=api_client, cluster_id=cluster_id, db=k8s_service.db, cluster=cluster)

    results: list[dict[str, Any]] = []
    pass_count = 0
    fail_count = 0

    for idx, step in enumerate(runbook.steps):
        try:
            check_fn = CHECK_FUNCTIONS.get(step.check_fn_name)
            if not check_fn:
                results.append({
                    "step_index": idx,
                    "name": step.name,
                    "description": step.description,
                    "status": "skip",
                    "message": f"Check function '{step.check_fn_name}' not implemented",
                    "details": None,
                    "resource_link": None,
                })
                continue

            passed, message, details, resource_link = check_fn(ctx)
            status = "pass" if passed else "fail"
            display_message = message or (step.pass_message if passed else step.fail_message)

            if passed:
                pass_count += 1
            else:
                fail_count += 1

            results.append({
                "step_index": idx,
                "name": step.name,
                "description": step.description,
                "status": status,
                "message": display_message,
                "details": details,
                "resource_link": resource_link,
            })

        except Exception as e:
            logger.error(f"Runbook step '{step.name}' failed: {e}\n{traceback.format_exc()}")
            fail_count += 1
            results.append({
                "step_index": idx,
                "name": step.name,
                "description": step.description,
                "status": "fail",
                "message": f"Step execution error: {str(e)}",
                "details": None,
                "resource_link": None,
            })

    total = len(runbook.steps)
    if fail_count == 0:
        overall = "pass"
        summary = f"All {total} checks passed"
    elif pass_count == 0:
        overall = "fail"
        summary = f"All {total} checks failed"
    else:
        overall = "partial"
        summary = f"{pass_count} passed, {fail_count} failed out of {total} checks"

    return {
        "runbook_id": runbook.id,
        "runbook_name": runbook.name,
        "status": overall,
        "summary": summary,
        "steps": results,
        "cluster_id": cluster_id,
    }


# ---------------------------------------------------------------------------
# Execution Context
# ---------------------------------------------------------------------------

class _RunbookContext:
    """Holds the K8s API client and cached data for a runbook execution."""

    def __init__(self, api_client: k8s_client.ApiClient, cluster_id: int, db=None, cluster=None):
        self.api_client = api_client
        self.cluster_id = cluster_id
        self.db = db
        self.cluster = cluster
        self._pods_cache: dict | None = None
        self._classified_cache: dict | None = None
        self._namespaces_cache: set[str] | None = None
        self._install_shape_cache: str | None = None

    @property
    def pods(self) -> dict:
        if self._pods_cache is None:
            tenant, utils = discover_f5_pods(self.api_client)
            self._pods_cache = {"tenant": tenant, "utils": utils}
            self._classified_cache = classify_f5_pods(tenant, utils)
        return self._pods_cache

    @property
    def classified(self) -> dict:
        if self._classified_cache is None:
            _ = self.pods  # triggers discovery
        return self._classified_cache  # type: ignore[return-value]

    @property
    def install_shape(self) -> str:
        """
        How BNK was installed: "flo" (Forge deploy-flow), "helm" (direct
        helm/manual, no FLO), or "unknown" (nothing discovered). Mirrors
        the guard already used by services/bnk/health.py and
        routes/k8s/recovery.py so FLO-only checks don't hard-fail on a
        healthy non-FLO install.
        """
        if self._install_shape_cache is None:
            self._install_shape_cache = detect_install_shape(self.classified)
        return self._install_shape_cache

    @property
    def discovered_namespaces(self) -> set[str]:
        """
        Namespaces where BNK pods were *actually found* via live discovery —
        no static hint list included. Used to judge whether BNK is really
        present, as opposed to `namespaces` (below) which is the broader
        scan list for secrets/events/PVC checks.
        """
        return {
            pod_ns
            for pod in self.pods.get("tenant", []) + self.pods.get("utils", [])
            if (pod_ns := pod.get("namespace"))
        }

    @property
    def namespaces(self) -> set[str]:
        """
        Namespaces to inspect for BNK resources (D-019 live discovery).

        Union of: the static BNK_NAMESPACES fallback, the real namespace of
        every discovered BNK pod (helm/manual installs land pods anywhere),
        and the cluster's own discovered/default namespace if available.
        Never narrower than BNK_NAMESPACES — only ever adds to it.
        """
        if self._namespaces_cache is None:
            ns: set[str] = set(BNK_NAMESPACES)

            for pod in self.pods.get("tenant", []) + self.pods.get("utils", []):
                pod_ns = pod.get("namespace")
                if pod_ns:
                    ns.add(pod_ns)

            discovered = getattr(self.cluster, "discovered_namespaces", None)
            if isinstance(discovered, list | tuple | set):
                ns.update(n for n in discovered if isinstance(n, str) and n)

            default_ns = getattr(self.cluster, "default_namespace", None)
            if isinstance(default_ns, str) and default_ns:
                ns.add(default_ns)

            self._namespaces_cache = ns
        return self._namespaces_cache


# ---------------------------------------------------------------------------
# Check Functions — each returns (passed: bool, message: str, details, link)
# ---------------------------------------------------------------------------

CheckResult = tuple[
    bool,
    str,
    dict[str, Any] | None,
    dict[str, str] | None,
]


def _safe_fetch_crd(ctx: _RunbookContext, resource_type_key: str, namespace: str | None = None) -> list[dict]:
    """Safely fetch a CRD type, returning empty list on failure."""
    try:
        rt = resolve_resource_type(ctx.db, ctx.cluster_id, resource_type_key)
        custom_api = k8s_client.CustomObjectsApi(ctx.api_client)
        if namespace:
            resp = custom_api.list_namespaced_custom_object(
                group=rt.api_group, version=rt.api_version,
                namespace=namespace, plural=rt.plural,
            )
        else:
            resp = custom_api.list_cluster_custom_object(
                group=rt.api_group, version=rt.api_version,
                plural=rt.plural,
            )
        return [i for i in resp.get("items", []) if isinstance(i, dict)]
    except Exception as e:
        logger.debug(f"Failed to fetch {resource_type_key}: {e}")
        return []


# ---- Runbook 1: Gateway Not Routing Traffic ----

def _check_gateway_status(ctx: _RunbookContext) -> CheckResult:
    gateways = _safe_fetch_crd(ctx, "gateway")
    if not gateways:
        return False, "No Gateway resources found on the cluster", {"gateway_count": 0}, None

    programmed = [g for g in gateways if _has_condition(g, "Programmed")]
    accepted = [g for g in gateways if _has_condition(g, "Accepted")]

    details = {
        "total": len(gateways),
        "programmed": len(programmed),
        "accepted": len(accepted),
        "gateways": [
            {
                "name": g.get("metadata", {}).get("name", ""),
                "namespace": g.get("metadata", {}).get("namespace", ""),
                "programmed": _has_condition(g, "Programmed"),
                "accepted": _has_condition(g, "Accepted"),
                "message": _get_condition_message(g, "Programmed") or _get_condition_message(g, "Accepted"),
            }
            for g in gateways
        ],
    }

    if len(programmed) == len(gateways) and len(accepted) == len(gateways):
        return True, f"All {len(gateways)} gateways are Programmed and Accepted", details, None

    bad = [g for g in gateways if not _has_condition(g, "Programmed") or not _has_condition(g, "Accepted")]
    first_bad = bad[0] if bad else gateways[0]
    link = {
        "type": "gateway",
        "name": first_bad.get("metadata", {}).get("name", ""),
        "namespace": first_bad.get("metadata", {}).get("namespace", ""),
    }
    return False, f"{len(programmed)}/{len(gateways)} programmed, {len(accepted)}/{len(gateways)} accepted", details, link


def _check_httproutes(ctx: _RunbookContext) -> CheckResult:
    routes = _safe_fetch_crd(ctx, "httproute")
    if not routes:
        return False, "No HTTPRoute resources found — traffic has no routing rules", {"route_count": 0}, None

    issues = []
    route_details = []
    for r in routes:
        name = r.get("metadata", {}).get("name", "")
        ns = r.get("metadata", {}).get("namespace", "")
        spec = r.get("spec", {})
        parent_refs = spec.get("parentRefs", [])
        rules = spec.get("rules", [])

        has_parents = len(parent_refs) > 0
        backend_count = sum(
            len(rule.get("backendRefs", []))
            for rule in rules
        )

        rd = {"name": name, "namespace": ns, "parents": len(parent_refs), "backends": backend_count}
        route_details.append(rd)

        if not has_parents:
            issues.append(f"HTTPRoute '{name}' has no parentRefs (not attached to any gateway)")
        if backend_count == 0:
            issues.append(f"HTTPRoute '{name}' has no backendRefs (no backend services)")

    details = {"total": len(routes), "routes": route_details, "issues": issues}

    if not issues:
        return True, f"{len(routes)} HTTPRoutes attached with valid backends", details, None

    first_route = routes[0]
    link = {
        "type": "httproute",
        "name": first_route.get("metadata", {}).get("name", ""),
        "namespace": first_route.get("metadata", {}).get("namespace", ""),
    }
    return False, "; ".join(issues[:3]), details, link


def _check_backend_services(ctx: _RunbookContext) -> CheckResult:
    """Check that backend services referenced by HTTPRoutes have endpoints."""
    routes = _safe_fetch_crd(ctx, "httproute")
    if not routes:
        return True, "No HTTPRoutes — skipping backend check", None, None

    # Collect unique backend service references
    backend_refs: list[dict[str, str]] = []
    for r in routes:
        ns = r.get("metadata", {}).get("namespace", "default")
        for rule in r.get("spec", {}).get("rules", []):
            for br in rule.get("backendRefs", []):
                svc_name = br.get("name", "")
                if svc_name:
                    backend_refs.append({"name": svc_name, "namespace": ns})

    if not backend_refs:
        return True, "No backend service references found in HTTPRoutes", None, None

    # Check endpoints for each unique service
    core_v1 = k8s_client.CoreV1Api(ctx.api_client)
    issues = []
    svc_details = []
    seen = set()

    for ref in backend_refs:
        key = f"{ref['namespace']}/{ref['name']}"
        if key in seen:
            continue
        seen.add(key)

        try:
            endpoints = core_v1.read_namespaced_endpoints(ref["name"], ref["namespace"])
            addr_count = sum(
                len(subset.addresses or [])
                for subset in (endpoints.subsets or [])
            )
            svc_details.append({"service": key, "endpoints": addr_count})
            if addr_count == 0:
                issues.append(f"Service '{key}' has 0 endpoints")
        except Exception:
            issues.append(f"Service '{key}' not found or inaccessible")
            svc_details.append({"service": key, "endpoints": 0, "error": "not found"})

    details = {"services_checked": len(seen), "services": svc_details, "issues": issues}

    if not issues:
        return True, f"All {len(seen)} backend services have healthy endpoints", details, None

    return False, "; ".join(issues[:3]), details, None


def _check_tmm_pods(ctx: _RunbookContext) -> CheckResult:
    tmm_pods = ctx.classified.get("tmm", [])
    if not tmm_pods:
        return False, "No TMM pods found — no traffic processing is possible", {"tmm_count": 0}, None

    healthy = [p for p in tmm_pods if pod_is_healthy(p)]
    pod_details = []
    for p in tmm_pods:
        containers = p.get("containers", [])
        restarts = sum(c.get("restartCount", 0) for c in containers)
        ready_count = sum(1 for c in containers if c.get("ready"))
        pod_details.append({
            "name": p.get("name", ""),
            "namespace": p.get("namespace", ""),
            "phase": p.get("phase", "Unknown"),
            "ready": f"{ready_count}/{len(containers)}",
            "restarts": restarts,
            "healthy": pod_is_healthy(p),
        })

    details = {"total": len(tmm_pods), "healthy": len(healthy), "pods": pod_details}

    if len(healthy) == len(tmm_pods):
        return True, f"All {len(tmm_pods)} TMM pods are Running and Ready", details, None

    first_bad = next((p for p in tmm_pods if not pod_is_healthy(p)), tmm_pods[0])
    link = {
        "type": "pod",
        "name": first_bad.get("name", ""),
        "namespace": first_bad.get("namespace", ""),
    }
    return (
        False,
        f"{len(healthy)}/{len(tmm_pods)} TMM pods healthy",
        details,
        link,
    )


def _check_listeners_vlans(ctx: _RunbookContext) -> CheckResult:
    gateways = _safe_fetch_crd(ctx, "gateway")
    vlans = _safe_fetch_crd(ctx, "f5spkvlan")

    listener_count = sum(
        len(g.get("spec", {}).get("listeners", []) or [])
        for g in gateways
    )

    vlans_programmed = [v for v in vlans if _has_condition(v, "Programmed")]
    issues = []

    if listener_count == 0 and gateways:
        issues.append("Gateways exist but have no listeners configured")

    if vlans and len(vlans_programmed) < len(vlans):
        unprogrammed = [
            v.get("metadata", {}).get("name", "?")
            for v in vlans
            if not _has_condition(v, "Programmed")
        ]
        issues.append(f"VLANs not programmed: {', '.join(unprogrammed[:3])}")

    details = {
        "listeners": listener_count,
        "gateways": len(gateways),
        "vlans_total": len(vlans),
        "vlans_programmed": len(vlans_programmed),
        "issues": issues,
    }

    if not issues:
        msg = f"{listener_count} listeners configured"
        if vlans:
            msg += f", {len(vlans_programmed)}/{len(vlans)} VLANs programmed"
        return True, msg, details, None

    return False, "; ".join(issues), details, None


# ---- Runbook 2: BNK Pods Not Starting ----

def _check_flo_operator(ctx: _RunbookContext) -> CheckResult:
    flo_pods = ctx.classified.get("flo", [])
    install_shape = ctx.install_shape

    if not flo_pods:
        # Mirrors services/bnk/health.py:258 and routes/k8s/recovery.py — an
        # absent FLO operator is expected on a direct-helm/manual install or
        # when BNK is not yet installed (unknown shape), not a failure.
        if install_shape in ("helm", "unknown"):
            return (
                True,
                "Direct-helm install detected — no FLO operator expected (lifecycle managed by helm/controller)",
                {"flo_count": 0, "install_shape": install_shape},
                None,
            )
        return (
            False,
            "No FLO operator pods found — BNK cannot manage pod lifecycles",
            {"flo_count": 0, "install_shape": install_shape},
            None,
        )

    healthy = [p for p in flo_pods if pod_is_healthy(p)]
    pod_details = [
        {
            "name": p.get("name", ""),
            "namespace": p.get("namespace", ""),
            "phase": p.get("phase", "Unknown"),
            "healthy": pod_is_healthy(p),
        }
        for p in flo_pods
    ]

    details = {"total": len(flo_pods), "healthy": len(healthy), "pods": pod_details}

    if len(healthy) == len(flo_pods):
        return True, f"FLO operator running ({len(healthy)} pod(s) healthy)", details, None

    first_bad = next((p for p in flo_pods if not pod_is_healthy(p)), flo_pods[0])
    link = {
        "type": "pod",
        "name": first_bad.get("name", ""),
        "namespace": first_bad.get("namespace", ""),
    }
    return (
        False,
        f"FLO operator unhealthy: {len(healthy)}/{len(flo_pods)} pods ready",
        details,
        link,
    )


def _check_bnk_namespaces(ctx: _RunbookContext) -> CheckResult:
    """
    Verify BNK's namespace(s) exist.

    The static BNK_NAMESPACES tuple (f5-bnk/f5-operator/f5-utils/default) is
    only a hint — a direct-helm/manual install commonly lives in a
    non-standard namespace (e.g. "bnk-app1") and won't have any of those.
    Only fail if we can't find BNK running anywhere: i.e. none of the
    hint namespaces exist AND live pod discovery found nothing either.
    """
    core_v1 = k8s_client.CoreV1Api(ctx.api_client)
    missing = []
    found = []

    for ns_name in ctx.namespaces:
        try:
            core_v1.read_namespace(ns_name)
            found.append(ns_name)
        except Exception:
            missing.append(ns_name)

    discovered = ctx.discovered_namespaces
    details = {"found": found, "missing": missing, "discovered_from_pods": sorted(discovered)}

    if not missing:
        return True, f"All BNK namespaces exist: {', '.join(found)}", details, None

    if discovered:
        return (
            True,
            f"BNK pods discovered in namespace(s): {', '.join(sorted(discovered))} "
            f"(hint namespaces not present: {', '.join(missing)})",
            details,
            None,
        )

    return (
        False,
        f"Missing namespaces: {', '.join(missing)}",
        details,
        None,
    )


def _check_image_pull_secrets(ctx: _RunbookContext) -> CheckResult:
    # Only inspect namespaces where BNK pods actually run (live discovery) —
    # NOT the static BNK_NAMESPACES hints, which are a discovery seed. Scanning
    # the hints would false-fail every non-standard install on "default"/missing
    # f5-* namespaces that legitimately hold no BNK pull secrets (issue #391).
    discovered = ctx.discovered_namespaces
    core_v1 = k8s_client.CoreV1Api(ctx.api_client)
    ns_with_secrets = []
    ns_without_secrets = []

    if not discovered:
        return (
            True,
            "No BNK pods discovered — no namespaces to check for pull secrets",
            {"with_secrets": [], "without_secrets": []},
            None,
        )

    for ns_name in discovered:
        try:
            secrets = core_v1.list_namespaced_secret(ns_name)
            pull_secrets = [
                s for s in secrets.items
                if s.type == "kubernetes.io/dockerconfigjson"
            ]
            if pull_secrets:
                ns_with_secrets.append({
                    "namespace": ns_name,
                    "secrets": [s.metadata.name for s in pull_secrets],
                })
            else:
                ns_without_secrets.append(ns_name)
        except Exception:
            ns_without_secrets.append(ns_name)

    details = {"with_secrets": ns_with_secrets, "without_secrets": ns_without_secrets}

    if not ns_without_secrets:
        return True, "Image pull secrets configured in all BNK namespaces", details, None

    return (
        False,
        f"No image pull secrets in: {', '.join(ns_without_secrets)}",
        details,
        None,
    )


def _check_pod_events(ctx: _RunbookContext) -> CheckResult:
    core_v1 = k8s_client.CoreV1Api(ctx.api_client)
    error_events = []

    if not ctx.discovered_namespaces:
        return (
            False,
            "No BNK namespaces discovered — cannot check pod events",
            {"error_event_count": 0, "events": []},
            None,
        )

    error_reasons = {
        "ImagePullBackOff", "ErrImagePull", "CrashLoopBackOff",
        "RunContainerError", "CreateContainerError", "OOMKilled",
        "FailedScheduling", "FailedMount",
    }

    # Scan only the live-discovered BNK namespaces, not the static hints (#391).
    for ns_name in ctx.discovered_namespaces:
        try:
            events = core_v1.list_namespaced_event(ns_name)
            for ev in events.items:
                if ev.reason in error_reasons or ev.type == "Warning":
                    error_events.append({
                        "namespace": ns_name,
                        "reason": ev.reason,
                        "message": (ev.message or "")[:200],
                        "involved_object": ev.involved_object.name if ev.involved_object else "",
                        "count": ev.count,
                        "last_seen": ev.last_timestamp.isoformat() if ev.last_timestamp else "",
                    })
        except Exception:
            pass

    # Sort by last seen, take top 10
    error_events.sort(key=lambda e: e.get("last_seen", ""), reverse=True)
    top_events = error_events[:10]

    details = {"error_event_count": len(error_events), "events": top_events}

    if not error_events:
        return True, "No error events in BNK namespaces", details, None

    # Summarize the top issues
    reasons = {}
    for ev in error_events:
        r = ev.get("reason", "Unknown")
        reasons[r] = reasons.get(r, 0) + 1

    summary_parts = [f"{count}x {reason}" for reason, count in sorted(reasons.items(), key=lambda x: -x[1])[:3]]

    first_pod = next((e for e in error_events if e.get("involved_object")), None)
    link = None
    if first_pod:
        link = {
            "type": "pod",
            "name": first_pod["involved_object"],
            "namespace": first_pod["namespace"],
        }

    return (
        False,
        f"{len(error_events)} error events: {', '.join(summary_parts)}",
        details,
        link,
    )


def _check_node_resources(ctx: _RunbookContext) -> CheckResult:
    core_v1 = k8s_client.CoreV1Api(ctx.api_client)

    try:
        nodes = core_v1.list_node()
    except Exception as e:
        return False, f"Could not list nodes: {e}", None, None

    pressure_nodes = []
    pressure_conditions = {"MemoryPressure", "DiskPressure", "PIDPressure"}

    for node in nodes.items:
        node_name = node.metadata.name
        for cond in (node.status.conditions or []):
            if cond.type in pressure_conditions and cond.status == "True":
                pressure_nodes.append({
                    "node": node_name,
                    "condition": cond.type,
                    "message": cond.message,
                })

    details = {
        "node_count": len(nodes.items),
        "pressure_nodes": pressure_nodes,
    }

    if not pressure_nodes:
        return True, f"All {len(nodes.items)} nodes have sufficient resources", details, None

    summary = ", ".join(f"{p['node']}: {p['condition']}" for p in pressure_nodes[:3])
    return False, f"Resource pressure: {summary}", details, None


# ---- Runbook 3: BNK Upgrade Failed ----

def _check_bnk_version(ctx: _RunbookContext) -> CheckResult:
    """Determine what BNK version is running by checking CRD labels and FLO pods.

    On a direct-helm/manual install (no FLO), fall back to the TMM/controller
    container image tags instead of hard-failing on FLO absence.
    """
    flo_pods = ctx.classified.get("flo", [])
    install_shape = ctx.install_shape

    version = _first_image_tag(flo_pods)
    version_source = "flo" if version else None

    if not version and install_shape != "flo":
        # Prefer the primary component image (controller=f5ingress, tmm) over
        # sidecars (license-helper, fluentbit, pod-manager) sharing the pod.
        for pod_key, image_hint in (("controller", "f5ingress"), ("tmm", "tmm")):
            version = _first_image_tag(ctx.classified.get(pod_key, []), image_hint=image_hint)
            if version:
                version_source = pod_key
                break

    cne_instances = _safe_fetch_crd(ctx, "cneinstance")
    cne_version = None
    if cne_instances:
        cne = cne_instances[0]
        cne_version = cne.get("status", {}).get("phase", "")

    details = {
        "flo_image_version": version if version_source == "flo" else None,
        "detected_version": version,
        "version_source": version_source,
        "cne_status": cne_version,
        "flo_pod_count": len(flo_pods),
        "install_shape": install_shape,
    }

    if version:
        return True, f"BNK version detected: {version}", details, None

    other_pods_found = install_shape != "flo" and (
        ctx.classified.get("controller") or ctx.classified.get("tmm")
    )
    if flo_pods or other_pods_found:
        return True, "BNK pods found but version tag not parseable", details, None

    if install_shape != "flo":
        return True, "No BNK pods found on this cluster yet — nothing to version", details, None

    return False, "No FLO pods found — cannot determine BNK version", details, None


def _check_flo_version(ctx: _RunbookContext) -> CheckResult:
    flo_pods = ctx.classified.get("flo", [])
    install_shape = ctx.install_shape

    if not flo_pods:
        if install_shape != "flo":
            return (
                True,
                "Direct-helm install detected — no FLO operator to version-check",
                {"flo_count": 0, "install_shape": install_shape},
                None,
            )
        return False, "No FLO operator pods found", {"flo_count": 0, "install_shape": install_shape}, None

    healthy = [p for p in flo_pods if pod_is_healthy(p)]
    images = set()
    for p in flo_pods:
        for c in p.get("containers", []):
            img = c.get("image", "")
            if img:
                images.add(img)

    details = {
        "total": len(flo_pods),
        "healthy": len(healthy),
        "images": list(images),
    }

    if len(healthy) == len(flo_pods):
        img_str = ", ".join(images) if images else "unknown"
        return True, f"FLO operator healthy, images: {img_str}", details, None

    first_bad = next((p for p in flo_pods if not pod_is_healthy(p)), flo_pods[0])
    link = {
        "type": "pod",
        "name": first_bad.get("name", ""),
        "namespace": first_bad.get("namespace", ""),
    }
    return (
        False,
        f"FLO operator unhealthy: {len(healthy)}/{len(flo_pods)} ready",
        details,
        link,
    )


def _check_pending_pods(ctx: _RunbookContext) -> CheckResult:
    all_pods = ctx.pods.get("tenant", []) + ctx.pods.get("utils", [])
    stuck = []
    stuck_statuses = {"Pending", "ContainerCreating", "Init:CrashLoopBackOff", "Init:Error"}

    for p in all_pods:
        phase = p.get("phase", "")
        if phase in stuck_statuses or phase == "Pending":
            stuck.append({
                "name": p.get("name", ""),
                "namespace": p.get("namespace", ""),
                "phase": phase,
            })

    # Also check containers in waiting state
    for p in all_pods:
        if p.get("phase") == "Running":
            for c in p.get("containers", []):
                state = c.get("state", "")
                if state == "waiting":
                    stuck.append({
                        "name": p.get("name", ""),
                        "namespace": p.get("namespace", ""),
                        "phase": f"Container '{c.get('name', '?')}' waiting",
                    })

    details = {"stuck_count": len(stuck), "stuck_pods": stuck[:10]}

    if not stuck:
        return True, f"No stuck pods among {len(all_pods)} BNK pods", details, None

    first = stuck[0]
    link = {"type": "pod", "name": first["name"], "namespace": first["namespace"]}
    return (
        False,
        f"{len(stuck)} pods stuck: {', '.join(p['name'] for p in stuck[:3])}",
        details,
        link,
    )


def _check_pvc_status(ctx: _RunbookContext) -> CheckResult:
    core_v1 = k8s_client.CoreV1Api(ctx.api_client)
    unbound = []
    total = 0

    # Scan only the live-discovered BNK namespaces, not the static hints (#391).
    for ns_name in ctx.discovered_namespaces:
        try:
            pvcs = core_v1.list_namespaced_persistent_volume_claim(ns_name)
            for pvc in pvcs.items:
                total += 1
                if pvc.status.phase != "Bound":
                    unbound.append({
                        "name": pvc.metadata.name,
                        "namespace": ns_name,
                        "phase": pvc.status.phase,
                    })
        except Exception:
            pass

    details = {"total_pvcs": total, "unbound": unbound}

    if total == 0:
        return True, "No PVCs in BNK namespaces (none required)", details, None

    if not unbound:
        return True, f"All {total} PVCs are Bound", details, None

    return (
        False,
        f"{len(unbound)} PVCs not Bound: {', '.join(p['name'] for p in unbound[:3])}",
        details,
        None,
    )


def _check_recent_events(ctx: _RunbookContext) -> CheckResult:
    core_v1 = k8s_client.CoreV1Api(ctx.api_client)
    from datetime import datetime, timedelta

    cutoff = datetime.now(UTC) - timedelta(minutes=10)
    recent_errors = []

    if not ctx.discovered_namespaces:
        return (
            False,
            "No BNK namespaces discovered — cannot check recent events",
            {"count": 0, "events": []},
            None,
        )

    # Scan only the live-discovered BNK namespaces, not the static hints (#391).
    for ns_name in ctx.discovered_namespaces:
        try:
            events = core_v1.list_namespaced_event(ns_name)
            for ev in events.items:
                ts = ev.last_timestamp or ev.event_time
                if ts and ev.type == "Warning":
                    # Handle both timezone-aware and naive datetimes
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=UTC)
                    if ts >= cutoff:
                        recent_errors.append({
                            "namespace": ns_name,
                            "reason": ev.reason,
                            "message": (ev.message or "")[:200],
                            "object": ev.involved_object.name if ev.involved_object else "",
                            "time": ts.isoformat(),
                        })
        except Exception:
            pass

    recent_errors.sort(key=lambda e: e.get("time", ""), reverse=True)
    details = {"count": len(recent_errors), "events": recent_errors[:10]}

    if not recent_errors:
        return True, "No warning events in the last 10 minutes", details, None

    reasons = {}
    for ev in recent_errors:
        r = ev.get("reason", "Unknown")
        reasons[r] = reasons.get(r, 0) + 1

    summary_parts = [f"{count}x {reason}" for reason, count in sorted(reasons.items(), key=lambda x: -x[1])[:3]]
    return (
        False,
        f"{len(recent_errors)} recent warnings: {', '.join(summary_parts)}",
        details,
        None,
    )


# ---------------------------------------------------------------------------
# Check Function Registry
# ---------------------------------------------------------------------------

CHECK_FUNCTIONS: dict[str, Callable[[_RunbookContext], CheckResult]] = {
    # Runbook 1: Gateway Not Routing Traffic
    "check_gateway_status": _check_gateway_status,
    "check_httproutes": _check_httproutes,
    "check_backend_services": _check_backend_services,
    "check_tmm_pods": _check_tmm_pods,
    "check_listeners_vlans": _check_listeners_vlans,
    # Runbook 2: BNK Pods Not Starting
    "check_flo_operator": _check_flo_operator,
    "check_bnk_namespaces": _check_bnk_namespaces,
    "check_image_pull_secrets": _check_image_pull_secrets,
    "check_pod_events": _check_pod_events,
    "check_node_resources": _check_node_resources,
    # Runbook 3: BNK Upgrade Failed
    "check_bnk_version": _check_bnk_version,
    "check_flo_version": _check_flo_version,
    "check_pending_pods": _check_pending_pods,
    "check_pvc_status": _check_pvc_status,
    "check_recent_events": _check_recent_events,
}
