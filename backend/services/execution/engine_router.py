"""
Engine Router — picks the right engine for each module.

Decision tree (metadata-driven dispatch):
  1. execution_engine == "kubernetes-direct" or "operator":
     a. Connected operator available for project → OperatorEngine (preferred)
     b. Kubeconfig available → KubernetesEngine (direct)
     c. Neither → error (cannot execute K8s module without connectivity)
  2. Everything else → OpenTofuEngine (default behavior)

The router resolves connectivity in priority order:
  - Operator (outbound WS from cluster) — no kubeconfig needed
  - Direct K8s (kubeconfig, EKS auth, SSH tunnel) — fallback

Resilience features (ENG-005a):
  - Health checks per engine (cached for 30s to avoid re-checking on every request)
  - Circuit breaker: 3 consecutive failures → tripped for 60s, then one probe allowed
  - Fallback: if preferred engine is tripped/unhealthy, try next compatible engine
  - Clear error messages listing all engine statuses when nothing is available
"""

import json
import logging
import os
import time
from datetime import UTC

from sqlalchemy.orm import Session

from core.encryption import decrypt_value
from models import CloudCredentialTemplate
from services.azure_oauth_service import request_azure_oauth_token
from services.engine_registry import engine_registry
from services.execution.engine_interface import DeploymentEngine
from services.kubeconfig_normalizer import NormalizationSource, normalize_kubeconfig
from services.registry import ServiceRegistry

logger = logging.getLogger(__name__)


# =============================================================================
# Module-level circuit breaker + health cache state
# =============================================================================

# Circuit breaker state per engine name
# { engine_name: { "failures": int, "tripped_at": float | None, "last_probe_at": float | None } }
_circuit_breaker_state: dict[str, dict] = {}

# Health check cache per engine name
# { engine_name: { "healthy": bool, "checked_at": float } }
_health_cache: dict[str, dict] = {}

# Circuit breaker constants
CIRCUIT_BREAKER_FAILURE_THRESHOLD = 3   # Trip after N consecutive failures
CIRCUIT_BREAKER_TRIP_DURATION = 60.0    # Seconds to stay tripped
HEALTH_CACHE_TTL = 30.0                 # Seconds to cache health check results


class EngineUnavailableError(ValueError):
    """
    Raised when all compatible engines are unavailable.

    Extends ValueError for backward compatibility with existing callers
    that catch ValueError from get_engine().
    """
    pass


# =============================================================================
# Circuit breaker functions
# =============================================================================

def get_circuit_breaker_state(engine_name: str) -> dict:
    """Get or initialize circuit breaker state for an engine."""
    if engine_name not in _circuit_breaker_state:
        _circuit_breaker_state[engine_name] = {
            "failures": 0,
            "tripped_at": None,
            "last_probe_at": None,
        }
    return _circuit_breaker_state[engine_name]


def is_circuit_tripped(engine_name: str) -> bool:
    """
    Check if an engine's circuit breaker is currently tripped.

    Returns True if the breaker was tripped and the trip duration hasn't elapsed.
    Once the trip duration expires, returns False (probe allowed).
    """
    state = get_circuit_breaker_state(engine_name)
    if state["tripped_at"] is None:
        return False

    elapsed = time.monotonic() - state["tripped_at"]
    if elapsed < CIRCUIT_BREAKER_TRIP_DURATION:
        return True

    # Trip duration expired — allow a probe
    return False


def is_probe_allowed(engine_name: str) -> bool:
    """
    After circuit trip expires, allow exactly one probe operation.

    Returns True if the trip duration has elapsed and we haven't recently probed
    (or no probe was attempted yet). The probe tests whether the engine has recovered.
    """
    state = get_circuit_breaker_state(engine_name)
    if state["tripped_at"] is None:
        return True  # Not tripped, normal operation

    elapsed = time.monotonic() - state["tripped_at"]
    if elapsed < CIRCUIT_BREAKER_TRIP_DURATION:
        return False  # Still in trip window

    # Trip expired — check if we recently probed and it failed (re-tripped)
    if state["last_probe_at"] is not None:
        probe_elapsed = time.monotonic() - state["last_probe_at"]
        if probe_elapsed < CIRCUIT_BREAKER_TRIP_DURATION:
            return False  # Recently probed and re-tripped — wait

    return True


