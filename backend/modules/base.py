"""
Base classes for Python-defined K8s/BNK modules.

Three module types:
  - ManifestModule: renders K8s manifest dicts, applied via kr8s server-side apply
  - HelmModule: renders Helm values, installed via helm CLI
  - MixedModule: both manifests AND subprocess steps (e.g., bnk-prerequisites)

Each module declares:
  - inputs:  what variables it needs (with sources and defaults)
  - outputs: what values it produces (read from cluster after apply)
  - dependencies: other module paths it requires
  - readiness conditions: what to wait for after apply
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class InputSpec:
    """Declares a module input variable."""
    name: str = ""
    type: str = "string"            # "string", "number", "bool", "list", "map"
    required: bool = True
    default: Any = None
    source: str = "user"            # "user", "module", "profile", "auto", "system"
    from_module: str | None = None   # e.g., "bnk/flo"
    from_output: str | None = None   # e.g., "flo_namespace"
    description: str = ""
    encoding: str | None = None  # "raw" = must never be transformed (e.g. JWT tokens)
    sensitive: bool = False


@dataclass
class OutputSpec:
    """Declares how to read an output from the cluster after apply."""
    resource_kind: str              # e.g., "GatewayClass", "Namespace"
    resource_name: str              # Can use {variable} placeholders: "{gatewayclass_name}"
    namespace: str = "default"
    field_path: str = ""            # Dot-notation: "metadata.name", "status.conditions[0].status"
    static_value: Any = None        # For outputs that are just the input value passed through

    def resolve_name(self, variables: dict[str, Any]) -> str:
        """Replace {placeholder} tokens with variable values."""
        name = self.resource_name
        for key, val in variables.items():
            name = name.replace(f"{{{key}}}", str(val))
        return name

    def extract_from(self, resource_dict: dict) -> Any:
        """Extract value from a K8s resource dict using field_path."""
        if self.static_value is not None:
            return self.static_value
        if not self.field_path:
            return None

        parts = self.field_path.split(".")
        current = resource_dict
        for part in parts:
            if current is None:
                return None
            if "[" in part:
                key, idx = part.rstrip("]").split("[")
                current = current.get(key) if isinstance(current, dict) else None
                if current is not None and isinstance(current, list):
                    idx_int = int(idx)
                    current = current[idx_int] if idx_int < len(current) else None
            else:
                current = current.get(part) if isinstance(current, dict) else None
        return current


class BaseModule(ABC):
    """
    Base class for all Python-defined K8s modules.

    Subclasses must set class attributes and implement render_manifests().
    """

    def __init_subclass__(cls, **kwargs):
        """Deep-copy mutable class attributes so each subclass gets its own copy.

        Prevents the classic Python mutable default gotcha where mutation on
        one subclass (e.g. self.inputs["foo"] = bar) silently affects all others.
        """
        super().__init_subclass__(**kwargs)
        if 'inputs' in cls.__dict__ or not hasattr(cls, '_inputs_copied'):
            cls.inputs = dict(cls.inputs)
            cls._inputs_copied = True
        if 'outputs' in cls.__dict__ or not hasattr(cls, '_outputs_copied'):
            cls.outputs = dict(cls.outputs)
            cls._outputs_copied = True
        if 'dependencies' in cls.__dict__ or not hasattr(cls, '_deps_copied'):
            cls.dependencies = list(cls.dependencies)
            cls._deps_copied = True

    # Module identity — set in subclass
    name: str = ""
    path: str = ""                      # e.g., "k8s/network-setup"
    category: str = ""                  # "k8s", "bnk", "app"
    description: str = ""
    module_type: str = "manifest"       # "manifest" or "helm_chart"
    version: str = "1.0.0"

    # Execution config
    timeout: int = 300                  # Seconds to wait for readiness

    # Declarations
    inputs: dict[str, InputSpec] = {}
    outputs: dict[str, OutputSpec] = {}
    dependencies: list[str] = []        # Module paths this depends on

    @abstractmethod
    def render_manifests(self, variables: dict[str, Any]) -> list[dict]:
        """
        Return list of K8s manifest dicts to apply.

        Each dict is a complete K8s resource (apiVersion, kind, metadata, spec).
        They will be applied in order via server-side apply.
        """
        ...

    def get_readiness_condition(self, manifest: dict) -> str | None:
        """
        Return the condition string to wait for after applying this manifest.

        Examples:
          - "condition=Accepted"      (GatewayClass)
          - "condition=Programmed"    (F5SPKVlan)
          - "condition=Ready"         (Pod, Deployment)
          - None                      (don't wait — e.g., ConfigMap, Secret)
        """
        return None

    def get_destroy_order(self, manifests: list[dict]) -> list[dict]:
        """
        Return manifests in the order they should be deleted.
        Default: reverse of apply order.
        """
        return list(reversed(manifests))

    def get_static_outputs(self, variables: dict[str, Any]) -> dict[str, Any]:
        """
        Return outputs that can be computed from variables alone
        (without querying the cluster). Useful for simple pass-through outputs.
        """
        result = {}
        for name, spec in self.outputs.items():
            if spec.static_value is not None:
                result[name] = spec.static_value
        return result


class ManifestModule(BaseModule):
    """Base for modules that apply K8s manifests directly via kr8s."""
    module_type = "manifest"


class HelmModule(BaseModule):
    """
    Base for modules that install a Helm chart.

    Subclasses must set chart config and implement render_helm_values().
    """
    module_type = "helm_chart"

    # Helm chart config — set in subclass
    release_name: str = ""
    chart_ref: str = ""                 # e.g., "jetstack/cert-manager" or local path
    chart_version: str | None = None
    namespace: str = "default"
    create_namespace: bool = True

    # Optional: extra repos to add before install
    helm_repos: dict[str, str] = {}     # e.g., {"jetstack": "https://charts.jetstack.io"}

    def render_manifests(self, variables: dict[str, Any]) -> list[dict]:
        """Helm modules don't render manifests — they use render_helm_values."""
        return []

    @abstractmethod
    def render_helm_values(self, variables: dict[str, Any]) -> dict:
        """Return the Helm values dict to pass to helm install/upgrade."""
        ...


class SSHModule(BaseModule):
    """
    Base for modules that execute via SSH commands on bare-metal hosts.

    Subclasses define shell commands instead of K8s manifests.
    The SSHEngine calls these methods to get the commands to execute.

    Module types:
      - ManifestModule: renders K8s YAML, applied via kr8s
      - HelmModule: renders Helm values, installed via helm CLI
      - SSHModule: renders shell commands, executed via paramiko SSH
    """

    module_type = "ssh"

    # Phase grouping for progress reporting (e.g., "Discovery", "DPU Flash")
    phase: str = ""

    # SSH-specific config
    target: str = "host"             # "host" or "dpu"
    connectivity_risk: bool = False
    reconnect_timeout: int = 600     # Seconds to wait for reconnect
    estimated_duration: int = 60     # Seconds (for UI progress estimation)
    timeout: int = 300               # Per-command timeout

    def render_manifests(self, variables: dict[str, Any]) -> list[dict]:
        """SSH modules don't render K8s manifests."""
        return []

    def plan_commands(self, variables: dict[str, Any]) -> list[str]:
        """Return shell commands to probe current state.

        These commands should exit 0 and produce output that
        parse_plan_output() can interpret.
        """
        return []

    def parse_plan_output(self, output: str, variables: dict[str, Any]) -> bool:
        """Parse plan command output. Return True if apply is needed."""
        return True  # Default: always apply

    def apply_commands(self, variables: dict[str, Any]) -> list[str]:
        """Return shell commands to execute the step."""
        raise NotImplementedError(f"{self.__class__.__name__} must implement apply_commands()")

    def validate_commands(self, variables: dict[str, Any]) -> list[str]:
        """Return shell commands to verify apply succeeded.

        Each command must exit 0 for validation to pass.
        """
        return []

    def pre_stage_commands(self, variables: dict[str, Any]) -> list[str]:
        """Return commands to run BEFORE apply for connectivity preservation.

        Only called when connectivity_risk=True.
        """
        return []

    def execute(
        self,
        session: Any,
        variables: dict[str, Any],
        on_output: Any,
    ) -> dict[str, Any]:
        """Execute step using Python logic with a live SSH session.

        Override this to replace apply_commands() + parse_apply_output().
        Returns the outputs dict directly.

        Default: raises NotImplementedError — engine falls back to
        apply_commands() shell-string path.

        Args:
            session:    Live SSHSession connected to the target (host or DPU).
                        Use session.execute(cmd, timeout=N) -> result with
                        .exit_code, .stdout, .stderr
            variables:  Resolved input variables for this module.
            on_output:  Callback for real-time log lines (str -> None).

        Returns:
            dict of output key-value pairs for downstream modules.
        """
        raise NotImplementedError

    def parse_apply_output(self, output: str, variables: dict[str, Any]) -> dict[str, Any]:
        """Parse apply command output into structured outputs.

        Returns dict of key-value pairs available to downstream modules.
        """
        return {}

    def prereq_commands(self, variables: dict[str, Any]) -> list[str]:
        """Return commands to check prerequisites during init().

        Each must exit 0 for init to succeed.
        """
        return []

    def validate_inputs(self, variables: dict[str, Any]) -> list[str]:
        """Validate that all required inputs are present and non-empty.

        Returns a list of error messages. Empty list = valid.
        Called by SSHEngine before init/apply/plan.
        """
        errors: list[str] = []
        for input_name, spec in self.inputs.items():
            if not spec.required:
                continue
            # Skip host-sourced inputs (injected by engine, not user-provided)
            if spec.source == "host":
                continue
            val = variables.get(input_name)
            # Check for missing or empty
            if val is None:
                # Check if there's a default
                if spec.default is not None:
                    continue
                errors.append(
                    f"Required input '{input_name}' is missing"
                    + (f" (source: {spec.from_module}.{spec.from_output})" if spec.from_module else "")
                )
            elif isinstance(val, str) and not val.strip():
                if spec.default is not None and spec.default != "":
                    continue
                errors.append(
                    f"Required input '{input_name}' is empty"
                    + (" — provide a value via Edit Variables" if spec.source == "user" else "")
                )
        return errors

    def get_readiness_condition(self, manifest: dict) -> str | None:
        """Not applicable for SSH modules."""
        return None
