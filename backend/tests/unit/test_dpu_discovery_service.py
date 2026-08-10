"""Unit tests for DpuDiscoveryService.

Verifies credential fallback, state transitions (running → ok/failed),
denormalized field extraction, and error-path capture — all without
touching a real network. The BluefieldRedfishProbe is swapped for a
fixture-backed fetcher.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import models  # noqa: F401 — registers Base
from core.encryption import encrypt_value
from database import Base
from models.dpu import Dpu, ProjectDpuSettings
from models.project import Project
from services.bare_metal.bluefield import BluefieldRedfishProbe, ProbeError
from services.bare_metal.bluefield.inventory import DpuInterface
from services.dpu_discovery_service import DpuDiscoveryService, _pick_oob_interface

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "bluefield_redfish"


def _fixture_fetcher(path: str) -> dict:
    safe = path.strip("/").replace("redfish/", "").replace("/", "_")
    if safe.endswith("_"):
        safe = safe[:-1]
    return json.loads((FIXTURE_DIR / f"{safe}.json").read_text())


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
    p = Project(name="p", description="")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@pytest.fixture
def dpu(db: Session, project: Project) -> Dpu:
    d = Dpu(
        project_id=project.id,
        bmc_ip="192.168.68.100",
        bmc_username_encrypted=encrypt_value("root"),
        bmc_password_encrypted=encrypt_value("hb9rwm4hb9fx"),
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    return d


def _patch_probe_with_fixture(monkeypatch):
    """Replace BluefieldRedfishProbe's fetcher with fixture lookup."""
    original_init = BluefieldRedfishProbe.__init__

    def new_init(self, bmc_ip, username, password, **kwargs):
        kwargs["fetcher"] = _fixture_fetcher
        original_init(self, bmc_ip, username, password, **kwargs)

    monkeypatch.setattr(BluefieldRedfishProbe, "__init__", new_init)


class TestMarkRunning:
    def test_sets_status_and_clears_error(self, db, project, dpu):
        dpu.last_discovery_status = "failed"
        dpu.last_discovery_error = "previous failure"
        db.commit()

        DpuDiscoveryService(db).mark_running(project.id, dpu.id)
        db.commit()

        db.refresh(dpu)
        assert dpu.last_discovery_status == "running"
        assert dpu.last_discovery_error is None


class TestRunAndPersistSuccess:
    def test_captures_snapshot_and_denormalizes_fields(self, db, dpu, monkeypatch):
        _patch_probe_with_fixture(monkeypatch)
        DpuDiscoveryService(db).run_and_persist(dpu.id)

        db.refresh(dpu)
        assert dpu.last_discovery_status == "ok"
        assert dpu.last_discovery_error is None
        assert dpu.last_discovery_at is not None
        assert dpu.serial_number == "MT2428XZ0N1D"
        assert dpu.oob0_mac == "5c:25:73:e6:38:68"

        payload = dpu.last_discovery_payload
        assert payload["manufacturer"] == "Nvidia"
        assert payload["bios"]["host_privilege_level"] == "Privileged"
        assert payload["bios"]["nic_mode"] == "DpuMode"
        assert payload["firmware"]["bsp"] == "4.13.1.13827"


class TestCredentialFallback:
    def test_falls_back_to_project_default_password(self, db, project, monkeypatch):
        # DPU has NO BMC creds — must fall back to project settings
        dpu = Dpu(project_id=project.id, bmc_ip="10.0.0.1")
        db.add(dpu)
        settings = ProjectDpuSettings(
            project_id=project.id,
            default_oob_username="root",
            default_oob_password_encrypted=encrypt_value("fallback-pw"),
        )
        db.add(settings)
        db.commit()
        db.refresh(dpu)

        _patch_probe_with_fixture(monkeypatch)
        DpuDiscoveryService(db).run_and_persist(dpu.id)

        db.refresh(dpu)
        assert dpu.last_discovery_status == "ok"

    def test_defaults_username_to_root_when_only_password_set(self, db, project, monkeypatch):
        # Project default has password but no username — should default to "root"
        # because BlueField BMCs only have a root account.
        dpu = Dpu(project_id=project.id, bmc_ip="10.0.0.1")
        db.add(dpu)
        settings = ProjectDpuSettings(
            project_id=project.id,
            default_oob_username=None,
            default_oob_password_encrypted=encrypt_value("only-pw"),
        )
        db.add(settings)
        db.commit()
        db.refresh(dpu)

        _patch_probe_with_fixture(monkeypatch)
        DpuDiscoveryService(db).run_and_persist(dpu.id)

        db.refresh(dpu)
        assert dpu.last_discovery_status == "ok"

    def test_missing_credentials_captured_as_failure(self, db, project):
        # No DPU creds, no project defaults
        dpu = Dpu(project_id=project.id, bmc_ip="10.0.0.1")
        db.add(dpu)
        db.commit()
        db.refresh(dpu)

        DpuDiscoveryService(db).run_and_persist(dpu.id)

        db.refresh(dpu)
        assert dpu.last_discovery_status == "failed"
        assert "password" in (dpu.last_discovery_error or "").lower()
        assert dpu.last_discovery_at is not None


class TestProbeFailure:
    def test_probe_error_captured_on_row(self, db, dpu, monkeypatch):
        def raise_probe_error(self):
            raise ProbeError("service root unreachable")

        monkeypatch.setattr(BluefieldRedfishProbe, "probe", raise_probe_error)
        DpuDiscoveryService(db).run_and_persist(dpu.id)

        db.refresh(dpu)
        assert dpu.last_discovery_status == "failed"
        assert "unreachable" in dpu.last_discovery_error.lower()

    def test_unexpected_exception_captured_on_row(self, db, dpu, monkeypatch):
        def boom(self):
            raise RuntimeError("boom")

        monkeypatch.setattr(BluefieldRedfishProbe, "probe", boom)
        DpuDiscoveryService(db).run_and_persist(dpu.id)

        db.refresh(dpu)
        assert dpu.last_discovery_status == "failed"
        assert "boom" in dpu.last_discovery_error


class TestMissingDpu:
    def test_vanished_dpu_does_not_crash(self, db):
        # DPU id that never existed — task must noop, not raise
        DpuDiscoveryService(db).run_and_persist(99999)


class TestPickOobInterface:
    def test_prefers_oob0_when_present(self):
        ifs = [
            DpuInterface(name="eth0", mac="aa:aa:aa:aa:aa:aa"),
            DpuInterface(name="oob0", mac="bb:bb:bb:bb:bb:bb"),
            DpuInterface(name="eth1", mac="cc:cc:cc:cc:cc:cc"),
        ]
        assert _pick_oob_interface(ifs).name == "oob0"

    def test_falls_back_to_eth0_when_no_oob0(self):
        ifs = [
            DpuInterface(name="eth0", mac="aa:aa:aa:aa:aa:aa"),
            DpuInterface(name="vlan4040", mac="bb:bb:bb:bb:bb:bb"),
        ]
        assert _pick_oob_interface(ifs).name == "eth0"

    def test_falls_back_to_first_as_last_resort(self):
        ifs = [DpuInterface(name="something", mac="aa:aa:aa:aa:aa:aa")]
        assert _pick_oob_interface(ifs).name == "something"

    def test_empty_list_returns_none(self):
        assert _pick_oob_interface([]) is None
