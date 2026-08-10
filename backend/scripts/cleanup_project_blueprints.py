#!/usr/bin/env python3
"""Clean up blueprint artifacts for a project.

Deletes stacks, modules, tasks, and auto-registered K8s clusters.
Preserves: BareMetalHost, ProjectDpuSettings, ProjectSecrets, Project record.

Usage:
    docker exec bnk-forge-backend python scripts/cleanup_project_blueprints.py <project_id>
    docker exec bnk-forge-backend python scripts/cleanup_project_blueprints.py <project_id> --force
"""
import json
import sys


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/cleanup_project_blueprints.py <project_id> [--force]")
        sys.exit(1)

    project_id = int(sys.argv[1])
    force = "--force" in sys.argv

    from sqlalchemy import text

    from database import SessionLocal

    db = SessionLocal()

    try:
        # Verify project exists
        proj = db.execute(text("SELECT id, name FROM projects WHERE id = :pid"), {"pid": project_id}).fetchone()
        if not proj:
            print(f"Project {project_id} not found")
            sys.exit(1)

        print(f"=== Cleanup blueprint artifacts for project: {proj[1]} (id={proj[0]}) ===\n")

        # Gather what will be deleted
        stacks = db.execute(
            text("SELECT id, name, status FROM stack_instances WHERE project_id = :pid"),
            {"pid": project_id},
        ).fetchall()

        # Collect all module IDs from stacks (deployed_modules is JSON array)
        all_module_ids: set[int] = set()
        for s in stacks:
            mods_json = db.execute(
                text("SELECT deployed_modules FROM stack_instances WHERE id = :sid"),
                {"sid": s[0]},
            ).fetchone()
            if mods_json and mods_json[0]:
                ids = json.loads(mods_json[0]) if isinstance(mods_json[0], str) else mods_json[0]
                all_module_ids.update(ids)

        # Also find orphaned modules not in any stack
        orphan_mods = db.execute(
            text("SELECT id FROM project_modules WHERE project_id = :pid"),
            {"pid": project_id},
        ).fetchall()
        all_module_ids.update(r[0] for r in orphan_mods)

        task_count = 0
        if all_module_ids:
            id_list = ",".join(str(i) for i in all_module_ids)
            task_count = db.execute(text(f"SELECT COUNT(*) FROM tasks WHERE module_id IN ({id_list})")).scalar() or 0

        # Also count orphan tasks by project; take the larger of the two counts
        orphan_task_count = db.execute(
            text("SELECT COUNT(*) FROM tasks WHERE project_id = :pid"),
            {"pid": project_id},
        ).scalar() or 0
        task_count = max(task_count, orphan_task_count)

        clusters = db.execute(
            text("SELECT id, name FROM kubernetes_clusters WHERE project_id = :pid"),
            {"pid": project_id},
        ).fetchall()

        # Show summary
        print(f"  Stacks:    {len(stacks)}")
        for s in stacks:
            print(f"    [{s[0]}] {s[1]} ({s[2]})")
        print(f"  Modules:   {len(all_module_ids)}")
        print(f"  Tasks:     {task_count}")
        print(f"  Clusters:  {len(clusters)}")
        for c in clusters:
            print(f"    [{c[0]}] {c[1]}")
        print()

        # Preserved
        host_count = (
            db.execute(
                text("SELECT COUNT(*) FROM bare_metal_hosts WHERE project_id = :pid"),
                {"pid": project_id},
            ).scalar()
            or 0
        )
        settings_count = (
            db.execute(
                text("SELECT COUNT(*) FROM project_dpu_settings WHERE project_id = :pid"),
                {"pid": project_id},
            ).scalar()
            or 0
        )
        secret_count = (
            db.execute(
                text("SELECT COUNT(*) FROM project_secrets WHERE project_id = :pid"),
                {"pid": project_id},
            ).scalar()
            or 0
        )
        print(f"  Preserved: {host_count} hosts, {settings_count} DPU settings, {secret_count} secrets")
        print()

        if not stacks and not all_module_ids and not clusters:
            print("Nothing to clean up.")
            sys.exit(0)

        if not force:
            answer = input("Proceed? [y/N] ").strip().lower()
            if answer != "y":
                print("Aborted.")
                sys.exit(0)

        # Delete in FK order: tasks → modules → stacks → clusters
        print("Deleting...")

        # 1. Tasks (by project_id covers everything)
        deleted_tasks = (
            db.execute(
                text("DELETE FROM tasks WHERE project_id = :pid"),
                {"pid": project_id},
            ).rowcount
            or 0
        )
        print(f"  Tasks: {deleted_tasks}")

        # 2. Modules
        deleted_mods = (
            db.execute(
                text("DELETE FROM project_modules WHERE project_id = :pid"),
                {"pid": project_id},
            ).rowcount
            or 0
        )
        print(f"  Modules: {deleted_mods}")

        # 3. Stacks
        deleted_stacks = (
            db.execute(
                text("DELETE FROM stack_instances WHERE project_id = :pid"),
                {"pid": project_id},
            ).rowcount
            or 0
        )
        print(f"  Stacks: {deleted_stacks}")

        # 4. Clusters
        deleted_clusters = (
            db.execute(
                text("DELETE FROM kubernetes_clusters WHERE project_id = :pid"),
                {"pid": project_id},
            ).rowcount
            or 0
        )
        print(f"  Clusters: {deleted_clusters}")

        db.commit()
        print("\nDone.")

    except Exception as e:
        db.rollback()
        print(f"Error: {e}")
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
