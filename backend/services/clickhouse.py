"""
ClickHouse client service for WAF dashboard analytics.

Connects to ClickHouse via its HTTP interface (port 8123) using clickhouse-connect.
Gracefully degrades when CLICKHOUSE_URL is not configured — callers receive
None from get_client() and should return a suitable "not configured" response.

Schema (created on first connect if missing):
  waf_events — MergeTree, partitioned by month, ordered by (cluster_id, ts)
"""
from __future__ import annotations

import logging
import os
from contextlib import suppress
from typing import Any

logger = logging.getLogger(__name__)

# ClickHouse HTTP endpoint — overridable via env var.
# Default assumes ClickHouse runs on the same host (network_mode: host or localhost).
_CLICKHOUSE_URL = os.getenv("CLICKHOUSE_URL", "")
_CLICKHOUSE_USER = os.getenv("CLICKHOUSE_USER", "default")
_CLICKHOUSE_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD", "")
_CLICKHOUSE_DB = os.getenv("CLICKHOUSE_DB", "bnkforge")

# Exposed for use in route modules so the DB name stays env-configurable
CLICKHOUSE_DB = _CLICKHOUSE_DB

# DDL executed once to set up the database and table
_DDL_STATEMENTS = [
    f"CREATE DATABASE IF NOT EXISTS {_CLICKHOUSE_DB}",
    f"""
    CREATE TABLE IF NOT EXISTS {_CLICKHOUSE_DB}.waf_events (
        cluster_id       UInt32,
        ts               DateTime,
        policy_name      LowCardinality(String),
        vs_name          LowCardinality(String),
        outcome          LowCardinality(String),
        attack_type      LowCardinality(String),
        ip_client        String,
        uri              String,
        method           LowCardinality(String),
        violation_rating UInt8,
        support_id       String,
        sig_ids          String,
        sig_names        String,
        namespace        LowCardinality(String),
        ingest_ts        DateTime DEFAULT now()
    )
    ENGINE = MergeTree()
    PARTITION BY toYYYYMM(ts)
    ORDER BY (cluster_id, ts)
    TTL ts + INTERVAL 90 DAY
    SETTINGS index_granularity = 8192
    """,
]


class ClickHouseService:
    """Thin wrapper around clickhouse-connect for WAF analytics queries.

    Uses a new HTTP client per query — clickhouse_connect's HTTP transport is
    stateless, so per-request clients are safe and avoid shared-state race
    conditions when FastAPI handles multiple concurrent requests.
    """

    def __init__(self) -> None:
        self._available = False
        self._host: str = ""
        self._port: int = 8123
        self._url = _CLICKHOUSE_URL
        if self._url:
            self._connect()

    def _connect(self) -> None:
        try:
            import clickhouse_connect  # type: ignore[import-untyped]

            url = self._url.rstrip("/")
            if "://" in url:
                url = url.split("://", 1)[1]
            host, _, port_str = url.partition(":")
            self._host = host
            self._port = int(port_str) if port_str else 8123

            # Create a temporary client just to verify connectivity and set up schema
            client = clickhouse_connect.get_client(
                host=self._host,
                port=self._port,
                username=_CLICKHOUSE_USER,
                password=_CLICKHOUSE_PASSWORD,
                connect_timeout=5,
                send_receive_timeout=30,
            )
            for ddl in _DDL_STATEMENTS:
                client.command(ddl)
            client.close()
            self._available = True
            logger.info("ClickHouse ready: %s:%s", self._host, self._port)
        except ImportError:
            logger.warning("clickhouse-connect not installed — WAF dashboard disabled")
        except Exception as exc:
            logger.warning("ClickHouse connection failed: %s", exc)

    def _new_client(self) -> Any:
        """Create a fresh HTTP client for a single query — avoids shared-state races."""
        import clickhouse_connect  # type: ignore[import-untyped]
        return clickhouse_connect.get_client(
            host=self._host,
            port=self._port,
            username=_CLICKHOUSE_USER,
            password=_CLICKHOUSE_PASSWORD,
            connect_timeout=5,
            send_receive_timeout=30,
        )

    @property
    def available(self) -> bool:
        return self._available

    def query(self, sql: str, parameters: dict | None = None) -> list[dict]:
        """Execute a SELECT using a fresh per-request client to avoid concurrency issues."""
        if not self._available:
            return []
        client = None
        try:
            client = self._new_client()
            result = client.query(sql, parameters=parameters or {})
            cols = result.column_names
            return [dict(zip(cols, row)) for row in result.result_rows]
        except Exception as exc:
            logger.error("ClickHouse query failed: %s | SQL: %.200s", exc, sql)
            return []
        finally:
            if client:
                with suppress(Exception):
                    client.close()

    def insert_rows(self, rows: list[dict]) -> int:
        """Bulk-insert rows into waf_events using a fresh client."""
        if not self._available or not rows:
            return 0
        client = None
        try:
            client = self._new_client()
            cols = list(rows[0].keys())
            data = [[r[c] for c in cols] for r in rows]
            client.insert(f"{_CLICKHOUSE_DB}.waf_events", data, column_names=cols)
            return len(rows)
        except Exception as exc:
            logger.error("ClickHouse insert failed: %s", exc)
            return 0
        finally:
            if client:
                with suppress(Exception):
                    client.close()

    def count_events(self, cluster_id: int, hours: int = 24) -> int:
        rows = self.query(
            f"SELECT count() FROM {_CLICKHOUSE_DB}.waf_events"
            " WHERE cluster_id = {cid:UInt32} AND ts >= now() - INTERVAL {h:UInt32} HOUR",
            {"cid": cluster_id, "h": hours},
        )
        return int(rows[0].get("count()", 0)) if rows else 0


# Module-level singleton — created once on import.
# Returns None-equivalent when CLICKHOUSE_URL is not set.
_service: ClickHouseService | None = None


def get_clickhouse() -> ClickHouseService:
    """Return the module-level ClickHouseService singleton."""
    global _service  # noqa: PLW0603
    if _service is None:
        _service = ClickHouseService()
    return _service
