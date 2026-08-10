"""
Proxy Deploy Service — Helm-based deployment of reverse proxies.

Phase 4c: Deploys envoy, nginx, haproxy, or f5-bnk proxies to a K8s
cluster via Helm, using the target's LLM endpoint as the upstream.

Each proxy type has a values template that wires:
  proxy listen port  →  target LLM endpoint (base_url + llm_endpoint)

The service is called from Celery tasks for async deployment.
"""

import hashlib
import ipaddress
import json
import logging
import os
import re
import subprocess
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import yaml
from sqlalchemy.orm import Session

from core.errors import BadRequestError, NotFoundError, ReleaseNotFoundError
from models.benchmark import BenchmarkTarget, ProxyDeployment
from models.enums import ProxyDeploymentStatus
from services.cluster_utils import kubeconfig_for_cluster
from services.entity_lock import EntityLock, set_locked_entity_fields
from services.helm_service import HelmService
from utils.security import validate_cli_arg

logger = logging.getLogger(__name__)

# K8s label values cap at 63 chars.  The envoy gateway-helm chart (our
# longest-suffixed default) appends `-gateway-helm-certgen` (21 chars) to
# the release name when it generates the cert-gen Job's pod labels.
# 63 - 21 = 42 is the safe ceiling; nginx/haproxy/f5-bnk have shorter
# suffixes so this also works for them.
MAX_RELEASE_NAME_LEN = 42


def _safe_release_name(proxy_type: str, target_name: str) -> str:
    """Build a Helm release name short enough for chart-derived k8s labels.

    Auto-discovered targets routinely have names like
    `vllm-agg-router-frontend-dynamo-system` (38 chars) which combined with
    the `perf-{proxy_type}-` prefix and the envoy chart's certgen suffix
    overflow the 63-char k8s label limit.

    Truncate deterministically and append a 6-char hash so different
    long names don't collide and re-deploys of the same target stay idempotent.
    """
    candidate = f"perf-{proxy_type}-{target_name}"
    if len(candidate) <= MAX_RELEASE_NAME_LEN:
        return candidate
    digest = hashlib.sha256(candidate.encode()).hexdigest()[:6]
    prefix = candidate[: MAX_RELEASE_NAME_LEN - 7].rstrip("-")
    return f"{prefix}-{digest}"

# ---------------------------------------------------------------------------
# Default Helm chart references per proxy type
# Verified against official docs as of 2026-03-13.
# ---------------------------------------------------------------------------

DEFAULT_CHARTS: dict[str, dict[str, str]] = {
    "envoy": {
        "chart": "oci://docker.io/envoyproxy/gateway-helm",
        "version": "v1.7.1",
    },
    "nginx": {
        "chart": "ingress-nginx/ingress-nginx",
        "version": "4.15.0",
    },
    "haproxy": {
        "chart": "haproxytech/haproxy",
        "version": "1.28.0",
    },
    # F5 BNK uses Gateway API CRDs — no single chart to deploy.
    "f5-bnk": {
        "chart": "",
        "version": "",
    },
    # Envoy AI Gateway — primary release is the AI Gateway controller. The base
    # Envoy Gateway + AI Gateway CRD releases are deployed first (see AUX_CHARTS).
    "envoy-ai-gateway": {
        "chart": "oci://docker.io/envoyproxy/ai-gateway-helm",
        "version": "v0.6.0",
    },
}

# ---------------------------------------------------------------------------
# Auxiliary chart / version pins for multi-step proxy installs.
# Verified against:
#   ~/go/src/ai-gateway @ v0.6.0 — site/versioned_docs/version-0.6/_vars.json
#     (aigwVersion=0.6.0, egVersion=1.7.0), manifests/charts/{ai-gateway-crds-helm,
#     ai-gateway-helm}, manifests/envoy-gateway-values.yaml,
#     examples/inference-pool/{envoy-gateway-values-addon.yaml,base.yaml}.
#   ~/go/src/gateway-api-inference-extension — config/charts/inferencepool/values.yaml,
#     config/charts/epplib/templates/_config.yaml (prefix-cache-scorer default).
# ---------------------------------------------------------------------------

# Envoy Gateway base chart (shared dependency of both AI-Gateway and GAIE flows).
ENVOY_GATEWAY_CHART = "oci://docker.io/envoyproxy/gateway-helm"
ENVOY_GATEWAY_VERSION = "v1.7.0"
ENVOY_GATEWAY_NAMESPACE = "envoy-gateway-system"
ENVOY_GATEWAY_RELEASE = "eg"

# Envoy AI Gateway CRDs + controller.
AI_GATEWAY_CRDS_CHART = "oci://docker.io/envoyproxy/ai-gateway-crds-helm"
AI_GATEWAY_CRDS_VERSION = "v0.6.0"
AI_GATEWAY_CRDS_RELEASE = "aieg-crd"
AI_GATEWAY_CONTROLLER_CHART = "oci://docker.io/envoyproxy/ai-gateway-helm"
# Controller chart version — pinned INDEPENDENTLY of the CRDs version so the two
# can't silently diverge. Defaults to the same v0.6.0 line as the CRDs but is its
# own env-overridable constant; never silently falls back to AI_GATEWAY_CRDS_VERSION.
AI_GATEWAY_CONTROLLER_VERSION = os.environ.get("AI_GATEWAY_CONTROLLER_VERSION", "v0.6.0")
AI_GATEWAY_NAMESPACE = "envoy-ai-gateway-system"
AI_GATEWAY_CONTROLLER_DEPLOYMENT = "ai-gateway-controller"
# The controller is a CLUSTER-WIDE SINGLETON, not per-target. Fixed release name
# so repeat deploys reuse (and never collide with) the one control plane. Matches
# the conventional upstream release name.
AI_GATEWAY_CONTROLLER_RELEASE = "aieg"

# GIE (Gateway API Inference Extension) CRD release manifest. Applied via kubectl
# so the per-target InferencePool/InferenceObjective CRs validate.
# v1.0.1 matches the EPP image referenced in the ai-gateway inference-pool example
# (examples/inference-pool/base.yaml: registry.k8s.io/.../epp:v1.0.1).
# Pinned to v1.5.0 to match what F5 BNK's f5-epp (built against GIE v1.5.0) installs
# on the shared cluster — applying an OLDER version with --force-conflicts would
# downgrade the cluster-scoped InferencePool CRD and crashloop f5-epp. Env-overridable.
GIE_CRD_VERSION = os.environ.get("GIE_CRD_VERSION", "v1.5.0")

# NETWORK / AIR-GAP DEPENDENCY: the default source is a live, version-pinned
# (not digest-pinned) GitHub release URL fetched server-side at deploy time. This
# hard-fails in air-gapped clusters and can fight operator-managed CRDs when
# re-applied with --force-conflicts. Override GIE_CRD_MANIFEST_SOURCE with either
# a different URL or a local file path (anything kubectl apply -f accepts) to
# point at a mirror / vendored copy and remove the egress dependency.
GIE_CRD_MANIFEST_SOURCE = os.environ.get(
    "GIE_CRD_MANIFEST_SOURCE",
    "https://github.com/kubernetes-sigs/gateway-api-inference-extension"
    f"/releases/download/{GIE_CRD_VERSION}/manifests.yaml",
)
# Backwards-compatible alias; prefer GIE_CRD_MANIFEST_SOURCE.
GIE_CRD_MANIFEST_URL = GIE_CRD_MANIFEST_SOURCE

# GAIE Endpoint Picker (EPP) — the per-target data plane behind the AI Gateway.
# Image pinned to the same v1.0.1 line as the GIE CRDs (ai-gateway inference-pool
# example: examples/inference-pool/base.yaml).
GIE_EPP_IMAGE = "registry.k8s.io/gateway-api-inference-extension/epp:v1.0.1"
EPP_GRPC_PORT = 9002
EPP_HEALTH_PORT = 9003

# ---------------------------------------------------------------------------
# llm-d-router — the llm-d inference scheduler with PRECISE (KV-event-driven)
# prefix-cache-aware routing. Mirrors the llm-d precise-prefix-cache-aware guide
# (~/go/src/llm-d/guides/precise-prefix-cache-aware/gaie-kv-events/values.yaml).
#
# Data plane: the GAIE InferencePool helm chart's EPP, but running the llm-d
# inference-scheduler image (precise KV-cache awareness) with a UDS tokenizer
# sidecar, fronted by AGENTGATEWAY (_build_agentgateway_manifest) — NOT Envoy AI
# Gateway. llm-d-router shares zero gateway infra with envoy-ai-gateway: it installs
# its own agentgateway control plane (see AGENTGATEWAY_* below) and routes via the
# `agentgateway` GatewayClass directly to the InferencePool backend.
#
# PREREQUISITES for true end-to-end precise routing (documented, not enforced —
# we don't control the benchmark target's vLLM):
#   1. The target's vLLM pods must publish KV-cache events over ZMQ
#      (vLLM `--kv-events-config`). The EPP subscribes per-pod (pod discovery);
#      the publish port is auto-discovered from the target pods, defaulting to
#      LLM_D_ROUTER_DEFAULT_KV_EVENTS_PORT when not found.
#   2. An HF token secret (LLM_D_ROUTER_HF_TOKEN_SECRET, key HF_TOKEN) must exist
#      in the target's llm_namespace so the tokenizer can fetch the model tokenizer.
#      The deploy AUTO-CREATES this secret EMPTY if absent (_ensure_hf_token_secret) —
#      enough for public models like Qwen. For a gated model, pre-create the secret
#      with a real token and the deploy will reuse it (never overwritten).
# Without (1) the scheduler still routes (degrading toward load-aware); precise
# scoring activates once KV-events flow.
# ---------------------------------------------------------------------------
LLM_D_ROUTER_CHART = "oci://registry.k8s.io/gateway-api-inference-extension/charts/inferencepool"
LLM_D_ROUTER_VERSION = os.environ.get("LLM_D_ROUTER_VERSION", "v1.4.0")
# EPP image — the llm-d inference-scheduler (supports precise KV-cache awareness),
# pinned independently and env-overridable.
LLM_D_ROUTER_EPP_IMAGE_HUB = "ghcr.io/llm-d"
LLM_D_ROUTER_EPP_IMAGE_NAME = "llm-d-inference-scheduler"
LLM_D_ROUTER_EPP_IMAGE_TAG = os.environ.get("LLM_D_ROUTER_EPP_IMAGE_TAG", "v0.7.1")
# UDS tokenizer sidecar — pre-tokenizes prompts for the precise prefix-cache scorer.
LLM_D_ROUTER_TOKENIZER_IMAGE = os.environ.get(
    "LLM_D_ROUTER_TOKENIZER_IMAGE", "ghcr.io/llm-d/llm-d-uds-tokenizer:v0.7.1"
)
# HF token secret the EPP/tokenizer needs to fetch the model tokenizer. Must exist
# in the target's llm_namespace with key HF_TOKEN (prerequisite, not created here).
LLM_D_ROUTER_HF_TOKEN_SECRET = os.environ.get("LLM_D_ROUTER_HF_TOKEN_SECRET", "llm-d-hf-token")
# Default base ZMQ port vLLM publishes KV-cache events on — used only when
# discovery from the target's model-server pods finds nothing.
LLM_D_ROUTER_DEFAULT_KV_EVENTS_PORT = int(
    os.environ.get("LLM_D_ROUTER_KV_EVENTS_PORT", "5557")
)
# KV-cache block size the prefix-cache token processor hashes on; matches vLLM's
# `--block-size=64` in the llm-d guide so cache-block boundaries line up.
LLM_D_ROUTER_KV_BLOCK_SIZE = 64

# ---------------------------------------------------------------------------
# agentgateway — the inference gateway for llm-d-router (NOT Envoy AI Gateway).
# Verified against ~/rust/src/agentgateway: GatewayClass "agentgateway"
# (controllerName agentgateway.dev/agentgateway), control plane = agentgateway-crds +
# agentgateway charts with the GIE inference extension enabled.
#
# The data-plane Gateway Service defaults to LoadBalancer (controller/pkg/helm/
# agentgateway/templates/service.yaml); on LB-less clusters that stays pending and the
# Gateway never gets an address. We override it to ClusterIP via AgentgatewayParameters
# (spec.service.spec.type) referenced from Gateway.spec.infrastructure.parametersRef —
# the controller then reports the ClusterIP as the Gateway address.
#
# IMPORTANT (BNK coexistence): we deliberately do NOT apply core Gateway API CRDs. They
# are cluster-scoped and shared with F5 BNK, which is pinned to the installed version;
# agentgateway works against the existing Gateway API v1 (parametersRef + HTTPRoute
# timeouts are present in the installed schema). Only the isolated `agentgateway.dev`
# CRD group is installed.
# ---------------------------------------------------------------------------
AGENTGATEWAY_CRDS_CHART = "oci://cr.agentgateway.dev/charts/agentgateway-crds"
AGENTGATEWAY_CHART = "oci://cr.agentgateway.dev/charts/agentgateway"
AGENTGATEWAY_VERSION = os.environ.get("AGENTGATEWAY_VERSION", "v1.2.1")
AGENTGATEWAY_NAMESPACE = "agentgateway-system"
AGENTGATEWAY_CRDS_RELEASE = "agentgateway-crds"
AGENTGATEWAY_RELEASE = "agentgateway"
AGENTGATEWAY_CLASS_NAME = "agentgateway"
AGENTGATEWAY_PARAMS_GROUP = "agentgateway.dev"
AGENTGATEWAY_PARAMS_KIND = "AgentgatewayParameters"
AGENTGATEWAY_PARAMS_API_VERSION = "agentgateway.dev/v1alpha1"

