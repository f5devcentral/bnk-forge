#!/usr/bin/env bash
#
# Enforce the AGENTS.md "Commit conventions" rule -- documentation is not
# enforcement (#166; bonnyr-f5 #182 r3). Shared by the ci.yml `commit-lint` job,
# `make commit-lint`, and .githooks/pre-push, so a local run == CI.
#
# FAILS a commit-message range on any of:
#
#   1. A CI-control marker anywhere in subject or body. GitHub scans the whole
#      message, so one of these sitting even in prose SUPPRESSES the workflow run
#      for that commit -- and the gates that get skipped (ShellCheck, Secret Scan,
#      Script Self-Tests) are exactly the ones that matter. Bit us on #179/#181.
#      The `skip-checks: true` commit-check trailer is caught too (bonnyr-f5 #182
#      r4): it is GitHub's documented way to suppress ALL required checks and is
#      not a bracketed token, so the fixed-string list alone would miss it.
#
#   2. A line that STARTS a major-version-bump declaration as prose rather than a
#      real Conventional Commits footer. compute_version_bump.sh bumps major on
#      any `\bBREAKING[[:space:] -]+CHANGE\b`, so a bold "**BREAKING CHANGE**"
#      heading, a bullet "- BREAKING CHANGE", a block-quoted "> BREAKING CHANGE",
#      an indented one, or a bare colon-less line all spuriously ship a major
#      release. A PROPER footer -- a line of the exact canonical form
#      `BREAKING CHANGE: <text>` (or `BREAKING-CHANGE: <text>`), column 0, no
#      markdown, single separator -- is the intended, documented, self-tested
#      mechanism and is ALLOWED. (Mid-line prose mentions are a separate,
#      pre-existing greediness in the detector itself, owned by the
#      version-tooling PRs #179/#180; this gate does not touch them.)
#
# EXEMPT: the release bot's own commits (subject `^release: `). release.yml's
# promotion commits are of the form "release: vX.Y.Z [skip ci]" -- that marker
# is DELIBERATE (release.yml's loop-guard filters them so a release push does not
# re-trigger a release). Linting them would turn the staging->main promotion
# range red -> the CI Gate fails -> release.yml's preflight refuses that SHA ->
# main never releases again (bonnyr-f5 #182 r4, INV-4/INV-28: a gate that forbids
# a token must exempt the machine identity told to emit it). The exemption is
# scoped to the exact release-bot subject prefix, so a HUMAN quoting a marker in
# any other commit is still caught.
#
# RANGE (env): "base..head" to scan. If unset/empty, defaults to
# @{upstream}..HEAD, else just the tip commit. Never scans all history (old
# release-bot commits legitimately carry the deliberate skip marker). An
# explicitly-set RANGE that does not resolve is a HARD failure -- we never
# silently fall back to scanning the tip while claiming we scanned the range
# (bonnyr-f5 #182 r4; matches secret-scan.sh's fail-closed behaviour).
set -uo pipefail

# Resolve the commit list without ever falling back to full history, and fail
# closed when an explicit RANGE is unresolvable.
if [ -n "${RANGE:-}" ]; then
  if ! commits="$(git rev-list "$RANGE" 2>/dev/null)"; then
    echo "::error::commit-lint: RANGE '$RANGE' is not a resolvable revision range -- the scan did not run"
    exit 1
  fi
elif upstream="$(git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null)"; then
  commits="$(git rev-list "${upstream}..HEAD" 2>/dev/null || true)"
  [ -z "$commits" ] && commits="$(git rev-list -1 HEAD)"
else
  commits="$(git rev-list -1 HEAD)"
fi

# CI-control markers (matched case-insensitively, as fixed strings).
markers=('[skip ci]' '[ci skip]' '[no ci]' '[skip actions]' '[actions skip]')

fail=0
n=0
while IFS= read -r sha; do
  [ -z "$sha" ] && continue
  n=$((n + 1))
  msg="$(git log -1 --format='%B' "$sha")"
  subject="$(git log -1 --format='%s' "$sha")"

  # Release-bot commits are exempt (see header): machine identity told to emit
  # the marker. A human quoting a marker in any non-release commit is still hit.
  if grep -qE '^release: ' <<< "$subject"; then
    echo "commit-lint: commit $sha ($subject) is a release-bot commit -- exempt"
    continue
  fi

  for m in "${markers[@]}"; do
    if grep -iqF -- "$m" <<< "$msg"; then
      echo "::error::commit $sha ($subject): message contains CI-control marker \"$m\" -- it would suppress the workflow run. Refer to it indirectly (e.g. \"the skip-CI marker\") or split it across backticks."
      fail=1
    fi
  done

  # GitHub's documented commit-check trailer suppresses ALL required checks; it
  # is a key:value trailer, not a bracketed token, so the fixed-string list above
  # would miss it.
  if grep -iqE '^[[:space:]]*skip-checks:[[:space:]]*true\b' <<< "$msg"; then
    echo "::error::commit $sha ($subject): message carries the 'skip-checks: true' trailer -- it suppresses all required checks. Remove it or refer to it indirectly."
    fail=1
  fi

  # A line that OPENS with a major-bump declaration -- in any of the shapes a
  # human writes (bare, bold, bulleted, block-quoted, indented) -- spuriously
  # majors a release, because the detector fires on the token anywhere.
  while IFS= read -r line; do
    # Peel a leading run of markdown/quote/whitespace/bold so we judge the line
    # by the shape a human wrote, not just a bare column-0 token.
    stripped="$(sed -E 's/^[[:space:]]*([>*+-][[:space:]]*)*//' <<< "$line")"
    if grep -qE '^BREAKING[[:space:] -]+CHANGE' <<< "$stripped"; then
      # ...allowed ONLY as the exact canonical footer at column 0: no leading
      # prefix, no markdown, a single separator, real "<TOKEN>: <text>".
      if grep -qE '^BREAKING[ -]CHANGE: .' <<< "$line"; then
        continue
      fi
      echo "::error::commit $sha ($subject): line \"$line\" starts a BREAKING CHANGE declaration that is not a plain Conventional Commits footer -- it spuriously triggers a major release. Use a real footer 'BREAKING CHANGE: <description>' at column 0 or reword (e.g. lowercase 'breaking-change')."
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
