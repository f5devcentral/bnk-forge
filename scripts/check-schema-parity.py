#!/usr/bin/env python3
"""Assert that ``create_all`` and the migration chain build the same schema.

``init_db.py`` provisions a fresh install with ``create_all`` + ``alembic stamp
head``: the schema is whatever the ORM declared *in that build*, while the stamp
records "every revision up to head has run". Those two agree only while the ORM
and the chain stay in step — and when they drift, the drift is frozen into every
install made from that build, in one of two directions:

  * The ORM declares an object whose migration is not in that build's chain.
    ``create_all`` builds it anyway. When a later release brings the migration
    in ABOVE the recorded stamp, the upgrade replays it onto an object that is
    already there and aborts.

  * A migration in that build's chain creates an object the ORM does not
    declare. ``create_all`` never builds it, and no upgrade ever will, because
    the stamp already claims that revision ran. It is silently absent until some
    later build's ORM selects it.

Neither is visible from head, and neither is reachable by provisioning at a
release tag and upgrading — at any tag the ORM and the chain are in step, so
there is nothing to find. The divergence is a property of a single commit, so
that is where it has to be checked: build both schemas at THIS commit and diff
them.

Usage:
    check-schema-parity.py <orm-database-url> <chain-database-url>

where the first database was provisioned by ``init_db.py`` and the second by
``alembic upgrade head`` against an empty database.
"""

from __future__ import annotations

import sys

from sqlalchemy import create_engine, text

# Bookkeeping, not schema — present in one database and not the other by design.
_IGNORED_TABLES = {"alembic_version"}

_TABLES_SQL = """
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
"""

_COLUMNS_SQL = """
SELECT table_name, column_name, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public'
"""

# Indexes are compared by their CANONICAL DEFINITION with the name stripped —
# pg_get_indexdef() renders from the catalog, so two identical indexes created by
# different means render identically, while every property that makes an index
# different is preserved: column ORDER and direction, uniqueness, the WHERE of a
# partial index, INCLUDE columns, opclass and method.
#
# Comparing names instead reports 44 differences here, nearly all the same index
# under two spellings (ix_notifications_category vs idx_notification_category,
# ix_tasks_archived vs idx_task_archived). Reconstructing a key from
# `attnum = ANY(indkey)` is what this used to do, and it silently normalised away
# the things that matter: column order (an index on (a,b) compared equal to one
# on (b,a)), partial predicates, expression columns (indkey stores 0 for those,
# which matches no pg_attribute row), and INCLUDE columns. Both of the first two
# are live in this tree — module_state_transitions differs by `at DESC` vs `at`,
# and three indexes are partial on `holding_task_id IS NOT NULL` — and neither
# was visible before.
_INDEXES_SQL = """
SELECT t.relname AS tbl,
       regexp_replace(
           pg_get_indexdef(i.oid),
           '^(CREATE (UNIQUE )?INDEX )\\S+ ON ',
           '\\1ON '
       ) AS def
FROM pg_class t
JOIN pg_index ix ON t.oid = ix.indrelid
JOIN pg_class i ON i.oid = ix.indexrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
WHERE n.nspname = 'public' AND t.relkind = 'r'
"""