# Proxy types whose install needs multiple ordered Helm/kubectl steps.
_MULTI_STEP_PROXY_TYPES = frozenset({"envoy-ai-gateway", "llm-d-router"})

# Default proxy listen port inside the cluster (NodePort will be different)
PROXY_LISTEN_PORT = 10080


class ProxyDeployService:
    """Deploy / undeploy reverse-proxy Helm releases for benchmark targets."""

    def __init__(self, db: Session):
        self.db = db
        self.helm = HelmService(db)

    # ------------------------------------------------------------------
    # Public API (called by Celery tasks)
    # ------------------------------------------------------------------

    def deploy(
        self,
        proxy_id: int,
        *,
        lock: EntityLock | None = None,
        on_status: Any = None,
    ) -> ProxyDeployment:
        """Deploy a proxy via Helm install/upgrade.

        Args:
            proxy_id: ProxyDeployment row ID.
            lock: EntityLock held by the Celery task entrypoint. When provided
                all status writes go through set_locked_entity_fields (fence-
                protected). When None (legacy callers / tests without a lock)
                writes fall back to plain ORM + commit.
            on_status: Optional callback ``(msg: str) -> None`` for streaming output.

        Returns:
            Updated ProxyDeployment row.

        Raises:
            NotFoundError: If proxy_id doesn't exist.
            BadRequestError: If target has no cluster_id.
            RuntimeError: If Helm install fails.
        """
        deploy = self._get_deploy(proxy_id)
        target = self._get_target(deploy.target_id)
        cluster = self._get_cluster(target.cluster_id)

        self._emit(on_status, f"Starting deploy: {deploy.proxy_type} → target '{target.name}'")

        # Mark deploying
        self._write(deploy, lock, status=ProxyDeploymentStatus.DEPLOYING, status_message="Helm install in progress")

        # Multi-step proxy types (base + CRDs + controller / EPP) own their own
        # orchestration; the single-install path below doesn't fit them.
        if deploy.proxy_type in _MULTI_STEP_PROXY_TYPES:
            return self._deploy_multi_step(deploy, target, cluster, lock, on_status)

        try:
            values = self._build_values(deploy, target)
            chart = deploy.helm_chart or DEFAULT_CHARTS.get(deploy.proxy_type, {}).get("chart", "")
            version = deploy.helm_version or DEFAULT_CHARTS.get(deploy.proxy_type, {}).get("version")
            namespace = target.proxy_namespace or "perf-proxies"
            release = deploy.helm_release or _safe_release_name(deploy.proxy_type, target.name)

            # H4: Persist the release name BEFORE install so undeploy can always
            # find and clean leaked resources (envoy also applies Gateway/HTTPRoute
            # post-install that would otherwise orphan on a mid-deploy failure).
            self._write(deploy, lock, helm_release=release)

            self._emit(on_status, f"Helm install: release={release} chart={chart} ns={namespace}")

            self.helm.install_chart(
                cluster_id=target.cluster_id,
                release_name=release,
                chart=chart,
                namespace=namespace,
                values=values,
                version=version,
                create_namespace=True,
                wait=True,
                timeout="5m",
                context=cluster.context,
            )

            # Per-proxy post-install: apply data-plane resources Helm doesn't
            # generate (envoy gateway-helm only ships the controller; we need
            # GatewayClass + Gateway + HTTPRoute to actually serve traffic).
            if deploy.proxy_type == "envoy":
                proxy_url, external_url = self._post_install_envoy(
                    deploy, target, release, namespace, on_status,
                )
            else:
                proxy_url = f"http://{release}.{namespace}:{PROXY_LISTEN_PORT}"
                external_url = None

            self._write(
                deploy,
                lock,
                proxy_url=proxy_url,
                external_url=external_url,
                helm_release=release,
                helm_values=values,
                status=ProxyDeploymentStatus.READY,
                status_message="Deployed successfully",
                deployed_at=datetime.now(UTC),
            )

            self._emit(on_status, f"Deploy complete: {proxy_url}")
            return deploy

        except ValueError as exc:
            # User-level error (chart not found, release name conflict)
            self._write(
                deploy, lock,
                status=ProxyDeploymentStatus.FAILED,
                status_message=f"Helm install error: {exc}",
            )
            self._emit(on_status, f"Deploy FAILED (user error): {exc}")
            raise
        except Exception as exc:
            self._write(
                deploy, lock,
                status=ProxyDeploymentStatus.FAILED,
                status_message=f"Helm install error: {exc}",
            )
            self._emit(on_status, f"Deploy FAILED: {exc}")
            raise

    def undeploy(
        self,
        proxy_id: int,
        *,
        lock: EntityLock | None = None,
        on_status: Any = None,
    ) -> ProxyDeployment:
        """Undeploy a proxy via Helm uninstall.

        Args:
            proxy_id: ProxyDeployment row ID.
            lock: EntityLock held by the Celery task entrypoint.
            on_status: Optional callback for streaming output.

        Returns:
            Updated ProxyDeployment row.
        """
        deploy = self._get_deploy(proxy_id)
        target = self._get_target(deploy.target_id)
        cluster = self._get_cluster(target.cluster_id)

        # Multi-step proxy types own their own teardown (aux releases + namespaces).
        if deploy.proxy_type in _MULTI_STEP_PROXY_TYPES:
            return self._undeploy_multi_step(deploy, target, cluster, lock, on_status)

        release = deploy.helm_release
        if not release:
            self._write(
                deploy, lock,
                status=ProxyDeploymentStatus.UNINSTALLED,
                status_message="No Helm release to uninstall",
            )
            return deploy

        self._emit(on_status, f"Uninstalling: release={release}")

        self._write(deploy, lock, status=ProxyDeploymentStatus.UNINSTALLING, status_message="Helm uninstall in progress")

        try:
            namespace = target.proxy_namespace or "perf-proxies"

            # Tear down envoy data-plane resources before helm uninstall so
            # the controller can drain in-flight reconciles cleanly.
            if deploy.proxy_type == "envoy":
                self._pre_uninstall_envoy(
                    deploy, target, release, namespace, on_status,
                )

            self.helm.uninstall_release(
                cluster_id=target.cluster_id,
                release_name=release,
                namespace=namespace,
                wait=True,
                timeout="5m",
                context=cluster.context,
            )

            self._write(
                deploy,
                lock,
                status=ProxyDeploymentStatus.UNINSTALLED,
                status_message="Uninstalled successfully",
                proxy_url=None,
                external_url=None,
                deployed_at=None,
            )

            self._emit(on_status, "Uninstall complete")
            return deploy

        except Exception as exc:
            self._write(
                deploy, lock,
                status=ProxyDeploymentStatus.FAILED,
                status_message=f"Helm uninstall error: {exc}",
            )
            self._emit(on_status, f"Uninstall FAILED: {exc}")
            raise

    # ------------------------------------------------------------------
    # Envoy Gateway data-plane resources (Helm chart only ships controller)
    # ------------------------------------------------------------------

    def _post_install_envoy(
        self,
        deploy: ProxyDeployment,
        target: BenchmarkTarget,
        release: str,
        namespace: str,
        on_status: Any,
    ) -> tuple[str, str | None]:
        """Apply GatewayClass + Gateway + HTTPRoute, return reachable URLs.

        Returns:
            ``(proxy_url, external_url)``:
            - ``proxy_url``: URL of the Envoy data-plane Service address,
              suitable for in-cluster benchmarking.
            - ``external_url``: same address as a full ``http://`` URL when the
              target opts into ``tags["proxy_expose"] == "internal-nlb"`` (the
              address is then the externally/jumphost-reachable internal-NLB
              hostname); ``None`` for the default ClusterIP path, which is not
              reachable outside the cluster. The benchmark resolves its
              front-end endpoint from ``external_url`` and fail-closes when it
              is null, so this must be populated for the internal-NLB case.
        """
        cluster = self._get_cluster(target.cluster_id)
        manifest = _build_envoy_dataplane_manifest(release, namespace, target)
        self._emit(on_status, f"Applying Gateway API resources for envoy release {release}")

        # Internal NLBs on AWS take 2-4 minutes to provision; use a longer timeout
        # for that path.  Keep 120s for the default ClusterIP path (materializes in
        # seconds) so non-AWS deploys are not penalised.
        target_tags = target.tags if isinstance(target.tags, dict) else {}
        if target_tags.get("proxy_expose") == "internal-nlb":
            gateway_timeout_sec = 360
        else:
            gateway_timeout_sec = 120

        with kubeconfig_for_cluster(cluster, self.db) as kubeconfig_path:
            _kubectl_apply(kubeconfig_path, manifest, context=cluster.context)
            address = _wait_for_gateway_address(
                kubeconfig_path, namespace, release, timeout_sec=gateway_timeout_sec,
                context=cluster.context,
            )

        if not address:
            raise RuntimeError(
                f"Envoy Gateway '{release}' programmed but no data-plane "
                f"address materialized within {gateway_timeout_sec}s",
            )
        proxy_url = f"http://{address}:{PROXY_LISTEN_PORT}"
        self._emit(on_status, f"Envoy data-plane reachable at {proxy_url}")

        # external_url is the externally/jumphost-reachable front-end the
        # benchmark drives load through. Only the internal-NLB path produces a
        # reachable address (the NLB hostname); the default ClusterIP path is
        # in-cluster only, so it stays None and the benchmark fail-closes.
        if target_tags.get("proxy_expose") == "internal-nlb":
            external_url: str | None = f"http://{address}:{PROXY_LISTEN_PORT}"
        else:
            external_url = None
        return proxy_url, external_url

    def _pre_uninstall_envoy(
        self,
        deploy: ProxyDeployment,
        target: BenchmarkTarget,
        release: str,
        namespace: str,
        on_status: Any,
    ) -> None:
        """Delete Gateway API resources we created for an envoy deploy.

        GatewayClass `eg` is shared across envoy deploys — leave it alone.
        """
        cluster = self._get_cluster(target.cluster_id)
        self._emit(on_status, f"Deleting Gateway API resources for envoy release {release}")
        with kubeconfig_for_cluster(cluster, self.db) as kubeconfig_path:
            _kubectl_delete(
                kubeconfig_path,
                f"httproute/{release}",
                _route_namespace(target),
                ignore_missing=True,
                context=cluster.context,
            )
            _kubectl_delete(
                kubeconfig_path,
                f"gateway/{release}",
                namespace,
                ignore_missing=True,
                context=cluster.context,
            )
            _kubectl_delete(
                kubeconfig_path,
                f"envoyproxy.gateway.envoyproxy.io/{release}",
                namespace,
                ignore_missing=True,
                context=cluster.context,
            )

    # ------------------------------------------------------------------
    # Multi-step proxy installs (envoy-ai-gateway)
    # ------------------------------------------------------------------

    def _deploy_multi_step(
        self,
        deploy: ProxyDeployment,
        target: BenchmarkTarget,
        cluster: Any,
        lock: EntityLock | None,
        on_status: Any,
    ) -> ProxyDeployment:
        """Deploy a proxy that needs base + CRDs + controller/EPP steps.

        All steps use ``helm upgrade --install`` semantics (idempotent) and the
        kubectl applies are server-side (also idempotent). The single
        ProxyDeployment row owns the PRIMARY release in ``helm_release``; the
        auxiliary releases (shared Envoy Gateway, AI Gateway CRDs) live in their
        own namespaces and are reversed in ``_undeploy_multi_step``.
        """
        context = cluster.context
        namespace = target.proxy_namespace or "perf-proxies"
        release = deploy.helm_release or _safe_release_name(deploy.proxy_type, target.name)

        # H4: Persist the release name BEFORE any install step. A multi-step
        # deploy applies per-target Gateway/HTTPRoute + InferencePool/EPP + RBAC
        # (incl. cluster-scoped *-epp-auth ClusterRole/ClusterRoleBinding) that
        # _undeploy_multi_step keys off ``helm_release``. If a later step fails
        # (e.g. _wait_for_gateway_address timeout) and the row still had
        # helm_release=None, undeploy would be a no-op and those resources would
        # leak. Persisting up front keeps undeploy able to reach them; every
        # apply is idempotent so a re-deploy stays safe.
        self._write(deploy, lock, helm_release=release)

        try:
            if deploy.proxy_type == "envoy-ai-gateway":
                proxy_url = self._deploy_envoy_ai_gateway(
                    deploy, target, cluster, release, namespace, context, on_status,
                )
                applied_values = self._build_values(deploy, target)
            elif deploy.proxy_type == "llm-d-router":
                # llm-d-router re-derives values at install time (discovered KV-events
                # port + pod selector), so the per-type method returns the dict it
                # actually applied for the stored snapshot.
                proxy_url, applied_values = self._deploy_llm_d_router(
                    deploy, target, cluster, release, namespace, context, on_status,
                )
            else:  # pragma: no cover — guarded by _MULTI_STEP_PROXY_TYPES
                raise BadRequestError(f"Unknown multi-step proxy type: {deploy.proxy_type}")

            self._write(
                deploy,
                lock,
                proxy_url=proxy_url,
                helm_release=release,
                helm_values=applied_values,
                status=ProxyDeploymentStatus.READY,
                status_message="Deployed successfully",
                deployed_at=datetime.now(UTC),
            )
            self._emit(on_status, f"Deploy complete: {proxy_url}")
            return deploy

        except ValueError as exc:
            self._write(
                deploy, lock,
                status=ProxyDeploymentStatus.FAILED,
                status_message=f"Helm install error: {exc}",
            )
            self._emit(on_status, f"Deploy FAILED (user error): {exc}")
            raise
        except Exception as exc:
            self._write(
                deploy, lock,
                status=ProxyDeploymentStatus.FAILED,
                status_message=f"Helm install error: {exc}",
            )
            self._emit(on_status, f"Deploy FAILED: {exc}")
            raise

    def _release_exists(self, cluster_id: int, release: str, namespace: str, context: str | None) -> bool:
        """True if a helm release already exists (used to skip singleton control-plane installs).

        Distinguishes a genuine "release absent" (ReleaseNotFoundError) from a
        transient backend failure (API-server unreachable, auth expiry, helm
        timeout). A transient error must NOT be reported as "absent" — that would
        let _ensure_singleton_release re-run ``helm upgrade --install`` over a
        shared control plane someone else owns. Only not-found → False; any other
        error propagates so the deploy fails loudly instead of clobbering.
        """
        try:
            self.helm.get_release(cluster_id, release, namespace, context=context)
            return True
        except ReleaseNotFoundError:
            return False

    def _ensure_singleton_release(
        self,
        *,
        cluster_id: int,
        release: str,
        chart: str,
        namespace: str,
        values: dict,
        version: str,
        context: str | None,
        on_status: Any,
        label: str,
    ) -> None:
        """Install a cluster-wide singleton helm release only if it does not exist.

        Reusing (rather than re-installing) an existing control plane avoids helm
        ownership collisions with a release the user/operator already created, and
        preserves whatever configuration that control plane was installed with.
        """
        if self._release_exists(cluster_id, release, namespace, context):
            self._emit(on_status, f"{label}: '{release}' already present — reusing")
            return
        self._emit(on_status, f"{label}: installing '{release}' ({version})")
        self.helm.install_chart(
            cluster_id=cluster_id,
            release_name=release,
            chart=chart,
            namespace=namespace,
            values=values,
            version=version,
            create_namespace=True,
            wait=True,
            timeout="5m",
            context=context,
        )

    def _deploy_envoy_ai_gateway(
        self,
        deploy: ProxyDeployment,
        target: BenchmarkTarget,
        cluster: Any,
        release: str,
        namespace: str,
        context: str | None,
        on_status: Any,
    ) -> str:
        """Install Envoy AI Gateway control plane + per-target prefix-cache data plane.

        Control plane (cluster-wide singletons, installed only if absent):
          1. Envoy Gateway base (with AI Gateway extension-manager wiring) — shared
             release ``eg`` in ``envoy-gateway-system``.
          2. AI Gateway CRDs.
          3. AI Gateway controller.

        Per-target data plane (one set per benchmark target):
          4. GIE CRDs (kubectl) so the InferencePool/InferenceObjective CRs validate.
          5. InferencePool + EPP (approximate prefix-cache scorer) selecting the
             target's vLLM pods.
          6. Gateway + HTTPRoute backing the InferencePool. Wait for the Gateway's
             data-plane address, then return its reachable URL.
        """
        # The control plane (Envoy Gateway base + AI Gateway CRDs + controller) is
        # a cluster-wide singleton. Install each component only if absent so repeat
        # deploys (and clusters where the operator pre-installed it) reuse the
        # existing control plane instead of colliding with its owned resources.
        self._ensure_singleton_release(
            cluster_id=target.cluster_id, release=ENVOY_GATEWAY_RELEASE,
            chart=ENVOY_GATEWAY_CHART, namespace=ENVOY_GATEWAY_NAMESPACE,
            values=_envoy_gateway_base_values(), version=ENVOY_GATEWAY_VERSION,
            context=context, on_status=on_status, label="[1/6] Envoy Gateway base",
        )
        self._ensure_singleton_release(
            cluster_id=target.cluster_id, release=AI_GATEWAY_CRDS_RELEASE,
            chart=AI_GATEWAY_CRDS_CHART, namespace=AI_GATEWAY_NAMESPACE,
            values={}, version=AI_GATEWAY_CRDS_VERSION,
            context=context, on_status=on_status, label="[2/6] AI Gateway CRDs",
        )
        self._ensure_singleton_release(
            cluster_id=target.cluster_id, release=AI_GATEWAY_CONTROLLER_RELEASE,
            chart=deploy.helm_chart or AI_GATEWAY_CONTROLLER_CHART,
            namespace=AI_GATEWAY_NAMESPACE, values=self._build_values(deploy, target),
            version=deploy.helm_version or AI_GATEWAY_CONTROLLER_VERSION,
            context=context, on_status=on_status, label="[3/6] AI Gateway controller",
        )

        # Per-target data plane: GIE CRDs + InferencePool/EPP + Gateway/HTTPRoute.
        pool_namespace = target.llm_namespace or "default"
        gw_namespace = target.proxy_namespace or "perf-proxies"
        backend_label = _svc_name(target.llm_base_url)
        backend_port = _svc_port(target.llm_base_url)
        epp_manifest = _build_inference_epp_manifest(
            release, pool_namespace, backend_label, backend_port,
        )
        gateway_manifest = _build_gaie_gateway_manifest(release, gw_namespace, pool_namespace)

        with kubeconfig_for_cluster(cluster, self.db) as kubeconfig_path:
            # Apply GIE CRDs ONLY IF ABSENT and never force-overwrite — the CRD is
            # cluster-scoped and shared with F5 BNK's f5-epp (pinned to its own GIE
            # version). Re-applying/force could downgrade it and crashloop f5-epp.
            if _gie_crds_present(kubeconfig_path, context=context):
                self._emit(on_status, "[4/6] GIE InferencePool CRD already present — reusing (not touching shared CRD)")
            else:
                self._emit(on_status, f"[4/6] Applying GIE CRDs ({GIE_CRD_VERSION})")
                _kubectl_apply_url(kubeconfig_path, GIE_CRD_MANIFEST_URL, context=context, force_conflicts=False)

            self._emit(on_status, f"[5/6] Applying InferencePool + EPP (release={release})")
            _kubectl_apply(kubeconfig_path, epp_manifest, context=context)

            self._emit(on_status, f"[6/6] Applying Gateway + HTTPRoute for {release}")
            _kubectl_apply(kubeconfig_path, gateway_manifest, context=context)

            address = _wait_for_gateway_address(
                kubeconfig_path, gw_namespace, release, timeout_sec=120, context=context,
            )

        if not address:
            raise RuntimeError(
                f"AI Gateway '{release}' programmed but no data-plane "
                f"address materialized within 120s",
            )
        proxy_url = f"http://{address}:{PROXY_LISTEN_PORT}"
        self._emit(on_status, f"AI Gateway data-plane reachable at {proxy_url}")
        return proxy_url

    def _deploy_llm_d_router(
        self,
        deploy: ProxyDeployment,
        target: BenchmarkTarget,
        cluster: Any,
        release: str,
        namespace: str,
        context: str | None,
        on_status: Any,
    ) -> tuple[str, dict]:
        """Install the llm-d inference scheduler (precise prefix-cache) for a target.

        Uses **agentgateway** as the inference gateway (not Envoy AI Gateway), so it
        shares ZERO gateway infra with envoy-ai-gateway.

        Control plane (cluster-wide singletons, installed only if absent):
          1. agentgateway CRDs + controller (``inferenceExtension.enabled=true``) in
             ``agentgateway-system``. Provides the ``agentgateway`` GatewayClass and the
             InferencePool reconciler. The ``agentgateway.dev`` CRD group is isolated —
             installing it never touches the cluster-scoped core Gateway API / GIE CRDs
             that F5 BNK shares (BNK is pinned to the installed versions). We rely on
             whatever Gateway API version is already on the cluster.

        Per-target data plane:
          2. GIE InferencePool CRDs — applied ONLY IF ABSENT (never bumped, to protect
             BNK's shared CRD). HF-token secret ensured (empty if absent).
          3. Discover the target's pod selector, the ZMQ port its vLLM pods publish
             KV-cache events on, and its HF model id (vLLM ``--model``), then
             helm-install the GAIE InferencePool chart with the llm-d inference-scheduler
             image, UDS tokenizer sidecar, and a precise-prefix-cache-scorer wired to
             that discovered port. The chart emits the InferencePool + EPP + RBAC.
          4. agentgateway Gateway + HTTPRoute backing the InferencePool, with an
             AgentgatewayParameters forcing a ClusterIP data-plane Service (the default
             LoadBalancer stays pending on LB-less clusters). Wait for the Gateway
             address, return its URL.

        Returns:
            (proxy_url, applied_values) — the values dict actually applied (with the
            discovered KV-events port) so the caller can persist an accurate snapshot.
        """
        # 1. agentgateway control plane (cluster-wide singletons): its own CRDs +
        #    the controller with the GIE inference extension enabled. Both installed
        #    only if absent so repeat/other-target deploys reuse them.
        self._ensure_singleton_release(
            cluster_id=target.cluster_id, release=AGENTGATEWAY_CRDS_RELEASE,
            chart=AGENTGATEWAY_CRDS_CHART, namespace=AGENTGATEWAY_NAMESPACE,
            values={}, version=AGENTGATEWAY_VERSION,
            context=context, on_status=on_status, label="[1/4] agentgateway CRDs",
        )
        self._ensure_singleton_release(
            cluster_id=target.cluster_id, release=AGENTGATEWAY_RELEASE,
            chart=AGENTGATEWAY_CHART, namespace=AGENTGATEWAY_NAMESPACE,
            values={"inferenceExtension": {"enabled": True}}, version=AGENTGATEWAY_VERSION,
            context=context, on_status=on_status, label="[1/4] agentgateway controller",
        )

        pool_namespace = target.llm_namespace or "default"
        gw_namespace = target.proxy_namespace or "perf-proxies"
        target_port = _svc_port(target.llm_base_url)

        # 2-3. Discover the target's pod selector, KV-events ZMQ port, and HF model id.
        match_labels, kv_port, hf_model = self._discover_target_routing(
            cluster, target, pool_namespace, on_status,
        )
        if kv_port is None:
            kv_port = LLM_D_ROUTER_DEFAULT_KV_EVENTS_PORT
            self._emit(
                on_status,
                f"[llm-d-router] No KV-events port found on target pods — defaulting "
                f"to {kv_port}. Precise routing needs the target vLLM to publish "
                f"KV-cache events (vLLM --kv-events-config); see prerequisites.",
            )
        else:
            self._emit(on_status, f"[llm-d-router] Discovered KV-events ZMQ port {kv_port}")

        # The tokenizer needs a real HF repo id. Prefer the vLLM pod's --model arg
        # (e.g. Qwen/Qwen3-32B) over the target's llm_model, which is usually the
        # served-model-name (e.g. qwen3-32b) and would fail tokenizer init.
        model_name = hf_model or target.llm_model
        if hf_model:
            self._emit(on_status, f"[llm-d-router] Discovered HF model id '{hf_model}' from vLLM --model")
        else:
            self._emit(
                on_status,
                f"[llm-d-router] No --model arg found on target pods; using llm_model "
                f"'{target.llm_model}' for the tokenizer (must be an HF-resolvable repo id).",
            )

        values = _build_llm_d_router_values(
            model_name=model_name,
            target_port=target_port,
            match_labels=match_labels,
            kv_events_port=kv_port,
        )
        gateway_manifest = _build_agentgateway_manifest(release, gw_namespace, pool_namespace)

        with kubeconfig_for_cluster(cluster, self.db) as kubeconfig_path:
            # GIE InferencePool CRD: apply ONLY IF ABSENT. The CRD is cluster-scoped and
            # shared with F5 BNK (pinned to the installed version) — re-applying could
            # mutate it, so we never touch it when it's already present.
            if _gie_crds_present(kubeconfig_path, context=context):
                self._emit(on_status, "[2/4] GIE InferencePool CRD already present — reusing (not bumping shared CRD)")
            else:
                self._emit(on_status, f"[2/4] Applying GIE CRDs ({GIE_CRD_VERSION})")
                _kubectl_apply_url(kubeconfig_path, GIE_CRD_MANIFEST_URL, context=context, force_conflicts=False)

            # Ensure the EPP's HF-token secret exists BEFORE the chart install — a
            # MISSING secret makes the EPP container fail with CreateContainerConfigError
            # and the helm --wait then hangs to timeout. We create it empty (sufficient
            # for public model tokenizers) but never overwrite an existing one, so a
            # real token pre-created for a gated model is preserved.
            created = _ensure_hf_token_secret(
                kubeconfig_path, pool_namespace, LLM_D_ROUTER_HF_TOKEN_SECRET, context=context,
            )
            self._emit(
                on_status,
                f"[llm-d-router] HF token secret '{LLM_D_ROUTER_HF_TOKEN_SECRET}' "
                + ("created (empty)" if created else "already present — reusing"),
            )

        # 4. Install the InferencePool chart (EPP + InferencePool + RBAC) as the
        #    per-target release, into the model-server namespace so the pool selector
        #    resolves locally and the HTTPRoute's InferencePool backendRef is in-ns.
        self._emit(
            on_status,
            f"[3/4] Installing inferencepool chart (precise): release={release} "
            f"ns={pool_namespace}",
        )
        self.helm.install_chart(
            cluster_id=target.cluster_id,
            release_name=release,
            chart=deploy.helm_chart or LLM_D_ROUTER_CHART,
            namespace=pool_namespace,
            values=values,
            version=deploy.helm_version or LLM_D_ROUTER_VERSION,
            create_namespace=True,
            wait=True,
            timeout="5m",
            context=context,
        )

        with kubeconfig_for_cluster(cluster, self.db) as kubeconfig_path:
            self._emit(on_status, f"[4/4] Applying Gateway + HTTPRoute for {release}")
            _kubectl_apply(kubeconfig_path, gateway_manifest, context=context)
            address = _wait_for_gateway_address(
                kubeconfig_path, gw_namespace, release, timeout_sec=120, context=context,
            )

        if not address:
            raise RuntimeError(
                f"llm-d-router Gateway '{release}' programmed but no data-plane "
                f"address materialized within 120s",
            )
        proxy_url = f"http://{address}:{PROXY_LISTEN_PORT}"
        self._emit(on_status, f"llm-d-router data-plane reachable at {proxy_url}")
        return proxy_url, values

    def _discover_target_routing(
        self,
        cluster: Any,
        target: BenchmarkTarget,
        pool_namespace: str,
        on_status: Any,
    ) -> tuple[dict[str, str], int | None, str | None]:
        """Discover the target's pod selector, KV-events ZMQ port, and HF model id.

        All three are read live from the cluster so the deploy needs no manual config:

          * pod selector ← the target Service's ``spec.selector`` (falls back to
            ``{"app": <svc-name>}`` if the Service has no selector / isn't found).
          * KV-events port ← the vLLM container's ``kv-events`` containerPort, else
            the port embedded in its ``--kv-events-config`` endpoint (None if absent).
          * HF model id ← the vLLM container's ``--model`` arg (the real HuggingFace
            repo id, e.g. ``Qwen/Qwen3-32B``). The target's ``llm_model`` is often the
            vLLM *served-model-name* (e.g. ``qwen3-32b``), which is NOT a valid HF repo
            and breaks the tokenizer — so we prefer the discovered ``--model`` value.
            None if not found (caller falls back to ``llm_model``).

        Returns:
            (match_labels, kv_events_port, hf_model_id).
        """
        fallback_labels = {"app": _svc_name(target.llm_base_url)}
        svc_name = _svc_name(target.llm_base_url)
        try:
            with kubeconfig_for_cluster(cluster, self.db) as kubeconfig_path:
                ctx = getattr(cluster, "context", None)
                svc = _kubectl_get_json(
                    kubeconfig_path, ["-n", pool_namespace, "get", "service", svc_name],
                    context=ctx,
                )
                selector = ((svc or {}).get("spec") or {}).get("selector") or {}
                match_labels = {str(k): str(v) for k, v in selector.items()} or fallback_labels

                label_arg = ",".join(f"{k}={v}" for k, v in match_labels.items())
                pods = _kubectl_get_json(
                    kubeconfig_path,
                    ["-n", pool_namespace, "get", "pods", "-l", label_arg],
                    context=ctx,
                )
                kv_port = _kv_events_port_from_pods(pods)
                hf_model = _hf_model_from_pods(pods)
        except Exception as exc:  # discovery is best-effort — never fail the deploy on it
            self._emit(on_status, f"[llm-d-router] routing discovery failed ({exc}); using fallbacks")
            return fallback_labels, None, None

        return match_labels, kv_port, hf_model

    def _undeploy_multi_step(
        self,
        deploy: ProxyDeployment,
        target: BenchmarkTarget,
        cluster: Any,
        lock: EntityLock | None,
        on_status: Any,
    ) -> ProxyDeployment:
        """Reverse a multi-step install: per-target data plane only.

        Shared infrastructure (Envoy Gateway base release ``eg``, AI Gateway CRDs
        ``aieg-crd``, AI Gateway controller ``aieg``, GatewayClass ``eg``, GIE CRDs)
        is a cluster-wide singleton and is NEVER uninstalled here — other deploys
        depend on it.
        """
        context = cluster.context
        release = deploy.helm_release
        self._emit(on_status, f"Uninstalling multi-step proxy: release={release}")
        self._write(
            deploy, lock,
            status=ProxyDeploymentStatus.UNINSTALLING,
            status_message="Helm uninstall in progress",
        )

        try:
            if deploy.proxy_type == "envoy-ai-gateway" and release:
                # Delete only the per-target data plane (Gateway/HTTPRoute +
                # InferencePool/EPP and its RBAC). The control plane singletons
                # (eg / aieg-crd / aieg) are preserved.
                pool_namespace = target.llm_namespace or "default"
                gw_namespace = target.proxy_namespace or "perf-proxies"
                with kubeconfig_for_cluster(cluster, self.db) as kubeconfig_path:
                    # Namespaced resources.
                    for resource, ns in (
                        (f"clienttrafficpolicy/{release}-long-streams", gw_namespace),
                        (f"backendtrafficpolicy/{release}-long-streams-backend", pool_namespace),
                        (f"httproute/{release}", pool_namespace),
                        (f"gateway/{release}", gw_namespace),
                        (f"envoyproxy.gateway.envoyproxy.io/{release}", gw_namespace),
                        (f"inferencepool.inference.networking.k8s.io/{release}", pool_namespace),
                        (f"inferenceobjective/{release}", pool_namespace),
                        (f"deployment/{release}-epp", pool_namespace),
                        (f"service/{release}-epp", pool_namespace),
                        (f"serviceaccount/{release}-epp", pool_namespace),
                        (f"configmap/{release}-epp-config", pool_namespace),
                        (f"role/{release}-epp-pod-read", pool_namespace),
                        (f"rolebinding/{release}-epp-pod-read", pool_namespace),
                    ):
                        _kubectl_delete(
                            kubeconfig_path, resource, ns,
                            ignore_missing=True, context=context,
                        )
                    # Cluster-scoped resources (no namespace; release-prefixed names).
                    for resource in (
                        f"clusterrole/{release}-epp-auth",
                        f"clusterrolebinding/{release}-epp-auth",
                    ):
                        _kubectl_delete_cluster_scoped(
                            kubeconfig_path, resource,
                            ignore_missing=True, context=context,
                        )

            elif deploy.proxy_type == "llm-d-router" and release:
                # Delete only the per-target agentgateway data plane we applied via
                # kubectl (HTTPRoute + Gateway + AgentgatewayParameters). The EPP +
                # InferencePool + RBAC are owned by the helm release (inferencepool
                # chart) and removed by helm uninstall below. The shared agentgateway
                # control plane (agentgateway-crds / agentgateway) + GatewayClass + GIE
                # CRDs are cluster-wide singletons and preserved. The HF-token secret is
                # also left in place — it's empty/credential-bearing and may be shared by
                # other workloads in the namespace; we never created data worth deleting.
                pool_namespace = target.llm_namespace or "default"
                gw_namespace = target.proxy_namespace or "perf-proxies"
                with kubeconfig_for_cluster(cluster, self.db) as kubeconfig_path:
                    for resource, ns in (
                        (f"httproute/{release}", pool_namespace),
                        (f"gateway/{release}", gw_namespace),
                        (f"agentgatewayparameters.{AGENTGATEWAY_PARAMS_GROUP}/{release}", gw_namespace),
                    ):
                        _kubectl_delete(
                            kubeconfig_path, resource, ns,
                            ignore_missing=True, context=context,
                        )
                self.helm.uninstall_release(
                    cluster_id=target.cluster_id,
                    release_name=release,
                    namespace=pool_namespace,
                    wait=True,
                    timeout="5m",
                    context=context,
                )

            self._write(
                deploy, lock,
                status=ProxyDeploymentStatus.UNINSTALLED,
                status_message="Uninstalled successfully",
                proxy_url=None,
                external_url=None,
                deployed_at=None,
            )
            self._emit(on_status, "Uninstall complete")
            return deploy

        except Exception as exc:
            self._write(
                deploy, lock,
                status=ProxyDeploymentStatus.FAILED,
                status_message=f"Helm uninstall error: {exc}",
            )
            self._emit(on_status, f"Uninstall FAILED: {exc}")
            raise

    def _write(
        self,
        deploy: ProxyDeployment,
        lock: EntityLock | None,
        **fields,
    ) -> None:
        """Route a status/field write through the lock fence if held, else plain ORM.

        When lock is provided, uses set_locked_entity_fields so the write
        is rejected if the fence token no longer matches (another worker
        reclaimed the lock). When lock is None (legacy callers, unit tests
        without a lock), falls back to plain ORM attribute set + commit.
        """
        if lock is not None:
            # Expire ORM attributes we're about to overwrite so the fence-
            # protected UPDATE doesn't race against a pending ORM flush.
            db = self.db
            db.expire(deploy, list(fields.keys()))
            set_locked_entity_fields(db, deploy, lock, table="proxy_deployments", **fields)
        else:
            for k, v in fields.items():
                setattr(deploy, k, v)
            self.db.commit()

    def _get_cluster(self, cluster_id: int):
        from models import KubernetesCluster
        cluster = self.db.query(KubernetesCluster).filter(
            KubernetesCluster.id == cluster_id,
        ).first()
        if not cluster:
            raise NotFoundError("kubernetes_cluster", cluster_id)
        return cluster

    # ------------------------------------------------------------------
    # Values generation — one method per proxy type
    # ------------------------------------------------------------------

    def _build_values(self, deploy: ProxyDeployment, target: BenchmarkTarget) -> dict:
        """Build Helm values dict for the given proxy type.

        Merges the per-type template with any user-supplied ``helm_values``
        overrides stored on the ProxyDeployment row.
        """
        builder = {
            "envoy": self._values_envoy,
            "nginx": self._values_nginx,
            "haproxy": self._values_haproxy,
            "f5-bnk": self._values_f5_bnk,
            "envoy-ai-gateway": self._values_envoy_ai_gateway,
            "llm-d-router": self._values_llm_d_router,
        }.get(deploy.proxy_type)

        if not builder:
            raise BadRequestError(f"Unknown proxy type: {deploy.proxy_type}")

        base = builder(deploy, target)

        # Merge user overrides (shallow — user values win)
        if deploy.helm_values:
            base = _deep_merge(base, deploy.helm_values)

        return base

    def _values_envoy(self, deploy: ProxyDeployment, target: BenchmarkTarget) -> dict:
        """Envoy Gateway Helm values.

        The upstream ``envoyproxy/gateway-helm`` chart ONLY deploys the
        controller — it has no values keys for ``Gateway`` or ``HTTPRoute``
        resources.  Those are applied separately in ``_post_install_envoy()``
        as raw Gateway API CRDs after Helm install completes.
        """
        return {}

    def _values_nginx(self, deploy: ProxyDeployment, target: BenchmarkTarget) -> dict:
        """NGINX Ingress Controller Helm values.

        Configures a TCP proxy on ``PROXY_LISTEN_PORT`` forwarding to
        the target LLM service.
        """
        upstream = f"{_route_namespace(target)}/{_backend_svc_name(target)}:{_backend_svc_port(target)}"
        return {
            "controller": {
                "service": {
                    "type": "NodePort",
                },
                "config": {
                    "proxy-connect-timeout": "60",
                    "proxy-read-timeout": "120",
                    "proxy-send-timeout": "120",
                },
            },
            "tcp": {
                str(PROXY_LISTEN_PORT): upstream,
            },
        }

    def _values_haproxy(self, deploy: ProxyDeployment, target: BenchmarkTarget) -> dict:
        """HAProxy Helm values.

        Configures a frontend/backend pair proxying to the LLM service.
        """
        backend_server = f"{_backend_svc_name(target)}.{_route_namespace(target)}.svc.cluster.local"
        backend_port = _backend_svc_port(target)
        return {
            "service": {
                "type": "NodePort",
            },
            "config": (
                f"frontend llm_proxy\n"
                f"  bind *:{PROXY_LISTEN_PORT}\n"
                f"  default_backend llm_backend\n"
                f"\n"
                f"backend llm_backend\n"
                f"  server llm1 {backend_server}:{backend_port} check\n"
            ),
        }

    def _values_f5_bnk(self, deploy: ProxyDeployment, target: BenchmarkTarget) -> dict:
        """F5 BNK Controller Helm values.

        Configures the BIG-IP Next Kubernetes controller to route
        to the target LLM service.
        """
        return {
            "args": {
                "bigip_url": target.llm_base_url,
                "bigip_partition": "perf-proxies",
            },
            "service": {
                "type": "NodePort",
            },
            "namespace": target.proxy_namespace or "perf-proxies",
        }

    def _values_envoy_ai_gateway(self, deploy: ProxyDeployment, target: BenchmarkTarget) -> dict:
        """Envoy AI Gateway controller Helm values.

        The ``ai-gateway-helm`` chart deploys the controller; routing to the
        target's LLM endpoint is expressed through AIServiceBackend/AIGatewayRoute
        CRs (applied out of band per the inference-pool example), not chart values.
        The controller chart is installed with defaults here; callers can override
        anything via the row's ``helm_values``.
        """
        return {}

    def _values_llm_d_router(self, deploy: ProxyDeployment, target: BenchmarkTarget) -> dict:
        """InferencePool chart values for the precise (KV-events) llm-d scheduler.

        This is the SNAPSHOT form used by the generic ``_build_values`` path; the
        real deploy (``_deploy_llm_d_router``) re-derives the KV-events port and pod
        selector live from the cluster and applies those instead. Here we use the
        default port and a best-effort ``{"app": <svc>}`` selector.
        """
        return _build_llm_d_router_values(
            model_name=target.llm_model,
            target_port=_svc_port(target.llm_base_url),
            match_labels={"app": _svc_name(target.llm_base_url)},
            kv_events_port=LLM_D_ROUTER_DEFAULT_KV_EVENTS_PORT,
        )

    # ------------------------------------------------------------------
    # BNK manifest apply/delete — thin wrappers for ProxyMigrationService
    # ------------------------------------------------------------------

    def apply_bnk_manifest(
        self,
        cluster_id: int,
        combined_yaml: str,
        gw_name: str,
        gw_ns: str,
        *,
        on_status: Any = None,
        timeout_sec: int = 120,
    ) -> str | None:
        """Apply a combined BNK GatewayClass+Gateway+HTTPRoute YAML to the cluster.

        Reuses _kubectl_apply and _wait_for_gateway_address.  Returns the
        Gateway address (IP or hostname) once the data-plane is ready, or None
        if no address materialises within timeout_sec.

        Args:
            cluster_id: KubernetesCluster row ID.
            combined_yaml: Multi-doc YAML (``'\\n---\\n'.join(...)`` form).
            gw_name: Name of the Gateway resource (for polling its status).
            gw_ns: Namespace of the Gateway resource.
            on_status: Optional callback ``(msg: str) -> None``.
            timeout_sec: Seconds to wait for gateway address.

        Returns:
            Gateway address string, or None.
        """
        cluster = self._get_cluster(cluster_id)
        self._emit(on_status, f"Applying BNK manifest: gateway={gw_name} ns={gw_ns}")
        with kubeconfig_for_cluster(cluster, self.db) as kubeconfig_path:
            _kubectl_apply(kubeconfig_path, combined_yaml)
            self._emit(on_status, "kubectl apply succeeded — waiting for gateway address")
            address = _wait_for_gateway_address(
                kubeconfig_path, gw_ns, gw_name, timeout_sec=timeout_sec,
            )
        if address:
            self._emit(on_status, f"Gateway {gw_name} ready at {address}")
        else:
            self._emit(on_status, f"Gateway {gw_name} applied but no address within {timeout_sec}s")
        return address

    def delete_bnk_manifest(
        self,
        cluster_id: int,
        gw_name: str,
        gw_ns: str,
        route_name: str | None = None,
        route_ns: str | None = None,
        *,
        on_status: Any = None,
    ) -> None:
        """Delete a BNK Gateway (and optionally HTTPRoute) we applied.

        Reuses _kubectl_delete with ignore_missing=True so idempotent
        re-runs are safe.

        Args:
            cluster_id: KubernetesCluster row ID.
            gw_name: Name of the Gateway to delete.
            gw_ns: Namespace of the Gateway.
            route_name: Name of the HTTPRoute to delete (may differ from gw_name).
            route_ns: Namespace of the HTTPRoute.
            on_status: Optional status callback.
        """
        cluster = self._get_cluster(cluster_id)
        r_name = route_name or gw_name
        r_ns = route_ns or gw_ns
        self._emit(on_status, f"Deleting BNK manifest: gateway={gw_name} ns={gw_ns}")
        with kubeconfig_for_cluster(cluster, self.db) as kubeconfig_path:
            _kubectl_delete(kubeconfig_path, f"httproute/{r_name}", r_ns, ignore_missing=True)
            _kubectl_delete(kubeconfig_path, f"gateway/{gw_name}", gw_ns, ignore_missing=True)
        self._emit(on_status, f"BNK manifest for gateway {gw_name} deleted")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_deploy(self, proxy_id: int) -> ProxyDeployment:
        deploy = self.db.query(ProxyDeployment).filter(ProxyDeployment.id == proxy_id).first()
        if not deploy:
            raise NotFoundError("proxy_deployment", proxy_id)
        return deploy

    def _get_target(self, target_id: int) -> BenchmarkTarget:
        target = self.db.query(BenchmarkTarget).filter(BenchmarkTarget.id == target_id).first()
        if not target:
            raise NotFoundError("benchmark_target", target_id)
        if not target.cluster_id:
            raise BadRequestError("Target has no cluster_id — cannot deploy proxy")
        return target

    @staticmethod
    def _emit(callback: Any, msg: str) -> None:
        if callback:
            try:
                callback(msg)
            except Exception:
                pass
        logger.info(msg)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _svc_name(url: str) -> str:
    """Extract the K8s service name from a URL like ``http://svc-name.ns:8000``."""
    # Strip scheme
    host = url.split("//", 1)[-1]
    # Strip port
    host = host.split(":")[0]
    # Strip namespace suffix (if present as hostname.namespace)
    return host.split(".")[0]


