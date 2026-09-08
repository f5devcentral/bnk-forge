"""ADR-478 P1-1: introduce bnk_deployable_release, retire bnk_version_profiles.

Revision ID: v2_144
Revises: v2_143
Create Date: 2026-07-20

New table: bnk_deployable_release
  Full deploy matrix (mirrors former bnk_version_profiles) plus:
  - is_active: Boolean
  - source_type: String(30) default 'manual'
  - bnk_release_id: Integer FK → bnk_releases.id ON DELETE SET NULL (display only)

Migration steps (upgrade):
  1. Create bnk_deployable_release
  2. Migrate existing bnk_version_profiles rows; seed 2.3.1 + any absent 2.1/2.2
  3. Update bare_metal_hosts.version_profile_id values; repoint FK
  4. Add bare_metal_deployments.deployable_release_id FK column
  5. Drop bnk_version_profiles

Downgrade: strict inverse.
"""

import json
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision = "v2_144"
down_revision = "v2_143"
branch_labels = None
depends_on = None

# ---------------------------------------------------------------------------
# Seed data (mirrors services/bare_metal/version_profiles.py SEED_RELEASES)
# ---------------------------------------------------------------------------

_SEED_ROWS = [
    {
        "name": "bnk-2.1",
        "display_name": "BNK 2.1 (GA)",
        "description": "BNK 2.1 General Availability release",
        "is_default": False,
        "is_active": True,
        "source_type": "manual",
        "bnk_manifest_version": "2.1.0",
        "bnk_cr_kind": "CNEInstance",
        "flo_version": "0.9.23",
        "k8s_version": "1.29.8",
        "doca_version": "2.7.0",
        "containerd_version": "1.7.12",
        "runc_version": "1.1.12",
        "calico_version": "3.28.0",
        "cert_manager_version": "v1.14.5",
        "gateway_api_version": "1.1.0",
        "multus_version": "4.0.2",
        "sriov_version": "1.3.0",
        "storage_class_type": "local-path",
        "storage_provisioner": "rancher.io/local-path",
        "feature_flags": '{"ipv6": false, "tmm_node_labels": true}',
    },
    {
        "name": "bnk-2.2",
        "display_name": "BNK 2.2 (GA)",
        "description": "BNK 2.2 General Availability release",
        "is_default": True,
        "is_active": True,
        "source_type": "manual",
        "bnk_manifest_version": "2.2.1-3.2226.0-0.0.511",
        "bnk_cr_kind": "CNEInstance",
        "flo_version": "v2.9.27-0.3.4",
        "k8s_version": "1.30.4",
        "doca_version": "2.9.1",
        "containerd_version": "1.7.20",
        "runc_version": "1.1.13",
        "calico_version": "3.28.1",
        "cert_manager_version": "v1.15.3",
        "gateway_api_version": "1.1.0",
        "multus_version": "4.1.0",
        "sriov_version": "1.4.0",
        "storage_class_type": "local-path",
        "storage_provisioner": "rancher.io/local-path",
        "feature_flags": '{"ipv6": false, "tmm_node_labels": true}',
    },
    {
        "name": "bnk-2.3.1",
        "display_name": "BNK 2.3.1 (GA)",
        "description": "BNK 2.3.1 General Availability release",
        "is_default": False,
        "is_active": True,
        "source_type": "manual",
        "bnk_manifest_version": "2.3.1-3.2598.3-0.0.304",
        "bnk_cr_kind": "CNEInstance",
        "flo_version": "v2.21.13-0.0.53",
        "k8s_version": "1.30.14",
        "doca_version": "3.2.0",
        "containerd_version": "1.7.23",
        "runc_version": "1.2.1",
        "calico_version": "3.28.1",
        "cert_manager_version": "v1.16.2",
        "gateway_api_version": "1.1.0",
        "multus_version": "4.1.0",
        "sriov_version": "1.4.0",
        "storage_class_type": "local-path",
        "storage_provisioner": "rancher.io/local-path",
        "feature_flags": '{"ipv6": false, "tmm_node_labels": true}',
        "_bnk_release_flo_prefix": "2.21",  # used below to resolve bnk_release_id
    },
]


