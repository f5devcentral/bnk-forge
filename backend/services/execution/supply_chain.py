"""Graph-wide supply-chain resolution for container artifacts.

A container artifact's manifest may declare ``references[]`` pointing at other
artifacts (e.g. a deploy image that also pulls sidecar images). Every
``container_image`` node in that graph needs a pull credential, and every
registry host must be on the admin allowlist. This module walks the references
graph for a deployed module, collects the registry host of every reachable
``container_image`` artifact, enforces the host allowlist, resolves a matching
named :class:`ContainerRegistry` per host, and assembles ONE merged
``dockerconfigjson`` whose ``auths`` cover all hosts. The merged document is the
project's ``cne_pull_secret``.

Two no-op supply-chain hooks (:func:`mirror_image`, :func:`verify_signature`)
mark the call sites where image mirroring and cosign signature verification will
plug in later — they are pass-through today (no mirror target / cosign policy).

This module does DB reads (resolving registries + referenced artifacts) but the
pure pull-secret assembly (:func:`build_merged_dockerconfigjson`) is a free
function so it can be unit-tested without a database.
"""

from __future__ import annotations

import base64
import json
import logging

from sqlalchemy.orm import Session

from models import ContainerRegistry, ModuleLibrary
from models.container_registry import DERIVED_TYPES
from services.container_registry_service import (
    ContainerRegistryService,
    DerivedTokenExchangeError,
)
from services.defaults_service import get_default

logger = logging.getLogger(__name__)

REGISTRY_HOST_ALLOWLIST_KEY = "container.registry_host_allowlist"


class SupplyChainPolicyError(Exception):
    """Raised when a supply-chain policy (e.g. host allowlist) is violated."""


# ── No-op supply-chain hooks (call sites present, pass-through today) ─────────

def mirror_image(image_ref: str) -> str:
    """Mirror ``image_ref`` into the internal registry and return the new ref.

    TODO(supply-chain): no mirror target is configured yet. Once an internal
    mirror registry exists, copy ``registry/repo@sha256:...`` into it (e.g. via
    ``skopeo copy`` / crane) and return the mirrored, digest-pinned reference.
    Pass-through for now — the original reference is returned unchanged so the
    call site is wired and behavior is unchanged.
    """
    return image_ref


def verify_signature(image_ref: str) -> bool:
    """Verify the cosign signature of ``image_ref``.

    TODO(supply-chain): no cosign policy / trust root is configured yet. Once a
    verification policy exists, run ``cosign verify`` against the digest-pinned
    reference and fail closed on an unsigned / untrusted image. Pass-through for
    now — always returns True so the call site is wired and behavior is
    unchanged.
    """
    return True


# ── Graph walk ───────────────────────────────────────────────────────────────

def collect_container_image_hosts(db: Session, library_module: ModuleLibrary) -> list[str]:
    """Collect the registry host of every ``container_image`` node in the graph.

    Walks the root module's own ``container_image`` block plus every artifact
    reachable through its persisted ``artifact_references`` graph (resolved by
    ``name@version`` against :class:`ModuleLibrary`). Returns a de-duplicated,
    order-stable list of lowercase registry hosts (mirror + signature hooks run
    against each node's image as a side effect).
    """
    hosts: list[str] = []
    seen: set[str] = set()

    def _add_host(manifest: dict | None) -> None:
        if not isinstance(manifest, dict):
            return
        if manifest.get("kind") and manifest.get("kind") != "container_image":
            return
        block = manifest.get("container_image")
        if not isinstance(block, dict):
            return
        host = (block.get("registry_host") or "").strip().lower()
        if host and host not in seen:
            seen.add(host)
            hosts.append(host)
        # Wire the supply-chain hooks against this node's image (no-op today).
        image_ref = _image_ref(block)
        if image_ref:
            mirror_image(image_ref)
            verify_signature(image_ref)

    # Root module.
    _add_host(getattr(library_module, "pack_manifest", None))

    # Referenced artifacts (one DB lookup per node by name@version).
    graph = getattr(library_module, "artifact_references", None) or {}
    nodes = graph.get("nodes") if isinstance(graph, dict) else None
    root_id = graph.get("root") if isinstance(graph, dict) else None
    for node_id in nodes or []:
        if node_id == root_id:
            continue
        ref_manifest = _resolve_referenced_manifest(db, str(node_id))
        _add_host(ref_manifest)

    return hosts


def _image_ref(block: dict) -> str | None:
    host = (block.get("registry_host") or "").strip().rstrip("/")
    repo = (block.get("repository") or "").strip().strip("/")
    digest = (block.get("digest") or "").strip()
    if host and repo and digest:
        return f"{host}/{repo}@{digest}"
    return None