def _svc_port(url: str) -> int:
    """Extract port from URL, defaulting to 8000 for LLM services."""
    try:
        host = url.split("//", 1)[-1]
        if ":" in host:
            return int(host.split(":")[-1].split("/")[0])
    except (ValueError, IndexError):
        pass
    return 8000


def _backend_svc_name(target: "BenchmarkTarget") -> str:  # type: ignore[name-defined]
    """Resolve the in-cluster upstream Service name for a proxy backend.

    Priority order:
    1. ``target.tags["upstream_service"]`` — explicit override (required when
       ``llm_base_url`` is a VIP IP such as the BNK VIP used by aiperf).
    2. Parse ``_svc_name(target.llm_base_url)`` — works when the URL host is a
       real K8s DNS name.  If the host is an IP literal, raises ``BadRequestError``
       rather than silently producing a bogus service name like ``"10"``.

    The ``isinstance`` guard is LOAD-BEARING: existing tests use bare ``MagicMock()``
    targets whose ``.tags`` attribute is itself a mock; without the guard,
    ``target.tags.get(...)`` returns a truthy child mock and every legacy test
    would flip to the opt-in branch.
    """
    tags = target.tags if isinstance(target.tags, dict) else {}
    override = tags.get("upstream_service")
    if override:
        return str(override)
    # No override — parse from llm_base_url but reject IP literals.
    # urlparse strips IPv6 brackets (e.g. "http://[::1]:8000" → "::1") so the
    # ipaddress guard works correctly; naive split(":")  would yield "[" for v6.
    # But urlparse().hostname is None for a scheme-less URL (e.g. bare
    # "10.0.10.108:8000" with no "//"), which would silently bypass the guard.
    # Retry with a synthetic "//" prefix so urlparse treats it as netloc —
    # this still strips IPv6 brackets correctly (confirmed: urlparse("//[::1]:8000").hostname == "::1").
    raw_host = urlparse(target.llm_base_url).hostname
    if not raw_host:
        raw_host = urlparse(f"//{target.llm_base_url}").hostname or ""
    try:
        ipaddress.ip_address(raw_host)
        raise BadRequestError(
            f"target '{target.name}' llm_base_url host '{raw_host}' is an IP literal; "
            f"set tags['upstream_service'] to the in-cluster vLLM Service name "
            f"(the proxy backendRef needs a Service name, not the VIP IP)"
        )
    except ValueError:
        pass  # Not an IP — treat as a DNS hostname; proceed with name parse.
    return _svc_name(target.llm_base_url)


