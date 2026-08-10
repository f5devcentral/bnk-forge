"""Shared helpers for blueprint catalog services."""

from __future__ import annotations

from typing import Any


def _resolve_category(manifest: dict[str, Any] | None, source_path: str | None = None) -> str:
    """Return the category for a blueprint release.

    Priority:
    1. ``manifest["category"]`` when the manifest is present and the value is
       a non-empty string.
    2. Fall back to ``"bnk"`` in all other cases (empty manifest, None manifest,
       or a manifest that has no ``category`` key / an empty value).

    The ``source_path`` argument is accepted for call-site compatibility but is
    intentionally ignored — path-segment inference was the source of the
    historical category drift and is no longer used.
    """
    if manifest:
        category = manifest.get("category")
        if category and isinstance(category, str) and category.strip():
            return category.strip()
    return "bnk"
