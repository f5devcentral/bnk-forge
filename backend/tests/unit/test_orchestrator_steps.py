"""Unit tests for orchestrator.py step registries."""

import pytest

from services.bare_metal.orchestrator import (
    TOPOLOGY_STEPS,
    get_steps_for_topology,
)


@pytest.mark.unit
class TestTopologySteps:
    def test_all_topologies_registered(self):
        """All known topologies must have step registries."""
        expected = {"regular", "bf3", "bf3_ipmi", "bmc", "dual_dpu_obmc"}
        assert set(TOPOLOGY_STEPS.keys()) == expected

    def test_dual_dpu_obmc_has_correct_phase1_steps(self):
        steps = get_steps_for_topology("dual_dpu_obmc")
        phase1_names = [s.name for s in steps if s.phase.value == "phase_1_dpu"]
        assert phase1_names == [
            "probe_dpu_state",
            "flash_dpu",
            "wait_dpu_ready",
            "setup_dpu_networking",
            "install_dpu_prereqs",
        ]

    def test_dual_dpu_obmc_shares_phase_2_4(self):
        """Phase 2-4 should be shared with regular topology."""
        regular = get_steps_for_topology("regular")
        dual = get_steps_for_topology("dual_dpu_obmc")
        regular_phase_2_4 = [s for s in regular if s.phase.value != "phase_1_dpu"]
        dual_phase_2_4 = [s for s in dual if s.phase.value != "phase_1_dpu"]
        assert [s.name for s in regular_phase_2_4] == [s.name for s in dual_phase_2_4]

    def test_unknown_topology_raises(self):
        with pytest.raises(ValueError, match="Unknown topology"):
            get_steps_for_topology("not_a_topology")

    @pytest.mark.parametrize("topo", list(TOPOLOGY_STEPS.keys()))
    def test_all_topologies_have_at_least_one_step(self, topo):
        steps = get_steps_for_topology(topo)
        assert len(steps) > 0