def _backend_svc_port(target: "BenchmarkTarget") -> int:  # type: ignore[name-defined]
    """Resolve the upstream Service port for a proxy backend.

    Uses ``target.tags["upstream_port"]`` (int) when present, falling back to
    ``_svc_port(target.llm_base_url)`` otherwise.
    """
    tags = target.tags if isinstance(target.tags, dict) else {}
    override = tags.get("upstream_port")
    if override is not None:
        # Reject bool (int(True) == 1 would silently produce a bogus port) and
        # any non-numeric/free-form JSON value (list, dict, "grpc", ...) with a
        # clean BadRequestError instead of an uncaught ValueError/TypeError,
        # mirroring the IP-literal guard in _backend_svc_name.
        if isinstance(override, bool) or not isinstance(override, (int, str)):
            raise BadRequestError(
                f"target '{target.name}' tags['upstream_port'] value {override!r} "
                f"is not a valid port; set it to an integer or numeric string"
            )
        try:
            return int(override)
        except ValueError:
            raise BadRequestError(
                f"target '{target.name}' tags['upstream_port'] value {override!r} "
                f"is not a valid port; set it to an integer or numeric string"
            )
    return _svc_port(target.llm_base_url)


# ---------------------------------------------------------------------------
# Envoy Gateway API resource helpers
# ---------------------------------------------------------------------------

