"""Unit tests for DpuService + ProjectDpuSettingsService.

Covers CRUD paths, credential encryption, uniqueness, defaults, and that
plaintext credentials never leak into responses.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import models  # noqa: F401 — registers Base
from core.encryption import decrypt_value
from core.errors import NotFoundError, ValidationError
from database import Base
from models.dpu import Dpu, ProjectDpuSettings
from models.project import Project
from schemas.dpu import (
    DEFAULT_DNS_SERVERS,
    DpuCreate,
    DpuUpdate,
    ProjectDpuSettingsUpdate,
)
from services.dpu_service import (
    DpuService,
    ProjectDpuSettingsService,
    _derive_link_mode,
    _link_type_from_mlxconfig,
)


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def project(db: Session) -> Project:
    p = Project(name="test-proj", description="test")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@pytest.fixture
def dpu_svc(db: Session) -> DpuService:
    return DpuService(db)


@pytest.fixture
def settings_svc(db: Session) -> ProjectDpuSettingsService:
    return ProjectDpuSettingsService(db)


class TestDpuCreate:
    def test_minimal_create(self, dpu_svc, db, project):
        r = dpu_svc.create_dpu(project.id, DpuCreate(bmc_ip="192.168.68.100"))
        db.commit()
        assert r.bmc_ip == "192.168.68.100"
        assert r.oob0_ipv4 == "dhcp"
        assert r.has_bmc_password is False
        assert r.bmc_username is None

    def test_encrypts_bmc_credentials(self, dpu_svc, db, project):
        r = dpu_svc.create_dpu(
            project.id,
            DpuCreate(bmc_ip="10.0.0.1", bmc_username="root", bmc_password="s3cret"),
        )
        db.commit()
        assert r.has_bmc_password is True
        assert r.bmc_username == "root"

        # Plaintext password never appears on response
        dumped = r.model_dump()
        assert "bmc_password" not in dumped
        assert "bmc_password_encrypted" not in dumped

        # Value IS encrypted in DB (decrypts to original)
        row = db.get(Dpu, r.id)
        assert row.bmc_password_encrypted != "s3cret"
        assert decrypt_value(row.bmc_password_encrypted) == "s3cret"

    def test_duplicate_bmc_ip_in_project_raises(self, dpu_svc, db, project):
        dpu_svc.create_dpu(project.id, DpuCreate(bmc_ip="10.0.0.1"))
        db.commit()
        with pytest.raises(ValidationError):
            dpu_svc.create_dpu(project.id, DpuCreate(bmc_ip="10.0.0.1"))

    def test_same_bmc_ip_across_projects_ok(self, dpu_svc, db, project):
        p2 = Project(name="p2", description="")
        db.add(p2)
        db.commit()
        db.refresh(p2)
        dpu_svc.create_dpu(project.id, DpuCreate(bmc_ip="10.0.0.1"))
        dpu_svc.create_dpu(p2.id, DpuCreate(bmc_ip="10.0.0.1"))
        db.commit()
        assert dpu_svc.list_dpus(project.id).dpus[0].project_id == project.id
        assert dpu_svc.list_dpus(p2.id).dpus[0].project_id == p2.id

    def test_vlan_fields_persist(self, dpu_svc, db, project):
        r = dpu_svc.create_dpu(
            project.id,
            DpuCreate(
                bmc_ip="10.0.0.2",
                external_vlan_id=100, external_vlan_ipv4="10.1.0.0/24",
                internal_vlan_id=200, internal_vlan_ipv6="fd00:1::/64",
                extra_vlan_interfaces=[
                    {"name": "storage", "vlan_id": 300,
                     "ipv4": "10.3.0.0/24", "ipv6": "fd00:3::/64"},
                ],
            ),
        )
        db.commit()
        assert r.external_vlan_id == 100
        assert r.internal_vlan_ipv6 == "fd00:1::/64"
        storage = next(e for e in r.extra_vlan_interfaces if e.name == "storage")
        assert storage.ipv4 == "10.3.0.0/24"


class TestDpuAutoPopulateFromSubnets:
    """When project settings have VLAN subnets, new DPUs get auto-assigned IPs."""

    def test_populates_ipv4_from_project_subnet(self, dpu_svc, settings_svc, db, project):
        settings_svc.update(
            project.id,
            ProjectDpuSettingsUpdate(
                external_vlan_ipv4_subnet="10.1.0.0/24",
                internal_vlan_ipv4_subnet="10.2.0.0/24",
            ),
        )
        db.commit()

        r1 = dpu_svc.create_dpu(project.id, DpuCreate(bmc_ip="192.168.1.101"))
        r2 = dpu_svc.create_dpu(project.id, DpuCreate(bmc_ip="192.168.1.102"))
        db.commit()

        # Ordinals are 1 and 2 → .1 and .2 hosts.
        assert r1.external_vlan_ipv4 == "10.1.0.1/24"
        assert r1.internal_vlan_ipv4 == "10.2.0.1/24"
        assert r2.external_vlan_ipv4 == "10.1.0.2/24"
        assert r2.internal_vlan_ipv4 == "10.2.0.2/24"

    def test_populates_ipv6_from_project_subnet(self, dpu_svc, settings_svc, db, project):
        settings_svc.update(
            project.id,
            ProjectDpuSettingsUpdate(external_vlan_ipv6_subnet="fd00:1::/64"),
        )
        db.commit()

        r = dpu_svc.create_dpu(project.id, DpuCreate(bmc_ip="10.0.0.1"))
        db.commit()
        assert r.external_vlan_ipv6 == "fd00:1::1/64"

    def test_caller_supplied_ip_overrides_subnet_suggestion(self, dpu_svc, settings_svc, db, project):
        settings_svc.update(
            project.id, ProjectDpuSettingsUpdate(external_vlan_ipv4_subnet="10.1.0.0/24")
        )
        db.commit()

        r = dpu_svc.create_dpu(
            project.id,
            DpuCreate(bmc_ip="10.0.0.1", external_vlan_ipv4="10.9.9.9/24"),
        )
        db.commit()
        assert r.external_vlan_ipv4 == "10.9.9.9/24"

    def test_no_subnet_no_populate(self, dpu_svc, db, project):
        r = dpu_svc.create_dpu(project.id, DpuCreate(bmc_ip="10.0.0.1"))
        db.commit()
        assert r.external_vlan_ipv4 is None
        assert r.internal_vlan_ipv4 is None

    def test_malformed_subnet_silently_skipped(self, dpu_svc, settings_svc, db, project):
        settings_svc.update(
            project.id,
            ProjectDpuSettingsUpdate(external_vlan_ipv4_subnet="not-an-ip"),
        )
        db.commit()
        # Should not raise; field left blank.
        r = dpu_svc.create_dpu(project.id, DpuCreate(bmc_ip="10.0.0.1"))
        db.commit()
        assert r.external_vlan_ipv4 is None


class TestResetDpu:
    """Redfish ComputerSystem.Reset dispatch."""

    def test_posts_reset_with_valid_type(self, dpu_svc, db, project, monkeypatch):
        r = dpu_svc.create_dpu(
            project.id,
            DpuCreate(bmc_ip="192.0.2.50", bmc_username="root", bmc_password="pw"),
        )
        db.commit()

        captured: dict = {}

        class _Resp:
            status_code = 204
            text = ""

        def fake_post(url, **kwargs):
            captured["url"] = url
            captured["json"] = kwargs.get("json")
            captured["auth"] = kwargs.get("auth")
            return _Resp()

        monkeypatch.setattr(
            "services.dpu_credentials.requests.post", fake_post,
        )

        result = dpu_svc.reset_dpu(project.id, r.id, "ForceRestart")
        assert result["status"] == "initiated"
        assert result["reset_type"] == "ForceRestart"
        assert "ComputerSystem.Reset" in captured["url"]
        assert captured["json"] == {"ResetType": "ForceRestart"}

    def test_rejects_unknown_reset_type(self, dpu_svc, db, project):
        r = dpu_svc.create_dpu(project.id, DpuCreate(bmc_ip="192.0.2.50"))
        db.commit()
        with pytest.raises(ValidationError):
            dpu_svc.reset_dpu(project.id, r.id, "SelfDestruct")

    def test_missing_creds_errors(self, dpu_svc, db, project):
        # No DPU creds + no project defaults → BadRequestError
        r = dpu_svc.create_dpu(project.id, DpuCreate(bmc_ip="192.0.2.50"))
        db.commit()
        from core.errors import BadRequestError
        with pytest.raises(BadRequestError, match="No BMC password"):
            dpu_svc.reset_dpu(project.id, r.id, "ForceRestart")

    def test_http_error_bubbles_up(self, dpu_svc, db, project, monkeypatch):
        r = dpu_svc.create_dpu(
            project.id,
            DpuCreate(bmc_ip="192.0.2.50", bmc_username="root", bmc_password="pw"),
        )
        db.commit()

        class _Resp:
            status_code = 500
            text = "Internal Server Error"

        monkeypatch.setattr(
            "services.dpu_credentials.requests.post", lambda url, **k: _Resp(),
        )

        from core.errors import BadRequestError
        with pytest.raises(BadRequestError, match="rejected by BMC"):
            dpu_svc.reset_dpu(project.id, r.id, "PowerCycle")

    def test_retries_with_default_password_on_401(
        self, dpu_svc, db, project, monkeypatch
    ):
        r = dpu_svc.create_dpu(
            project.id,
            DpuCreate(bmc_ip="192.0.2.50", bmc_username="root", bmc_password="wrong"),
        )
        db.commit()

        attempts: list[str] = []

        def fake_post(url, **kwargs):  # noqa: ARG001
            auth = kwargs.get("auth")
            pw = getattr(auth, "password", None)
            attempts.append(pw)

            class _Resp:
                status_code = 401 if pw == "wrong" else 204
                text = ""
            return _Resp()

        monkeypatch.setattr("services.dpu_credentials.requests.post", fake_post)
        # Stub the Redfish password-reset that fires on 0penBmc fallback so
        # the test doesn't hit the network.
        monkeypatch.setattr(
            "services.dpu_credentials.patch_bmc_root_password",
            lambda *a, **kw: None,
        )
        dpu_svc.reset_dpu(project.id, r.id, "GracefulRestart")
        assert attempts == ["wrong", "0penBmc"]


class TestRevealPasswords:
    """Operator-side reveal endpoints return plaintext passwords."""

    def test_reveal_dpu_bmc_password(self, dpu_svc, db, project):
        r = dpu_svc.create_dpu(
            project.id, DpuCreate(bmc_ip="1.1.1.1", bmc_password="s3cret"),
        )
        db.commit()
        assert dpu_svc.reveal_bmc_password(project.id, r.id) == "s3cret"

    def test_reveal_dpu_bmc_password_missing_raises(self, dpu_svc, db, project):
        r = dpu_svc.create_dpu(project.id, DpuCreate(bmc_ip="1.1.1.1"))
        db.commit()
        with pytest.raises(NotFoundError):
            dpu_svc.reveal_bmc_password(project.id, r.id)

    def test_reveal_default_oob_password(self, settings_svc, db, project):
        settings_svc.update(
            project.id, ProjectDpuSettingsUpdate(default_oob_password="oobpw"),
        )
        db.commit()
        assert settings_svc.reveal_default_oob_password(project.id) == "oobpw"

    def test_reveal_default_os_password(self, settings_svc, db, project):
        settings_svc.update(
            project.id, ProjectDpuSettingsUpdate(default_os_password="ospw"),
        )
        db.commit()
        assert settings_svc.reveal_default_os_password(project.id) == "ospw"

    def test_reveal_default_missing_raises(self, settings_svc, project):
        with pytest.raises(NotFoundError):
            settings_svc.reveal_default_oob_password(project.id)
        with pytest.raises(NotFoundError):
            settings_svc.reveal_default_os_password(project.id)


class TestAutoAssignVlanIps:
    """auto_assign_vlan_ips re-numbers every DPU deterministically."""

    def test_assigns_per_dpu_ips_starting_at_start_host(
        self, dpu_svc, settings_svc, db, project
    ):
        settings_svc.update(
            project.id,
            ProjectDpuSettingsUpdate(
                external_vlan_ipv4_subnet="10.10.20.0/24",
                external_vlan_ipv6_subnet="fd00:20::/64",
                external_vlan_start_host=100,
                internal_vlan_ipv4_subnet="10.10.10.0/24",
                internal_vlan_start_host=50,
                # storage: subnet unset → skipped
            ),
        )
        db.commit()

        dpu_svc.create_dpu(project.id, DpuCreate(bmc_ip="1.1.1.1"))
        dpu_svc.create_dpu(project.id, DpuCreate(bmc_ip="1.1.1.2"))
        dpu_svc.create_dpu(project.id, DpuCreate(bmc_ip="1.1.1.3"))
        db.commit()

        result, count = dpu_svc.auto_assign_vlan_ips(project.id)
        db.commit()

        dpus = result.dpus
        assert dpus[0].external_vlan_ipv4 == "10.10.20.100/24"
        assert dpus[1].external_vlan_ipv4 == "10.10.20.101/24"
        assert dpus[2].external_vlan_ipv4 == "10.10.20.102/24"
        assert dpus[0].external_vlan_ipv6 == "fd00:20::64/64"  # 100 = 0x64
        assert dpus[2].external_vlan_ipv6 == "fd00:20::66/64"  # 102 = 0x66
        assert dpus[0].internal_vlan_ipv4 == "10.10.10.50/24"
        assert dpus[2].internal_vlan_ipv4 == "10.10.10.52/24"
        # No storage subnet → no extras present on any DPU
        assert dpus[0].extra_vlan_interfaces == []
        # 3 DPUs × (ext-v4 + ext-v6 + int-v4) = 9 assignments
        assert count == 9

    def test_defaults_start_host_to_100_when_unset(
        self, dpu_svc, settings_svc, db, project
    ):
        settings_svc.update(
            project.id,
            ProjectDpuSettingsUpdate(external_vlan_ipv4_subnet="10.0.0.0/24"),
        )
        db.commit()
        dpu_svc.create_dpu(project.id, DpuCreate(bmc_ip="1.1.1.1"))
        db.commit()

        result, count = dpu_svc.auto_assign_vlan_ips(project.id)
        db.commit()
        assert result.dpus[0].external_vlan_ipv4 == "10.0.0.100/24"
        assert count == 1

    def test_overwrites_existing_per_dpu_ips(self, dpu_svc, settings_svc, db, project):
        settings_svc.update(
            project.id,
            ProjectDpuSettingsUpdate(
                external_vlan_ipv4_subnet="10.10.20.0/24",
                external_vlan_start_host=200,
            ),
        )
        db.commit()
        # Create a DPU with a manually-set external IP — auto-assign should override.
        dpu_svc.create_dpu(
            project.id,
            DpuCreate(bmc_ip="1.1.1.1", external_vlan_ipv4="10.99.99.99/24"),
        )
        db.commit()
        result, count = dpu_svc.auto_assign_vlan_ips(project.id)
        db.commit()
        assert result.dpus[0].external_vlan_ipv4 == "10.10.20.200/24"
        assert count == 1

    def test_no_settings_row_is_noop_not_error(self, dpu_svc, db, project):
        # No settings row at all → return count=0, no exception.
        dpu_svc.create_dpu(project.id, DpuCreate(bmc_ip="1.1.1.1"))
        db.commit()
        result, count = dpu_svc.auto_assign_vlan_ips(project.id)
        assert count == 0
        assert len(result.dpus) == 1

    def test_get_or_create_seeds_usable_defaults(self, settings_svc, dpu_svc, db, project):
        # Opening the DPU tab creates the settings row with sensible defaults;
        # Auto-assign must work immediately after.
        settings_svc.get_or_create(project.id)
        dpu_svc.create_dpu(project.id, DpuCreate(bmc_ip="1.1.1.1"))
        db.commit()

        _, count = dpu_svc.auto_assign_vlan_ips(project.id)
        db.commit()
        # external + internal each have IPv4 → 2 assignments per DPU
        assert count == 2


class TestDpuUpdate:
    def test_partial_update_leaves_other_fields(self, dpu_svc, db, project):
        r = dpu_svc.create_dpu(
            project.id, DpuCreate(bmc_ip="10.0.0.1", name="dpu-a", bmc_password="orig")
        )
        db.commit()
        dpu_svc.update_dpu(project.id, r.id, DpuUpdate(name="dpu-b"))
        db.commit()
        fresh = dpu_svc.get_dpu(project.id, r.id)
        assert fresh.name == "dpu-b"
        assert fresh.bmc_ip == "10.0.0.1"
        assert fresh.has_bmc_password is True  # unchanged

    def test_clear_password_by_sending_empty_string(self, dpu_svc, db, project):
        r = dpu_svc.create_dpu(
            project.id, DpuCreate(bmc_ip="10.0.0.1", bmc_password="s3cret")
        )
        db.commit()
        dpu_svc.update_dpu(project.id, r.id, DpuUpdate(bmc_password=""))
        db.commit()
        fresh = dpu_svc.get_dpu(project.id, r.id)
        assert fresh.has_bmc_password is False

    def test_update_missing_raises(self, dpu_svc, project):
        with pytest.raises(NotFoundError):
            dpu_svc.update_dpu(project.id, 999, DpuUpdate(name="x"))


class TestDpuDelete:
    def test_delete_removes_row(self, dpu_svc, db, project):
        r = dpu_svc.create_dpu(project.id, DpuCreate(bmc_ip="10.0.0.1"))
        db.commit()
        dpu_svc.delete_dpu(project.id, r.id)
        db.commit()
        assert dpu_svc.list_dpus(project.id).dpus == []

    def test_delete_missing_raises(self, dpu_svc, project):
        with pytest.raises(NotFoundError):
            dpu_svc.delete_dpu(project.id, 999)


class TestDpuList:
    def test_list_sorted_by_bmc_ip(self, dpu_svc, db, project):
        dpu_svc.create_dpu(project.id, DpuCreate(bmc_ip="10.0.0.3"))
        dpu_svc.create_dpu(project.id, DpuCreate(bmc_ip="10.0.0.1"))
        dpu_svc.create_dpu(project.id, DpuCreate(bmc_ip="10.0.0.2"))
        db.commit()
        ips = [d.bmc_ip for d in dpu_svc.list_dpus(project.id).dpus]
        assert ips == ["10.0.0.1", "10.0.0.2", "10.0.0.3"]


# ─── ProjectDpuSettings ─────────────────────────────────────────────────────

class TestProjectDpuSettings:
    def test_get_or_create_creates_defaults(self, settings_svc, db, project):
        r = settings_svc.get_or_create(project.id)
        db.commit()
        assert r.project_id == project.id
        # Seeded: standard subnets + VLAN ids, start host 100, uplink=bond0.
        assert r.external_vlan_ipv4_subnet == "10.10.20.0/24"
        assert r.internal_vlan_ipv4_subnet == "10.10.10.0/24"
        assert r.external_vlan_id == 0
        assert r.internal_vlan_id == 100
        assert r.external_vlan_uplink == "bond0"
        assert r.internal_vlan_uplink == "bond0"
        # Extras list starts empty — operator adds storage/vdi/… only as
        # needed.
        assert r.extra_vlan_interfaces == []
        # dns_servers default applies via schema when DB row has none
        assert r.dns_servers == DEFAULT_DNS_SERVERS

    def test_get_or_create_is_idempotent(self, settings_svc, db, project):
        a = settings_svc.get_or_create(project.id)
        db.commit()
        b = settings_svc.get_or_create(project.id)
        assert a.id == b.id

    def test_update_persists_partial_fields(self, settings_svc, db, project):
        settings_svc.get_or_create(project.id)
        db.commit()
        r = settings_svc.update(
            project.id,
            ProjectDpuSettingsUpdate(
                high_speed_mtu=9000,
                dns_servers=["9.9.9.9"],
            ),
        )
        db.commit()
        assert r.high_speed_mtu == 9000
        assert r.dns_servers == ["9.9.9.9"]

    def test_update_encrypts_default_passwords(self, settings_svc, db, project):
        r = settings_svc.update(
            project.id,
            ProjectDpuSettingsUpdate(
                default_oob_username="admin",
                default_oob_password="pw1",
                default_os_password="pw2",
            ),
        )
        db.commit()
        assert r.has_default_oob_password is True
        assert r.has_default_os_password is True
        assert r.default_oob_username == "admin"

        row = (
            db.query(ProjectDpuSettings)
            .filter(ProjectDpuSettings.project_id == project.id)
            .first()
        )
        assert decrypt_value(row.default_oob_password_encrypted) == "pw1"
        assert decrypt_value(row.default_os_password_encrypted) == "pw2"

    def test_update_clear_password_with_empty_string(self, settings_svc, db, project):
        settings_svc.update(
            project.id,
            ProjectDpuSettingsUpdate(default_oob_password="pw1"),
        )
        db.commit()
        r = settings_svc.update(
            project.id, ProjectDpuSettingsUpdate(default_oob_password="")
        )
        db.commit()
        assert r.has_default_oob_password is False

    def test_update_creates_settings_if_missing(self, settings_svc, db, project):
        # Skip get_or_create — update should create the row on demand
        r = settings_svc.update(project.id, ProjectDpuSettingsUpdate(high_speed_mtu=1500))
        db.commit()
        assert r.project_id == project.id
        assert r.high_speed_mtu == 1500

    def test_non_lag_rejects_extras(self, settings_svc, db, project):
        # Non-LAG path (uplinks via p0/p1) doesn't support extra VLAN
        # interfaces — the bf.conf template gives each uplink its own
        # OVS bridge with no trunk. Force operators to LAG if they need
        # more than one VLAN per uplink.
        from core.errors import BadRequestError
        with pytest.raises(BadRequestError, match="extra VLAN"):
            settings_svc.update(
                project.id,
                ProjectDpuSettingsUpdate(
                    external_vlan_uplink="p0",
                    internal_vlan_uplink="p1",
                    extra_vlan_interfaces=[
                        {"name": "storage", "vlan_id": 200, "uplink": "p0"},
                    ],
                ),
            )

    def test_non_lag_rejects_router_on_a_stick(self, settings_svc, db, project):
        # Both VLANs assigned to the same physical uplink is router-on-
        # a-stick — also unsupported in non-LAG mode.
        from core.errors import BadRequestError
        with pytest.raises(BadRequestError, match="unique uplink port"):
            settings_svc.update(
                project.id,
                ProjectDpuSettingsUpdate(
                    external_vlan_uplink="p0",
                    internal_vlan_uplink="p0",
                ),
            )

    def test_non_lag_accepts_distinct_p0_p1(self, settings_svc, db, project):
        r = settings_svc.update(
            project.id,
            ProjectDpuSettingsUpdate(
                external_vlan_uplink="p0",
                internal_vlan_uplink="p1",
            ),
        )
        db.commit()
        assert r.external_vlan_uplink == "p0"
        assert r.internal_vlan_uplink == "p1"

    def test_save_rejects_one_uplink_without_the_other(
        self, settings_svc, db, project,
    ):
        # External set, Internal blank → must error. Regression for the
        # bug where the UI let operators save a half-configured project
        # that produced broken bf.conf output at flash time.
        from core.errors import BadRequestError
        with pytest.raises(BadRequestError, match="must have an uplink"):
            settings_svc.update(
                project.id,
                ProjectDpuSettingsUpdate(external_vlan_uplink="p0"),
            )


# ── Phase 1: link mode (IB / Eth / mixed) ────────────────────────────────────


class TestDeriveLinkMode:
    """The DpuResponse.link_mode field drives the IB chip in the DPU tab.

    Sourced from the mlxconfig snapshot the in-band probe captures
    (BMC-only DPUs return None until later phases wire up a BMC-side
    LINK_TYPE source — None means "unknown" and the UI shows no chip).
    """

    def test_link_type_parser_handles_named_and_numeric_forms(self):
        # mlxconfig prints "ETH(2)" / "IB(1)" today; older firmware
        # emits the bare numeric. Both must classify the same way.
        assert _link_type_from_mlxconfig("ETH(2)") == "eth"
        assert _link_type_from_mlxconfig("IB(1)") == "ib"
        assert _link_type_from_mlxconfig("2") == "eth"
        assert _link_type_from_mlxconfig("1") == "ib"

    def test_link_type_parser_unknown_values_return_none(self):
        # Anything we can't classify confidently must drop to None so
        # the aggregate doesn't claim a mode it doesn't actually know.
        assert _link_type_from_mlxconfig(None) is None
        assert _link_type_from_mlxconfig("") is None
        assert _link_type_from_mlxconfig("VPI(3)") is None  # not yet supported

    def test_eth_when_both_ports_eth(self):
        payload = {"mlxconfig_settings": {"LINK_TYPE_P1": "ETH(2)", "LINK_TYPE_P2": "ETH(2)"}}
        assert _derive_link_mode(payload) == "eth"

    def test_ib_when_both_ports_ib(self):
        payload = {"mlxconfig_settings": {"LINK_TYPE_P1": "IB(1)", "LINK_TYPE_P2": "IB(1)"}}
        assert _derive_link_mode(payload) == "ib"

    def test_mixed_when_ports_differ(self):
        payload = {"mlxconfig_settings": {"LINK_TYPE_P1": "ETH(2)", "LINK_TYPE_P2": "IB(1)"}}
        assert _derive_link_mode(payload) == "mixed"

    def test_single_known_port_still_classifies(self):
        # If only one LINK_TYPE entry exists (older mlxconfig output), use it.
        payload = {"mlxconfig_settings": {"LINK_TYPE_P1": "IB(1)"}}
        assert _derive_link_mode(payload) == "ib"

    def test_no_mlxconfig_returns_none(self):
        # BMC-only DPU (no in-band probe yet) → None, UI shows no chip.
        assert _derive_link_mode({"bios": {"nic_mode": "Trusted"}}) is None
        assert _derive_link_mode({}) is None
        assert _derive_link_mode(None) is None

    def test_unparseable_link_type_falls_through_to_none(self):
        payload = {"mlxconfig_settings": {"LINK_TYPE_P1": "WUT(99)", "LINK_TYPE_P2": ""}}
        assert _derive_link_mode(payload) is None


# ── Register Host from DPU tab ─────────────────────────────────────────────


class TestRegisterHostFromDpu:
    """The Action menu's Register Host item creates a Bare Metal row from
    an in-band DPU's host fields. Idempotent on (project, host_ip) so
    multiple DPUs sharing a host all succeed silently.
    """

    def _make_inband(
        self, dpu_svc, db, project, *,
        host_node_ip="10.0.0.10",
        host_hostname="worker-a",
        ssh_credential_id=None,
        host_ssh_username=None,
        host_ssh_password_encrypted=None,
    ):
        from core.encryption import encrypt_value
        from models.dpu import Dpu
        d = Dpu(
            project_id=project.id,
            name="test-dpu",
            access_mode="in-band",
            host_node_ip=host_node_ip,
            host_hostname=host_hostname,
            host_ssh_credential_id=ssh_credential_id,
            host_ssh_username=host_ssh_username,
            host_ssh_password_encrypted=(
                encrypt_value(host_ssh_password_encrypted)
                if host_ssh_password_encrypted else None
            ),
            host_ssh_port=22,
            host_ssh_auth_type="password" if host_ssh_password_encrypted else None,
            oob0_ipv4="dhcp",
        )
        db.add(d)
        db.commit()
        db.refresh(d)
        return d

    def test_creates_bare_metal_host_with_inline_creds(
        self, dpu_svc, db, project,
    ):
        from models.bare_metal import BareMetalHost
        from models.ssh_credential import SSHCredential

        d = self._make_inband(
            dpu_svc, db, project,
            host_ssh_username="ubuntu",
            host_ssh_password_encrypted="hostpw",
        )
        result = dpu_svc.register_host_from_dpu(project.id, d.id)
        db.commit()
        assert result["status"] == "created"
        assert result["host_ip"] == "10.0.0.10"
        # A SSHCredential row was synthesised because the DPU only had
        # inline creds — BareMetalHost can't reference inline fields.
        host = db.get(BareMetalHost, result["host_id"])
        cred = db.get(SSHCredential, host.ssh_credential_id)
        assert cred is not None
        assert cred.username == "ubuntu"
        assert cred.name.startswith("Discovery: worker-a")

    def test_reuses_saved_credential_when_present(
        self, dpu_svc, db, project,
    ):
        # When the DPU points at a saved SSHCredential, no synthetic row
        # — we keep referencing the existing one.
        from core.encryption import encrypt_value
        from models.bare_metal import BareMetalHost
        from models.ssh_credential import SSHCredential

        cred = SSHCredential(
            name="lab-key",
            host="placeholder",
            port=22,
            username="ubuntu",
            auth_type="password",
            password_encrypted=encrypt_value("pw"),
        )
        db.add(cred)
        db.commit()
        db.refresh(cred)

        d = self._make_inband(
            dpu_svc, db, project, ssh_credential_id=cred.id,
        )
        result = dpu_svc.register_host_from_dpu(project.id, d.id)
        db.commit()
        host = db.get(BareMetalHost, result["host_id"])
        assert host.ssh_credential_id == cred.id

    def test_idempotent_for_two_dpus_sharing_a_host(
        self, dpu_svc, db, project,
    ):
        # Two in-band DPUs share the same host. Registering the host
        # from each must end up pointing at the SAME bare-metal row.
        d1 = self._make_inband(
            dpu_svc, db, project,
            host_node_ip="10.0.0.20", host_hostname="worker-x",
            host_ssh_username="ubuntu", host_ssh_password_encrypted="pw",
        )
        d2 = self._make_inband(
            dpu_svc, db, project,
            host_node_ip="10.0.0.20", host_hostname="worker-x",
            host_ssh_username="ubuntu", host_ssh_password_encrypted="pw",
        )
        first = dpu_svc.register_host_from_dpu(project.id, d1.id)
        db.commit()
        second = dpu_svc.register_host_from_dpu(project.id, d2.id)
        db.commit()
        assert first["status"] == "created"
        assert second["status"] == "exists"
        assert first["host_id"] == second["host_id"]

    def test_rejects_bmc_mode_dpu(self, dpu_svc, db, project):
        from models.dpu import Dpu
        d = Dpu(
            project_id=project.id, name="bmc-dpu",
            access_mode="bmc", bmc_ip="192.0.2.10", oob0_ipv4="dhcp",
        )
        db.add(d)
        db.commit()
        db.refresh(d)
        with pytest.raises(ValueError, match="in-band"):
            try:
                dpu_svc.register_host_from_dpu(project.id, d.id)
            except Exception as exc:  # core.errors.BadRequestError
                # Re-raise as ValueError so pytest.raises matches the
                # message regardless of which custom exception class
                # the project happens to use.
                raise ValueError(str(exc)) from exc

    def test_rejects_inband_dpu_without_host_ip(self, dpu_svc, db, project):
        from core.errors import BadRequestError
        d = self._make_inband(dpu_svc, db, project, host_node_ip=None)
        with pytest.raises(BadRequestError, match="host_node_ip"):
            dpu_svc.register_host_from_dpu(project.id, d.id)
