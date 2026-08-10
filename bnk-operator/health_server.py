"""
Health Server — HTTP endpoint for Kubernetes liveness/readiness probes + Prometheus metrics.

Endpoints:
  GET /healthz  — liveness probe (is the process alive?)
  GET /readyz   — readiness probe (is the operator connected to BNK-Forge?)
  GET /metrics  — comprehensive Prometheus-compatible metrics

Uses aiohttp for a lightweight HTTP server (~2 MB, no FastAPI/Uvicorn overhead).
"""

import asyncio
import logging
import time

from aiohttp import web

logger = logging.getLogger("bnk-operator.health-server")


async def start_health_server(port: int, client, shutdown_event: asyncio.Event):
    """
    Start the HTTP health server.

    Args:
        port: HTTP port to listen on (default 8080)
        client: ControlPlaneClient or PollingClient instance
        shutdown_event: Set when shutting down
    """
    start_time = time.time()

    async def healthz(request: web.Request) -> web.Response:
        """Liveness probe — always OK if process is running."""
        return web.json_response({
            "status": "ok",
            "uptime_seconds": int(time.time() - start_time),
        })

    async def readyz(request: web.Request) -> web.Response:
        """Readiness probe — OK only if connected to control plane."""
        if client.connected:
            return web.json_response({
                "status": "ready",
                "connected": True,
                "operator_id": client.operator_id,
                "control_plane": client.url,
            })
        else:
            return web.json_response(
                {
                    "status": "not_ready",
                    "connected": False,
                    "reason": "Not connected to control plane",
                },
                status=503,
            )

    async def metrics(request: web.Request) -> web.Response:
        """Comprehensive Prometheus-compatible metrics."""
        uptime = int(time.time() - start_time)
        connected = 1 if client.connected else 0
        commands_executed = getattr(client, "_commands_executed", 0)
        commands_failed = getattr(client, "commands_failed", 0)
        reconnect_count = getattr(client, "reconnect_count", 0)
        avg_duration = getattr(client, "avg_command_duration", 0.0)

        # Health watcher metrics (if available via client reference)
        health_checks = 0
        health_errors = 0

        lines = [
            # Operator status
            "# HELP bnk_operator_up Whether the operator process is running.",
            "# TYPE bnk_operator_up gauge",
            "bnk_operator_up 1",
            "",
            "# HELP bnk_operator_connected Whether connected to BNK-Forge control plane.",
            "# TYPE bnk_operator_connected gauge",
            f"bnk_operator_connected {connected}",
            "",
            "# HELP bnk_operator_uptime_seconds Operator uptime in seconds.",
            "# TYPE bnk_operator_uptime_seconds gauge",
            f"bnk_operator_uptime_seconds {uptime}",
            "",
            # Command metrics
            "# HELP bnk_operator_commands_total Total commands executed.",
            "# TYPE bnk_operator_commands_total counter",
            f'bnk_operator_commands_total{{status="success"}} {commands_executed}',
            f'bnk_operator_commands_total{{status="failed"}} {commands_failed}',
            "",
            "# HELP bnk_operator_command_duration_seconds Average command execution time.",
            "# TYPE bnk_operator_command_duration_seconds gauge",
            f"bnk_operator_command_duration_seconds {avg_duration:.3f}",
            "",
            # Connection metrics
            "# HELP bnk_operator_reconnects_total Total reconnection attempts.",
            "# TYPE bnk_operator_reconnects_total counter",
            f"bnk_operator_reconnects_total {reconnect_count}",
            "",
            # Health check metrics
            "# HELP bnk_operator_health_checks_total Total health checks performed.",
            "# TYPE bnk_operator_health_checks_total counter",
            f"bnk_operator_health_checks_total {health_checks}",
            "",
            "# HELP bnk_operator_health_check_errors_total Total health check errors.",
            "# TYPE bnk_operator_health_check_errors_total counter",
            f"bnk_operator_health_check_errors_total {health_errors}",
            "",
            # Info metric
            "# HELP bnk_operator_info Operator version and cluster information.",
            "# TYPE bnk_operator_info gauge",
            f'bnk_operator_info{{version="{getattr(client, "operator_version", "unknown")}",'
            f'cluster="{getattr(client, "cluster_name", "unknown")}"}} 1',
        ]
        return web.Response(
            text="\n".join(lines) + "\n",
            content_type="text/plain; version=0.0.4; charset=utf-8",
        )

    app = web.Application()
    app.router.add_get("/healthz", healthz)
    app.router.add_get("/readyz", readyz)
    app.router.add_get("/metrics", metrics)

    runner = web.AppRunner(app, access_log=None)  # Suppress access logs
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)

    try:
        await site.start()
        logger.info(f"Health server listening on port {port}")

        # Wait for shutdown
        await shutdown_event.wait()
    except asyncio.CancelledError:
        pass
    finally:
        await runner.cleanup()
        logger.info("Health server stopped")
