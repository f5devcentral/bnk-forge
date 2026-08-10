"""
Adaptive Module Selector

Takes cluster scan results and produces a customized deployment plan:
  - Which modules to deploy, skip, or investigate
  - Pre-filled variable overrides based on detected cluster state
  - Ordered module list respecting dependencies
  - Warnings and blockers that prevent safe deployment

Usage:
    scan_data = scanner.scan(cluster_id)
    selector = AdaptiveModuleSelector(scan_data)

    # Get plan for BNK stack
    plan = selector.plan_for_template("f5-bnk-2.2")

    # Or get plan for any list of module paths
    plan = selector.plan_for_modules(["k8s/bnk-prerequisites", "k8s/cert-manager", ...])
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ModuleAction:
    """Decision for a single module in the deployment plan."""

    path: str                       # Module path, e.g., "k8s/cert-manager"
    name: str                       # Human-readable name
    action: str                     # "deploy" | "skip" | "upgrade" | "investigate" | "blocked"
    reason: str                     # Why this action was chosen
    confidence: str                 # "high" | "medium" | "low"
    required: bool = True           # Is this module required for the stack?
    order: int = 0                  # Deployment order (lower = earlier)

    # Pre-filled variables from scan data
    variable_overrides: dict[str, Any] = field(default_factory=dict)

    # Warnings that don't block but should be shown
    warnings: list[str] = field(default_factory=list)

    # Blockers that must be resolved before deploying
    blockers: list[str] = field(default_factory=list)

    # Scan data that informed this decision
    detected_info: dict[str, Any] = field(default_factory=dict)


@dataclass
class DeploymentPlan:
    """Complete deployment plan for a set of modules on a scanned cluster."""

    cluster_id: int
    cluster_name: str
    template_slug: str | None = None
    template_name: str | None = None

    # Module decisions
    modules: list[ModuleAction] = field(default_factory=list)

    # Summary counts
    deploy_count: int = 0
    skip_count: int = 0
    investigate_count: int = 0
    blocked_count: int = 0

    # Global blockers (prevent the entire deployment)
    global_blockers: list[str] = field(default_factory=list)

    # Global warnings
    global_warnings: list[str] = field(default_factory=list)

    # Pre-filled project-level variables from scan data
    suggested_variables: dict[str, Any] = field(default_factory=dict)

    # Ready to deploy? (no blockers)
    is_ready: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for API response."""
        return {
            "cluster_id": self.cluster_id,
            "cluster_name": self.cluster_name,
            "template_slug": self.template_slug,
            "template_name": self.template_name,
            "modules": [
                {
                    "path": m.path,
                    "name": m.name,
                    "action": m.action,
                    "reason": m.reason,
                    "confidence": m.confidence,
                    "required": m.required,
                    "order": m.order,
                    "variable_overrides": m.variable_overrides,
                    "warnings": m.warnings,
                    "blockers": m.blockers,
                    "detected_info": m.detected_info,
                }
                for m in self.modules
            ],
            "summary": {
                "deploy": self.deploy_count,
                "skip": self.skip_count,
                "investigate": self.investigate_count,
                "blocked": self.blocked_count,
                "total": len(self.modules),
            },
            "global_blockers": self.global_blockers,
            "global_warnings": self.global_warnings,
            "suggested_variables": self.suggested_variables,
            "is_ready": self.is_ready,
        }


# ---------------------------------------------------------------------------
# Stack template definitions — loaded from data/template_modules.json
# ---------------------------------------------------------------------------

def _load_template_modules() -> dict[str, list[dict[str, Any]]]:
    """Load template module definitions from JSON data file."""
    json_path = Path(__file__).parent.parent / "data" / "template_modules.json"
    with open(json_path) as f:
        return json.load(f)


TEMPLATE_MODULES: dict[str, list[dict[str, Any]]] = _load_template_modules()


