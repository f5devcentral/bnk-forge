"""DPU extra VLAN interfaces — replace fixed `storage_*` with a JSON list.

The DPU project defaults table and per-DPU rows used to carry three
hard-coded VLAN rows: external, internal, storage. External and
internal remain fixed (they're the mandatory pair every BNK deployment
needs), but storage was always optional and operators want more
flexibility — arbitrary extra VLANs (vdi, control-plane, backup, …)
with the same subnet/start-host/uplink story as storage.

This migration:
  * adds `extra_vlan_interfaces` JSON on `project_dpu_settings`
    (shape: `[{name, vlan_id, ipv4_subnet, ipv6_subnet, start_host, uplink}, ...]`)
  * adds `extra_vlan_interfaces` JSON on `dpus`
    (shape: `[{name, vlan_id, ipv4, ipv6}, ...]`)
  * copies each row's existing `storage_*` fields into the first entry
    of the new list when any of them is set (preserves data)
  * drops the five `storage_vlan_*` columns on `project_dpu_settings`
    and the three `storage_vlan_*` columns on `dpus`

Revision ID: v2_080
Revises: v2_079
"""

import json

import sqlalchemy as sa

from alembic import op

revision = "v2_080"
down_revision = "v2_079"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add the new JSON columns (nullable so the backfill can run,
    #    tightened to NOT NULL afterwards).
    op.add_column(
        "project_dpu_settings",
        sa.Column("extra_vlan_interfaces", sa.JSON(), nullable=True),
    )
    op.add_column(
        "dpus",
        sa.Column("extra_vlan_interfaces", sa.JSON(), nullable=True),
    )

    bind = op.get_bind()

    # 2. Migrate ProjectDpuSettings.storage_* → extra_vlan_interfaces list.
    rows = bind.execute(sa.text(
        "SELECT id, storage_vlan_id, storage_vlan_ipv4_subnet, "
        "storage_vlan_ipv6_subnet, storage_vlan_start_host, storage_vlan_uplink "
        "FROM project_dpu_settings"
    )).fetchall()
    for row in rows:
        has_any = any(
            v is not None for v in (row[1], row[2], row[3], row[4], row[5])
        )
        payload = []
        if has_any:
            payload = [{
                "name": "storage",
                "vlan_id": row[1],
                "ipv4_subnet": row[2],
                "ipv6_subnet": row[3],
                "start_host": row[4],
                "uplink": row[5],
            }]
        bind.execute(
            sa.text(
                "UPDATE project_dpu_settings SET extra_vlan_interfaces = :p WHERE id = :id"
            ),
            {"p": json.dumps(payload), "id": row[0]},
        )

    # 3. Migrate Dpu.storage_vlan_* → extra_vlan_interfaces list.
    rows = bind.execute(sa.text(
        "SELECT id, storage_vlan_id, storage_vlan_ipv4, storage_vlan_ipv6 FROM dpus"
    )).fetchall()
    for row in rows:
        has_any = any(v is not None for v in (row[1], row[2], row[3]))
        payload = []
        if has_any:
            payload = [{
                "name": "storage",
                "vlan_id": row[1],
                "ipv4": row[2],
                "ipv6": row[3],
            }]
        bind.execute(
            sa.text("UPDATE dpus SET extra_vlan_interfaces = :p WHERE id = :id"),
            {"p": json.dumps(payload), "id": row[0]},
        )

    # 4. Backfill any rows that didn't get touched above (no storage_*
    #    at all) with an empty list so the NOT NULL constraint holds.
    bind.execute(sa.text(
        "UPDATE project_dpu_settings SET extra_vlan_interfaces = '[]' "
        "WHERE extra_vlan_interfaces IS NULL"
    ))
    bind.execute(sa.text(
        "UPDATE dpus SET extra_vlan_interfaces = '[]' "
        "WHERE extra_vlan_interfaces IS NULL"
    ))

    # 5. Lock the new columns to NOT NULL.
    op.alter_column("project_dpu_settings", "extra_vlan_interfaces", nullable=False)
    op.alter_column("dpus", "extra_vlan_interfaces", nullable=False)

    # 6. Drop the now-unused storage_* columns.
    op.drop_column("project_dpu_settings", "storage_vlan_uplink")
    op.drop_column("project_dpu_settings", "storage_vlan_start_host")
    op.drop_column("project_dpu_settings", "storage_vlan_ipv6_subnet")
    op.drop_column("project_dpu_settings", "storage_vlan_ipv4_subnet")
    op.drop_column("project_dpu_settings", "storage_vlan_id")
    op.drop_column("dpus", "storage_vlan_ipv6")
    op.drop_column("dpus", "storage_vlan_ipv4")
    op.drop_column("dpus", "storage_vlan_id")


def downgrade() -> None:
    # Restore the storage_* columns, copy back the first list entry
    # whose name is "storage" (if any), then drop the list.
    op.add_column("project_dpu_settings", sa.Column("storage_vlan_id", sa.Integer(), nullable=True))
    op.add_column("project_dpu_settings", sa.Column("storage_vlan_ipv4_subnet", sa.String(length=64), nullable=True))
    op.add_column("project_dpu_settings", sa.Column("storage_vlan_ipv6_subnet", sa.String(length=128), nullable=True))
    op.add_column("project_dpu_settings", sa.Column("storage_vlan_start_host", sa.Integer(), nullable=True))
    op.add_column("project_dpu_settings", sa.Column("storage_vlan_uplink", sa.String(length=16), nullable=True))
    op.add_column("dpus", sa.Column("storage_vlan_id", sa.Integer(), nullable=True))
    op.add_column("dpus", sa.Column("storage_vlan_ipv4", sa.String(length=64), nullable=True))
    op.add_column("dpus", sa.Column("storage_vlan_ipv6", sa.String(length=128), nullable=True))

    bind = op.get_bind()
    for row in bind.execute(sa.text(
        "SELECT id, extra_vlan_interfaces FROM project_dpu_settings"
    )).fetchall():
        extras = row[1] or []
        if isinstance(extras, str):
            extras = json.loads(extras)
        storage = next((e for e in extras if (e.get("name") or "").lower() == "storage"), None)
        if not storage:
            continue
        bind.execute(
            sa.text(
                "UPDATE project_dpu_settings SET "
                "storage_vlan_id = :vid, storage_vlan_ipv4_subnet = :v4, "
                "storage_vlan_ipv6_subnet = :v6, storage_vlan_start_host = :sh, "
                "storage_vlan_uplink = :up WHERE id = :id"
            ),
            {
                "vid": storage.get("vlan_id"),
                "v4": storage.get("ipv4_subnet"),
                "v6": storage.get("ipv6_subnet"),
                "sh": storage.get("start_host"),
                "up": storage.get("uplink"),
                "id": row[0],
            },
        )

    for row in bind.execute(sa.text("SELECT id, extra_vlan_interfaces FROM dpus")).fetchall():
        extras = row[1] or []
        if isinstance(extras, str):
            extras = json.loads(extras)
        storage = next((e for e in extras if (e.get("name") or "").lower() == "storage"), None)
        if not storage:
            continue
        bind.execute(
            sa.text(
                "UPDATE dpus SET storage_vlan_id = :vid, "
                "storage_vlan_ipv4 = :v4, storage_vlan_ipv6 = :v6 WHERE id = :id"
            ),
            {
                "vid": storage.get("vlan_id"),
                "v4": storage.get("ipv4"),
                "v6": storage.get("ipv6"),
                "id": row[0],
            },
        )

    op.drop_column("dpus", "extra_vlan_interfaces")
    op.drop_column("project_dpu_settings", "extra_vlan_interfaces")