def upgrade() -> None:
    bind = op.get_bind()
    now = datetime.now(UTC)

    # ------------------------------------------------------------------
    # 1. Create bnk_deployable_release
    # ------------------------------------------------------------------
    op.create_table(
        "bnk_deployable_release",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=True, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=True, server_default="1"),
        sa.Column("source_type", sa.String(30), nullable=False, server_default="manual"),
        sa.Column("bnk_release_id", sa.Integer(), sa.ForeignKey("bnk_releases.id", ondelete="SET NULL"), nullable=True),
        sa.Column("bnk_manifest_version", sa.String(50), nullable=False),
        sa.Column("bnk_cr_kind", sa.String(50), nullable=False),
        sa.Column("flo_version", sa.String(50), nullable=False),
        sa.Column("k8s_version", sa.String(50), nullable=False),
        sa.Column("doca_version", sa.String(50), nullable=False),
        sa.Column("containerd_version", sa.String(50), nullable=False),
        sa.Column("runc_version", sa.String(50), nullable=False),
        sa.Column("calico_version", sa.String(50), nullable=False),
        sa.Column("cert_manager_version", sa.String(50), nullable=False),
        sa.Column("gateway_api_version", sa.String(50), nullable=False),
        sa.Column("multus_version", sa.String(50), nullable=False),
        sa.Column("sriov_version", sa.String(50), nullable=False),
        sa.Column("storage_class_type", sa.String(50), nullable=False),
        sa.Column("storage_provisioner", sa.String(255), nullable=False),
        sa.Column("feature_flags", sa.JSON(), nullable=True),
        sa.Column("full_manifest", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.func.now()),
    )
    op.create_index("idx_bnk_deployable_release_default", "bnk_deployable_release", ["is_default"])
    op.create_index("idx_bnk_deployable_release_active", "bnk_deployable_release", ["is_active"])
    op.create_index(op.f("ix_bnk_deployable_release_name"), "bnk_deployable_release", ["name"], unique=True)
    op.create_index(op.f("ix_bnk_deployable_release_id"), "bnk_deployable_release", ["id"], unique=False)

    # ------------------------------------------------------------------
    # 2. Migrate existing bnk_version_profiles rows → bnk_deployable_release
    # ------------------------------------------------------------------
    old_rows = bind.execute(sa.text(
        "SELECT id, name, display_name, description, is_default, "
        "bnk_manifest_version, bnk_cr_kind, flo_version, k8s_version, doca_version, "
        "containerd_version, runc_version, calico_version, cert_manager_version, "
        "gateway_api_version, multus_version, sriov_version, "
        "storage_class_type, storage_provisioner, feature_flags, full_manifest "
        "FROM bnk_version_profiles"
    )).fetchall()

    old_id_to_name: dict[int, str] = {}
    for row in old_rows:
        old_id_to_name[row.id] = row.name

    # Insert migrated rows; collect old_id → new_id mapping by name
    for row in old_rows:
        bind.execute(sa.text(
            "INSERT INTO bnk_deployable_release "
            "(name, display_name, description, is_default, is_active, source_type, "
            "bnk_manifest_version, bnk_cr_kind, flo_version, k8s_version, doca_version, "
            "containerd_version, runc_version, calico_version, cert_manager_version, "
            "gateway_api_version, multus_version, sriov_version, "
            "storage_class_type, storage_provisioner, feature_flags, full_manifest, "
            "created_at, updated_at) "
            "VALUES (:name, :display_name, :description, :is_default, :is_active, :source_type, "
            ":bnk_manifest_version, :bnk_cr_kind, :flo_version, :k8s_version, :doca_version, "
            ":containerd_version, :runc_version, :calico_version, :cert_manager_version, "
            ":gateway_api_version, :multus_version, :sriov_version, "
            ":storage_class_type, :storage_provisioner, :feature_flags, :full_manifest, "
            ":created_at, :updated_at)"
        ), {
            "name": row.name,
            "display_name": row.display_name,
            "description": row.description,
            "is_default": row.is_default,
            "is_active": True,
            "source_type": "manual",
            "bnk_manifest_version": row.bnk_manifest_version,
            "bnk_cr_kind": row.bnk_cr_kind,
            "flo_version": row.flo_version,
            "k8s_version": row.k8s_version,
            "doca_version": row.doca_version,
            "containerd_version": row.containerd_version,
            "runc_version": row.runc_version,
            "calico_version": row.calico_version,
            "cert_manager_version": row.cert_manager_version,
            "gateway_api_version": row.gateway_api_version,
            "multus_version": row.multus_version,
            "sriov_version": row.sriov_version,
            "storage_class_type": row.storage_class_type,
            "storage_provisioner": row.storage_provisioner,
            "feature_flags": json.dumps(row.feature_flags) if isinstance(row.feature_flags, dict) else row.feature_flags,
            "full_manifest": json.dumps(row.full_manifest) if isinstance(row.full_manifest, dict) else row.full_manifest,
            "created_at": now,
            "updated_at": now,
        })

    # Build old-id → new-id map by name lookup
    old_to_new: dict[int, int] = {}
    for old_id, name in old_id_to_name.items():
        new_id = bind.execute(
            sa.text("SELECT id FROM bnk_deployable_release WHERE name = :name"),
            {"name": name},
        ).scalar()
        if new_id is not None:
            old_to_new[old_id] = new_id

    # Seed 2.3.1 (and 2.1/2.2 if absent — idempotent)
    # Resolve bnk_release_id for 2.3.1 from bnk_releases by flo_version_prefix
    for seed_row in _SEED_ROWS:
        existing = bind.execute(
            sa.text("SELECT id FROM bnk_deployable_release WHERE name = :name"),
            {"name": seed_row["name"]},
        ).scalar()
        if existing is not None:
            continue  # already migrated or present

        row_data = {k: v for k, v in seed_row.items() if not k.startswith("_")}

        # Resolve GA-label FK for 2.3.1
        flo_prefix = seed_row.get("_bnk_release_flo_prefix")
        if flo_prefix:
            bnk_release_id = bind.execute(
                sa.text("SELECT id FROM bnk_releases WHERE flo_version_prefix = :p"),
                {"p": flo_prefix},
            ).scalar()
            row_data["bnk_release_id"] = bnk_release_id  # may be None
        else:
            row_data["bnk_release_id"] = None

        bind.execute(sa.text(
            "INSERT INTO bnk_deployable_release "
            "(name, display_name, description, is_default, is_active, source_type, bnk_release_id, "
            "bnk_manifest_version, bnk_cr_kind, flo_version, k8s_version, doca_version, "
            "containerd_version, runc_version, calico_version, cert_manager_version, "
            "gateway_api_version, multus_version, sriov_version, "
            "storage_class_type, storage_provisioner, feature_flags, "
            "created_at, updated_at) "
            "VALUES (:name, :display_name, :description, :is_default, :is_active, :source_type, :bnk_release_id, "
            ":bnk_manifest_version, :bnk_cr_kind, :flo_version, :k8s_version, :doca_version, "
            ":containerd_version, :runc_version, :calico_version, :cert_manager_version, "
            ":gateway_api_version, :multus_version, :sriov_version, "
            ":storage_class_type, :storage_provisioner, :feature_flags, "
            ":created_at, :updated_at)"
        ), {**row_data, "created_at": now, "updated_at": now})

    # ------------------------------------------------------------------
    # 3. Repoint bare_metal_hosts.version_profile_id → bnk_deployable_release
    #
    # ORDER MATTERS on Postgres: the old FK (→ bnk_version_profiles) must be
    # dropped BEFORE the UPDATE, otherwise Postgres rejects the new catalog IDs
    # (they exist in bnk_deployable_release, not in bnk_version_profiles).
    # SQLite doesn't enforce FKs so order is less critical, but we keep the
    # drop-first sequence here too for clarity.
    # ------------------------------------------------------------------
    if bind.dialect.name == "postgresql":
        op.drop_constraint("bare_metal_hosts_version_profile_id_fkey", "bare_metal_hosts", type_="foreignkey")

    for old_id, new_id in old_to_new.items():
        bind.execute(
            sa.text("UPDATE bare_metal_hosts SET version_profile_id = :new WHERE version_profile_id = :old"),
            {"new": new_id, "old": old_id},
        )

    with op.batch_alter_table("bare_metal_hosts") as b:
        b.create_foreign_key(
            "fk_bmh_version_profile_deployable_release",
            "bnk_deployable_release",
            ["version_profile_id"],
            ["id"],
            ondelete="SET NULL",
        )

    # ------------------------------------------------------------------
    # 4. Add deployable_release_id to bare_metal_deployments
    # ------------------------------------------------------------------
    with op.batch_alter_table("bare_metal_deployments") as b:
        b.add_column(sa.Column("deployable_release_id", sa.Integer(), nullable=True))
        b.create_foreign_key(
            "fk_bmd_deployable_release",
            "bnk_deployable_release",
            ["deployable_release_id"],
            ["id"],
            ondelete="SET NULL",
        )

    # ------------------------------------------------------------------
    # 5. Drop bnk_version_profiles (now retired)
    # ------------------------------------------------------------------
    op.drop_index("idx_version_profile_default", table_name="bnk_version_profiles")
    op.drop_index(op.f("ix_bnk_version_profiles_name"), table_name="bnk_version_profiles")
    op.drop_index(op.f("ix_bnk_version_profiles_id"), table_name="bnk_version_profiles")
    op.drop_table("bnk_version_profiles")


