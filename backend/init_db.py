#!/usr/bin/env python3
"""
Initialize database for fresh BNK-Forge v2 installations.

This script:
1. Creates all tables from models.py using SQLAlchemy
2. Stamps the database with the latest Alembic migration version
3. Seeds initial data (application settings, templates, etc.)

Use this for fresh installations only. Existing databases should use alembic upgrade.
"""
import sys

from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command
from database import DATABASE_URL
from models import Base


def init_database():
    """Initialize database with base schema and mark as migrated."""
    print("🔧 Initializing BNK-Forge v2 database...")

    # Create engine
    engine = create_engine(DATABASE_URL)

    # Check if database is already initialized
    inspector = inspect(engine)
    existing_tables = inspector.get_table_names()

    # entrypoint.sh only calls init_db.py when there is NO alembic_version table
    # (a "fresh database"). create_all builds the full ORM schema; stamp head
    # records it as current without replaying the incremental ALTER migrations
    # (which assume a pre-existing v1 schema and cannot run from empty — see #161
    # and the migration-roundtrip CI job notes). We DELIBERATELY keep stamp-to-head
    # here rather than running `alembic upgrade head`: the chain's base revision is
    # an ALTER TABLE that presupposes existing tables, so replaying it against a
    # create_all schema would error ("column already exists"). The masking risk the
    # issue raises is instead closed by the post-init drift guard below, which fails
    # loud if the stamped schema is actually missing anything the ORM expects.
    alembic_cfg = Config("alembic.ini")

    if existing_tables:
        print(f"⚠️  Database already has {len(existing_tables)} tables")
        print("   Existing tables:", ", ".join(existing_tables[:5]))
        if len(existing_tables) > 5:
            print(f"   ... and {len(existing_tables) - 5} more")
        print("\n   Skipping table creation (already initialized)")
    else:
        # Create all tables from models
        print("\n📊 Creating tables from models...")
        Base.metadata.create_all(bind=engine)
        print("✅ Tables created")

    # Stamp database with latest migration version
    print("\n📝 Stamping database with latest migration...")
    command.stamp(alembic_cfg, "head")
    print("✅ Database stamped with latest migration version")

    # Drift guard (#161): stamping head records "at head" without verifying the
    # schema actually matches the ORM — that masking is the root cause from the
    # issue. Verify ORM metadata matches the live DB before declaring success,
    # so a stamped-but-divergent schema fails here instead of crashing at runtime.
    from startup_steps import _detect_missing_schema

    with engine.connect() as conn:
        missing = _detect_missing_schema(conn)
    if missing:
        print("\n❌ Schema drift after init — the DB is MISSING objects the ORM expects:")
        for item in missing:
            print(f"   - {item}")
        raise RuntimeError(
            "init_db produced a schema the ORM does not match — refusing to declare success"
        )
    print("✅ Schema drift check passed (ORM matches DB)")

    # Seed rows that live inside migration `upgrade()` functions. Because
    # `create_all()` + `stamp(head)` skips the migration bodies entirely,
    # those seeds never run on a fresh install. Do them explicitly here
    # (idempotent — each checks uniqueness before inserting).
    print("\n🌱 Seeding initial data...")
    seed_initial_data(engine)
    print("✅ Initial data seeded")

    print("\n✅ Database initialization complete!")
    return True


def seed_initial_data(engine) -> None:
    """Idempotent seeds for tables that carry admin-managed catalog data.

    Mirrors the inserts in:
      - alembic/versions/v2_063_seed_initial_bluefield_image.py
      - alembic/versions/v2_069_bf_conf_templates_and_uplink.py
      - alembic/versions/v2_098_unify_bfb_doca_catalog.py

    Sourcing the bf.conf template content from
    `alembic/seed_data/*.conf` keeps a single source of truth.
    """
    import os

    from sqlalchemy import text

    bfb_filename = "bf-bundle-2.9.2-32_25.02_ubuntu-22.04_prod.bfb"
    bfb_base_url = "https://content.mellanox.com/BlueField/BFBs/Ubuntu22.04"
    bf_templates = [
        (
            "Uplinks via LAG (bond0)",
            "bf_template_lag.conf",
            "F5/Nvidia RCP RA with LACP based LAG uplinks",
        ),
        (
            "Uplinks via p0/p1 (no LAG)",
            "bf_template_nolag.conf",
            "VLAN interfaces attached p0 and/or p1 uplinks",
        ),
    ]
    seed_dir = os.path.join(os.path.dirname(__file__), "alembic", "seed_data")

    with engine.begin() as conn:
        existing = conn.execute(
            text("SELECT id FROM bluefield_software_images WHERE doca_version = :dv AND host_os = :os AND host_arch = :arch"),
            {"dv": "2.9.2", "os": "ubuntu", "arch": "arm64"},
        ).first()
        if existing is None:
            conn.execute(
                text(
                    "INSERT INTO bluefield_software_images "
                    "(image_filename, base_url, doca_version, doca_package, "
                    " host_os, host_os_version, host_arch, doca_host_url, "
                    " is_default, notes) "
                    "VALUES (:fn, :url, :dv, :dp, :host_os, :host_os_version, :host_arch, :doca_host_url, :d, :notes)"
                ),
                {
                    "fn": bfb_filename,
                    "url": bfb_base_url,
                    "dv": "2.9.2",
                    "dp": "doca-all",
                    "host_os": "ubuntu",
                    "host_os_version": "22.04",
                    "host_arch": "arm64",
                    "doca_host_url": "https://www.mellanox.com/downloads/DOCA/DOCA_v2.9.2/host/doca-host_2.9.2-012101-24.10-ubuntu2204_arm64.deb",
                    "d": True,
                    "notes": "DOCA 2.9.2 LTS — BNK 2.2 tested. Host: doca-all 2.9.2, DPU: bf-bundle 2.9.2",
                },
            )
            print(f"   • seeded bluefield image: {bfb_filename}")
        else:
            print(f"   • bluefield image already present: {bfb_filename}")

        for name, filename, description in bf_templates:
            existing = conn.execute(
                text("SELECT id FROM bf_conf_templates WHERE name = :name"),
                {"name": name},
            ).first()
            if existing is not None:
                print(f"   • bf.conf template already present: {name}")
                continue
            seed_path = os.path.join(seed_dir, filename)
            with open(seed_path, encoding="utf-8") as f:
                content = f.read()
            conn.execute(
                text(
                    "INSERT INTO bf_conf_templates "
                    "(name, description, content, matching_bnk_version) "
                    "VALUES (:name, :desc, :content, NULL)"
                ),
                {"name": name, "desc": description, "content": content},
            )
            print(f"   • seeded bf.conf template: {name}")

if __name__ == "__main__":
    try:
        success = init_database()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
