"""Tests for canonical cross-source module resolution (#90).

D-033 defines module identity as (module_source_id, path, version), but blueprint
pins carry no source, so several surfaces resolve on `path` alone. They used
three different tie-breaks:

    deploy  is_latest DESC, last_synced DESC, id DESC
    policy  is_latest ASC,  id ASC  -> last wins
    stacks  is_latest ASC,  id ASC  -> last wins

The map builds omitted `last_synced` entirely, so with two sources cataloging the
same path the secret-policy check and the deploy could bind *different* modules
-- disagreeing about which schema counts (F8).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest

from models import ModuleSource
from services.module_resolution import (
    resolve_module_row,
    resolve_module_rows_by_path,
)

PATH = "cli-bnkctl/awsbnkctl/bnk-demo"


def _source(db, name: str) -> ModuleSource:
    src = ModuleSource(
        name=name,
        source_type="git",
        url=f"https://github.com/example/{name}.git",
        branch="main",
        is_active=True,
        sync_status="success",
    )
    db.add(src)
    db.flush()
    return src


def _module(db, *, source, path=PATH, is_latest=True, last_synced=None, version="1.0.0"):
    """Use the shared factory so NOT NULL columns stay in one place."""
    from tests.factories import ModuleLibraryFactory

    return ModuleLibraryFactory(
        db,
        path=path,
        version=version,
        is_active=True,
        is_latest=is_latest,
        module_source_id=source.id,
        last_synced=last_synced,
    )


class TestOrderingsAgree:
    def test_single_and_batch_resolution_pick_the_same_row(self, db):
        """The core F8 guarantee: the map build and the point lookup must agree.

        Constructed so the two OLD orderings disagreed: source B was synced more
        recently (so deploy picked it) but source A has the higher id (so the
        last-wins map picked A).
        """
        now = datetime.now(UTC)
        src_a = _source(db, "original")
        src_b = _source(db, "fork")

        # B synced more recently, A inserted later (higher id).
        _module(db, source=src_b, last_synced=now)
        row_a = _module(db, source=src_a, last_synced=now - timedelta(days=7))

        single = resolve_module_row(db, PATH)
        batch = resolve_module_rows_by_path(db, [PATH])[PATH]

        assert single.id == batch.id, (
            "point lookup and batch map resolved different rows — this is the "
            "policy-check vs deploy disagreement in #90 F8"
        )
        # And the winner is the recently-synced one, matching deploy's ordering.
        assert single.module_source_id == src_b.id

    def test_is_latest_dominates_last_synced(self, db):
        now = datetime.now(UTC)
        src_a = _source(db, "a")
        src_b = _source(db, "b")

        latest = _module(db, source=src_a, is_latest=True, last_synced=now - timedelta(days=30))
        _module(db, source=src_b, is_latest=False, last_synced=now)

        assert resolve_module_row(db, PATH).id == latest.id
        assert resolve_module_rows_by_path(db, [PATH])[PATH].id == latest.id

    def test_null_last_synced_loses_to_a_synced_row(self, db):
        """nullslast on DESC must reverse to nullsfirst on ASC, or the map inverts."""
        now = datetime.now(UTC)
        src_a = _source(db, "never-synced")
        src_b = _source(db, "synced")

        _module(db, source=src_a, last_synced=None)
        synced = _module(db, source=src_b, last_synced=now)

        assert resolve_module_row(db, PATH).id == synced.id
        assert resolve_module_rows_by_path(db, [PATH])[PATH].id == synced.id

    def test_no_rows_resolves_to_none(self, db):
        assert resolve_module_row(db, "nope/missing") is None
        assert resolve_module_rows_by_path(db, ["nope/missing"]) == {}

    def test_empty_path_list_short_circuits(self, db):
        assert resolve_module_rows_by_path(db, []) == {}


class TestAmbiguityWarning:
    def test_warns_when_two_sources_claim_one_path(self, db, caplog):
        src_a = _source(db, "original")
        src_b = _source(db, "fork")
        _module(db, source=src_a)
        _module(db, source=src_b)

        with caplog.at_level(logging.WARNING, logger="services.module_resolution"):
            resolve_module_row(db, PATH)

        assert "Cross-source module ambiguity" in caplog.text
        assert PATH in caplog.text

    def test_silent_when_one_source_owns_the_path(self, db, caplog):
        """Multiple version rows from ONE source is normal D-033 shape, not ambiguity."""
        src = _source(db, "only")
        _module(db, source=src, version="1.0.0", is_latest=False)
        _module(db, source=src, version="2.0.0", is_latest=True)

        with caplog.at_level(logging.WARNING, logger="services.module_resolution"):
            resolve_module_row(db, PATH)

        assert "Cross-source module ambiguity" not in caplog.text

    def test_batch_resolution_warns_per_ambiguous_path(self, db, caplog):
        src_a = _source(db, "original")
        src_b = _source(db, "fork")
        _module(db, source=src_a, path="a/mod")
        _module(db, source=src_b, path="a/mod")
        _module(db, source=src_a, path="b/mod")  # unambiguous

        with caplog.at_level(logging.WARNING, logger="services.module_resolution"):
            resolve_module_rows_by_path(db, ["a/mod", "b/mod"])

        assert caplog.text.count("Cross-source module ambiguity") == 1
        assert "a/mod" in caplog.text
