"""
BNK traffic statistics — map TMM dataplane counters to Gateway listeners / egresses.

Data flow:
  1. ``fetch_tmm_traffic_stats`` contacts one TMM debug sidecar and pulls
     ``virtual_server_stat`` + ``fw_rule_stat`` plus optional ``configview``
     UUID metadata.  This is the only I/O in the module.
  2. ``analyze_traffic_stats`` takes the raw TMM output plus the BNK resource
     bundle from ``fetch_all_bnk_data`` and returns structured traffic stats
     per listener, egress, and firewall rule.  Pure data transformation.

Mapping strategy:
  - Primary: explicit hints from ``configview uuid <uuid>`` output
    (gateway/listener/egress names recorded in TMM config).
  - Fallback: heuristic name matching between TMM virtual-server names and
    Gateway/Listener or Egress CR names.

Graceful degradation:
  - If no TMM pods, no debug sidecar, or any exec fails, the function returns
    an empty stats envelope with ``available: false`` and a short error/message.
    The caller (``/f5bnk/data``) always gets a 200 response.
"""

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from kubernetes import client as k8s_client

from core.cache import cache
from services.bnk.helpers import resource_name, resource_ns
from services.tmm_debug_service import (
    DEFAULT_EXEC_TIMEOUT,
    discover_configview_uuids,
    exec_configview,
    exec_tmctl,
)

# Shared executor for configview uuid probes. Each probe is a kubectl exec
# (~2s) and the old sequential loop could take 100s on busy clusters. A small
# shared pool keeps latency bounded without spawning threads per request.
_TMM_CONFIGVIEW_WORKERS = min(4, (os.cpu_count() or 2) + 1)
_tmm_configview_executor: ThreadPoolExecutor | None = None

# Short-term cache for TMM traffic stats. The expensive part is configview uuid
# kubectl exec probes; cache the whole envelope so dashboard polling and tab
# switching don't re-probe every few seconds.
_TMM_TRAFFIC_STATS_CACHE_TTL = 60

# Configview uuid-to-resource mappings are stable for a given TMM pod (they
# reflect configured virtual servers/gateways). Cache them separately for 5
# minutes so the per-request stats fetch only does the fast tmctl calls.
_TMM_CONFIGVIEW_MAP_CACHE_TTL = 300


def _get_tmm_configview_executor() -> ThreadPoolExecutor:
    global _tmm_configview_executor
    if _tmm_configview_executor is None:
        _tmm_configview_executor = ThreadPoolExecutor(
            max_workers=_TMM_CONFIGVIEW_WORKERS,
            thread_name_prefix="tmm-cv-",
        )
    return _tmm_configview_executor


def _tmm_traffic_stats_cache_key(cluster_id: int) -> str:
    return f"bnk:tmm_stats:{cluster_id}"

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TMCTL_DIRECTORY = "blade"
_VIRTUAL_SERVER_STAT_TABLE = "virtual_server_stat"
_FW_RULE_STAT_TABLE = "fw_rule_stat"

# Columns we care about from tmctl tables.  We request a superset so the
# response is useful even if a particular TMM build omits one column.
_VIRTUAL_SERVER_COLUMNS = [
    "name",
    "clientside.bytes_in",
    "clientside.bytes_out",
    "clientside.cur_conns",
    "clientside.tot_conns",
    "serverside.bytes_in",
    "serverside.bytes_out",
    "serverside.cur_conns",
    "serverside.tot_conns",
]