def record_engine_success(engine_name: str) -> None:
    """Record a successful operation — resets the circuit breaker."""
    state = get_circuit_breaker_state(engine_name)
    if state["failures"] > 0 or state["tripped_at"] is not None:
        logger.info(
            f"Circuit breaker reset for engine '{engine_name}' after successful operation "
            f"(was at {state['failures']} failures)"
        )
    state["failures"] = 0
    state["tripped_at"] = None
    state["last_probe_at"] = None


def record_engine_failure(engine_name: str) -> None:
    """
    Record a failed operation. After CIRCUIT_BREAKER_FAILURE_THRESHOLD
    consecutive failures, trip the breaker for CIRCUIT_BREAKER_TRIP_DURATION.
    """
    state = get_circuit_breaker_state(engine_name)
    state["failures"] += 1

    if state["tripped_at"] is not None:
        # Already tripped — this was a probe failure, re-trip
        state["tripped_at"] = time.monotonic()
        state["last_probe_at"] = time.monotonic()
        logger.warning(
            f"Circuit breaker probe failed for engine '{engine_name}' — "
            f"re-tripped for {CIRCUIT_BREAKER_TRIP_DURATION}s"
        )
    elif state["failures"] >= CIRCUIT_BREAKER_FAILURE_THRESHOLD:
        state["tripped_at"] = time.monotonic()
        logger.warning(
            f"Circuit breaker TRIPPED for engine '{engine_name}' after "
            f"{state['failures']} consecutive failures — skipping for "
            f"{CIRCUIT_BREAKER_TRIP_DURATION}s"
        )
    else:
        logger.debug(
            f"Engine '{engine_name}' failure "
            f"#{state['failures']}/{CIRCUIT_BREAKER_FAILURE_THRESHOLD}"
        )


# =============================================================================
# Health check cache
# =============================================================================

def check_engine_health_cached(engine_name: str, engine: DeploymentEngine) -> bool:
    """
    Check engine health with 30-second TTL caching.

    Avoids hammering health checks on every request. If the cache is fresh,
    return the cached result. Otherwise, run the actual health_check().
    """
    cached = _health_cache.get(engine_name)
    if cached is not None:
        elapsed = time.monotonic() - cached["checked_at"]
        if elapsed < HEALTH_CACHE_TTL:
            return cached["healthy"]

    # Cache miss or expired — run actual health check
    try:
        healthy = engine.health_check()
    except Exception as e:
        logger.warning(f"Health check exception for engine '{engine_name}': {e}")
        healthy = False

    _health_cache[engine_name] = {
        "healthy": healthy,
        "checked_at": time.monotonic(),
    }

    if not healthy:
        logger.warning(f"Engine '{engine_name}' health check failed")

    return healthy


def invalidate_health_cache(engine_name: str | None = None) -> None:
    """
    Invalidate health cache — forces re-check on next request.

    If engine_name is None, invalidate all engines.
    """
    if engine_name is None:
        _health_cache.clear()
    elif engine_name in _health_cache:
        del _health_cache[engine_name]


def get_engine_status_summary() -> dict[str, dict]:
    """
    Get a summary of all engine health + circuit breaker states.

    Useful for diagnostics, error messages, and the /system/health endpoint.
    """
    summary = {}
    for name in set(list(_circuit_breaker_state.keys()) + list(_health_cache.keys())):
        state = get_circuit_breaker_state(name)
        cached_health = _health_cache.get(name, {})
        summary[name] = {
            "failures": state["failures"],
            "tripped": is_circuit_tripped(name),
            "healthy": cached_health.get("healthy", "unknown"),
        }
    return summary


# =============================================================================
# Engine Router
# =============================================================================

