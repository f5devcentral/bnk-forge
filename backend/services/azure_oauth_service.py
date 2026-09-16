"""Shared helpers for Azure OAuth2 token exchange flows."""

from __future__ import annotations

from typing import Any

import requests


def request_azure_oauth_token(
    *,
    tenant_id: str,
    data: dict[str, str],
    timeout: int = 30,
) -> dict[str, Any]:
    """Exchange Azure OAuth2 credentials for a token payload.

    Raises requests/ValueError exceptions on HTTP or payload failures so callers
    can handle fallback/logging behavior at their boundary.
    """
    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    response = requests.post(token_url, data=data, timeout=timeout)
    response.raise_for_status()

    token_data = response.json()
    if not token_data.get("access_token"):
        raise ValueError("Azure OAuth2 token response missing access_token")

    return token_data