def downgrade() -> None:
    bind = op.get_bind()
    now = datetime.now(UTC)

    # ------------------------------------------------------------------
    # 1. Recreate bnk_version_profiles (schema from v2_057)
    # ------------------------------------------------------------------
    op.create_table(
        "bnk_version_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=True),
        sa.Column("bnk_manifest_version", sa.String(length=50), nullable=False),
        sa.Column("bnk_cr_kind", sa.String(length=50), nullable=False),
        sa.Column("flo_version", sa.String(length=50), nullable=False),
        sa.Column("k8s_version", sa.String(length=50), nullable=False),
        sa.Column("doca_version", sa.String(length=50), nullable=False),
        sa.Column("containerd_version", sa.String(length=50), nullable=False),
        sa.Column("runc_version", sa.String(length=50), nullable=False),
        sa.Column("calico_version", sa.String(length=50), nullable=False),
        sa.Column("cert_manager_version", sa.String(length=50), nullable=False),
        sa.Column("gateway_api_version", sa.String(length=50), nullable=False),
        sa.Column("multus_version", sa.String(length=50), nullable=False),
        sa.Column("sriov_version", sa.String(length=50), nullable=False),
        sa.Column("storage_class_type", sa.String(length=50), nullable=False),
        sa.Column("storage_provisioner", sa.String(length=255), nullable=False),
        sa.Column("feature_flags", sa.JSON(), nullable=True),
        sa.Column("full_manifest", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index(op.f("ix_bnk_version_profiles_id"), "bnk_version_profiles", ["id"], unique=False)
    op.create_index(op.f("ix_bnk_version_profiles_name"), "bnk_version_profiles", ["name"], unique=True)
    op.create_index("idx_version_profile_default", "bnk_version_profiles", ["is_default"], unique=False)

    # ------------------------------------------------------------------
    # 2. Migrate bnk_deployable_release rows back to bnk_version_profiles
    # ------------------------------------------------------------------
    releases = bind.execute(sa.text(
        "SELECT id, name, display_name, description, is_default, "
        "bnk_manifest_version, bnk_cr_kind, flo_version, k8s_version, doca_version, "
        "containerd_version, runc_version, calico_version, cert_manager_version, "
        "gateway_api_version, multus_version, sriov_version, "
        "storage_class_type, storage_provisioner, feature_flags, full_manifest "
        "FROM bnk_deployable_release"
    )).fetchall()

    # new_release_id → restored vp_id (looked up by name after insert)
    release_to_vp: dict[int, int] = {}
    for row in releases:
        bind.execute(sa.text(
            "INSERT INTO bnk_version_profiles "
            "(name, display_name, description, is_default, "
            "bnk_manifest_version, bnk_cr_kind, flo_version, k8s_version, doca_version, "
            "containerd_version, runc_version, calico_version, cert_manager_version, "
            "gateway_api_version, multus_version, sriov_version, "
            "storage_class_type, storage_provisioner, feature_flags, full_manifest, "
            "created_at, updated_at) "
            "VALUES (:name, :display_name, :description, :is_default, "
            ":bnk_manifest_version, :bnk_cr_kind, :flo_version, :k8s_version, :doca_version, "
            ":containerd_version, :runc_version, :calico_version, :cert_manager_version, "
            ":gateway_api_version, :multus_version, :sriov_version, "
            ":storage_class_type, :storage_provisioner, :feature_flags, :full_manifest, "
            ":created_at, :updated_at)"
        ), {
            "name": row.name,
            "display_name": row.display_name,
            "description": row.description,
            "is_default": row.is_default,
            "bnk_manifest_version": row.bnk_manifest_version,
            "bnk_cr_kind": row.bnk_cr_kind,
            "flo_version": row.flo_version,
            "k8s_version": row.k8s_version,
            "doca_version": row.doca_version,
            "containerd_version": row.containerd_version,
            "runc_version": row.runc_version,
            "calico_version": row.calico_version,
            "cert_manager_version": row.cert_manager_version,
            "gateway_api_version": row.gateway_api_version,
            "multus_version": row.multus_version,
            "sriov_version": row.sriov_version,
            "storage_class_type": row.storage_class_type,
            "storage_provisioner": row.storage_provisioner,
            "feature_flags": json.dumps(row.feature_flags) if isinstance(row.feature_flags, dict) else row.feature_flags,
            "full_manifest": json.dumps(row.full_manifest) if isinstance(row.full_manifest, dict) else row.full_manifest,
            "created_at": now,
            "updated_at": now,
        })
        vp_id = bind.execute(
            sa.text("SELECT id FROM bnk_version_profiles WHERE name = :name"),
            {"name": row.name},
        ).scalar()
        if vp_id is not None:
            release_to_vp[row.id] = vp_id

    # ------------------------------------------------------------------
    # 3. Repoint bare_metal_hosts.version_profile_id → bnk_version_profiles
    #
    # Same ordering rule as upgrade: drop the current FK FIRST so Postgres
    # doesn't reject the UPDATE (new vp_id values exist in bnk_version_profiles,
    # not in bnk_deployable_release which the column currently points to).
    # ------------------------------------------------------------------
    if bind.dialect.name == "postgresql":
        op.drop_constraint("fk_bmh_version_profile_deployable_release", "bare_metal_hosts", type_="foreignkey")

    for release_id, vp_id in release_to_vp.items():
        bind.execute(
            sa.text("UPDATE bare_metal_hosts SET version_profile_id = :vp WHERE version_profile_id = :rel"),
            {"vp": vp_id, "rel": release_id},
        )

    with op.batch_alter_table("bare_metal_hosts") as b:
        b.create_foreign_key(
            "bare_metal_hosts_version_profile_id_fkey",
            "bnk_version_profiles",
            ["version_profile_id"],
            ["id"],
            ondelete="SET NULL",
        )

    # ------------------------------------------------------------------
    # 4. Remove deployable_release_id from bare_metal_deployments
    # ------------------------------------------------------------------
    if bind.dialect.name == "postgresql":
        op.drop_constraint("fk_bmd_deployable_release", "bare_metal_deployments", type_="foreignkey")
        op.drop_column("bare_metal_deployments", "deployable_release_id")
    else:
        with op.batch_alter_table("bare_metal_deployments") as b:
            b.drop_column("deployable_release_id")

    # ------------------------------------------------------------------
    # 5. Drop bnk_deployable_release (must come after FK removal above)
    # ------------------------------------------------------------------
    op.drop_index("idx_bnk_deployable_release_active", table_name="bnk_deployable_release")
    op.drop_index("idx_bnk_deployable_release_default", table_name="bnk_deployable_release")
    op.drop_index(op.f("ix_bnk_deployable_release_name"), table_name="bnk_deployable_release")
    op.drop_index(op.f("ix_bnk_deployable_release_id"), table_name="bnk_deployable_release")
    op.drop_table("bnk_deployable_release")