class EngineRouter:
    """Routes module execution to the correct engine with health checks and circuit breaker."""

    def __init__(
        self,
        kubeconfig_path: str | None = None,
        operator_id: str | None = None,
        connectivity_mode: str = "direct_ws",
        context: str | None = None,
    ):
        """
        Args:
            kubeconfig_path: Path to kubeconfig for direct K8s engine.
            operator_id: Connected operator ID for operator engine.
                         If both are provided, operator takes priority.
            connectivity_mode: How the operator communicates (direct_ws, polling, etc.)
            context: Optional kubectl context name for the direct K8s engine.
                When set, the KubernetesEngine targets that context without
                relying on the kubeconfig's current-context. When None,
                behavior is unchanged.
        """
        self._kubeconfig_path = kubeconfig_path
        self._operator_id = operator_id
        self._connectivity_mode = connectivity_mode
        self._context = context
        self._k8s_engine = None  # Lazy init
        self._operator_engine = None  # Lazy init

    @property
    def k8s_engine(self):
        """Lazy-init KubernetesEngine."""
        if self._k8s_engine is None and self._kubeconfig_path:
            from services.execution.kubernetes_engine import KubernetesEngine
            self._k8s_engine = KubernetesEngine(self._kubeconfig_path, context=self._context)
        return self._k8s_engine

    @property
    def operator_engine(self):
        """Lazy-init OperatorEngine."""
        if self._operator_engine is None and self._operator_id:
            from services.execution.operator_engine import OperatorEngine
            op_conns = ServiceRegistry.get().operator_connections
            # For WS mode, only create if actually connected via WebSocket
            # For polling mode, the operator may not have a WS connection
            if self._connectivity_mode == "polling" or op_conns.is_connected(self._operator_id):
                self._operator_engine = OperatorEngine(
                    self._operator_id,
                    connectivity_mode=self._connectivity_mode,
                )
        return self._operator_engine

    def get_engine_type(self, module_path: str, execution_engine: str | None = None, category: str = "") -> str:
        """
        Determine which engine should execute this module.

        Args:
            module_path: The module path (for logging).
            execution_engine: The execution engine from catalog metadata.
            category: Module category (for logging).

        Returns: "ssh", "operator", "kubernetes", or "opentofu"
        """
        # 0. SSH modules (bare-metal Python registry)
        svc = ServiceRegistry.get()
        registry = svc.module_registry
        if module_path in registry:
            module_def = registry[module_path]
            if getattr(module_def, "module_type", "") == "ssh":
                return "ssh"

        # 1. K8s-native modules (execution_engine from catalog)
        if execution_engine in {"kubernetes-direct", "operator"}:
            # Prefer operator if available
            if self._operator_id:
                op_conns = ServiceRegistry.get().operator_connections
                if (self._connectivity_mode == "polling" or
                        op_conns.is_connected(self._operator_id)):
                    return "operator"

            # Fall back to direct K8s
            if self._kubeconfig_path:
                return "kubernetes"

            logger.warning(
                f"Module {module_path} has execution_engine={execution_engine} but no operator or kubeconfig — "
                f"falling back to OpenTofu"
            )

        # Default to OpenTofu
        return "opentofu"

    def _get_compatible_engines(self, module_path: str, execution_engine: str | None = None) -> list[tuple[str, DeploymentEngine]]:
        """
        Get all compatible engines for a module in priority order.

        Args:
            module_path: The module path.
            execution_engine: The execution engine from catalog metadata.

        Returns list of (engine_name, engine_instance) tuples.
        For SSH modules: [ssh] (no fallback)
        For K8s-native modules: [operator, kubernetes] (whichever are configured)
        For infra modules: [opentofu]
        """
        engines: list[tuple[str, DeploymentEngine]] = []

        # 0. SSH modules (bare-metal Python registry) — only compatible with SSH engine
        svc = ServiceRegistry.get()
        registry = svc.module_registry
        if module_path in registry:
            module_def = registry[module_path]
            if getattr(module_def, "module_type", "") == "ssh":
                from services.execution.ssh_engine import SSHEngine
                engines.append(("ssh", SSHEngine()))
                return engines

        # 1. K8s-native modules (operator + kubernetes fallback)
        if execution_engine in {"kubernetes-direct", "operator"}:
            # K8s-native module — operator and kubernetes are compatible
            if self.operator_engine is not None:
                engines.append(("operator", self.operator_engine))
            if self.k8s_engine is not None:
                engines.append(("kubernetes", self.k8s_engine))
        else:
            # Default to OpenTofu
            from database import SessionLocal
            from services.execution.opentofu_engine import OpenTofuEngine
            engines.append(("opentofu", OpenTofuEngine(SessionLocal)))

        return engines

    def get_engine(self, module_path: str, execution_engine: str | None = None, category: str = "") -> DeploymentEngine:
        """
        Get the engine instance for a module with health check + circuit breaker.

        Selection logic:
          1. Determine preferred engine type (operator > kubernetes > opentofu)
          2. Check circuit breaker — if tripped and probe not yet allowed, skip
          3. Check health (cached 30s) — if unhealthy, skip to fallback
          4. If preferred engine unavailable, try next compatible engine
          5. If no compatible engine available, raise EngineUnavailableError

        Raises EngineUnavailableError if all compatible engines are unavailable.
        """
        engine_type = self.get_engine_type(module_path, execution_engine, category)

        # Build ordered list of candidate engines
        candidates = self._get_compatible_engines(module_path, execution_engine)

        # Sort so preferred engine type comes first
        preferred_order = engine_registry.router_priority()
        candidates.sort(key=lambda x: preferred_order.get(x[0], 99))

        # Try each candidate in order
        unavailable_reasons: dict[str, str] = {}

        for engine_name, engine in candidates:
            # Check circuit breaker
            if is_circuit_tripped(engine_name):
                state = get_circuit_breaker_state(engine_name)
                unavailable_reasons[engine_name] = (
                    f"tripped ({state['failures']} consecutive failures)"
                )
                logger.debug(
                    f"Skipping engine '{engine_name}' for {module_path} — "
                    f"circuit breaker tripped"
                )

                # Check if probe is allowed (trip duration expired)
                if is_probe_allowed(engine_name):
                    logger.info(
                        f"Circuit breaker trip expired for '{engine_name}' — "
                        f"allowing probe operation"
                    )
                    state["last_probe_at"] = time.monotonic()
                    return engine
                continue

            # Check health (cached 30s)
            if not check_engine_health_cached(engine_name, engine):
                unavailable_reasons[engine_name] = "health check failed"
                if engine_name == engine_type:
                    logger.warning(
                        f"Primary engine '{engine_name}' unhealthy for {module_path} — "
                        f"trying fallback"
                    )
                continue

            # Engine is healthy and circuit is not tripped — use it
            if engine_name != engine_type:
                logger.warning(
                    f"Falling back from '{engine_type}' to '{engine_name}' for "
                    f"{module_path} (primary unavailable: "
                    f"{unavailable_reasons.get(engine_type, 'unknown')})"
                )
            return engine

        # No compatible engine available — build a clear error message
        if not candidates:
            if execution_engine in {"kubernetes-direct", "operator"}:
                raise EngineUnavailableError(
                    f"No engine available for {module_path}: "
                    f"execution_engine={execution_engine} but operator disconnected and no kubeconfig"
                )
            raise EngineUnavailableError(
                f"No engine available for {module_path}: "
                f"no OpenTofu engine configured"
            )

        # All candidates were unavailable — list each engine's status
        status_parts = []
        for engine_name, reason in unavailable_reasons.items():
            status_parts.append(f"{engine_name}: {reason}")
        status_str = ". ".join(status_parts) if status_parts else "unknown"

        raise EngineUnavailableError(
            f"All compatible engines are unavailable for {module_path}. {status_str}."
        )

    @staticmethod
    def resolve_operator_for_project(project, db: Session) -> str | None:
        """
        Resolve a connected operator for a project's cluster.

        Looks up the project's KubernetesCluster, then finds a ConnectedOperator
        linked to that cluster (via cluster_id FK) that is currently connected.

        For WebSocket operators: verifies live WS connection.
        For polling operators: trusts DB is_connected flag (set by recent heartbeat).

        Returns the operator_id if found, None otherwise.
        """
        from models import ConnectedOperator, KubernetesCluster

        # Find the project's cluster
        cluster = (
            db.query(KubernetesCluster)
            .filter(KubernetesCluster.project_id == project.id)
            .first()
        )
        if not cluster:
            return None

        # Find operator linked to this cluster
        operator = (
            db.query(ConnectedOperator)
            .filter(
                ConnectedOperator.cluster_id == cluster.id,
                ConnectedOperator.is_connected,
            )
            .first()
        )

        if not operator:
            return None

        # For polling-mode operators, trust DB is_connected (set by recent heartbeat)
        if operator.connectivity_mode == "polling":
            # Check heartbeat recency — if no heartbeat in 60s, consider stale
            from datetime import datetime
            if operator.last_heartbeat_at:
                heartbeat_age = (datetime.now(UTC) - operator.last_heartbeat_at).total_seconds()
                if heartbeat_age > 60:
                    logger.debug(
                        f"Polling operator {operator.operator_id} linked to cluster {cluster.name} "
                        f"but last heartbeat was {heartbeat_age:.0f}s ago — stale"
                    )
                    return None

            logger.info(
                f"Resolved polling operator {operator.operator_id} ({operator.cluster_name}) "
                f"for project '{project.name}' cluster '{cluster.name}'"
            )
            return operator.operator_id

        # For WS-mode operators, verify the live WebSocket connection
        if not ServiceRegistry.get().operator_connections.is_connected(operator.operator_id):
            logger.debug(
                f"Operator {operator.operator_id} linked to cluster {cluster.name} "
                f"but not connected in-memory — skipping"
            )
            return None

        logger.info(
            f"Resolved operator {operator.operator_id} ({operator.cluster_name}) "
            f"for project '{project.name}' cluster '{cluster.name}'"
        )
        return operator.operator_id

    @staticmethod
    def resolve_context(project, db) -> str | None:
        """
        Resolve the kubectl context name for a project's cluster.

        Returns ``cluster.context`` for the project's registered
        KubernetesCluster so direct-K8s execution can target that named
        context via ``--context`` without depending on the kubeconfig's
        current-context. Returns None if no cluster is registered.
        """
        from models import KubernetesCluster

        cluster = (
            db.query(KubernetesCluster)
            .filter(KubernetesCluster.project_id == project.id)
            .first()
        )
        if not cluster:
            return None
        return cluster.context

    @staticmethod
    def resolve_kubeconfig(project, db) -> str | None:
        """
        Resolve kubeconfig path for a project's cluster.

        Checks (in order):
          1. Project's registered KubernetesCluster with stored kubeconfig
          2. EKS cluster — generate kubeconfig via aws eks update-kubeconfig
          3. Fallback: None (K8s engine unavailable)

        For EKS/AWS clusters with stored kubeconfigs, the exec-based
        ``aws eks get-token`` plugin is replaced with a boto3-generated
        bearer token so the aws CLI binary is not required at runtime.
        """
        from models import KubernetesCluster

        # Find the project's cluster
        cluster = (
            db.query(KubernetesCluster)
            .filter(KubernetesCluster.project_id == project.id)
            .first()
        )

        if not cluster:
            return None

        # If cluster has stored kubeconfig content, decrypt and write to temp file
        if cluster.kubeconfig_encrypted:
            kubeconfig_content = decrypt_value(cluster.kubeconfig_encrypted)
            if not kubeconfig_content:
                logger.error(f"Failed to decrypt kubeconfig for cluster {cluster.name}")
                return None

            # Defense-in-depth: assert portability before writing to disk.
            # KubeconfigUnportableError here means a legacy row survived pre-fix;
            # the error propagates to the job with an actionable re-upload message.
            kubeconfig_content = normalize_kubeconfig(
                kubeconfig_content, source=NormalizationSource.INTERNAL_REREAD
            )

            kubeconfig_dir = "/tmp/bnk-forge-kubeconfigs"
            os.makedirs(kubeconfig_dir, exist_ok=True)
            kubeconfig_path = os.path.join(kubeconfig_dir, f"project-{project.id}.yaml")

            # For EKS/AWS clusters, replace exec-plugin auth with a boto3-
            # generated bearer token.  The stored kubeconfig typically contains
            # an exec section that calls ``aws eks get-token`` which requires
            # the AWS CLI binary.  The kr8s library (and Celery workers)
            # may not have it, so we generate the token via boto3 directly.
            if cluster.cloud_provider in ("eks", "aws"):
                kubeconfig_content = _inject_eks_bearer_token(
                    kubeconfig_content, cluster, project, db,
                )
            # For IBM ROKS clusters, refresh the IAM bearer token from the
            # project's credential template. ROKS kubeconfigs typically embed
            # a static IAM token that expires within ~1h, so without refresh
            # any module that runs more than an hour after cluster registration
            # will fail with 401 Unauthorized against the cluster API.
            elif cluster.cloud_provider in ("ibm", "ibmcloud", "roks"):
                kubeconfig_content = _inject_ibm_bearer_token(
                    kubeconfig_content, cluster, project, db,
                )
            # For GKE clusters, mint a GCP access token from the project's
            # service-account JSON so the stored kubeconfig's exec-based
            # ``gke-gcloud-auth-plugin`` auth (which needs the gcloud binary)
            # is replaced with a static bearer token.
            elif cluster.cloud_provider in ("gke", "gcp"):
                kubeconfig_content = _inject_gke_bearer_token(
                    kubeconfig_content, cluster, project, db,
                )
            # For AKS clusters, mint an AAD access token via the OAuth2
            # client-credentials flow so the stored kubeconfig's exec-based
            # ``kubelogin`` auth is replaced with a static bearer token.
            elif cluster.cloud_provider in ("aks", "azure"):
                kubeconfig_content = _inject_aks_bearer_token(
                    kubeconfig_content, cluster, project, db,
                )

            # If the cluster has SSH tunnelling enabled (on-prem clusters
            # whose API IP isn't routable from the worker container), open
            # / reuse the tunnel and rewrite the kubeconfig's `server` URL
            # to point at the local tunnel port. Without this, kr8s /
            # Terraform / helm all dial the API IP directly and fail.
            # Mirrors `cluster_utils._write_kubeconfig` and
            # `_resolve_project_kubeconfig_path` — the third writer to this
            # path now also handles the rewrite.
            if cluster.ssh_tunnel_enabled:
                try:
                    from services.cluster_utils import _maybe_open_ssh_tunnel

                    tunnel_port = _maybe_open_ssh_tunnel(cluster)
                    if tunnel_port:
                        import yaml as yaml_lib
                        kc_dict = yaml_lib.safe_load(kubeconfig_content)
                        for c in kc_dict.get("clusters", []):
                            # 127.0.0.1 explicitly — see cluster_utils.
                            c["cluster"]["server"] = f"https://127.0.0.1:{tunnel_port}"
                            c["cluster"]["insecure-skip-tls-verify"] = True
                            c["cluster"].pop("certificate-authority-data", None)
                            c["cluster"].pop("certificate-authority", None)
                        kubeconfig_content = yaml_lib.dump(kc_dict, default_flow_style=False)
                        logger.info(
                            "engine_router: rewrote kubeconfig server URL to "
                            "https://127.0.0.1:%d (SSH tunnel to cluster %s)",
                            tunnel_port, cluster.name,
                        )
                except Exception as exc:
                    logger.warning(
                        "engine_router: SSH tunnel setup for cluster %s failed "
                        "(%s); kubeconfig will be written without rewrite",
                        cluster.name, exc,
                    )

            with open(kubeconfig_path, "w") as f:
                f.write(kubeconfig_content)

            return kubeconfig_path

        # EKS: generate kubeconfig (requires AWS credentials)
        if cluster.cloud_provider in ["eks", "aws"] and cluster.name:
            return _generate_eks_kubeconfig(cluster, project, db)

        return None


