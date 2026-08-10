"""
Unit tests for bare-metal DPU deployment enums and Pydantic schemas.

Tests all enum values and schema validation (positive and negative cases).
"""

from datetime import UTC, datetime, timezone

import pytest
from pydantic import ValidationError

from models.enums import (
    BareMetalDeploymentStatus,
    BareMetalTopology,
    DeploymentPhase,
    DeploymentStepStatus,
    HostAccessTier,
)
from schemas.bare_metal import (
    BareMetalDeploymentCreate,
    BareMetalDeploymentListResponse,
    BareMetalDeploymentResponse,
    BareMetalDeploymentResumeRequest,
    BareMetalDiscoveryRequest,
    BareMetalHostCreate,
    BareMetalHostListResponse,
    BareMetalHostResponse,
    BareMetalHostUpdate,
    BnkVersionProfileListResponse,
    BnkVersionProfileResponse,
    DeploymentStepResponse,
)

_NOW = datetime(2026, 4, 13, 10, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _host_response_data(**overrides) -> dict:
    base = {
        "id": 1,
        "project_id": 10,
        "name": "dpu-host-01",
        "description": "Test DPU host",
        "hostname": "dpu-host-01.local",
        "host_ip": "10.0.0.1",
        "ssh_credential_id": 5,
        "ssh_port": 22,
        "has_jumphost_chain": False,
        "bmc_ip": "10.0.0.2",
        "bmc_access_tier": "redfish",
        "bmc_vendor": "supermicro",
        "ipmi_ip": None,
        "has_bmc_credentials": True,
        "has_ipmi_credentials": False,
        "topology": "bmc",
        "topology_auto_detected": True,
        "network_mode": "vlan",
        "vlan_id": 100,
        "gateway_ip": "10.0.0.254",
        "host_mgmt_ip": "10.0.0.1/24",
        "dpu_mgmt_ip": "10.0.0.3/24",
        "dpu_credential_id": None,
        "version_profile_id": 2,
        "os_info": {"os_type": "ubuntu", "os_version": "22.04"},
        "dpu_info": [{"index": 0, "nic_mode": "dpu"}],
        "k8s_info": {"installed": True, "running": True},
        "last_discovery_at": _NOW,
        "kubernetes_cluster_id": 7,
        "created_at": _NOW,
        "updated_at": _NOW,
        "deploy_dpu_pci_address": None,
        "deploy_dpu_index": None,
        "rshim_source": None,
        "bond_mode": None,
    }
    base.update(overrides)
    return base


def _step_response_data(**overrides) -> dict:
    base = {
        "id": 1,
        "step_index": 0,
        "phase": "phase_1_dpu",
        "name": "flash_dpu",
        "description": "Flash DPU firmware",
        "status": "completed",
        "already_done": False,
        "connectivity_risk": False,
        "connectivity_mechanism": None,
        "started_at": _NOW,
        "completed_at": _NOW,
        "duration_seconds": 45.2,
        "error_message": None,
        "exit_code": 0,
        "created_at": _NOW,
    }
    base.update(overrides)
    return base


def _deployment_response_data(**overrides) -> dict:
    base = {
        "id": 1,
        "host_id": 1,
        "project_id": 10,
        "topology": "bmc",
        "status": "completed",
        "current_phase": "phase_4_platform",
        "current_step_index": 15,
        "celery_task_id": "abc-123",
        "resume_from_step": None,
        "started_at": _NOW,
        "completed_at": _NOW,
        "duration_seconds": 1800.5,
        "error_message": None,
        "error_phase": None,
        "error_step_index": None,
        "triggered_by": "user",
        "created_at": _NOW,
        "steps": [],
    }
    base.update(overrides)
    return base


def _profile_response_data(**overrides) -> dict:
    base = {
        "id": 1,
        "name": "bnk-2.2",
        "display_name": "BNK 2.2 (GA)",
        "description": "BNK 2.2 General Availability release",
        "is_default": True,
        "bnk_manifest_version": "2.2.0",
        "bnk_cr_kind": "BNKGatewayClass",
        "flo_version": "0.10.5",
        "k8s_version": "1.30.4",
        "doca_version": "2.9.1",
        "containerd_version": "1.7.20",
        "runc_version": "1.1.13",
        "calico_version": "3.28.1",
        "cert_manager_version": "1.15.3",
        "gateway_api_version": "1.1.0",
        "multus_version": "4.1.0",
        "sriov_version": "1.4.0",
        "storage_class_type": "local-path",
        "storage_provisioner": "rancher.io/local-path",
        "feature_flags": {"ipv6": False, "tmm_node_labels": True},
        "created_at": _NOW,
    }
    base.update(overrides)
    return base


# ===========================================================================
# TestBareMetalEnums
# ===========================================================================

@pytest.mark.unit
class TestBareMetalEnums:
    """Tests for the 5 bare-metal enums."""

    def test_bare_metal_topology_values(self):
        assert BareMetalTopology.REGULAR == "regular"
        assert BareMetalTopology.BF3 == "bf3"
        assert BareMetalTopology.BF3_IPMI == "bf3_ipmi"
        assert BareMetalTopology.BMC == "bmc"
        assert BareMetalTopology.DUAL_DPU_OBMC == "dual_dpu_obmc"

    def test_bare_metal_topology_member_count(self):
        assert len(BareMetalTopology) == 5

    def test_bare_metal_deployment_status_values(self):
        assert BareMetalDeploymentStatus.PENDING == "pending"
        assert BareMetalDeploymentStatus.DISCOVERING == "discovering"
        assert BareMetalDeploymentStatus.PLANNING == "planning"
        assert BareMetalDeploymentStatus.IN_PROGRESS == "in_progress"
        assert BareMetalDeploymentStatus.PAUSED == "paused"
        assert BareMetalDeploymentStatus.COMPLETED == "completed"
        assert BareMetalDeploymentStatus.FAILED == "failed"
        assert BareMetalDeploymentStatus.CANCELLED == "cancelled"

    def test_bare_metal_deployment_status_terminal_states(self):
        terminal = BareMetalDeploymentStatus.terminal_states()
        assert terminal == frozenset({
            BareMetalDeploymentStatus.COMPLETED,
            BareMetalDeploymentStatus.FAILED,
            BareMetalDeploymentStatus.CANCELLED,
        })

    def test_bare_metal_deployment_status_terminal_states_excludes_non_terminal(self):
        terminal = BareMetalDeploymentStatus.terminal_states()
        assert BareMetalDeploymentStatus.PENDING not in terminal
        assert BareMetalDeploymentStatus.IN_PROGRESS not in terminal
        assert BareMetalDeploymentStatus.PAUSED not in terminal

    def test_deployment_step_status_values(self):
        assert DeploymentStepStatus.PENDING == "pending"
        assert DeploymentStepStatus.RUNNING == "running"
        assert DeploymentStepStatus.COMPLETED == "completed"
        assert DeploymentStepStatus.FAILED == "failed"
        assert DeploymentStepStatus.SKIPPED == "skipped"

    def test_deployment_step_status_member_count(self):
        assert len(DeploymentStepStatus) == 5

    def test_deployment_phase_values(self):
        assert DeploymentPhase.PHASE_1_DPU == "phase_1_dpu"
        assert DeploymentPhase.PHASE_2_K8S == "phase_2_k8s"
        assert DeploymentPhase.PHASE_3_JOIN == "phase_3_join"
        assert DeploymentPhase.PHASE_4_PLATFORM == "phase_4_platform"

    def test_deployment_phase_member_count(self):
        assert len(DeploymentPhase) == 4

    def test_host_access_tier_values(self):
        assert HostAccessTier.NONE == "none"
        assert HostAccessTier.IPMI_ONLY == "ipmi"
        assert HostAccessTier.REDFISH == "redfish"

    def test_host_access_tier_member_count(self):
        assert len(HostAccessTier) == 3

    def test_enums_are_strings(self):
        """All enum members must compare equal to their string value."""
        assert BareMetalTopology.REGULAR == "regular"
        assert BareMetalDeploymentStatus.COMPLETED == "completed"
        assert DeploymentStepStatus.RUNNING == "running"
        assert DeploymentPhase.PHASE_1_DPU == "phase_1_dpu"
        assert HostAccessTier.REDFISH == "redfish"


# ===========================================================================
# TestBareMetalHostSchemas
# ===========================================================================

@pytest.mark.unit
class TestBareMetalHostSchemas:
    """Tests for BareMetalHostCreate, BareMetalHostUpdate, BareMetalHostResponse."""

    def test_host_create_minimal_valid(self):
        req = BareMetalHostCreate(name="host-01", host_ip="192.168.1.10")
        assert req.name == "host-01"
        assert req.host_ip == "192.168.1.10"
        assert req.ssh_port == 22
        assert req.description is None

    def test_host_create_full_valid(self):
        req = BareMetalHostCreate(
            name="dpu-host",
            host_ip="10.0.0.1",
            description="A DPU host",
            ssh_credential_id=1,
            ssh_port=2222,
            jumphost_chain=[{"ssh_credential_id": 99}],
            bmc_ip="10.0.0.2",
            bmc_username="admin",
            bmc_password="secret",
            ipmi_ip="10.0.0.3",
            ipmi_username="ipmiadmin",
            ipmi_password="ipmipass",
            topology="bmc",
            network_mode="vlan",
            vlan_id=100,
            gateway_ip="10.0.0.254",
            host_mgmt_ip="10.0.0.1/24",
            dpu_mgmt_ip="10.0.0.5/24",
            version_profile_id=2,
        )
        assert req.ssh_port == 2222
        assert req.topology == "bmc"
        assert req.vlan_id == 100

    def test_host_create_missing_name_rejected(self):
        with pytest.raises(ValidationError):
            BareMetalHostCreate(host_ip="10.0.0.1")  # type: ignore[call-arg]

    def test_host_create_missing_host_ip_rejected(self):
        with pytest.raises(ValidationError):
            BareMetalHostCreate(name="host-01")  # type: ignore[call-arg]

    def test_host_create_non_string_host_ip_rejected(self):
        with pytest.raises(ValidationError):
            BareMetalHostCreate(name="host-01", host_ip=12345)  # type: ignore[arg-type]

    def test_host_create_non_integer_ssh_port_rejected(self):
        with pytest.raises(ValidationError):
            BareMetalHostCreate(name="h", host_ip="1.2.3.4", ssh_port="not-a-port")  # type: ignore[arg-type]

    def test_host_update_all_optional(self):
        """BareMetalHostUpdate can be instantiated with no fields."""
        req = BareMetalHostUpdate()
        assert req.name is None
        assert req.host_ip is None

    def test_host_update_partial(self):
        req = BareMetalHostUpdate(name="new-name", ssh_port=2222)
        assert req.name == "new-name"
        assert req.ssh_port == 2222
        assert req.host_ip is None

    def test_host_response_valid(self):
        resp = BareMetalHostResponse(**_host_response_data())
        assert resp.id == 1
        assert resp.project_id == 10
        assert resp.name == "dpu-host-01"
        assert resp.has_jumphost_chain is False
        assert resp.has_bmc_credentials is True
        assert resp.bmc_access_tier == "redfish"

    def test_host_response_missing_required_field_rejected(self):
        data = _host_response_data()
        del data["host_ip"]
        with pytest.raises(ValidationError):
            BareMetalHostResponse(**data)

    def test_host_response_nullable_fields(self):
        data = _host_response_data(
            description=None, hostname=None, bmc_ip=None, bmc_vendor=None,
            ipmi_ip=None, topology=None, network_mode=None, vlan_id=None,
            gateway_ip=None, host_mgmt_ip=None, dpu_mgmt_ip=None,
            dpu_credential_id=None,
            version_profile_id=None, os_info=None, dpu_info=None,
            k8s_info=None, last_discovery_at=None, kubernetes_cluster_id=None,
            ssh_credential_id=None,
        )
        resp = BareMetalHostResponse(**data)
        assert resp.description is None
        assert resp.topology is None

    def test_host_list_response_empty(self):
        resp = BareMetalHostListResponse(hosts=[])
        assert resp.hosts == []

    def test_host_list_response_with_hosts(self):
        host_data = _host_response_data()
        resp = BareMetalHostListResponse(hosts=[BareMetalHostResponse(**host_data)])
        assert len(resp.hosts) == 1
        assert resp.hosts[0].name == "dpu-host-01"


# ===========================================================================
# TestBareMetalDeploymentSchemas
# ===========================================================================

@pytest.mark.unit
class TestBareMetalDeploymentSchemas:
    """Tests for deployment-related schemas."""

    def test_deployment_create_minimal(self):
        req = BareMetalDeploymentCreate(host_id=1)
        assert req.host_id == 1
        assert req.resume_from_step is None
        assert req.skip_discovery is False

    def test_deployment_create_with_resume(self):
        req = BareMetalDeploymentCreate(host_id=1, resume_from_step=5, skip_discovery=True)
        assert req.resume_from_step == 5
        assert req.skip_discovery is True

    def test_deployment_create_missing_host_id_rejected(self):
        with pytest.raises(ValidationError):
            BareMetalDeploymentCreate()  # type: ignore[call-arg]

    def test_deployment_step_response_valid(self):
        resp = DeploymentStepResponse(**_step_response_data())
        assert resp.step_index == 0
        assert resp.phase == "phase_1_dpu"
        assert resp.status == "completed"
        assert resp.already_done is False

    def test_deployment_step_response_missing_required_rejected(self):
        data = _step_response_data()
        del data["step_index"]
        with pytest.raises(ValidationError):
            DeploymentStepResponse(**data)

    def test_deployment_response_valid_no_steps(self):
        resp = BareMetalDeploymentResponse(**_deployment_response_data())
        assert resp.id == 1
        assert resp.host_id == 1
        assert resp.topology == "bmc"
        assert resp.steps == []

    def test_deployment_response_with_steps(self):
        step = DeploymentStepResponse(**_step_response_data())
        data = _deployment_response_data(steps=[_step_response_data()])
        resp = BareMetalDeploymentResponse(**data)
        assert len(resp.steps) == 1
        assert resp.steps[0].name == "flash_dpu"

    def test_deployment_response_missing_topology_rejected(self):
        data = _deployment_response_data()
        del data["topology"]
        with pytest.raises(ValidationError):
            BareMetalDeploymentResponse(**data)

    def test_deployment_list_response_empty(self):
        resp = BareMetalDeploymentListResponse(deployments=[])
        assert resp.deployments == []

    def test_deployment_list_response_with_items(self):
        dep_data = _deployment_response_data()
        resp = BareMetalDeploymentListResponse(deployments=[BareMetalDeploymentResponse(**dep_data)])
        assert len(resp.deployments) == 1

    def test_deployment_resume_request_default(self):
        req = BareMetalDeploymentResumeRequest()
        assert req.from_step is None

    def test_deployment_resume_request_with_step(self):
        req = BareMetalDeploymentResumeRequest(from_step=3)
        assert req.from_step == 3

    def test_deployment_resume_request_non_int_rejected(self):
        with pytest.raises(ValidationError):
            BareMetalDeploymentResumeRequest(from_step="not-an-int")  # type: ignore[arg-type]


# ===========================================================================
# TestBareMetalDiscoverySchemas
# ===========================================================================

@pytest.mark.unit
class TestBareMetalDiscoverySchemas:
    """Tests for BareMetalDiscoveryRequest and BareMetalDiscoveryResponse."""

    def test_discovery_request_defaults(self):
        req = BareMetalDiscoveryRequest()
        assert req.probe_bmc is True
        assert req.probe_ipmi is True
        assert req.probe_dpu is True
        assert req.probe_vlan is False

    def test_discovery_request_opt_in_vlan(self):
        req = BareMetalDiscoveryRequest(probe_vlan=True)
        assert req.probe_vlan is True

    def test_discovery_request_disable_probes(self):
        req = BareMetalDiscoveryRequest(probe_bmc=False, probe_ipmi=False, probe_dpu=False)
        assert req.probe_bmc is False
        assert req.probe_ipmi is False
        assert req.probe_dpu is False

    def test_discovery_request_non_bool_coercible_rejected(self):
        """Pydantic v2 coerces bool from strings/ints but rejects dicts/lists."""
        with pytest.raises(ValidationError):
            BareMetalDiscoveryRequest(probe_bmc={"invalid": "value"})  # type: ignore[arg-type]


# ===========================================================================
# TestVersionProfileSchemas
# ===========================================================================

@pytest.mark.unit
class TestVersionProfileSchemas:
    """Tests for BnkVersionProfileResponse and BnkVersionProfileListResponse."""

    def test_profile_response_valid(self):
        resp = BnkVersionProfileResponse(**_profile_response_data())
        assert resp.name == "bnk-2.2"
        assert resp.is_default is True
        assert resp.bnk_cr_kind == "BNKGatewayClass"
        assert resp.feature_flags == {"ipv6": False, "tmm_node_labels": True}

    def test_profile_response_nullable_description(self):
        resp = BnkVersionProfileResponse(**_profile_response_data(description=None))
        assert resp.description is None

    def test_profile_response_nullable_feature_flags(self):
        resp = BnkVersionProfileResponse(**_profile_response_data(feature_flags=None))
        assert resp.feature_flags is None

    def test_profile_response_missing_required_rejected(self):
        data = _profile_response_data()
        del data["bnk_manifest_version"]
        with pytest.raises(ValidationError):
            BnkVersionProfileResponse(**data)

    def test_profile_response_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            BnkVersionProfileResponse(**_profile_response_data(id="not-an-int"))  # type: ignore[arg-type]

    def test_profile_list_response_empty(self):
        resp = BnkVersionProfileListResponse(profiles=[])
        assert resp.profiles == []

    def test_profile_list_response_with_profiles(self):
        prof = BnkVersionProfileResponse(**_profile_response_data())
        resp = BnkVersionProfileListResponse(profiles=[prof])
        assert len(resp.profiles) == 1
        assert resp.profiles[0].name == "bnk-2.2"


class TestBareMetalDiscoveryResponseDocaStatus:
    def test_doca_status_in_probes(self):
        """BareMetalDiscoveryResponse accepts doca in probes dict."""
        from schemas.bare_metal import BareMetalDiscoveryResponse
        resp = BareMetalDiscoveryResponse(
            host_id=1,
            host_ip="10.0.0.1",
            topology_recommendation="dpu",
            probes={"host": {}, "doca": {"ready": True, "repo_configured": True}},
            assessment=[],
            version_drift=None,
            recommendations=[],
            discovered_at=_NOW,
        )
        assert resp.probes["doca"]["ready"] is True
