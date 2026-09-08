"""Service-level tests for benchmark baseline flagging + trends (D-020 UX/trends work).

Covers:
  - set_baseline: set / replace / non-completed rejection
  - unset_baseline
  - get_trends: filtering + ordering + baseline inclusion outside the limit window
"""

from datetime import UTC, datetime, timedelta

import pytest

from core.errors import BadRequestError, NotFoundError
from models.benchmark import BenchmarkConfig, BenchmarkRun
from models.enums import BenchmarkRunStatus
from services.benchmark_service import BenchmarkService
from tests.factories import BenchmarkTargetFactory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(db, *, target=None, status=BenchmarkRunStatus.COMPLETED, created_at=None, **overrides):
    run = BenchmarkRun(
        tool="aiperf",
        proxy=overrides.pop("proxy", "envoy"),
        model="tinyllama",
        base_url="http://envoy:10080",
        target_id=target.id if target else None,
        status=status,
        latency_p50=overrides.pop("latency_p50", 0.1),
        latency_p99=overrides.pop("latency_p99", 0.2),
        overall_rps=overrides.pop("overall_rps", 100.0),
        **overrides,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    if created_at is not None:
        run.created_at = created_at
        db.commit()
        db.refresh(run)
    return run


# ---------------------------------------------------------------------------
# set_baseline
# ---------------------------------------------------------------------------


class TestSetBaseline:
    def test_setBaseline_completedRun_marksBaseline(self, db):
        target = BenchmarkTargetFactory(db)
        run = _run(db, target=target)

        svc = BenchmarkService(db)
        result = svc.set_baseline(run.id)
        db.commit()

        assert result.is_baseline is True
        db.refresh(run)
        assert run.is_baseline is True

    def test_setBaseline_nonCompletedRun_raises400(self, db):
        target = BenchmarkTargetFactory(db)
        run = _run(db, target=target, status=BenchmarkRunStatus.RUNNING)

        svc = BenchmarkService(db)
        with pytest.raises(BadRequestError):
            svc.set_baseline(run.id)

    def test_setBaseline_missingRun_raisesNotFound(self, db):
        svc = BenchmarkService(db)
        with pytest.raises(NotFoundError):
            svc.set_baseline(999999)

    def test_setBaseline_sameContext_replacesPreviousBaseline(self, db):
        target = BenchmarkTargetFactory(db)
        first = _run(db, target=target, config_id=None, scenario_key=None)
        second = _run(db, target=target, config_id=None, scenario_key=None)

        svc = BenchmarkService(db)
        svc.set_baseline(first.id)
        db.commit()
        svc.set_baseline(second.id)
        db.commit()

        db.refresh(first)
        db.refresh(second)
        assert first.is_baseline is False
        assert second.is_baseline is True

    def test_setBaseline_sequentialReplaces_invariantHoldsAtMostOnePerContext(self, db):
        """Guards the one-baseline-per-context invariant the locking in
        set_baseline exists to protect — asserted via a raw DB count, not just
        the two runs under test, so a regression that leaves a stray flagged
        row anywhere in the context would be caught."""
        target = BenchmarkTargetFactory(db)
        runs = [_run(db, target=target, config_id=None, scenario_key=None) for _ in range(4)]

        svc = BenchmarkService(db)
        for r in runs:
            svc.set_baseline(r.id)
            db.commit()

        baseline_count = (
            db.query(BenchmarkRun)
            .filter(
                BenchmarkRun.target_id == target.id,
                BenchmarkRun.scenario_key.is_(None),
                BenchmarkRun.config_id.is_(None),
                BenchmarkRun.is_baseline.is_(True),
            )
            .count()
        )
        assert baseline_count == 1

        db.refresh(runs[-1])
        assert runs[-1].is_baseline is True

    def test_setBaseline_differentContext_doesNotReplace(self, db):
        target = BenchmarkTargetFactory(db)
        other_target = BenchmarkTargetFactory(db)
        first = _run(db, target=target)
        second = _run(db, target=other_target)

        svc = BenchmarkService(db)
        svc.set_baseline(first.id)
        db.commit()
        svc.set_baseline(second.id)
        db.commit()

        db.refresh(first)
        db.refresh(second)
        assert first.is_baseline is True
        assert second.is_baseline is True

    def test_setBaseline_missingTargetId_raises400(self, db):
        run = _run(db, target=None)

        svc = BenchmarkService(db)
        with pytest.raises(BadRequestError) as exc_info:
            svc.set_baseline(run.id)
        assert exc_info.value.code == "MISSING_TARGET_ID"

    def test_setBaseline_differentProxy_doesNotReplace(self, db):
        target = BenchmarkTargetFactory(db)
        first = _run(db, target=target, proxy="envoy")
        second = _run(db, target=target, proxy="haproxy")

        svc = BenchmarkService(db)
        svc.set_baseline(first.id)
        db.commit()
        svc.set_baseline(second.id)
        db.commit()

        db.refresh(first)
        db.refresh(second)
        assert first.is_baseline is True
        assert second.is_baseline is True

    def test_setBaseline_differentVariantLabel_doesNotReplace(self, db):
        target = BenchmarkTargetFactory(db)
        first = _run(db, target=target, variant_label="concurrency-10")
        second = _run(db, target=target, variant_label="concurrency-50")

        svc = BenchmarkService(db)
        svc.set_baseline(first.id)
        db.commit()
        svc.set_baseline(second.id)
        db.commit()

        db.refresh(first)
        db.refresh(second)
        assert first.is_baseline is True
        assert second.is_baseline is True


# ---------------------------------------------------------------------------
# _attach_baseline_context
# ---------------------------------------------------------------------------


class TestAttachBaselineContext:
    def test_attachBaselineContext_populatesBaselineMetricsOnListRuns(self, db):
        target = BenchmarkTargetFactory(db)
        baseline = _run(
            db,
            target=target,
            proxy="envoy",
            variant_label="c10",
            latency_p99=0.15,
            overall_rps=150.0,
        )
        svc = BenchmarkService(db)
        svc.set_baseline(baseline.id)
        db.commit()

        newer = _run(
            db,
            target=target,
            proxy="envoy",
            variant_label="c10",
            latency_p99=0.25,
            overall_rps=120.0,
        )

        runs, _ = svc.list_runs(proxy="envoy")
        newer_run = next(r for r in runs if r.id == newer.id)
        assert newer_run.baseline_latency_p99 == 0.15
        assert newer_run.baseline_overall_rps == 150.0

    def test_attachBaselineContext_differentProxy_doesNotPopulate(self, db):
        target = BenchmarkTargetFactory(db)
        baseline = _run(
            db,
            target=target,
            proxy="envoy",
            latency_p99=0.15,
            overall_rps=150.0,
        )
        svc = BenchmarkService(db)
        svc.set_baseline(baseline.id)
        db.commit()

        haproxy_run = _run(db, target=target, proxy="haproxy")

        runs, _ = svc.list_runs()
        haproxy_res = next(r for r in runs if r.id == haproxy_run.id)
        assert haproxy_res.baseline_latency_p99 is None
        assert haproxy_res.baseline_overall_rps is None

    def test_attachBaselineContext_differentVariantLabel_doesNotPopulate(self, db):
        target = BenchmarkTargetFactory(db)
        baseline = _run(
            db,
            target=target,
            variant_label="c10",
            latency_p99=0.15,
            overall_rps=150.0,
        )
        svc = BenchmarkService(db)
        svc.set_baseline(baseline.id)
        db.commit()

        other_variant_run = _run(db, target=target, variant_label="c50")

        runs, _ = svc.list_runs()
        res = next(r for r in runs if r.id == other_variant_run.id)
        assert res.baseline_latency_p99 is None
        assert res.baseline_overall_rps is None

    def test_attachBaselineContext_baselineRunItselfHasNoReference(self, db):
        target = BenchmarkTargetFactory(db)
        baseline = _run(db, target=target, latency_p99=0.15, overall_rps=150.0)
        svc = BenchmarkService(db)
        svc.set_baseline(baseline.id)
        db.commit()

        runs, _ = svc.list_runs()
        baseline_res = next(r for r in runs if r.id == baseline.id)
        assert baseline_res.is_baseline is True
        assert baseline_res.baseline_latency_p99 is None
        assert baseline_res.baseline_overall_rps is None


# ---------------------------------------------------------------------------
# unset_baseline
# ---------------------------------------------------------------------------


class TestUnsetBaseline:
    def test_unsetBaseline_clearsFlag(self, db):
        target = BenchmarkTargetFactory(db)
        run = _run(db, target=target)

        svc = BenchmarkService(db)
        svc.set_baseline(run.id)
        db.commit()

        result = svc.unset_baseline(run.id)
        db.commit()

        assert result.is_baseline is False
        db.refresh(run)
        assert run.is_baseline is False

    def test_unsetBaseline_notCurrentlyBaseline_isNoop(self, db):
        target = BenchmarkTargetFactory(db)
        run = _run(db, target=target)

        svc = BenchmarkService(db)
        result = svc.unset_baseline(run.id)
        db.commit()

        assert result.is_baseline is False


# ---------------------------------------------------------------------------
# get_trends
# ---------------------------------------------------------------------------


class TestGetTrends:
    def test_getTrends_filtersByTargetAndOrdersOldestFirst(self, db):
        target = BenchmarkTargetFactory(db)
        other_target = BenchmarkTargetFactory(db)
        now = datetime.now(UTC)

        older = _run(db, target=target, created_at=now - timedelta(hours=2))
        newer = _run(db, target=target, created_at=now - timedelta(hours=1))
        _run(db, target=other_target, created_at=now)  # different target — excluded

        svc = BenchmarkService(db)
        result = svc.get_trends(target_id=target.id)

        point_ids = [p.id for p in result["points"]]
        assert point_ids == [older.id, newer.id]
        assert result["baseline_run_id"] is None

    def test_getTrends_excludesNonCompletedRuns(self, db):
        target = BenchmarkTargetFactory(db)
        _run(db, target=target, status=BenchmarkRunStatus.RUNNING)
        completed = _run(db, target=target, status=BenchmarkRunStatus.COMPLETED)

        svc = BenchmarkService(db)
        result = svc.get_trends(target_id=target.id)

        assert [p.id for p in result["points"]] == [completed.id]

    def test_getTrends_includesBaselineOutsideLimitWindow(self, db):
        target = BenchmarkTargetFactory(db)
        now = datetime.now(UTC)

        baseline = _run(db, target=target, created_at=now - timedelta(days=10))
        svc = BenchmarkService(db)
        svc.set_baseline(baseline.id)
        db.commit()

        for i in range(3):
            _run(db, target=target, created_at=now - timedelta(hours=i))

        result = svc.get_trends(target_id=target.id, limit=2)

        point_ids = {p.id for p in result["points"]}
        assert baseline.id in point_ids
        assert result["baseline_run_id"] == baseline.id
        baseline_point = next(p for p in result["points"] if p.id == baseline.id)
        assert baseline_point.is_baseline is True

    def test_getTrends_scenarioKeyFilter(self, db):
        target = BenchmarkTargetFactory(db)
        matching = _run(db, target=target, scenario_key="prefix-cache")
        _run(db, target=target, scenario_key="burst")

        svc = BenchmarkService(db)
        result = svc.get_trends(target_id=target.id, scenario_key="prefix-cache")

        assert [p.id for p in result["points"]] == [matching.id]

    def test_getTrends_mixedContextSeriesHasNoBaseline(self, db):
        """Baseline only makes sense for single-context series. When an unfiltered query
        returns runs from multiple (proxy, config, scenario) contexts, do not return a baseline."""
        target = BenchmarkTargetFactory(db)
        now = datetime.now(UTC)

        # Create a baseline in one proxy context
        baseline_envoy = _run(db, target=target, proxy="envoy", scenario_key="baseline",
                             created_at=now - timedelta(days=10))
        svc = BenchmarkService(db)
        svc.set_baseline(baseline_envoy.id)
        db.commit()

        # Create runs in different proxy contexts (series is mixed)
        run_envoy = _run(db, target=target, proxy="envoy", scenario_key="baseline",
                        created_at=now - timedelta(hours=2))
        run_nginx = _run(db, target=target, proxy="nginx", scenario_key="baseline",
                        created_at=now - timedelta(hours=1))

        # Query trends without proxy filter (returns mixed contexts)
        result = svc.get_trends(target_id=target.id)

        # Points include both proxies (mixed context)
        point_ids = {p.id for p in result["points"]}
        assert run_envoy.id in point_ids
        assert run_nginx.id in point_ids
        # baseline_run_id should be None because series is mixed-context
        assert result["baseline_run_id"] is None

    def test_getTrends_singleContextWithBaselineOutsideLimit(self, db):
        """Baseline outside the limit window is still included for single-context series."""
        target = BenchmarkTargetFactory(db)
        now = datetime.now(UTC)

        # Create a baseline far in the past
        baseline = _run(db, target=target, proxy="envoy", scenario_key="baseline",
                       created_at=now - timedelta(days=100))
        svc = BenchmarkService(db)
        svc.set_baseline(baseline.id)
        db.commit()

        # Create recent runs in the same context
        run1 = _run(db, target=target, proxy="envoy", scenario_key="baseline",
                   created_at=now - timedelta(hours=2))
        run2 = _run(db, target=target, proxy="envoy", scenario_key="baseline",
                   created_at=now - timedelta(hours=1))

        # Query with limit=1 (should only fetch 1 run, but baseline should still be included)
        result = svc.get_trends(target_id=target.id, proxy="envoy", scenario_key="baseline", limit=1)

        # Points should include both the recent run AND the old baseline
        point_ids = {p.id for p in result["points"]}
        assert baseline.id in point_ids
        assert run2.id in point_ids  # Most recent
        # baseline_run_id should be set
        assert result["baseline_run_id"] == baseline.id


# ---------------------------------------------------------------------------
# compare_runs — context mismatch warning
# ---------------------------------------------------------------------------


class TestCompareContextMismatch:
    def test_compareRuns_sameConfigAndScenario_noMismatch(self, db):
        target = BenchmarkTargetFactory(db)
        a = _run(db, target=target, scenario_key="prefix-cache")
        b = _run(db, target=target, scenario_key="prefix-cache")

        svc = BenchmarkService(db)
        result = svc.compare_runs([a.id, b.id])

        assert result["context_mismatch"] is False

    def test_compareRuns_differentScenarioKey_flagsMismatch(self, db):
        target = BenchmarkTargetFactory(db)
        a = _run(db, target=target, scenario_key="prefix-cache")
        b = _run(db, target=target, scenario_key="burst")

        svc = BenchmarkService(db)
        result = svc.compare_runs([a.id, b.id])

        assert result["context_mismatch"] is True

    def test_compareRuns_differentConfigId_flagsMismatch(self, db):
        target = BenchmarkTargetFactory(db)
        config = BenchmarkConfig(name="alt-config", config_json={"concurrency": 200})
        db.add(config)
        db.commit()
        db.refresh(config)

        a = _run(db, target=target, config_id=None)
        b = _run(db, target=target, config_id=config.id)

        svc = BenchmarkService(db)
        result = svc.compare_runs([a.id, b.id])

        assert result["context_mismatch"] is True

    def test_compareRuns_differentProxy_noMismatch(self, db):
        """Proxies are deliberately-varied comparison dimensions on the Compare tab."""
        target = BenchmarkTargetFactory(db)
        a = _run(db, target=target, proxy="envoy")
        b = _run(db, target=target, proxy="haproxy")

        svc = BenchmarkService(db)
        result = svc.compare_runs([a.id, b.id])

        assert result["context_mismatch"] is False

    def test_compareRuns_differentVariantLabel_noMismatch(self, db):
        """Variants are deliberately-varied comparison dimensions on the Compare tab."""
        target = BenchmarkTargetFactory(db)
        a = _run(db, target=target, variant_label="c10")
        b = _run(db, target=target, variant_label="c50")

        svc = BenchmarkService(db)
        result = svc.compare_runs([a.id, b.id])

        assert result["context_mismatch"] is False

