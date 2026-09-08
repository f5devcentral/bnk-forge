#!/usr/bin/env python3
"""Static migration chain validator — no database, no venv, no alembic import.

Parses revision/down_revision from migration .py files using the ast module.
Validates chain linearity, filename consistency, and sequence ordering.

Exit 0 = clean chain. Exit 1 = errors found (printed to stderr).
"""
import ast
import re
import subprocess
import sys
from pathlib import Path

VERSIONS_DIR = Path(__file__).parent.parent / "backend" / "alembic" / "versions"
FILENAME_PATTERN = re.compile(r"^v2_(\d{3})_.*\.py$")


def _parse_revision_from_source(source: str, filename: str) -> dict | None:
    """Parse revision/down_revision from Python source text using AST."""
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return None

    meta: dict = {"file": filename}
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in (
                    "revision", "down_revision", "branch_labels", "depends_on"
                ):
                    if isinstance(node.value, ast.Constant):
                        meta[target.id] = node.value.value
                    elif isinstance(node.value, (ast.Tuple, ast.List)):
                        meta[target.id] = tuple(
                            elt.value for elt in node.value.elts
                            if isinstance(elt, ast.Constant)
                        )
    if "revision" not in meta:
        return None
    return meta


def extract_revision_metadata(filepath: Path) -> dict | None:
    """Parse revision and down_revision from a migration file using AST."""
    meta = _parse_revision_from_source(filepath.read_text(), filepath.name)
    if meta is not None:
        meta["path"] = filepath
    return meta


