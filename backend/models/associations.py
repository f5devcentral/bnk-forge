"""
Association tables for converting JSON ID arrays to proper relational structures.

Tables:
- module_dependencies: Self-referential M2M for ProjectModule dependency graph

Note: parallel_execution_modules and parallel_executions were dropped in D-001
Phase 3 S3b (migration v2_119). ParallelExecutionModule ORM class removed with them.
"""

from sqlalchemy import Column, ForeignKey, Integer, Table

from database import Base

# Self-referential M2M for ProjectModule dependency graph.
# Each row means: module_id depends on dependency_id.
module_dependencies = Table(
    "module_dependencies",
    Base.metadata,
    Column(
        "module_id",
        Integer,
        ForeignKey("project_modules.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "dependency_id",
        Integer,
        ForeignKey("project_modules.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)
