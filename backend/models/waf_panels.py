import os

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from database import Base

_DB = os.getenv("CLICKHOUSE_DB", "bnkforge")

# Predefined query templates — maps a short key to a ClickHouse SQL fragment.
# All templates are parameterised by cluster_id and a time range; no free-form SQL.
# The {db} placeholder is substituted at query-execution time with the configured DB name.
def _t(sql: str) -> str:
    return sql.replace("{db}", _DB)

PANEL_QUERY_TEMPLATES = {
    "events_by_outcome":       _t("SELECT outcome AS label, count() AS value FROM {db}.waf_events WHERE cluster_id={cid} AND ts >= now() - INTERVAL {h} HOUR GROUP BY label ORDER BY value DESC"),
    "events_by_attack_type":   _t("SELECT attack_type AS label, count() AS value FROM {db}.waf_events WHERE cluster_id={cid} AND ts >= now() - INTERVAL {h} HOUR GROUP BY label ORDER BY value DESC LIMIT 10"),
    "events_by_ip":            _t("SELECT ip_client AS label, countIf(outcome='REJECTED') AS value FROM {db}.waf_events WHERE cluster_id={cid} AND ts >= now() - INTERVAL {h} HOUR GROUP BY label ORDER BY value DESC LIMIT 10"),
    "events_by_uri":           _t("SELECT uri AS label, count() AS value FROM {db}.waf_events WHERE cluster_id={cid} AND ts >= now() - INTERVAL {h} HOUR AND outcome='REJECTED' GROUP BY label ORDER BY value DESC LIMIT 10"),
    "events_by_policy":        _t("SELECT policy_name AS label, count() AS value FROM {db}.waf_events WHERE cluster_id={cid} AND ts >= now() - INTERVAL {h} HOUR GROUP BY label ORDER BY value DESC"),
    "events_by_vs":            _t("SELECT vs_name AS label, count() AS value FROM {db}.waf_events WHERE cluster_id={cid} AND ts >= now() - INTERVAL {h} HOUR GROUP BY label ORDER BY value DESC"),
    "blocked_rate_over_time":  _t("SELECT toStartOfInterval(ts, INTERVAL {bucket} HOUR) AS ts_bucket, round(countIf(outcome='REJECTED') / count() * 100, 1) AS value FROM {db}.waf_events WHERE cluster_id={cid} AND ts >= now() - INTERVAL {h} HOUR GROUP BY ts_bucket ORDER BY ts_bucket"),
    "trend_by_outcome":        _t("SELECT toStartOfInterval(ts, INTERVAL {bucket} HOUR) AS ts_bucket, countIf(outcome='REJECTED') AS REJECTED, countIf(outcome='PASSED') AS PASSED, countIf(outcome='ALERTED') AS ALERTED FROM {db}.waf_events WHERE cluster_id={cid} AND ts >= now() - INTERVAL {h} HOUR GROUP BY ts_bucket ORDER BY ts_bucket"),
    "events_by_ingest_source": _t("SELECT ingest_source AS label, count() AS value FROM {db}.waf_events WHERE cluster_id={cid} AND ts >= now() - INTERVAL {h} HOUR GROUP BY label ORDER BY value DESC"),
}

VALID_CHART_TYPES = {"bar", "horizontal_bar", "area", "line", "pie", "kpi", "table"}
VALID_TIME_RANGES = {"1h", "24h", "7d", "30d"}
VALID_WIDTHS = {"full", "half"}


class WafPanel(Base):
    __tablename__ = "waf_panels"
    # Must match migration v2_157_add_waf_panels (+ tab_id from v2_158) EXACTLY so
    # create_all and the migration chain build the same schema (schema-parity gate).
    __table_args__ = (
        Index("idx_waf_panels_cluster", "cluster_id", "panel_order"),
        {"extend_existing": True},
    )

    id          = Column(Integer, primary_key=True, autoincrement=True)
    cluster_id  = Column(Integer, nullable=False, index=True)  # no FK to keep schema portable
    created_by  = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    tab_id      = Column(Integer, nullable=True)   # null = legacy/default "Custom" tab
    title       = Column(String(255), nullable=False)
    chart_type  = Column(String(50),  nullable=False, default="bar")
    query_template = Column(String(100), nullable=False)
    groupby_field  = Column(String(100), nullable=True)
    time_range  = Column(String(10),  nullable=False, default="7d")
    panel_order = Column(Integer,     nullable=False, default=0)
    width       = Column(String(10),  nullable=False, default="full")
    extra_config = Column(JSON,       nullable=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())
    updated_at  = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class WafDashboardTab(Base):
    """A user-defined dashboard tab that groups a set of custom panels."""
    __tablename__ = "waf_dashboard_tabs"
    # Must match migration v2_158_add_waf_dashboard_tabs EXACTLY (schema-parity gate).
    __table_args__ = (
        Index("idx_waf_dashboard_tabs_cluster", "cluster_id", "tab_order"),
        {"extend_existing": True},
    )

    id          = Column(Integer, primary_key=True, autoincrement=True)
    cluster_id  = Column(Integer, nullable=False, index=True)
    name        = Column(String(100), nullable=False)
    tab_order   = Column(Integer,     nullable=False, default=0)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())
    updated_at  = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class WafIngestionCursor(Base):
    """Tracks the last-ingested line per (cluster, log file) for ClickHouse ingest.

    Mirrors migration v2_156_add_waf_ingestion_cursors EXACTLY so create_all and
    the migration chain build the same schema (schema-parity gate). The table is
    not yet used by app code, but the parity gate requires every chain table to
    have an ORM model.
    """
    __tablename__ = "waf_ingestion_cursors"
    __table_args__ = (
        UniqueConstraint("cluster_id", "log_file", name="uq_waf_cursor_cluster_file"),
        Index("ix_waf_ingestion_cursors_cluster_id", "cluster_id"),
        {"extend_existing": True},
    )

    id            = Column(Integer, primary_key=True, autoincrement=True)
    cluster_id    = Column(Integer, nullable=False)
    log_file      = Column(String(512), nullable=False)
    last_line_num = Column(Integer, nullable=False, server_default="0")
    updated_at    = Column(DateTime(timezone=True), server_default=func.now())
