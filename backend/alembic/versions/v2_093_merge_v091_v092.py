"""Merge v2_091 + v2_092 into a single head.

Background: PRs #72 (v2_091, module_library.validation_error) and #68
(v2_092, stack_templates.bnk_version) were both rebased onto staging tip
v2_090 and merged independently. Each chain started from v2_090, so the
result is two parallel heads on staging — alembic's single-head invariant
broke and any subsequent migration would fail the chain check.

This is a no-op merge revision: no schema changes, just rejoins the two
heads under v2_093 so the chain is linear again.
"""

revision = "v2_093"
# After the IBM-branch merge, v2_092 (kubernetes_cluster_enabled_prerequisites)
# chains linearly from v2_091, so the multi-parent tuple originally needed to
# rejoin two staging-side branches collapses to a single parent.
down_revision = "v2_092"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
