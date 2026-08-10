"""
ROKS-specific service for auto-registration and IAM bearer-token refresh.

Mirrors `services/eks_service.py` for IBM ROKS clusters. Extracted from
`cluster_management_service.py` so the IBM cluster lifecycle has a single
home and can grow independently (auto-discover, kubeconfig refresh, etc.)
without bloating cluster_management_service further.

ENG-006: This service retains its own db.commit() calls because it is
called from Celery tasks (opentofu_tasks.py) which are the outermost
callers, and from cluster_management_service which already manages its
own transaction.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

import yaml
from sqlalchemy.orm import Session

from core.encryption import encrypt_value
from models import KubernetesCluster, ProjectModule
from services.kubeconfig_normalizer import NormalizationSource, normalize_kubeconfig
from services.platform_context_service import PlatformContextService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output / kubeconfig parsing helpers
# ---------------------------------------------------------------------------

def decode_module_kubeconfig_output(outputs: dict[str, Any]) -> str | None:
    """Pull a kubeconfig from terraform outputs and return the YAML.

    Accepts kubeconfig under any of: ``kubeconfig``, ``kubeconfig_yaml``,
    ``kube_config``. The value may be raw YAML or base64-encoded YAML —
    we sniff for ``apiVersion:`` to decide.
    """
    raw = outputs.get("kubeconfig") or outputs.get("kubeconfig_yaml") or outputs.get("kube_config")
    if not raw or not isinstance(raw, str):
        return None

    candidate: str = raw.strip()
    if "apiVersion:" not in candidate:
        try:
            candidate = base64.b64decode(candidate).decode("utf-8")
        except Exception:
            logger.warning("ROKS kubeconfig output is neither YAML nor valid base64, ignoring")
            return None

    if "apiVersion:" not in candidate:
        logger.warning("ROKS kubeconfig output decoded but does not look like a kubeconfig, ignoring")
        return None

    return candidate


def kubeconfig_default_context(kubeconfig_yaml: str | None) -> str | None:
    """Pick the kubeconfig's current-context (or first listed context)."""
    if not kubeconfig_yaml:
        return None
    try:
        cfg = yaml.safe_load(kubeconfig_yaml)
    except Exception:
        return None
    if not isinstance(cfg, dict):
        return None
    current = cfg.get("current-context")
    if isinstance(current, str) and current:
        return current
    contexts = cfg.get("contexts") or []
    if contexts and isinstance(contexts[0], dict):
        name = contexts[0].get("name")
        return name if isinstance(name, str) else None
    return None


# ---------------------------------------------------------------------------
# Auto-registration
# ---------------------------------------------------------------------------

def register_roks_cluster(db: Session, module: ProjectModule) -> KubernetesCluster:
    """Register a deployed ROKS cluster to the Kubernetes page from module outputs.

    Args:
        db: Database session.
        module: The deployed ROKS ProjectModule with outputs.

    Returns:
        The created or existing KubernetesCluster record.

    Raises:
        ValueError: If required outputs are missing.
    """
    outputs: dict[str, Any] = dict(module.outputs or {})
    cluster_name = (
        outputs.get("cluster_name")
        or outputs.get("roks_cluster_name")
        or outputs.get("openshift_cluster_name")
    )
    cluster_endpoint = (
        outputs.get("cluster_endpoint")
        or outputs.get("openshift_cluster_public_endpoint")
    )
    cluster_id = (
        outputs.get("cluster_id")
        or outputs.get("openshift_cluster_id")
        or cluster_name
    )
    region = (
        outputs.get("region")
        or outputs.get("ibm_region")
        or getattr(module.project, "region", None)
    )

    if not all([cluster_name, cluster_endpoint, region]):
        missing = []
        if not cluster_name:
            missing.append("cluster_name")
        if not cluster_endpoint:
            missing.append("cluster_endpoint")
        if not region:
            missing.append("region")
        raise ValueError(f"Missing required ROKS outputs for registration: {', '.join(missing)}")

    kubeconfig_yaml = decode_module_kubeconfig_output(outputs)

    # Assert invariant — ROKS kubeconfig builder must produce portable output.
    # Hard-fail on violation (kubeconfig_invariant_violation) so the bug surfaces loudly.
    if kubeconfig_yaml:
        try:
            kubeconfig_yaml = normalize_kubeconfig(
                kubeconfig_yaml, source=NormalizationSource.CLOUD_API_GENERATED
            )
        except Exception as norm_exc:
            logger.error(
                "kubeconfig_invariant_violation: ROKS kubeconfig builder produced non-portable "
                "output for cluster %s: %s",
                cluster_name, norm_exc,
            )
            raise

    kubeconfig_encrypted = encrypt_value(kubeconfig_yaml) if kubeconfig_yaml else None
    kubeconfig_context = kubeconfig_default_context(kubeconfig_yaml)

    existing = db.query(KubernetesCluster).filter(
        KubernetesCluster.name == cluster_name,
        KubernetesCluster.project_id == module.project_id,
    ).first()
    if existing:
        # Backfill kubeconfig if the cluster row was created without one.
        if kubeconfig_encrypted and not existing.kubeconfig_encrypted:
            existing.kubeconfig_encrypted = kubeconfig_encrypted
            if kubeconfig_context:
                existing.context = kubeconfig_context
            db.flush()
        logger.info(f"Cluster {cluster_name} already registered (id={existing.id}), skipping")
        return existing

    cluster = KubernetesCluster(
        name=cluster_name,
        context=kubeconfig_context or cluster_name,
        api_server=cluster_endpoint,
        version=outputs.get("cluster_version") or outputs.get("openshift_cluster_version"),
        status="active",
        project_id=module.project_id,
        kubeconfig_encrypted=kubeconfig_encrypted,
        cloud_provider="ibm",
        region=region,
        default_namespace="default",
        meta_data={
            "auto_registered": True,
            "source_module_id": module.id,
            "cluster_id": cluster_id,
            "cluster_crn": outputs.get("openshift_cluster_crn"),
        },
    )
    PlatformContextService.apply_cluster_context(cluster)
    db.add(cluster)
    db.flush()
    db.refresh(cluster)
    logger.info(f"Successfully registered ROKS cluster {cluster_name} (id={cluster.id})")
    return cluster


