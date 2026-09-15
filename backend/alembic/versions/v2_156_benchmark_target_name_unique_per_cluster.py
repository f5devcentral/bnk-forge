"""Scope benchmark_targets.name uniqueness to (cluster_id, name).

Revision ID: v2_156
Revises: v2_155

BenchmarkTarget.name previously carried a GLOBAL unique constraint, so target
names were unique across the entire Forge instance rather than per cluster:
two clusters running the same demo route (e.g. "mcp-default-mcp-financial-route")
collided on the second registration with HTTP 409, causing discovery clients
to either fail or incorrectly map endpoints across clusters.

This migration drops the global unique constraint/index on `name` and introduces
a composite unique constraint `uq_benchmark_targets_cluster_name` on
`(cluster_id, name)`, preserving a non-unique index `ix_benchmark_targets_name`
for fast lookup by target name across clusters.
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_156"
down_revision = "v2_155"
branch_labels = None
depends_on = None

_TABLE = "benchmark_targets"
_NEW = "uq_benchmark_targets_cluster_name"


def _find_all_global_name_uniques(bind) -> list[tuple[str, str]]:
    """Return list of (name, kind) for unique constraints/indexes on single column `name`."""
    insp = sa.inspect(bind)
    found: list[tuple[str, str]] = []
    for uc in insp.get_unique_constraints(_TABLE):
        if uc.get("column_names") == ["name"] and uc.get("name"):
            found.append((uc["name"], "constraint"))
    for ix in insp.get_indexes(_TABLE):
        if ix.get("unique") and ix.get("column_names") == ["name"] and ix.get("name"):
            found.append((ix["name"], "index"))
    return found


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    uniques = _find_all_global_name_uniques(bind)

    if dialect == "sqlite":
        # SQLite cannot ALTER a column's unique-ness in place; batch mode
        # recreates the table from the reflected schema.
        with op.batch_alter_table(_TABLE, recreate="always") as batch:
            for name, kind in uniques:
                if kind == "constraint":
                    batch.drop_constraint(name, type_="unique")
                else:
                    batch.drop_index(name)
            batch.create_unique_constraint(_NEW, ["cluster_id", "name"])
        # Preserve fast non-unique lookup index on name
        op.create_index("ix_benchmark_targets_name", _TABLE, ["name"], unique=False, if_not_exists=True)
        return

    # Postgres / MySQL / etc.
    for name, kind in uniques:
        if kind == "constraint":
            op.drop_constraint(name, _TABLE, type_="unique")
        else:
            op.drop_index(name, table_name=_TABLE, if_exists=True)

    remaining = _find_all_global_name_uniques(bind)
    if remaining:
        raise RuntimeError(
            f"v2_156: global unique {remaining!r} on {_TABLE}.name survived the drop; "
            "refusing to add the composite on top of it"
        )

    op.create_unique_constraint(_NEW, _TABLE, ["cluster_id", "name"])
    # Keep name cheaply searchable without uniqueness
    op.create_index("ix_benchmark_targets_name", _TABLE, ["name"], unique=False, if_not_exists=True)


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "sqlite":
        with op.batch_alter_table(_TABLE, recreate="always") as batch:
            batch.drop_constraint(_NEW, type_="unique")
            batch.create_unique_constraint("benchmark_targets_name_key", ["name"])
        return

    op.drop_constraint(_NEW, _TABLE, type_="unique")
    op.drop_index("ix_benchmark_targets_name", table_name=_TABLE, if_exists=True)
    # Restoring the global unique will fail if multiple clusters now share a name,
    # which is intentional: the operator must deduplicate first.
    op.create_unique_constraint("benchmark_targets_name_key", _TABLE, ["name"])
