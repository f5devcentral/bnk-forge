"""
Auth-probe for the MCP container healthcheck.

Attempts to authenticate to the BNK-Forge backend using the configured
credentials (BNK_FORGE_USERNAME / BNK_FORGE_PASSWORD).  Exits 0 on success,
1 on auth failure — so Docker marks the container UNHEALTHY when the
password has drifted away from what the backend expects.

Usage (from healthcheck.test in docker-compose):
    python -m bnk_forge_mcp.healthcheck
"""

from __future__ import annotations

import json
import logging
import sys

import httpx

from .config import load_config

logger = logging.getLogger(__name__)


def probe() -> int:
    """
    Perform a synchronous login probe against the backend.

    Returns 0 if authentication succeeds, 1 otherwise.
    Intentionally avoids importing asyncio or the full BNKForgeClient so this
    stays a lightweight subprocess with no side-effects on the running server.
    """
    config = load_config()

    if not config.has_credentials:
        # bonnyr-f5 #188: with MCP_SERVICE_PASSWORD now shipping empty by default,
        # "no credentials" means the MCP server CANNOT authenticate — every tool
        # call 401s. Reporting healthy here made a default `make deploy` show a
        # green mcp container that does nothing. Fail the probe instead.
        logger.error("no MCP credentials configured — cannot authenticate to the backend")
        return 1

    try:
        resp = httpx.post(
            f"{config.api_base_url}/api/auth/login",
            json={"username": config.api_username, "password": config.api_password},
            timeout=8,
            verify=config.verify_ssl,
        )
    except Exception:
        # Backend unreachable — not an auth failure, Docker start_period handles this.
        return 1

    if resp.status_code == 200:
        try:
            data = resp.json()
            if data.get("token"):
                return 0
        except (json.JSONDecodeError, ValueError):
            # 200 with a non-JSON body → treat as auth failure, not a crash.
            return 1
    # 401 or missing token → password mismatch → UNHEALTHY
    return 1


def main() -> None:
    sys.exit(probe())


if __name__ == "__main__":
    main()
