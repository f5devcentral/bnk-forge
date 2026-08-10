"""Unit tests for the post-flash reachability poller.

The task itself wraps a `with get_db_context()` + Celery retry machinery —
we exercise the state-transition branches by monkeypatching the db
context manager and the DpuOsProbeService, and driving the task via
`task.apply(args=...)` (which runs the function synchronously).
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import models  # noqa: F401 — registers Base metadata
from core.encryption import encrypt_value
from database import Base
from models.dpu import Dpu, ProjectDpuSettings
from models.project import Project


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
    p = Project(name="p", description="d")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@pytest.fixture
def dpu(db: Session, project: Project) -> Dpu:
    d = Dpu(
        project_id=project.id,
        bmc_ip="192.0.2.50",
        bmc_username_encrypted=encrypt_value("root"),
        bmc_password_encrypted=encrypt_value("pw"),
        serial_number="MT-TEST-1",
        flash_status="waiting_reboot",
    )
    db.add(d)
    # Settings row with OS creds so DpuOsProbeService's precondition passes.
    db.add(ProjectDpuSettings(
        project_id=project.id,
        default_os_username="ubuntu",
        default_os_password_encrypted=encrypt_value("ubuntu-pw"),
    ))
    db.commit()
    db.refresh(d)
    return d


@pytest.fixture(autouse=True)
def patch_db_context(monkeypatch, db: Session):
    """Route `get_db_context` in tasks.dpu_tasks to our in-memory session.

    Celery's task code uses `with get_db_context() as db: ...`, so we need
    to yield the SAME session the fixtures populated. The real one opens
    a new SQLAlchemy Session per call — would not see our test data.
    """
    @contextmanager
    def fake_ctx():
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

    monkeypatch.setattr("tasks.dpu_tasks.get_db_context", fake_ctx)


def _stub_probe(monkeypatch, *, becomes_reachable: bool):
    """Replace DpuOsProbeService.run_and_persist with a stub that sets
    dpu_os_status based on `becomes_reachable`."""

    def fake_run_and_persist(self, dpu_id):  # noqa: ARG001
        d = self.db.query(Dpu).filter(Dpu.id == dpu_id).first()
        if d is None:
            return
        d.dpu_os_status = "reachable" if becomes_reachable else "unreachable"
        d.dpu_os_reachable = becomes_reachable
        self.db.commit()

    monkeypatch.setattr(
        "services.dpu_os_probe_service.DpuOsProbeService.run_and_persist",
        fake_run_and_persist,
    )


# ── Tests ─────────────────────────────────────────────────────────────────

class TestPollerHappyPath:
    def test_reachable_promotes_to_os_online(self, db, dpu, monkeypatch):
        from tasks.dpu_tasks import poll_flash_online_task
        _stub_probe(monkeypatch, becomes_reachable=True)

        result = poll_flash_online_task.apply(args=[dpu.project_id, dpu.id]).get()
        db.refresh(dpu)

        assert result == {"status": "os_online"}
        assert dpu.flash_status == "os_online"
        assert dpu.flash_error is None
        assert dpu.flash_completed_at is not None


class TestPollerAbortPaths:
    def test_exits_quietly_if_cancelled(self, db, dpu, monkeypatch):
        from tasks.dpu_tasks import poll_flash_online_task
        # Operator cancelled between poll invocations.
        dpu.flash_status = "cancelled"
        db.commit()

        _stub_probe(monkeypatch, becomes_reachable=True)
        result = poll_flash_online_task.apply(args=[dpu.project_id, dpu.id]).get()
        db.refresh(dpu)

        assert result["status"] == "aborted"
        # Cancellation state preserved — the poller must not re-mark it.
        assert dpu.flash_status == "cancelled"

    def test_exits_if_dpu_vanished(self, db, project, monkeypatch):
        from tasks.dpu_tasks import poll_flash_online_task
        _stub_probe(monkeypatch, becomes_reachable=True)
        result = poll_flash_online_task.apply(args=[project.id, 99999]).get()
        assert result["status"] == "aborted"
        assert result["reason"] == "dpu_gone"


class TestPollerRetry:
    def test_unreachable_updates_stage_detail_and_retries(self, db, dpu, monkeypatch):
        # Drive one attempt by calling the task's underlying function
        # directly with a fake Celery `self`. Bypasses eager-mode cascade
        # so we can observe a single-attempt side effect.
        from celery.exceptions import Retry

        from tasks import dpu_tasks

        _stub_probe(monkeypatch, becomes_reachable=False)

        class _FakeRequest:
            retries = 0

        class _FakeSelf:
            request = _FakeRequest()
            max_retries = dpu_tasks._POLL_MAX_RETRIES
            retry_count = 0

            def retry(self, *, countdown):
                self.retry_count += 1
                self.retry_countdown = countdown
                raise Retry()

        fake_self = _FakeSelf()
        # __wrapped__ on a bind=True Celery task is a bound method; peel off
        # the binding to drive the raw function with our fake `self`.
        task_fn = dpu_tasks.poll_flash_online_task.__wrapped__.__func__
        with pytest.raises(Retry):
            task_fn(fake_self, dpu.project_id, dpu.id)

        db.refresh(dpu)
        assert dpu.flash_status == "waiting_reboot"
        assert "attempt 1" in (dpu.flash_stage_detail or "")
        assert fake_self.retry_count == 1
        assert fake_self.retry_countdown == dpu_tasks._POLL_RETRY_COUNTDOWN


class TestPollerTimeout:
    def test_exhausted_retries_mark_failed(self, db, dpu, monkeypatch):
        from tasks.dpu_tasks import _POLL_MAX_RETRIES, poll_flash_online_task
        _stub_probe(monkeypatch, becomes_reachable=False)

        # Simulate "last retry": retries already at max.
        result = poll_flash_online_task.apply(
            args=[dpu.project_id, dpu.id],
            retries=_POLL_MAX_RETRIES,
        ).get()
        db.refresh(dpu)

        assert result == {"status": "timeout"}
        assert dpu.flash_status == "failed"
        assert "did not come back online" in (dpu.flash_error or "")
        assert dpu.flash_completed_at is not None