def check_chain(versions_dir: Path) -> tuple[list[str], list[str]]:
    """Run all chain integrity checks. Returns (errors, warnings).

    Errors are fatal (exit 1).  Warnings are informational (exit 0).
    """
    errors: list[str] = []
    warnings: list[str] = []

    # Parse all files
    migrations: list[dict] = []
    for f in sorted(versions_dir.iterdir()):
        if not f.name.endswith(".py") or f.name.startswith("__"):
            continue
        meta = extract_revision_metadata(f)
        if meta:
            migrations.append(meta)

    if not migrations:
        return ["No migration files found"], []

    rev_to_meta: dict = {}
    down_rev_to_children: dict[str, list[str]] = {}

    # Check 1: No duplicate revision IDs
    for m in migrations:
        rev = m["revision"]
        if rev in rev_to_meta:
            errors.append(
                f"DUPLICATE REVISION: '{rev}' declared in both "
                f"{rev_to_meta[rev]['file']} and {m['file']}"
            )
        rev_to_meta[rev] = m

        down = m.get("down_revision")
        if down is not None:
            if isinstance(down, tuple):
                for d in down:
                    down_rev_to_children.setdefault(d, []).append(rev)
            else:
                down_rev_to_children.setdefault(down, []).append(rev)

    # Check 2: Exactly one base
    bases = [m for m in migrations if m.get("down_revision") is None]
    if len(bases) == 0:
        errors.append("NO BASE: No migration has down_revision=None")
    elif len(bases) > 1:
        errors.append(
            f"MULTIPLE BASES: {[b['file'] for b in bases]} all have down_revision=None"
        )

    # Check 3: No orphan down_revisions
    all_revisions = set(rev_to_meta.keys())
    for m in migrations:
        down = m.get("down_revision")
        if down is None:
            continue
        targets = [down] if isinstance(down, str) else list(down)
        for d in targets:
            if d not in all_revisions:
                errors.append(
                    f"ORPHAN: {m['file']} has down_revision='{d}' "
                    f"but no file declares revision='{d}'"
                )

    # Check 4: No unresolved branches.
    #
    # A revision X "branches" when more than one migration declares X as its
    # down_revision (e.g. two stacked PRs both rebase on v2_090). Alembic
    # tolerates this only if a downstream merge revision M exists with
    # down_revision = (child1, child2, ...) that re-converges the children
    # back into a single head. A branch without a resolving merge revision
    # is a hard chain break.
    #
    # Index every merge revision (those whose down_revision is a tuple of >1
    # parents) by the set of revisions they merge. A branch from X is
    # considered resolved iff some merge revision merges every child of X.
    merge_resolved_sets: list[set[str]] = [
        set(m["down_revision"])
        for m in migrations
        if isinstance(m.get("down_revision"), tuple)
        and len(m.get("down_revision") or ()) > 1
    ]
    for down_rev, children in down_rev_to_children.items():
        if len(children) > 1:
            children_set = set(children)
            resolved = any(
                children_set.issubset(merged) for merged in merge_resolved_sets
            )
            if not resolved:
                errors.append(
                    f"BRANCH: revision '{down_rev}' is down_revision for "
                    f"multiple migrations: {children} with no merge revision "
                    f"resolving them. Add a migration with "
                    f"down_revision = ({', '.join(repr(c) for c in children)},)."
                )

    # Check 5: Filename sequence number matches revision sequence number.
    # Revisions may use any of: 'v2_NNN', 'v2_NNN_short_slug', or
    # 'v2_NNN_full_description' — all are valid as long as the NNN matches
    # the filename prefix NNN.
    REV_PREFIX = re.compile(r"^v2_(\d{3})")
    for m in migrations:
        fname = m["file"]
        rev = m["revision"]
        fname_match = FILENAME_PATTERN.match(fname)
        rev_match = REV_PREFIX.match(rev) if isinstance(rev, str) else None
        if fname_match and rev_match:
            fname_seq = fname_match.group(1)
            rev_seq = rev_match.group(1)
            if fname_seq != rev_seq:
                errors.append(
                    f"FILENAME-REVISION MISMATCH: {fname} (sequence {fname_seq}) "
                    f"declares revision='{rev}' (sequence {rev_seq}) — "
                    f"filename and revision sequence numbers must match"
                )
        elif not fname_match and rev_match:
            # revision follows v2_NNN convention but filename doesn't
            seq = rev_match.group(1)
            errors.append(
                f"FILENAME CONVENTION: {fname} declares revision='{rev}' "
                f"— rename to v2_{seq}_*.py for consistency"
            )

    # Check 6: Numeric contiguity (non-fatal warning).
    # Gaps are expected when two branches independently parent the same revision
    # and one is merged first — the second branch's migrations are renumbered to
    # follow the merged head, leaving a hole in the numeric sequence.  Alembic
    # only requires graph connectivity (Checks 1-5); gaps here are informational.
    seq_numbers: list[tuple[int, str]] = []
    for m in migrations:
        rev = m["revision"]
        if rev.startswith("v2_"):
            try:
                num = int(rev.split("_")[1])
                seq_numbers.append((num, m["file"]))
            except (ValueError, IndexError):
                pass

    seq_numbers.sort()
    if seq_numbers:
        for i in range(1, len(seq_numbers)):
            prev_num, prev_file = seq_numbers[i - 1]
            curr_num, curr_file = seq_numbers[i]
            if curr_num != prev_num + 1:
                warnings.append(
                    f"SEQUENCE GAP: v2_{prev_num:03d} -> v2_{curr_num:03d} "
                    f"(missing v2_{prev_num + 1:03d}) — expected after parallel-branch merge. "
                    f"Files: {prev_file} -> {curr_file}"
                )

    return errors, warnings


def _run(args: list[str], **kwargs) -> subprocess.CompletedProcess:  # type: ignore[type-arg]
    return subprocess.run(args, capture_output=True, text=True, **kwargs)


