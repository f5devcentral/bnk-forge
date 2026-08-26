"""WAF Dashboard models — ingest cursor for ClickHouse ingestion tracking."""
from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.sql import func

from database import Base


class WafIngestionCursor(Base):
    """Tracks the last ingested line per (cluster, log_file) for idempotent ingest."""

    __tablename__ = "waf_ingestion_cursors"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cluster_id = Column(Integer, nullable=False, index=True)
    log_file = Column(String(512), nullable=False)
    last_line_num = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        # Composite unique: one cursor per (cluster, file)
        __import__("sqlalchemy").UniqueConstraint("cluster_id", "log_file", name="uq_waf_cursor_cluster_file"),
    )
