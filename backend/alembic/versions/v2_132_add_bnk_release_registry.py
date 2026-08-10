"""Add bnk_releases table with seeded grounded release matrix (issue #217).

Revision ID: v2_132
Revises: v2_131
Create Date: 2026-06-09

New table: bnk_releases
  id, ga_label, product_line, manifest_version (nullable),
  flo_version_prefix (nullable), flo_version_min (nullable),
  flo_version_max (nullable), min_k8s (nullable), max_k8s (nullable),
  release_date (nullable), source_type, source_url (nullable),
  notes (nullable), is_active, created_at, updated_at.

Seeded rows (grounded sources):
  BNK 2.3 GA  — flo_prefix 2.21, manifest 2.3.0
  BNK 2.2 GA  — flo_prefix 2.9,  manifest 2.2
  BNK 2.1.1   — flo_prefix 1.198, manifest 2.1.1
  BNK 2.1.0   — flo_prefix 1.198, manifest 2.1.0
  BNK 2.0     — flo_prefix 1.197, manifest 2.0.0
"""

from datetime import datetime, timezone

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "v2_132"
down_revision = "v2_131"
branch_labels = None
depends_on = None

# ---------------------------------------------------------------------------
# Grounded release matrix seed data
# Each dict maps column name → value.  source_url is the canonical reference.
# ---------------------------------------------------------------------------

_SEED_ROWS = [
    {
        "ga_label": "BNK 2.3 GA",
        "product_line": "BNK",
        "manifest_version": "2.3.0",
        "flo_version_prefix": "2.21",
        "flo_version_min": "2.21.0",
        "flo_version_max": "2.22.0",
        "min_k8s": "1.30",
        "max_k8s": "1.31",
        "release_date": None,
        "source_type": "clouddocs",
        "source_url": "https://clouddocs.f5.com/bigip-next-for-kubernetes/latest/",
        "notes": (
            "IBM-F5 manifest 2.3.0 + live-observed FLO 2.21.13 on a known-2.3 cluster. "
            "FLO scheme changed to 2.x.x at BNK 2.2+."
        ),
        "is_active": True,
    },
    {
        "ga_label": "BNK 2.2 GA",
        "product_line": "BNK",
        "manifest_version": "2.2",
        "flo_version_prefix": "2.9",
        "flo_version_min": "2.0.0",
        "flo_version_max": "2.20.0",
        "min_k8s": "1.30",
        "max_k8s": "1.30",
        "release_date": None,
        "source_type": "clouddocs",
        "source_url": "https://clouddocs.f5.com/cloud/bigip-next-for-k8s/2.2/",
        "notes": (
            "docs/DPU_DEPLOY_ANALYSIS.md (cites clouddocs BNK 2.2) + live-observed FLO 2.9.27. "
            "FLO 2.x.x scheme introduced at BNK 2.2."
        ),
        "is_active": True,
    },
    {
        "ga_label": "BNK 2.1.1",
        "product_line": "BNK",
        "manifest_version": "2.1.1",
        "flo_version_prefix": "1.198",
        "flo_version_min": "1.198.0",
        "flo_version_max": "1.199.0",
        "min_k8s": "1.26",
        "max_k8s": "1.29",
        "release_date": None,
        "source_type": "clouddocs",
        "source_url": "https://clouddocs.f5.com/cloud/bigip-next-for-k8s/2.1/",
        "notes": (
            "docs/DPU_DEPLOY_ANALYSIS.md + existing codebase tests. "
            "FLO v1.198.x (legacy v1.19x.x scheme). Covers both 2.1.0 and 2.1.1."
        ),
        "is_active": True,
    },
    {
        "ga_label": "BNK 2.1.0",
        "product_line": "BNK",
        "manifest_version": "2.1.0",
        "flo_version_prefix": "1.198",
        "flo_version_min": "1.198.0",
        "flo_version_max": "1.199.0",
        "min_k8s": "1.26",
        "max_k8s": "1.29",
        "release_date": None,
        "source_type": "clouddocs",
        "source_url": "https://clouddocs.f5.com/cloud/bigip-next-for-k8s/2.1/",
        "notes": "clouddocs BNK 2.1 GA. Shares FLO v1.198.x range with 2.1.1.",
        "is_active": True,
    },
    {
        "ga_label": "BNK 2.0",
        "product_line": "BNK",
        "manifest_version": "2.0.0",
        "flo_version_prefix": "1.197",
        "flo_version_min": "1.0.0",
        "flo_version_max": "1.198.0",
        "min_k8s": "1.25",
        "max_k8s": "1.28",
        "release_date": None,
        "source_type": "clouddocs",
        "source_url": "https://clouddocs.f5.com/cloud/bigip-next-for-k8s/2.0/",
        "notes": "clouddocs 2.0 release notes. FLO v1.197.x / v1.7.8 era (SPK era).",
        "is_active": True,
    },
]


def upgrade() -> None:
    table = op.create_table(
        "bnk_releases",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ga_label", sa.String(100), nullable=False),
        sa.Column("product_line", sa.String(50), nullable=False, server_default="BNK"),
        sa.Column("manifest_version", sa.String(100), nullable=True),
        sa.Column("flo_version_prefix", sa.String(50), nullable=True),
        sa.Column("flo_version_min", sa.String(50), nullable=True),
        sa.Column("flo_version_max", sa.String(50), nullable=True),
        sa.Column("min_k8s", sa.String(20), nullable=True),
        sa.Column("max_k8s", sa.String(20), nullable=True),
        sa.Column("release_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_type", sa.String(30), nullable=False, server_default="manual"),
        sa.Column("source_url", sa.Text, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_index("idx_bnk_release_ga_label", "bnk_releases", ["ga_label"])
    op.create_index("idx_bnk_release_flo_prefix", "bnk_releases", ["flo_version_prefix"])
    op.create_index("idx_bnk_release_active", "bnk_releases", ["is_active"])

    now = datetime.now(timezone.utc)  # noqa: UP017 (Python 3.9 compat)
    op.bulk_insert(
        table,
        [
            {**row, "created_at": now, "updated_at": now}
            for row in _SEED_ROWS
        ],
    )


def downgrade() -> None:
    op.drop_index("idx_bnk_release_active", table_name="bnk_releases")
    op.drop_index("idx_bnk_release_flo_prefix", table_name="bnk_releases")
    op.drop_index("idx_bnk_release_ga_label", table_name="bnk_releases")
    op.drop_table("bnk_releases")
