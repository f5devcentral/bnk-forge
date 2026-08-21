#!/usr/bin/env bash
#
# Enforce the AGENTS.md "Commit conventions" rule -- documentation is not
# enforcement (#166; bonnyr-f5 #182 r3). Shared by the ci.yml `commit-lint` job,
# `make commit-lint`, and .githooks/pre-push, so a local run == CI.
#
# FAILS a commit-message range on either of:
#
#   1. A CI-control marker anywhere in subject or body. GitHub scans the whole
#      message, so one of these sitting even in prose SUPPRESSES the workflow run
#      for that commit -- and the gates that get skipped (ShellCheck, Secret Scan,
#      Script Self-Tests) are exactly the ones that matter. Bit us on #179/#181.
#
#   2. A line that STARTS a major-version-bump declaration as prose rather than a
#      real Conventional Commits footer. compute_version_bump.sh bumps major on
#      any `\bBREAKING<sep>CHANGE\b`, so a bold "**BREAKING CHANGE**" heading or a
#      bare colon-less "BREAKING CHANGE" line spuriously ships a major release.
#      A PROPER footer -- a line of the exact form `BREAKING CHANGE: <text>` (or
#      `BREAKING-CHANGE:`), no markdown bold -- is the intended, documented,
#      self-tested mechanism and is ALLOWED. (Mid-line prose mentions are a
#      separate, pre-existing greediness in the detector itself, owned by the
#      version-tooling PRs #179/#180; this gate does not touch them.)
#
# RANGE (env): "base..head" to scan. If unset/empty, defaults to
# @{upstream}..HEAD, else just the tip commit. Never scans all history (old
# release-bot commits legitimately carry the deliberate skip marker).
set -uo pipefail

# Resolve the commit list without ever falling back to full history.
commits=""
if [ -n "${RANGE:-}" ]; then
  commits="$(git rev-list "$RANGE" 2>/dev/null || true)"
elif upstream="$(git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null)"; then
  commits="$(git rev-list "${upstream}..HEAD" 2>/dev/null || true)"
fi
[ -z "$commits" ] && commits="$(git rev-list -1 HEAD)"

# CI-control markers (matched case-insensitively, as fixed strings).
markers=('[skip ci]' '[ci skip]' '[no ci]' '[skip actions]' '[actions skip]')

fail=0
n=0
while IFS= read -r sha; do
  [ -z "$sha" ] && continue
  n=$((n + 1))
  msg="$(git log -1 --format='%B' "$sha")"
  subject="$(git log -1 --format='%s' "$sha")"

  for m in "${markers[@]}"; do
    if grep -iqF -- "$m" <<< "$msg"; then
      echo "::error::commit $sha ($subject): message contains CI-control marker \"$m\" -- it would suppress the workflow run. Refer to it indirectly (e.g. \"the skip-CI marker\") or split it across backticks."
      fail=1
    fi
  done

  # A line that opens with a major-bump declaration...
  while IFS= read -r line; do
    if grep -qE '^(\*\*)?BREAKING[ -]CHANGE' <<< "$line"; then
      # ...is allowed ONLY as a bare, unbolded footer "BREAKING CHANGE: <text>".
      if grep -qE '^BREAKING[ -]CHANGE: .' <<< "$line"; then
        continue
      fi
      echo "::error::commit $sha ($subject): line \"$line\" starts a BREAKING CHANGE declaration that is not a plain Conventional Commits footer -- it spuriously triggers a major release. Use a real footer 'BREAKING CHANGE: <description>' or reword (e.g. lowercase 'breaking-change')."
      fail=1
    fi
  done <<< "$msg"
done <<< "$commits"

echo "commit-lint: scanned $n commit(s) in range '${RANGE:-<local default>}'"
if [ "$fail" -ne 0 ]; then
  echo "::error::commit-lint failed -- see markers above."
  exit 1
fi
echo "commit-lint: OK"