_FW_RULE_COLUMNS = [
    "name",
    "hit_count",
    "action",
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fetch_tmm_traffic_stats(
    api_client: k8s_client.ApiClient,
    classified_pods: dict[str, list[dict]],
    timeout: int = DEFAULT_EXEC_TIMEOUT,
    cluster_id: int | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """
    Fetch raw traffic statistics from a TMM debug sidecar.

    Args:
        api_client: Authenticated K8s API client.
        classified_pods: Output of ``classify_f5_pods``; used to find TMM pods.
        timeout: Max seconds to wait for each ``tmctl``/``configview`` exec.
        cluster_id: When provided, results are cached for 15 seconds per cluster.
        force: Bypass the cache (used for explicit refresh actions).

    Returns:
        Dict with keys:
          - source: "tmctl"
          - podName, namespace
          - virtualServerStat: parsed tmctl result dict
          - fwRuleStat: parsed tmctl result dict
          - configviewMappings: list of parsed configview uuid outputs
          - error: None or a short error string
          - durationMs: total fetch time
    """
    cache_key = _tmm_traffic_stats_cache_key(cluster_id) if cluster_id is not None else None
    if not force and cache_key is not None:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    start = datetime.now(UTC)
    result: dict[str, Any] = {
        "source": "tmctl",
        "podName": None,
        "namespace": None,
        "virtualServerStat": None,
        "fwRuleStat": None,
        "configviewMappings": [],
        "error": None,
    }

    tmm_pods = classified_pods.get("tmm", []) if isinstance(classified_pods, dict) else []
    pod = _pick_tmm_pod(tmm_pods)
    if not pod:
        result["error"] = "No TMM pods with debug sidecar found"
        result["durationMs"] = _elapsed_ms(start)
        return result

    pod_name = pod["name"]
    namespace = pod["namespace"]
    result["podName"] = pod_name
    result["namespace"] = namespace

    try:
        vs_result = exec_tmctl(
            api_client, pod_name, namespace,
            _VIRTUAL_SERVER_STAT_TABLE, _VIRTUAL_SERVER_COLUMNS,
            directory=_TMCTL_DIRECTORY, timeout=timeout,
            cluster_id=cluster_id,
        )
        fw_result = exec_tmctl(
            api_client, pod_name, namespace,
            _FW_RULE_STAT_TABLE, _FW_RULE_COLUMNS,
            directory=_TMCTL_DIRECTORY, timeout=timeout,
            cluster_id=cluster_id,
        )
        result["virtualServerStat"] = vs_result
        result["fwRuleStat"] = fw_result

        # Only probe configview if tmctl succeeded and there are virtual servers
        # to map. Skipping the uuid probes when vs_rows is empty avoids ~50
        # sequential kubectl exec calls (each ~2s) on clusters with no traffic.
        if vs_result.get("exit_code") == 0 and _tmctl_rows_as_dicts(vs_result):
            mappings = _fetch_configview_mappings(
                api_client, pod_name, namespace, timeout, cluster_id=cluster_id
            )
            result["configviewMappings"] = mappings
    except Exception as exc:  # pragma: no cover - defensive catch-all
        logger.exception("Failed to fetch TMM traffic stats")
        result["error"] = f"TMM exec failed: {exc}"

    result["durationMs"] = _elapsed_ms(start)
    if cache_key is not None:
        cache.set(cache_key, result, ttl_seconds=_TMM_TRAFFIC_STATS_CACHE_TTL)
    return result


def analyze_traffic_stats(
    data: dict[str, Any],
    raw_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Map raw TMM traffic statistics to Gateway listeners / egresses / firewall rules.

    Pure function — all I/O must happen before this call (see
    ``fetch_tmm_traffic_stats``).

    Args:
        data: The BNK resource bundle from ``fetch_all_bnk_data``.
        raw_stats: Optional raw TMM stats envelope.  If None or unavailable,
            an empty but valid envelope is returned.

    Returns:
        Dict with keys:
          - source, podName, sampledAt, available, error
          - listeners: list of BnkListenerTrafficStats-shaped dicts
          - egresses: list of BnkEgressTrafficStats-shaped dicts
          - firewallRules: list of BnkFirewallRuleTrafficStats-shaped dicts
    """
    topology = data.get("topology", []) or []
    data_plane = data.get("dataPlane", {}) or {}

    envelope: dict[str, Any] = {
        "source": raw_stats.get("source") if raw_stats else None,
        "podName": raw_stats.get("podName") if raw_stats else None,
        "sampledAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "available": False,
        "error": (raw_stats.get("error") if raw_stats else None) or None,
        "listeners": [],
        "egresses": [],
        "firewallRules": [],
    }

    if not raw_stats or raw_stats.get("error"):
        return envelope

    vs_result = raw_stats.get("virtualServerStat") or {}
    if vs_result.get("exit_code") != 0:
        envelope["error"] = envelope["error"] or vs_result.get("stderr") or "virtual_server_stat failed"
        return envelope

    fw_result = raw_stats.get("fwRuleStat") or {}
    vs_rows = _tmctl_rows_as_dicts(vs_result)
    fw_rows = _tmctl_rows_as_dicts(fw_result)

    listener_index = _build_listener_index(topology)
    egress_index = _build_egress_index(data_plane.get("egresses", []))
    configview_index = _build_configview_index(raw_stats.get("configviewMappings", []))

    envelope["available"] = True
    envelope["listeners"] = _analyze_listener_stats(vs_rows, listener_index, configview_index)
    envelope["egresses"] = _analyze_egress_stats(vs_rows, egress_index, configview_index)
    envelope["firewallRules"] = _analyze_firewall_rule_stats(fw_rows, data)

    return envelope


# ---------------------------------------------------------------------------
# TMM pod selection
# ---------------------------------------------------------------------------


def _pick_tmm_pod(tmm_pods: list[dict]) -> dict | None:
    """Pick a Running TMM pod that has a debug sidecar container."""
    for pod in tmm_pods:
        if not isinstance(pod, dict):
            continue
        phase = (pod.get("phase") or "").lower()
        if phase != "running":
            continue
        containers = pod.get("containers", []) or []
        container_names = {c.get("name", "") for c in containers if isinstance(c, dict)}
        if "debug" in container_names:
            return pod
    return None


# ---------------------------------------------------------------------------
# configview mapping discovery
# ---------------------------------------------------------------------------


def _fetch_configview_mappings(
    api_client: k8s_client.ApiClient,
    pod_name: str,
    namespace: str,
    timeout: int,
    cluster_id: int | None = None,
) -> list[dict[str, Any]]:
    """Run configview list + uuid for each UUID and return parsed metadata."""
    cache_key = f"bnk:configview_mappings:{cluster_id}:{pod_name}:{namespace}" if cluster_id is not None else None
    if cluster_id is not None:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    mappings: list[dict[str, Any]] = []
    try:
        uuids_result = discover_configview_uuids(
            api_client, pod_name, namespace, timeout, cluster_id=cluster_id
        )
        if uuids_result.get("exit_code") != 0:
            return mappings
        uuids = uuids_result.get("uuids", []) or []
        # Cap probing to avoid long exec bursts on busy clusters.
        uuids = uuids[:20]

        def _probe_uuid(uuid: str) -> dict[str, Any] | None:
            try:
                cv_result = exec_configview(
                    api_client, pod_name, namespace, uuid, timeout, cluster_id=cluster_id
                )
                if cv_result.get("exit_code") == 0:
                    hints = _parse_configview_uuid_output(cv_result.get("stdout", ""))
                    if hints:
                        return {"uuid": uuid, **hints}
            except Exception:
                logger.debug("configview uuid %s probe failed", uuid, exc_info=True)
            return None

        executor = _get_tmm_configview_executor()
        futures = [executor.submit(_probe_uuid, uuid) for uuid in uuids]
        for future in futures:
            result = future.result()
            if result:
                mappings.append(result)
    except Exception:
        logger.debug("configview list probe failed", exc_info=True)

    if cache_key is not None:
        cache.set(cache_key, mappings, ttl_seconds=_TMM_CONFIGVIEW_MAP_CACHE_TTL)
    return mappings


def _parse_configview_uuid_output(raw: str) -> dict[str, str]:
    """
    Extract mapping hints from a ``configview uuid`` output.

    TMM configview output varies by CR kind and release.  We first attempt JSON
    parsing, then fall back to line-oriented key/value extraction for common
    field names.
    """
    hints: dict[str, str] = {}
    if not raw or not raw.strip():
        return hints

    # Try JSON first
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            _extract_configview_hints_from_dict(parsed, hints)
            return hints
    except json.JSONDecodeError:
        pass

    # Line-oriented fallback
    for line in raw.splitlines():
        match = re.match(r"^\s*([a-zA-Z0-9_\-]+)\s*[:=]\s*(.+?)\s*$", line)
        if not match:
            continue
        key = match.group(1).lower().replace("-", "").replace("_", "")
        value = match.group(2).strip().strip('"\'')
        if key in ("name", "virtualserver", "virtualservername", "vs"):
            hints["virtual_server_name"] = value
        elif key in ("gateway", "gatewayname"):
            hints["gateway_name"] = value
        elif key in ("listener", "listenername"):
            hints["listener_name"] = value
        elif key in ("egress", "egressname"):
            hints["egress_name"] = value
        elif key in ("namespace", "ns"):
            hints["namespace"] = value
        elif key in ("kind",):
            hints["kind"] = value

    return hints


def _extract_configview_hints_from_dict(data: dict[str, Any], hints: dict[str, str]) -> None:
    """Recursively pull known keys out of a parsed configview JSON object."""
    if not isinstance(data, dict):
        return

    def _set(key: str, *paths: tuple[str, ...]) -> None:
        for path in paths:
            value = data
            for segment in path:
                if isinstance(value, dict):
                    value = value.get(segment)
                else:
                    value = None
                    break
            if isinstance(value, str) and value:
                hints[key] = value
                return

    _set("virtual_server_name", ("name",), ("virtual_server", "name"), ("vs", "name"))
    _set("gateway_name", ("gateway",), ("gateway_name",), ("spec", "gateway", "name"))
    _set("listener_name", ("listener",), ("listener_name",), ("spec", "listener", "name"))
    _set("egress_name", ("egress",), ("egress_name",), ("spec", "name"))
    _set("namespace", ("namespace",), ("metadata", "namespace"))
    _set("kind", ("kind",))

    # Recurse into nested dicts that might hold the actual config
    for value in data.values():
        if isinstance(value, dict):
            _extract_configview_hints_from_dict(value, hints)


def _build_configview_index(mappings: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """
    Build a lookup from normalized TMM virtual-server name to configview hints.
    """
    index: dict[str, dict[str, Any]] = {}
    for mapping in mappings:
        vs_name = mapping.get("virtual_server_name")
        if vs_name:
            index[_normalize_name(vs_name)] = mapping
    return index


# ---------------------------------------------------------------------------
# Index builders for topology objects
# ---------------------------------------------------------------------------


def _normalize_name(name: str) -> str:
    """Normalize a TMM/CR name for fuzzy matching."""
    return re.sub(r"[-_/.:]", "", str(name).lower())


def _build_listener_index(topology: list[dict]) -> dict[str, dict[str, Any]]:
    """Build a normalized lookup from plausible virtual-server names to listeners."""
    index: dict[str, dict[str, Any]] = {}
    for gw in topology:
        gw_name = gw.get("name", "")
        gw_ns = gw.get("namespace", "")
        for listener in gw.get("listeners", []) or []:
            listener_name = listener.get("name", "")
            entry = {
                "gatewayName": gw_name,
                "gatewayNamespace": gw_ns,
                "listenerName": listener_name,
                "protocol": listener.get("protocol", ""),
                "port": listener.get("port"),
            }
            # Candidate virtual-server name patterns seen in BNK TMM configs
            candidates = {
                _normalize_name(f"{gw_name}_{listener_name}"),
                _normalize_name(f"{gw_ns}_{gw_name}_{listener_name}"),
                _normalize_name(f"{gw_name}-{listener_name}"),
                _normalize_name(f"{gw_ns}_{gw_name}-{listener_name}"),
                _normalize_name(listener_name),
                _normalize_name(f"{gw_name}{listener_name}"),
            }
            for key in candidates:
                index.setdefault(key, entry)
    return index


def _build_egress_index(egresses: list[dict]) -> dict[str, dict[str, Any]]:
    """Build a normalized lookup from plausible virtual-server names to egresses."""
    index: dict[str, dict[str, Any]] = {}
    for egress in egresses or []:
        name = egress.get("name", "")
        ns = egress.get("namespace", "")
        entry = {"egressName": name, "namespace": ns}
        candidates = {
            _normalize_name(name),
            _normalize_name(f"{ns}_{name}"),
            _normalize_name(f"egress-{name}"),
            _normalize_name(f"{ns}_egress-{name}"),
        }
        for key in candidates:
            index.setdefault(key, entry)
    return index


# ---------------------------------------------------------------------------
# tmctl row parsing
# ---------------------------------------------------------------------------


def _tmctl_rows_as_dicts(tmctl_result: dict[str, Any]) -> list[dict[str, str]]:
    """Convert tmctl {columns, rows} into list of dicts keyed by column name."""
    columns = tmctl_result.get("columns", []) or []
    rows = tmctl_result.get("rows", []) or []
    result: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, (list, tuple)):
            continue
        row_dict = {}
        for i, col in enumerate(columns):
            row_dict[col] = row[i] if i < len(row) else ""
        result.append(row_dict)
    return result


def _parse_int(value: Any) -> int:
    """Parse a tmctl counter string to int, defaulting to 0."""
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return 0
    return 0


def _find_counter(row: dict[str, str], *candidates: str) -> int:
    """Return the first matching integer counter from a tmctl row."""
    for key in candidates:
        if key in row:
            return _parse_int(row[key])
    return 0


# ---------------------------------------------------------------------------
# Analysis: virtual-server → listener / egress
# ---------------------------------------------------------------------------


def _match_virtual_server_row(
    row: dict[str, str],
    listener_index: dict[str, dict[str, Any]],
    egress_index: dict[str, dict[str, Any]],
    configview_index: dict[str, dict[str, Any]],
) -> tuple[str | None, dict[str, Any] | None]:
    """
    Return ("listener" | "egress" | None, matched object) for a tmctl row.

    Priority:
      1. Explicit configview mapping on the TMM virtual-server name.
      2. Listener index match.
      3. Egress index match.
    """
    vs_name = (row.get("name") or "").strip()
    normalized = _normalize_name(vs_name)
    if not normalized:
        return None, None

    # 1. configview hint
    cv = configview_index.get(normalized)
    if cv:
        if cv.get("gateway_name") and cv.get("listener_name"):
            return "listener", {
                "gatewayName": cv.get("gateway_name", ""),
                "gatewayNamespace": cv.get("namespace", ""),
                "listenerName": cv.get("listener_name", ""),
            }
        if cv.get("egress_name"):
            return "egress", {
                "egressName": cv.get("egress_name", ""),
                "namespace": cv.get("namespace", ""),
            }

    # 2. listener match
    listener = listener_index.get(normalized)
    if listener:
        return "listener", listener

    # 3. egress match
    egress = egress_index.get(normalized)
    if egress:
        return "egress", egress

    # 4. Substring fallback: virtual-server name contains a listener or egress key
    for key, listener in listener_index.items():
        if key and (key in normalized or normalized in key):
            return "listener", listener
    for key, egress in egress_index.items():
        if key and (key in normalized or normalized in key):
            return "egress", egress

    return None, None


def _virtual_server_stats(row: dict[str, str]) -> dict[str, int]:
    """Extract counters from a virtual_server_stat row."""
    return {
        "clientsideBytesIn": _find_counter(row, "clientside.bytes_in"),
        "clientsideBytesOut": _find_counter(row, "clientside.bytes_out"),
        "clientsideCurConns": _find_counter(row, "clientside.cur_conns"),
        "clientsideTotConns": _find_counter(row, "clientside.tot_conns"),
        "serversideBytesIn": _find_counter(row, "serverside.bytes_in"),
        "serversideBytesOut": _find_counter(row, "serverside.bytes_out"),
        "serversideCurConns": _find_counter(row, "serverside.cur_conns"),
        "serversideTotConns": _find_counter(row, "serverside.tot_conns"),
    }


def _analyze_listener_stats(
    vs_rows: list[dict[str, str]],
    listener_index: dict[str, dict[str, Any]],
    configview_index: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Sum virtual-server stats for each Gateway listener."""
    totals: dict[tuple[str, str, str], dict[str, Any]] = {}

    for row in vs_rows:
        kind, matched = _match_virtual_server_row(row, listener_index, {}, configview_index)
        if kind != "listener" or not matched:
            continue

        key = (
            matched.get("gatewayNamespace", ""),
            matched.get("gatewayName", ""),
            matched.get("listenerName", ""),
        )
        if key not in totals:
            totals[key] = {
                "gatewayName": matched["gatewayName"],
                "gatewayNamespace": matched["gatewayNamespace"],
                "listenerName": matched["listenerName"],
                **{k: 0 for k in _virtual_server_stats({}).keys()},
            }
        for counter_key, value in _virtual_server_stats(row).items():
            totals[key][counter_key] += value

    return list(totals.values())


def _analyze_egress_stats(
    vs_rows: list[dict[str, str]],
    egress_index: dict[str, dict[str, Any]],
    configview_index: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Sum virtual-server stats for each F5SPKEgress."""
    totals: dict[tuple[str, str], dict[str, Any]] = {}

    for row in vs_rows:
        kind, matched = _match_virtual_server_row(row, {}, egress_index, configview_index)
        if kind != "egress" or not matched:
            continue

        key = (matched.get("namespace", ""), matched.get("egressName", ""))
        if key not in totals:
            totals[key] = {
                "egressName": matched["egressName"],
                "namespace": matched["namespace"],
                **{k: 0 for k in _virtual_server_stats({}).keys()},
            }
        for counter_key, value in _virtual_server_stats(row).items():
            totals[key][counter_key] += value

    return list(totals.values())


# ---------------------------------------------------------------------------
# Analysis: firewall rule hits
# ---------------------------------------------------------------------------


def _analyze_firewall_rule_stats(
    fw_rows: list[dict[str, str]],
    data: dict[str, Any],
) -> list[dict[str, Any]]:
    """Map fw_rule_stat rows to F5BigFwPolicy rules."""
    resources = data.get("resources", {}) or {}
    policies = resources.get("f5bigfwpolicy", []) or []

    # Build a lookup from plausible TMM rule name → policy + rule
    rule_index: dict[str, tuple[str, str, dict]] = {}
    for policy in policies:
        policy_name = resource_name(policy)
        policy_ns = resource_ns(policy)
        for rule in (policy.get("spec", {}).get("rule", []) or []):
            rule_name = rule.get("name", "")
            if not rule_name:
                continue
            tmm_name = _normalize_name(f"{policy_name}_{rule_name}")
            rule_index[tmm_name] = (policy_name, policy_ns, rule)
            # Some TMM builds use just the rule name
            rule_index[_normalize_name(rule_name)] = (policy_name, policy_ns, rule)

    results: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in fw_rows:
        raw_name = (row.get("name") or "").strip()
        if not raw_name:
            continue
        normalized = _normalize_name(raw_name)
        hit_count = _find_counter(row, "hit_count", "hits", "hitcount")

        matched = rule_index.get(normalized)
        if not matched:
            # Try splitting on common separators: policy_rule
            for sep in ("_", "-", "."):
                if sep in raw_name:
                    parts = raw_name.split(sep)
                    for i in range(1, len(parts)):
                        candidate = _normalize_name(sep.join(parts[i:]))
                        matched = rule_index.get(candidate)
                        if matched:
                            break
                if matched:
                    break

        if matched:
            policy_name, policy_ns, rule = matched
            key = (policy_ns, policy_name, rule.get("name", ""))
            if key in seen:
                # Sum hits if the same rule appears in multiple TMM rows
                existing = next(r for r in results if r["policyName"] == policy_name
                                and r["namespace"] == policy_ns
                                and r["ruleName"] == rule["name"])
                existing["hitCount"] += hit_count
            else:
                seen.add(key)
                results.append({
                    "policyName": policy_name,
                    "namespace": policy_ns,
                    "ruleName": rule.get("name", ""),
                    "action": rule.get("action", ""),
                    "ipProtocol": rule.get("ipProtocol", ""),
                    "hitCount": hit_count,
                })
        else:
            # Unmatched rule — still surface the raw hit count for observability
            results.append({
                "policyName": "",
                "namespace": "",
                "ruleName": raw_name,
                "action": row.get("action", ""),
                "ipProtocol": "",
                "hitCount": hit_count,
            })

    return results


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _elapsed_ms(start: datetime) -> int:
    """Return milliseconds elapsed since ``start``."""
    return int((datetime.now(UTC) - start).total_seconds() * 1000)
