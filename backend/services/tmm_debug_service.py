"""
TMM Debug Sidecar Service — exec commands into the debug container of TMM pods.

Architecture:
  Every f5-tmm-* pod has a sidecar container named "debug" that includes
  diagnostic tools: tmctl, configview, bdt_cli, tcpdump, ping, netkvest.

  We exec one-shot commands via kubeconfig — no agent pod needed.
  This is a DIFFERENT pattern from QKView/licensing which use the
  bnk-forge-agent pod (alpine/curl) for CWC REST API access.

  - bnk-forge-agent pod = CWC REST API access (mTLS + Bearer auth)
  - debug sidecar = TMM diagnostics (direct pod exec)

F5 Docs:
  https://clouddocs.f5.com/bigip-next-for-kubernetes/latest/overviews/spk-tmm-debug.html

Decision: D4 (see .agent/DECISIONS.md)
"""

import logging
import re
import time
from typing import Any

from kubernetes import client as k8s_client
from kubernetes.client.rest import ApiException
from kubernetes.stream import stream as k8s_stream

from services.bnk_pod_discovery import classify_f5_pods, discover_f5_pods

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEBUG_CONTAINER_NAME = "debug"

# Default timeout for one-shot debug commands (seconds)
DEFAULT_EXEC_TIMEOUT = 30

# bdt_cli default socket address (TMM instance 0)
BDT_CLI_DEFAULT_SOCKET = "tmm0:8850"


# ---------------------------------------------------------------------------
# Pod Discovery — list TMM pods with debug sidecar availability
# ---------------------------------------------------------------------------


def list_tmm_debug_pods(api_client: k8s_client.ApiClient) -> list[dict[str, Any]]:
    """
    List TMM pods that have a debug sidecar container.

    Returns a list of dicts:
        [{ name, namespace, has_debug, containers: [str, ...] }]

    Uses bnk_pod_discovery to find TMM pods, then checks each pod's
    container list for the "debug" container.
    """
    tenant_pods, utils_pods = discover_f5_pods(api_client)
    classified = classify_f5_pods(tenant_pods, utils_pods)
    tmm_pods = classified.get("tmm", [])

    results = []
    for pod in tmm_pods:
        container_names = [c["name"] for c in pod.get("containers", [])]
        results.append({
            "name": pod["name"],
            "namespace": pod["namespace"],
            "has_debug": DEBUG_CONTAINER_NAME in container_names,
            "containers": container_names,
            "phase": pod.get("phase", "Unknown"),
        })

    return results


# ---------------------------------------------------------------------------
# Core Exec — run a one-shot command in the debug sidecar
# ---------------------------------------------------------------------------