def unregister_roks_cluster(db: Session, module: ProjectModule) -> bool:
    """Remove a ROKS cluster from the Kubernetes page when its source module is destroyed.

    Mirrors ``unregister_eks_cluster``. Looks up the cluster by the
    ``source_module_id`` JSON field on `meta_data` so destroying one
    blueprint module doesn't accidentally drop a hand-registered cluster
    that happens to share the name.
    """
    cluster = db.query(KubernetesCluster).filter(
        KubernetesCluster.project_id == module.project_id,
        KubernetesCluster.meta_data.op('->>')('source_module_id') == str(module.id),
    ).first()

    if cluster:
        cluster_name = cluster.name
        db.delete(cluster)
        db.commit()
        logger.info(f"Unregistered ROKS cluster {cluster_name} (id={cluster.id})")
        return True

    logger.info(f"No registered ROKS cluster found for module {module.id}")
    return False


# ---------------------------------------------------------------------------
# Bearer-token refresh (used by engine_router.resolve_kubeconfig)
# ---------------------------------------------------------------------------

def fetch_iam_bearer_token(api_key: str) -> str | None:
    """Exchange an IBM Cloud API key for a short-lived IAM bearer token.

    Returns None if the exchange fails (network error, bad key). Never
    raises — the caller is in the runner kubeconfig path and should fall
    back to whatever token is currently embedded in the kubeconfig if we
    can't refresh.
    """
    try:
        from services.ibm_cloud_service import IBMCloudService

        # IBMCloudService._exchange_api_key is an instance method but
        # doesn't actually use self.db. Pass None.
        svc = IBMCloudService.__new__(IBMCloudService)
        svc.db = None  # type: ignore[assignment]
        return svc._exchange_api_key(api_key)
    except Exception as e:
        logger.warning(f"Failed to refresh IBM IAM bearer token: {e}")
        return None


def refresh_kubeconfig_iam_token(kubeconfig_yaml: str, api_key: str) -> str | None:
    """Re-derive a ROKS kubeconfig by replacing its embedded IAM bearer token.

    Exchanges *api_key* for a fresh IAM token and rewrites every
    ``users[*].user`` entry to ``{"token": <fresh token>}`` — the same
    rewrite the runner path (`engine_router._inject_ibm_bearer_token`)
    applies transiently, factored out here so the on-demand cluster
    kubeconfig refresh shares one implementation.

    Returns the updated kubeconfig YAML, or None if the IAM exchange
    failed or the kubeconfig has no users block to rewrite. Never raises —
    callers fall back to the existing (possibly expired) kubeconfig.
    """
    token = fetch_iam_bearer_token(api_key)
    if not token:
        return None
    try:
        cfg = yaml.safe_load(kubeconfig_yaml)
        if not isinstance(cfg, dict):
            return None
        users = cfg.get("users") or []
        if not users:
            return None
        # Only rewrite users that ALREADY authenticate with a token. A cert-based
        # kubeconfig (IBM ROKS / OpenShift admin certs) has no token to refresh,
        # and injecting one would clobber the client cert/key and break auth (ROKS
        # rejects IAM bearer tokens with 401). Leave such kubeconfigs untouched.
        rewrote = 0
        for user_entry in users:
            if not isinstance(user_entry, dict):
                continue
            inner = user_entry.get("user")
            if isinstance(inner, dict) and "token" in inner:
                inner["token"] = token
                rewrote += 1
        if rewrote == 0:
            return None
        return yaml.dump(cfg, default_flow_style=False)
    except Exception as e:
        logger.warning(f"Failed to rewrite IBM kubeconfig token: {e}")
        return None