# ---------------------------------------------------------------------------
# Selector
# ---------------------------------------------------------------------------

class AdaptiveModuleSelector:
    """
    Analyzes cluster scan results and produces customized deployment plans.

    The selector maps scan findings to module decisions:
      - cert-manager detected → skip k8s/cert-manager (or upgrade if version too old)
      - Multus missing → block network-setup (or warn that it needs manual install)
      - BNK already installed → skip all BNK modules (or offer upgrade path)
      - SR-IOV detected → pre-fill NAD resource names from node allocatable
      - HugePages detected → pre-fill MTU settings
    """

    def __init__(self, scan_data: dict[str, Any]):
        self.scan = scan_data
        self.cluster_id = scan_data.get("cluster_id", 0)
        self.cluster_name = scan_data.get("cluster_name", "Unknown")
        self.cluster_info = scan_data.get("cluster_info", {})
        self.prerequisites = scan_data.get("prerequisites", {})
        self.bnk_install = scan_data.get("bnk_install", {})
        self.platform_context = scan_data.get("platform_context") or {}

        # Extract commonly used facts
        self.cert_manager = self.prerequisites.get("cert_manager", {})
        self.multus = self.prerequisites.get("multus", {})
        self.sriov = self.prerequisites.get("sriov", {})
        self.hugepages = self.prerequisites.get("hugepages", {})
        self.storage = self.prerequisites.get("storage", {})
        self.gateway_api = self.prerequisites.get("gateway_api", {})

        self.distribution = self.cluster_info.get("distribution", "generic")
        self.detected_platform_profile = self.platform_context.get("detected_platform_profile", "unknown")
        self.hp_nodes = self.cluster_info.get("hp_nodes", 0)
        self.bnk_status = self.bnk_install.get("status", "not_installed")

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def plan_for_template(self, template_slug: str, sizing_profile: str | None = None) -> DeploymentPlan:
        """
        Produce a deployment plan for a known stack template.

        ``sizing_profile="lab"`` (issue #387 part C) merges the field-validated
        lab f5-tmm resource overrides into ``suggested_variables`` and attaches
        the NON-PRODUCTION warning. Unset by default — no change in behavior.

        Returns a DeploymentPlan with per-module decisions.
        """
        modules = TEMPLATE_MODULES.get(template_slug)
        if not modules:
            raise ValueError(f"Unknown template: {template_slug}. Known: {list(TEMPLATE_MODULES.keys())}")

        plan = DeploymentPlan(
            cluster_id=self.cluster_id,
            cluster_name=self.cluster_name,
            template_slug=template_slug,
            template_name=self._template_display_name(template_slug),
        )

        # Check global blockers
        self._check_global_blockers(plan, template_slug)

        # Analyze each module
        for mod_def in modules:
            action = self._analyze_module(mod_def, template_slug)
            plan.modules.append(action)

        # Extract suggested project-level variables
        plan.suggested_variables = self._extract_suggested_variables(template_slug)

        if sizing_profile == "lab":
            self._apply_lab_sizing_profile(plan)

        # Compute summary
        plan.deploy_count = sum(1 for m in plan.modules if m.action == "deploy")
        plan.skip_count = sum(1 for m in plan.modules if m.action == "skip")
        plan.investigate_count = sum(1 for m in plan.modules if m.action == "investigate")
        plan.blocked_count = sum(1 for m in plan.modules if m.action == "blocked")
        plan.is_ready = len(plan.global_blockers) == 0 and plan.blocked_count == 0

        return plan

    def plan_for_modules(self, module_paths: list[str]) -> DeploymentPlan:
        """
        Produce a deployment plan for an arbitrary list of module paths.
        """
        plan = DeploymentPlan(
            cluster_id=self.cluster_id,
            cluster_name=self.cluster_name,
        )

        for i, path in enumerate(module_paths):
            mod_def = {
                "path": path,
                "name": self._module_display_name(path),
                "required": True,
                "order": i + 1,
            }
            action = self._analyze_module(mod_def, None)
            plan.modules.append(action)

        plan.deploy_count = sum(1 for m in plan.modules if m.action == "deploy")
        plan.skip_count = sum(1 for m in plan.modules if m.action == "skip")
        plan.investigate_count = sum(1 for m in plan.modules if m.action == "investigate")
        plan.blocked_count = sum(1 for m in plan.modules if m.action == "blocked")
        plan.is_ready = plan.blocked_count == 0

        return plan

    # -----------------------------------------------------------------------
    # Global blockers
    # -----------------------------------------------------------------------

    def _check_global_blockers(self, plan: DeploymentPlan, template_slug: str):
        """Check for conditions that block the entire deployment."""

        # No nodes ready
        if self.cluster_info.get("nodes_ready", 0) == 0:
            plan.global_blockers.append(
                "No ready nodes detected. Ensure the cluster is healthy before deploying."
            )

        if template_slug in ("f5-bnk-2.2", "f5-bnk-2.3", "eks-bnk-smartllm-demo"):
            # BNK stack needs Multus
            if self.multus.get("status") == "missing":
                plan.global_warnings.append(self._multus_missing_warning())

        if template_slug in ("f5-bnk-2.2", "f5-bnk-2.3"):
            # BNK needs SR-IOV (or at least VFs)
            if self.sriov.get("status") == "missing":
                plan.global_warnings.append(
                    "No SR-IOV device plugin or virtual functions detected. BNK requires SR-IOV "
                    "for high-performance data-plane networking. Ensure SR-IOV is configured on "
                    "at least one node."
                )

            # BNK needs HP nodes
            if self.hp_nodes == 0:
                plan.global_warnings.append(
                    "No high-performance nodes (label app=f5-tmm) detected. BNK TMM requires "
                    "dedicated HP nodes with SR-IOV and HugePages."
                )

        if template_slug == "eks-bnk-smartllm-demo":
            # GenAI Gateway solution needs BNK to be installed
            if self.bnk_status == "not_installed":
                plan.global_blockers.append(
                    "F5 BNK is not installed on this cluster. Deploy the F5 BNK 2.2 blueprint first."
                )
            elif self.bnk_status == "partial":
                plan.global_warnings.append(
                    "F5 BNK is partially installed. Some solution features may not work correctly."
                )

    # -----------------------------------------------------------------------
    # Per-module analysis
    # -----------------------------------------------------------------------

    def _analyze_module(self, mod_def: dict[str, Any], template_slug: str | None) -> ModuleAction:
        """Analyze a single module against scan results."""

        path = mod_def["path"]
        name = mod_def.get("name", self._module_display_name(path))
        required = mod_def.get("required", True)
        order = mod_def.get("order", 0)

        # Dispatch to path-specific analyzers
        # _MODULE_ANALYZERS stores unbound functions, so pass self explicitly.
        # For the fallback (_analyze_generic), call directly as a bound method.
        unbound_analyzer = self._MODULE_ANALYZERS.get(path)
        if unbound_analyzer:
            return unbound_analyzer(self, path, name, required, order, template_slug)
        return self._analyze_generic(path, name, required, order, template_slug)

    # --- k8s/bnk-prerequisites ---

    def _analyze_bnk_prerequisites(
        self, path: str, name: str, required: bool, order: int, template_slug: str | None
    ) -> ModuleAction:
        has_ns = self.bnk_install.get("namespaces", {})
        f5_op = has_ns.get("f5_operator", False)
        f5_utils = has_ns.get("f5_utils", False)

        if self.bnk_status == "installed" and f5_op and f5_utils:
            return ModuleAction(
                path=path, name=name, action="skip", required=required, order=order,
                confidence="high",
                reason="BNK namespaces and secrets already exist. Re-deploying would be safe but unnecessary.",
                detected_info={"f5_operator_ns": f5_op, "f5_utils_ns": f5_utils},
            )
        elif f5_op or f5_utils:
            return ModuleAction(
                path=path, name=name, action="investigate", required=required, order=order,
                confidence="medium",
                reason="Some F5 namespaces exist but BNK is not fully installed. Deploying is safe (idempotent).",
                detected_info={"f5_operator_ns": f5_op, "f5_utils_ns": f5_utils},
                warnings=["Existing namespaces will be preserved; secrets will be updated."],
            )
        else:
            return ModuleAction(
                path=path, name=name, action="deploy", required=required, order=order,
                confidence="high",
                reason="No F5 namespaces detected. This module creates them along with pull secrets.",
            )

    # --- k8s/network-setup ---

    def _analyze_network_setup(
        self, path: str, name: str, required: bool, order: int, template_slug: str | None
    ) -> ModuleAction:
        multus_status = self.multus.get("status", "missing")
        sriov_status = self.sriov.get("status", "missing")

        overrides: dict[str, Any] = {}
        warnings: list[str] = []
        blockers: list[str] = []
        detected: dict[str, Any] = {}

        # Check if NADs already exist (BNK is installed and running)
        if self.bnk_status == "installed":
            return ModuleAction(
                path=path, name=name, action="skip", required=required, order=order,
                confidence="high",
                reason="BNK is already installed with network attachments configured.",
                detected_info={"bnk_status": "installed"},
            )

        if multus_status == "missing":
            blockers.append(
                "Multus CNI not detected — NetworkAttachmentDefinitions require Multus."
            )

        # Pre-fill SR-IOV resource names from node allocatable
        if sriov_status in ("detected", "partial"):
            sriov_details = self.sriov.get("node_details", [])
            if sriov_details:
                # Collect all SR-IOV resource names
                resource_names = set()
                for node in sriov_details:
                    for res_name in node.get("resources", {}).keys():
                        resource_names.add(res_name)
                detected["sriov_resources"] = sorted(resource_names)

                # Try to identify external/internal by naming convention
                external_res = None
                internal_res = None
                for rn in sorted(resource_names):
                    lower = rn.lower()
                    if "external" in lower or "ext" in lower:
                        external_res = rn
                    elif "internal" in lower or "int" in lower:
                        internal_res = rn

                # If exact match not found, use first two alphabetically
                res_list = sorted(resource_names)
                if not external_res and len(res_list) >= 1:
                    external_res = res_list[0]
                if not internal_res and len(res_list) >= 2:
                    internal_res = res_list[1]

                if external_res:
                    overrides["external_resource_name"] = external_res
                if internal_res:
                    overrides["internal_resource_name"] = internal_res

                detected["total_vfs"] = self.sriov.get("total_vfs", 0)
        else:
            warnings.append(
                "No SR-IOV virtual functions detected. You'll need to configure resource names manually."
            )

        # Distribution-based CNI type
        if self.distribution == "EKS":
            overrides["cni_type"] = "host-device"
        elif self.distribution in ("AKS", "GKE"):
            overrides["cni_type"] = "host-device"
        # DPU environments would use "sf" but we don't auto-detect that

        action = "blocked" if blockers else "deploy"
        confidence = "low" if blockers else ("medium" if warnings else "high")

        return ModuleAction(
            path=path, name=name, action=action, required=required, order=order,
            confidence=confidence,
            reason=(
                "Multus not detected — NADs require Multus CNI."
                if blockers else
                "Network attachments needed for TMM data-plane interfaces."
            ),
            variable_overrides=overrides,
            warnings=warnings,
            blockers=blockers,
            detected_info=detected,
        )

    # --- k8s/cert-manager ---

    def _analyze_cert_manager(
        self, path: str, name: str, required: bool, order: int, template_slug: str | None
    ) -> ModuleAction:
        status = self.cert_manager.get("status", "missing")
        version = self.cert_manager.get("version")
        pods = self.cert_manager.get("pods", {})
        total_running = pods.get("total_running", 0)

        detected = {
            "status": status,
            "version": version,
            "running_pods": total_running,
        }

        if status == "detected" and total_running >= 3:
            # Fully operational cert-manager — skip
            return ModuleAction(
                path=path, name=name, action="skip", required=required, order=order,
                confidence="high",
                reason=f"cert-manager v{version or 'unknown'} is fully operational ({total_running} pods running).",
                detected_info=detected,
            )
        elif status == "detected":
            # CRDs present but not all pods running
            return ModuleAction(
                path=path, name=name, action="investigate", required=required, order=order,
                confidence="medium",
                reason=f"cert-manager v{version or 'unknown'} detected but only {total_running} pod(s) running (expected 3+).",
                detected_info=detected,
                warnings=["cert-manager may be degraded. Check pods in cert-manager namespace."],
            )
        elif status == "partial":
            return ModuleAction(
                path=path, name=name, action="investigate", required=required, order=order,
                confidence="medium",
                reason="cert-manager CRDs found but pods are not running. May need reinstallation.",
                detected_info=detected,
                warnings=["Existing CRDs will be preserved. Helm install may fail if an old release exists."],
            )
        else:
            return ModuleAction(
                path=path, name=name, action="deploy", required=required, order=order,
                confidence="high",
                reason="cert-manager not detected. Required for BNK TLS certificates.",
                detected_info=detected,
            )

    # --- bnk/flo ---

    def _analyze_flo(
        self, path: str, name: str, required: bool, order: int, template_slug: str | None
    ) -> ModuleAction:
        flo_info = self.bnk_install.get("flo", {})
        flo_running = flo_info.get("running", 0)
        flo_version = flo_info.get("version")
        helm_release = flo_info.get("helm_release")
        install_shape = self.bnk_install.get("install_shape")

        detected = {
            "running": flo_running,
            "version": flo_version,
            "helm_release": helm_release,
        }

        if flo_running > 0 and self.bnk_status == "installed":
            return ModuleAction(
                path=path, name=name, action="skip", required=required, order=order,
                confidence="high",
                reason=f"FLO {'v' + flo_version if flo_version else ''} is running ({flo_running} pod(s)). BNK is installed.",
                detected_info=detected,
            )
        elif self.bnk_status == "installed":
            reason = (
                "BNK is already installed via Helm (F5-supported); FLO is not used "
                "in a direct-Helm install."
                if install_shape == "helm"
                else "BNK is already installed via a direct Helm/manual install "
                "(F5-supported); FLO is not used in a direct-Helm install."
            )
            return ModuleAction(
                path=path, name=name, action="skip", required=required, order=order,
                confidence="high",
                reason=reason,
                detected_info=detected,
            )
        elif flo_running > 0:
            return ModuleAction(
                path=path, name=name, action="skip", required=required, order=order,
                confidence="medium",
                reason=f"FLO is running but BNK status is '{self.bnk_status}'. Investigate before re-deploying.",
                detected_info=detected,
                warnings=["FLO is present but BNK may not be fully operational."],
            )
        elif helm_release:
            return ModuleAction(
                path=path, name=name, action="investigate", required=required, order=order,
                confidence="medium",
                reason="FLO Helm release found but no running pods. May need restart or upgrade.",
                detected_info=detected,
                warnings=["A Helm release exists but FLO is not running. Check the f5-operator namespace."],
            )
        else:
            return ModuleAction(
                path=path, name=name, action="deploy", required=required, order=order,
                confidence="high",
                reason="FLO not detected. Required to manage BNK TMM lifecycle.",
                detected_info=detected,
            )

    # --- bnk/cneinstance ---

    def _analyze_cneinstance(
        self, path: str, name: str, required: bool, order: int, template_slug: str | None
    ) -> ModuleAction:
        cne = self.bnk_install.get("cne_instance")
        tmm = self.bnk_install.get("tmm", {})
        tmm_running = tmm.get("running", 0)

        detected = {
            "cne_instance": cne.get("name") if cne else None,
            "tmm_running": tmm_running,
            "features": cne.get("features") if cne else None,
        }

        install_shape = self.bnk_install.get("install_shape")

        if cne and tmm_running > 0:
            containers = tmm.get("containers", {})
            container_str = containers.get("containers", "") if containers else ""
            return ModuleAction(
                path=path, name=name, action="skip", required=required, order=order,
                confidence="high",
                reason=f"CNEInstance '{cne.get('name', '')}' exists with TMM running ({container_str or f'{tmm_running} pod(s)'}).",
                detected_info=detected,
                variable_overrides={"instance_name": cne.get("name", "bnk-instance")},
            )
        elif cne:
            return ModuleAction(
                path=path, name=name, action="investigate", required=required, order=order,
                confidence="medium",
                reason=f"CNEInstance exists but TMM has {tmm_running} running pod(s). TMM may be starting or unhealthy.",
                detected_info=detected,
                warnings=["CNEInstance exists but TMM is not fully running. Check FLO and TMM pod logs."],
            )
        elif self.bnk_status == "installed":
            # BNK is already installed (e.g. direct Helm/manual, F5-supported) but has
            # no CNEInstance CR — the running install manages its own BNK instance, so
            # deploying this module would be wrong. Only deploy when nothing is installed.
            shape_str = "Helm" if install_shape == "helm" else "Helm/manual"
            return ModuleAction(
                path=path, name=name, action="skip", required=required, order=order,
                confidence="high",
                reason=(
                    f"BNK is already installed ({shape_str}, F5-supported); the BNK "
                    "instance is managed by the existing install."
                ),
                detected_info=detected,
            )
        else:
            overrides: dict[str, Any] = {}
            # Pre-fill based on detected features
            if self.distribution == "EKS":
                overrides["dpu_enabled"] = False

            return ModuleAction(
                path=path, name=name, action="deploy", required=required, order=order,
                confidence="high",
                reason="No CNEInstance detected. This creates the CR that tells FLO to deploy TMM.",
                detected_info=detected,
                variable_overrides=overrides,
            )

    # --- bnk/bnk-vlans ---

    def _analyze_bnk_vlans(
        self, path: str, name: str, required: bool, order: int, template_slug: str | None
    ) -> ModuleAction:
        vlans = self.bnk_install.get("vlans", [])

        detected: dict[str, Any] = {"vlan_count": len(vlans)}
        overrides: dict[str, Any] = {}

        if vlans:
            # Extract VLAN details for display
            vlan_details = []
            external_ips = []
            internal_ips = []
            for v in vlans:
                info = {
                    "name": v.get("name"),
                    "self_ips": v.get("self_ips", []),
                    "interfaces": v.get("interfaces", []),
                    "programmed": v.get("programmed", False),
                }
                vlan_details.append(info)

                # Classify VLANs by name convention
                vname = (v.get("name") or "").lower()
                if "external" in vname or "ext" in vname:
                    external_ips.extend(v.get("self_ips", []))
                elif "internal" in vname or "int" in vname:
                    internal_ips.extend(v.get("self_ips", []))

            detected["vlans"] = vlan_details
            all_programmed = all(v.get("programmed") for v in vlans)

            if all_programmed:
                return ModuleAction(
                    path=path, name=name, action="skip", required=required, order=order,
                    confidence="high",
                    reason=f"{len(vlans)} VLAN(s) configured and Programmed=True.",
                    detected_info=detected,
                )
            else:
                unprogrammed = [v["name"] for v in vlans if not v.get("programmed")]
                return ModuleAction(
                    path=path, name=name, action="investigate", required=required, order=order,
                    confidence="medium",
                    reason=f"{len(vlans)} VLAN(s) exist but {len(unprogrammed)} not programmed: {', '.join(unprogrammed)}.",
                    detected_info=detected,
                    warnings=["Some VLANs are not programmed. TMM may not have configured self-IPs on these interfaces."],
                )

        return ModuleAction(
            path=path, name=name, action="deploy", required=required, order=order,
            confidence="high",
            reason="No VLANs detected. Required for TMM data-plane self-IP assignment.",
            detected_info=detected,
            variable_overrides=overrides,
        )

    # --- bnk/bnk-gatewayclass ---

    def _analyze_bnk_gatewayclass(
        self, path: str, name: str, required: bool, order: int, template_slug: str | None
    ) -> ModuleAction:
        gw_api = self.gateway_api
        gatewayclasses = gw_api.get("gatewayclasses", 0)

        detected = {
            "gateway_api_status": gw_api.get("status", "missing"),
            "gatewayclasses": gatewayclasses,
            "gateways": gw_api.get("gateways", 0),
        }

        if gatewayclasses > 0 and self.bnk_status == "installed":
            return ModuleAction(
                path=path, name=name, action="skip", required=required, order=order,
                confidence="high",
                reason=f"BNK GatewayClass already registered ({gatewayclasses} GatewayClass(es) found).",
                detected_info=detected,
            )
        elif gatewayclasses > 0:
            return ModuleAction(
                path=path, name=name, action="investigate", required=required, order=order,
                confidence="medium",
                reason=f"{gatewayclasses} GatewayClass(es) exist but BNK is '{self.bnk_status}'. Verify it's the BNK GatewayClass.",
                detected_info=detected,
            )
        else:
            return ModuleAction(
                path=path, name=name, action="deploy", required=required, order=order,
                confidence="high",
                reason="No GatewayClass detected. Required to create Gateway resources.",
                detected_info=detected,
            )

    # --- bnk/gateway (for GenAI Gateway solution) ---

    def _analyze_bnk_gateway(
        self, path: str, name: str, required: bool, order: int, template_slug: str | None
    ) -> ModuleAction:
        gateways = self.gateway_api.get("gateways", 0)

        return ModuleAction(
            path=path, name=name, action="deploy", required=required, order=order,
            confidence="high",
            reason="Gateway needed for traffic routing.",
            detected_info={"gateways": gateways},
        )

    # --- Generic analyzer (for app/* modules and unknown paths) ---

    def _analyze_generic(
        self, path: str, name: str, required: bool, order: int, template_slug: str | None
    ) -> ModuleAction:
        """Default analyzer for modules without specific scan-based logic."""

        # For solution app modules, check if BNK is installed
        if path.startswith("app/") and template_slug == "eks-bnk-smartllm-demo":
            if self.bnk_status == "not_installed":
                return ModuleAction(
                    path=path, name=name, action="blocked", required=required, order=order,
                    confidence="high",
                    reason="BNK is not installed. Deploy the BNK blueprint first.",
                    blockers=["F5 BNK must be installed before deploying solution modules."],
                )

        return ModuleAction(
            path=path, name=name, action="deploy", required=required, order=order,
            confidence="high",
            reason="No specific scan data affects this module. Ready to deploy.",
        )

    # Module path → analyzer method mapping
    _MODULE_ANALYZERS: dict[str, Any] = {
        "k8s/bnk-prerequisites": _analyze_bnk_prerequisites,
        "k8s/network-setup": _analyze_network_setup,
        "k8s/cert-manager": _analyze_cert_manager,
        "bnk/flo": _analyze_flo,
        "bnk/cneinstance": _analyze_cneinstance,
        "bnk/bnk-vlans": _analyze_bnk_vlans,
        "bnk/bnk-gatewayclass": _analyze_bnk_gatewayclass,
        "bnk/gateway": _analyze_bnk_gateway,
        "app/demo-gateway": _analyze_bnk_gateway,
    }

    # -----------------------------------------------------------------------
    # Variable suggestions
    # -----------------------------------------------------------------------

    def _extract_suggested_variables(self, template_slug: str) -> dict[str, Any]:
        """
        Extract project-level variable suggestions from scan data.

        These are variables that apply across multiple modules — the UI can
        pre-fill them into the project variable form.
        """
        suggestions: dict[str, Any] = {}

        if self.distribution == "EKS":
            suggestions["cloud_provider"] = "aws"
            region = self.cluster_info.get("region")
            if region:
                suggestions["region"] = region

        # Storage class
        default_sc = self.storage.get("default")
        if default_sc:
            suggestions["storage_class_name"] = default_sc

        # SR-IOV resource names
        if self.sriov.get("status") in ("detected", "partial"):
            sriov_details = self.sriov.get("node_details", [])
            resource_names = set()
            for node in sriov_details:
                for res_name in node.get("resources", {}).keys():
                    resource_names.add(res_name)

            if resource_names:
                suggestions["detected_sriov_resources"] = sorted(resource_names)

        # HP node info for MTU
        if self.hp_nodes > 0:
            suggestions["tmm_default_mtu"] = "9000"

        # BNK-specific: if already installed, capture existing config
        if self.bnk_status == "installed":
            cne = self.bnk_install.get("cne_instance")
            if cne:
                suggestions["instance_name"] = cne.get("name")

            vlans = self.bnk_install.get("vlans", [])
            for v in vlans:
                vname = (v.get("name") or "").lower()
                if "external" in vname:
                    suggestions["external_self_ips"] = v.get("self_ips", [])
                elif "internal" in vname:
                    suggestions["internal_self_ips"] = v.get("self_ips", [])

        return suggestions

    def _apply_lab_sizing_profile(self, plan: DeploymentPlan) -> None:
        """Merge the #387 part C lab sizing overrides into a plan.

        Adds the f5-tmm resource-override tree to ``suggested_variables``
        (the UI pre-fill / values-override surface this selector owns) and
        attaches the NON-PRODUCTION warning to ``global_warnings``. The
        actual helm-values application at deploy time happens in the
        catalog OpenTofu modules that consume these project variables —
        this method only shapes what gets suggested.
        """
        from services.bnk.sizing import LAB_PROFILE_WARNING, lab_profile_helm_values

        plan.suggested_variables.update(lab_profile_helm_values())
        plan.global_warnings.append(LAB_PROFILE_WARNING)

    def _multus_missing_warning(self) -> str:
        """Return truthful warning text for missing Multus by platform context."""
        if self.detected_platform_profile == "generic_onprem":
            return (
                "Multus CNI is not detected. On the generic_onprem baseline "
                "(kubeconfig-first/manual-friendly), BNK assumes Multus/NAD support for "
                "data-plane networking. Install or validate Multus before deploying the BNK stack."
            )
        return (
            "Multus CNI is not detected. BNK requires Multus for data-plane networking. "
            "Install Multus before deploying the BNK stack, or ensure your cluster uses "
            "a CNI that supports NetworkAttachmentDefinitions."
        )

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _template_display_name(slug: str) -> str:
        names = {
            "f5-bnk-2.2": "F5 BNK 2.2",
            "f5-bnk-2.3": "F5 BNK 2.3",
            "eks-bnk-smartllm-demo": "EKS + BNK Intelligent AI Load Balancing Demo",
            "aws-k8s-foundation": "AWS EKS Foundation",
        }
        return names.get(slug, slug)

    @staticmethod
    def _module_display_name(path: str) -> str:
        """Generate a human-readable name from a module path."""
        parts = path.split("/")
        if len(parts) >= 2:
            return parts[-1].replace("-", " ").replace("_", " ").title()
        return path.replace("-", " ").replace("_", " ").title()
