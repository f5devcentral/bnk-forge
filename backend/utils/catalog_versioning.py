"""Shared catalog-versioning primitives (D-033).

Single home for the two idioms that were previously copy-pasted per service:
canonical content hashing (module sync, blueprint sync, blueprint catalog) and
tolerant semver ordering (module is_latest recompute, registry latest-version
pick). Keeping one definition prevents the two failure modes review #433
flagged: hash canonicalization drift producing spurious immutability conflicts,
and two backends disagreeing on which version is "latest".
"""

from __future__ import annotations

import hashlib
import json


def canonical_json_sha256(obj: object) -> str:
    """sha256 over the canonical JSON form of ``obj``.

    ``default=str`` keeps the hash total (datetime/Decimal stringify instead of
    raising); for JSON-serializable content the output is byte-identical to the
    pre-helper call sites, so stored hashes remain valid.
    """
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def version_sort_key(version: str | None) -> tuple:
    """Tolerant semver-ish ordering key.

    Handles the shapes seen in real catalogs: '1.20.0', 'v2.3.1',
    '2.3.0-ehf-2-3.2598.3-0.0.17'. Numeric dotted segments dominate; a release
    outranks a pre-release of the same numeric core; unparseable versions sort
    lowest (call sites break ties by row id / recency).
    """
    if not version:
        return ((), 0, "")
    # Build metadata (+build.7) carries no precedence per semver; strip it
    # before parsing (the registry _semver_key this consolidated also did),
    # or int("1+build") breaks and the version ranks below its patch series.
    bare = version.strip().lstrip("vV").split("+", 1)[0]
    core, _, prerelease = bare.partition("-")
    numeric_parts: list[int] = []
    for part in core.split("."):
        try:
            numeric_parts.append(int(part))
        except ValueError:
            break
    return (tuple(numeric_parts), 0 if prerelease else 1, prerelease)