ENVOY_GATEWAY_CLASS_NAME = "eg"
ENVOY_GATEWAY_CONTROLLER = "gateway.envoyproxy.io/gatewayclass-controller"

# AWS LB Controller annotations that configure an internal NLB for the envoy data-plane Service.
# Applied to the EnvoyProxy CR (spec.provider.kubernetes.envoyService.annotations) when the
# target opts in via tags["proxy_expose"] == "internal-nlb".  Subnet auto-discovery works
# because awsbnkctl Slice-1 tags the data-path subnet with kubernetes.io/role/internal-elb.
AWS_INTERNAL_NLB_ANNOTATIONS: dict[str, str] = {
    "service.beta.kubernetes.io/aws-load-balancer-scheme": "internal",
    "service.beta.kubernetes.io/aws-load-balancer-nlb-target-type": "ip",
    "service.beta.kubernetes.io/aws-load-balancer-type": "external",
}


def _envoy_proxy_clusterip_doc(release: str, namespace: str) -> dict:
    """Backward-compatible shim — delegates to ``_envoy_proxy_doc`` with default intent.

    Retained so any callers outside ``_build_envoy_dataplane_manifest`` (e.g.
    the GAIE manifest builder) continue to work without modification.
    """
    return _envoy_proxy_doc(release, namespace, expose_intent=None)


def _envoy_proxy_doc(release: str, namespace: str, expose_intent: str | None) -> dict:
    """EnvoyProxy that controls the data-plane Envoy Service type.

    When ``expose_intent == "internal-nlb"``:
      - ``envoyService.type`` is set to ``LoadBalancer``
      - ``envoyService.annotations`` carries the AWS LB Controller annotations that
        request an internal NLB (``AWS_INTERNAL_NLB_ANNOTATIONS``).  Confirmed
        propagating in Envoy Gateway v1.7.1 via ``KubernetesServiceSpec.Annotations``
        + controller ``Service()`` ``maps.Copy`` — no post-apply kubectl patch needed.

    Otherwise (default / kind / OCI / bare-metal):
      - ``envoyService.type`` is ``ClusterIP`` — byte-identical to the prior behaviour.
      - No ``annotations`` key is emitted (avoids surprising empty-dict diffs on redeploy).

    On bare-metal clusters with no LoadBalancer provider, the default LB-type
    Envoy Service stays <pending> forever, so the Gateway never gets an address
    (``Programmed=False``, ``AddressNotAssigned``). A ClusterIP service resolves
    immediately — the Gateway reports its ClusterIP as the address, reachable for
    in-cluster benchmarking. Mirrors the working ``eg-proxy-config`` EnvoyProxy
    pattern on the target clusters.
    """
    if expose_intent == "internal-nlb":
        envoy_service: dict = {
            "type": "LoadBalancer",
            "annotations": dict(AWS_INTERNAL_NLB_ANNOTATIONS),
        }
    else:
        envoy_service = {"type": "ClusterIP"}

    return {
        "apiVersion": "gateway.envoyproxy.io/v1alpha1",
        "kind": "EnvoyProxy",
        "metadata": {
            "name": release,
            "namespace": namespace,
            "labels": {"app.kubernetes.io/managed-by": "bnk-forge"},
        },
        "spec": {
            "provider": {
                "type": "Kubernetes",
                "kubernetes": {"envoyService": envoy_service},
            },
        },
    }


def _gateway_infrastructure_ref(release: str) -> dict:
    """Gateway.spec.infrastructure that binds the Gateway to its ClusterIP EnvoyProxy."""
    return {
        "parametersRef": {
            "group": "gateway.envoyproxy.io",
            "kind": "EnvoyProxy",
            "name": release,
        },
    }


