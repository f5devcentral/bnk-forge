"""
Execution engine package — multi-engine support for BNK-Forge v2.

Engines:
  - OpenTofuEngine: adapter wrapping OpenTofuRuntime for IaC (infra/* modules)
  - KubernetesEngine: kr8s + helm CLI for K8s/BNK modules (k8s/*, bnk/*)
  - OpenTofuRuntime: core subprocess management (init/plan/apply/destroy)

Router:
  - EngineRouter: picks the right engine per module

Public API:
  - OpenTofuRuntime(db) — low-level OT subprocess execution
  - can_execute(db, module) — check if module dependencies are satisfied
  - build_variables(db, module) — build the 7-layer variable chain
  - config_writer — write_tfvars, write_backend_config, write_encryption_config, write_provider_config
"""

# Re-export commonly used classes and functions
from services.execution.opentofu_runtime import OpenTofuRuntime  # noqa: F401
from services.execution.variable_assembler import build_variables, can_execute  # noqa: F401
