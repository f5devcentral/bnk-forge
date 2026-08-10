"""
Unit tests for BnkVersionProfileService.

Uses an in-memory SQLite database — no external services required.
"""

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from database import Base
from models.bare_metal import BnkVersionProfile
from services.bare_metal.version_profiles import BNK_21_PROFILE, BNK_22_PROFILE, BnkVersionProfileService

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="function")
def db_session():
    """Create an in-memory SQLite database with the bare-metal schema."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )

    # Enable foreign keys for SQLite
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    # Only create the tables we need for this test
    BnkVersionProfile.__table__.create(engine, checkfirst=True)

    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def service(db_session):
    return BnkVersionProfileService(db=db_session)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestBnkVersionProfileServiceSeed:
    """Tests for seed_profiles()."""

    def test_seed_creates_both_profiles(self, service, db_session):
        count = service.seed_profiles()
        db_session.commit()
        assert count == 2
        profiles = db_session.query(BnkVersionProfile).all()
        assert len(profiles) == 2

    def test_seed_idempotent_second_call_returns_zero(self, service, db_session):
        service.seed_profiles()
        db_session.commit()
        count2 = service.seed_profiles()
        db_session.commit()
        assert count2 == 0

    def test_seed_creates_bnk_21_profile(self, service, db_session):
        service.seed_profiles()
        db_session.commit()
        p = db_session.query(BnkVersionProfile).filter_by(name="bnk-2.1").first()
        assert p is not None
        assert p.is_default is False
        assert p.bnk_cr_kind == "CNEInstance"
        assert p.k8s_version == "1.29.8"

    def test_seed_creates_bnk_22_profile(self, service, db_session):
        service.seed_profiles()
        db_session.commit()
        p = db_session.query(BnkVersionProfile).filter_by(name="bnk-2.2").first()
        assert p is not None
        assert p.is_default is True
        assert p.bnk_cr_kind == "BNKGatewayClass"
        assert p.k8s_version == "1.30.4"

    def test_seed_partial_idempotency(self, service, db_session):
        """If one profile exists already, seed only creates the missing one."""
        db_session.add(BnkVersionProfile(**BNK_21_PROFILE))
        db_session.commit()
        count = service.seed_profiles()
        db_session.commit()
        assert count == 1  # Only BNK 2.2 was missing


@pytest.mark.unit
class TestBnkVersionProfileServiceList:
    """Tests for list_profiles()."""

    def test_list_empty_when_no_profiles(self, service):
        result = service.list_profiles()
        assert result.profiles == []

    def test_list_returns_all_profiles_after_seed(self, service, db_session):
        service.seed_profiles()
        db_session.commit()
        result = service.list_profiles()
        assert len(result.profiles) == 2

    def test_list_ordered_by_name(self, service, db_session):
        service.seed_profiles()
        db_session.commit()
        result = service.list_profiles()
        names = [p.name for p in result.profiles]
        assert names == sorted(names)

    def test_list_returns_response_objects(self, service, db_session):
        service.seed_profiles()
        db_session.commit()
        from schemas.bare_metal import BnkVersionProfileResponse
        result = service.list_profiles()
        assert all(isinstance(p, BnkVersionProfileResponse) for p in result.profiles)


@pytest.mark.unit
class TestBnkVersionProfileServiceGet:
    """Tests for get_profile()."""

    def test_get_profile_returns_correct_profile(self, service, db_session):
        service.seed_profiles()
        db_session.commit()
        # Get the ID of bnk-2.2
        p = db_session.query(BnkVersionProfile).filter_by(name="bnk-2.2").first()
        result = service.get_profile(p.id)
        assert result.name == "bnk-2.2"
        assert result.is_default is True

    def test_get_profile_invalid_id_raises_not_found(self, service):
        from core.errors import NotFoundError
        with pytest.raises(NotFoundError):
            service.get_profile(9999)

    def test_get_profile_returns_response_object(self, service, db_session):
        service.seed_profiles()
        db_session.commit()
        from schemas.bare_metal import BnkVersionProfileResponse
        p = db_session.query(BnkVersionProfile).first()
        result = service.get_profile(p.id)
        assert isinstance(result, BnkVersionProfileResponse)


@pytest.mark.unit
class TestBnkVersionProfileServiceGetDefault:
    """Tests for get_default_profile()."""

    def test_get_default_returns_bnk_22(self, service, db_session):
        service.seed_profiles()
        db_session.commit()
        result = service.get_default_profile()
        assert result is not None
        assert result.name == "bnk-2.2"
        assert result.is_default is True

    def test_get_default_returns_none_when_no_profiles(self, service):
        result = service.get_default_profile()
        assert result is None

    def test_get_default_returns_model_instance(self, service, db_session):
        service.seed_profiles()
        db_session.commit()
        result = service.get_default_profile()
        assert isinstance(result, BnkVersionProfile)


@pytest.mark.unit
class TestBnkVersionProfileServiceCreate:
    """Tests for create_profile()."""

    def test_create_profile_persists(self, service, db_session):
        data = {
            "name": "bnk-custom",
            "display_name": "Custom BNK",
            "description": "A custom version",
            "is_default": False,
            "bnk_manifest_version": "3.0.0",
            "bnk_cr_kind": "BNKGatewayClass",
            "flo_version": "1.0.0",
            "k8s_version": "1.31.0",
            "doca_version": "3.0.0",
            "containerd_version": "1.8.0",
            "runc_version": "1.2.0",
            "calico_version": "3.29.0",
            "cert_manager_version": "1.16.0",
            "gateway_api_version": "1.2.0",
            "multus_version": "4.2.0",
            "sriov_version": "1.5.0",
            "storage_class_type": "local-path",
            "storage_provisioner": "rancher.io/local-path",
            "feature_flags": {"ipv6": True},
        }
        result = service.create_profile(data)
        db_session.commit()
        assert result.name == "bnk-custom"
        assert result.feature_flags == {"ipv6": True}

    def test_create_profile_returns_response_object(self, service, db_session):
        from schemas.bare_metal import BnkVersionProfileResponse
        data = {**BNK_21_PROFILE, "name": "bnk-test-create"}
        result = service.create_profile(data)
        db_session.commit()
        assert isinstance(result, BnkVersionProfileResponse)

    def test_create_profile_assigns_id(self, service, db_session):
        data = {**BNK_22_PROFILE, "name": "bnk-test-id"}
        result = service.create_profile(data)
        db_session.commit()
        assert result.id is not None
        assert result.id > 0