def _route_namespace(target: BenchmarkTarget) -> str:
    """Resolve the namespace the envoy HTTPRoute (and its backendRef) lives in.

    Priority order:
    1. ``target.tags["upstream_namespace"]`` — explicit override (required when
       ``llm_namespace`` is forge's ``"default"`` placeholder but the real vLLM
       Service lives elsewhere, e.g. ``awsbnkctl-scn-aiinference``). Putting the
       route in the wrong namespace makes its namespace-LESS backendRef resolve
       to ``default/<svc>`` → ``ResolvedRefs=BackendNotFound`` → Envoy 500.
    2. ``target.llm_namespace`` — the benchmark client's namespace.
    3. ``"default"`` — last-resort fallback.

    The ``isinstance`` guard is LOAD-BEARING: existing tests use bare
    ``MagicMock()`` targets whose ``.tags`` attribute is itself a mock; without
    the guard, ``target.tags.get(...)`` returns a truthy child mock and every
    legacy test would flip to the opt-in branch.

    Used by both ``_build_envoy_dataplane_manifest`` (create) and
    ``_pre_uninstall_envoy`` (delete) so the route is torn down from the same
    namespace it was created in.
    """
    tags = target.tags if isinstance(target.tags, dict) else {}
    return tags.get("upstream_namespace") or target.llm_namespace or "default"


def _build_envoy_dataplane_manifest(
    release: str, gateway_namespace: str, target: BenchmarkTarget,
) -> str:
    """Render GatewayClass + Gateway + HTTPRoute as a single multi-doc YAML.

    GatewayClass is cluster-scoped and shared across envoy deploys.  Gateway
    lives in the proxy namespace.  HTTPRoute lives in the route namespace
    resolved by ``_route_namespace`` — ``tags["upstream_namespace"]`` when
    provided, else the target's ``llm_namespace`` — so its namespace-LESS
    backendRef is local and avoids needing a ReferenceGrant.
    """
    route_namespace = _route_namespace(target)
    backend_name = _backend_svc_name(target)
    backend_port = _backend_svc_port(target)

    tags = target.tags if isinstance(target.tags, dict) else {}
    expose_intent: str | None = tags.get("proxy_expose")

    docs = [
        {
            "apiVersion": "gateway.networking.k8s.io/v1",
            "kind": "GatewayClass",
            "metadata": {"name": ENVOY_GATEWAY_CLASS_NAME},
            "spec": {"controllerName": ENVOY_GATEWAY_CONTROLLER},
        },
        _envoy_proxy_doc(release, gateway_namespace, expose_intent),
        {
            "apiVersion": "gateway.networking.k8s.io/v1",
            "kind": "Gateway",
            "metadata": {
                "name": release,
                "namespace": gateway_namespace,
                "labels": {"app.kubernetes.io/managed-by": "bnk-forge"},
            },
            "spec": {
                "gatewayClassName": ENVOY_GATEWAY_CLASS_NAME,
                "infrastructure": _gateway_infrastructure_ref(release),
                "listeners": [{
                    "name": "llm",
                    "port": PROXY_LISTEN_PORT,
                    "protocol": "HTTP",
                    "allowedRoutes": {
                        "namespaces": {"from": "All"},
                    },
                }],
            },
        },
        {
            "apiVersion": "gateway.networking.k8s.io/v1",
            "kind": "HTTPRoute",
            "metadata": {
                "name": release,
                "namespace": route_namespace,
                "labels": {"app.kubernetes.io/managed-by": "bnk-forge"},
            },
            "spec": {
                "parentRefs": [{
                    "name": release,
                    "namespace": gateway_namespace,
                }],
                "rules": [{
                    "matches": [{"path": {"type": "PathPrefix", "value": "/"}}],
                    "backendRefs": [{
                        "name": backend_name,
                        "port": backend_port,
                    }],
                }],
            },
        },
    ]
    return "\n---\n".join(yaml.safe_dump(d, default_flow_style=False) for d in docs)


def _envoy_gateway_base_values() -> dict:
    """Minimal Envoy Gateway values for AI Gateway + InferencePool integration.

    Inlined from ~/go/src/ai-gateway @ v0.6.0:
      manifests/envoy-gateway-values.yaml (base AI Gateway extension-manager hooks)
      + examples/inference-pool/envoy-gateway-values-addon.yaml (InferencePool
        backendResources). We merge both so a single shared ``eg`` release serves
        the envoy-ai-gateway control plane and its per-target InferencePool data plane.

    The extensionManager service FQDN must match the AI Gateway controller Service
    in ``envoy-ai-gateway-system``.
    """
    return {
        "config": {
            "envoyGateway": {
                "gateway": {
                    "controllerName": ENVOY_GATEWAY_CONTROLLER,
                },
                "logging": {"level": {"default": "info"}},
                "provider": {"type": "Kubernetes"},
                "extensionApis": {
                    "enableEnvoyPatchPolicy": True,
                    "enableBackend": True,
                },
                "extensionManager": {
                    "hooks": {
                        "xdsTranslator": {
                            "translation": {
                                "listener": {"includeAll": True},
                                "route": {"includeAll": True},
                                "cluster": {"includeAll": True},
                                "secret": {"includeAll": True},
                            },
                            "post": ["Translation", "Cluster", "Route"],
                        },
                    },
                    "service": {
                        "fqdn": {
                            "hostname": (
                                f"{AI_GATEWAY_CONTROLLER_DEPLOYMENT}"
                                f".{AI_GATEWAY_NAMESPACE}.svc.cluster.local"
                            ),
                            "port": 1063,
                        },
                    },
                    # InferencePool backend support (inference-pool addon).
                    "backendResources": [
                        {
                            "group": "inference.networking.k8s.io",
                            "kind": "InferencePool",
                            "version": "v1",
                        },
                    ],
                },
            },
        },
    }


def _build_inference_epp_manifest(
    release: str, pool_namespace: str, backend_label: str, backend_port: int,
) -> str:
    """Render the per-target GAIE Endpoint Picker (EPP) data plane as multi-doc YAML.

    Mirrors ~/go/src/ai-gateway/examples/inference-pool/base.yaml, parameterized by
    the benchmark target. The EndpointPickerConfig keeps all three default scorers,
    including ``prefix-cache-scorer`` — this is what enables APPROXIMATE prefix-cache
    aware routing. Every resource is labeled ``app.kubernetes.io/managed-by: bnk-forge``.

    Cluster-scoped resources (ClusterRole/ClusterRoleBinding) are prefixed with the
    release name so concurrent per-target deploys never collide on a shared name.
    """
    managed_by = {"app.kubernetes.io/managed-by": "bnk-forge"}
    epp_name = f"{release}-epp"
    config_name = f"{release}-epp-config"

    epp_config = (
        "apiVersion: inference.networking.x-k8s.io/v1alpha1\n"
        "kind: EndpointPickerConfig\n"
        "plugins:\n"
        "- type: queue-scorer\n"
        "- type: kv-cache-utilization-scorer\n"
        "- type: prefix-cache-scorer\n"
        "schedulingProfiles:\n"
        "- name: default\n"
        "  plugins:\n"
        "  - pluginRef: queue-scorer\n"
        "  - pluginRef: kv-cache-utilization-scorer\n"
        "  - pluginRef: prefix-cache-scorer\n"
    )

    docs = [
        {
            "apiVersion": "inference.networking.k8s.io/v1",
            "kind": "InferencePool",
            "metadata": {"name": release, "namespace": pool_namespace, "labels": managed_by},
            "spec": {
                "targetPorts": [{"number": backend_port}],
                "selector": {"matchLabels": {"app": backend_label}},
                "endpointPickerRef": {"name": epp_name, "port": {"number": EPP_GRPC_PORT}},
            },
        },
        {
            "apiVersion": "inference.networking.x-k8s.io/v1alpha2",
            "kind": "InferenceObjective",
            "metadata": {"name": release, "namespace": pool_namespace, "labels": managed_by},
            "spec": {"priority": 10, "poolRef": {"name": release}},
        },
        {
            "apiVersion": "v1",
            "kind": "ServiceAccount",
            "metadata": {"name": epp_name, "namespace": pool_namespace, "labels": managed_by},
        },
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": config_name, "namespace": pool_namespace, "labels": managed_by},
            "data": {"default-plugins.yaml": epp_config},
        },
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": epp_name,
                "namespace": pool_namespace,
                "labels": {**managed_by, "app": epp_name},
            },
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": {"app": epp_name}},
                "template": {
                    "metadata": {"labels": {**managed_by, "app": epp_name}},
                    "spec": {
                        "serviceAccountName": epp_name,
                        "terminationGracePeriodSeconds": 130,
                        "containers": [{
                            "name": "epp",
                            "image": GIE_EPP_IMAGE,
                            "imagePullPolicy": "IfNotPresent",
                            "args": [
                                "--pool-name", release,
                                "--pool-namespace", pool_namespace,
                                "--v", "4",
                                "--zap-encoder", "json",
                                "--grpc-port", str(EPP_GRPC_PORT),
                                "--grpc-health-port", str(EPP_HEALTH_PORT),
                                "--config-file", "/config/default-plugins.yaml",
                            ],
                            "ports": [
                                {"containerPort": EPP_GRPC_PORT, "name": "grpc"},
                                {"containerPort": EPP_HEALTH_PORT, "name": "grpc-health"},
                                {"containerPort": 9090, "name": "metrics"},
                            ],
                            "livenessProbe": {
                                "grpc": {"port": EPP_HEALTH_PORT, "service": "inference-extension"},
                                "initialDelaySeconds": 5,
                                "periodSeconds": 10,
                            },
                            "readinessProbe": {
                                "grpc": {"port": EPP_HEALTH_PORT, "service": "inference-extension"},
                                "initialDelaySeconds": 5,
                                "periodSeconds": 10,
                            },
                            "volumeMounts": [
                                {"name": "plugins-config-volume", "mountPath": "/config"},
                            ],
                        }],
                        "volumes": [{
                            "name": "plugins-config-volume",
                            "configMap": {"name": config_name},
                        }],
                    },
                },
            },
        },
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": epp_name, "namespace": pool_namespace, "labels": managed_by},
            "spec": {
                "selector": {"app": epp_name},
                "type": "ClusterIP",
                "ports": [{
                    "name": "grpc",
                    "protocol": "TCP",
                    "port": EPP_GRPC_PORT,
                    "targetPort": EPP_GRPC_PORT,
                    "appProtocol": "http2",
                }],
            },
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "Role",
            "metadata": {
                "name": f"{release}-epp-pod-read",
                "namespace": pool_namespace,
                "labels": managed_by,
            },
            "rules": [
                {
                    "apiGroups": ["inference.networking.x-k8s.io"],
                    "resources": ["inferenceobjectives", "inferencepools"],
                    "verbs": ["get", "watch", "list"],
                },
                {
                    "apiGroups": ["inference.networking.k8s.io"],
                    "resources": ["inferencepools"],
                    "verbs": ["get", "watch", "list"],
                },
                {
                    "apiGroups": [""],
                    "resources": ["pods"],
                    "verbs": ["get", "watch", "list"],
                },
            ],
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "RoleBinding",
            "metadata": {
                "name": f"{release}-epp-pod-read",
                "namespace": pool_namespace,
                "labels": managed_by,
            },
            "roleRef": {
                "apiGroup": "rbac.authorization.k8s.io",
                "kind": "Role",
                "name": f"{release}-epp-pod-read",
            },
            "subjects": [{
                "kind": "ServiceAccount",
                "name": epp_name,
                "namespace": pool_namespace,
            }],
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "ClusterRole",
            "metadata": {"name": f"{release}-epp-auth", "labels": managed_by},
            "rules": [
                {
                    "apiGroups": ["authentication.k8s.io"],
                    "resources": ["tokenreviews"],
                    "verbs": ["create"],
                },
                {
                    "apiGroups": ["authorization.k8s.io"],
                    "resources": ["subjectaccessreviews"],
                    "verbs": ["create"],
                },
            ],
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "ClusterRoleBinding",
            "metadata": {"name": f"{release}-epp-auth", "labels": managed_by},
            "roleRef": {
                "apiGroup": "rbac.authorization.k8s.io",
                "kind": "ClusterRole",
                "name": f"{release}-epp-auth",
            },
            "subjects": [{
                "kind": "ServiceAccount",
                "name": epp_name,
                "namespace": pool_namespace,
            }],
        },
    ]
    return "\n---\n".join(yaml.safe_dump(d, default_flow_style=False) for d in docs)