def check_staging_collision(versions_dir: Path, base_ref: str = "origin/staging") -> list[str]:
    """Check for revision-id collisions and multi-head between this branch and base_ref.

    Returns a list of fatal error strings (empty = clean).  Skips gracefully
    (returns []) when the ref is unavailable or HEAD already contains it.
    """
    errors: list[str] = []

    # --- Guard 1: attempt a quiet fetch so CI shallow clones have the ref ----
    _run(["git", "fetch", "--no-tags", "--depth=200", "origin", "staging"],
         timeout=30)  # best-effort; ignore return code

    # --- Guard 2: verify the ref actually exists now -------------------------
    if _run(["git", "rev-parse", "--verify", base_ref]).returncode != 0:
        print(f"  (skip staging collision check: {base_ref} not available locally)")
        return []

    # --- Guard 3: skip if HEAD already contains base_ref (merged branch) -----
    if _run(["git", "merge-base", "--is-ancestor", base_ref, "HEAD"]).returncode == 0:
        print(f"  (skip staging collision check: HEAD already contains {base_ref})")
        return []

    # --- Guard 4: skip if we ARE on staging ----------------------------------
    head_ref = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    if head_ref == "staging":
        print("  (skip staging collision check: running on staging)")
        return []

    # --- Read staging's migration set via git ls-tree + git show -------------
    rel_versions = str(versions_dir.relative_to(
        Path(_run(["git", "rev-parse", "--show-toplevel"]).stdout.strip())
    ))
    ls = _run(["git", "ls-tree", "-r", "--name-only", base_ref])
    if ls.returncode != 0:
        print(f"  (skip staging collision check: cannot list {base_ref} tree)")
        return []

    staging_migrations: list[dict] = []
    for path_str in ls.stdout.splitlines():
        if not path_str.startswith(rel_versions + "/"):
            continue
        fname = path_str.split("/")[-1]
        if not fname.endswith(".py") or fname.startswith("__"):
            continue
        show = _run(["git", "show", f"{base_ref}:{path_str}"])
        if show.returncode != 0:
            continue
        meta = _parse_revision_from_source(show.stdout, fname)
        if meta:
            staging_migrations.append(meta)

    if not staging_migrations:
        return []  # nothing to compare

    # --- Build the union of staging + branch revision maps -------------------
    # Branch migrations (already parsed by check_chain; re-parse here for independence)
    branch_migrations: list[dict] = []
    for f in sorted(versions_dir.iterdir()):
        if not f.name.endswith(".py") or f.name.startswith("__"):
            continue
        meta = extract_revision_metadata(f)
        if meta:
            branch_migrations.append(meta)

    # Map: revision_id -> (down_revision, source_label)
    staging_rev_map: dict[str, tuple] = {
        m["revision"]: (m.get("down_revision"), f"staging:{m['file']}")
        for m in staging_migrations
    }
    branch_rev_map: dict[str, tuple] = {
        m["revision"]: (m.get("down_revision"), f"branch:{m['file']}")
        for m in branch_migrations
    }

    # Shared revisions (same id, same down_revision) are common ancestors — OK.
    # A collision is: same revision id, different down_revision OR different file.
    for rev_id, (b_down, b_label) in branch_rev_map.items():
        if rev_id not in staging_rev_map:
            continue
        s_down, s_label = staging_rev_map[rev_id]
        if b_down != s_down:
            errors.append(
                f"DUPLICATE REVISION across {base_ref}: '{rev_id}' has "
                f"down_revision='{s_down}' in {s_label} but "
                f"down_revision='{b_down}' in {b_label} — "
                f"rename your migration to a unique revision id"
            )

    # Build combined revision graph and count heads
    union_rev_map: dict[str, str | tuple | None] = {**{
        m["revision"]: m.get("down_revision") for m in staging_migrations
    }, **{
        m["revision"]: m.get("down_revision") for m in branch_migrations
    }}
    all_revs = set(union_rev_map.keys())
    referenced_as_parent: set[str] = set()
    for down in union_rev_map.values():
        if down is None:
            continue
        targets = (down,) if isinstance(down, str) else down
        for t in targets:
            referenced_as_parent.add(t)
    heads = [r for r in all_revs if r not in referenced_as_parent]

    if len(heads) > 1:
        heads_sorted = sorted(heads)
        errors.append(
            f"MULTIPLE HEADS in union with {base_ref}: {heads_sorted}. "
            f"Fix: merge {base_ref} into your branch and renumber your migrations "
            f"to append after {base_ref}'s head (do not reparent {base_ref}'s migrations)."
        )

    return errors


def main() -> int:
    versions_dir = VERSIONS_DIR
    if not versions_dir.is_dir():
        print(f"ERROR: {versions_dir} not found", file=sys.stderr)
        return 1

    errors, warnings = check_chain(versions_dir)
    errors += check_staging_collision(versions_dir)

    if warnings:
        for w in warnings:
            print(f"  WARNING: {w}")

    if errors:
        print(f"\n{'=' * 60}", file=sys.stderr)
        print(f"  MIGRATION CHAIN ERRORS ({len(errors)})", file=sys.stderr)
        print(f"{'=' * 60}", file=sys.stderr)
        for e in errors:
            print(f"  ✗ {e}", file=sys.stderr)
        print(f"{'=' * 60}\n", file=sys.stderr)
        return 1

    count = len([f for f in versions_dir.iterdir() if f.name.endswith(".py") and not f.name.startswith("__")])
    print(f"  Migration chain OK: {count} migrations, single linear chain ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
