"""Re-seed bf.conf templates so tmfifo_net0 uses a per-rshim IP.

The original templates baked `192.168.100.2/30` into the netplan stanza
that configures `tmfifo_net0` on the DPU. That IP is fine for a host
with a single DPU (rshim0), but on multi-DPU hosts every DPU would land
on the same address and clobber the host-side `192.168.100.1` route.

This migration replaces the literal address with the new Jinja variable
`{{ hostvars[bfb_hostname].tmfifo_dpu_ip }}` (rendered per DPU as
`192.168.{100+rshim_index}.2/30`).

Match against the unique LAG/non-LAG marker inside each template body
rather than the row's `name`, since the names have been renamed in
earlier migrations (v2_077, v2_079) and operators may have edited them.
Skip rows whose body already contains the new Jinja var so re-running
this migration is a no-op.

Revision ID: v2_084
Revises: v2_083
"""

import os

import sqlalchemy as sa

from alembic import op

revision = "v2_084"
down_revision = "v2_083"
branch_labels = None
depends_on = None


# Distinguishing markers: only the LAG template configures bond0;
# only the non-LAG template skips it. Pair each marker with the seed
# file we want to push into the matching row.
_TEMPLATES = [
    ("70-bf-lag.yaml", "bf_template_lag.conf"),
    ("70-bf-uplinks.yaml", "bf_template_nolag.conf"),
]


def _read_seed(filename: str) -> str:
    seed_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "seed_data", filename,
    )
    with open(seed_path, encoding="utf-8") as f:
        return f.read()


def upgrade() -> None:
    bind = op.get_bind()
    for marker, filename in _TEMPLATES:
        new_content = _read_seed(filename)
        # Update every row whose body matches this template family AND
        # still has the legacy hard-coded tmfifo IP. Avoids clobbering
        # rows that an operator already edited to use the new Jinja var.
        bind.execute(
            sa.text(
                "UPDATE bf_conf_templates SET content = :content "
                "WHERE content LIKE :marker "
                "AND content LIKE '%192.168.100.2/30%' "
                "AND content NOT LIKE '%tmfifo_dpu_ip%'"
            ),
            {"content": new_content, "marker": f"%{marker}%"},
        )


def downgrade() -> None:
    # The previous template content lives only in the v2_069 migration's
    # seed_data files (now updated). Operators can re-seed manually if
    # needed by reverting the seed_data directory and running the same
    # UPDATE; we don't carry a full previous-content snapshot here.
    pass
