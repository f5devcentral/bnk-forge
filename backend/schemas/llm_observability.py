"""
Pydantic v2 response models for AI-gateway observability (Tier 1).

Every model carries the degradation envelope so a panel can render an
"unavailable" state without the UI crashing:
  - ``available`` / ``reason`` — false + reason when Loki can't be reached.
  - ``endpoint`` — the in-cluster Loki service the query was proxied to.
  - ``updated_at`` — ISO-8601 timestamp of the response.
  - ``errors`` — per-query error map for partial failures (never raised).

Data fields default to empty/zero so an unavailable envelope validates
without supplying them. ``reason`` is declared here (not in the design's
contract table) because ``response_model`` silently drops undeclared fields.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class _Envelope(BaseModel):
    """Shared degradation fields on every observability response."""

    available: bool
    endpoint: str | None = None
    updated_at: str
    reason: str | None = None
    errors: dict[str, str] = Field(default_factory=dict)


class LlmStatsResponse(_Envelope):
    total_requests: int = 0
    success_rate: float = 0.0
    avg_latency_ms: float = 0.0
    total_tokens: int = 0
    total_cost: float = 0.0
    models: int = 0


class HistogramPoint(BaseModel):
    ts: str
    value: float


class HistogramSeries(BaseModel):
    name: str
    points: list[HistogramPoint] = Field(default_factory=list)


class LlmHistogramResponse(_Envelope):
    metric: str = ""
    step_s: int = 0
    series: list[HistogramSeries] = Field(default_factory=list)


class RankingTrend(BaseModel):
    requests: float | None = None
    tokens: float | None = None
    cost: float | None = None


class RankingRow(BaseModel):
    model: str
    provider: str
    requests: int = 0
    success_rate: float = 0.0
    tokens: int = 0
    cost: float = 0.0
    avg_latency_ms: float = 0.0
    trend: RankingTrend = Field(default_factory=RankingTrend)


class LlmRankingsResponse(_Envelope):
    rows: list[RankingRow] = Field(default_factory=list)


class LlmProviderUsageResponse(_Envelope):
    """Same envelope as histogram; series keyed by inferred provider."""

    metric: str = ""
    step_s: int = 0
    series: list[HistogramSeries] = Field(default_factory=list)


class LogRow(BaseModel):
    ts: str
    type: str
    message: str
    model: str
    latency_ms: float
    prompt_tk: int
    comp_tk: int
    total_tk: int
    cost: float
    status: str
    req_body: str
    resp_body: str
    cluster_id: int | None = None
    cluster_name: str | None = None


class LlmLogsResponse(_Envelope):
    rows: list[LogRow] = Field(default_factory=list)
    next_end: str | None = None


class LlmFilterDataResponse(_Envelope):
    models: list[str] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=list)