def _build_agentgateway_manifest(
    release: str, gateway_namespace: str, pool_namespace: str,
) -> str:
    """Render the agentgateway Gateway + HTTPRoute fronting a GAIE InferencePool.

    Three docs (no GatewayClass — the agentgateway chart owns the ``agentgateway``
    class; no EnvoyProxy / traffic policies — those are Envoy-specific):

      * ``AgentgatewayParameters`` forcing a ClusterIP data-plane Service. agentgateway
        defaults the Gateway Service to LoadBalancer, which stays pending (no address)
        on LB-less clusters; ClusterIP makes the controller report the ClusterIP as the
        Gateway address immediately. Referenced via the Gateway's parametersRef.
      * ``Gateway`` on GatewayClass ``agentgateway`` listening on PROXY_LISTEN_PORT.
      * ``HTTPRoute`` whose backendRef is the InferencePool (named after the release),
        with the request timeout disabled (``0s``) so long LLM generations aren't cut.
    """
    managed_by = {"app.kubernetes.io/managed-by": "bnk-forge"}
    docs = [
        {
            "apiVersion": AGENTGATEWAY_PARAMS_API_VERSION,
            "kind": AGENTGATEWAY_PARAMS_KIND,
            "metadata": {"name": release, "namespace": gateway_namespace, "labels": managed_by},
            # KubernetesResourceOverlay: strategic-merge ServiceSpec patch.
            "spec": {"service": {"spec": {"type": "ClusterIP"}}},
        },
        {
            "apiVersion": "gateway.networking.k8s.io/v1",
            "kind": "Gateway",
            "metadata": {"name": release, "namespace": gateway_namespace, "labels": managed_by},
            "spec": {
                "gatewayClassName": AGENTGATEWAY_CLASS_NAME,
                "infrastructure": {
                    "parametersRef": {
                        "group": AGENTGATEWAY_PARAMS_GROUP,
                        "kind": AGENTGATEWAY_PARAMS_KIND,
                        "name": release,
                    },
                },
                "listeners": [{
                    "name": "llm",
                    "port": PROXY_LISTEN_PORT,
                    "protocol": "HTTP",
                    "allowedRoutes": {"namespaces": {"from": "All"}},
                }],
            },
        },
        {
            "apiVersion": "gateway.networking.k8s.io/v1",
            "kind": "HTTPRoute",
            "metadata": {"name": release, "namespace": pool_namespace, "labels": managed_by},
            "spec": {
                "parentRefs": [{"name": release, "namespace": gateway_namespace}],
                "rules": [{
                    "matches": [{"path": {"type": "PathPrefix", "value": "/"}}],
                    "backendRefs": [{
                        "group": "inference.networking.k8s.io",
                        "kind": "InferencePool",
                        "name": release,
                    }],
                    # 0s disables the timeout — long generations must not be cut off.
                    "timeouts": {"request": "0s"},
                }],
            },
        },
    ]
    return "\n---\n".join(yaml.safe_dump(d, default_flow_style=False) for d in docs)


def _build_gaie_gateway_manifest(
    release: str, gateway_namespace: str, pool_namespace: str,
) -> str:
    """Render Gateway + HTTPRoute fronting a GAIE InferencePool as multi-doc YAML.

    The HTTPRoute's backendRef targets the InferencePool (group
    ``inference.networking.k8s.io``, kind ``InferencePool``) created by the
    inferencepool chart — the EPP picks the endpoint per request. The InferencePool
    is named after the Helm release (the chart names the pool after the release).
    GatewayClass ``eg`` is shared with the plain-envoy flow.
    """
    docs = [
        {
            "apiVersion": "gateway.networking.k8s.io/v1",
            "kind": "GatewayClass",
            "metadata": {"name": ENVOY_GATEWAY_CLASS_NAME},
            "spec": {"controllerName": ENVOY_GATEWAY_CONTROLLER},
        },
        _envoy_proxy_clusterip_doc(release, gateway_namespace),
        {
            "apiVersion": "gateway.networking.k8s.io/v1",
            "kind": "Gateway",
            "metadata": {
                "name": release,
                "namespace": gateway_namespace,
                "labels": {"app.kubernetes.io/managed-by": "bnk-forge"},
            },
            "spec": {
                "gatewayClassName": ENVOY_GATEWAY_CLASS_NAME,
                "infrastructure": _gateway_infrastructure_ref(release),
                "listeners": [{
                    "name": "llm",
                    "port": PROXY_LISTEN_PORT,
                    "protocol": "HTTP",
                    "allowedRoutes": {"namespaces": {"from": "All"}},
                }],
            },
        },
        {
            "apiVersion": "gateway.networking.k8s.io/v1",
            "kind": "HTTPRoute",
            "metadata": {
                "name": release,
                "namespace": pool_namespace,
                "labels": {"app.kubernetes.io/managed-by": "bnk-forge"},
            },
            "spec": {
                "parentRefs": [{
                    "name": release,
                    "namespace": gateway_namespace,
                }],
                "rules": [{
                    "matches": [{"path": {"type": "PathPrefix", "value": "/"}}],
                    "backendRefs": [{
                        # No port: an InferencePool backendRef routes via the
                        # pool's own targetPorts + EPP, not a Service port. Matches
                        # the upstream inference-pool HTTPRoute example.
                        "group": "inference.networking.k8s.io",
                        "kind": "InferencePool",
                        "name": release,
                    }],
                }],
            },
        },
        # ClientTrafficPolicy — lift Envoy's default 5-min stream_idle_timeout that
        # otherwise kills long LLM streams under load with
        # "TransferEncodingError: Not enough data to satisfy transfer length header",
        # masking real results. Targets the Gateway (client side).
        {
            "apiVersion": "gateway.envoyproxy.io/v1alpha1",
            "kind": "ClientTrafficPolicy",
            "metadata": {
                "name": f"{release}-long-streams",
                "namespace": gateway_namespace,
                "labels": {"app.kubernetes.io/managed-by": "bnk-forge"},
            },
            "spec": {
                "targetRefs": [{
                    "group": "gateway.networking.k8s.io",
                    "kind": "Gateway",
                    "name": release,
                }],
                "timeout": {"http": {"streamIdleTimeout": "30m", "idleTimeout": "1h"}},
            },
        },
        # BackendTrafficPolicy — remove Envoy's per-route request/stream-duration
        # ceilings on the upstream side so they stop capping long generations; the
        # only cap is aiperf's own --request-timeout-seconds. Targets the HTTPRoute.
        {
            "apiVersion": "gateway.envoyproxy.io/v1alpha1",
            "kind": "BackendTrafficPolicy",
            "metadata": {
                "name": f"{release}-long-streams-backend",
                "namespace": pool_namespace,
                "labels": {"app.kubernetes.io/managed-by": "bnk-forge"},
            },
            "spec": {
                "targetRefs": [{
                    "group": "gateway.networking.k8s.io",
                    "kind": "HTTPRoute",
                    "name": release,
                }],
                "timeout": {"http": {"requestTimeout": "0s", "maxStreamDuration": "0s", "connectionIdleTimeout": "1h"}},
            },
        },
    ]
    return "\n---\n".join(yaml.safe_dump(d, default_flow_style=False) for d in docs)


# ---------------------------------------------------------------------------
# llm-d-router (precise prefix-cache) values + KV-events port discovery
# ---------------------------------------------------------------------------

def _precise_prefix_cache_config(model_name: str, kv_events_port: int) -> str:
    """Render the EndpointPickerConfig for the precise prefix-cache scorer.

    Mirrors ~/go/src/llm-d/guides/precise-prefix-cache-aware/gaie-kv-events/
    values.yaml's ``precise-prefix-cache-config.yaml``, parameterized by the
    target's model (for the tokenizer) and the discovered KV-events ZMQ port.

    Uses POD DISCOVERY (``discoverPods: true`` + ``podDiscoveryConfig.socketPort``)
    so the EPP connects out to each model-server pod's published port — no
    dependency on the (already-deployed) vLLM knowing the EPP's address.
    """
    cfg = {
        "apiVersion": "inference.networking.x-k8s.io/v1alpha1",
        "kind": "EndpointPickerConfig",
        "plugins": [
            {"type": "single-profile-handler"},
            {
                "type": "tokenizer",
                "parameters": {
                    "modelName": model_name,
                    "udsTokenizerConfig": {"socketFile": "/tmp/tokenizer/tokenizer-uds.socket"},
                },
            },
            {
                "type": "precise-prefix-cache-scorer",
                "parameters": {
                    "tokenProcessorConfig": {"blockSize": LLM_D_ROUTER_KV_BLOCK_SIZE},
                    "indexerConfig": {
                        "speculativeIndexing": True,
                        "tokenizersPoolConfig": {
                            "modelName": model_name,
                            "local": None,
                            "hf": None,
                            "uds": {"socketFile": "/tmp/tokenizer/tokenizer-uds.socket"},
                        },
                    },
                    "kvEventsConfig": {
                        "topicFilter": "kv@",
                        "concurrency": 4,
                        "discoverPods": True,
                        "podDiscoveryConfig": {"socketPort": kv_events_port},
                    },
                },
            },
            {"type": "kv-cache-utilization-scorer"},
            {"type": "queue-scorer"},
            {"type": "max-score-picker"},
        ],
        "schedulingProfiles": [
            {
                "name": "default",
                "plugins": [
                    {"pluginRef": "precise-prefix-cache-scorer", "weight": 3.0},
                    {"pluginRef": "kv-cache-utilization-scorer", "weight": 2.0},
                    {"pluginRef": "queue-scorer", "weight": 2.0},
                    {"pluginRef": "max-score-picker"},
                ],
            },
        ],
    }
    return yaml.safe_dump(cfg, default_flow_style=False, sort_keys=False)


def _build_llm_d_router_values(
    *,
    model_name: str,
    target_port: int,
    match_labels: dict[str, str],
    kv_events_port: int,
) -> dict:
    """Build the GAIE InferencePool chart values for the precise llm-d scheduler.

    The InferencePool selects the target's model-server pods (``match_labels``,
    ``target_port``); the EPP runs the llm-d inference-scheduler image with a UDS
    tokenizer sidecar and the precise prefix-cache scorer wired to ``kv_events_port``.
    """
    return {
        "inferenceExtension": {
            "replicas": 1,
            "flags": {"v": 4},
            "image": {
                "name": LLM_D_ROUTER_EPP_IMAGE_NAME,
                "hub": LLM_D_ROUTER_EPP_IMAGE_HUB,
                "tag": LLM_D_ROUTER_EPP_IMAGE_TAG,
                "pullPolicy": "Always",
            },
            "extProcPort": EPP_GRPC_PORT,
            # HF token for the tokenizer pool. Secret must pre-exist in the pool ns.
            "env": [
                {
                    "name": "HF_TOKEN",
                    "valueFrom": {
                        "secretKeyRef": {"name": LLM_D_ROUTER_HF_TOKEN_SECRET, "key": "HF_TOKEN"},
                    },
                },
            ],
            # UDS tokenizer sidecar — the single epplib sidecar slot (which is why
            # the self-contained standalone proxy sidecar can't coexist with precise).
            "sidecar": {
                "enabled": True,
                "image": LLM_D_ROUTER_TOKENIZER_IMAGE,
                "imagePullPolicy": "IfNotPresent",
                "name": "tokenizer-uds",
                "configMap": {"name": "tokenizer-uds-config", "data": {"placeholder": ""}},
                "env": [
                    {"name": "TOKENIZERS_DIR", "value": "/tokenizers"},
                    {"name": "HF_HOME", "value": "/tokenizers"},
                ],
                "volumeMounts": [
                    {"mountPath": "/tokenizers", "name": "tokenizers"},
                    {"mountPath": "/tmp/tokenizer", "name": "tokenizer-uds"},
                ],
            },
            "volumes": [
                {"name": "tokenizers", "emptyDir": {}},
                {"name": "tokenizer-uds", "emptyDir": {}},
            ],
            "volumeMounts": [
                {"mountPath": "/tmp/tokenizer", "name": "tokenizer-uds"},
            ],
            "pluginsConfigFile": "precise-prefix-cache-config.yaml",
            "pluginsCustomConfig": {
                "precise-prefix-cache-config.yaml": _precise_prefix_cache_config(
                    model_name, kv_events_port,
                ),
            },
            # Benchmark deploys don't ship the infra prometheus-reader secret the
            # llm-d guide references; disable EPP prometheus auth to keep the install
            # self-contained (no missing-secret dependency).
            "monitoring": {"prometheus": {"enabled": False}},
        },
        "inferencePool": {
            "targetPorts": [{"number": target_port}],
            "modelServerType": "vllm",
            "modelServers": {"matchLabels": match_labels},
        },
    }


def _kubectl_get_json(
    kubeconfig_path: str, args: list[str], context: str | None = None,
) -> dict | None:
    """Run ``kubectl get ... -o json`` and parse it; None on any error/non-zero."""
    proc = subprocess.run(
        ["kubectl", "--kubeconfig", kubeconfig_path, *_context_args(context),
         *args, "-o", "json"],
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError):
        return None


def _container_cmdline(container: dict) -> str:
    """Join a container's command+args into one string for regex scanning.

    Model servers are often launched via ``/bin/sh -c '<one big script>'``, so the
    real flags live inside a single string element rather than as discrete tokens.
    """
    parts = (container.get("command") or []) + (container.get("args") or [])
    return " ".join(p for p in parts if isinstance(p, str))


def _port_from_kv_events_config(raw: str) -> int | None:
    """Extract the port from a vLLM ``--kv-events-config`` JSON value.

    The config's ``endpoint`` is a ZMQ URI like ``tcp://*:5557`` or
    ``tcp://host:5557``; return the trailing port, or None if unparseable.
    """
    try:
        cfg = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(cfg, dict):
        return None
    endpoint = cfg.get("endpoint") or ""
    match = re.search(r":(\d+)\s*$", str(endpoint))
    return int(match.group(1)) if match else None


