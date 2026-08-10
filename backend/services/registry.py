"""
Lightweight service registry — populated at startup, accessed anywhere.

Replaces lazy imports that were hiding circular dependencies between:
  - services/operator_registry.py (operator_connections)
  - services/execution/ (engine_router, task_dispatch, etc.)

The registry is a singleton populated once during app lifespan startup.
Thread-safe reads are guaranteed by the Python GIL.

Usage:
    from services.registry import ServiceRegistry

    registry = ServiceRegistry.get()
    op_conns = registry.operator_connections    # OperatorConnectionManager

For testing:
    ServiceRegistry.reset()  # clear singleton between tests
"""

import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from modules.base import BaseModule
    from services.operator_registry import OperatorConnectionManager

logger = logging.getLogger(__name__)


class ServiceRegistry:
    """
    Central service registry — eliminates circular import workarounds.

    Populated at startup in main.py lifespan, then accessed via .get()
    throughout the codebase. Replaces ~20 lazy imports scattered inside
    methods that were hiding circular dependencies.
    """

    _instance: Optional["ServiceRegistry"] = None

    def __init__(self):
        # Operator connection manager (in-memory WebSocket layer)
        # Populated from services.operator_registry.operator_connections
        self._operator_connections: OperatorConnectionManager | None = None

        # Python module registry (bare-metal SSH modules)
        # Populated from modules.get_module_registry()
        self._module_registry: dict[str, BaseModule] | None = None

    @classmethod
    def get(cls) -> "ServiceRegistry":
        """Get the singleton instance (creates if needed)."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton — for testing only."""
        cls._instance = None

    # -- Operator connections ------------------------------------------------

    @property
    def operator_connections(self) -> "OperatorConnectionManager":
        """
        Get the operator connection manager.

        Falls back to the global singleton if not yet populated.
        """
        if self._operator_connections is None:
            logger.debug("ServiceRegistry: operator_connections not populated, using global")
            from services.operator_registry import operator_connections
            self._operator_connections = operator_connections
        return self._operator_connections

    @operator_connections.setter
    def operator_connections(self, value: "OperatorConnectionManager") -> None:
        self._operator_connections = value

    # -- Module registry (bare-metal SSH modules) ----------------------------

    @property
    def module_registry(self) -> dict[str, "BaseModule"]:
        """
        Get the Python module registry (SSH/bare-metal modules).

        K8s/BNK/app modules live in the git catalog; this registry only
        holds the Python-defined bare-metal modules used by SSHEngine.
        """
        if self._module_registry is None:
            logger.debug("ServiceRegistry: module_registry not populated, initializing lazily")
            from modules import get_module_registry
            self._module_registry = get_module_registry()
        return self._module_registry

    @module_registry.setter
    def module_registry(self, value: dict[str, "BaseModule"]) -> None:
        self._module_registry = value
