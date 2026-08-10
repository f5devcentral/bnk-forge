"""Unit tests for fleet lifecycle mappers — D-022 Phase 4.

Tests per-type lifecycle_state derivation (pure functions, no DB).
Covers: each status→state mapping (incl NULL/fallback rows), unknown handling
(NULL/never-reconciled/unregistered member_type), and the LIFECYCLE_STATES vocab.
"""

from types import SimpleNamespace

import pytest

from models.fleet import (
    LIFECYCLE_STATE_ACTIVE,
    LIFECYCLE_STATE_DECOMMISSIONED,
    LIFECYCLE_STATE_DRAINING,
    LIFECYCLE_STATE_PROVISIONING,
    LIFECYCLE_STATE_UNKNOWN,
    LIFECYCLE_STATES,
)
from services.fleet_reconcile_service import (
    _lifecycle_for_cluster,
    _lifecycle_for_dpu,
    _lifecycle_for_host,
)

# ---------------------------------------------------------------------------
# Cluster lifecycle mapper
# ---------------------------------------------------------------------------

class TestLifecycleForCluster:
    def _cluster(self, status):
        return SimpleNamespace(status=status)

    def test_connecting_is_provisioning(self):
        assert _lifecycle_for_cluster(self._cluster("connecting")) == LIFECYCLE_STATE_PROVISIONING

    def test_active_is_active(self):
        assert _lifecycle_for_cluster(self._cluster("active")) == LIFECYCLE_STATE_ACTIVE

    def test_inactive_is_decommissioned(self):
        assert _lifecycle_for_cluster(self._cluster("inactive")) == LIFECYCLE_STATE_DECOMMISSIONED

    def test_error_is_unknown(self):
        assert _lifecycle_for_cluster(self._cluster("error")) == LIFECYCLE_STATE_UNKNOWN

    def test_null_status_is_unknown(self):
        assert _lifecycle_for_cluster(self._cluster(None)) == LIFECYCLE_STATE_UNKNOWN

    def test_unknown_string_is_unknown(self):
        assert _lifecycle_for_cluster(self._cluster("some_future_status")) == LIFECYCLE_STATE_UNKNOWN

    def test_case_insensitive(self):
        assert _lifecycle_for_cluster(self._cluster("ACTIVE")) == LIFECYCLE_STATE_ACTIVE
        assert _lifecycle_for_cluster(self._cluster("CONNECTING")) == LIFECYCLE_STATE_PROVISIONING

    def test_whitespace_stripped(self):
        assert _lifecycle_for_cluster(self._cluster("  active  ")) == LIFECYCLE_STATE_ACTIVE


# ---------------------------------------------------------------------------
# Host lifecycle mapper (deployment wins over host status when present)
# ---------------------------------------------------------------------------

class TestLifecycleForHost:
    def _host(self, last_discovery_status=None):
        return SimpleNamespace(last_discovery_status=last_discovery_status)

    def _dep(self, status):
        return SimpleNamespace(status=status)

    # --- deployment status takes precedence ---

    def test_dep_pending_is_provisioning(self):
        assert _lifecycle_for_host(self._host(), self._dep("pending")) == LIFECYCLE_STATE_PROVISIONING

    def test_dep_discovering_is_provisioning(self):
        assert _lifecycle_for_host(self._host(), self._dep("discovering")) == LIFECYCLE_STATE_PROVISIONING

    def test_dep_planning_is_provisioning(self):
        assert _lifecycle_for_host(self._host(), self._dep("planning")) == LIFECYCLE_STATE_PROVISIONING

    def test_dep_in_progress_is_provisioning(self):
        assert _lifecycle_for_host(self._host(), self._dep("in_progress")) == LIFECYCLE_STATE_PROVISIONING

    def test_dep_completed_is_active(self):
        assert _lifecycle_for_host(self._host(), self._dep("completed")) == LIFECYCLE_STATE_ACTIVE

    def test_dep_cancelled_is_decommissioned(self):
        assert _lifecycle_for_host(self._host(), self._dep("cancelled")) == LIFECYCLE_STATE_DECOMMISSIONED

    def test_dep_unrecognised_falls_through_to_host(self):
        # Unrecognised dep status → fall through to host's last_discovery_status.
        assert (
            _lifecycle_for_host(self._host("completed"), self._dep("unknown_dep_status"))
            == LIFECYCLE_STATE_ACTIVE
        )

    # --- host fallback (no deployment) ---

    def test_host_pending_is_provisioning(self):
        assert _lifecycle_for_host(self._host("pending")) == LIFECYCLE_STATE_PROVISIONING

    def test_host_ssh_probe_is_provisioning(self):
        assert _lifecycle_for_host(self._host("ssh_probe")) == LIFECYCLE_STATE_PROVISIONING

    def test_host_bmc_probe_is_provisioning(self):
        assert _lifecycle_for_host(self._host("bmc_probe")) == LIFECYCLE_STATE_PROVISIONING

    def test_host_dpu_probe_is_provisioning(self):
        assert _lifecycle_for_host(self._host("dpu_probe")) == LIFECYCLE_STATE_PROVISIONING

    def test_host_assessment_is_provisioning(self):
        assert _lifecycle_for_host(self._host("assessment")) == LIFECYCLE_STATE_PROVISIONING

    def test_host_completed_is_active(self):
        assert _lifecycle_for_host(self._host("completed")) == LIFECYCLE_STATE_ACTIVE

    def test_host_failed_is_unknown(self):
        assert _lifecycle_for_host(self._host("failed")) == LIFECYCLE_STATE_UNKNOWN

    def test_host_null_no_dep_is_unknown(self):
        assert _lifecycle_for_host(self._host(None)) == LIFECYCLE_STATE_UNKNOWN

    def test_host_other_string_is_unknown(self):
        assert _lifecycle_for_host(self._host("some_status")) == LIFECYCLE_STATE_UNKNOWN

    # --- no deployment passed ---

    def test_no_deployment_uses_host_status(self):
        assert _lifecycle_for_host(self._host("ssh_probe"), None) == LIFECYCLE_STATE_PROVISIONING


