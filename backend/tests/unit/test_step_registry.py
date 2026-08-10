"""Unit tests for step registries — pure data, no DB."""

import pytest

from models.enums import DeploymentPhase
from services.bare_metal.orchestrator import (
    BF3_IPMI_STEPS,
    BF3_STEPS,
    BMC_STEPS,
    REGULAR_STEPS,
    TOPOLOGY_STEPS,
    get_steps_for_topology,
)


@pytest.mark.unit
class TestStepRegistries:
    def test_regular_step_count(self):
        """Regular topology: 19 steps (4 + 7 + 3 + 5)."""
        assert len(REGULAR_STEPS) == 19

    def test_bf3_step_count(self):
        """BF3 topology: 21 steps (6 Phase 1 + 15 shared)."""
        assert len(BF3_STEPS) == 21

    def test_bf3_ipmi_step_count(self):
        """BF3-IPMI topology: 23 steps (8 Phase 1 + 15 shared)."""
        assert len(BF3_IPMI_STEPS) == 23

    def test_bmc_step_count(self):
        """BMC topology: 23 steps (8 Phase 1 + 15 shared)."""
        assert len(BMC_STEPS) == 23

    def test_all_topologies_registered(self):
        assert set(TOPOLOGY_STEPS.keys()) == {"regular", "bf3", "bf3_ipmi", "bmc", "dual_dpu_obmc"}

    def test_regular_phases_covered(self):
        phases = {s.phase for s in REGULAR_STEPS}
        assert phases == {
            DeploymentPhase.PHASE_1_DPU,
            DeploymentPhase.PHASE_2_K8S,
            DeploymentPhase.PHASE_3_JOIN,
            DeploymentPhase.PHASE_4_PLATFORM,
        }

    def test_phase_2_4_shared_across_topologies(self):
        """All topologies share the same Phase 2-4 steps."""
        regular_p2 = [s for s in REGULAR_STEPS if s.phase != DeploymentPhase.PHASE_1_DPU]
        bf3_p2 = [s for s in BF3_STEPS if s.phase != DeploymentPhase.PHASE_1_DPU]
        bf3_ipmi_p2 = [s for s in BF3_IPMI_STEPS if s.phase != DeploymentPhase.PHASE_1_DPU]
        bmc_p2 = [s for s in BMC_STEPS if s.phase != DeploymentPhase.PHASE_1_DPU]

        assert regular_p2 == bf3_p2 == bf3_ipmi_p2 == bmc_p2

    def test_bf3_has_connectivity_risk_steps(self):
        risky = [s for s in BF3_STEPS if s.connectivity_risk]
        assert len(risky) >= 1
        assert any(s.connectivity_mechanism == "pre_staged_netplan" for s in risky)

    def test_bf3_ipmi_has_ipmi_power_cycle(self):
        names = [s.name for s in BF3_IPMI_STEPS]
        assert "ipmi_power_cycle" in names
        assert "set_nic_mode_mlxconfig" in names

    def test_bmc_has_redfish_steps(self):
        names = [s.name for s in BMC_STEPS]
        assert "set_nic_mode_redfish" in names
        assert "redfish_cold_boot" in names

    def test_get_steps_for_valid_topology(self):
        steps = get_steps_for_topology("regular")
        assert steps is REGULAR_STEPS

    def test_get_steps_for_invalid_topology(self):
        with pytest.raises(ValueError, match="Unknown topology"):
            get_steps_for_topology("invalid")

    def test_step_names_unique_within_topology(self):
        """Step names should be unique within each topology's Phase 1."""
        for topology, steps in TOPOLOGY_STEPS.items():
            p1_names = [s.name for s in steps if s.phase == DeploymentPhase.PHASE_1_DPU]
            assert len(p1_names) == len(set(p1_names)), f"Duplicate Phase 1 step names in {topology}"

    def test_all_steps_have_description(self):
        for topology, steps in TOPOLOGY_STEPS.items():
            for step in steps:
                assert step.description, f"Missing description: {topology}/{step.name}"

    def test_connectivity_mechanism_only_on_risky_steps(self):
        for topology, steps in TOPOLOGY_STEPS.items():
            for step in steps:
                if step.connectivity_mechanism:
                    assert step.connectivity_risk, (
                        f"{topology}/{step.name} has mechanism but no risk flag"
                    )

    def test_regular_phase_1_has_four_steps(self):
        p1 = [s for s in REGULAR_STEPS if s.phase == DeploymentPhase.PHASE_1_DPU]
        assert len(p1) == 4

    def test_regular_phase_1_no_connectivity_risk(self):
        """Regular topology has no connectivity-risky steps."""
        risky = [s for s in REGULAR_STEPS if s.connectivity_risk]
        assert len(risky) == 0

    def test_bf3_ipmi_phase_1_step_names(self):
        """BF3-IPMI Phase 1 must include mlxconfig + IPMI power cycle + flash."""
        p1_names = [s.name for s in BF3_IPMI_STEPS if s.phase == DeploymentPhase.PHASE_1_DPU]
        assert "set_nic_mode_mlxconfig" in p1_names
        assert "deposit_phase_c" in p1_names
        assert "ipmi_power_cycle" in p1_names
        assert "flash_dpu" in p1_names
        assert "wait_dpu_ready" in p1_names

    def test_bmc_phase_1_step_names(self):
        """BMC Phase 1 must include Redfish steps."""
        p1_names = [s.name for s in BMC_STEPS if s.phase == DeploymentPhase.PHASE_1_DPU]
        assert "set_nic_mode_redfish" in p1_names
        assert "deposit_phase_c" in p1_names
        assert "redfish_cold_boot" in p1_names
        assert "flash_dpu" in p1_names
        assert "wait_dpu_ready" in p1_names

    def test_shared_phase_2_has_seven_steps(self):
        """Phase 2 (K8s setup) has exactly 7 steps."""
        p2 = [s for s in REGULAR_STEPS if s.phase == DeploymentPhase.PHASE_2_K8S]
        assert len(p2) == 7

    def test_shared_phase_3_has_three_steps(self):
        """Phase 3 (DPU join) has exactly 3 steps."""
        p3 = [s for s in REGULAR_STEPS if s.phase == DeploymentPhase.PHASE_3_JOIN]
        assert len(p3) == 3

    def test_shared_phase_4_has_five_steps(self):
        """Phase 4 (Platform) has exactly 5 steps."""
        p4 = [s for s in REGULAR_STEPS if s.phase == DeploymentPhase.PHASE_4_PLATFORM]
        assert len(p4) == 5

    def test_all_topology_keys_match_bm_topology_enum(self):
        """TOPOLOGY_STEPS keys should match BareMetalTopology values."""
        from models.enums import BareMetalTopology

        enum_values = {t.value for t in BareMetalTopology}
        assert set(TOPOLOGY_STEPS.keys()) == enum_values


@pytest.mark.unit
class TestTopologyModuleMap:
    def test_all_bare_metal_topologies_have_map_entry(self):
        """_TOPOLOGY_MODULE_MAP must have an entry for every BareMetalTopology value.

        This is a regression guard: if a new topology is added to the enum but
        not to the map, _auto_select_topology_modules() silently falls back to
        an empty set and disables ALL optional modules for that topology.
        """
        from models.enums import BareMetalTopology
        from services.stack_deployment_service import _TOPOLOGY_MODULE_MAP

        for topology in BareMetalTopology:
            assert topology.value in _TOPOLOGY_MODULE_MAP, (
                f"BareMetalTopology.{topology.name} ('{topology.value}') is missing from "
                f"_TOPOLOGY_MODULE_MAP in stack_deployment_service.py. "
                f"Add an entry (even an empty set()) to make the fallback explicit."
            )

    def test_dual_dpu_obmc_has_no_optional_modules(self):
        """dual_dpu_obmc Phase 1 is all required steps — no optional blueprint modules."""
        from services.stack_deployment_service import _TOPOLOGY_MODULE_MAP

        assert _TOPOLOGY_MODULE_MAP["dual_dpu_obmc"] == set()
