"""Run-secret persistence for container-engine runs.

On every container-artifact run we resolve the image-pull credential for the
artifact's registry host, persist it on the project as the ``cne_pull_secret``
ProjectSecret (a base64 dockerconfigjson), and — when the project has a
registered cluster — push it into that cluster as a
``kubernetes.io/dockerconfigjson`` Secret so in-cluster pulls of the same image
succeed.

Pull-credential resolution order for a given registry host:
  1. a matching :class:`ContainerRegistry` access method (standalone ghcr/quay/
     far carry their own secret today; derived types are resolved later),
  2. an existing ``cne_pull_secret`` ProjectSecret (already a dockerconfigjson),
  3. the global ``bnk.far_pull_secret_default`` system default.

This module does the DB/cluster side-effects; the pure engine never touches the
database. Kept here (not in the task file) so the upsert + resolution can be
unit/component-tested with mocked clients.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re

from sqlalchemy.orm import Session

from core.encryption import decrypt_value
from models import ContainerRegistry, ProjectSecret
from models.container_registry import DERIVED_TYPES
from services.container_registry_service import ContainerRegistryService
from services.defaults_service import get_default
from services.execution.container_engine import _INPUT_TOKEN_RE
from services.module_metadata import validate_workspace_relative_path
from services.secrets_service import SecretsService

logger = logging.getLogger(__name__)

# Canonical run-secret name persisted on the project for container pulls.
CNE_PULL_SECRET_NAME = "cne_pull_secret"

# In-cluster Secret name + default namespace the pull secret is pushed to.
CLUSTER_PULL_SECRET_NAME = "cne-pull-secret"
DEFAULT_PULL_SECRET_NAMESPACE = "default"


def resolve_pull_authfile_for_module(db: Session, project_id: int, library_module) -> str | None:
    """Resolve the merged pull authfile for a module's whole artifact graph.

    Walks the artifact references graph, enforces the registry-host allowlist,
    resolves a credential per container_image host (standalone secret or derived
    short-lived token), and merges them into ONE base64 dockerconfigjson. Falls
    back to an existing project ``cne_pull_secret`` then the global FAR default
    when the graph yields no credential (e.g. an all-public graph).

    Raises :class:`SupplyChainPolicyError` when a host is not on the allowlist.
    """
    from services.execution.supply_chain import resolve_graph_pull_authfile

    if library_module is not None:
        merged = resolve_graph_pull_authfile(db, library_module)
        if merged:
            return merged

    # Fallbacks: existing run secret, then global FAR default.
    existing = _existing_cne_pull_secret(db, project_id)
    if existing:
        return existing
    fallback = get_default(db, "bnk.far_pull_secret_default")
    if fallback:
        return str(fallback)
    return None


def resolve_pull_authfile(db: Session, project_id: int, registry_host: str) -> str | None:
    """Resolve a base64 dockerconfigjson for pulling from ``registry_host``.

    Single-host resolver (kept for callers that know exactly one host).
    Returns the base64-encoded dockerconfigjson string (suitable for a
    ``--authfile`` / imagePullSecret), or ``None`` when the image is public /
    no credential is configured.
    """
    host = (registry_host or "").strip().lower()

    # 1. A matching ContainerRegistry access method.
    authfile = _authfile_from_registry(db, host)
    if authfile:
        return authfile

    # 2. An existing cne_pull_secret ProjectSecret (already dockerconfigjson).
    existing = _existing_cne_pull_secret(db, project_id)
    if existing:
        return existing

    # 3. Global FAR default.
    fallback = get_default(db, "bnk.far_pull_secret_default")
    if fallback:
        return str(fallback)

    return None


def _authfile_from_registry(db: Session, host: str) -> str | None:
    """Build a base64 dockerconfigjson from a ContainerRegistry matching host."""
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

    if match.type in DERIVED_TYPES:
        # Derived types exchange a cloud credential for a short-lived registry
        # token at pull time — that exchange lands in the supply-chain phase.
        logger.info(
            "Registry '%s' is a derived type (%s); short-lived token exchange "
            "not yet implemented — skipping its pull authfile.",
            match.name,
            match.type,
        )
        return None

    if match.type == "far":
        if not match.far_service_account_encrypted:
            return None
        sa_json = decrypt_value(match.far_service_account_encrypted)
        return ContainerRegistryService.build_far_dockerconfigjson(match.registry_host, sa_json)

    # ghcr | quay — HTTP Basic (username, token) → dockerconfigjson.
    if not match.token_encrypted:
        return None
    token = decrypt_value(match.token_encrypted)
    return _build_basic_dockerconfigjson(match.registry_host, match.username or "", token)


def _build_basic_dockerconfigjson(host: str, username: str, password: str) -> str:
    auth = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    document = {"auths": {host: {"username": username, "password": password, "auth": auth}}}
    return base64.b64encode(json.dumps(document).encode("utf-8")).decode("ascii")


def _existing_cne_pull_secret(db: Session, project_id: int) -> str | None:
    secret = (
        db.query(ProjectSecret)
        .filter(
            ProjectSecret.project_id == project_id,
            ProjectSecret.name == CNE_PULL_SECRET_NAME,
            ProjectSecret.is_active,
        )
        .first()
    )
    if secret and secret.value_encrypted:
        return decrypt_value(secret.value_encrypted)
    return None


def persist_cne_pull_secret(db: Session, project_id: int, authfile_b64: str) -> ProjectSecret:
    """Upsert the project's ``cne_pull_secret`` ProjectSecret (base64 dockerconfigjson).

    Idempotent: if an active secret already exists its value is updated in
    place; otherwise a new (or reactivated soft-deleted) value secret is created
    via SecretsService.
    """
    from core.encryption import encrypt_value

    service = SecretsService(db)
    existing = service.get_secret_by_name(project_id, CNE_PULL_SECRET_NAME)
    if existing:
        existing.value_encrypted = encrypt_value(authfile_b64)
        existing.secret_type = "value"
        db.flush()
        db.refresh(existing)
        logger.info("Updated cne_pull_secret for project %s", project_id)
        return existing

    return service.create_value_secret(
        project_id=project_id,
        name=CNE_PULL_SECRET_NAME,
        value=authfile_b64,
        description="Image-pull credential (base64 dockerconfigjson) for container-engine runs.",
    )


def push_pull_secret_to_cluster(db: Session, project, authfile_b64: str) -> bool:
    """Push the pull secret into the project's cluster as a dockerconfigjson Secret.

    Best-effort: returns False (and logs) when the project has no cluster or the
    push fails — a failed cluster push must not fail the deployment run.
    """
    try:
        from kubernetes import client as k8s_client
        from kubernetes.client.rest import ApiException

        from models import KubernetesCluster
        from services.kubernetes_service import KubernetesService

        cluster = (
            db.query(KubernetesCluster)
            .filter(KubernetesCluster.project_id == project.id)
            .first()
        )
        if cluster is None:
            logger.debug("No cluster for project %s; skipping pull-secret push", project.id)
            return False

        api_client = KubernetesService(db).load_kubeconfig(cluster)
        core_v1 = k8s_client.CoreV1Api(api_client)

        # The in-cluster Secret stores the raw (decoded) dockerconfigjson under
        # the .dockerconfigjson key; data values are base64 of the file bytes.
        decoded = base64.b64decode(authfile_b64).decode("utf-8")
        body = k8s_client.V1Secret(
            metadata=k8s_client.V1ObjectMeta(
                name=CLUSTER_PULL_SECRET_NAME,
                namespace=DEFAULT_PULL_SECRET_NAMESPACE,
            ),
            type="kubernetes.io/dockerconfigjson",
            string_data={".dockerconfigjson": decoded},
        )
        try:
            core_v1.create_namespaced_secret(DEFAULT_PULL_SECRET_NAMESPACE, body)
        except ApiException as exc:
            if exc.status == 409:
                core_v1.patch_namespaced_secret(
                    CLUSTER_PULL_SECRET_NAME, DEFAULT_PULL_SECRET_NAMESPACE, body
                )
            else:
                raise
        logger.info("Pushed pull secret to cluster for project %s", project.id)
        return True
    except Exception as exc:  # never fail the run on a cluster push problem
        logger.warning("Failed to push pull secret to cluster for project %s: %s", project.id, exc)
        return False


# ── secret_files: project secrets → run workspace (#442) ─────────────────────


class MissingRequiredSecretError(Exception):
    """A secret_files entry names a project secret that isn't set."""


