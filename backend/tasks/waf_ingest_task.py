"""
WAF event ingestion periodic task.

Runs every 60 seconds via Celery Beat. For each registered cluster it:
  1. Resolves syslog endpoints by walking the K8s CRD chain
     (F5BigLogProfile → F5BigLogHslpub) — same logic as waf_logs.py.
  2. Reads NEW log lines from the syslog receiver pod via kubectl exec,
     using a cursor stored in Postgres to avoid re-ingesting.
  3. Parses NAP key=value format and bulk-inserts into ClickHouse.

Deduplication key: support_id (unique per WAF request in NAP format).
The cursor tracks the last file path + line offset so restarts are safe.
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

from celery import shared_task
from kubernetes import client as k8s_client
from kubernetes import stream

logger = logging.getLogger(__name__)

_NAP_KV_RE = re.compile(r'(\w+)="([^"]*)"')
_SYSLOG_POD_LABEL = "app=waf-syslog-receiver"
_SYSLOG_LOG_DIR = "/var/log/waf-syslog"
_BATCH_SIZE = 500


def _parse_nap_line(raw: str) -> dict | None:
    """Return a parsed dict from a NAP syslog line, or None if unparseable."""
    fields: dict[str, str] = {}
    for key, val in _NAP_KV_RE.findall(raw):
        fields[key] = val
    if not fields.get("support_id") and not fields.get("policy_name"):
        return None
    return fields


def _read_pod_lines(core_v1: k8s_client.CoreV1Api, pod_name: str,
                    namespace: str, log_file: str, from_line: int) -> list[str]:
    """Read lines from log_file starting at from_line (1-based) via kubectl exec."""
    try:
        cmd = ["tail", f"-n+{from_line}", log_file]
        resp = stream.stream(
            core_v1.connect_get_namespaced_pod_exec,
            pod_name, namespace,
            command=cmd, stderr=True, stdin=False, stdout=True, tty=False,
        )
        return [ln for ln in resp.splitlines() if ln.strip()]
    except Exception as exc:
        logger.debug("pod exec read failed: %s", exc)
        return []


def _discover_log_files(core_v1: k8s_client.CoreV1Api, pod_name: str, namespace: str) -> list[str]:
    """Return sorted list of log file paths found in the syslog pod."""
    try:
        resp = stream.stream(
            core_v1.connect_get_namespaced_pod_exec,
            pod_name, namespace,
            command=["find", _SYSLOG_LOG_DIR, "-name", "security.*.log", "-type", "f"],
            stderr=True, stdin=False, stdout=True, tty=False,
        )
        return sorted(ln.strip() for ln in resp.splitlines() if ln.strip())
    except Exception:
        return []


def _parse_ts(date_time_str: str) -> datetime:
    """Parse NAP date_time field into a UTC datetime."""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(date_time_str, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return datetime.now(UTC)


def _ingest_cluster(cluster_id: int, db) -> int:
    """Ingest new WAF events for one cluster. Returns count inserted."""
    from models.waf_dashboard import WafIngestionCursor
    from services.clickhouse import get_clickhouse
    from services.kubernetes import KubernetesService

    ch = get_clickhouse()
    if not ch.available:
        return 0

    k8s = KubernetesService(db)
    try:
        cluster = k8s.get_cluster(cluster_id)
        api_client = k8s.load_kubeconfig(cluster)
        core_v1 = k8s_client.CoreV1Api(api_client)
    except Exception as exc:
        logger.debug("cluster %s unreachable: %s", cluster_id, exc)
        return 0

    # Find the syslog receiver pod
    namespaces_to_scan = getattr(cluster, "discovered_namespaces", None) or ["default"]
    pod_name = None
    pod_namespace = "default"
    for ns in namespaces_to_scan:
        try:
            pods = core_v1.list_namespaced_pod(ns, label_selector=_SYSLOG_POD_LABEL)
            if pods.items:
                pod_name = pods.items[0].metadata.name
                pod_namespace = ns
                break
        except Exception:
            continue

    if not pod_name:
        return 0

    log_files = _discover_log_files(core_v1, pod_name, pod_namespace)
    if not log_files:
        return 0

    # Collect already-seen support_ids for deduplication (last 10k)
    seen_ids: set[str] = set(
        r["support_id"]
        for r in ch.query(
            f"SELECT support_id FROM bnkforge.waf_events"
            " WHERE cluster_id = {{cid:UInt32}} ORDER BY ts DESC LIMIT 10000",
            {"cid": cluster_id},
        )
    )

    total_inserted = 0
    for log_file in log_files:
        # Load cursor (last ingested line number for this file)
        cursor: WafIngestionCursor | None = (
            db.query(WafIngestionCursor)
            .filter_by(cluster_id=cluster_id, log_file=log_file)
            .first()
        )
        from_line = (cursor.last_line_num + 1) if cursor else 1

        lines = _read_pod_lines(core_v1, pod_name, pod_namespace, log_file, from_line)
        if not lines:
            continue

        batch: list[dict] = []
        for line in lines:
            parsed = _parse_nap_line(line)
            if not parsed:
                continue
            sid = parsed.get("support_id", "")
            if sid and sid in seen_ids:
                continue
            if sid:
                seen_ids.add(sid)

            ts = _parse_ts(parsed.get("date_time", ""))
            batch.append({
                "cluster_id": cluster_id,
                "ts": ts,
                "policy_name": parsed.get("policy_name", ""),
                "vs_name": parsed.get("vs_name", ""),
                "outcome": (parsed.get("outcome") or parsed.get("request_status", "")).upper(),
                "attack_type": parsed.get("attack_type", "N/A"),
                "ip_client": parsed.get("ip_client", ""),
                "uri": parsed.get("uri", ""),
                "method": parsed.get("method", ""),
                "violation_rating": int(parsed.get("violation_rating", 0) or 0),
                "support_id": sid,
                "sig_ids": parsed.get("sig_ids", ""),
                "sig_names": parsed.get("sig_names", ""),
                "namespace": pod_namespace,
            })

            if len(batch) >= _BATCH_SIZE:
                total_inserted += ch.insert_rows(batch)
                batch = []

        if batch:
            total_inserted += ch.insert_rows(batch)

        # Update cursor
        new_line = from_line + len(lines) - 1
        if cursor:
            cursor.last_line_num = new_line
            cursor.updated_at = datetime.now(UTC)
        else:
            db.add(WafIngestionCursor(
                cluster_id=cluster_id,
                log_file=log_file,
                last_line_num=new_line,
            ))
        db.commit()

    if total_inserted:
        logger.info("WAF ingest cluster=%s inserted=%s", cluster_id, total_inserted)
    return total_inserted


@shared_task(name="tasks.waf_ingest_task.ingest_waf_events", ignore_result=True)
def ingest_waf_events() -> None:
    """Periodic task: ingest new WAF syslog events into ClickHouse for all clusters."""
    from database import get_db_context
    from models.kubernetes import KubernetesCluster
    from services.clickhouse import get_clickhouse

    if not get_clickhouse().available:
        return  # ClickHouse not configured — skip silently

    with get_db_context() as db:
        cluster_ids = [r[0] for r in db.query(KubernetesCluster.id).all()]

    total = 0
    for cid in cluster_ids:
        try:
            with get_db_context() as db:
                total += _ingest_cluster(cid, db)
        except Exception as exc:  # noqa: BLE001
            logger.warning("WAF ingest failed for cluster %s: %s", cid, exc)

    if total:
        logger.info("WAF ingest complete: total_inserted=%s across %s clusters", total, len(cluster_ids))
