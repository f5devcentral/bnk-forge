"""
Unit tests for BnkDeployableReleaseService (ADR-478).

Uses an in-memory SQLite database — no external services required.
FK enforcement is left OFF so only the bnk_deployable_release table is needed.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models.bnk_deployable_release import BnkDeployableRelease
from schemas.bare_metal import DeployableReleaseResponse
from services.bare_metal.version_profiles import (
    BNK_21_PROFILE,
    BNK_22_PROFILE,
    BNK_231_RELEASE,
    BnkDeployableReleaseService,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
def db_session():
    """In-memory SQLite with only the deployable-release table.

    FK enforcement is off (no PRAGMA) so bnk_releases need not exist.
    _resolve_bnk_release_id() handles missing bnk_releases gracefully.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    BnkDeployableRelease.__table__.create(engine, checkfirst=True)

    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def service(db_session):
    return BnkDeployableReleaseService(db=db_session)


# ---------------------------------------------------------------------------
# TestBnkDeployableReleaseServiceSeed
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBnkDeployableReleaseServiceSeed:
    """Tests for seed_profiles()."""

    def test_seed_creates_three_releases(self, service, db_session):
        count = service.seed_profiles()
        db_session.commit()
        assert count == 3
        releases = db_session.query(BnkDeployableRelease).all()
        assert len(releases) == 3

    def test_seed_idempotent_second_call_returns_zero(self, service, db_session):
        service.seed_profiles()
        db_session.commit()
        count2 = service.seed_profiles()
        db_session.commit()
        assert count2 == 0

    def test_seed_creates_bnk_21_release(self, service, db_session):
        service.seed_profiles()
        db_session.commit()
        r = db_session.query(BnkDeployableRelease).filter_by(name="bnk-2.1").first()
        assert r is not None
        assert r.is_default is False
        assert r.is_active is True
        assert r.bnk_cr_kind == "CNEInstance"
        assert r.k8s_version == "1.29.8"
        assert r.source_type == "manual"

    def test_seed_creates_bnk_22_release_as_default(self, service, db_session):
        service.seed_profiles()
        db_session.commit()
        r = db_session.query(BnkDeployableRelease).filter_by(name="bnk-2.2").first()
        assert r is not None
        assert r.is_default is True
        assert r.is_active is True
        assert r.bnk_cr_kind == "CNEInstance"
        assert r.k8s_version == "1.30.4"
        assert r.cert_manager_version == "v1.15.3"

    def test_seed_creates_bnk_231_release(self, service, db_session):
        service.seed_profiles()
        db_session.commit()
        r = db_session.query(BnkDeployableRelease).filter_by(name="bnk-2.3.1").first()
        assert r is not None
        assert r.is_default is False
        assert r.is_active is True
        assert r.bnk_cr_kind == "CNEInstance"
        assert r.k8s_version == "1.30.14"
        assert r.cert_manager_version == "v1.16.2"
        assert r.doca_version == "3.2.0"

    def test_single_default_invariant_after_seed(self, service, db_session):
        """Exactly one release may have is_default=True after seed."""
        service.seed_profiles()
        db_session.commit()
        defaults = db_session.query(BnkDeployableRelease).filter_by(is_default=True).all()
        assert len(defaults) == 1
        assert defaults[0].name == "bnk-2.2"

    def test_seed_partial_idempotency(self, service, db_session):
        """If one release exists, seed only creates the two missing ones."""
        db_session.add(BnkDeployableRelease(**BNK_21_PROFILE))
        db_session.commit()
        count = service.seed_profiles()
        db_session.commit()
        assert count == 2  # bnk-2.2 and bnk-2.3.1 were missing


# ---------------------------------------------------------------------------
# TestBnkDeployableReleaseServiceList
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBnkDeployableReleaseServiceList:
    """Tests for list_profiles()."""

    def test_list_empty_when_no_releases(self, service):
        result = service.list_profiles()
        assert result.releases == []

    def test_list_returns_all_releases_after_seed(self, service, db_session):
        service.seed_profiles()
        db_session.commit()
        result = service.list_profiles()
        assert len(result.releases) == 3

    def test_list_ordered_by_name(self, service, db_session):
        service.seed_profiles()
        db_session.commit()
        result = service.list_profiles()
        names = [r.name for r in result.releases]
        assert names == sorted(names)

    def test_list_returns_response_objects(self, service, db_session):
        service.seed_profiles()
        db_session.commit()
        result = service.list_profiles()
        assert all(isinstance(r, DeployableReleaseResponse) for r in result.releases)


