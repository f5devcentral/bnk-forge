"""Shared backend platform-context normalization and detection helpers.

PLATFORM-CONTEXT-002 scope:
- canonical platform profile normalization
- bounded capability + constraint derivation
- shared reusable backend source of truth for cluster/project API surfaces
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.platform_context import (
    PLATFORM_CAPABILITY_KEYS,
    PLATFORM_CONSTRAINT_KEYS,
    PLATFORM_PROFILE_VALUES,
    PLATFORM_PROVIDER_VALUES,
    PlatformProfile,
)


@dataclass(slots=True)
class ClusterPlatformContext:
    """Normalized platform context contract for cluster serialization."""

    detected_platform_profile: str
    detected_platform_provider: str | None
    platform_capabilities: dict[str, bool | None]
    platform_constraints: dict[str, bool | None]

    def to_dict(self) -> dict[str, Any]:
        return {
            "detected_platform_profile": self.detected_platform_profile,
            "detected_platform_provider": self.detected_platform_provider,
            "platform_capabilities": self.platform_capabilities,
            "platform_constraints": self.platform_constraints,
        }


class PlatformContextService:
    """Source-of-truth helper for platform context normalization/detection."""

    _PROFILE_PROVIDER_MAP = {
        "eks": "aws",
        "aks": "azure",
        "gke": "gcp",
        "roks": "ibm",
    }

    _MANAGED_CLOUD_PROFILE_MAP = {
        "eks": "eks",
        "aws": "eks",
        "aks": "aks",
        "azure": "aks",
        "gke": "gke",
        "gcp": "gke",
        "roks": "roks",
        "ibm": "roks",
    }

    _ONPREM_PROVIDER_PROFILE_MAP = {
        "on-prem": "generic_onprem",
        "onprem": "generic_onprem",
        "baremetal": "generic_onprem",
        "vmware": "generic_onprem",
        "vsphere": "generic_onprem",
    }

    _CLOUD_PROVIDER_PROVIDER_MAP = {
        "eks": "aws",
        "aws": "aws",
        "aks": "azure",
        "azure": "azure",
        "gke": "gcp",
        "gcp": "gcp",
        "roks": "ibm",
        "ibm": "ibm",
        "on-prem": "baremetal",
        "onprem": "baremetal",
        "baremetal": "baremetal",
        "vmware": "vmware",
        "vsphere": "vmware",
    }

    @classmethod
    def normalize_project_value(cls, value: str | None, allowed: tuple[str, ...]) -> str | None:
        """Normalize a stored project context enum value to allowed vocabulary."""
        if value is None:
            return None
        normalized = value.strip().lower()
        return normalized if normalized in allowed else None

    @classmethod
    def derive_cluster_context(cls, cluster, scan_result: dict[str, Any] | None = None) -> ClusterPlatformContext:
        """Derive normalized cluster platform context from cluster + optional scan signals."""
        profile = cls._detect_platform_profile(cluster, scan_result)
        provider = cls._detect_platform_provider(cluster, profile)
        capabilities = cls._derive_capabilities(profile, scan_result)
        constraints = cls._derive_constraints(profile)

        return ClusterPlatformContext(
            detected_platform_profile=profile,
            detected_platform_provider=provider,
            platform_capabilities=capabilities,
            platform_constraints=constraints,
        )

    @classmethod
    def apply_cluster_context(cls, cluster, scan_result: dict[str, Any] | None = None) -> ClusterPlatformContext:
        """Compute and persist normalized cluster platform context fields."""
        context = cls.derive_cluster_context(cluster, scan_result)
        cluster.detected_platform_profile = context.detected_platform_profile
        cluster.detected_platform_provider = context.detected_platform_provider
        cluster.platform_capabilities = context.platform_capabilities
        cluster.platform_constraints = context.platform_constraints
        return context

    @classmethod
    def serialize_cluster_context(cls, cluster) -> ClusterPlatformContext:
        """Return normalized context for API serialization.

        Uses stored values when valid; falls back to deterministic derived values for
        backward compatibility with existing rows that predate platform fields.
        """
        stored_profile = cls.normalize_project_value(
            getattr(cluster, "detected_platform_profile", None),
            PLATFORM_PROFILE_VALUES,
        )
        stored_provider = cls.normalize_project_value(
            getattr(cluster, "detected_platform_provider", None),
            PLATFORM_PROVIDER_VALUES,
        )

        stored_capabilities = getattr(cluster, "platform_capabilities", None)
        stored_constraints = getattr(cluster, "platform_constraints", None)

        if (
            stored_profile
            and isinstance(stored_capabilities, dict)
            and isinstance(stored_constraints, dict)
        ):
            return ClusterPlatformContext(
                detected_platform_profile=stored_profile,
                detected_platform_provider=stored_provider,
                platform_capabilities=cls._normalized_capability_map(stored_capabilities),
                platform_constraints=cls._normalized_constraint_map(stored_constraints),
            )

        # Legacy/backfill path: derive deterministic context for rows that predate
        # stored platform metadata, and stage values back onto the model instance
        # so serving paths can persist them when a DB session is available.
        derived = cls.derive_cluster_context(cluster)
        cluster.detected_platform_profile = derived.detected_platform_profile
        cluster.detected_platform_provider = derived.detected_platform_provider
        cluster.platform_capabilities = derived.platform_capabilities
        cluster.platform_constraints = derived.platform_constraints
        return derived

    @classmethod
    def _detect_platform_profile(cls, cluster, scan_result: dict[str, Any] | None) -> str:
        cloud_provider = (getattr(cluster, "cloud_provider", None) or "").strip().lower()
        if cloud_provider in cls._MANAGED_CLOUD_PROFILE_MAP:
            return cls._MANAGED_CLOUD_PROFILE_MAP[cloud_provider]

        if cls._has_ocp_signals(cluster, scan_result):
            return "ocp"

        if cloud_provider in cls._ONPREM_PROVIDER_PROFILE_MAP:
            return cls._ONPREM_PROVIDER_PROFILE_MAP[cloud_provider]

        # Backward-compatible deterministic fallback: if we have concrete
        # kubeconfig signals but no explicit provider metadata, classify as
        # generic on-prem instead of unknown.
        if cls._has_kubeconfig_signals(cluster):
            return PlatformProfile.GENERIC_ONPREM.value

        return PlatformProfile.UNKNOWN.value

    @classmethod
    def _has_kubeconfig_signals(cls, cluster) -> bool:
        context = (getattr(cluster, "context", None) or "").strip()
        api_server = (getattr(cluster, "api_server", None) or "").strip()
        return bool(context or api_server)

    @classmethod
    def _has_ocp_signals(cls, cluster, scan_result: dict[str, Any] | None) -> bool:
        strong_signals = cls._has_strong_ocp_signals(scan_result)
        if strong_signals:
            return True

        # Weak naming hints are not sufficient on their own for OCP normalization.
        # Prefer unknown/generic fallback over false-positive OCP claims.
        if cls._has_weak_ocp_naming_signals(cluster):
            return False

        return False

    @classmethod
    def _has_strong_ocp_signals(cls, scan_result: dict[str, Any] | None) -> bool:
        distribution = (
            ((scan_result or {}).get("cluster_info") or {}).get("distribution") or ""
        ).strip().lower()
        if distribution in {"openshift", "open_shift", "okd", "ocp"}:
            return True

        crd_groups = {
            str(group).strip().lower()
            for group in ((scan_result or {}).get("crd_groups") or set())
            if isinstance(group, str)
        }
        if {
            "route.openshift.io",
            "security.openshift.io",
            "operator.openshift.io",
            "config.openshift.io",
            "machineconfiguration.openshift.io",
        } & crd_groups:
            return True

        return False

    @classmethod
    def _has_weak_ocp_naming_signals(cls, cluster) -> bool:
        context = (getattr(cluster, "context", None) or "").strip().lower()
        api_server = (getattr(cluster, "api_server", None) or "").strip().lower()
        return "openshift" in context or "okd" in context or "openshift" in api_server

    @classmethod
    def _detect_platform_provider(cls, cluster, profile: str) -> str | None:
        cloud_provider = (getattr(cluster, "cloud_provider", None) or "").strip().lower()
        if cloud_provider in cls._CLOUD_PROVIDER_PROVIDER_MAP:
            return cls._CLOUD_PROVIDER_PROVIDER_MAP[cloud_provider]
        if profile in cls._PROFILE_PROVIDER_MAP:
            return cls._PROFILE_PROVIDER_MAP[profile]
        return None

    @classmethod
    def _derive_capabilities(
        cls,
        profile: str,
        scan_result: dict[str, Any] | None,
    ) -> dict[str, bool | None]:
        capabilities = {key: None for key in PLATFORM_CAPABILITY_KEYS}

        if profile in {"eks", "aks", "gke", "roks"}:
            capabilities["cloud_load_balancer"] = True

        crd_groups = {
            str(group).strip().lower()
            for group in ((scan_result or {}).get("crd_groups") or set())
            if isinstance(group, str)
        }

        if profile == "ocp":
            capabilities["openshift_routes"] = "route.openshift.io" in crd_groups
            capabilities["scc"] = "security.openshift.io" in crd_groups
            capabilities["machine_config"] = "machineconfiguration.openshift.io" in crd_groups
            capabilities["operator_framework"] = bool(
                {
                    "operators.coreos.com",
                    "operator.openshift.io",
                    "config.openshift.io",
                }
                & crd_groups
            )

        prerequisites = (scan_result or {}).get("prerequisites") or {}

        multus = prerequisites.get("multus") or {}
        capabilities["network_attachment_definitions"] = cls._safe_bool(multus.get("nad_crd_installed"))
        if capabilities["network_attachment_definitions"] is True:
            capabilities["secondary_networks"] = True

        sriov = prerequisites.get("sriov") or {}
        if cls._safe_int(sriov.get("nodes_with_vfs")) > 0:
            capabilities["sriov"] = True
        elif sriov:
            capabilities["sriov"] = False

        hugepages = prerequisites.get("hugepages") or {}
        if cls._safe_int(hugepages.get("nodes_with_hugepages")) > 0:
            capabilities["hugepages"] = True
        elif hugepages:
            capabilities["hugepages"] = False

        gateway_api = prerequisites.get("gateway_api") or {}
        gw_status = (gateway_api.get("status") or "").strip().lower()
        if gw_status in {"detected", "partial"}:
            capabilities["gateway_api"] = True
        elif gw_status == "missing":
            capabilities["gateway_api"] = False

        return capabilities

    @classmethod
    def _derive_constraints(cls, profile: str) -> dict[str, bool | None]:
        constraints = {key: None for key in PLATFORM_CONSTRAINT_KEYS}

        if profile in {"eks", "aks", "gke", "roks"}:
            constraints["managed_control_plane"] = True
            constraints["cluster_lifecycle_external"] = True
            constraints["direct_node_package_mutation_not_supported"] = True
        elif profile == PlatformProfile.GENERIC_ONPREM.value:
            constraints["managed_control_plane"] = False
            constraints["cluster_lifecycle_external"] = None
            constraints["direct_node_package_mutation_not_supported"] = False
        elif profile == "ocp":
            constraints["managed_control_plane"] = False
            constraints["cluster_lifecycle_external"] = True
            constraints["operator_required_for_networking"] = True
            constraints["privileged_workloads_require_platform_security_binding"] = True
            constraints["direct_node_package_mutation_not_supported"] = True

        return constraints

    @staticmethod
    def _safe_bool(value: Any) -> bool | None:
        if isinstance(value, bool):
            return value
        return None

    @staticmethod
    def _safe_int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _normalized_capability_map(cls, raw: dict[str, Any]) -> dict[str, bool | None]:
        return {
            key: cls._safe_bool(raw.get(key)) if key in raw else None
            for key in PLATFORM_CAPABILITY_KEYS
        }

    @classmethod
    def _normalized_constraint_map(cls, raw: dict[str, Any]) -> dict[str, bool | None]:
        return {
            key: cls._safe_bool(raw.get(key)) if key in raw else None
            for key in PLATFORM_CONSTRAINT_KEYS
        }