def exec_debug_command(
    api_client: k8s_client.ApiClient,
    pod_name: str,
    namespace: str,
    command: list[str],
    timeout: int = DEFAULT_EXEC_TIMEOUT,
) -> dict[str, Any]:
    """
    Execute a one-shot command in the debug sidecar of a TMM pod.

    Uses kubernetes.stream.stream() with _preload_content=True to collect
    the full output and return it as a string. NOT streaming.

    Args:
        api_client: Authenticated K8s API client (from KubernetesService.load_kubeconfig)
        pod_name: Name of the TMM pod (e.g. "f5-tmm-znvz2")
        namespace: Namespace of the TMM pod (typically "f5-bnk")
        command: Command to execute as a list of strings
        timeout: Max time to wait for command completion (seconds)

    Returns:
        { stdout: str, stderr: str, exit_code: int, duration_ms: int, command: str }

    Raises:
        ValueError: If the pod doesn't have a debug container
        ApiException: If the pod doesn't exist or is unreachable
    """
    start = time.monotonic()

    core_v1 = k8s_client.CoreV1Api(api_client)

    # Verify pod exists and has the debug container
    try:
        pod = core_v1.read_namespaced_pod(name=pod_name, namespace=namespace)
    except ApiException as e:
        if e.status == 404:
            raise ValueError(f"Pod '{pod_name}' not found in namespace '{namespace}'")
        raise

    container_names = []
    if pod.spec and pod.spec.containers:
        container_names = [c.name for c in pod.spec.containers]
    if pod.spec and pod.spec.init_containers:
        container_names += [c.name for c in pod.spec.init_containers]

    if DEBUG_CONTAINER_NAME not in container_names:
        present = ", ".join(container_names) if container_names else "none"
        raise ValueError(
            f"TMM pod '{pod_name}' has no '{DEBUG_CONTAINER_NAME}' sidecar "
            f"container (containers present: {present}). TMM debug tooling "
            "(tmctl, configview, bdt_cli) requires the TMM debug sidecar, "
            "which must be enabled in the BNK TMM configuration."
        )

    # Execute the command
    logger.info(
        f"Exec in {pod_name}/{namespace} -c {DEBUG_CONTAINER_NAME}: {' '.join(command)}"
    )

    try:
        # Use _preload_content=False to get the WSClient object so we can
        # read stdout and stderr separately and get the return code.
        resp = k8s_stream(
            core_v1.connect_get_namespaced_pod_exec,
            pod_name,
            namespace,
            container=DEBUG_CONTAINER_NAME,
            command=command,
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False,
            _preload_content=False,
            _request_timeout=timeout,
        )

        # Read output (resp is a WSClient when _preload_content=False)
        stdout_data = ""
        stderr_data = ""

        # Collect output until the channel closes
        while resp.is_open():
            resp.update(timeout=timeout)
            if resp.peek_stdout():
                stdout_data += resp.read_stdout()
            if resp.peek_stderr():
                stderr_data += resp.read_stderr()

        # Get exit code — may raise ValueError for distroless containers
        # (see LESSONS.md gotcha about WSClient.returncode)
        try:
            exit_code = resp.returncode
        except ValueError:
            # Distroless container or error string instead of int
            # If we got output, assume success; otherwise assume failure
            exit_code = 0 if stdout_data and not stderr_data else 1

        resp.close()

    except ApiException as e:
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.error(f"Exec failed in {pod_name}: {e}")
        return {
            "stdout": "",
            "stderr": f"Kubernetes API error: {e.reason or str(e)}",
            "exit_code": -1,
            "duration_ms": duration_ms,
            "command": " ".join(command),
        }

    duration_ms = int((time.monotonic() - start) * 1000)

    logger.info(
        f"Exec completed in {pod_name} ({duration_ms}ms, exit={exit_code}, "
        f"stdout={len(stdout_data)} bytes, stderr={len(stderr_data)} bytes)"
    )

    return {
        "stdout": stdout_data,
        "stderr": stderr_data,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "command": " ".join(command),
    }


# ---------------------------------------------------------------------------
# tmctl — structured table queries
# ---------------------------------------------------------------------------


def exec_tmctl(
    api_client: k8s_client.ApiClient,
    pod_name: str,
    namespace: str,
    table: str,
    columns: list[str] | None = None,
    width: int = 200,
    directory: str = "blade",
    timeout: int = DEFAULT_EXEC_TIMEOUT,
) -> dict[str, Any]:
    """
    Execute a tmctl command and parse the tabular output.

    Builds the command:
        tmctl -d <directory> <table> [-s <col1>,<col2>,...] [-w <width>]

    Returns:
        { columns: [str, ...], rows: [[str, ...], ...], raw: str,
          exit_code: int, duration_ms: int, command: str }
    """
    cmd = ["tmctl", "-d", directory, table]

    if columns:
        cmd.extend(["-s", ",".join(columns)])

    cmd.extend(["-w", str(width)])

    result = exec_debug_command(api_client, pod_name, namespace, cmd, timeout)

    # Parse tabular output if the command succeeded
    parsed = parse_tmctl_output(result["stdout"]) if result["exit_code"] == 0 else None

    return {
        "columns": parsed["columns"] if parsed else [],
        "rows": parsed["rows"] if parsed else [],
        "raw": result["stdout"],
        "stderr": result["stderr"],
        "exit_code": result["exit_code"],
        "duration_ms": result["duration_ms"],
        "command": result["command"],
    }


