"""Unit tests for DpuConnectivityService.

Covers endpoint construction (interface naming convention, CIDR
stripping, skipping unreachable DPUs) and the orchestration logic
(skip vs run vs failure persistence). The pairwise SSH+ping itself
is patched so the test doesn't open real network sockets — we only
check shape contracts (the ping helper, the persistence path).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import models  # noqa: F401  — register all model classes for create_all
from core.encryption import encrypt_value
from database import Base
from models.dpu import Dpu, ProjectDpuSettings
from models.project import Project
from services.dpu_connectivity_service import (
    DpuConnectivityService,
    _collect_endpoints,
    _strip_cidr,
    _vlan_iface_name,
)


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = Session(engine)
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def project(db: Session) -> Project:
    p = Project(name="conn-test", project_type="bnk")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@pytest.fixture
def settings(db: Session, project: Project) -> ProjectDpuSettings:
    """Project DPU settings with VLAN ids + default OS creds."""
    s = ProjectDpuSettings(
        project_id=project.id,
        external_vlan_id=0,
        internal_vlan_id=100,
        default_os_username="ubuntu",
        default_os_password_encrypted=encrypt_value("hunter2"),
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def _make_dpu(
    db: Session, project_id: int, *,
    name: str, dpu_os_ip: str | None, reachable: bool | None,
    ext_ipv4: str | None = None, int_ipv4: str | None = None,
    access_mode: str = "in-band",
) -> Dpu:
    d = Dpu(
        project_id=project_id,
        name=name,
        access_mode=access_mode,
        oob0_ipv4="0.0.0.0",  # not-null on schema
        dpu_os_ip=dpu_os_ip,
        dpu_os_reachable=reachable,
        external_vlan_ipv4=ext_ipv4,
        internal_vlan_ipv4=int_ipv4,
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    return d


class TestStripCidr:
    def test_strips_prefix(self):
        assert _strip_cidr("10.0.0.5/24") == "10.0.0.5"

    def test_passthrough_when_no_slash(self):
        assert _strip_cidr("10.0.0.5") == "10.0.0.5"

    def test_none_passthrough(self):
        assert _strip_cidr(None) is None
        assert _strip_cidr("") is None


class TestVlanIfaceName:
    def test_external_zero(self):
        # external_vlan_id=0 → `external0` (matches what shows up in
        # `ip -br addr` on a flashed DPU).
        assert _vlan_iface_name("external", 0) == "external0"

    def test_internal_hundred(self):
        assert _vlan_iface_name("internal", 100) == "internal100"

    def test_named_extra(self):
        assert _vlan_iface_name("storage", 200) == "storage200"

    def test_no_vlan_id_skips(self):
        assert _vlan_iface_name("external", None) is None


class TestCollectEndpoints:
    def test_only_reachable_dpus_with_assigned_vlan_ips(self, db, project, settings):
        # A: reachable + both VLANs assigned → 2 endpoints
        a = _make_dpu(
            db, project.id, name="a",
            dpu_os_ip="192.168.100.2", reachable=True,
            ext_ipv4="10.10.20.100/24", int_ipv4="10.10.10.100/24",
        )
        # B: unreachable → 0 endpoints
        b = _make_dpu(
            db, project.id, name="b",
            dpu_os_ip="192.168.100.2", reachable=False,
            ext_ipv4="10.10.20.101/24", int_ipv4="10.10.10.101/24",
        )
        # C: reachable but no VLAN IPs assigned → 0 endpoints
        c = _make_dpu(
            db, project.id, name="c",
            dpu_os_ip="192.168.100.2", reachable=True,
        )

        eps = _collect_endpoints([a, b, c], settings)
        assert len(eps) == 2
        ifaces = {e["interface"] for e in eps}
        assert ifaces == {"external0", "internal100"}
        # CIDR stripped on both addresses.
        addrs = {e["addresses"][0]["address"] for e in eps}
        assert addrs == {"10.10.20.100", "10.10.10.100"}

    def test_iface_names_use_vlan_id_suffix(self, db, project, settings):
        a = _make_dpu(
            db, project.id, name="a",
            dpu_os_ip="192.168.100.2", reachable=True,
            ext_ipv4="10.10.20.100", int_ipv4="10.10.10.100",
        )
        eps = _collect_endpoints([a], settings)
        # external_vlan_id=0 → "external0"; internal_vlan_id=100 → "internal100"
        ifaces = sorted(e["interface"] for e in eps)
        assert ifaces == ["external0", "internal100"]

    def test_extra_vlan_uses_per_dpu_address(self, db, project):
        # Project declares an extra VLAN "storage" with vlan_id=200; the
        # DPU's own extra_vlan_interfaces holds the assigned ipv4 for it.
        s = ProjectDpuSettings(
            project_id=project.id,
            extra_vlan_interfaces=[
                {"name": "storage", "vlan_id": 200, "ipv4_subnet": "10.20.30.0/24"},
            ],
            default_os_username="ubuntu",
            default_os_password_encrypted=encrypt_value("hunter2"),
        )
        db.add(s)
        db.commit()
        db.refresh(s)

        d = _make_dpu(
            db, project.id, name="d",
            dpu_os_ip="192.168.100.2", reachable=True,
        )
        d.extra_vlan_interfaces = [
            {"name": "storage", "vlan_id": 200, "ipv4": "10.20.30.5/24"},
        ]
        db.commit()
        db.refresh(d)

        eps = _collect_endpoints([d], s)
        assert len(eps) == 1
        assert eps[0]["interface"] == "storage200"
        assert eps[0]["addresses"][0]["address"] == "10.20.30.5"


class TestRunOrchestration:
    def test_fewer_than_two_reachable_skips(self, db, project, settings):
        # Only one DPU has assigned VLAN addresses; the other doesn't.
        # `unique_sources` < 2 ⇒ persisted as `skipped`.
        _make_dpu(
            db, project.id, name="a",
            dpu_os_ip="192.168.100.2", reachable=True,
            ext_ipv4="10.10.20.100/24", int_ipv4="10.10.10.100/24",
        )
        result = DpuConnectivityService(db).run(project.id)
        assert result["status"] == "skipped"
        db.refresh(settings)
        assert settings.dpu_connectivity_status == "skipped"
        assert "fewer than two" in settings.dpu_connectivity_matrix["note"]

    def test_runs_pings_and_persists_matrix(self, db, project, settings):
        # Two DPUs with VLAN IPs set; patch the per-source SSH-ping
        # helper to return canned results so the orchestration is what
        # we're actually testing.
        _make_dpu(
            db, project.id, name="a",
            dpu_os_ip="192.168.100.2", reachable=True,
            ext_ipv4="10.10.20.100/24", int_ipv4="10.10.10.100/24",
        )
        _make_dpu(
            db, project.id, name="b",
            dpu_os_ip="192.168.100.2", reachable=True,
            ext_ipv4="10.10.20.101/24", int_ipv4="10.10.10.101/24",
        )

        def fake_pings(*, db, src_dpu, src_endpoints, target_endpoints, os_user, os_pw):  # noqa: ARG001
            return [{
                "src_node_id": int(src_dpu.id),
                "src_iface": (src_endpoints[0]["interface"] if src_endpoints else "?"),
                "dst_node_id": -1,
                "dst_iface": "external0",
                "family": "inet",
                "dst_address": "10.10.20.99",
                "reachable": True,
                "rtt_ms": 0.5,
                "error": None,
            }]

        with patch(
            "services.dpu_connectivity_service._run_dpu_pings",
            side_effect=fake_pings,
        ):
            result = DpuConnectivityService(db).run(project.id)

        assert result["status"] == "completed"
        groups = result["matrix"]["groups"]
        assert "dpu" in groups
        # Two sources × one fake ping each ⇒ two rows.
        assert len(groups["dpu"]["results"]) == 2
        # Endpoint count: 2 DPUs × 2 VLAN ifaces each = 4 endpoints.
        assert len(groups["dpu"]["endpoints"]) == 4
        db.refresh(settings)
        assert settings.dpu_connectivity_status == "completed"
        assert settings.dpu_connectivity_run_at is not None

    def test_missing_creds_raises(self, db, project):
        # No ProjectDpuSettings row at all → service refuses early.
        from core.errors import BadRequestError
        with pytest.raises(BadRequestError, match="DPU OS credentials"):
            DpuConnectivityService(db).run(project.id)


class TestGetAndMark:
    def test_get_returns_none_until_first_run(self, db, project):
        out = DpuConnectivityService(db).get_matrix(project.id)
        assert out == {"status": None, "run_at": None, "matrix": None}

    def test_mark_running_creates_settings_row(self, db, project):
        DpuConnectivityService(db).mark_running(project.id)
        s = (
            db.query(ProjectDpuSettings)
            .filter(ProjectDpuSettings.project_id == project.id)
            .one()
        )
        assert s.dpu_connectivity_status == "in_progress"