def _render_input_tokens(text: str, variables: dict) -> str:
    """Expand ``{{inputs.foo}}`` tokens — same convention as step argv/env."""

    def _sub(match: re.Match[str]) -> str:
        node: object = variables
        for part in match.group(1).split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return ""
        return "" if node is None else str(node)

    return _INPUT_TOKEN_RE.sub(_sub, text)


def materialize_secret_files(
    db: Session,
    project_id: int,
    manifest: dict,
    workspace_local_path: str,
    variables: dict | None = None,
) -> list[str]:
    """Write the artifact's declared ``secret_files`` into the run workspace.

    Vendor CLIs routinely want entitlement material as files in their working
    directory (ocibnkctl: FAR tarball + JWT under ``<poc>/keys/``). Container
    artifacts had no way to receive a project secret at all — only cloud creds
    and the image-pull secret — so those files could never arrive (#442).

    The worker writes them directly: it shares the workspace volume with the
    sibling container (it already writes run_once markers there) and runs as the
    same uid, so no chown dance is needed. Files are 0600 and re-created on
    every run, so a rotated secret takes effect on the next run.

    Persistence trade-off (deliberate): materialized files stay on the module's
    workspace volume for the module's lifetime — the decrypted copy is at rest
    on disk between runs. That is required by the tool contract: destroy runs
    the same materialization (``_build_engine_and_ctx`` is shared by every
    lifecycle op) and vendor CLIs like ocibnkctl need the entitlement files
    present for destroy as well as apply. Each run re-tightens mode to 0600,
    and the files are removed with the workspace on module/project deletion
    (``workspace_manager.cleanup_module_workspace`` /
    ``cleanup_project_workspaces``).

    Paths may use the same ``{{inputs.*}}`` templating as step argv (a tool whose
    workspace layout depends on a form input — e.g. ``<poc_name>/keys/…`` — could
    not name its destination otherwise). Rendering happens BEFORE the containment
    check, so a hostile input value cannot smuggle in traversal.

    Raises MissingRequiredSecretError when a declared secret isn't set: failing
    here names the missing secret, where letting the CLI run would fail
    obscurely minutes later.
    """
    entries = manifest.get("secret_files") or []
    if not entries:
        return []

    service = SecretsService(db)
    written: list[str] = []
    for entry in entries:
        secret_name = str(entry.get("secret_name") or "").strip()
        rendered_path = _render_input_tokens(str(entry.get("path") or ""), variables or {})
        rel_path = validate_workspace_relative_path(
            rendered_path, field=f"secret_files[{secret_name}].path"
        )

        secret = service.get_secret_by_name(project_id, secret_name)
        if secret is None:
            raise MissingRequiredSecretError(
                f"Required secret '{secret_name}' is not set on this project. "
                f"Add it under the project's Secrets tab, then re-run."
            )

        if secret.secret_type == "file":
            content = service.get_decrypted_file_content(secret)
        else:
            content = service.get_decrypted_value(secret).encode("utf-8")

        dest = os.path.join(workspace_local_path, rel_path)
        # Defence in depth: validation ran on the manifest, but re-assert
        # containment against the FULLY resolved path (every component
        # including the leaf) before writing. The workspace is writable by the
        # artifact's own container between runs, so a prior step can plant
        # symlinks anywhere under it — including as the destination file
        # itself — to redirect this write outside the workspace.
        workspace_real = os.path.realpath(workspace_local_path)
        dest_real = os.path.realpath(dest)
        if not (dest_real == workspace_real or dest_real.startswith(workspace_real + os.sep)):
            raise ValueError(
                f"secret_files[{secret_name}].path resolves outside the workspace"
            )
        # A pre-existing leaf symlink is never legitimate (this code only ever
        # creates regular files) — refuse even one that points inside the
        # workspace rather than writing the secret through it.
        if os.path.islink(dest):
            raise ValueError(
                f"secret_files[{secret_name}].path is a symlink in the workspace; "
                f"refusing to write a secret through it"
            )

        os.makedirs(os.path.dirname(dest), exist_ok=True)
        # Create 0600 from the start rather than write-then-chmod, so the
        # content is never briefly readable by other uids. O_CREAT's mode does
        # not apply to an existing file, so the fchmod below covers a re-run
        # over a file created before this code (or with a different umask).
        # O_NOFOLLOW closes the race between the islink check above and this
        # open: a symlink planted in between fails the open (ELOOP) instead of
        # being followed.
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
        with os.fdopen(os.open(dest, flags, 0o600), "wb") as handle:
            handle.write(content)
            # fchmod on the open descriptor, NOT os.chmod(dest) after close.
            # chmod by path follows symlinks, so a co-tenant swapping `dest`
            # for a symlink between our close and the chmod would get an
            # arbitrary reachable file chmod'd to 0600 (perm clobber, no
            # content exposure -- within the documented F3 co-tenancy, but
            # free to close). fchmod acts on the exact inode we opened with
            # O_NOFOLLOW, so there is no path re-resolution to race (#94 N1).
            os.fchmod(handle.fileno(), 0o600)
        written.append(rel_path)
        logger.info(
            "Materialized secret '%s' for project %s at workspace path %s",
            secret_name, project_id, rel_path,
        )

    return written
