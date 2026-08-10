"""Unit tests for BluefieldImageService (unified DOCA release catalog).

The DOCA catalog is keyed on (doca_version, host_os, host_os_version, host_arch).
image_filename and base_url are optional metadata.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import models  # noqa: F401 — registers all models on Base
from core.errors import NotFoundError, ValidationError
from database import Base
from schemas.dpu import BluefieldSoftwareImageCreate, BluefieldSoftwareImageUpdate
from services.bluefield_image_service import BluefieldImageService


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
def service(db: Session) -> BluefieldImageService:
    return BluefieldImageService(db)


class TestCreate:
    def test_creates_image_with_doca_identity(self, service: BluefieldImageService, db: Session):
        img = service.create_image(
            BluefieldSoftwareImageCreate(
                doca_version="3.2.1",
                host_os="ubuntu",
                host_arch="arm64",
                image_filename="bf-bundle-3.2.1-34_25.11_ubuntu-24.04_64k_prod.bfb",
                base_url="https://content.mellanox.com/BlueField/BFBs/Ubuntu24.04/",
                doca_package="doca-all",
                doca_host_url="https://www.mellanox.com/downloads/DOCA/DOCA_v3.2.1/host/doca-host_3.2.1-012101-24.10-ubuntu2404_arm64.deb",
                notes="New image",
            )
        )
        db.commit()
        assert img.id is not None
        assert img.notes == "New image"
        assert img.doca_version == "3.2.1"
        assert img.doca_package == "doca-all"
        assert img.host_os == "ubuntu"
        assert img.host_arch == "arm64"
        assert img.doca_host_url == "https://www.mellanox.com/downloads/DOCA/DOCA_v3.2.1/host/doca-host_3.2.1-012101-24.10-ubuntu2404_arm64.deb"

    def test_creates_image_without_optional_filename(self, service: BluefieldImageService, db: Session):
        img = service.create_image(
            BluefieldSoftwareImageCreate(
                doca_version="2.9.2",
                host_os="rhel",
                host_arch="amd64",
            )
        )
        db.commit()
        assert img.id is not None
        assert img.image_filename is None
        assert img.base_url is None

    def test_duplicate_doca_release_raises_validation_error(
        self, service: BluefieldImageService, db: Session
    ):
        service.create_image(BluefieldSoftwareImageCreate(
            doca_version="2.9.2", host_os="ubuntu", host_os_version="22.04", host_arch="arm64",
        ))
        db.commit()
        with pytest.raises(ValidationError):
            service.create_image(BluefieldSoftwareImageCreate(
                doca_version="2.9.2", host_os="ubuntu", host_os_version="22.04", host_arch="arm64",
            ))

    def test_same_version_different_os_arch_allowed(
        self, service: BluefieldImageService, db: Session
    ):
        service.create_image(BluefieldSoftwareImageCreate(
            doca_version="2.9.2", host_os="ubuntu", host_os_version="22.04", host_arch="arm64",
        ))
        service.create_image(BluefieldSoftwareImageCreate(
            doca_version="2.9.2", host_os="ubuntu", host_os_version="22.04", host_arch="amd64",
        ))
        service.create_image(BluefieldSoftwareImageCreate(
            doca_version="2.9.2", host_os="rhel", host_os_version="9.4", host_arch="arm64",
        ))
        db.commit()
        assert len(service.list_images()) == 3

    def test_same_version_different_os_version_allowed(
        self, service: BluefieldImageService, db: Session
    ):
        """The unique constraint includes host_os_version, so the same DOCA
        version on different OS releases is allowed."""
        service.create_image(BluefieldSoftwareImageCreate(
            doca_version="2.9.2", host_os="ubuntu", host_os_version="22.04", host_arch="arm64",
        ))
        service.create_image(BluefieldSoftwareImageCreate(
            doca_version="2.9.2", host_os="ubuntu", host_os_version="24.04", host_arch="arm64",
        ))
        db.commit()
        assert len(service.list_images()) == 2

    def test_create_with_is_default_clears_previous(
        self, service: BluefieldImageService, db: Session
    ):
        a = service.create_image(BluefieldSoftwareImageCreate(
            doca_version="2.9.2", host_os="ubuntu", host_arch="arm64", is_default=True,
        ))
        db.commit()
        assert a.is_default is True
        b = service.create_image(BluefieldSoftwareImageCreate(
            doca_version="3.2.1", host_os="ubuntu", host_arch="arm64", is_default=True,
        ))
        db.commit()
        db.refresh(a)
        assert a.is_default is False
        assert b.is_default is True


class TestRead:
    def test_list_returns_sorted_by_doca_version_desc(self, service: BluefieldImageService, db: Session):
        service.create_image(BluefieldSoftwareImageCreate(
            doca_version="2.9.2", host_os="ubuntu", host_arch="arm64",
        ))
        service.create_image(BluefieldSoftwareImageCreate(
            doca_version="3.2.1", host_os="ubuntu", host_arch="arm64",
        ))
        db.commit()
        images = service.list_images()
        assert [i.doca_version for i in images] == ["3.2.1", "2.9.2"]

    def test_get_missing_raises(self, service: BluefieldImageService):
        with pytest.raises(NotFoundError):
            service.get_image(999)

    def test_get_default_returns_none_when_no_default(self, service: BluefieldImageService, db: Session):
        service.create_image(BluefieldSoftwareImageCreate(
            doca_version="2.9.2", host_os="ubuntu", host_arch="arm64", is_default=False,
        ))
        db.commit()
        assert service.get_default() is None

    def test_get_default_returns_default_row(self, service: BluefieldImageService, db: Session):
        service.create_image(BluefieldSoftwareImageCreate(
            doca_version="2.9.2", host_os="ubuntu", host_arch="arm64", is_default=False,
        ))
        service.create_image(BluefieldSoftwareImageCreate(
            doca_version="3.2.1", host_os="ubuntu", host_arch="arm64", is_default=True,
        ))
        db.commit()
        default = service.get_default()
        assert default is not None
        assert default.doca_version == "3.2.1"


class TestUpdate:
    def test_partial_update_leaves_untouched_fields(
        self, service: BluefieldImageService, db: Session
    ):
        img = service.create_image(BluefieldSoftwareImageCreate(
            doca_version="2.9.2",
            host_os="ubuntu",
            host_arch="arm64",
            image_filename="x.bfb",
            base_url="https://a/",
            notes="original",
        ))
        db.commit()
        service.update_image(img.id, BluefieldSoftwareImageUpdate(notes="updated"))
        db.commit()
        refreshed = service.get_image(img.id)
        assert refreshed.notes == "updated"
        assert refreshed.image_filename == "x.bfb"
        assert refreshed.base_url == "https://a/"

    def test_update_to_existing_doca_release_raises(
        self, service: BluefieldImageService, db: Session
    ):
        a = service.create_image(BluefieldSoftwareImageCreate(
            doca_version="2.9.2", host_os="ubuntu", host_os_version="22.04", host_arch="arm64",
        ))
        service.create_image(BluefieldSoftwareImageCreate(
            doca_version="3.2.1", host_os="ubuntu", host_os_version="22.04", host_arch="arm64",
        ))
        db.commit()
        with pytest.raises(ValidationError):
            service.update_image(a.id, BluefieldSoftwareImageUpdate(doca_version="3.2.1"))

    def test_update_is_default_clears_previous(
        self, service: BluefieldImageService, db: Session
    ):
        a = service.create_image(BluefieldSoftwareImageCreate(
            doca_version="2.9.2", host_os="ubuntu", host_arch="arm64", is_default=True,
        ))
        b = service.create_image(BluefieldSoftwareImageCreate(
            doca_version="3.2.1", host_os="ubuntu", host_arch="arm64", is_default=False,
        ))
        db.commit()
        service.update_image(b.id, BluefieldSoftwareImageUpdate(is_default=True))
        db.commit()
        db.refresh(a)
        assert a.is_default is False
        assert b.is_default is True


class TestDelete:
    def test_delete_removes_row(self, service: BluefieldImageService, db: Session):
        img = service.create_image(BluefieldSoftwareImageCreate(
            doca_version="2.9.2", host_os="ubuntu", host_arch="arm64",
        ))
        db.commit()
        service.delete_image(img.id)
        db.commit()
        assert service.list_images() == []

    def test_delete_missing_raises(self, service: BluefieldImageService):
        with pytest.raises(NotFoundError):
            service.delete_image(999)