def parse_tmctl_output(raw_output: str) -> dict[str, Any] | None:
    """
    Parse tmctl space-separated tabular output into structured data.

    tmctl outputs columns separated by two or more spaces with a header row
    followed by a dashed separator line, then data rows.

    Example input:
        name                           clientside.bytes_in  clientside.bytes_out
        -----                          ---                  ---
        /Common/my_virtual             12345                67890

    Returns:
        { columns: ["name", "clientside.bytes_in", ...], rows: [["...", "...", ...], ...] }

    Returns None if the output cannot be parsed.
    """
    if not raw_output or not raw_output.strip():
        return None

    lines = raw_output.strip().split("\n")

    if len(lines) < 1:
        return None

    # Strategy: find column boundaries from the header line.
    # tmctl uses fixed-width columns. We can detect column positions from
    # the header by finding where multi-space gaps are.
    header_line = lines[0]

    # Find column headers — they're separated by 2+ spaces
    # First, find the start positions of each column
    col_positions = []
    in_space = True
    for i, ch in enumerate(header_line):
        if ch != " " and in_space:
            col_positions.append(i)
            in_space = False
        elif ch == " ":
            in_space = True

    if not col_positions:
        return None

    # Extract column names using positions
    columns = []
    for idx, pos in enumerate(col_positions):
        end = col_positions[idx + 1] if idx + 1 < len(col_positions) else len(header_line)
        columns.append(header_line[pos:end].strip())

    # Skip separator line(s) that contain only dashes/spaces
    data_start = 1
    for i in range(1, min(3, len(lines))):
        stripped = lines[i].strip()
        if stripped and all(c in "-= " for c in stripped):
            data_start = i + 1
        else:
            break

    # Parse data rows using the same column positions
    rows = []
    for line in lines[data_start:]:
        if not line.strip():
            continue

        row = []
        for idx, pos in enumerate(col_positions):
            end = col_positions[idx + 1] if idx + 1 < len(col_positions) else len(line)
            value = line[pos:end].strip() if pos < len(line) else ""
            row.append(value)

        rows.append(row)

    return {"columns": columns, "rows": rows}


# ---------------------------------------------------------------------------
# configview — CR config inspection
# ---------------------------------------------------------------------------


def exec_configview(
    api_client: k8s_client.ApiClient,
    pod_name: str,
    namespace: str,
    uuid: str,
    timeout: int = DEFAULT_EXEC_TIMEOUT,
) -> dict[str, Any]:
    """
    Execute 'configview uuid <uuid>' to inspect a specific CR config.

    Returns:
        { stdout, stderr, exit_code, duration_ms, command }
    """
    # Sanitize UUID — only allow alphanumeric, hyphens, underscores, dots
    if not re.match(r"^[\w\-.]+$", uuid):
        raise ValueError(f"Invalid UUID format: {uuid}")

    cmd = ["configview", "uuid", uuid]
    return exec_debug_command(api_client, pod_name, namespace, cmd, timeout)


def discover_configview_uuids(
    api_client: k8s_client.ApiClient,
    pod_name: str,
    namespace: str,
    timeout: int = DEFAULT_EXEC_TIMEOUT,
) -> dict[str, Any]:
    """
    Run 'configview list' to discover available configuration UUIDs.

    Returns:
        { uuids: [str, ...], raw: str, exit_code, duration_ms, command }
    """
    cmd = ["configview", "list"]
    result = exec_debug_command(api_client, pod_name, namespace, cmd, timeout)

    # Parse UUIDs from the output
    uuids = []
    if result["exit_code"] == 0 and result["stdout"]:
        # configview list outputs one entry per line; extract UUID-like strings
        for line in result["stdout"].strip().split("\n"):
            line = line.strip()
            if line and not line.startswith("#"):
                # Extract the first token as the UUID
                parts = line.split()
                if parts:
                    uuids.append(parts[0])

    return {
        "uuids": uuids,
        "raw": result["stdout"],
        "stderr": result["stderr"],
        "exit_code": result["exit_code"],
        "duration_ms": result["duration_ms"],
        "command": result["command"],
    }


# ---------------------------------------------------------------------------
# bdt_cli — networking diagnostics
# ---------------------------------------------------------------------------


def exec_bdt_cli(
    api_client: k8s_client.ApiClient,
    pod_name: str,
    namespace: str,
    subcommand: str,
    socket: str = BDT_CLI_DEFAULT_SOCKET,
    timeout: int = DEFAULT_EXEC_TIMEOUT,
) -> dict[str, Any]:
    """
    Execute 'bdt_cli -u -s <socket> <subcommand>' for networking diagnostics.

    Common subcommands: arp, route, connection list

    Returns:
        { stdout, stderr, exit_code, duration_ms, command }
    """
    # Sanitize subcommand — allow alphanumeric, spaces, hyphens
    if not re.match(r"^[\w\s\-.]+$", subcommand):
        raise ValueError(f"Invalid bdt_cli subcommand: {subcommand}")

    cmd = ["bdt_cli", "-u", "-s", socket] + subcommand.split()
    return exec_debug_command(api_client, pod_name, namespace, cmd, timeout)
