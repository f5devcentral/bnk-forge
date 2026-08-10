"""
Health Watcher — continuously monitors BNK health and reports to control plane.

Runs on a configurable loop. Only sends health reports when:
  1. State has changed (delta detection)
  2. Or every 5 minutes (periodic full report)

This keeps the WebSocket quiet most of the time while ensuring BNK-Forge
always has a recent view of cluster health.
"""

import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("bnk-operator.health")

# How often to check health (seconds) — configurable via env
CHECK_INTERVAL = int(os.environ.get("HEALTH_CHECK_INTERVAL", "30"))

# Force a full report even if nothing changed (seconds)
FULL_REPORT_INTERVAL = int(os.environ.get("HEALTH_FULL_REPORT_INTERVAL", "300"))


class HealthWatcher:
    """Watches BNK cluster health and reports changes to the control plane."""

    def __init__(self, handlers=None):
        """
        Args:
            handlers: Shared CommandHandlers instance. If None, creates a new one
                      each cycle (legacy behavior, less efficient).
        """
        self._handlers = handlers
        self._last_health: Optional[Dict[str, Any]] = None
        self._last_full_report_time: float = 0
        self._check_count: int = 0
        self._error_count: int = 0

    @property
    def check_count(self) -> int:
        return self._check_count

    @property
    def error_count(self) -> int:
        return self._error_count

    async def watch_and_report(self, client, shutdown_event: asyncio.Event):
        """
        Main loop — check health every CHECK_INTERVAL seconds, report changes.

        Args:
            client: ControlPlaneClient instance (has send_health() method)
            shutdown_event: Set when operator is shutting down
        """
        # Wait for client to connect before starting
        while not client.connected and not shutdown_event.is_set():
            await asyncio.sleep(1)

        logger.info(
            f"Health watcher started (check_interval={CHECK_INTERVAL}s, "
            f"full_report_interval={FULL_REPORT_INTERVAL}s)"
        )

        while not shutdown_event.is_set():
            try:
                if client.connected:
                    await self._check_and_report(client)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._error_count += 1
                logger.error(f"Health check error: {e}")

            # Sleep until next check or shutdown
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=CHECK_INTERVAL)
                break  # Shutdown requested
            except asyncio.TimeoutError:
                pass  # Check interval elapsed

        logger.info(
            f"Health watcher stopped (checks={self._check_count}, errors={self._error_count})"
        )

    async def _check_and_report(self, client):
        """Run a health check and report to the control plane if changed."""
        self._check_count += 1

        # Use shared handlers instance, or create one if not provided
        if self._handlers:
            handlers = self._handlers
        else:
            from command_handlers import CommandHandlers
            handlers = CommandHandlers()

        health = await handlers.get_health({})

        if not health.get("success"):
            logger.warning(f"Health check failed: {health.get('error_message')}")
            # Still report the failure
            await client.send_health(health)
            return

        now = time.time()
        force_report = (now - self._last_full_report_time) >= FULL_REPORT_INTERVAL

        # Compare with last known state (simple JSON comparison)
        if force_report or self._has_changed(health):
            await client.send_health(health)
            self._last_health = health
            self._last_full_report_time = now

            if force_report:
                logger.debug("Sent periodic full health report")
            else:
                logger.info("Health state changed — sent update to control plane")

    def _has_changed(self, new_health: Dict[str, Any]) -> bool:
        """Check if health state has changed since last report."""
        if self._last_health is None:
            return True

        # Compare key fields (ignore timestamp)
        old = self._last_health.copy()
        new = new_health.copy()
        old.pop("timestamp", None)
        new.pop("timestamp", None)

        try:
            return json.dumps(old, sort_keys=True) != json.dumps(new, sort_keys=True)
        except (TypeError, ValueError):
            return True  # If serialization fails, report anyway