def _inject_eks_bearer_token(kubeconfig_content: str, cluster, project, db) -> str:
    """
    Replace exec-based EKS auth with a boto3-generated bearer token.

    The stored kubeconfig typically has an exec section like::

        users:
          - name: cluster
            user:
              exec:
                command: aws
                args: [eks, get-token, --cluster-name, X, --region, Y]

    We generate the same token via boto3 STS (no CLI needed) and rewrite
    the kubeconfig to use it as a static bearer token instead.

    Returns the (possibly modified) kubeconfig YAML string.
    """
    import base64

    from services.credentials_service import get_cloud_credentials_env

    try:
        import boto3
        from botocore.auth import SigV4QueryAuth
        from botocore.awsrequest import AWSRequest
    except ImportError:
        logger.warning("boto3/botocore not available — cannot generate EKS bearer token")
        return kubeconfig_content

    creds_env = get_cloud_credentials_env(project, db)
    access_key = creds_env.get("AWS_ACCESS_KEY_ID")
    secret_key = creds_env.get("AWS_SECRET_ACCESS_KEY")

    if not access_key or not secret_key:
        logger.warning(
            f"AWS credentials not found for project {project.name} — "
            f"cannot generate EKS bearer token"
        )
        return kubeconfig_content

    # Determine region — prefer cluster, then project, then default
    region = (
        creds_env.get("AWS_REGION")
        or creds_env.get("AWS_DEFAULT_REGION")
        or cluster.region
        or project.region
        or "us-east-1"
    )

    # Determine cluster name — use context or cluster name
    cluster_name = cluster.context or cluster.name
    if cluster.meta_data and cluster.meta_data.get("cluster_arn"):
        # ARN format: arn:aws:eks:region:account:cluster/name
        arn_parts = cluster.meta_data["cluster_arn"].split("/")
        if len(arn_parts) >= 2:
            cluster_name = arn_parts[-1]

    try:
        session = boto3.Session(
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            aws_session_token=creds_env.get("AWS_SESSION_TOKEN"),
            region_name=region,
        )

        # Build a presigned STS GetCallerIdentity URL with the cluster name
        # in a signed header — this is exactly what `aws eks get-token` does.
        regional_sts_url = f"https://sts.{region}.amazonaws.com/?Action=GetCallerIdentity&Version=2011-06-15"

        credentials = session.get_credentials().get_frozen_credentials()
        signer = SigV4QueryAuth(credentials, "sts", region, expires=60)
        request = AWSRequest(
            method="GET",
            url=regional_sts_url,
            headers={"x-k8s-aws-id": cluster_name},
        )
        signer.add_auth(request)

        # Encode as k8s-aws-v1 token
        token = "k8s-aws-v1." + base64.urlsafe_b64encode(
            request.url.encode("utf-8")
        ).rstrip(b"=").decode("utf-8")

        logger.info(f"Generated EKS bearer token for cluster {cluster_name} (region={region})")

        # Rewrite kubeconfig to use static bearer token instead of exec
        return _rewrite_kubeconfig_with_token(kubeconfig_content, token)

    except Exception as e:
        logger.warning(f"Failed to generate EKS bearer token for {cluster.name}: {e}")
        return kubeconfig_content