def _kv_port_from_cmdline(text: str) -> int | None:
    """Extract the KV-events ZMQ port from a (possibly shell-wrapped) command line.

    Matches the PRIMARY ``endpoint`` of ``--kv-events-config`` — e.g.
    ``--kv-events-config "{...\\"endpoint\\":\\"tcp://*:20080\\",\\"replay_endpoint\\":...}"`` —
    and returns ``20080``. The ``(?<![\\w])`` guard skips ``replay_endpoint`` (its
    ``endpoint`` is preceded by ``_``). Returns None if no kv-events config present.
    """
    if "--kv-events-config" not in text:
        return None
    match = re.search(
        r'(?<![\w])endpoint\\?["\']?\s*:\s*\\?["\']?tcp://[^:\s\\"\']+:(\d+)', text,
    )
    return int(match.group(1)) if match else None


def _parse_kv_events_port(container: dict) -> int | None:
    """Find the KV-events ZMQ port a vLLM container publishes on.

    Preference order:
      1. a named containerPort (``kv-events`` / ``kv_events`` / ``zmq``),
      2. the port in a discrete ``--kv-events-config`` arg,
      3. the port in a shell-wrapped command line (``sh -c 'vllm serve ... --kv-events-config "{...}"'``).
    Returns None when none are present (KV-events not enabled on this pod).
    """
    for port in (container.get("ports") or []):
        name = str(port.get("name") or "").lower()
        if name in ("kv-events", "kv_events", "kvevents", "zmq") or (
            "kv" in name and "event" in name
        ):
            num = port.get("containerPort")
            if isinstance(num, int):
                return num

    args = container.get("args") or []
    for i, arg in enumerate(args):
        if not isinstance(arg, str):
            continue
        if arg == "--kv-events-config" and i + 1 < len(args):
            port = _port_from_kv_events_config(args[i + 1])
            if port is not None:
                return port
        if arg.startswith("--kv-events-config="):
            port = _port_from_kv_events_config(arg.split("=", 1)[1])
            if port is not None:
                return port

    # Shell-wrapped form: scan the whole command line.
    return _kv_port_from_cmdline(_container_cmdline(container))


def _kv_events_port_from_pods(pods_json: dict | None) -> int | None:
    """Scan a ``kubectl get pods -o json`` payload for a vLLM KV-events port.

    Inspects each pod's containers (preferring one named/imaged like vLLM) and
    returns the first KV-events port found, else None.
    """
    if not pods_json:
        return None
    for pod in (pods_json.get("items") or []):
        containers = ((pod.get("spec") or {}).get("containers") or [])
        # Prefer a vLLM-looking container, but fall back to scanning all.
        ordered = sorted(
            containers,
            key=lambda c: 0 if "vllm" in (str(c.get("name") or "") + str(c.get("image") or "")).lower() else 1,
        )
        for container in ordered:
            port = _parse_kv_events_port(container)
            if port is not None:
                return port
    return None


def _model_from_container(container: dict) -> str | None:
    """Extract the vLLM HF model id from a container spec.

    Handles every form seen in the wild:
      * discrete tokens ``["--model", "Qwen/Qwen3-32B"]``
      * a single shell string ``["python3 -m dynamo.vllm --model Qwen/Qwen3-32B ..."]``
      * the **positional** ``vllm serve <model>`` (model is NOT behind ``--model``),
        incl. shell-wrapped ``/bin/sh -c 'exec vllm serve Qwen/Qwen3-32B ...'``
    The ``(?:^|\\s)`` / ``[^-\\s]`` guards prevent matching ``--served-model-name`` or a
    flag where the positional model is expected.
    """
    tokens = (container.get("command") or []) + (container.get("args") or [])
    for idx, tok in enumerate(tokens):
        if not isinstance(tok, str):
            continue
        if tok == "--model" and idx + 1 < len(tokens) and isinstance(tokens[idx + 1], str):
            return tokens[idx + 1].strip().strip("'\"")
        match = re.search(r"(?:^|\s)--model[=\s]+(\S+)", tok)
        if match:
            return match.group(1).strip().strip("'\"")

    # Positional `vllm serve <model>` / `--model` inside a shell-wrapped command line.
    text = _container_cmdline(container)
    match = re.search(r"vllm\s+serve\s+([^-\s]\S*)", text)
    if match:
        return match.group(1).strip().strip("'\"")
    match = re.search(r"(?:^|\s)--model[=\s]+(\S+)", text)
    if match:
        return match.group(1).strip().strip("'\"")
    return None


def _served_model_from_container(container: dict) -> str | None:
    """Extract the vLLM ``--served-model-name`` from a container spec.

    This is the name clients put in the OpenAI ``model`` field — what benchmark
    REQUESTS must send — which can differ from ``--model`` (the HF repo the server
    loads / the tokenizer needs). Handles discrete tokens and shell-wrapped strings.
    Returns None if ``--served-model-name`` isn't set (vLLM then defaults the served
    name to ``--model``, so callers fall back to ``_model_from_container``).
    """
    tokens = (container.get("command") or []) + (container.get("args") or [])
    for idx, tok in enumerate(tokens):
        if not isinstance(tok, str):
            continue
        if tok == "--served-model-name" and idx + 1 < len(tokens) and isinstance(tokens[idx + 1], str):
            return tokens[idx + 1].strip().strip("'\"")
        match = re.search(r"(?:^|\s)--served-model-name[=\s]+(\S+)", tok)
        if match:
            return match.group(1).strip().strip("'\"")

    match = re.search(r"(?:^|\s)--served-model-name[=\s]+(\S+)", _container_cmdline(container))
    if match:
        return match.group(1).strip().strip("'\"")
    return None


def _hf_model_from_pods(pods_json: dict | None) -> str | None:
    """Scan a ``kubectl get pods -o json`` payload for the vLLM ``--model`` HF id.

    Prefers a vLLM-looking container, mirroring ``_kv_events_port_from_pods``.
    Returns None when no ``--model`` arg is present.
    """
    if not pods_json:
        return None
    for pod in (pods_json.get("items") or []):
        containers = ((pod.get("spec") or {}).get("containers") or [])
        ordered = sorted(
            containers,
            key=lambda c: 0 if "vllm" in (str(c.get("name") or "") + str(c.get("image") or "")).lower() else 1,
        )
        for container in ordered:
            model = _model_from_container(container)
            if model:
                return model
    return None


def _gie_crds_present(kubeconfig_path: str, context: str | None = None) -> bool:
    """True if the GIE InferencePool CRD is already installed on the cluster.

    Used to apply the GIE CRDs ONLY IF ABSENT — the CRD is cluster-scoped and shared
    with F5 BNK (pinned to the installed version), so we must never re-apply/bump it
    when it already exists.
    """
    proc = subprocess.run(
        ["kubectl", "--kubeconfig", kubeconfig_path, *_context_args(context),
         "get", "crd", "inferencepools.inference.networking.k8s.io"],
        capture_output=True, text=True, timeout=30,
    )
    return proc.returncode == 0


def _ensure_hf_token_secret(
    kubeconfig_path: str, namespace: str, name: str, context: str | None = None,
) -> bool:
    """Create an empty HF-token secret (key ``HF_TOKEN``) if it doesn't already exist.

    The llm-d EPP container references ``HF_TOKEN`` via ``secretKeyRef``; a *missing*
    secret causes ``CreateContainerConfigError`` so the pod never starts and the helm
    ``--wait`` hangs to timeout. Creating the secret empty is enough for public model
    tokenizers (e.g. Qwen) to resolve and makes the deploy self-contained.

    Idempotent and non-destructive: if the secret already exists it is left untouched,
    so a real token a user pre-created for a gated model is preserved.

    Returns:
        True if it created the secret, False if one already existed.
    """
    get = subprocess.run(
        ["kubectl", "--kubeconfig", kubeconfig_path, *_context_args(context),
         "-n", namespace, "get", "secret", name],
        capture_output=True, text=True, timeout=30,
    )
    if get.returncode == 0:
        return False
    create = subprocess.run(
        ["kubectl", "--kubeconfig", kubeconfig_path, *_context_args(context),
         "-n", namespace, "create", "secret", "generic", name, "--from-literal=HF_TOKEN="],
        capture_output=True, text=True, timeout=30,
    )
    # Tolerate a concurrent creator (AlreadyExists) — the goal is "exists", not "I made it".
    if create.returncode != 0 and "AlreadyExists" not in (create.stderr or ""):
        raise RuntimeError(
            f"failed to create HF token secret '{name}' in {namespace}: {create.stderr.strip()}"
        )
    return True


def _context_args(context: str | None) -> list[str]:
    """Return ``["--context", <ctx>]`` when a context is set, else ``[]``.

    Keeps ``context=None`` behavior byte-identical to the pre-context argv.
    The context is run through ``validate_cli_arg`` so a value that could be
    parsed as a flag (e.g. leading ``-``) is rejected before it reaches kubectl.
    """
    validate_cli_arg("context", context)
    return ["--context", context] if context else []


def _kubectl_apply(kubeconfig_path: str, manifest: str, context: str | None = None) -> None:
    """Apply a multi-doc YAML via `kubectl apply --server-side -f -`."""
    proc = subprocess.run(
        ["kubectl", "--kubeconfig", kubeconfig_path, *_context_args(context), "apply",
         "--server-side", "--force-conflicts", "-f", "-"],
        input=manifest,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"kubectl apply failed (rc={proc.returncode}): {proc.stderr.strip()}"
        )
    logger.info("kubectl apply succeeded:\n%s", proc.stdout.strip())


def _kubectl_apply_url(
    kubeconfig_path: str, url: str, context: str | None = None, force_conflicts: bool = True,
) -> None:
    """Apply a manifest from a remote URL (used for the pinned GIE CRD release).

    ``force_conflicts`` defaults True (server-side apply takes ownership of fields).
    Pass False for cluster-scoped, cross-tenant resources like the shared GIE CRD so
    we never overwrite a version another consumer (f5-epp) installed.
    """
    force = ["--force-conflicts"] if force_conflicts else []
    proc = subprocess.run(
        ["kubectl", "--kubeconfig", kubeconfig_path, *_context_args(context), "apply",
         "--server-side", *force, "-f", url],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"kubectl apply -f {url} failed (rc={proc.returncode}): {proc.stderr.strip()}"
        )
    logger.info("kubectl apply -f %s succeeded:\n%s", url, proc.stdout.strip())


def _kubectl_delete(
    kubeconfig_path: str,
    resource: str,
    namespace: str,
    ignore_missing: bool = False,
    context: str | None = None,
) -> None:
    """Delete a single resource by `kind/name`."""
    proc = subprocess.run(
        ["kubectl", "--kubeconfig", kubeconfig_path, *_context_args(context),
         "-n", namespace, "delete", resource,
         "--ignore-not-found" if ignore_missing else "--wait=true"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0 and not ignore_missing:
        raise RuntimeError(
            f"kubectl delete {resource} failed: {proc.stderr.strip()}"
        )
    logger.info("kubectl delete %s -n %s: %s", resource, namespace, proc.stdout.strip())


def _kubectl_delete_cluster_scoped(
    kubeconfig_path: str,
    resource: str,
    ignore_missing: bool = False,
    context: str | None = None,
) -> None:
    """Delete a cluster-scoped resource by `kind/name` (no namespace)."""
    proc = subprocess.run(
        ["kubectl", "--kubeconfig", kubeconfig_path, *_context_args(context),
         "delete", resource,
         "--ignore-not-found" if ignore_missing else "--wait=true"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0 and not ignore_missing:
        raise RuntimeError(
            f"kubectl delete {resource} failed: {proc.stderr.strip()}"
        )
    logger.info("kubectl delete %s: %s", resource, proc.stdout.strip())


def _wait_for_gateway_address(
    kubeconfig_path: str,
    namespace: str,
    name: str,
    timeout_sec: int = 120,
    poll_interval: float = 2.0,
    context: str | None = None,
) -> str | None:
    """Poll the Gateway's status until it reports a data-plane address.

    Envoy Gateway populates ``status.addresses[].value`` with the auto-created
    Service's IP once the data-plane Deployment is ready.  Returns None if no
    address materializes within the timeout.
    """
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        proc = subprocess.run(
            ["kubectl", "--kubeconfig", kubeconfig_path, *_context_args(context),
             "-n", namespace, "get", "gateway", name, "-o", "json"],
            capture_output=True, text=True, timeout=15,
        )
        if proc.returncode == 0:
            try:
                obj = json.loads(proc.stdout)
                addresses = (obj.get("status") or {}).get("addresses") or []
                if addresses and addresses[0].get("value"):
                    return addresses[0]["value"]
            except (json.JSONDecodeError, KeyError, IndexError):
                pass
        time.sleep(poll_interval)
    return None


def _deep_merge(base: dict, overrides: dict) -> dict:
    """Recursively merge *overrides* into *base*, returning a new dict."""
    result = base.copy()
    for key, val in overrides.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result
