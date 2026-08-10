"""Unit tests for fleet reconcile service — D-022 Phase 1.

Tests per-type label mapping, idempotency, assigned-label preservation,
None omission, and the vocabulary validator.
"""

from types import SimpleNamespace

import pytest

from services.fleet_reconcile_service import (
    _discovered_labels_for_cluster,
    _discovered_labels_for_dpu,
    _discovered_labels_for_host,
)
from services.fleet_vocabulary import (
    ASSIGNED_LABEL_KEYS,
    validate_assigned_labels,
    validate_member_type,
)

# ---------------------------------------------------------------------------
# Cluster mapper
# ---------------------------------------------------------------------------

class TestDiscoveredLabelsForCluster:
    def _cluster(self, **kwargs):
        defaults = {
            "name": "aws-syd-test-cluster",
            "cloud_provider": "aws",
            "region": "us-east-1",
            "version": "1.29.2",
            "detected_platform_profile": "eks",
            "detected_platform_provider": "aws",
            "status": "active",
            "platform_capabilities": {"has_dpu": True},
        }
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def test_all_facts_mapped(self):
        labels = _discovered_labels_for_cluster(self._cluster())
        assert labels["name"] == "aws-syd-test-cluster"
        assert labels["vendor"] == "aws"
        assert labels["region"] == "us-east-1"
        assert labels["k8s-version"] == "1.29.2"
        assert labels["platform-profile"] == "eks"
        assert labels["platform-provider"] == "aws"
        assert labels["status"] == "active"
        assert labels["has-dpu"] == "true"

    def test_name_label_present(self):
        labels = _discovered_labels_for_cluster(self._cluster(name="prod-cluster-01"))
        assert labels["name"] == "prod-cluster-01"

    def test_name_label_preserves_case(self):
        # name is NOT lowercased so `name=AWS-Cluster` round-trips correctly.
        labels = _discovered_labels_for_cluster(self._cluster(name="AWS-Cluster-PROD"))
        assert labels["name"] == "AWS-Cluster-PROD"

    def test_name_label_absent_when_empty(self):
        labels = _discovered_labels_for_cluster(self._cluster(name=None))
        assert "name" not in labels

    def test_none_values_omitted(self):
        labels = _discovered_labels_for_cluster(self._cluster(
            cloud_provider=None,
            region=None,
            detected_platform_profile=None,
            platform_capabilities=None,
        ))
        assert "vendor" not in labels
        assert "region" not in labels
        assert "platform-profile" not in labels
        assert "has-dpu" not in labels

    def test_has_dpu_false(self):
        labels = _discovered_labels_for_cluster(self._cluster(
            platform_capabilities={"has_dpu": False}
        ))
        assert labels["has-dpu"] == "false"

    def test_empty_platform_capabilities_tolerated(self):
        labels = _discovered_labels_for_cluster(self._cluster(platform_capabilities={}))
        assert "has-dpu" not in labels

    def test_values_lowercase(self):
        labels = _discovered_labels_for_cluster(self._cluster(
            cloud_provider="AWS",
            status="ACTIVE",
        ))
        assert labels["vendor"] == "aws"
        assert labels["status"] == "active"

    def test_no_empty_string_values(self):
        labels = _discovered_labels_for_cluster(self._cluster(
            cloud_provider="",
            region="  ",
        ))
        # empty string and whitespace-only values must both be omitted
        assert "vendor" not in labels
        assert "region" not in labels

    def test_idempotent(self):
        cluster = self._cluster()
        labels_a = _discovered_labels_for_cluster(cluster)
        labels_b = _discovered_labels_for_cluster(cluster)
        assert labels_a == labels_b


# ---------------------------------------------------------------------------
# Host mapper
# ---------------------------------------------------------------------------

class TestDiscoveredLabelsForHost:
    def _host(self, **kwargs):
        defaults = {
            "name": "syd-host-01",
            "os_info": {"os_type": "ubuntu", "os_version": "22.04", "arch": "x86_64"},
            "k8s_info": {"installed": True, "version": "1.28.5"},
            "rshim_present": True,
            "nic_mode": "EMBEDDED_CPU(1)",
            "bmc_vendor": "supermicro",
            "topology": "bf3",
            "dpu_info": None,
        }
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def test_all_facts_mapped(self):
        labels = _discovered_labels_for_host(self._host())
        assert labels["name"] == "syd-host-01"
        assert labels["os"] == "ubuntu"
        assert labels["os-version"] == "22.04"
        assert labels["arch"] == "x86_64"
        assert labels["k8s-version"] == "1.28.5"
        assert labels["has-dpu"] == "true"
        assert labels["nic-mode"] == "embedded_cpu(1)"
        assert labels["bmc-vendor"] == "supermicro"
        assert labels["topology"] == "bf3"

    def test_name_label_present(self):
        labels = _discovered_labels_for_host(self._host(name="rack-01-blade-04"))
        assert labels["name"] == "rack-01-blade-04"

    def test_name_label_absent_when_none(self):
        labels = _discovered_labels_for_host(self._host(name=None))
        assert "name" not in labels

    def test_none_omitted(self):
        labels = _discovered_labels_for_host(self._host(
            os_info=None,
            k8s_info=None,
            rshim_present=None,
            nic_mode=None,
            bmc_vendor=None,
            topology=None,
        ))
        assert "os" not in labels
        assert "os-version" not in labels
        assert "has-dpu" not in labels
        assert "nic-mode" not in labels
        assert "bmc-vendor" not in labels
        assert "topology" not in labels

    def test_has_dpu_false(self):
        labels = _discovered_labels_for_host(self._host(rshim_present=False))
        assert labels["has-dpu"] == "false"

    def test_idempotent(self):
        host = self._host()
        assert _discovered_labels_for_host(host) == _discovered_labels_for_host(host)