def _inject_ibm_bearer_token(kubeconfig_content: str, cluster, project, db) -> str:
    """Refresh the IAM bearer token in a stored ROKS kubeconfig.

    ROKS kubeconfigs typically pin a static IAM token that expires within
    about an hour. After that, any module that runs against the cluster
    fails with 401 Unauthorized. Pull the project's IBM Cloud API key
    from its credential template, exchange it for a fresh IAM token,
    and rewrite the kubeconfig users[*].user.token in place.

    Returns the (possibly modified) kubeconfig YAML string. Falls back
    to the unmodified input if the credential template is missing,
    can't be decrypted, or the IAM exchange fails — the cluster will
    keep working as long as the embedded token hasn't expired yet.
    """
    from core.encryption import decrypt_value
    from models import CloudCredentialTemplate
    from services.roks_service import refresh_kubeconfig_iam_token

    # Resolve the project's IBM credential template.
    template_id = getattr(project, "credential_template_id", None)
    if not template_id:
        logger.debug(
            f"No credential_template_id on project {project.name} — "
            f"skipping IBM bearer token refresh for cluster {cluster.name}"
        )
        return kubeconfig_content

    template = (
        db.query(CloudCredentialTemplate)
        .filter(CloudCredentialTemplate.id == template_id)
        .first()
    )
    if not template or template.provider != "ibm" or not template.ibmcloud_api_key_encrypted:
        logger.debug(
            f"Credential template for project {project.name} is not an IBM "
            f"template with an API key — skipping bearer token refresh"
        )
        return kubeconfig_content

    api_key = decrypt_value(template.ibmcloud_api_key_encrypted)
    if not api_key:
        logger.warning(
            f"Failed to decrypt IBM API key on credential template {template_id} "
            f"— skipping bearer token refresh for cluster {cluster.name}"
        )
        return kubeconfig_content

    refreshed = refresh_kubeconfig_iam_token(kubeconfig_content, api_key)
    if not refreshed:
        logger.warning(
            f"IBM IAM token refresh failed for cluster {cluster.name} "
            f"— using whatever token is embedded in the kubeconfig"
        )
        return kubeconfig_content

    logger.info(f"Refreshed IBM IAM bearer token for cluster {cluster.name}")
    return refreshed


