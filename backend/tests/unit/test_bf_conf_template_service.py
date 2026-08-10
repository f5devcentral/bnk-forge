"""Unit tests for BfConfTemplateService."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import models  # noqa: F401 — registers all models on Base
from core.errors import BadRequestError, NotFoundError, ValidationError
from database import Base
from models.dpu import ProjectDpuSettings
from models.project import Project
from schemas.dpu import BfConfTemplateCreate, BfConfTemplateUpdate
from services.bf_conf_template_service import (
    BfConfTemplateService,
    classify_uplink_mode,
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
def service(db: Session) -> BfConfTemplateService:
    return BfConfTemplateService(db)


class TestCreate:
    def test_creates_template(self, service: BfConfTemplateService, db: Session):
        t = service.create_template(
            BfConfTemplateCreate(
                name="test-lag",
                description="test",
                content="UPDATE_DPU_OS=yes",
            )
        )
        db.commit()
        assert t.id is not None
        assert t.name == "test-lag"

    def test_duplicate_name_raises_validation_error(
        self, service: BfConfTemplateService, db: Session
    ):
        service.create_template(BfConfTemplateCreate(name="dup", content="x"))
        db.commit()
        with pytest.raises(ValidationError):
            service.create_template(BfConfTemplateCreate(name="dup", content="y"))


class TestRead:
    def test_list_sorted_by_name(self, service: BfConfTemplateService, db: Session):
        service.create_template(BfConfTemplateCreate(name="zzz", content="x"))
        service.create_template(BfConfTemplateCreate(name="aaa", content="x"))
        db.commit()
        assert [t.name for t in service.list_templates()] == ["aaa", "zzz"]

    def test_get_missing_raises(self, service: BfConfTemplateService):
        with pytest.raises(NotFoundError):
            service.get_template(999)


class TestUpdate:
    def test_partial_update_preserves_untouched_fields(
        self, service: BfConfTemplateService, db: Session
    ):
        t = service.create_template(BfConfTemplateCreate(
            name="t", description="original desc", content="body",
        ))
        db.commit()
        service.update_template(t.id, BfConfTemplateUpdate(content="new body"))
        db.commit()
        refreshed = service.get_template(t.id)
        assert refreshed.content == "new body"
        assert refreshed.name == "t"
        assert refreshed.description == "original desc"

    def test_update_to_existing_name_raises(
        self, service: BfConfTemplateService, db: Session
    ):
        a = service.create_template(BfConfTemplateCreate(name="a", content="x"))
        service.create_template(BfConfTemplateCreate(name="b", content="x"))
        db.commit()
        with pytest.raises(ValidationError):
            service.update_template(a.id, BfConfTemplateUpdate(name="b"))


class TestDelete:
    def test_delete_removes_row(self, service: BfConfTemplateService, db: Session):
        t = service.create_template(BfConfTemplateCreate(name="gone", content="x"))
        db.commit()
        service.delete_template(t.id)
        db.commit()
        assert service.list_templates() == []

    def test_delete_missing_raises(self, service: BfConfTemplateService):
        with pytest.raises(NotFoundError):
            service.delete_template(999)

    def test_delete_refuses_when_referenced(
        self, service: BfConfTemplateService, db: Session
    ):
        # Create a project + settings row referencing the template
        project = Project(name="p", description="d")
        db.add(project)
        db.commit()
        db.refresh(project)

        t = service.create_template(BfConfTemplateCreate(name="ref", content="x"))
        db.commit()
        db.add(ProjectDpuSettings(project_id=project.id, bf_template_id=t.id))
        db.commit()

        with pytest.raises(BadRequestError):
            service.delete_template(t.id)


class TestClassifyUplinkMode:
    """The bf.conf classifier drives the DPU Project Settings dropdown.

    LAG templates expose only `bond0` (because p0/p1 are consumed to
    build the bond); non-LAG templates expose only `p0` / `p1`. Operator-
    authored templates that mention neither resolve to "unknown" so the
    UI keeps every choice available.
    """

    def test_lag_template_marker(self):
        # Minimal LAG fragment: a netplan bonds: block.
        content = """
        bonds:
          bond0:
            interfaces: [p0, p1]
            parameters:
              mode: 802.3ad
        """
        assert classify_uplink_mode(content) == "lag"

    def test_p0p1_template(self):
        content = """
        ethernets:
          p0:
            mtu: 9216
          p1:
            mtu: 9216
        """
        assert classify_uplink_mode(content) == "p0p1"

    def test_unknown_when_neither_marker_present(self):
        assert classify_uplink_mode("UPDATE_DPU_OS=yes\n") == "unknown"

    def test_bond0_inside_a_shell_comment_is_ignored(self):
        # The non-LAG seed mentions "bond0" in a narrative comment;
        # the classifier must look at executable content only.
        content = """
        # the LAG-specific bond0 stanza is intentionally omitted
        ethernets:
          p0:
            mtu: 9216
        """
        assert classify_uplink_mode(content) == "p0p1"

    def test_empty_content_is_unknown(self):
        assert classify_uplink_mode("") == "unknown"
        assert classify_uplink_mode(None) == "unknown"

    def test_seeded_lag_template_classifies_as_lag(self):
        from pathlib import Path

        path = (
            Path(__file__).resolve().parents[2]
            / "alembic" / "seed_data" / "bf_template_lag.conf"
        )
        if not path.exists():
            pytest.skip("LAG seed template not present")
        assert classify_uplink_mode(path.read_text()) == "lag"

    def test_seeded_nolag_template_classifies_as_p0p1(self):
        from pathlib import Path

        path = (
            Path(__file__).resolve().parents[2]
            / "alembic" / "seed_data" / "bf_template_nolag.conf"
        )
        if not path.exists():
            pytest.skip("non-LAG seed template not present")
        assert classify_uplink_mode(path.read_text()) == "p0p1"
