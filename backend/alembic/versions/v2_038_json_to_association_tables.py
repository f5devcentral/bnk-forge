"""Replace JSON ID-array columns with proper association tables.

IMP-020: Creates module_dependencies and parallel_execution_modules tables,
migrates existing JSON data into association rows.

JSON columns are NOT dropped from their source tables in this migration
to allow for a gradual transition and safe rollback.

Tables created:
- module_dependencies: Self-referential M2M for ProjectModule dependencies
- parallel_execution_modules: Per-module results of ParallelExecution runs

Revision ID: v2_038
Revises: v2_037
Create Date: 2026-02-22
"""
import json

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "v2_038"
down_revision = "v2_037"
branch_labels = None
depends_on = None


def upgrade():
    # ----------------------------------------------------------------
    # 1. Create module_dependencies association table
    # ----------------------------------------------------------------
    op.create_table(
        "module_dependencies",
        sa.Column(
            "module_id",
            sa.Integer(),
            sa.ForeignKey("project_modules.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "dependency_id",
            sa.Integer(),
            sa.ForeignKey("project_modules.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    # ----------------------------------------------------------------
    # 2. Create parallel_execution_modules association table
    # ----------------------------------------------------------------
    op.create_table(
        "parallel_execution_modules",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "execution_id",
            sa.Integer(),
            sa.ForeignKey("parallel_executions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("module_id", sa.Integer(), nullable=False),
        sa.Column("result_status", sa.String(20), nullable=False),
    )
    op.create_index(
        "idx_pem_execution_status",
        "parallel_execution_modules",
        ["execution_id", "result_status"],
    )
    op.create_index(
        "idx_pem_module",
        "parallel_execution_modules",
        ["module_id"],
    )

    # ----------------------------------------------------------------
    # 3. Migrate data from JSON columns
    # ----------------------------------------------------------------
    conn = op.get_bind()

    # 3a. Migrate ProjectModule.dependencies -> module_dependencies
    rows = conn.execute(
        sa.text("SELECT id, dependencies FROM project_modules WHERE dependencies IS NOT NULL")
    ).fetchall()
    for row in rows:
        module_id = row[0]
        raw = row[1]
        if raw is None:
            continue
        deps = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(deps, list) or not deps:
            continue
        for dep_id in deps:
            if dep_id is None or dep_id == module_id:
                continue
            try:
                conn.execute(
                    sa.text(
                        "INSERT INTO module_dependencies (module_id, dependency_id) "
                        "VALUES (:mid, :did)"
                    ),
                    {"mid": module_id, "did": int(dep_id)},
                )
            except Exception:
                pass  # Skip duplicate or invalid FK

    # 3b. Migrate ParallelExecution.successful/failed/skipped_modules
    pe_rows = conn.execute(
        sa.text(
            "SELECT id, successful_modules, failed_modules, skipped_modules "
            "FROM parallel_executions"
        )
    ).fetchall()
    for pe_row in pe_rows:
        exec_id = pe_row[0]
        for col_idx, status in [(1, "successful"), (2, "failed"), (3, "skipped")]:
            raw = pe_row[col_idx]
            if raw is None:
                continue
            ids = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(ids, list) or not ids:
                continue
            for mid in ids:
                if mid is None:
                    continue
                try:
                    conn.execute(
                        sa.text(
                            "INSERT INTO parallel_execution_modules "
                            "(execution_id, module_id, result_status) "
                            "VALUES (:eid, :mid, :status)"
                        ),
                        {"eid": exec_id, "mid": int(mid), "status": status},
                    )
                except Exception:
                    pass  # Skip duplicates


def downgrade():
    op.drop_table("parallel_execution_modules")
    op.drop_table("module_dependencies")