# Index differences that predate this check, as "<table> :: <normalised def>".
# Anything OUTSIDE this set is new drift and fails the build. Shrink it — do not
# grow it.
_KNOWN_INDEX_DRIFT = {
    "api_tokens :: CREATE INDEX ON public.api_tokens USING btree (id)",
    "benchmark_run_groups :: CREATE INDEX ON public.benchmark_run_groups USING btree (agent_id)",
    "benchmark_run_groups :: CREATE INDEX ON public.benchmark_run_groups USING btree (proxy)",
    "benchmark_run_groups :: CREATE INDEX ON public.benchmark_run_groups USING btree (proxy_id)",
    "bf_conf_templates :: CREATE INDEX ON public.bf_conf_templates USING btree (id)",
    "bnk_deployable_release :: CREATE INDEX ON public.bnk_deployable_release USING btree (name)",
    "bnk_upgrades :: CREATE INDEX ON public.bnk_upgrades USING btree (holding_task_id) WHERE (holding_task_id IS NOT NULL)",
    "f5_credentials :: CREATE INDEX ON public.f5_credentials USING btree (name)",
    "fleet_operation_strategies :: CREATE INDEX ON public.fleet_operation_strategies USING btree (id)",
    "module_state_transitions :: CREATE INDEX ON public.module_state_transitions USING btree (module_id, at DESC)",
    "module_state_transitions :: CREATE INDEX ON public.module_state_transitions USING btree (module_id, at)",
    "project_dpu_settings :: CREATE INDEX ON public.project_dpu_settings USING btree (project_id)",
    "project_modules :: CREATE INDEX ON public.project_modules USING btree (heartbeat_at) WHERE (holding_task_id IS NOT NULL)",
    "proxy_deployments :: CREATE INDEX ON public.proxy_deployments USING btree (holding_task_id) WHERE (holding_task_id IS NOT NULL)",
}

# Nullability differences that predate this check. Anything OUTSIDE this set is
# new drift and fails the build; these are reported and tolerated so the gate
# does not start life red. Shrink it — do not grow it.
_KNOWN_NULLABILITY_DRIFT = {
    "api_tokens.created_at",
    "bf_conf_templates.created_at",
    "bf_conf_templates.updated_at",
    "proxy_migration_steps.requires_confirm",
}


