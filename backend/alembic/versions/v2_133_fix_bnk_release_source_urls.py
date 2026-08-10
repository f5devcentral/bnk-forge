"""Fix BNK 2.3 source_url — replace GitHub IBM-F5 URL with clouddocs (issue #217 follow-up).

Revision ID: v2_133
Revises: v2_132
Create Date: 2026-06-09

BNK 2.3 GA was seeded with source_url pointing at the IBM-F5 GitHub
schematics repo, which is a deployment scaffold — not the canonical
release reference.  The correct URL is the F5 clouddocs release notes
page for BNK 2.3.  This migration corrects the live row and is idempotent
(WHERE clause on ga_label + product_line).

BNK 2.2 source_url is already a valid clouddocs URL; no change needed.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "v2_133"
down_revision = "v2_132"
branch_labels = None
depends_on = None

_BNK_23_CLOUDDOCS = "https://clouddocs.f5.com/bigip-next-for-kubernetes/latest/"
_BNK_23_OLD_URL = "https://github.com/IBM-F5/ibmcloud_schematics_bigip_next_for_kubernetes_2_3_flo"


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE bnk_releases "
            "SET source_url = :new_url "
            "WHERE ga_label = 'BNK 2.3 GA' "
            "  AND product_line = 'BNK' "
            "  AND source_url = :old_url"
        ).bindparams(new_url=_BNK_23_CLOUDDOCS, old_url=_BNK_23_OLD_URL)
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE bnk_releases "
            "SET source_url = :old_url "
            "WHERE ga_label = 'BNK 2.3 GA' "
            "  AND product_line = 'BNK' "
            "  AND source_url = :new_url"
        ).bindparams(new_url=_BNK_23_CLOUDDOCS, old_url=_BNK_23_OLD_URL)
    )