# ---------------------------------------------------------------------------
# DPU mapper
# ---------------------------------------------------------------------------

class TestDiscoveredLabelsForDpu:
    def _dpu(self, **kwargs):
        defaults = {
            "name": "dpu-syd-01",
            "bmc_ip": "10.0.1.50",
            "access_mode": "bmc",
            "flash_status": "os_online",
            "serial_number": "MT12345678",
            "dpu_os_reachable": True,
        }
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def test_all_facts_mapped(self):
        labels = _discovered_labels_for_dpu(self._dpu())
        assert labels["name"] == "dpu-syd-01"
        assert labels["vendor"] == "nvidia-bluefield"
        assert labels["access-mode"] == "bmc"
        assert labels["flash-status"] == "os_online"
        assert labels["serial"] == "mt12345678"
        assert labels["reachable"] == "true"

    def test_name_label_present(self):
        labels = _discovered_labels_for_dpu(self._dpu(name="bf3-rack01"))
        assert labels["name"] == "bf3-rack01"

    def test_name_label_falls_back_to_bmc_ip(self):
        labels = _discovered_labels_for_dpu(self._dpu(name=None))
        assert labels["name"] == "10.0.1.50"

    def test_name_label_falls_back_to_serial(self):
        labels = _discovered_labels_for_dpu(self._dpu(name=None, bmc_ip=None))
        assert labels["name"] == "MT12345678"

    def test_name_label_absent_when_all_none(self):
        labels = _discovered_labels_for_dpu(self._dpu(name=None, bmc_ip=None, serial_number=None))
        assert "name" not in labels

    def test_vendor_always_set(self):
        # vendor is hard-coded at P1 — no input needed
        labels = _discovered_labels_for_dpu(self._dpu(
            name=None, bmc_ip=None, access_mode=None, flash_status=None,
            serial_number=None, dpu_os_reachable=None
        ))
        assert labels["vendor"] == "nvidia-bluefield"

    def test_none_omitted(self):
        labels = _discovered_labels_for_dpu(self._dpu(
            flash_status=None,
            serial_number=None,
            dpu_os_reachable=None,
        ))
        assert "flash-status" not in labels
        assert "serial" not in labels
        assert "reachable" not in labels

    def test_reachable_false(self):
        labels = _discovered_labels_for_dpu(self._dpu(dpu_os_reachable=False))
        assert labels["reachable"] == "false"

    def test_idempotent(self):
        dpu = self._dpu()
        assert _discovered_labels_for_dpu(dpu) == _discovered_labels_for_dpu(dpu)


# ---------------------------------------------------------------------------
# Vocabulary validator
# ---------------------------------------------------------------------------

class TestVocabularyValidator:
    def test_allowed_keys_pass(self):
        # All keys in ASSIGNED_LABEL_KEYS should not raise
        for key in ASSIGNED_LABEL_KEYS:
            validate_assigned_labels({key: "somevalue"})  # should not raise

    def test_empty_labels_pass(self):
        validate_assigned_labels({})  # should not raise

    def test_unknown_key_raises(self):
        from core.errors import ValidationError
        with pytest.raises(ValidationError, match="Unknown assigned label"):
            validate_assigned_labels({"k8s-version": "1.29"})

    def test_discovered_namespace_key_raises(self):
        from core.errors import ValidationError
        with pytest.raises(ValidationError):
            validate_assigned_labels({"vendor": "aws"})

    def test_multiple_unknown_keys_listed(self):
        from core.errors import ValidationError
        with pytest.raises(ValidationError, match="Unknown assigned label"):
            validate_assigned_labels({"env": "prod", "k8s-version": "1.29", "bad-key": "x"})


class TestMemberTypeValidator:
    def test_known_types_pass(self):
        from services.fleet_vocabulary import validate_member_type
        for t in ("cluster", "bare-metal-host", "dpu"):
            validate_member_type(t)  # should not raise

    def test_unknown_type_raises(self):
        from core.errors import ValidationError
        with pytest.raises(ValidationError, match="Unknown member_type"):
            validate_member_type("f5-bigip")
