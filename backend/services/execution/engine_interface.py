"""
Engine Interface — the ABC that all deployment engines implement.

This is the single contract between the orchestrator (Celery tasks, parallel
execution, stack deployment) and the execution backends (OpenTofu, kr8s/helm).

Design notes:
  - Synchronous interface (not async) because Celery workers are sync.
    The KubernetesEngine uses asyncio.run() internally.
  - on_output callback is for real-time log streaming via WebSocket.
  - OperationResult carries everything the task layer needs to update DB/UI.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class OperationResult:
    """Result of an apply/destroy/init operation."""
    success: bool
    outputs: dict[str, Any] = field(default_factory=dict)
    stdout: str = ""
    stderr: str = ""
    resources_created: int = 0
    resources_modified: int = 0
    resources_destroyed: int = 0
    duration_seconds: float = 0.0
    error_message: str | None = None
    error_suggestion: str | None = None

    @property
    def log_output(self) -> str:
        """Combined stdout+stderr for storing in task.logs."""
        parts = []
        if self.stdout:
            parts.append(self.stdout)
        if self.stderr:
            parts.append(f"--- STDERR ---\n{self.stderr}")
        if self.error_message:
            parts.append(f"--- ERROR ---\n{self.error_message}")
        if self.error_suggestion:
            parts.append(f"--- SUGGESTION ---\n{self.error_suggestion}")
        return "\n".join(parts) if parts else ""


@dataclass
class PlanResult:
    """Result of a plan/preview operation."""
    has_changes: bool
    adds: int = 0
    changes: int = 0
    destroys: int = 0
    details: str = ""
    plan_id: str | None = None  # OpenTofu: saved plan file path
    skipped: bool = False  # engine deliberately no-op'd (e.g. no use-cases selected) — a
    # structural success sentinel, distinct from has_changes=False on a real failure. Callers
    # must not infer this from substring-matching `details`, which can embed raw tool stdout.


@dataclass
class ModuleContext:
    """
    Engine-agnostic module context — everything an engine needs to execute.

    This replaces the tight coupling to ProjectModule/ModuleLibrary SQLAlchemy
    models. The task layer builds this from DB models, then passes it to the
    engine which has zero DB knowledge.
    """
    module_id: int
    project_id: int
    path: str                          # e.g., "bnk/flo" or "infra/aws/vpc"
    category: str                      # "infra", "k8s", "bnk", "app"
    variables: dict[str, Any] = field(default_factory=dict)
    credentials_env: dict[str, str] = field(default_factory=dict)

    # OpenTofu-specific (ignored by K8s engine)
    source_url: str | None = None   # Git URL for TF modules
    source_ref: str | None = None   # Git branch/tag
    workspace_path: str | None = None  # Persistent workspace

    # K8s-specific (ignored by OpenTofu engine)
    kubeconfig_path: str | None = None
    cluster_name: str | None = None

    # Catalog/runtime metadata (optional, primarily for K8s/Ansible decoupling)
    module_source_kind: str | None = None
    deploy_model: str | None = None
    pack_manifest: dict[str, Any] | None = None
    module_name: str | None = None
    module_version: str | None = None

    # SSH-specific (ignored by OpenTofu/K8s/Ansible engines)
    ssh_host: str | None = None
    ssh_port: int = 22
    ssh_username: str | None = None
    ssh_password: str | None = None
    ssh_private_key_content: str | None = None
    ssh_key_passphrase: str | None = None
    jumphost_chain: list[dict] | None = None

    # DPU relay (SSH through host to DPU)
    dpu_host: str | None = None
    dpu_username: str | None = None
    dpu_password: str | None = None
    dpu_private_key_content: str | None = None

    # TMOS-specific (ignored by all other engines)
    # Populated by tmos_tasks._build_tmos_context(); engine has zero DB knowledge.
    tmos_host: str | None = None
    tmos_port: int = 443
    tmos_verify_https: bool | None = None   # None → default True in client
    tmos_credential: Any | None = None       # decrypted F5Credential instance
    as3_tenant: str | None = None            # AS3 tenant name — MUST be set; destroy fails-closed if empty
    as3_declaration: dict | None = None      # Rendered AS3 declaration dict; NEVER logged


class DeploymentEngine(ABC):
    """
    Interface that all deployment engines implement.

    Contract:
      - All methods are synchronous (Celery workers are sync).
      - The engine MUST NOT access the database directly.
      - The engine MUST NOT modify module/task status in DB.
      - Output streaming goes through the on_output callback.
      - Returns OperationResult/PlanResult — caller handles DB updates.
    """

    @abstractmethod
    def init(
        self,
        ctx: ModuleContext,
        on_output: Callable[[str], None] | None = None,
    ) -> OperationResult:
        """
        Initialize the module workspace.

        For OpenTofu: tofu init (download providers, configure backend).
        For K8s: no-op (or validate kubeconfig connectivity).
        """
        ...

    @abstractmethod
    def plan(
        self,
        ctx: ModuleContext,
        on_output: Callable[[str], None] | None = None,
    ) -> PlanResult:
        """
        Preview what would change.

        For OpenTofu: tofu plan -detailed-exitcode.
        For K8s: compare rendered manifests vs live cluster state.
        """
        ...

    @abstractmethod
    def apply(
        self,
        ctx: ModuleContext,
        on_output: Callable[[str], None] | None = None,
    ) -> OperationResult:
        """
        Deploy the module.

        For OpenTofu: tofu apply (with optional saved plan).
        For K8s: kubectl apply / helm upgrade --install.
        """
        ...

    @abstractmethod
    def destroy(
        self,
        ctx: ModuleContext,
        on_output: Callable[[str], None] | None = None,
    ) -> OperationResult:
        """
        Remove all resources created by this module.

        For OpenTofu: tofu destroy.
        For K8s: kubectl delete / helm uninstall.
        """
        ...

    @abstractmethod
    def get_outputs(
        self,
        ctx: ModuleContext,
    ) -> dict[str, Any]:
        """
        Read current outputs for dependency wiring.

        For OpenTofu: tofu output -json.
        For K8s: read from live cluster resources.
        """
        ...

    def health_check(self) -> bool:
        """
        Quick check that this engine can execute right now.

        Returns True if the engine is operational, False otherwise.
        Each engine implements its own check:
          - OpenTofu: `tofu --version` succeeds
          - KubernetesEngine: can reach the API server (list namespaces)
          - OperatorEngine: WebSocket connected and heartbeat < 60s ago
        """
        return True  # Default: assume healthy

    def check_drift(
        self,
        ctx: ModuleContext,
    ) -> PlanResult | None:
        """
        Check if actual state differs from desired.
        Default implementation delegates to plan().
        """
        return self.plan(ctx)
