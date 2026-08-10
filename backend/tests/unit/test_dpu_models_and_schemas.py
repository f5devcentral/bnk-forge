"""Smoke tests for DPU Provisioning models + schemas.

Checks: models importable, tables creatable on SQLite, uniqueness
constraints work, schemas round-trip as expected, and defaults apply.
"""

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import models  # noqa: F401 — registers all models on Base
from database import Base
from models.dpu import BluefieldSoftwareImage, Dpu, ProjectDpuSettings
from models.project import Project
from schemas.dpu import (
    DEFAULT_DNS_SERVERS,
    BluefieldSoftwareImageCreate,
    DpuCreate,
    ProjectDpuSettingsResponse,
    ProjectDpuSettingsUpdate,
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


class TestBluefieldSoftwareImage:
    def test_create_with_doca_identity(self, db: Session):
        db.add(BluefieldSoftwareImage(
            doca_version="2.9.2",
            host_os="ubuntu",
            host_arch="arm64",
            image_filename="bf-bundle-2.9.2-32_25.02_ubuntu-22.04_prod.bfb",
            base_url="https://content.mellanox.com/BlueField/BFBs/Ubuntu22.04/",
            doca_package="doca-all",
        ))
        db.commit()
        assert db.query(BluefieldSoftwareImage).count() == 1

    def test_image_filename_nullable(self, db: Session):
        """image_filename is now optional metadata."""
        db.add(BluefieldSoftwareImage(
            doca_version="3.2.1",
            host_os="rhel",
            host_arch="amd64",
        ))
        db.commit()
        row = db.query(BluefieldSoftwareImage).first()
        assert row.image_filename is None
        assert row.base_url is None
        assert row.doca_version == "3.2.1"

    def test_doca_fields_nullable(self, db: Session):
        """Backward compat: rows without DOCA fields are valid at model level."""
        db.add(BluefieldSoftwareImage())
        db.commit()
        row = db.query(BluefieldSoftwareImage).first()
        assert row.doca_version is None
        # doca_package defaults to "doca-all" in the model
        assert row.doca_package == "doca-all"
        assert row.is_default is False


class TestProjectDpuSettings:
    def test_one_settings_row_per_project(self, db: Session, project: Project):
        db.add(ProjectDpuSettings(project_id=project.id))
        db.commit()
        assert db.query(ProjectDpuSettings).filter_by(project_id=project.id).count() == 1

        db.add(ProjectDpuSettings(project_id=project.id))
        with pytest.raises(IntegrityError):
            db.commit()


class TestDpu:
    def test_bmc_ip_unique_per_project(self, db: Session, project: Project):
        db.add(Dpu(project_id=project.id, bmc_ip="192.168.68.100"))
        db.commit()

        db.add(Dpu(project_id=project.id, bmc_ip="192.168.68.100"))
        with pytest.raises(IntegrityError):
            db.commit()

    def test_same_bmc_ip_allowed_across_projects(self, db: Session, project: Project):
        p2 = Project(name="test-proj-2", description="test 2")
        db.add(p2)
        db.commit()
        db.refresh(p2)

        db.add(Dpu(project_id=project.id, bmc_ip="192.168.68.100"))
        db.add(Dpu(project_id=p2.id, bmc_ip="192.168.68.100"))
        db.commit()  # should succeed

        assert db.query(Dpu).count() == 2

    def test_oob0_ipv4_default_is_dhcp(self, db: Session, project: Project):
        d = Dpu(project_id=project.id, bmc_ip="192.168.68.101")
        db.add(d)
        db.commit()
        db.refresh(d)
        assert d.oob0_ipv4 == "dhcp"


class TestSchemas:
    def test_image_create_requires_doca_version_os_arch(self):
        # valid — only the identity triple is required
        BluefieldSoftwareImageCreate(
            doca_version="2.9.2",
            host_os="ubuntu",
            host_arch="arm64",
        )
        # missing host_os
        with pytest.raises(ValidationError):
            BluefieldSoftwareImageCreate(doca_version="2.9.2", host_arch="arm64")  # type: ignore[call-arg]
        # missing doca_version
        with pytest.raises(ValidationError):
            BluefieldSoftwareImageCreate(host_os="ubuntu", host_arch="arm64")  # type: ignore[call-arg]

    def test_dpu_create_defaults_oob0_to_dhcp(self):
        c = DpuCreate(bmc_ip="10.0.0.1")
        assert c.oob0_ipv4 == "dhcp"

    def test_dpu_settings_response_default_dns(self):
        # If the DB row has no dns_servers set, the schema default applies.
        r = ProjectDpuSettingsResponse(
            id=1, project_id=1,
            bluefield_image_id=None,
            high_speed_mtu=None,
            default_oob_username=None,
            default_os_username=None,
            has_default_oob_password=False,
            has_default_os_password=False,
            created_at="2026-04-18T00:00:00Z",
            updated_at="2026-04-18T00:00:00Z",
        )
        assert r.dns_servers == DEFAULT_DNS_SERVERS

    def test_settings_update_accepts_partial(self):
        u = ProjectDpuSettingsUpdate(high_speed_mtu=9000)
        assert u.high_speed_mtu == 9000
        assert u.dns_servers is None
