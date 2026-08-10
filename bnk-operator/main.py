"""
BNK Operator — lightweight agent that runs on customer K8s clusters.

Connects to BNK-Forge control plane via WebSocket or HTTP polling.
Executes K8s operations (apply manifests, install Helm, scan cluster, report health)
locally inside the cluster using its ServiceAccount.

Supports two connectivity modes:
  - websocket: Persistent outbound WebSocket connection (default, lowest latency)
  - polling:   HTTP polling for commands (works through any proxy/firewall)

TLS Configuration:
  - TLS_CA_CERT       — path to custom CA certificate for self-signed BNK-Forge
  - TLS_INSECURE      — skip TLS verification (dev only, default: false)

Usage:
  Environment variables:
    CONTROL_PLANE_URL    — wss://bnk-forge/ws/operator (or https:// for polling)
    REGISTRATION_TOKEN   — bnk_reg_<hex>
    CLUSTER_NAME         — human-readable cluster name (default: hostname)
    CONNECTIVITY_MODE    — "websocket" (default) or "polling"
    POLL_INTERVAL_SECONDS — polling interval in seconds (default: 5, polling mode only)
    LOG_LEVEL            — DEBUG, INFO, WARNING (default: INFO)
    LOG_FORMAT           — "text" (default) or "json" for structured logging
    HEALTH_PORT          — HTTP port for liveness/readiness probes (default: 8080)
    TLS_CA_CERT          — path to custom CA cert file (PEM format)
    TLS_INSECURE         — "true" to skip TLS verification (dev/testing only)
"""

import asyncio
import logging
import os
import signal
import sys

from command_handlers import CommandHandlers
from cwc_handlers import CWCHandlers
from health_server import start_health_server
from health_watcher import HealthWatcher
from llm_metrics_collector import LlmMetricsCollector

# ---------------------------------------------------------------------------
# Logging — supports both text and JSON format
# ---------------------------------------------------------------------------

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
LOG_FORMAT = os.environ.get("LOG_FORMAT", "text").lower()

if LOG_FORMAT == "json":
    try:
        from pythonjsonlogger import jsonlogger

        handler = logging.StreamHandler(sys.stdout)
        formatter = jsonlogger.JsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%SZ",
        )
        handler.setFormatter(formatter)
        logging.root.handlers = [handler]
        logging.root.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    except ImportError:
        logging.basicConfig(
            level=getattr(logging, LOG_LEVEL, logging.INFO),
            format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%SZ",
            stream=sys.stdout,
        )
        logging.getLogger("bnk-operator").warning(
            "python-json-logger not installed, falling back to text format"
        )
else:
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
        stream=sys.stdout,
    )

logger = logging.getLogger("bnk-operator")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CONTROL_PLANE_URL = os.environ.get("CONTROL_PLANE_URL")
REGISTRATION_TOKEN = os.environ.get("REGISTRATION_TOKEN")
CLUSTER_NAME = os.environ.get("CLUSTER_NAME", os.environ.get("HOSTNAME", "unknown-cluster"))
HEALTH_PORT = int(os.environ.get("HEALTH_PORT", "8080"))
CONNECTIVITY_MODE = os.environ.get("CONNECTIVITY_MODE", "websocket").lower()
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL_SECONDS", "5"))
OPERATOR_VERSION = "1.2.0"

# TLS configuration
TLS_CA_CERT = os.environ.get("TLS_CA_CERT")
TLS_INSECURE = os.environ.get("TLS_INSECURE", "false").lower() in ("true", "1", "yes")


def _create_client(handlers: CommandHandlers):
    """Create the appropriate client based on connectivity mode."""
    tls_kwargs = {
        "tls_ca_cert": TLS_CA_CERT,
        "tls_insecure": TLS_INSECURE,
    }

    if CONNECTIVITY_MODE == "polling":
        from polling_client import PollingClient
        logger.info(f"Using HTTP polling mode (interval={POLL_INTERVAL}s)")
        return PollingClient(
            url=CONTROL_PLANE_URL,
            token=REGISTRATION_TOKEN,
            cluster_name=CLUSTER_NAME,
            operator_version=OPERATOR_VERSION,
            poll_interval=POLL_INTERVAL,
            **tls_kwargs,
        )
    else:
        from control_plane_client import ControlPlaneClient
        logger.info("Using WebSocket mode")
        return ControlPlaneClient(
            url=CONTROL_PLANE_URL,
            token=REGISTRATION_TOKEN,
            cluster_name=CLUSTER_NAME,
            operator_version=OPERATOR_VERSION,
            **tls_kwargs,
        )