def _resolve_referenced_manifest(db: Session, node_id: str) -> dict | None:
    """Resolve a ``name@version`` reference to a ModuleLibrary's pack_manifest."""
    name, _, version = node_id.partition("@")
    query = db.query(ModuleLibrary).filter(ModuleLibrary.name == name.strip())
    if version.strip():
        query = query.filter(ModuleLibrary.version == version.strip())
    lib = query.first()
    return getattr(lib, "pack_manifest", None) if lib else None


# ── Per-host credential resolution + merge ───────────────────────────────────

def resolve_host_auth(db: Session, host: str) -> dict[str, str] | None:
    """Resolve a single host's dockerconfig ``auths`` entry, or ``None``.

    Looks up a named :class:`ContainerRegistry` matching ``host``. Standalone
    types use their stored secret; derived types (``icr`` / ``ecr``) exchange
    their referenced cloud credential for a short-lived registry token. Returns
    ``{"username", "password", "auth"}`` or ``None`` when no credential is
    configured (public image / unresolved derived type).
    """
    if not host:
        return None
    registries = (
        db.query(ContainerRegistry)
        .filter(ContainerRegistry.registry_host.isnot(None))
        .all()
    )
    match = next(
        (r for r in registries if (r.registry_host or "").strip().lower() == host),
        None,
    )
    if match is None:
        return None

    service = ContainerRegistryService(db)
    try:
        username, password = service.resolve_pull_credentials(match)
    except DerivedTokenExchangeError as exc:
        if match.type in DERIVED_TYPES:
            logger.warning(
                "Derived registry '%s' (%s) token exchange skipped: %s",
                match.name, match.type, exc,
            )
        else:
            logger.warning("Registry '%s' has no usable credential: %s", match.name, exc)
        return None

    auth = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    return {"username": username, "password": password, "auth": auth}


def build_merged_dockerconfigjson(auths_by_host: dict[str, dict[str, str]]) -> str:
    """Assemble a base64 dockerconfigjson covering every host in ``auths_by_host``."""
    document = {"auths": dict(auths_by_host)}
    return base64.b64encode(json.dumps(document).encode("utf-8")).decode("ascii")


def resolve_registry_host_allowlist(db: Session) -> set[str]:
    """The registry-host allowlist, resolved fail-CLOSED.

    Reads ``container.registry_host_allowlist`` and falls back to the built-in
    safe default when the setting is unset, empty, or the lookup raises -- so
    the result is never empty and the check is always enforced. This is the
    SAME resolution ``module_sync_service._registry_host_allowlist`` uses at
    ingest. The two readers used to disagree on what "empty" meant: ingest fell
    back to the default (closed); this one returned early (open). An operator
    who cleared the setting, or a fresh install before the row existed, got
    an unenforced pull path while ingest still claimed to be enforcing
    (#79). One resolution, one answer.
    """
    from services.defaults_service import SYSTEM_DEFAULTS

    try:
        raw = get_default(db, REGISTRY_HOST_ALLOWLIST_KEY)
    except Exception:
        raw = None
    if not raw or not str(raw).strip():
        raw = SYSTEM_DEFAULTS[REGISTRY_HOST_ALLOWLIST_KEY]["value"]
    return {h.strip().lower() for h in str(raw).split(",") if h.strip()}


def enforce_host_allowlist(db: Session, hosts: list[str]) -> None:
    """Raise :class:`SupplyChainPolicyError` for any host not on the allowlist.

    Fail-closed: an unset/empty setting enforces the built-in default, never
    "anything goes". See resolve_registry_host_allowlist.
    """
    allow = resolve_registry_host_allowlist(db)
    for host in hosts:
        if host and host.lower() not in allow:
            raise SupplyChainPolicyError(
                f"Registry host '{host}' is not in the configured registry host "
                f"allowlist ({REGISTRY_HOST_ALLOWLIST_KEY})."
            )


def resolve_graph_pull_authfile(
    db: Session, library_module: ModuleLibrary
) -> str | None:
    """Resolve the merged pull authfile for a module's whole artifact graph.

    1. walk the references graph and collect every container_image host,
    2. enforce the registry-host allowlist,
    3. resolve a credential per host (standalone secret or derived token),
    4. merge them into ONE base64 dockerconfigjson.

    Returns ``None`` when no host in the graph has a configured credential
    (e.g. an all-public graph). Raises :class:`SupplyChainPolicyError` when a
    host is not on the allowlist.
    """
    hosts = collect_container_image_hosts(db, library_module)
    enforce_host_allowlist(db, hosts)

    auths_by_host: dict[str, dict[str, str]] = {}
    for host in hosts:
        entry = resolve_host_auth(db, host)
        if entry is not None:
            auths_by_host[host] = entry

    if not auths_by_host:
        return None
    return build_merged_dockerconfigjson(auths_by_host)