# ---------------------------------------------------------------------------
# TestBnkDeployableReleaseServiceGet
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBnkDeployableReleaseServiceGet:
    """Tests for get_profile()."""

    def test_get_profile_returns_correct_release(self, service, db_session):
        service.seed_profiles()
        db_session.commit()
        r = db_session.query(BnkDeployableRelease).filter_by(name="bnk-2.2").first()
        result = service.get_profile(r.id)
        assert result.name == "bnk-2.2"
        assert result.is_default is True

    def test_get_profile_invalid_id_raises_not_found(self, service):
        from core.errors import NotFoundError
        with pytest.raises(NotFoundError):
            service.get_profile(9999)

    def test_get_profile_returns_response_object(self, service, db_session):
        service.seed_profiles()
        db_session.commit()
        r = db_session.query(BnkDeployableRelease).first()
        result = service.get_profile(r.id)
        assert isinstance(result, DeployableReleaseResponse)


# ---------------------------------------------------------------------------
# TestBnkDeployableReleaseServiceGetDefault
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBnkDeployableReleaseServiceGetDefault:
    """Tests for get_default_profile()."""

    def test_get_default_returns_bnk_22(self, service, db_session):
        service.seed_profiles()
        db_session.commit()
        result = service.get_default_profile()
        assert result is not None
        assert result.name == "bnk-2.2"
        assert result.is_default is True

    def test_get_default_returns_none_when_no_releases(self, service):
        result = service.get_default_profile()
        assert result is None

    def test_get_default_returns_model_instance(self, service, db_session):
        service.seed_profiles()
        db_session.commit()
        result = service.get_default_profile()
        assert isinstance(result, BnkDeployableRelease)


# ---------------------------------------------------------------------------
# TestBnkDeployableReleaseServiceCreate
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBnkDeployableReleaseServiceCreate:
    """Tests for create_profile()."""

    def test_create_release_persists(self, service, db_session):
        data = {
            **BNK_22_PROFILE,
            "name": "bnk-custom",
            "display_name": "Custom BNK",
            "is_default": False,
            "feature_flags": {"ipv6": True},
        }
        result = service.create_profile(data)
        db_session.commit()
        assert result.name == "bnk-custom"
        assert result.feature_flags == {"ipv6": True}

    def test_create_release_returns_response_object(self, service, db_session):
        data = {**BNK_21_PROFILE, "name": "bnk-test-create"}
        result = service.create_profile(data)
        db_session.commit()
        assert isinstance(result, DeployableReleaseResponse)

    def test_create_release_assigns_id(self, service, db_session):
        data = {**BNK_22_PROFILE, "name": "bnk-test-id"}
        result = service.create_profile(data)
        db_session.commit()
        assert result.id is not None
        assert result.id > 0


# ---------------------------------------------------------------------------
# TestBnkDeployableReleaseServiceSetActive
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBnkDeployableReleaseServiceSetActive:
    """Tests for set_active()."""

    def test_set_inactive_disables_release(self, service, db_session):
        service.seed_profiles()
        db_session.commit()
        r = db_session.query(BnkDeployableRelease).filter_by(name="bnk-2.2").first()
        result = service.set_active(r.id, False)
        db_session.commit()
        assert result.is_active is False

    def test_set_active_enables_release(self, service, db_session):
        service.seed_profiles()
        db_session.commit()
        r = db_session.query(BnkDeployableRelease).filter_by(name="bnk-2.2").first()
        service.set_active(r.id, False)
        db_session.commit()
        result = service.set_active(r.id, True)
        db_session.commit()
        assert result.is_active is True

    def test_set_active_invalid_id_raises_not_found(self, service):
        from core.errors import NotFoundError
        with pytest.raises(NotFoundError):
            service.set_active(9999, False)


