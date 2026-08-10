"""
Cluster Scanner Service — backward-compatibility shim.

The implementation has been decomposed into ``services.scanner.*`` sub-modules.
This file re-exports the public API for existing imports.

Original: 1,198 lines → now 7 focused modules in services/scanner/
"""

from services.scanner import ClusterScanner  # noqa: F401
from services.scanner.constants import PrerequisiteStatus  # noqa: F401
