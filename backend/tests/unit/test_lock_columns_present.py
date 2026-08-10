"""Unit test: every allowed lock table has the four required lock columns in the ORM.

Checks the SQLAlchemy ORM metadata (not a live DB) so this test catches schema
drift even when the app doesn't boot. Complements the startup-time assertion in
startup_steps.py:assert_lock_columns_step().
"""

import pytest

REQUIRED_LOCK_COLUMNS = frozenset(
    {"lock_fence_token", "holding_task_id", "heartbeat_at", "lock_acquired_at"}
)

# Map table name → ORM model class (lazy-imported below)
_TABLE_TO_MODEL = {
    "project_modules": ("models.project", "ProjectModule"),
    "proxy_deployments": ("models.benchmark", "ProxyDeployment"),
    "bnk_upgrades": ("models.bnk_upgrade", "BnkUpgrade"),
}


def _get_model_columns(module_path: str, class_name: str) -> set[str]:
    """Import the ORM model and return the set of column names on its __table__."""
    import importlib
    mod = importlib.import_module(module_path)
    cls = getattr(mod, class_name)
    return {c.name for c in cls.__table__.columns}


@pytest.mark.parametrize("table", sorted(_TABLE_TO_MODEL.keys()))
def test_lock_columns_present_in_orm(table):
    """Assert the four lock columns exist on each allowed table's ORM model."""
    module_path, class_name = _TABLE_TO_MODEL[table]
    present = _get_model_columns(module_path, class_name)
    missing = REQUIRED_LOCK_COLUMNS - present
    assert not missing, (
        f"ORM model for {table!r} ({class_name}) is missing lock columns: "
        f"{sorted(missing)}. Run alembic upgrade head and update the model."
    )


def test_allowed_lock_tables_matches_orm_map():
    """_ALLOWED_LOCK_TABLES constant must exactly match the tables we test here."""
    from services.entity_lock import _ALLOWED_LOCK_TABLES
    assert _ALLOWED_LOCK_TABLES == frozenset(_TABLE_TO_MODEL.keys()), (
        "_ALLOWED_LOCK_TABLES has drifted from the ORM map in this test. "
        "Update _TABLE_TO_MODEL or _ALLOWED_LOCK_TABLES."
    )