# ---------------------------------------------------------------------------
# TestBnkDeployableReleaseServiceSetDefault
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBnkDeployableReleaseServiceSetDefault:
    """Tests for set_default() — single-default invariant."""

    def test_set_default_sets_new_default(self, service, db_session):
        service.seed_profiles()
        db_session.commit()
        r = db_session.query(BnkDeployableRelease).filter_by(name="bnk-2.3.1").first()
        result = service.set_default(r.id)
        db_session.commit()
        assert result.is_default is True
        assert result.name == "bnk-2.3.1"

    def test_set_default_clears_previous_default(self, service, db_session):
        service.seed_profiles()
        db_session.commit()
        r_231 = db_session.query(BnkDeployableRelease).filter_by(name="bnk-2.3.1").first()
        service.set_default(r_231.id)
        db_session.commit()
        db_session.expire_all()
        r_22 = db_session.query(BnkDeployableRelease).filter_by(name="bnk-2.2").first()
        assert r_22.is_default is False

    def test_set_default_single_default_invariant(self, service, db_session):
        """Only one release may be default after set_default."""
        service.seed_profiles()
        db_session.commit()
        r_231 = db_session.query(BnkDeployableRelease).filter_by(name="bnk-2.3.1").first()
        service.set_default(r_231.id)
        db_session.commit()
        db_session.expire_all()
        defaults = db_session.query(BnkDeployableRelease).filter_by(is_default=True).all()
        assert len(defaults) == 1

    def test_set_default_invalid_id_raises_not_found(self, service):
        from core.errors import NotFoundError
        with pytest.raises(NotFoundError):
            service.set_default(9999)


# ---------------------------------------------------------------------------
# TestCertManagerFailFast
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCertManagerFailFast:
    """cert_manager_version is catalog-driven (required); no hardcoded default."""

    def test_cert_manager_missing_version_is_flagged(self):
        from modules.bare_metal.bnk_cert_manager import CertManagerSSHModule
        mod = CertManagerSSHModule()
        errors = mod.validate_inputs({})
        assert any("cert_manager_version" in e for e in errors)

    def test_cert_manager_provided_version_is_not_flagged(self):
        from modules.bare_metal.bnk_cert_manager import CertManagerSSHModule
        mod = CertManagerSSHModule()
        errors = mod.validate_inputs({"cert_manager_version": "v1.16.2"})
        assert not any("cert_manager_version" in e for e in errors)

    def test_cert_manager_input_has_no_hardcoded_default(self):
        """cert_manager_version InputSpec must have no default — catalog supplies it."""
        from modules.bare_metal.bnk_cert_manager import CertManagerSSHModule
        spec = CertManagerSSHModule.inputs.get("cert_manager_version")
        assert spec is not None
        # default=None means catalog-driven; any hardcoded string would be a regression.
        assert spec.default is None


# ---------------------------------------------------------------------------
# TestCneInstanceFailFast
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCneInstanceFailFast:
    """bnk_cr_kind is catalog-driven (required); renders into the CR kind: field."""

    def test_cneinstance_missing_bnk_cr_kind_is_flagged(self):
        from modules.bare_metal.bnk_cneinstance import BnkCneInstanceSSHModule
        mod = BnkCneInstanceSSHModule()
        errors = mod.validate_inputs({})
        assert any("bnk_cr_kind" in e for e in errors)

    def test_cneinstance_provided_bnk_cr_kind_is_not_flagged(self):
        from modules.bare_metal.bnk_cneinstance import BnkCneInstanceSSHModule
        mod = BnkCneInstanceSSHModule()
        errors = mod.validate_inputs({"bnk_cr_kind": "CNEInstance"})
        assert not any("bnk_cr_kind" in e for e in errors)

    def test_cneinstance_bnk_cr_kind_renders_as_manifest_kind(self):
        """bnk_cr_kind value must render as the kind: field in the CR."""
        from modules.bare_metal.bnk_cneinstance import BnkCneInstanceSSHModule
        mod = BnkCneInstanceSSHModule()
        variables = {
            "bnk_cr_kind": "CNEInstance",
            "instance_namespace": "f5-bnk",
            "instance_name": "bnk-instance",
            "manifest_version": "2.3.1-3.2598.3-0.0.304",
        }
        manifests = mod.render_manifests(variables)
        assert manifests, "Expected at least one manifest"
        assert manifests[0]["kind"] == "CNEInstance"

    def test_cneinstance_input_has_no_hardcoded_default(self):
        """bnk_cr_kind InputSpec must have no default — catalog supplies it."""
        from modules.bare_metal.bnk_cneinstance import BnkCneInstanceSSHModule
        spec = BnkCneInstanceSSHModule.inputs.get("bnk_cr_kind")
        assert spec is not None
        assert spec.default is None