def _read(url: str) -> tuple[set[str], dict[tuple[str, str], str], set[str]]:
    engine = create_engine(url)
    with engine.connect() as conn:
        tables = {r[0] for r in conn.execute(text(_TABLES_SQL))} - _IGNORED_TABLES
        columns = {
            (r[0], r[1]): r[2]
            for r in conn.execute(text(_COLUMNS_SQL))
            if r[0] not in _IGNORED_TABLES
        }
        indexes = {
            f"{r[0]} :: {r[1]}"
            for r in conn.execute(text(_INDEXES_SQL))
            if r[0] not in _IGNORED_TABLES
        }
    return tables, columns, indexes


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2

    orm_tables, orm_columns, orm_indexes = _read(sys.argv[1])
    chain_tables, chain_columns, chain_indexes = _read(sys.argv[2])

    # Only compare columns of tables both schemas have; a missing table is
    # already reported as a table difference, and listing every one of its
    # columns again buries the signal.
    shared = orm_tables & chain_tables

    orm_only_tables = sorted(orm_tables - chain_tables)
    chain_only_tables = sorted(chain_tables - orm_tables)
    orm_only_columns = sorted(
        f"{t}.{c}" for (t, c) in orm_columns if t in shared and (t, c) not in chain_columns
    )
    chain_only_columns = sorted(
        f"{t}.{c}" for (t, c) in chain_columns if t in shared and (t, c) not in orm_columns
    )
    def _in_shared(entry: str) -> bool:
        return entry.split(" :: ", 1)[0] in shared

    index_drift = {e for e in orm_indexes - chain_indexes if _in_shared(e)}
    index_drift |= {e for e in chain_indexes - orm_indexes if _in_shared(e)}
    orm_only_indexes = sorted(
        e for e in orm_indexes - chain_indexes
        if _in_shared(e) and e not in _KNOWN_INDEX_DRIFT
    )
    chain_only_indexes = sorted(
        e for e in chain_indexes - orm_indexes
        if _in_shared(e) and e not in _KNOWN_INDEX_DRIFT
    )
    known_indexes = sorted(index_drift & _KNOWN_INDEX_DRIFT)
    healed_indexes = sorted(_KNOWN_INDEX_DRIFT - index_drift)

    drifted = {
        f"{t}.{c}"
        for (t, c) in orm_columns
        if t in shared and (t, c) in chain_columns and orm_columns[(t, c)] != chain_columns[(t, c)]
    }
    new_nullability = sorted(drifted - _KNOWN_NULLABILITY_DRIFT)
    known_nullability = sorted(drifted & _KNOWN_NULLABILITY_DRIFT)
    healed_nullability = sorted(_KNOWN_NULLABILITY_DRIFT - drifted)

    # Known nullability drift is reported and tolerated so the gate does not
    # start life red; anything new fails, so the warning cannot decay into noise
    # that hides a fresh mismatch.
    if known_indexes:
        print(f"Known index drift, tolerated ({len(known_indexes)} in _KNOWN_INDEX_DRIFT):")
        for name in known_indexes:
            print(f"  - {name}")
        print()
    if healed_indexes:
        print("In _KNOWN_INDEX_DRIFT but no longer drifting — remove them so the set")
        print("keeps shrinking:")
        for name in healed_indexes:
            print(f"  - {name}")
        print()
    if known_nullability:
        print("Known nullability drift (tolerated, listed in _KNOWN_NULLABILITY_DRIFT):")
        for name in known_nullability:
            print(f"  - {name}")
        print()
    if healed_nullability:
        print("These are in _KNOWN_NULLABILITY_DRIFT but no longer drift — remove them")
        print("from the set so it keeps shrinking:")
        for name in healed_nullability:
            print(f"  - {name}")
        print()

    if not any(
        (orm_only_tables, chain_only_tables, orm_only_columns, chain_only_columns,
         orm_only_indexes, chain_only_indexes, new_nullability)
    ):
        print("create_all and the migration chain agree on every table, column and index")
        return 0

    print("create_all and the migration chain DISAGREE at this commit.")
    print("Every install provisioned from this build freezes the difference in.")
    print()

    if orm_only_tables or orm_only_columns or orm_only_indexes:
        print("Declared by the ORM, not created by the chain:")
        for name in orm_only_tables:
            print(f"  - table  {name}")
        for name in orm_only_columns:
            print(f"  - column {name}")
        for name in orm_only_indexes:
            print(f"  - index  {name}")
        print()
        if orm_only_indexes:
            print("  An ORM-only INDEX is the INV-7 shape specifically: an index appended")
            print("  to an already-applied revision is built by create_all and never by")
            print("  the chain, so every stamped install has it and no upgrade adds it.")
            print("  Fix: give it its own new revision, guarded with if_not_exists.")
            print()
        print("  create_all builds these, so a fresh install HAS them while the stamp")
        print("  sits below the revision that creates them. Whichever release brings")
        print("  that revision in, its upgrade replays it and aborts on the collision.")
        print("  Fix: add the missing migration, or make its create idempotent.")
        print()

    if chain_only_tables or chain_only_columns or chain_only_indexes:
        print("Created by the chain, not declared by the ORM:")
        for name in chain_only_tables:
            print(f"  - table  {name}")
        for name in chain_only_columns:
            print(f"  - column {name}")
        for name in chain_only_indexes:
            print(f"  - index  {name}")
        print()
        print("  create_all never builds these, and no upgrade ever will — the stamp")
        print("  already claims their revision ran. They are permanently absent from")
        print("  every install made from this build, and surface only when a later")
        print("  ORM selects them. Fix: declare them on the model, or heal them by")
        print("  inspection in a migration (see v2_152).")
        print()

    if new_nullability:
        print("Nullability differs, and is not in the known set:")
        for name in new_nullability:
            print(f"  - {name}")
        print()
        print("  A constraint relaxed by a migration but still tight in the ORM (or the")
        print("  reverse) rejects rows on one provisioning path and not the other. If")
        print("  this is genuinely pre-existing rather than new, add it to")
        print("  _KNOWN_NULLABILITY_DRIFT with a reason — but prefer fixing it.")
        print()

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
