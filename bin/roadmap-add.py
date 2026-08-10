#!/usr/bin/env python3
"""Append a roadmap item to docs/roadmap.yaml, then regenerate the docs.

Usage:
    python bin/roadmap-add.py --section <section-id> --title "..." \\
        --status <key> [--refs "#216,PR #188"] [--note "..."] [--group "..."]

    python bin/roadmap-add.py --list-sections

Validates that --section exists and --status is a key in status_legend, then
appends to that section's `items` (preserving key order as much as practical)
and runs bin/roadmap-gen.py to rewrite docs/ROADMAP.md + docs/roadmap.html.

Requires PyYAML (repo venv at backend/.venv/bin/python has it).
"""
import argparse
import os
import subprocess
import sys

try:
    import yaml
except ImportError:
    sys.stderr.write(
        "ERROR: PyYAML not found. Run with the repo venv:\n"
        "  backend/.venv/bin/python bin/roadmap-add.py ...\n"
    )
    sys.exit(1)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
YAML_PATH = os.path.join(REPO, "docs", "roadmap.yaml")
GEN_PATH = os.path.join(REPO, "bin", "roadmap-gen.py")


def load():
    with open(YAML_PATH, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def capture_header():
    """Return the contiguous leading comment block from docs/roadmap.yaml.

    Captures the run of leading lines that start with '#' plus any blank lines
    interleaved within that block, so the self-documenting schema header can be
    re-prepended verbatim after PyYAML rewrites the file (PyYAML drops comments).
    Returns the header as a string (terminated by a newline) or "" if none.
    """
    with open(YAML_PATH, "r", encoding="utf-8") as fh:
        lines = fh.readlines()

    header = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") or stripped == "":
            header.append(line)
        else:
            break

    # Trim trailing blank lines so we control the gap before the YAML body.
    while header and header[-1].strip() == "":
        header.pop()

    if not header:
        return ""
    return "".join(header) + "\n"


def list_sections(data):
    print("Available sections (id — heading):")
    for sec in data["sections"]:
        print("  %-22s %s" % (sec["id"], sec["heading"]))


def main():
    ap = argparse.ArgumentParser(description="Append an item to docs/roadmap.yaml")
    ap.add_argument("--section", help="section id (see --list-sections)")
    ap.add_argument("--title")
    ap.add_argument("--status", help="status key (shipped/in_progress/blocked/deferred/planned)")
    ap.add_argument("--refs", default="", help='comma-separated, e.g. "#216,PR #188"')
    ap.add_argument("--note", default="")
    ap.add_argument("--group", default="")
    ap.add_argument("--list-sections", action="store_true")
    args = ap.parse_args()

    header = capture_header()
    data = load()

    if args.list_sections:
        list_sections(data)
        return

    # validate
    if not args.section or not args.title or not args.status:
        ap.error("--section, --title and --status are required (or use --list-sections)")

    sec = next((s for s in data["sections"] if s["id"] == args.section), None)
    if sec is None:
        sys.stderr.write("ERROR: unknown section '%s'.\n\n" % args.section)
        list_sections(data)
        sys.exit(2)

    if args.status not in data["status_legend"]:
        sys.stderr.write(
            "ERROR: unknown status '%s'. Valid: %s\n"
            % (args.status, ", ".join(data["status_legend"].keys()))
        )
        sys.exit(2)

    refs = [r.strip() for r in args.refs.split(",") if r.strip()]

    # build item with stable key order matching the schema
    item = {"title": args.title, "status": args.status}
    if refs:
        item["refs"] = refs
    if args.note:
        item["note"] = args.note
    if args.group:
        item["group"] = args.group

    if sec.get("items") is None:
        sec["items"] = []
    sec["items"].append(item)

    with open(YAML_PATH, "w", encoding="utf-8") as fh:
        # Re-prepend the captured comment header verbatim — PyYAML's dumper
        # strips comments, so without this the schema docs erode on every add.
        if header:
            fh.write(header)
        yaml.safe_dump(
            data, fh, sort_keys=False, allow_unicode=True, default_flow_style=False, width=4096
        )

    print("Added to section '%s':" % args.section)
    print("  title:  %s" % args.title)
    print("  status: %s" % args.status)
    if refs:
        print("  refs:   %s" % ", ".join(refs))
    if args.note:
        print("  note:   %s" % args.note)
    if args.group:
        print("  group:  %s" % args.group)
    print()

    # regenerate
    py = sys.executable
    subprocess.run([py, GEN_PATH], check=True)


if __name__ == "__main__":
    main()
