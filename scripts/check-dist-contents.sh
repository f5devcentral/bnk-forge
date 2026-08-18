#!/usr/bin/env bash
#
# Verify a `make dist` tarball ships exactly the intended files.
#
# `make dist` used to build the tarball with `cp -R dist`, which has no notion
# of .gitignore -- so anything a builder happened to have under dist/ went out
# to users, including dist/secrets/* (deliberately untracked, and exactly where
# real credentials land during a local run). See #133.
#
# The tarball manifest is now `git ls-files dist/` plus $DIST_GENERATED (the
# files the dist target generates but git does not track). This script asserts
# the built artifact matches that manifest, so a future change to the packaging
# step cannot silently start shipping local state again.
#
# Usage: DIST_GENERATED="install-guide.html" scripts/check-dist-contents.sh <tarball>

set -euo pipefail

TARBALL="${1:?usage: check-dist-contents.sh <tarball>}"
[ -f "$TARBALL" ] || { echo "✗ no such tarball: $TARBALL" >&2; exit 1; }

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

# ── Expected manifest: tracked ∪ generated ──────────────────────────────────
expected="$(mktemp)"
actual="$(mktemp)"
trap 'rm -f "$expected" "$actual"' EXIT

git ls-files -- dist/ | sed 's|^dist/||' > "$expected"
for g in ${DIST_GENERATED:-}; do echo "$g"; done >> "$expected"
sort -u -o "$expected" "$expected"

# Tarball entries are "bnk-forge-<version>/<path>"; strip the leading component
# and drop directory entries, which tar lists but the manifest does not name.
tar -tzf "$TARBALL" \
  | sed 's|^[^/]*/||' \
  | grep -v '/$' \
  | grep -v '^$' \
  | sort -u > "$actual"

status=0

# ── Hard stop: credential-bearing paths must never ship ─────────────────────
# Checked explicitly (not just via the manifest diff) so the intent survives
# even if someone later widens the manifest by mistake.
leaked="$(grep -E '^secrets/' "$actual" | grep -v '^secrets/\.gitkeep$' || true)"
if [ -n "$leaked" ]; then
  echo "✗ tarball ships files under secrets/ other than .gitkeep:" >&2
  echo "$leaked" | sed 's/^/    /' >&2
  status=1
fi

envleak="$(grep -E '(^|/)\.env($|\.)' "$actual" | grep -v '^\.env\.example$' || true)"
if [ -n "$envleak" ]; then
  echo "✗ tarball ships a .env file:" >&2
  echo "$envleak" | sed 's/^/    /' >&2
  status=1
fi

# ── Manifest diff, both directions ──────────────────────────────────────────
unexpected="$(comm -13 "$expected" "$actual")"
if [ -n "$unexpected" ]; then
  echo "✗ tarball ships files that are neither tracked nor in DIST_GENERATED:" >&2
  echo "$unexpected" | sed 's/^/    /' >&2
  echo "    (tracked set is dist/.gitignore's allowlist; add generated files to DIST_GENERATED)" >&2
  status=1
fi

missing="$(comm -23 "$expected" "$actual")"
if [ -n "$missing" ]; then
  echo "✗ tarball is missing expected files:" >&2
  echo "$missing" | sed 's/^/    /' >&2
  status=1
fi

if [ "$status" -eq 0 ]; then
  echo "  ✓ tarball contents match the manifest ($(wc -l < "$actual" | tr -d ' ') files, no secrets)"
fi
exit "$status"