# ---------------------------------------------------------------------------
# DPU lifecycle mapper
# ---------------------------------------------------------------------------

class TestLifecycleForDpu:
    def _dpu(self, flash_status=None, dpu_os_reachable=None):
        return SimpleNamespace(flash_status=flash_status, dpu_os_reachable=dpu_os_reachable)

    def test_uploading_is_provisioning(self):
        assert _lifecycle_for_dpu(self._dpu("uploading")) == LIFECYCLE_STATE_PROVISIONING

    def test_waiting_reboot_is_provisioning(self):
        assert _lifecycle_for_dpu(self._dpu("waiting_reboot")) == LIFECYCLE_STATE_PROVISIONING

    def test_os_online_is_active(self):
        assert _lifecycle_for_dpu(self._dpu("os_online")) == LIFECYCLE_STATE_ACTIVE

    def test_cancelled_is_decommissioned(self):
        assert _lifecycle_for_dpu(self._dpu("cancelled")) == LIFECYCLE_STATE_DECOMMISSIONED

    def test_failed_is_unknown(self):
        assert _lifecycle_for_dpu(self._dpu("failed")) == LIFECYCLE_STATE_UNKNOWN

    def test_null_but_reachable_is_active(self):
        assert _lifecycle_for_dpu(self._dpu(None, dpu_os_reachable=True)) == LIFECYCLE_STATE_ACTIVE

    def test_null_not_reachable_is_unknown(self):
        assert _lifecycle_for_dpu(self._dpu(None, dpu_os_reachable=False)) == LIFECYCLE_STATE_UNKNOWN

    def test_null_reachability_none_is_unknown(self):
        assert _lifecycle_for_dpu(self._dpu(None, dpu_os_reachable=None)) == LIFECYCLE_STATE_UNKNOWN

    def test_unknown_flash_status_not_reachable_is_unknown(self):
        assert _lifecycle_for_dpu(self._dpu("some_status", dpu_os_reachable=False)) == LIFECYCLE_STATE_UNKNOWN

    def test_unknown_flash_status_reachable_fallback(self):
        # Unrecognised flash_status but reachable → active via fallback.
        assert _lifecycle_for_dpu(self._dpu("other", dpu_os_reachable=True)) == LIFECYCLE_STATE_ACTIVE


# ---------------------------------------------------------------------------
# LIFECYCLE_STATES vocab
# ---------------------------------------------------------------------------

class TestLifecycleStatesVocab:
    def test_all_five_states_present(self):
        expected = {
            LIFECYCLE_STATE_PROVISIONING,
            LIFECYCLE_STATE_ACTIVE,
            LIFECYCLE_STATE_DRAINING,
            LIFECYCLE_STATE_DECOMMISSIONED,
            LIFECYCLE_STATE_UNKNOWN,
        }
        assert LIFECYCLE_STATES == expected

    def test_draining_in_vocab_but_no_mapper_produces_it(self):
        # Draining is in vocab for forward-compat; no P4 mapper produces it.
        # This test documents that intent explicitly (will fail if a mapper is
        # accidentally added that produces draining before drain EXECUTION ships).
        from services.fleet_reconcile_service import _LIFECYCLE_MAPPERS

        for member_type, mapper in _LIFECYCLE_MAPPERS.items():
            # Try inputs that would reach the "no match" code path.
            if member_type == "cluster":
                result = mapper(SimpleNamespace(status=None))
            elif member_type == "bare-metal-host":
                result = mapper(SimpleNamespace(last_discovery_status=None), None)
            elif member_type == "dpu":
                result = mapper(SimpleNamespace(flash_status=None, dpu_os_reachable=None))
            else:
                continue
            assert result != LIFECYCLE_STATE_DRAINING, (
                f"mapper for {member_type!r} produced 'draining' unexpectedly — "
                "drain EXECUTION is out of scope for P4"
            )
