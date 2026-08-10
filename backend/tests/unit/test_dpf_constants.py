"""
Unit tests for DPF resource registry and constants.

Verifies that all DPF CRDs are correctly registered and discoverable.
"""

import pytest

from core.k8s_resource_registry import (
    RESOURCE_REGISTRY,
    get_resource_type,
    list_resources_by_category,
)
from core.k8s_types import ApiGroups, ResourceCategory
from services.dpf.constants import DPF_DETECT_TYPES, DPF_RESOURCE_TYPES
from services.scanner.constants import DPF_CORE_CRDS, DPF_CRD_GROUPS, DPF_SERVICE_CRDS

# =========================================================================
# Resource Registry — DPF entries exist
# =========================================================================


class TestDpfRegistry:

    def test_all_dpf_resource_types_registered(self):
        """Every key in DPF_RESOURCE_TYPES must exist in RESOURCE_REGISTRY."""
        for rt_key in DPF_RESOURCE_TYPES:
            assert rt_key in RESOURCE_REGISTRY, f"{rt_key} not in RESOURCE_REGISTRY"

    def test_all_dpf_detect_types_registered(self):
        """Every key in DPF_DETECT_TYPES must exist in RESOURCE_REGISTRY."""
        for rt_key in DPF_DETECT_TYPES:
            assert rt_key in RESOURCE_REGISTRY, f"{rt_key} not in RESOURCE_REGISTRY"

    def test_dpf_detect_types_subset_of_resource_types(self):
        """DPF_DETECT_TYPES should be a subset of DPF_RESOURCE_TYPES."""
        assert set(DPF_DETECT_TYPES).issubset(set(DPF_RESOURCE_TYPES))

    def test_get_resource_type_dpudevice(self):
        rt = get_resource_type("dpudevice")
        assert rt.kind == "DPUDevice"
        assert rt.api_group == ApiGroups.DPF_PROVISIONING
        assert rt.api_version == "v1alpha1"
        assert rt.plural == "dpudevices"
        assert rt.namespaced is True
        assert rt.category == ResourceCategory.DPF

    def test_get_resource_type_dpfoperatorconfig(self):
        rt = get_resource_type("dpfoperatorconfig")
        assert rt.kind == "DPFOperatorConfig"
        assert rt.api_group == ApiGroups.DPF_OPERATOR
        assert rt.plural == "dpfoperatorconfigs"

    def test_get_resource_type_dpuservice(self):
        rt = get_resource_type("dpuservice")
        assert rt.kind == "DPUService"
        assert rt.api_group == ApiGroups.DPF_SERVICE
        assert rt.plural == "dpuservices"

    def test_get_resource_type_bfb(self):
        rt = get_resource_type("bfb")
        assert rt.kind == "BFB"
        assert rt.api_group == ApiGroups.DPF_PROVISIONING
        assert rt.plural == "bfbs"

    def test_get_resource_type_dpucluster(self):
        rt = get_resource_type("dpucluster")
        assert rt.kind == "DPUCluster"
        assert rt.plural == "dpuclusters"

    def test_list_by_dpf_category(self):
        dpf_resources = list_resources_by_category(ResourceCategory.DPF)
        assert len(dpf_resources) >= 15, f"Expected >=15 DPF resources, got {len(dpf_resources)}"
        # Check some expected keys are present
        assert "dpudevice" in dpf_resources
        assert "dpuservice" in dpf_resources
        assert "dpfoperatorconfig" in dpf_resources

    def test_dpf_provisioning_api_group(self):
        """All provisioning CRDs use the correct API group."""
        provisioning_keys = ["dpudevice", "dpuset", "dpucluster", "dpu", "bfb", "dpuflavor", "dpunode", "dpudiscovery"]
        for key in provisioning_keys:
            rt = get_resource_type(key)
            assert rt.api_group == "provisioning.dpu.nvidia.com", f"{key} has wrong api_group: {rt.api_group}"

    def test_dpf_service_api_group(self):
        """All service CRDs use the correct API group."""
        service_keys = ["dpuservice", "dpudeployment", "dpuservicechain", "dpuserviceinterface", "dpuserviceipam"]
        for key in service_keys:
            rt = get_resource_type(key)
            assert rt.api_group == "svc.dpu.nvidia.com", f"{key} has wrong api_group: {rt.api_group}"

    def test_full_api_version(self):
        rt = get_resource_type("dpudevice")
        assert rt.full_api_version == "provisioning.dpu.nvidia.com/v1alpha1"


# =========================================================================
# Scanner Constants — DPF CRD sets
# =========================================================================


class TestScannerDpfConstants:

    def test_dpf_crd_groups_correct(self):
        assert "provisioning.dpu.nvidia.com" in DPF_CRD_GROUPS
        assert "svc.dpu.nvidia.com" in DPF_CRD_GROUPS
        assert "operator.dpu.nvidia.com" in DPF_CRD_GROUPS

    def test_core_crds_are_fully_qualified(self):
        """Core CRD names should be fully qualified (plural.group)."""
        for crd_name in DPF_CORE_CRDS:
            assert "." in crd_name, f"CRD name {crd_name} should be fully qualified"

    def test_service_crds_are_fully_qualified(self):
        for crd_name in DPF_SERVICE_CRDS:
            assert "." in crd_name, f"CRD name {crd_name} should be fully qualified"

    def test_core_crds_count(self):
        assert len(DPF_CORE_CRDS) == 5

    def test_service_crds_count(self):
        assert len(DPF_SERVICE_CRDS) == 3

    def test_no_overlap_core_service(self):
        """Core and service CRD sets should not overlap."""
        assert DPF_CORE_CRDS.isdisjoint(DPF_SERVICE_CRDS)