def _rewrite_kubeconfig_with_token(kubeconfig_content: str, token: str) -> str:
    """Rewrite every kubeconfig user to a static bearer token.

    Drops any exec / auth-provider stanza so the (gcloud / kubelogin / aws)
    CLI binary is not required at runtime. Mirrors the rewrite in
    ``_inject_eks_bearer_token``.
    """
    import yaml as yaml_lib

    kubeconfig_dict = yaml_lib.safe_load(kubeconfig_content)
    for user_entry in kubeconfig_dict.get("users", []):
        user_entry["user"] = {"token": token}
    return yaml_lib.dump(kubeconfig_dict, default_flow_style=False)


def _inject_gke_bearer_token(kubeconfig_content: str, cluster, project, db) -> str:
    """Replace exec-based GKE auth with a google-auth-minted bearer token.

    GKE kubeconfigs typically use an exec section that calls
    ``gke-gcloud-auth-plugin`` (which needs the gcloud binary). We mint an
    OAuth2 access token directly from the project's service-account JSON via
    ``google-auth`` (already a dependency) and rewrite the kubeconfig users to
    use it as a static bearer token.

    Returns the (possibly modified) kubeconfig YAML string. Falls back to the
    unmodified input on any error (missing creds, invalid JSON, refresh failure).
    """
    from services.credentials_service import get_gcp_service_account_info

    try:
        sa_info = get_gcp_service_account_info(project, db)
        if not sa_info:
            logger.debug(
                f"No GCP service-account credentials for project {project.name} "
                f"— skipping GKE bearer token injection for cluster {cluster.name}"
            )
            return kubeconfig_content

        from google.auth.transport.requests import Request as GoogleAuthRequest
        from google.oauth2 import service_account

        creds = service_account.Credentials.from_service_account_info(
            sa_info,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        creds.refresh(GoogleAuthRequest())
        token = creds.token
        if not token:
            logger.warning(
                f"GCP token refresh returned empty token for cluster {cluster.name} "
                f"— using whatever auth is embedded in the kubeconfig"
            )
            return kubeconfig_content

        logger.info(f"Generated GKE bearer token for cluster {cluster.name}")
        return _rewrite_kubeconfig_with_token(kubeconfig_content, token)

    except Exception as e:
        logger.warning(f"Failed to generate GKE bearer token for {cluster.name}: {e}")
        return kubeconfig_content


def _inject_aks_bearer_token(kubeconfig_content: str, cluster, project, db) -> str:
    """Replace exec-based AKS auth with an AAD-minted bearer token.

    AKS kubeconfigs typically use an exec section that calls ``kubelogin``.
    We mint an AAD access token via the dependency-free OAuth2
    client-credentials flow (a plain ``requests`` POST to the Microsoft
    identity platform — no azure SDK) using the client_id/client_secret/tenant
    from the project's Azure credential template, then rewrite the kubeconfig
    users to use it as a static bearer token.

    Returns the (possibly modified) kubeconfig YAML string. Falls back to the
    unmodified input on any error (missing creds, invalid JSON, HTTP failure).
    """
    # AKS AAD server application id — the scope for kubelogin tokens.
    aks_aad_server_app_id = "6dae42f8-4368-4678-94ff-3960e28e3630"

    template_id = getattr(project, "credential_template_id", None)
    if not template_id:
        logger.debug(
            f"No credential_template_id on project {project.name} — "
            f"skipping AKS bearer token injection for cluster {cluster.name}"
        )
        return kubeconfig_content

    template = (
        db.query(CloudCredentialTemplate)
        .filter(CloudCredentialTemplate.id == template_id)
        .first()
    )
    if (
        not template
        or template.provider != "azure"
        or not template.azure_tenant_id
        or not template.azure_credentials_encrypted
    ):
        logger.debug(
            f"Credential template for project {project.name} is not an Azure "
            f"template with credentials — skipping AKS bearer token injection"
        )
        return kubeconfig_content

    try:
        creds_json = decrypt_value(template.azure_credentials_encrypted)
        if not creds_json:
            logger.warning(
                f"Failed to decrypt Azure credentials on template {template_id} "
                f"— skipping AKS bearer token injection for cluster {cluster.name}"
            )
            return kubeconfig_content

        creds = json.loads(creds_json)
        client_id = creds.get("client_id") or creds.get("clientId")
        client_secret = creds.get("client_secret") or creds.get("clientSecret")
        if not client_id or not client_secret:
            logger.warning(
                f"Azure credentials on template {template_id} missing "
                f"client_id/client_secret — skipping AKS bearer token injection"
            )
            return kubeconfig_content

        token_data = request_azure_oauth_token(
            tenant_id=template.azure_tenant_id,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": f"{aks_aad_server_app_id}/.default",
            },
            timeout=30,
        )
        token = token_data["access_token"]

        logger.info(f"Generated AKS bearer token for cluster {cluster.name}")
        return _rewrite_kubeconfig_with_token(kubeconfig_content, token)

    except Exception as e:
        logger.warning(f"Failed to generate AKS bearer token for {cluster.name}: {e}")
        return kubeconfig_content


def _generate_eks_kubeconfig(cluster, project, db) -> str | None:
    """Generate kubeconfig for an EKS cluster using aws CLI."""
    import subprocess

    from services.credentials_service import get_cloud_credentials_env

    kubeconfig_path = f"/tmp/bnk-forge-kubeconfigs/eks-{cluster.id}.yaml"
    os.makedirs(os.path.dirname(kubeconfig_path), exist_ok=True)

    creds_env = get_cloud_credentials_env(project, db)
    env = {**os.environ, **creds_env}

    region = cluster.region or project.region
    if not region:
        logger.error(
            f"No region available for EKS cluster {cluster.name} — "
            f"set region on cluster or project"
        )
        return None

    cmd = [
        "aws", "eks", "update-kubeconfig",
        "--name", cluster.name,
        "--region", region,
        "--kubeconfig", kubeconfig_path,
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, env=env,
        )
        if result.returncode == 0:
            return kubeconfig_path
        else:
            logger.error(f"Failed to generate EKS kubeconfig: {result.stderr}")
            return None
    except Exception as e:
        logger.error(f"Error generating EKS kubeconfig: {e}")
        return None
