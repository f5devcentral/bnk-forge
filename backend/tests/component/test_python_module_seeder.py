"""Tests for services.python_module_seeder — bridge from Python module
registry into ModuleLibrary."""

from models import ModuleLibrary
from modules import get_module_registry
from services.python_module_seeder import (
    PYTHON_REGISTRY_GIT_SOURCE,
    seed_python_modules,
)


class TestSeedPythonModules:
    def test_seeds_every_registry_entry_on_first_run(self, db):
        registry = get_module_registry()
        assert registry, "registry should expose at least one bare-metal module"

        created, updated = seed_python_modules(db)

        assert created == len(registry)
        assert updated == 0

        rows = db.query(ModuleLibrary).filter(
            ModuleLibrary.git_source == PYTHON_REGISTRY_GIT_SOURCE
        ).all()
        seeded_paths = {row.path for row in rows}
        assert seeded_paths == set(registry.keys())

    def test_idempotent_rerun_only_updates(self, db):
        registry = get_module_registry()
        seed_python_modules(db)

        created, updated = seed_python_modules(db)

        assert created == 0
        assert updated == len(registry)

    def test_seeded_row_carries_module_metadata(self, db):
        seed_python_modules(db)

        row = db.query(ModuleLibrary).filter(
            ModuleLibrary.path == "bare-metal/probe-dpu"
        ).one()

        assert row.name == "Probe DPU State"
        assert row.category == "bare-metal"
        assert row.module_source_kind == "builtin"
        assert row.execution_engine == "ssh"
        assert row.engine_type == "ssh"
        assert row.is_active is True
        assert row.git_source == PYTHON_REGISTRY_GIT_SOURCE