async def main():
    """Entry point — connect to control plane and run operator loop."""

    if not CONTROL_PLANE_URL:
        logger.error("CONTROL_PLANE_URL is required")
        sys.exit(1)
    if not REGISTRATION_TOKEN:
        logger.error("REGISTRATION_TOKEN is required")
        sys.exit(1)

    tls_info = "TLS: "
    if TLS_INSECURE:
        tls_info += "insecure (verification disabled)"
    elif TLS_CA_CERT:
        tls_info += f"custom CA ({TLS_CA_CERT})"
    elif CONTROL_PLANE_URL.startswith(("wss://", "https://")):
        tls_info += "system CAs"
    else:
        tls_info += "disabled (plain text)"

    logger.info("=" * 60)
    logger.info(f"BNK Operator v{OPERATOR_VERSION}")
    logger.info(f"  Cluster:        {CLUSTER_NAME}")
    logger.info(f"  Control Plane:  {CONTROL_PLANE_URL}")
    logger.info(f"  Mode:           {CONNECTIVITY_MODE}")
    logger.info(f"  {tls_info}")
    logger.info(f"  Health Port:    {HEALTH_PORT}")
    logger.info(f"  Log Format:     {LOG_FORMAT}")
    logger.info("=" * 60)

    # Initialize components — shared handler instances
    handlers = CommandHandlers()
    cwc_handlers = CWCHandlers()
    health_watcher = HealthWatcher(handlers=handlers)
    llm_metrics_collector = LlmMetricsCollector()
    client = _create_client(handlers)

    # Register command handlers (same for both WS and polling)
    # K8s operations
    client.on_command("apply_manifests", handlers.apply_manifests)
    client.on_command("destroy_manifests", handlers.destroy_manifests)
    client.on_command("install_helm", handlers.install_helm)
    client.on_command("uninstall_helm", handlers.uninstall_helm)
    client.on_command("scan_cluster", handlers.scan_cluster)
    client.on_command("get_health", handlers.get_health)
    client.on_command("get_resources", handlers.get_resources)
    client.on_command("get_pod_logs", handlers.get_pod_logs)
    # K8S-UX-010: CWC operations (QKView, licensing, cert setup)
    client.on_command("cwc_request", cwc_handlers.cwc_request)
    client.on_command("cwc_check_status", cwc_handlers.cwc_check_status)
    client.on_command("cwc_setup_certs", cwc_handlers.cwc_setup_certs)
    client.on_command("cwc_qkview_list", cwc_handlers.cwc_qkview_list)
    client.on_command("cwc_qkview_create", cwc_handlers.cwc_qkview_create)
    client.on_command("cwc_qkview_get", cwc_handlers.cwc_qkview_get)
    client.on_command("cwc_qkview_download", cwc_handlers.cwc_qkview_download)
    client.on_command("cwc_qkview_delete", cwc_handlers.cwc_qkview_delete)
    client.on_command("cwc_qkview_cancel", cwc_handlers.cwc_qkview_cancel)
    client.on_command("cwc_license_status", cwc_handlers.cwc_license_status)
    client.on_command("cwc_license_report", cwc_handlers.cwc_license_report)
    client.on_command("cwc_license_activate", cwc_handlers.cwc_license_activate)
    client.on_command("cwc_license_receipt", cwc_handlers.cwc_license_receipt)

    # Graceful shutdown
    shutdown_event = asyncio.Event()

    def _signal_handler():
        logger.info("Shutdown signal received")
        shutdown_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _signal_handler)

    # Start all concurrent tasks
    tasks = [
        asyncio.create_task(client.run(shutdown_event), name="control-plane"),
        asyncio.create_task(
            health_watcher.watch_and_report(client, shutdown_event),
            name="health-watcher",
        ),
        asyncio.create_task(
            llm_metrics_collector.collect_and_report(client, shutdown_event),
            name="llm-metrics-collector",
        ),
        asyncio.create_task(
            start_health_server(HEALTH_PORT, client, shutdown_event),
            name="health-server",
        ),
    ]

    # Wait for shutdown signal
    await shutdown_event.wait()
    logger.info("Shutting down...")

    # Cancel all tasks
    for task in tasks:
        task.cancel()

    await asyncio.gather(*tasks, return_exceptions=True)
    logger.info("BNK Operator stopped")


if __name__ == "__main__":
    asyncio.run(main())
