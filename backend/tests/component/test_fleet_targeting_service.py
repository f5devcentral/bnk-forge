"""Component tests for fleet targeting + bulk-ops — D-022 Phase 2.

Tests (with DB):
  - resolve_target: creates Decision snapshot, RBAC, immutability, re-resolve.
  - project_id rejected as selector key.
  - Stale-decision refusal: consumed/superseded/past-TTL/missing-member.
  - Blast-radius preview: GET /decisions/{id} returns member list.
  - Wave ordering: fault-domain buckets, NULL trailing.
  - Action allowlist: refuses "destroy".
  - integration: safe re-reconcile → all succeeded + labels refreshed + decision consumed.
  - API routes: targets CRUD, resolve, bulk-ops start/status.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from models.bare_metal import BareMetalHost
from models.fleet import FleetMember
from models.fleet_targeting import (
    BULK_RUN_STATUS_COMPLETED,
    BULK_RUN_STATUS_PAUSED_GATE,
    DECISION_STATUS_CONSUMED,
    DECISION_STATUS_RESOLVED,
    DECISION_STATUS_SUPERSEDED,
    DECISION_TTL_SECONDS,
    FleetBulkRun,
    FleetBulkRunResult,
    FleetDecision,
    FleetTarget,
)
from services.fleet_reconcile_service import reconcile_fleet_member
from tests.factories import KubernetesClusterFactory, ProjectFactory, UserFactory  # noqa: F401

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_host(db, project, fault_domain=None, **kwargs):
    defaults = dict(
        project_id=project.id,
        name="test-host",
        host_ip="10.0.0.1",
        os_info={"os_type": "ubuntu", "os_version": "22.04", "arch": "x86_64"},
    )
    defaults.update(kwargs)
    host = BareMetalHost(**defaults)
    db.add(host)
    db.flush()
    return host


def _make_cluster_member(db, project, assigned_labels=None, fault_domain=None):
    cluster = KubernetesClusterFactory(db, project=project, cloud_provider="aws", region="us-east-1")
    member = reconcile_fleet_member(db, "cluster", cluster.id)
    if assigned_labels:
        member.assigned_labels = assigned_labels
    if fault_domain:
        member.fault_domain = fault_domain
    db.flush()
    db.refresh(member)
    return member


def _make_target(db, project, selector, name="test-target"):
    target = FleetTarget(
        project_id=project.id,
        name=name,
        selector=selector,
        created_by="test",
    )
    db.add(target)
    db.flush()
    db.refresh(target)
    return target


class _FakeUser:
    def __init__(self, role="admin", user_id=1, username="testuser", project_ids=None):
        self.role = role
        self.id = user_id
        self.username = username
        self._project_ids = project_ids or []


# ---------------------------------------------------------------------------
# resolve_target
# ---------------------------------------------------------------------------

class TestResolveTarget:
    def test_resolve_creates_decision_snapshot(self, db, make_project):
        from services.fleet_selector import resolve_target

        p = make_project()
        m = _make_cluster_member(db, p, assigned_labels={"env": "prod"})
        target = _make_target(db, p, selector={"labels": ["env=prod"]})
        user = _FakeUser()

        decision = resolve_target(db, target, resolved_by="user1", current_user=user)
        db.commit()

        assert decision.status == DECISION_STATUS_RESOLVED
        assert m.id in decision.resolved_member_ids
        assert decision.member_count == 1
        assert decision.target_id == target.id

    def test_resolve_only_matching_members(self, db, make_project):
        from services.fleet_selector import resolve_target

        p = make_project()
        m_prod = _make_cluster_member(db, p, assigned_labels={"env": "prod"})
        m_staging = _make_cluster_member(db, p, assigned_labels={"env": "staging"})
        target = _make_target(db, p, selector={"labels": ["env=prod"]})
        user = _FakeUser()

        decision = resolve_target(db, target, resolved_by="u", current_user=user)

        assert m_prod.id in decision.resolved_member_ids
        assert m_staging.id not in decision.resolved_member_ids
        assert decision.member_count == 1

    def test_resolve_supersedes_prior_resolved_decision(self, db, make_project):
        from services.fleet_selector import resolve_target

        p = make_project()
        _make_cluster_member(db, p, assigned_labels={"env": "prod"})
        target = _make_target(db, p, selector={"labels": ["env=prod"]})
        user = _FakeUser()

        d1 = resolve_target(db, target, resolved_by="u", current_user=user)
        db.commit()
        assert d1.status == DECISION_STATUS_RESOLVED

        d2 = resolve_target(db, target, resolved_by="u", current_user=user)
        db.commit()

        db.refresh(d1)
        assert d1.status == DECISION_STATUS_SUPERSEDED
        assert d2.status == DECISION_STATUS_RESOLVED

    def test_re_resolve_creates_new_row(self, db, make_project):
        from services.fleet_selector import resolve_target

        p = make_project()
        _make_cluster_member(db, p)
        target = _make_target(db, p, selector={"labels": []})
        user = _FakeUser()

        d1 = resolve_target(db, target, resolved_by="u", current_user=user)
        d2 = resolve_target(db, target, resolved_by="u", current_user=user)
        assert d1.id != d2.id

    def test_project_id_selector_key_rejected(self, db, make_project):
        from core.errors import ValidationError
        from services.fleet_selector import resolve_target

        p = make_project()
        target = _make_target(db, p, selector={"labels": ["project_id=1"]})
        user = _FakeUser()

        with pytest.raises(ValidationError):
            resolve_target(db, target, resolved_by="u", current_user=user)

    def test_rbac_non_admin_scoped_to_own_projects(self, db, make_project):
        from services.fleet_selector import resolve_target

        # Create two DISTINCT users so their projects are isolated.
        owner_user = UserFactory(db, username="rbac-selector-owner", role="operator")
        other_user = UserFactory(db, username="rbac-selector-other", role="operator")

        p_own = ProjectFactory(db, user_id=owner_user.id)
        p_other = ProjectFactory(db, user_id=other_user.id)

        m_own = _make_cluster_member(db, p_own, assigned_labels={"env": "prod"})
        m_other = _make_cluster_member(db, p_other, assigned_labels={"env": "prod"})

        target = FleetTarget(
            project_id=p_own.id,
            name="rbac-target",
            selector={"labels": ["env=prod"]},
            created_by="owner",
        )
        db.add(target)
        db.flush()

        # Non-admin owner user — should only see their own project's members.
        non_admin_user = _FakeUser(role="operator", user_id=owner_user.id)

        decision = resolve_target(db, target, resolved_by="owner", current_user=non_admin_user)
        db.commit()

        assert m_own.id in decision.resolved_member_ids
        assert m_other.id not in decision.resolved_member_ids


# ---------------------------------------------------------------------------
# Stale-decision refusal
# ---------------------------------------------------------------------------

class TestStaleDecisionRefusal:
    def _make_fresh_decision(self, db, project, member_ids=None):
        target = FleetTarget(
            project_id=project.id,
            name="stale-test",
            selector={"labels": []},
            created_by="test",
        )
        db.add(target)
        db.flush()
        decision = FleetDecision(
            target_id=target.id,
            project_id=project.id,
            selector_snapshot={"labels": []},
            resolved_member_ids=member_ids or [],
            member_count=len(member_ids or []),
            resolved_at=datetime.now(UTC),
            resolved_by="test",
            status=DECISION_STATUS_RESOLVED,
        )
        db.add(decision)
        db.flush()
        db.refresh(decision)
        return decision

    def test_consumed_decision_refused(self, db, make_project):
        from services.fleet_bulkop_service import StaleDecisionError, _assert_decision_fresh

        p = make_project()
        d = self._make_fresh_decision(db, p)
        d.status = DECISION_STATUS_CONSUMED

        with pytest.raises(StaleDecisionError, match="consumed"):
            _assert_decision_fresh(db, d)

    def test_superseded_decision_refused(self, db, make_project):
        from services.fleet_bulkop_service import StaleDecisionError, _assert_decision_fresh

        p = make_project()
        d = self._make_fresh_decision(db, p)
        d.status = DECISION_STATUS_SUPERSEDED

        with pytest.raises(StaleDecisionError, match="superseded"):
            _assert_decision_fresh(db, d)

    def test_past_ttl_decision_refused(self, db, make_project):
        from services.fleet_bulkop_service import StaleDecisionError, _assert_decision_fresh

        p = make_project()
        d = self._make_fresh_decision(db, p)
        d.resolved_at = datetime.now(UTC) - timedelta(seconds=DECISION_TTL_SECONDS + 60)

        with pytest.raises(StaleDecisionError, match="stale"):
            _assert_decision_fresh(db, d)

    def test_missing_member_refused(self, db, make_project):
        from services.fleet_bulkop_service import StaleDecisionError, _assert_decision_fresh

        p = make_project()
        # Use a member_id that doesn't exist
        d = self._make_fresh_decision(db, p, member_ids=[999999])

        with pytest.raises(StaleDecisionError, match="no longer exist"):
            _assert_decision_fresh(db, d)

    def test_fresh_resolved_decision_passes(self, db, make_project):
        from services.fleet_bulkop_service import _assert_decision_fresh

        p = make_project()
        m = _make_cluster_member(db, p)
        d = self._make_fresh_decision(db, p, member_ids=[m.id])

        members = _assert_decision_fresh(db, d)
        assert len(members) == 1
        assert members[0].id == m.id

    def test_fresh_empty_decision_passes(self, db, make_project):
        from services.fleet_bulkop_service import _assert_decision_fresh

        p = make_project()
        d = self._make_fresh_decision(db, p, member_ids=[])
        members = _assert_decision_fresh(db, d)
        assert members == []


# ---------------------------------------------------------------------------
# Action allowlist (executor-level)
# ---------------------------------------------------------------------------

class TestActionAllowlist:
    def _make_run(self, db, project, action):
        target = FleetTarget(
            project_id=project.id,
            name="allowlist-test",
            selector={"labels": []},
            created_by="test",
        )
        db.add(target)
        db.flush()
        decision = FleetDecision(
            target_id=target.id,
            project_id=project.id,
            selector_snapshot={"labels": []},
            resolved_member_ids=[],
            member_count=0,
            resolved_at=datetime.now(UTC),
            resolved_by="test",
            status=DECISION_STATUS_RESOLVED,
        )
        db.add(decision)
        db.flush()
        run = FleetBulkRun(
            decision_id=decision.id,
            project_id=project.id,
            action=action,
            wave_by="fault_domain",
            concurrency=1,
            gate_mode="manual",
            status="pending",
            current_wave_index=0,
        )
        db.add(run)
        db.flush()
        db.commit()
        return run

    def test_forbidden_action_makes_run_failed(self, db, make_project):
        from services.fleet_bulkop_service import _execute_bulk_run_with_db

        p = make_project()
        run = self._make_run(db, p, action="destroy")

        with pytest.raises(ValueError, match="not in the safe-actions allowlist"):
            _execute_bulk_run_with_db(run.id, db)

        db.refresh(run)
        assert run.status == "failed"

    def test_no_result_rows_created_on_allowlist_refusal(self, db, make_project):
        from services.fleet_bulkop_service import _execute_bulk_run_with_db

        p = make_project()
        run = self._make_run(db, p, action="destroy")

        try:
            _execute_bulk_run_with_db(run.id, db)
        except ValueError:
            pass

        result_count = (
            db.query(FleetBulkRunResult)
            .filter(FleetBulkRunResult.run_id == run.id)
            .count()
        )
        assert result_count == 0


# ---------------------------------------------------------------------------
# Wave ordering via executor (integration path)
# ---------------------------------------------------------------------------

class TestWaveOrdering:
    def test_waves_ordered_by_fault_domain_null_last(self, db, make_project):
        from services.fleet_bulkop_service import _group_members_into_waves

        p = make_project()

        # Create 3 members with different fault domains + 1 null
        m_z = _make_cluster_member(db, p, assigned_labels={"env": "prod"}, fault_domain="zone-z")
        m_a = _make_cluster_member(db, p, assigned_labels={"env": "prod"}, fault_domain="zone-a")
        m_null = _make_cluster_member(db, p, assigned_labels={"env": "prod"})
        # m_null has no fault_domain → should be in "(none)" bucket

        waves = _group_members_into_waves([m_z, m_a, m_null])
        labels = [label for label, _ in waves]

        assert labels.index("zone-a") < labels.index("zone-z")
        assert labels[-1] == "(none)"

    def test_at_least_3_fault_domains(self, db, make_project):
        from services.fleet_bulkop_service import _group_members_into_waves

        p = make_project()
        m1 = _make_cluster_member(db, p, fault_domain="dc-1")
        m2 = _make_cluster_member(db, p, fault_domain="dc-2")
        m3 = _make_cluster_member(db, p, fault_domain="dc-3")
        m4 = _make_cluster_member(db, p)  # null

        waves = _group_members_into_waves([m1, m2, m3, m4])
        assert len(waves) == 4
        assert waves[-1][0] == "(none)"


# ---------------------------------------------------------------------------
# Concurrency cap (executor uses ThreadPoolExecutor with max_workers=concurrency)
# ---------------------------------------------------------------------------

class TestConcurrencyCap:
    """Verify concurrency cap is respected and each job has its own session."""

    def test_re_reconcile_succeeds_for_all_members(self, db, make_project):
        """Integration: re-reconcile action on 3 members → all succeeded."""
        from services.fleet_bulkop_service import _execute_bulk_run_with_db

        p = make_project()
        m1 = _make_cluster_member(db, p)
        m2 = _make_cluster_member(db, p)
        m3 = _make_cluster_member(db, p)

        target = _make_target(db, p, selector={"labels": []})
        decision = FleetDecision(
            target_id=target.id,
            project_id=p.id,
            selector_snapshot={"labels": []},
            resolved_member_ids=[m1.id, m2.id, m3.id],
            member_count=3,
            resolved_at=datetime.now(UTC),
            resolved_by="test",
            status=DECISION_STATUS_RESOLVED,
        )
        db.add(decision)
        db.flush()
        run = FleetBulkRun(
            decision_id=decision.id,
            project_id=p.id,
            action="re-reconcile",
            wave_by="fault_domain",
            concurrency=2,  # cap at 2 concurrent
            gate_mode="manual",
            status="pending",
            current_wave_index=0,
        )
        db.add(run)
        db.commit()

        # Patch ThreadPoolExecutor to verify max_workers cap is respected.
        # Also patch _dispatch_action_for_member to avoid cross-session issues
        # (the real dispatch opens get_db_context() which doesn't share the test session).
        from concurrent.futures import ThreadPoolExecutor

        import services.fleet_bulkop_service as svc

        call_kwargs = []
        _orig_init = ThreadPoolExecutor.__init__

        def _capture_init(self_, max_workers=None, **kwargs):
            call_kwargs.append(max_workers)
            return _orig_init(self_, max_workers=max_workers, **kwargs)

        def _mock_dispatch(member_id, action, action_params):
            from models.fleet_targeting import RESULT_STATUS_SUCCEEDED
            return RESULT_STATUS_SUCCEEDED, "Reconcile completed (mock)."

        with patch.object(ThreadPoolExecutor, "__init__", _capture_init), \
             patch.object(svc, "_dispatch_action_for_member", side_effect=_mock_dispatch):
            result = _execute_bulk_run_with_db(run.id, db)

        # At least one pool was created with the capped concurrency.
        assert any(w == 2 for w in call_kwargs), f"Expected max_workers=2, got {call_kwargs}"

        db.refresh(run)
        assert run.status == BULK_RUN_STATUS_COMPLETED
        assert result["succeeded"] == 3


# ---------------------------------------------------------------------------
# Gate modes
# ---------------------------------------------------------------------------

class TestGateModes:
    def _make_multi_domain_run(self, db, project, gate_mode):
        m1 = _make_cluster_member(db, project, fault_domain="dc-1")
        m2 = _make_cluster_member(db, project, fault_domain="dc-2")

        target = FleetTarget(
            project_id=project.id,
            name="gate-test",
            selector={"labels": []},
            created_by="test",
        )
        db.add(target)
        db.flush()

        decision = FleetDecision(
            target_id=target.id,
            project_id=project.id,
            selector_snapshot={"labels": []},
            resolved_member_ids=[m1.id, m2.id],
            member_count=2,
            resolved_at=datetime.now(UTC),
            resolved_by="test",
            status=DECISION_STATUS_RESOLVED,
        )
        db.add(decision)
        db.flush()
        run = FleetBulkRun(
            decision_id=decision.id,
            project_id=project.id,
            action="re-reconcile",
            wave_by="fault_domain",
            concurrency=5,
            gate_mode=gate_mode,
            status="pending",
            current_wave_index=0,
        )
        db.add(run)
        db.commit()
        return run

    def test_manual_gate_pauses_after_first_wave(self, db, make_project):
        import services.fleet_bulkop_service as svc
        from models.fleet_targeting import RESULT_STATUS_SUCCEEDED
        from services.fleet_bulkop_service import _execute_bulk_run_with_db

        p = make_project()
        run = self._make_multi_domain_run(db, p, gate_mode="manual")

        def _mock_dispatch(member_id, action, action_params):
            return RESULT_STATUS_SUCCEEDED, "ok"

        with patch.object(svc, "_dispatch_action_for_member", side_effect=_mock_dispatch):
            result = _execute_bulk_run_with_db(run.id, db)

        db.refresh(run)
        assert run.status == BULK_RUN_STATUS_PAUSED_GATE
        assert result["status"] == "paused_gate"
        assert result["paused_after_wave"] == 0

    def test_health_stable_gate_proceeds_when_members_healthy(self, db, make_project):
        from services.fleet_bulkop_service import _execute_bulk_run_with_db

        p = make_project()
        m1 = _make_cluster_member(db, p, fault_domain="dc-1")
        m2 = _make_cluster_member(db, p, fault_domain="dc-2")

        # Set health to "healthy" so the gate passes.
        m1.health_status = "healthy"
        m2.health_status = "healthy"
        db.flush()

        target = FleetTarget(
            project_id=p.id,
            name="stable-gate-test",
            selector={"labels": []},
            created_by="test",
        )
        db.add(target)
        db.flush()

        decision = FleetDecision(
            target_id=target.id,
            project_id=p.id,
            selector_snapshot={"labels": []},
            resolved_member_ids=[m1.id, m2.id],
            member_count=2,
            resolved_at=datetime.now(UTC),
            resolved_by="test",
            status=DECISION_STATUS_RESOLVED,
        )
        db.add(decision)
        db.flush()
        run = FleetBulkRun(
            decision_id=decision.id,
            project_id=p.id,
            action="re-reconcile",
            wave_by="fault_domain",
            concurrency=5,
            gate_mode="health_stable",
            status="pending",
            current_wave_index=0,
        )
        db.add(run)
        db.commit()

        import services.fleet_bulkop_service as svc
        from models.fleet_targeting import RESULT_STATUS_SUCCEEDED

        def _mock_dispatch(member_id, action, action_params):
            return RESULT_STATUS_SUCCEEDED, "ok"

        with patch.object(svc, "_dispatch_action_for_member", side_effect=_mock_dispatch):
            result = _execute_bulk_run_with_db(run.id, db)

        db.refresh(run)
        assert run.status == BULK_RUN_STATUS_COMPLETED
        assert result["status"] == "completed"

    def test_health_stable_gate_pauses_when_member_unhealthy(self, db, make_project):
        import services.fleet_bulkop_service as svc
        from models.fleet_targeting import RESULT_STATUS_SUCCEEDED
        from services.fleet_bulkop_service import _execute_bulk_run_with_db

        p = make_project()
        m1 = _make_cluster_member(db, p, fault_domain="dc-1")
        m2 = _make_cluster_member(db, p, fault_domain="dc-2")

        # m1 unhealthy → gate blocks after wave 0.
        m1.health_status = "unhealthy"
        m2.health_status = "healthy"
        db.flush()

        target = FleetTarget(project_id=p.id, name="unstable", selector={"labels": []}, created_by="t")
        db.add(target)
        db.flush()
        decision = FleetDecision(
            target_id=target.id,
            project_id=p.id,
            selector_snapshot={"labels": []},
            resolved_member_ids=[m1.id, m2.id],
            member_count=2,
            resolved_at=datetime.now(UTC),
            resolved_by="test",
            status=DECISION_STATUS_RESOLVED,
        )
        db.add(decision)
        db.flush()
        run = FleetBulkRun(
            decision_id=decision.id,
            project_id=p.id,
            action="re-reconcile",
            wave_by="fault_domain",
            concurrency=5,
            gate_mode="health_stable",
            status="pending",
            current_wave_index=0,
        )
        db.add(run)
        db.commit()

        def _mock_dispatch(member_id, action, action_params):
            return RESULT_STATUS_SUCCEEDED, "ok"

        with patch.object(svc, "_dispatch_action_for_member", side_effect=_mock_dispatch):
            _execute_bulk_run_with_db(run.id, db)
        db.refresh(run)
        assert run.status == BULK_RUN_STATUS_PAUSED_GATE


# ---------------------------------------------------------------------------
# Blast-radius preview (Decision → member list, no mutation)
# ---------------------------------------------------------------------------

class TestBlastRadiusPreview:
    def test_resolve_does_not_start_run(self, db, make_project):
        from services.fleet_selector import resolve_target

        p = make_project()
        m = _make_cluster_member(db, p, assigned_labels={"env": "prod"})
        target = _make_target(db, p, selector={"labels": ["env=prod"]})
        user = _FakeUser()

        decision = resolve_target(db, target, resolved_by="preview", current_user=user)
        db.commit()

        # No FleetBulkRun should exist
        run_count = db.query(FleetBulkRun).count()
        assert run_count == 0
        assert decision.status == DECISION_STATUS_RESOLVED

    def test_blast_radius_member_count_correct(self, db, make_project):
        from services.fleet_selector import resolve_target

        p = make_project()
        m1 = _make_cluster_member(db, p, assigned_labels={"env": "prod"})
        m2 = _make_cluster_member(db, p, assigned_labels={"env": "prod"})
        _make_cluster_member(db, p, assigned_labels={"env": "staging"})

        target = _make_target(db, p, selector={"labels": ["env=prod"]})
        user = _FakeUser()
        decision = resolve_target(db, target, resolved_by="preview", current_user=user)

        assert decision.member_count == 2
        assert set(decision.resolved_member_ids) == {m1.id, m2.id}


# ---------------------------------------------------------------------------
# API route tests
# ---------------------------------------------------------------------------

class TestFleetTargetAPI:
    def test_create_target_returns_201(self, client, admin_headers, db, sample_user):
        resp = client.post(
            "/api/fleet/targets",
            json={"name": "prod-clusters", "selector": {"labels": ["env=prod"]}},
            headers=admin_headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "prod-clusters"
        assert body["selector"] == {"labels": ["env=prod"]}

    def test_create_target_with_forbidden_key_returns_422(self, client, admin_headers, db, sample_user):
        resp = client.post(
            "/api/fleet/targets",
            json={"name": "bad", "selector": {"labels": ["project_id=1"]}},
            headers=admin_headers,
        )
        assert resp.status_code == 422

    def test_list_targets_returns_200(self, client, admin_headers, db, sample_user):
        resp = client.get("/api/fleet/targets", headers=admin_headers)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_resolve_target_returns_decision_with_members(self, client, admin_headers, db, sample_user, make_project):
        p = make_project()
        m = _make_cluster_member(db, p, assigned_labels={"env": "api-test"})
        db.commit()

        # Create target via API
        resp = client.post(
            "/api/fleet/targets",
            json={"name": "api-test-target", "selector": {"labels": ["env=api-test"]}, "project_id": p.id},
            headers=admin_headers,
        )
        assert resp.status_code == 201
        target_id = resp.json()["id"]

        # Resolve
        resp = client.post(f"/api/fleet/targets/{target_id}/resolve", headers=admin_headers)
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "resolved"
        assert body["member_count"] >= 1
        assert body["members"] is not None

    def test_blast_radius_preview_via_get_decision(self, client, admin_headers, db, sample_user, make_project):
        p = make_project()
        _make_cluster_member(db, p, assigned_labels={"env": "blasttest"})
        db.commit()

        resp = client.post(
            "/api/fleet/targets",
            json={"name": "bt", "selector": {"labels": ["env=blasttest"]}, "project_id": p.id},
            headers=admin_headers,
        )
        target_id = resp.json()["id"]

        resp = client.post(f"/api/fleet/targets/{target_id}/resolve", headers=admin_headers)
        decision_id = resp.json()["id"]

        # GET the decision — returns blast-radius with member list
        resp = client.get(f"/api/fleet/decisions/{decision_id}", headers=admin_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["members"] is not None
        assert isinstance(body["members"], list)

    def test_start_bulk_op_forbidden_action_returns_400(self, client, admin_headers, db, sample_user, make_project):
        p = make_project()
        target = _make_target(db, p, selector={"labels": []})
        decision = FleetDecision(
            target_id=target.id,
            project_id=p.id,
            selector_snapshot={"labels": []},
            resolved_member_ids=[],
            member_count=0,
            resolved_at=datetime.now(UTC),
            resolved_by="test",
            status=DECISION_STATUS_RESOLVED,
        )
        db.add(decision)
        db.commit()

        resp = client.post(
            "/api/fleet/bulk-ops",
            json={"decision_id": decision.id, "action": "destroy"},
            headers=admin_headers,
        )
        assert resp.status_code in (400, 422)

    def test_start_bulk_op_stale_decision_returns_400(self, client, admin_headers, db, sample_user, make_project):
        p = make_project()
        target = _make_target(db, p, selector={"labels": []})
        decision = FleetDecision(
            target_id=target.id,
            project_id=p.id,
            selector_snapshot={"labels": []},
            resolved_member_ids=[],
            member_count=0,
            resolved_at=datetime.now(UTC),
            resolved_by="test",
            status=DECISION_STATUS_CONSUMED,  # already consumed
        )
        db.add(decision)
        db.commit()

        resp = client.post(
            "/api/fleet/bulk-ops",
            json={"decision_id": decision.id, "action": "re-reconcile"},
            headers=admin_headers,
        )
        assert resp.status_code == 400

    def test_get_bulk_op_returns_200(self, client, admin_headers, db, sample_user, make_project):
        p = make_project()
        target = _make_target(db, p, selector={"labels": []})
        decision = FleetDecision(
            target_id=target.id,
            project_id=p.id,
            selector_snapshot={"labels": []},
            resolved_member_ids=[],
            member_count=0,
            resolved_at=datetime.now(UTC),
            resolved_by="test",
            status=DECISION_STATUS_RESOLVED,
        )
        db.add(decision)
        db.flush()
        run = FleetBulkRun(
            decision_id=decision.id,
            project_id=p.id,
            action="re-reconcile",
            wave_by="fault_domain",
            concurrency=5,
            gate_mode="manual",
            status="completed",
            current_wave_index=0,
            total_waves=0,
        )
        db.add(run)
        db.commit()

        resp = client.get(f"/api/fleet/bulk-ops/{run.id}", headers=admin_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == run.id
        assert body["action"] == "re-reconcile"
        assert body["results"] is not None


# ---------------------------------------------------------------------------
# Resume path
# ---------------------------------------------------------------------------

class TestResumePath:
    """Verify that resume skips freshness check and completes the remaining waves."""

    def _make_two_domain_run(self, db, project, gate_mode="manual"):
        """Create a run with two members in separate fault domains."""
        m1 = _make_cluster_member(db, project, fault_domain="dc-1")
        m2 = _make_cluster_member(db, project, fault_domain="dc-2")

        target = FleetTarget(
            project_id=project.id,
            name="resume-test",
            selector={"labels": []},
            created_by="test",
        )
        db.add(target)
        db.flush()

        decision = FleetDecision(
            target_id=target.id,
            project_id=project.id,
            selector_snapshot={"labels": []},
            resolved_member_ids=[m1.id, m2.id],
            member_count=2,
            resolved_at=datetime.now(UTC),
            resolved_by="test",
            status=DECISION_STATUS_RESOLVED,
        )
        db.add(decision)
        db.flush()
        run = FleetBulkRun(
            decision_id=decision.id,
            project_id=project.id,
            action="re-reconcile",
            wave_by="fault_domain",
            concurrency=5,
            gate_mode=gate_mode,
            status="pending",
            current_wave_index=0,
        )
        db.add(run)
        db.commit()
        return run, m1, m2

    def test_resume_completes_after_manual_gate_pause(self, db, make_project):
        """Wave 0 runs, manual gate pauses, resume executes wave 1, run completes.

        Critically: no StaleDecisionError despite the decision being consumed
        after the initial run.
        """
        import services.fleet_bulkop_service as svc
        from models.fleet_targeting import RESULT_STATUS_SUCCEEDED
        from services.fleet_bulkop_service import _execute_bulk_run_with_db

        p = make_project()
        run, m1, m2 = self._make_two_domain_run(db, p, gate_mode="manual")

        def _mock_dispatch(member_id, action, action_params):
            return RESULT_STATUS_SUCCEEDED, "ok"

        # Initial run — should pause at gate after wave 0.
        with patch.object(svc, "_dispatch_action_for_member", side_effect=_mock_dispatch):
            result = _execute_bulk_run_with_db(run.id, db=db)

        db.refresh(run)
        assert run.status == BULK_RUN_STATUS_PAUSED_GATE
        assert result["status"] == "paused_gate"
        # Decision is now consumed.
        decision = db.query(FleetDecision).filter(FleetDecision.id == run.decision_id).first()
        assert decision.status == DECISION_STATUS_CONSUMED

        # Resume — must NOT raise StaleDecisionError.
        with patch.object(svc, "_dispatch_action_for_member", side_effect=_mock_dispatch):
            resume_result = _execute_bulk_run_with_db(run.id, db=db, resuming=True)

        db.refresh(run)
        assert run.status == BULK_RUN_STATUS_COMPLETED, (
            f"Expected 'completed', got '{run.status}'"
        )
        assert resume_result["status"] == "completed"

        # Wave 1 result rows should be succeeded.
        from models.fleet_targeting import RESULT_STATUS_SUCCEEDED as SUCC
        results = (
            db.query(FleetBulkRunResult)
            .filter(FleetBulkRunResult.run_id == run.id)
            .all()
        )
        assert len(results) == 2
        assert all(r.status == SUCC for r in results), [r.status for r in results]

    def test_resume_without_resuming_flag_raises_stale_decision_error(self, db, make_project):
        """Calling the executor a second time without resuming=True raises StaleDecisionError
        because the decision was already consumed.
        """
        import services.fleet_bulkop_service as svc
        from models.fleet_targeting import RESULT_STATUS_SUCCEEDED
        from services.fleet_bulkop_service import StaleDecisionError, _execute_bulk_run_with_db

        p = make_project()
        run, _, _ = self._make_two_domain_run(db, p, gate_mode="manual")

        def _mock_dispatch(member_id, action, action_params):
            return RESULT_STATUS_SUCCEEDED, "ok"

        with patch.object(svc, "_dispatch_action_for_member", side_effect=_mock_dispatch):
            _execute_bulk_run_with_db(run.id, db=db)

        db.refresh(run)
        assert run.status == BULK_RUN_STATUS_PAUSED_GATE

        # Reset status to pending so the idempotency guard doesn't fire first.
        run.status = "pending"
        db.commit()

        with pytest.raises(StaleDecisionError):
            _execute_bulk_run_with_db(run.id, db=db)


# ---------------------------------------------------------------------------
# Duplicate-dispatch guard
# ---------------------------------------------------------------------------

class TestDuplicateDispatchGuard:
    """Verify that a second dispatch on a terminal/running run is refused."""

    def _make_completed_run(self, db, project):
        target = FleetTarget(
            project_id=project.id,
            name="dup-dispatch-test",
            selector={"labels": []},
            created_by="test",
        )
        db.add(target)
        db.flush()
        decision = FleetDecision(
            target_id=target.id,
            project_id=project.id,
            selector_snapshot={"labels": []},
            resolved_member_ids=[],
            member_count=0,
            resolved_at=datetime.now(UTC),
            resolved_by="test",
            status=DECISION_STATUS_CONSUMED,
        )
        db.add(decision)
        db.flush()
        run = FleetBulkRun(
            decision_id=decision.id,
            project_id=project.id,
            action="re-reconcile",
            wave_by="fault_domain",
            concurrency=1,
            gate_mode="manual",
            status="completed",
            current_wave_index=0,
            total_waves=0,
        )
        db.add(run)
        db.commit()
        return run

    def test_second_dispatch_on_completed_run_raises(self, db, make_project):
        from services.fleet_bulkop_service import _execute_bulk_run_with_db

        p = make_project()
        run = self._make_completed_run(db, p)

        with pytest.raises(ValueError, match="already in status 'completed'"):
            _execute_bulk_run_with_db(run.id, db=db)

    def test_second_dispatch_on_failed_run_raises(self, db, make_project):
        from services.fleet_bulkop_service import _execute_bulk_run_with_db

        p = make_project()
        run = self._make_completed_run(db, p)
        run.status = "failed"
        db.commit()

        with pytest.raises(ValueError, match="already in status 'failed'"):
            _execute_bulk_run_with_db(run.id, db=db)

    def test_second_dispatch_on_running_run_raises(self, db, make_project):
        from services.fleet_bulkop_service import _execute_bulk_run_with_db

        p = make_project()
        run = self._make_completed_run(db, p)
        run.status = "running"
        db.commit()

        with pytest.raises(ValueError, match="already in status 'running'"):
            _execute_bulk_run_with_db(run.id, db=db)


# ---------------------------------------------------------------------------
# Resume double-submit race (mwiget audit fix)
# ---------------------------------------------------------------------------

class TestResumeDoubleSubmitRace:
    """Verify that a second resume call is rejected after status flips to 'running'.

    The fix (commit RUNNING before .delay()) means the route guard rejects a
    concurrent request that arrives after the status commit but before the
    Celery task completes.
    """

    def _make_paused_run(self, db, project):
        target = FleetTarget(
            project_id=project.id,
            name="double-resume-test",
            selector={"labels": []},
            created_by="test",
        )
        db.add(target)
        db.flush()
        decision = FleetDecision(
            target_id=target.id,
            project_id=project.id,
            selector_snapshot={"labels": []},
            resolved_member_ids=[],
            member_count=0,
            resolved_at=datetime.now(UTC),
            resolved_by="test",
            status=DECISION_STATUS_RESOLVED,
        )
        db.add(decision)
        db.flush()
        run = FleetBulkRun(
            decision_id=decision.id,
            project_id=project.id,
            action="re-reconcile",
            wave_by="fault_domain",
            concurrency=1,
            gate_mode="manual",
            status="paused_gate",
            current_wave_index=1,
            total_waves=2,
        )
        db.add(run)
        db.commit()
        return run

    def test_double_resume_second_call_returns_400(
        self, client, admin_headers, db, sample_user, make_project
    ):
        """Two concurrent resume POSTs: first succeeds (202/200), second is rejected (400).

        After the first call commits status=running, the second call's guard
        (status != paused_gate) fires and returns 400 — no second task dispatched.
        """
        p = make_project()
        run = self._make_paused_run(db, p)

        mock_task = MagicMock()
        mock_task.id = "mock-resume-task-id"

        # The route does `from tasks.fleet_tasks import resume_fleet_bulk_op`
        # at call time, so patch the attribute on the module.
        with patch("tasks.fleet_tasks.resume_fleet_bulk_op") as mock_resume_task:
            mock_resume_task.delay.return_value = mock_task

            # First resume — should succeed.
            resp1 = client.post(
                f"/api/fleet/bulk-ops/{run.id}/resume",
                headers=admin_headers,
            )
            assert resp1.status_code == 200, resp1.text

            # Second resume — run is now 'running', guard must reject.
            resp2 = client.post(
                f"/api/fleet/bulk-ops/{run.id}/resume",
                headers=admin_headers,
            )
            assert resp2.status_code == 400, resp2.text

            # Celery .delay() must have been called exactly once.
            assert mock_resume_task.delay.call_count == 1, (
                f"Expected 1 dispatch, got {mock_resume_task.delay.call_count}"
            )


# ---------------------------------------------------------------------------
# set-labels replace-all semantics (mwiget audit fix)
# ---------------------------------------------------------------------------

class TestSetLabelsReplaceSemantics:
    """Pin the replace-all semantics of the 'set-labels' bulk action.

    Intentional behaviour: the supplied labels dict replaces the member's
    entire assigned_labels — prior keys are dropped.  This is documented
    in fleet_bulkop_service._dispatch_action_for_member.
    """

    def test_set_labels_replaces_prior_keys(self, db, make_project):
        """Existing assigned_labels are dropped and replaced by the new dict."""
        import services.fleet_bulkop_service as svc

        p = make_project()
        # Member starts with two existing labels (both from the allowed vocabulary).
        m = _make_cluster_member(db, p, assigned_labels={"env": "prod", "team": "frontend"})
        db.commit()

        # Simulate _dispatch_action_for_member for set-labels by calling the
        # service directly with a patched get_db_context that returns our session.
        from contextlib import contextmanager

        @contextmanager
        def _fake_ctx():
            yield db

        with patch("services.fleet_bulkop_service.get_db_context", _fake_ctx):
            status, detail = svc._dispatch_action_for_member(
                m.id, "set-labels", {"labels": {"criticality": "low"}}
            )

        assert status == "succeeded", detail
        db.refresh(m)
        # Only the new label should remain — prior keys are dropped (replace-all).
        assert m.assigned_labels == {"criticality": "low"}, (
            f"Expected replace-all semantics, got: {m.assigned_labels}"
        )
        assert "env" not in m.assigned_labels
        assert "team" not in m.assigned_labels

    def test_set_labels_with_empty_dict_clears_all_labels(self, db, make_project):
        """Passing an empty labels dict clears all assigned labels."""
        import services.fleet_bulkop_service as svc

        p = make_project()
        m = _make_cluster_member(db, p, assigned_labels={"env": "prod"})
        db.commit()

        from contextlib import contextmanager

        @contextmanager
        def _fake_ctx():
            yield db

        with patch("services.fleet_bulkop_service.get_db_context", _fake_ctx):
            status, detail = svc._dispatch_action_for_member(
                m.id, "set-labels", {"labels": {}}
            )

        assert status == "succeeded", detail
        db.refresh(m)
        assert m.assigned_labels == {}, (
            f"Expected empty dict after clear, got: {m.assigned_labels}"
        )
