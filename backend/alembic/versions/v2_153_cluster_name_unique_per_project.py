"""Scope kubernetes_clusters.name uniqueness to (project_id, name).

Revision ID: v2_153
Revises: v2_152

KubernetesCluster.name carried a GLOBAL unique constraint, so cluster names
were unique across the whole instance rather than per project: project A
naming a cluster "prod" blocked project B from using "prod", and B learned
of A's cluster via the 409 -- a cross-tenant information leak plus a false
collision (INV-1/INV-2, issue #113).

Fixing only the application-level duplicate check would move the failure
from a clean 409 to a raw IntegrityError at commit, so the constraint has
to change too: drop the global unique on name, add a composite unique on
(project_id, name).

project_id is nullable. Under Postgres, NULLs are distinct in a unique
constraint, so two project-less (hand-registered / global) clusters may
share a name. That is the intended reading: tenancy is the project, and a
cluster with no project has no tenant to collide within. SQLite behaves
the same way.

The global index was created implicitly by unique=True on the column, so
its name is DB-generated (kubernetes_clusters_name_key on Postgres; SQLite
uses an unnamed autoindex). Dropping by the explicit SQLAlchemy constraint
name is not portable, so the upgrade inspects for it; on SQLite the table
is recreated via batch_alter_table, which is the only way to drop a column
constraint there.
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_153"
down_revision = "v2_152"
branch_labels = None
depends_on = None

_TABLE = "kubernetes_clusters"
_NEW = "uq_kubernetes_clusters_project_name"


def _find_global_name_unique(bind) -> str | None:
    """Return the name of the single-column unique on `name`, if any."""
    insp = sa.inspect(bind)
    for uc in insp.get_unique_constraints(_TABLE):
        if uc.get("column_names") == ["name"]:
            return uc.get("name")
    for ix in insp.get_indexes(_TABLE):
        if ix.get("unique") and ix.get("column_names") == ["name"]:
            return ix.get("name")
    return None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    old = _find_global_name_unique(bind)

    if dialect == "sqlite":
        # SQLite cannot ALTER a column's unique-ness in place; batch mode
        # recreates the table from the REFLECTED schema. Be precise about what
        # actually drops the old unique here, because it is not the
        # drop_constraint below: SQLite reflection does not surface a
        # column-level UNIQUE at all (_find_global_name_unique returns None on
        # a table created with `name ... UNIQUE`), so `old` is usually None and
        # that branch is skipped. The recreate rebuilds `name` from the
        # reflected Column, which carries no unique flag -- and THAT is what
        # removes it. Verified on the post-upgrade DDL: `name VARCHAR(255) NOT
        # NULL` with no UNIQUE, composite present. The drop_constraint stays
        # for the case where the unique WAS reflectable (a named constraint
        # from an older explicit migration); it is belt-and-braces, not the
        # mechanism. test_v2_153 asserts the end state, not the path.
        with op.batch_alter_table(_TABLE, recreate="always") as batch:
            if old:
                batch.drop_constraint(old, type_="unique")
            batch.create_unique_constraint(_NEW, ["project_id", "name"])
        # The plain lookup index on name must survive the recreate.
        op.create_index("ix_kubernetes_clusters_name", _TABLE, ["name"], if_not_exists=True)
        return

    if old:
        # Postgres: a column-level UNIQUE lands in pg_constraint as contype='u'
        # named kubernetes_clusters_name_key, and the dialect's
        # get_unique_constraints reads pg_catalog.pg_constraint -- so the finder
        # DOES return it here, unlike SQLite, and this drop is the real
        # mechanism on this path. Some older stacks may carry it as a unique
        # index instead -- try both shapes.
        # Decide the shape from inspection rather than try/except-on-anything:
        # a bare `except Exception` would swallow a permissions or lock error
        # and then create the composite OVER a still-present global unique,
        # leaving the DB with both and the bug intact.
        insp = sa.inspect(bind)
        is_constraint = any(uc.get("name") == old for uc in insp.get_unique_constraints(_TABLE))
        if is_constraint:
            op.drop_constraint(old, _TABLE, type_="unique")
        else:
            op.drop_index(old, table_name=_TABLE, if_exists=True)
        # Refuse to continue if it is somehow still there: creating the composite
        # alongside a surviving global unique would be a silent no-fix.
        if _find_global_name_unique(bind) is not None:
            raise RuntimeError(
                f"v2_153: global unique {old!r} on {_TABLE}.name survived the drop; "
                "refusing to add the composite on top of it"
            )
    op.create_unique_constraint(_NEW, _TABLE, ["project_id", "name"])
    # Keep name cheaply searchable without uniqueness (the global unique
    # doubled as the lookup index; this restores that).
    op.create_index("ix_kubernetes_clusters_name", _TABLE, ["name"], unique=False, if_not_exists=True)


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "sqlite":
        with op.batch_alter_table(_TABLE, recreate="always") as batch:
            batch.drop_constraint(_NEW, type_="unique")
            batch.create_unique_constraint("kubernetes_clusters_name_key", ["name"])
        return
    op.drop_constraint(_NEW, _TABLE, type_="unique")
    op.drop_index("ix_kubernetes_clusters_name", table_name=_TABLE, if_exists=True)
    # Restoring the global unique can FAIL if two projects now share a name.
    # That is correct: downgrading would reintroduce the cross-tenant
    # collision, and the operator must rename first. Let it raise.
    op.create_unique_constraint("kubernetes_clusters_name_key", _TABLE, ["name"])
