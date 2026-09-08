"""
Benchmark Service — DB and business logic for LLM inference load testing.

Phase 2: Forge Dashboard — stores results from aiperf/llm-bench, provides
proxy-vs-proxy comparison, and manages test client agents.

⚠️  TERMINOLOGY: BenchmarkRun = load test, BenchmarkAgent = test client machine.
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import desc, func
from sqlalchemy.orm import joinedload

from core.errors import BadRequestError, ConflictError, NotFoundError
from models.benchmark import (
    BenchmarkAgent,
    BenchmarkConfig,
    BenchmarkRun,
    BenchmarkRunGroup,
    BenchmarkTarget,
    ProxyDeployment,
)
from models.enums import BenchmarkAgentStatus, BenchmarkRunStatus
from services.base_service import BaseService
from services.benchmark_scenarios import expand_scenario, get_scenario

logger = logging.getLogger(__name__)


class BenchmarkService(BaseService):
    """Service layer for benchmark operations."""

    # ================================================================
    # Result Ingestion — POST /api/benchmarks/results
    # ================================================================

    def ingest_result(self, result_data: dict) -> BenchmarkRun:
        """Ingest a BenchmarkResult JSON pushed by aiperf CLI.

        Creates a BenchmarkRun row with status=COMPLETED and extracts
        denormalized metrics for quick querying.
        """
        labels = result_data.get("labels", {})
        config = result_data.get("config", {})
        latency = result_data.get("latency", {})
        throughput = result_data.get("throughput", {})
        proxy_detection = result_data.get("proxy_detection", {})

        # Determine proxy — prefer labels.proxy, fall back to proxy_detection
        proxy = labels.get("proxy", config.get("proxy", "nodeport"))
        if proxy == "nodeport" and proxy_detection:
            detected = proxy_detection.get("detected_type", "nodeport")
            if detected != "unknown":
                proxy = detected

        # Determine tool from config or default to aiperf
        tool = config.get("tool", "aiperf")

        # Look up agent if agent_name is provided
        agent_id = None
        agent_name = result_data.get("agent_name")
        if agent_name:
            agent = self.db.query(BenchmarkAgent).filter(
                BenchmarkAgent.name == agent_name
            ).first()
            if agent:
                agent_id = agent.id

        # Resolve optional linkage FKs defensively — only set if the row exists,
        # so a stale id from the CLI never breaks ingestion (mirrors agent lookup).
        target_id = result_data.get("target_id")
        if target_id is not None and not self.db.query(BenchmarkTarget).filter(BenchmarkTarget.id == target_id).first():
            target_id = None
        config_id = result_data.get("config_id")
        if config_id is not None and not self.db.query(BenchmarkConfig).filter(BenchmarkConfig.id == config_id).first():
            config_id = None
        proxy_deployment_id = result_data.get("proxy_deployment_id")
        if proxy_deployment_id is not None and not self.db.query(ProxyDeployment).filter(ProxyDeployment.id == proxy_deployment_id).first():
            proxy_deployment_id = None

        run = BenchmarkRun(
            tool=tool,
            proxy=proxy,
            model=labels.get("model", config.get("model", "unknown")),
            base_url=labels.get("base_url", config.get("base_url", "unknown")),
            run_label=labels.get("run_label"),
            tags=result_data.get("tags"),
            agent_id=agent_id,
            target_id=target_id,
            config_id=config_id,
            proxy_deployment_id=proxy_deployment_id,
            status=BenchmarkRunStatus.COMPLETED,
            config_snapshot=config,
            result_json=result_data,

            # Denormalized metrics
            duration_seconds=result_data.get("duration_seconds"),
            total_requests=result_data.get("total_requests"),
            successful_requests=result_data.get("successful"),
            failed_requests=result_data.get("failed"),
            success_rate_pct=result_data.get("success_rate_pct"),
            latency_p50=latency.get("p50"),
            latency_p99=latency.get("p99"),
            overall_rps=throughput.get("overall_rps"),
            peak_rps=throughput.get("peak_rps"),
            tokens_per_sec=throughput.get("gen_tokens_per_sec"),
            total_output_tokens=result_data.get("total_output_tokens"),

            # Timestamps from the result
            started_at=_parse_iso(result_data.get("run_start")),
            completed_at=_parse_iso(result_data.get("run_end")),
        )

        self.db.add(run)
        self.db.flush()
        logger.info(
            "Ingested benchmark result: run_id=%d proxy=%s model=%s p50=%.3fs rps=%.1f",
            run.id, proxy, run.model,
            run.latency_p50 or 0, run.overall_rps or 0,
        )
        return run

    # ================================================================
    # Complete an existing run with aiperf result (agent flow)
    # ================================================================

    def complete_run_with_aiperf_result(self, run_id: int, raw: dict) -> BenchmarkRun:
        """Update an existing pending/running run with aiperf result data.

        Used when an agent completes a run triggered from Forge.
        Unlike ingest_aiperf_result(), this updates the existing run row
        rather than creating a new one.
        """
        run = self.db.query(BenchmarkRun).get(run_id)
        if not run:
            raise NotFoundError("benchmark_run", run_id)

        # Parse the raw aiperf JSON (same logic as ingest_aiperf_result)
        req_lat = raw.get("request_latency", {})
        req_thr = raw.get("request_throughput", {})
        out_thr = raw.get("output_token_throughput", {})
        req_count = raw.get("request_count", {})
        ttft = raw.get("time_to_first_token", {})
        itl = raw.get("inter_token_latency", {})
        osl = raw.get("output_sequence_length", {})
        isl = raw.get("input_sequence_length", {})

        # aiperf's request_count is the count of SUCCESSFUL records; error_request_count
        # is responses that failed at the record level (truncated streams, 4xx/5xx).
        # Both must be counted — using request_count alone with failed=0 falsely
        # reports 100% success and hides real upstream errors.
        err_count = raw.get("error_request_count", {})
        successful_reqs = int(req_count.get("avg", 0)) if isinstance(req_count, dict) else int(req_count or 0)
        failed_reqs = int(err_count.get("avg", 0)) if isinstance(err_count, dict) else int(err_count or 0)
        total_reqs = successful_reqs + failed_reqs
        success_rate = round(successful_reqs / total_reqs * 100, 2) if total_reqs else 100.0
        avg_osl = osl.get("avg", 0) if isinstance(osl, dict) else float(osl or 0)
        avg_isl = isl.get("avg", 0) if isinstance(isl, dict) else float(isl or 0)

        duration = raw.get("benchmark_duration", {})
        duration_sec = duration.get("avg", 0) if isinstance(duration, dict) else float(duration or 0)

        # Build result_json blob (same structure as ingest_aiperf_result)
        result_data = {
            "result_id": raw.get("benchmark_id", raw.get("schema_version", "unknown")),
            "run_start": raw.get("run_start", ""),
            "run_end": raw.get("run_end", ""),
            "duration_seconds": duration_sec,
            "total_requests": total_reqs,
            "successful": successful_reqs,
            "failed": failed_reqs,
            "success_rate_pct": success_rate,
            "total_input_tokens": int(avg_isl * successful_reqs),
            "total_output_tokens": int(avg_osl * successful_reqs),
            "avg_input_tokens": avg_isl,
            "avg_output_tokens": avg_osl,
            "latency": {
                "min": req_lat.get("min", 0) / 1000,
                "p25": req_lat.get("p25", 0) / 1000,
                "p50": req_lat.get("p50", 0) / 1000,
                "p75": req_lat.get("p75", 0) / 1000,
                "p90": req_lat.get("p90", 0) / 1000,
                "p95": req_lat.get("p95", 0) / 1000,
                "p99": req_lat.get("p99", 0) / 1000,
                "max": req_lat.get("max", 0) / 1000,
                "avg": req_lat.get("avg", 0) / 1000,
            },
            "throughput": {
                "overall_rps": req_thr.get("avg", 0),
                "peak_rps": req_thr.get("avg", 0),
                "gen_tokens_per_sec": out_thr.get("avg", 0),
            },
            "phases": {
                "profiling": {
                    "requests": total_reqs,
                    "successful": successful_reqs,
                    "failed": failed_reqs,
                    "duration_seconds": duration_sec,
                }
            },
            "aiperf_metrics": {
                "ttft": ttft,
                "itl": itl,
                "osl": osl,
                "isl": isl,
                "output_token_throughput_per_user": raw.get("output_token_throughput_per_user", {}),
                "time_to_second_token": raw.get("time_to_second_token", {}),
            },
        }

        # Update the existing run
        run.status = BenchmarkRunStatus.COMPLETED
        run.result_json = result_data
        run.duration_seconds = duration_sec
        run.total_requests = total_reqs
        run.successful_requests = successful_reqs
        run.failed_requests = failed_reqs
        run.success_rate_pct = success_rate
        run.latency_p50 = result_data["latency"]["p50"]
        run.latency_p99 = result_data["latency"]["p99"]
        run.overall_rps = result_data["throughput"]["overall_rps"]
        run.peak_rps = result_data["throughput"]["peak_rps"]
        run.tokens_per_sec = result_data["throughput"]["gen_tokens_per_sec"]
        run.total_output_tokens = int(avg_osl * successful_reqs)
        run.completed_at = _parse_iso(raw.get("run_end")) or datetime.now(UTC)

        self.db.flush()
        logger.info(
            "Completed run #%d with aiperf result: proxy=%s p50=%.3fs rps=%.1f",
            run.id, run.proxy, run.latency_p50 or 0, run.overall_rps or 0,
        )

        # Roll up the parent run-group aggregate once all children are terminal.
        if run.run_group_id:
            self.maybe_finalize_run_group(run.run_group_id)
        return run

    # ================================================================
    # Raw aiperf JSON Adapter
    # ================================================================

    def ingest_aiperf_result(
        self,
        raw: dict,
        *,
        proxy: str = "nodeport",
        model: str | None = None,
        url: str | None = None,
        agent_name: str | None = None,
        run_label: str | None = None,
        target_id: int | None = None,
        config_id: int | None = None,
        proxy_deployment_id: int | None = None,
        dataset_name: str | None = None,
    ) -> BenchmarkRun:
        """Transform raw aiperf profile_export_aiperf.json into Forge format and ingest.

        aiperf exports keys like `benchmark_id`, `request_latency`, `request_throughput`,
        `output_token_throughput`, etc.  This adapter maps them to Forge's schema so
        users can push results with a simple `curl -d @file.json`.

        aiperf 0.10.0 key mapping (with fallback to old keys for backward compat):
          config   → input_config  (raw.get("input_config") or raw.get("config"))
          run_start → start_time   (raw.get("start_time") or raw.get("run_start"))
          run_end   → end_time     (raw.get("end_time") or raw.get("run_end"))
        """
        # Extract aiperf metrics with safe defaults
        req_lat = raw.get("request_latency", {})
        req_thr = raw.get("request_throughput", {})
        out_thr = raw.get("output_token_throughput", {})
        req_count = raw.get("request_count", {})
        ttft = raw.get("time_to_first_token", {})
        itl = raw.get("inter_token_latency", {})
        osl = raw.get("output_sequence_length", {})
        isl = raw.get("input_sequence_length", {})

        # aiperf's request_count is the count of SUCCESSFUL records; error_request_count
        # is responses that failed at the record level (truncated streams, 4xx/5xx).
        # Both must be counted — using request_count alone with failed=0 falsely
        # reports 100% success and hides real upstream errors.
        err_count = raw.get("error_request_count", {})
        successful_reqs = int(req_count.get("avg", 0)) if isinstance(req_count, dict) else int(req_count or 0)
        failed_reqs = int(err_count.get("avg", 0)) if isinstance(err_count, dict) else int(err_count or 0)
        total_reqs = successful_reqs + failed_reqs
        success_rate = round(successful_reqs / total_reqs * 100, 2) if total_reqs else 100.0
        avg_osl = osl.get("avg", 0) if isinstance(osl, dict) else float(osl or 0)
        avg_isl = isl.get("avg", 0) if isinstance(isl, dict) else float(isl or 0)

        # FIX: aiperf 0.10.0 uses "input_config" (not "config"), "start_time"/"end_time"
        # (not "run_start"/"run_end"). Fall back to old keys for backward compat.
        aiperf_config = raw.get("input_config") or raw.get("config") or {}
        run_start = raw.get("start_time") or raw.get("run_start") or datetime.now(UTC).isoformat()
        run_end = raw.get("end_time") or raw.get("run_end") or datetime.now(UTC).isoformat()

        # Resolve model: prefer explicit param, then input_config models list, then
        # top-level model_name, then legacy config.model, finally "unknown".
        if not model:
            models_list = (aiperf_config.get("models") or {}).get("items") or []
            if models_list and isinstance(models_list[0], dict):
                model = models_list[0].get("name")
            if not model:
                model = aiperf_config.get("model") or raw.get("model_name") or "unknown"

        # Resolve base URL: prefer explicit param, then input_config endpoint urls list,
        # then legacy config.url.
        if not url:
            endpoint_urls = (aiperf_config.get("endpoint") or {}).get("urls") or []
            if endpoint_urls:
                url = endpoint_urls[0]
            if not url:
                url = aiperf_config.get("url") or "unknown"

        # Build duration from benchmark_duration if available
        duration = raw.get("benchmark_duration", {})
        duration_sec = duration.get("avg", 0) if isinstance(duration, dict) else float(duration or 0)

        # Additional detail blocks (aiperf 0.10.0+) — guard each with .get() so
        # a missing key simply omits the block rather than raising.
        http_timing: dict | None = None
        _timing_keys = (
            "http_req_blocked", "http_req_dns_lookup", "http_req_connecting",
            "http_req_waiting", "http_req_sending",
        )
        _timing_data = {k: raw.get(k) for k in _timing_keys if raw.get(k) is not None}
        if _timing_data:
            http_timing = _timing_data

        telemetry = raw.get("telemetry_data") or None

        usage: dict | None = None
        _usage_prompt = raw.get("usage_prompt_tokens")
        _usage_completion = raw.get("usage_completion_tokens")
        if _usage_prompt is not None or _usage_completion is not None:
            usage = {}
            if _usage_prompt is not None:
                usage["prompt_tokens"] = _usage_prompt
            if _usage_completion is not None:
                usage["completion_tokens"] = _usage_completion

        # Build the canonical result_data that ingest_result() expects
        result_data: dict = {
            "result_id": raw.get("benchmark_id", raw.get("schema_version", "unknown")),
            "result_version": raw.get("schema_version", "1.0"),
            "labels": {
                "proxy": proxy,
                "model": model,
                "base_url": url,
                "endpoint": (aiperf_config.get("endpoint") or {}).get("type") or aiperf_config.get("endpoint_type", "chat"),
                "run_label": run_label or f"aiperf-{proxy}",
            },
            "tags": {
                "source": "aiperf-raw-push",
                "aiperf_version": raw.get("aiperf_version"),
            },
            "run_start": run_start,
            "run_end": run_end,
            "duration_seconds": duration_sec,
            "duration_minutes": duration_sec / 60,
            "config": {
                "tool": "aiperf",
                "url": url,
                "model": model,
                **{k: v for k, v in aiperf_config.items() if k not in ("model", "url", "models", "endpoint")},
            },
            "total_requests": total_reqs,
            "successful": successful_reqs,
            "failed": failed_reqs,  # aiperf error_request_count (truncated/4xx/5xx)
            "success_rate_pct": success_rate,
            "total_input_tokens": int(avg_isl * successful_reqs),
            "total_output_tokens": int(avg_osl * successful_reqs),
            "avg_input_tokens": avg_isl,
            "avg_output_tokens": avg_osl,
            "latency": {
                "min": (req_lat.get("min", 0)) / 1000,
                "p25": (req_lat.get("p25", 0)) / 1000,
                "p50": (req_lat.get("p50", 0)) / 1000,
                "p75": (req_lat.get("p75", 0)) / 1000,
                "p90": (req_lat.get("p90", 0)) / 1000,
                "p95": (req_lat.get("p95", 0)) / 1000,
                "p99": (req_lat.get("p99", 0)) / 1000,
                "max": (req_lat.get("max", 0)) / 1000,
                "avg": (req_lat.get("avg", 0)) / 1000,
            },
            "throughput": {
                "overall_rps": req_thr.get("avg", 0),
                "peak_rps": req_thr.get("avg", 0),
                "gen_tokens_per_sec": out_thr.get("avg", 0),
            },
            "phases": {
                "profiling": {
                    "requests": total_reqs,
                    "successful": successful_reqs,
                    "failed": failed_reqs,
                    "duration_seconds": duration_sec,
                }
            },
            # Extra aiperf-specific metrics preserved in result_json
            "aiperf_metrics": {
                "ttft": ttft,
                "itl": itl,
                "osl": osl,
                "isl": isl,
                "output_token_throughput_per_user": raw.get("output_token_throughput_per_user", {}),
                "time_to_second_token": raw.get("time_to_second_token", {}),
            },
            "agent_name": agent_name,
        }

        # Linkage fields — stored in result_json for traceability; ingest_result()
        # also reads these to set FK columns on the BenchmarkRun row.
        if target_id is not None:
            result_data["target_id"] = target_id
        if config_id is not None:
            result_data["config_id"] = config_id
        if proxy_deployment_id is not None:
            result_data["proxy_deployment_id"] = proxy_deployment_id
        if dataset_name is not None:
            result_data["dataset_name"] = dataset_name

        # Optional detail blocks — omitted when data absent (guard with is not None)
        if http_timing is not None:
            result_data["http_timing"] = http_timing
        if telemetry is not None:
            result_data["telemetry"] = telemetry
        if usage is not None:
            result_data["usage"] = usage

        return self.ingest_result(result_data)

    # ================================================================
    # Config CRUD
    # ================================================================

    def list_configs(self, tool: str | None = None) -> list[BenchmarkConfig]:
        """List all benchmark configs, optionally filtered by tool."""
        query = self.db.query(BenchmarkConfig)
        if tool:
            query = query.filter(BenchmarkConfig.tool == tool)
        return query.order_by(BenchmarkConfig.name).all()

    def get_config(self, config_id: int) -> BenchmarkConfig:
        """Get a benchmark config by ID."""
        config = self.db.query(BenchmarkConfig).filter(BenchmarkConfig.id == config_id).first()
        if not config:
            raise NotFoundError("benchmark_config", config_id)
        return config

    def create_config(self, data: dict) -> BenchmarkConfig:
        """Create a new benchmark config."""
        existing = self.db.query(BenchmarkConfig).filter(
            BenchmarkConfig.name == data["name"]
        ).first()
        if existing:
            raise ConflictError("benchmark_config", f"Config with name '{data['name']}' already exists")

        config = BenchmarkConfig(**data)
        self.db.add(config)
        self.db.flush()
        return config

    def update_config(self, config_id: int, data: dict) -> BenchmarkConfig:
        """Update a benchmark config."""
        config = self.get_config(config_id)

        if data.get("name") and data["name"] != config.name:
            existing = self.db.query(BenchmarkConfig).filter(
                BenchmarkConfig.name == data["name"]
            ).first()
            if existing:
                raise ConflictError("benchmark_config", f"Config with name '{data['name']}' already exists")

        for key, value in data.items():
            if value is not None:
                setattr(config, key, value)

        config.updated_at = datetime.now(UTC)
        self.db.flush()
        return config

    def delete_config(self, config_id: int) -> None:
        """Delete a benchmark config."""
        config = self.get_config(config_id)
        self.db.delete(config)
        self.db.flush()

    # ================================================================
    # Run Lifecycle
    # ================================================================

    def list_runs(
        self,
        proxy: str | None = None,
        tool: str | None = None,
        model: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[BenchmarkRun], int]:
        """List benchmark runs with optional filters."""
        query = self.db.query(BenchmarkRun)
        if proxy:
            query = query.filter(BenchmarkRun.proxy == proxy)
        if tool:
            query = query.filter(BenchmarkRun.tool == tool)
        if model:
            query = query.filter(BenchmarkRun.model == model)
        if status:
            query = query.filter(BenchmarkRun.status == status)

        total = query.count()
        runs = query.order_by(desc(BenchmarkRun.created_at)).limit(limit).offset(offset).all()
        self._attach_baseline_context(runs)
        return runs, total

    def get_run(self, run_id: int, with_details: bool = False) -> BenchmarkRun:
        """Get a benchmark run by ID."""
        query = self.db.query(BenchmarkRun)
        if with_details:
            query = query.options(
                joinedload(BenchmarkRun.config),
                joinedload(BenchmarkRun.agent),
            )
        run = query.filter(BenchmarkRun.id == run_id).first()
        if not run:
            raise NotFoundError("benchmark_run", run_id)
        self._attach_baseline_context([run])
        return run

    def _attach_baseline_context(self, runs: list[BenchmarkRun]) -> None:
        """Set transient baseline_latency_p99 / baseline_overall_rps on each run.

        These are not DB columns — computed here so BenchmarkRunResponse can carry
        the reference values the frontend regression badge diffs against. Left unset
        (None) when a run's (target_id, scenario_key, config_id, proxy, variant_label)
        context has no baseline, or when the run itself IS the baseline.
        """
        target_ids = {r.target_id for r in runs if r.target_id is not None}
        baselines: dict[tuple, BenchmarkRun] = {}
        if target_ids:
            candidates = (
                self.db.query(BenchmarkRun)
                .filter(BenchmarkRun.is_baseline.is_(True), BenchmarkRun.target_id.in_(target_ids))
                .all()
            )
            for b in candidates:
                baselines[(b.target_id, b.scenario_key, b.config_id, b.proxy, b.variant_label)] = b

        for r in runs:
            baseline = baselines.get((r.target_id, r.scenario_key, r.config_id, r.proxy, r.variant_label))
            has_reference = baseline is not None and baseline.id != r.id
            r.baseline_latency_p99 = baseline.latency_p99 if has_reference else None
            r.baseline_overall_rps = baseline.overall_rps if has_reference else None

    def set_baseline(self, run_id: int) -> BenchmarkRun:
        """Mark a completed run as the baseline for its (target_id, scenario_key,
        config_id, proxy, variant_label) context, clearing any previous baseline in
        that same context.

        Concurrency: locks every run row sharing the context with SELECT ... FOR
        UPDATE (ordered by id, so concurrent calls acquire locks in a consistent
        order and can't deadlock) before clearing + setting is_baseline. Locking
        is scoped to the context's identity columns (target_id/scenario_key/
        config_id/proxy/variant_label — immutable) rather than only rows currently
        flagged is_baseline=True (mutable): under READ COMMITTED, a lock query
        filtered on is_baseline=True can miss a just-committed flip from a concurrent
        transaction (Postgres re-checks the WHERE clause on the specific locked
        row, it doesn't re-scan for newly-matching rows), which would let two
        concurrent set_baseline calls each believe they cleared "the" prior
        baseline and both end up flagged — the exact bug this locking closes.
        No-op under SQLite (tests): SQLite has no row-level locking and every
        writer is already serialized.
        """
        target_run = self.get_run(run_id)
        if target_run.status != BenchmarkRunStatus.COMPLETED:
            raise BadRequestError("Only completed runs can be marked as baseline", code="RUN_NOT_COMPLETED")

        if target_run.target_id is None:
            raise BadRequestError("Cannot set baseline on a run without a target_id", code="MISSING_TARGET_ID")

        context_runs = (
            self.db.query(BenchmarkRun)
            .filter(
                BenchmarkRun.target_id == target_run.target_id,
                BenchmarkRun.scenario_key == target_run.scenario_key,
                BenchmarkRun.config_id == target_run.config_id,
                BenchmarkRun.proxy == target_run.proxy,
                BenchmarkRun.variant_label == target_run.variant_label,
            )
            .order_by(BenchmarkRun.id)
            .with_for_update()
            .all()
        )
        run = next(r for r in context_runs if r.id == run_id)

        for other in context_runs:
            if other.id != run.id and other.is_baseline:
                other.is_baseline = False

        run.is_baseline = True
        self.db.flush()
        self._attach_baseline_context([run])
        return run

    def unset_baseline(self, run_id: int) -> BenchmarkRun:
        """Clear the baseline flag on a run."""
        run = self.get_run(run_id)
        run.is_baseline = False
        self.db.flush()
        self._attach_baseline_context([run])
        return run

    def get_trends(
        self,
        target_id: int | None = None,
        proxy: str | None = None,
        scenario_key: str | None = None,
        config_id: int | None = None,
        limit: int = 50,
    ) -> dict:
        """Time-ordered (oldest-first) completed-run metrics for a target/proxy/scenario/
        config context. The current baseline for that context is always included, even
        when it falls outside the `limit` window."""

        def _base_query():
            query = self.db.query(BenchmarkRun).filter(BenchmarkRun.status == BenchmarkRunStatus.COMPLETED)
            if target_id is not None:
                query = query.filter(BenchmarkRun.target_id == target_id)
            if proxy:
                query = query.filter(BenchmarkRun.proxy == proxy)
            if scenario_key:
                query = query.filter(BenchmarkRun.scenario_key == scenario_key)
            if config_id is not None:
                query = query.filter(BenchmarkRun.config_id == config_id)
            return query

        runs = _base_query().order_by(desc(BenchmarkRun.created_at)).limit(limit).all()
        runs = list(reversed(runs))

        # Baseline only makes sense for single-context series. Baseline identity is the 5-tuple
        # (target_id, config_id, scenario_key, proxy, variant_label) — same as set_baseline locking.
        # If plotted series interleaves multiple contexts, do not return a cross-context baseline.
        contexts = {(r.target_id, r.config_id, r.scenario_key, r.proxy, r.variant_label) for r in runs}
        baseline_run = None
        if len(contexts) == 1 and runs:
            # Single-context series — pick baseline scoped to exactly this context
            context_tuple = contexts.pop()
            target_id_ctx, config_id_ctx, scenario_key_ctx, proxy_ctx, variant_label_ctx = context_tuple
            baseline_run = (
                self.db.query(BenchmarkRun)
                .filter(
                    BenchmarkRun.status == BenchmarkRunStatus.COMPLETED,
                    BenchmarkRun.is_baseline.is_(True),
                    BenchmarkRun.target_id == target_id_ctx,
                    BenchmarkRun.config_id == config_id_ctx,
                    BenchmarkRun.scenario_key == scenario_key_ctx,
                    BenchmarkRun.proxy == proxy_ctx,
                    BenchmarkRun.variant_label == variant_label_ctx,
                )
                .order_by(desc(BenchmarkRun.created_at))
                .first()
            )
            if baseline_run and not any(r.id == baseline_run.id for r in runs):
                runs.append(baseline_run)
                runs.sort(key=lambda r: r.created_at)

        return {
            "points": runs,
            "baseline_run_id": baseline_run.id if baseline_run else None,
        }

    def create_run(self, data: dict) -> BenchmarkRun:
        """Create a new benchmark run (triggered from UI)."""
        if data.get("config_id"):
            config = self.get_config(data["config_id"])
            if not data.get("config_snapshot"):
                data["config_snapshot"] = config.config_json

        run = BenchmarkRun(**data)
        self.db.add(run)
        self.db.flush()
        return run

    def delete_run(self, run_id: int) -> None:
        """Delete a benchmark run."""
        run = self.get_run(run_id)
        if run.status == BenchmarkRunStatus.RUNNING:
            raise BadRequestError("Cannot delete a running benchmark — cancel it first", code="RUN_ACTIVE")
        self.db.delete(run)
        self.db.flush()

    def _cancel_single_run(self, run: BenchmarkRun, now: datetime) -> None:
        """Mark one run CANCELLED in place. No status-transition guard — callers
        that sweep a group must cancel non-terminal children unconditionally."""
        run.status = BenchmarkRunStatus.CANCELLED
        run.completed_at = now
        run.updated_at = now
        if run.started_at and run.duration_seconds is None:
            run.duration_seconds = (now - run.started_at).total_seconds()

    def cancel_run(self, run_id: int) -> BenchmarkRun:
        """Cancel a benchmark run.

        Standalone run (no run_group_id): cancel just this run.
        Group child: cancel-the-whole-group — every non-terminal sibling (PENDING
        and the RUNNING one) is cancelled and the group is finalized as CANCELLED.
        This prevents a single cancel from wedging the group in RUNNING forever
        with the sweep stalled. See cancel_run_group() for the running-child agent
        info the route uses to stop the live aiperf.
        """
        run = self.get_run(run_id)
        if run.status in BenchmarkRunStatus.terminal_states():
            raise BadRequestError(
                f"Cannot cancel run with terminal status '{run.status}'",
                code="INVALID_STATUS_TRANSITION",
            )
        now = datetime.now(UTC)
        self._cancel_single_run(run, now)
        if run.run_group_id:
            self._cancel_group_siblings(run.run_group_id, now, exclude_id=run.id)
            self._finalize_group_cancelled(run.run_group_id, now)
        self.db.flush()
        return run

    def _cancel_group_siblings(self, group_id: int, now: datetime, *, exclude_id: int) -> None:
        """Cancel every non-terminal sibling of a group (all PENDING + the RUNNING one)."""
        terminal = BenchmarkRunStatus.terminal_states()
        siblings = (
            self.db.query(BenchmarkRun)
            .filter(
                BenchmarkRun.run_group_id == group_id,
                BenchmarkRun.id != exclude_id,
                BenchmarkRun.status.notin_(list(terminal)),
            )
            .all()
        )
        for sib in siblings:
            self._cancel_single_run(sib, now)

    def _finalize_group_cancelled(self, group_id: int, now: datetime) -> None:
        """Finalize a group as CANCELLED after a cancel sweep, recomputing counts."""
        group = self.db.query(BenchmarkRunGroup).get(group_id)
        if not group:
            return
        children = (
            self.db.query(BenchmarkRun)
            .filter(BenchmarkRun.run_group_id == group_id)
            .all()
        )
        completed = [r for r in children if r.status == BenchmarkRunStatus.COMPLETED]
        failed = [r for r in children if r.status == BenchmarkRunStatus.FAILED]
        group.completed_runs = len(completed)
        group.failed_runs = len(failed)
        # A cancel sweep ends the group even if some children completed earlier:
        # the user asked to stop the whole sweep.
        group.status = BenchmarkRunStatus.CANCELLED
        group.completed_at = now
        group.aggregate_json = _aggregate_children(completed, children)
        if completed:
            self._roll_up_aggregate_metrics(group, completed)
        self.db.flush()

    def find_running_group_child(self, group_id: int) -> BenchmarkRun | None:
        """The currently-RUNNING child of a group, if any (used to stop live aiperf)."""
        return (
            self.db.query(BenchmarkRun)
            .filter(
                BenchmarkRun.run_group_id == group_id,
                BenchmarkRun.status == BenchmarkRunStatus.RUNNING,
            )
            .first()
        )

    # ================================================================
    # Run-Group Orchestration (Phase 6) — scenario → group + child runs
    # ================================================================

    def create_run_group_from_scenario(
        self,
        *,
        scenario_key: str,
        base_url: str,
        endpoint: str,
        model: str,
        target_id: int | None = None,
        proxy_id: int | None = None,
        proxy_type: str | None = None,
        agent_id: int | None = None,
        run_label: str | None = None,
        tags: dict | None = None,
        overrides: dict | None = None,
    ) -> tuple[BenchmarkRunGroup, list[BenchmarkRun]]:
        """Expand a scenario into a parent run-group + N child runs (one per variant).

        Returns the persisted group and its child runs. Child runs are created with
        status=PENDING; the caller dispatches each child's ``config_snapshot`` to the
        agent. Raises BadRequestError for an unknown scenario key.
        """
        try:
            preset = get_scenario(scenario_key)
        except KeyError as exc:
            raise BadRequestError(
                f"Unknown scenario '{scenario_key}'", code="UNKNOWN_SCENARIO"
            ) from exc

        child_configs = expand_scenario(
            scenario_key,
            base_url=base_url,
            endpoint=endpoint,
            model=model,
            overrides=overrides,
        )

        group = BenchmarkRunGroup(
            scenario_key=scenario_key,
            scenario_name=preset.name,
            run_label=run_label or f"{scenario_key}-{proxy_type or 'run'}",
            target_id=target_id,
            proxy_id=proxy_id,
            agent_id=agent_id,
            proxy=proxy_type,
            model=model,
            base_url=base_url,
            tags=tags,
            status=BenchmarkRunStatus.PENDING,
            total_runs=len(child_configs),
            completed_runs=0,
            failed_runs=0,
        )
        self.db.add(group)
        self.db.flush()

        runs: list[BenchmarkRun] = []
        for config in child_configs:
            variant_label = config.get("_variant_label")
            run = BenchmarkRun(
                tool=config.get("tool", "aiperf"),
                proxy=proxy_type or "nodeport",
                model=model,
                base_url=base_url,
                run_label=variant_label,
                tags=tags,
                agent_id=agent_id,
                target_id=target_id,
                proxy_deployment_id=proxy_id,
                run_group_id=group.id,
                scenario_key=scenario_key,
                variant_label=variant_label,
                status=BenchmarkRunStatus.PENDING,
                config_snapshot=config,
            )
            self.db.add(run)
            runs.append(run)

        self.db.flush()
        logger.info(
            "Created run-group #%d scenario=%s with %d child runs",
            group.id, scenario_key, len(runs),
        )
        return group, runs

    def get_run_group(self, group_id: int) -> BenchmarkRunGroup:
        """Get a run-group by ID with its child runs eagerly loaded."""
        group = (
            self.db.query(BenchmarkRunGroup)
            .options(joinedload(BenchmarkRunGroup.runs))
            .filter(BenchmarkRunGroup.id == group_id)
            .first()
        )
        if not group:
            raise NotFoundError("benchmark_run_group", group_id)
        return group

    def get_next_pending_group_run(self, group_id: int) -> BenchmarkRun | None:
        """Next not-yet-dispatched (pending) child of a run-group, lowest id first.

        Powers the gated dispatcher: child N+1 is sent only after N finishes, so
        benchmark runs execute strictly sequentially (never concurrently) and the
        group status reflects exactly one running run at a time.
        """
        return (
            self.db.query(BenchmarkRun)
            .filter(
                BenchmarkRun.run_group_id == group_id,
                BenchmarkRun.status == BenchmarkRunStatus.PENDING,
            )
            .order_by(BenchmarkRun.id)
            .first()
        )

    def claim_pending_run(self, run_id: int) -> bool:
        """Atomically transition a run PENDING→RUNNING. Returns True iff this call
        won the claim (rowcount == 1).

        Closes the gated-dispatch double-dispatch race: two near-simultaneous
        terminal WS messages can both select the same lowest-id PENDING child via
        get_next_pending_group_run. Guarding the transition with a conditional
        UPDATE (WHERE status='pending') means exactly one caller flips it to RUNNING
        and dispatches; the loser sees rowcount 0 and skips, so aiperf is invoked
        once. Caller commits the surrounding transaction.
        """
        now = datetime.now(UTC)
        result = (
            self.db.query(BenchmarkRun)
            .filter(
                BenchmarkRun.id == run_id,
                BenchmarkRun.status == BenchmarkRunStatus.PENDING,
            )
            .update(
                {
                    BenchmarkRun.status: BenchmarkRunStatus.RUNNING,
                    BenchmarkRun.started_at: now,
                    BenchmarkRun.updated_at: now,
                },
                synchronize_session=False,
            )
        )
        return result == 1

    def release_claimed_run(self, run_id: int) -> None:
        """Revert a claimed run RUNNING→PENDING (e.g. WS send failed after claim).

        Uses a conditional bulk UPDATE for the same reason claim_pending_run does:
        the claim was a synchronize_session=False UPDATE, so the in-session ORM
        object is stale and an attribute write would be a no-op. The WHERE guard
        keeps this safe — it only reverts a row still in RUNNING.
        """
        self.db.query(BenchmarkRun).filter(
            BenchmarkRun.id == run_id,
            BenchmarkRun.status == BenchmarkRunStatus.RUNNING,
        ).update(
            {
                BenchmarkRun.status: BenchmarkRunStatus.PENDING,
                BenchmarkRun.started_at: None,
                BenchmarkRun.updated_at: datetime.now(UTC),
            },
            synchronize_session=False,
        )

    def maybe_finalize_run_group(self, group_id: int) -> BenchmarkRunGroup | None:
        """Recompute group counts; roll up aggregate metrics when all children terminal.

        Idempotent. Updates completed/failed counts on every call, and only sets the
        group to a terminal status + writes the aggregate once no child is still
        pending/running.
        """
        group = self.db.query(BenchmarkRunGroup).get(group_id)
        if not group:
            return None

        children = (
            self.db.query(BenchmarkRun)
            .filter(BenchmarkRun.run_group_id == group_id)
            .all()
        )
        terminal = BenchmarkRunStatus.terminal_states()
        completed = [r for r in children if r.status == BenchmarkRunStatus.COMPLETED]
        failed = [r for r in children if r.status == BenchmarkRunStatus.FAILED]
        cancelled = [r for r in children if r.status == BenchmarkRunStatus.CANCELLED]

        group.completed_runs = len(completed)
        group.failed_runs = len(failed)

        all_terminal = all(r.status in terminal for r in children) and len(children) > 0
        if not all_terminal:
            if group.status == BenchmarkRunStatus.PENDING and any(
                r.status == BenchmarkRunStatus.RUNNING for r in children
            ):
                group.status = BenchmarkRunStatus.RUNNING
            self.db.flush()
            return group

        # All children terminal — finalize. CANCELLED is terminal too: count it
        # explicitly so completed + failed + cancelled == total. Status rule:
        # any COMPLETED → COMPLETED; else all-cancelled → CANCELLED; else FAILED.
        if completed:
            group.status = BenchmarkRunStatus.COMPLETED
        elif cancelled and not failed:
            group.status = BenchmarkRunStatus.CANCELLED
        else:
            group.status = BenchmarkRunStatus.FAILED
        group.completed_at = datetime.now(UTC)
        group.aggregate_json = _aggregate_children(completed, children)

        if completed:
            self._roll_up_aggregate_metrics(group, completed)

        self.db.flush()
        logger.info(
            "Finalized run-group #%d status=%s (%d completed, %d failed, %d cancelled / %d total)",
            group.id, group.status, group.completed_runs, group.failed_runs,
            len(cancelled), group.total_runs,
        )
        return group

    @staticmethod
    def _roll_up_aggregate_metrics(group: BenchmarkRunGroup, completed: list[BenchmarkRun]) -> None:
        """Write rolled-up aggregate metrics onto the group from its completed children."""
        p50s = [r.latency_p50 for r in completed if r.latency_p50 is not None]
        p99s = [r.latency_p99 for r in completed if r.latency_p99 is not None]
        rpss = [r.peak_rps for r in completed if r.peak_rps is not None]
        tok = [r.total_output_tokens for r in completed if r.total_output_tokens is not None]
        group.avg_latency_p50 = sum(p50s) / len(p50s) if p50s else None
        group.avg_latency_p99 = sum(p99s) / len(p99s) if p99s else None
        group.peak_rps = max(rpss) if rpss else None
        group.total_output_tokens = sum(tok) if tok else None

    # ================================================================
    # Agent Management
    # ================================================================

    def register_agent(self, data: dict) -> BenchmarkAgent:
        """Register a test client agent (called via curl or script)."""
        # Upsert — if agent with same name exists, update it
        existing = self.db.query(BenchmarkAgent).filter(
            BenchmarkAgent.name == data["name"]
        ).first()

        if existing:
            existing.hostname = data.get("hostname", existing.hostname)
            existing.ip_address = data.get("ip_address", existing.ip_address)
            existing.tags = data.get("tags", existing.tags)
            existing.capabilities = data.get("capabilities", existing.capabilities)
            existing.status = BenchmarkAgentStatus.CONNECTED
            existing.last_heartbeat = datetime.now(UTC)
            existing.updated_at = datetime.now(UTC)
            self.db.flush()
            return existing

        agent = BenchmarkAgent(
            **data,
            status=BenchmarkAgentStatus.CONNECTED,
            last_heartbeat=datetime.now(UTC),
        )
        self.db.add(agent)
        self.db.flush()
        return agent

    def list_agents(self) -> list[BenchmarkAgent]:
        """List all registered agents."""
        return self.db.query(BenchmarkAgent).order_by(BenchmarkAgent.name).all()

    def get_agent(self, agent_id: int) -> BenchmarkAgent:
        """Get an agent by ID."""
        agent = self.db.query(BenchmarkAgent).filter(BenchmarkAgent.id == agent_id).first()
        if not agent:
            raise NotFoundError("benchmark_agent", agent_id)
        return agent

    def update_agent_status(self, agent_id: int, status: str) -> BenchmarkAgent:
        """Update agent connection status."""
        agent = self.get_agent(agent_id)
        agent.status = status
        agent.last_heartbeat = datetime.now(UTC)
        agent.updated_at = datetime.now(UTC)
        self.db.flush()
        return agent

    def update_agent_heartbeat(self, agent_id: int) -> BenchmarkAgent:
        """Update agent heartbeat timestamp."""
        agent = self.get_agent(agent_id)
        agent.last_heartbeat = datetime.now(UTC)
        self.db.flush()
        return agent

    def delete_agent(self, agent_id: int) -> None:
        """Deregister an agent."""
        agent = self.get_agent(agent_id)
        self.db.delete(agent)
        self.db.flush()

    # ================================================================
    # Comparison — proxy vs proxy
    # ================================================================

    def compare_runs(self, run_ids: list[int]) -> dict:
        """Compare multiple benchmark runs side-by-side (proxy comparison)."""
        runs = (
            self.db.query(BenchmarkRun)
            .filter(BenchmarkRun.id.in_(run_ids))
            .all()
        )

        if len(runs) != len(run_ids):
            found_ids = {r.id for r in runs}
            missing = [rid for rid in run_ids if rid not in found_ids]
            raise NotFoundError("benchmark_runs", missing)

        # Build per-run metrics
        run_metrics = []
        for run in runs:
            # Extract aiperf-specific avg values from result_json if available
            aiperf = {}
            rj = run.result_json or {}
            am = rj.get("aiperf_metrics") if isinstance(rj, dict) else None
            if am and isinstance(am, dict):
                for key, out_key in [
                    ("ttft", "ttft_avg"),
                    ("itl", "itl_avg"),
                    ("time_to_second_token", "tst_avg"),
                    ("osl", "osl_avg"),
                    ("isl", "isl_avg"),
                    ("output_token_throughput_per_user", "per_user_throughput_avg"),
                ]:
                    metric_data = am.get(key)
                    if isinstance(metric_data, dict) and metric_data.get("avg") is not None:
                        aiperf[out_key] = metric_data["avg"]

            run_metrics.append({
                "run_id": run.id,
                "proxy": run.proxy,
                "model": run.model,
                "tool": run.tool,
                "run_label": run.run_label,
                "config_id": run.config_id,
                "scenario_key": run.scenario_key,
                "variant_label": run.variant_label,
                "status": run.status,
                "total_requests": run.total_requests,
                "success_rate_pct": run.success_rate_pct,
                "latency_p50": run.latency_p50,
                "latency_p99": run.latency_p99,
                "overall_rps": run.overall_rps,
                "peak_rps": run.peak_rps,
                "tokens_per_sec": run.tokens_per_sec,
                "duration_seconds": run.duration_seconds,
                **aiperf,
            })

        # Determine winners per metric
        winners = {}
        completed_metrics = [m for m in run_metrics if m["status"] == "completed"]
        if completed_metrics:
            # Lower is better
            for metric in ["latency_p50", "latency_p99", "ttft_avg", "itl_avg", "tst_avg"]:
                vals = [(m["run_id"], m.get(metric)) for m in completed_metrics if m.get(metric) is not None]
                if vals:
                    winners[metric] = min(vals, key=lambda x: x[1])[0]
            # Higher is better
            for metric in ["overall_rps", "peak_rps", "tokens_per_sec", "success_rate_pct", "per_user_throughput_avg"]:
                vals = [(m["run_id"], m.get(metric)) for m in completed_metrics if m.get(metric) is not None]
                if vals:
                    winners[metric] = max(vals, key=lambda x: x[1])[0]

        # Mismatch warning — comparing runs built from different configs or scenarios can
        # silently mislead (e.g., "which proxy is faster" when the workloads differ too).
        # Proxies and variants are deliberately-varied comparison dimensions on the Compare tab.
        config_ids = {m["config_id"] for m in run_metrics}
        scenario_keys = {m["scenario_key"] for m in run_metrics}
        context_mismatch = (
            len(config_ids) > 1
            or len(scenario_keys) > 1
        )

        return {
            "runs": run_metrics,
            "winners": winners,
            "context_mismatch": context_mismatch,
        }

    # ================================================================
    # Summary & Stats
    # ================================================================

    def get_summary(self) -> dict:
        """Get dashboard summary of benchmark activity."""
        total_runs = self.db.query(BenchmarkRun).count()
        completed_runs = self.db.query(BenchmarkRun).filter(
            BenchmarkRun.status == BenchmarkRunStatus.COMPLETED
        ).count()
        failed_runs = self.db.query(BenchmarkRun).filter(
            BenchmarkRun.status == BenchmarkRunStatus.FAILED
        ).count()
        running_count = self.db.query(BenchmarkRun).filter(
            BenchmarkRun.status == BenchmarkRunStatus.RUNNING
        ).count()

        # Average metrics across completed runs
        avg_p50 = (
            self.db.query(func.avg(BenchmarkRun.latency_p50))
            .filter(
                BenchmarkRun.status == BenchmarkRunStatus.COMPLETED,
                BenchmarkRun.latency_p50.isnot(None),
            )
            .scalar()
        )
        avg_rps = (
            self.db.query(func.avg(BenchmarkRun.overall_rps))
            .filter(
                BenchmarkRun.status == BenchmarkRunStatus.COMPLETED,
                BenchmarkRun.overall_rps.isnot(None),
            )
            .scalar()
        )
        avg_success = (
            self.db.query(func.avg(BenchmarkRun.success_rate_pct))
            .filter(
                BenchmarkRun.status == BenchmarkRunStatus.COMPLETED,
                BenchmarkRun.success_rate_pct.isnot(None),
            )
            .scalar()
        )

        # Last run
        last_run = (
            self.db.query(BenchmarkRun)
            .order_by(BenchmarkRun.created_at.desc())
            .first()
        )

        # Runs in last 7 days
        seven_days_ago = datetime.now(UTC) - timedelta(days=7)
        runs_last_7d = (
            self.db.query(BenchmarkRun)
            .filter(BenchmarkRun.created_at >= seven_days_ago)
            .count()
        )

        # Runs by proxy
        proxy_stats = (
            self.db.query(
                BenchmarkRun.proxy,
                func.count(BenchmarkRun.id).label("count"),
                func.avg(BenchmarkRun.latency_p50).label("avg_p50"),
                func.avg(BenchmarkRun.overall_rps).label("avg_rps"),
            )
            .filter(BenchmarkRun.status == BenchmarkRunStatus.COMPLETED)
            .group_by(BenchmarkRun.proxy)
            .order_by(desc("count"))
            .all()
        )
        runs_by_proxy = [
            {
                "proxy": row.proxy,
                "count": row.count,
                "avg_p50": round(row.avg_p50, 4) if row.avg_p50 else None,
                "avg_rps": round(row.avg_rps, 2) if row.avg_rps else None,
            }
            for row in proxy_stats
        ]

        # Runs by tool
        tool_stats = (
            self.db.query(
                BenchmarkRun.tool,
                func.count(BenchmarkRun.id).label("count"),
            )
            .group_by(BenchmarkRun.tool)
            .all()
        )
        runs_by_tool = [{"tool": row.tool, "count": row.count} for row in tool_stats]

        return {
            "total_runs": total_runs,
            "completed_runs": completed_runs,
            "failed_runs": failed_runs,
            "running_count": running_count,
            "avg_latency_p50": round(avg_p50, 4) if avg_p50 else None,
            "avg_rps": round(avg_rps, 2) if avg_rps else None,
            "avg_success_rate": round(avg_success, 2) if avg_success else None,
            "last_run_at": last_run.created_at if last_run else None,
            "runs_last_7d": runs_last_7d,
            "runs_by_proxy": runs_by_proxy,
            "runs_by_tool": runs_by_tool,
        }


# ================================================================
# Helpers
# ================================================================

def _aggregate_children(
    completed: list[BenchmarkRun],
    all_children: list[BenchmarkRun] | None = None,
) -> dict:
    """Build a per-variant aggregate rollup from completed child runs.

    Only completed runs carry metrics, so the per-variant block lists those. But
    cancelled/failed children must not vanish from the accounting: when the full
    child list is supplied, emit a ``status_counts`` breakdown so a finalized group
    where some children were cancelled still reconciles (completed + failed +
    cancelled + pending/running == total).
    """
    agg: dict = {
        "variants": [
            {
                "run_id": r.id,
                "variant_label": r.variant_label,
                "concurrency": (r.config_snapshot or {}).get("concurrency"),
                "latency_p50": r.latency_p50,
                "latency_p99": r.latency_p99,
                "overall_rps": r.overall_rps,
                "peak_rps": r.peak_rps,
                "tokens_per_sec": r.tokens_per_sec,
                "total_output_tokens": r.total_output_tokens,
            }
            for r in completed
        ],
    }
    if all_children is not None:
        counts: dict[str, int] = {}
        for r in all_children:
            key = str(r.status)
            counts[key] = counts.get(key, 0) + 1
        agg["status_counts"] = counts
    return agg


def _parse_iso(iso_str: str | None) -> datetime | None:
    """Parse an ISO 8601 datetime string, returning None on failure."""
    if not iso_str:
        return None
    try:
        return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
