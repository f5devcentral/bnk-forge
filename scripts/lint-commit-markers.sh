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
# The CI-control MARKER rule (rule 1) is EXEMPT for two machine paths; the
# spurious-major rule (rule 2) is applied to EVERY commit regardless -- see below
# why the split matters (bonnyr-f5 #193 B6c).
#
#   MARKER-EXEMPT (a): the release bot's own commits (subject `^release: `).
#   release.yml's promotion commits are "release: vX.Y.Z [skip ci]" -- that marker
#   is DELIBERATE (release.yml's loop-guard filters them so a release push does
#   not re-trigger a release). Linting them would turn the staging->main promotion
#   range red -> the CI Gate fails -> release.yml's preflight refuses that SHA ->
#   main never releases again (bonnyr-f5 #182 r4, INV-4/INV-28). Scoped to the
#   exact release-bot subject prefix, so a HUMAN quoting a marker elsewhere is hit.
#
#   MARKER-EXEMPT (b): already-merged, unamendable history -- a commit already
#   reachable from origin/main or origin/staging. Its markers were linted
#   pre-merge and it CANNOT be rewritten, so re-linting it on a push serves no
#   pre-merge purpose and would wedge the promotion range. This replaces the
#   round-5 committer-identity exemption (`GitHub <noreply@github.com>` + single
#   parent), which was SPOOFABLE: any client can set the committer with
#   `git -c user.name=GitHub -c user.email=noreply@github.com commit`, so a human
#   could self-exempt a `[skip ci]` (bonnyr-f5 #193 B6a). Reachability from a
#   protected branch is a git FACT the committing client cannot forge. Fails
#   CLOSED: if neither ref is available the commit is treated as NOT-merged and
#   fully linted.
#
# rule 2 (spurious-major) is NEVER exempted: a blank-line-anchored BREAKING CHANGE
# bullet in a machine-composed / already-merged squash body majors the next
# release with no other gate anywhere, so machine identity must not clear it
# (bonnyr-f5 #193 B6c). A PROPER `BREAKING CHANGE: <text>` footer is still allowed.
#
# PR TITLE (bonnyr-f5 #193 B6b): for any PR with >=2 commits GitHub's squash
# SUBJECT is the PR title (squash_title=COMMIT_OR_PR_TITLE), which is linted
# NOWHERE else -- a `[skip ci]` in the PR title then suppresses the merged
# commit's workflow run. When PR_TITLE is set (the ci.yml commit-lint job passes
# it on the pull_request event, via env so the untrusted text is never
# interpolated into a shell command) it is linted through BOTH rules with no
# exemption, since it is a PENDING subject, not already-merged.
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

# Is <sha> already reachable from origin/main or origin/staging -- i.e. merged and
# unamendable? This is the "already-merged" property the round-5 committer-identity
# exemption actually wanted, keyed on a git FACT the committing client cannot forge
# (unlike committer name/email, which `git -c user.name=… -c user.email=…` sets).
# Fails CLOSED: an unavailable ref means "not merged" -> the commit is fully linted.
_already_merged() {
  local sha="$1" ref
  for ref in origin/main origin/staging refs/remotes/origin/main refs/remotes/origin/staging; do
    git rev-parse --verify --quiet "${ref}^{commit}" >/dev/null 2>&1 || continue
    if git merge-base --is-ancestor "$sha" "$ref" 2>/dev/null; then
      return 0
    fi
  done
  return 1
}

# rule 1 -- CI-control MARKER checks (fixed-string markers + skip-checks trailer).
# $1=label $2=message. Sets `fail=1` on a hit. Exemptible for machine paths.
_lint_markers() {
  local label="$1" msg="$2" m
  for m in "${markers[@]}"; do
    if grep -iqF -- "$m" <<< "$msg"; then
      echo "::error::${label}: message contains CI-control marker \"$m\" -- it would suppress the workflow run. Refer to it indirectly (e.g. \"the skip-CI marker\") or split it across backticks."
      fail=1
    fi
  done
  # GitHub's documented commit-check trailer suppresses ALL required checks; it is
  # a key:value trailer, not a bracketed token, so the fixed-string list misses it.
  if grep -iqE '^[[:space:]]*skip-checks:[[:space:]]*true\b' <<< "$msg"; then
    echo "::error::${label}: message carries the 'skip-checks: true' trailer -- it suppresses all required checks. Remove it or refer to it indirectly."
    fail=1
  fi
}

# rule 2 -- SPURIOUS-MAJOR check. $1=label $2=message. Sets `fail=1` on a hit.
# NEVER exempted (bonnyr-f5 #193 B6c): a line OPENING with a major-bump declaration
# -- in any shape a human writes (bare, bold, bulleted, block-quoted, indented) --
# spuriously majors a release because the detector fires on the token anywhere, and
# there is no other gate on an already-merged / machine-composed body.
_lint_spurious_major() {
  local label="$1" msg="$2" line stripped
  while IFS= read -r line; do
    # Peel a leading run of markdown/quote/whitespace/bold so we judge the line by
    # the shape a human wrote, not just a bare column-0 token.
    stripped="$(sed -E 's/^[[:space:]]*([>*+-][[:space:]]*)*//' <<< "$line")"
    if grep -qE '^BREAKING[[:space:] -]+CHANGE' <<< "$stripped"; then
      # ...allowed ONLY as the exact canonical footer at column 0: no leading
      # prefix, no markdown, a single separator, real "<TOKEN>: <text>".
      if grep -qE '^BREAKING[ -]CHANGE: .' <<< "$line"; then
        continue
      fi
      echo "::error::${label}: line \"$line\" starts a BREAKING CHANGE declaration that is not a plain Conventional Commits footer -- it spuriously triggers a major release. Use a real footer 'BREAKING CHANGE: <description>' at column 0 or reword (e.g. lowercase 'breaking-change')."
      fail=1
    fi
  done <<< "$msg"
}

n=0
while IFS= read -r sha; do
  [ -z "$sha" ] && continue
  n=$((n + 1))
  msg="$(git log -1 --format='%B' "$sha")"
  subject="$(git log -1 --format='%s' "$sha")"

  # The MARKER rule is exempt for two machine paths (see header); the SPURIOUS-
  # MAJOR rule below is applied to EVERY commit regardless (bonnyr-f5 #193 B6c).
  marker_exempt=0
  reason=""
  if grep -qE '^release: ' <<< "$subject"; then
    marker_exempt=1; reason="release-bot subject"
  elif _already_merged "$sha"; then
    marker_exempt=1; reason="already merged to origin/main or origin/staging (unamendable)"
  fi

  if [ "$marker_exempt" = 1 ]; then
    echo "commit-lint: commit $sha ($subject) is marker-exempt ($reason); the spurious-major rule still applies"
  else
    _lint_markers "commit $sha ($subject)" "$msg"
  fi
  _lint_spurious_major "commit $sha ($subject)" "$msg"
done <<< "$commits"

# bonnyr-f5 #193 B6b: lint the PR TITLE, the one input that is UNLINTED elsewhere.
# For any PR with >=2 commits GitHub's squash SUBJECT is the PR title, so a
# `[skip ci]` there suppresses the merged commit's workflow run. PR_TITLE is
# untrusted free text delivered via env (never interpolated into a command); it is
# a PENDING subject, not already-merged, so BOTH rules apply with NO exemption.
if [ -n "${PR_TITLE:-}" ]; then
  echo "commit-lint: linting PR title -- $PR_TITLE"
  _lint_markers "PR title \"$PR_TITLE\"" "$PR_TITLE"
  _lint_spurious_major "PR title \"$PR_TITLE\"" "$PR_TITLE"
fi

echo "commit-lint: scanned $n commit(s) in range '${RANGE:-<local default>}'${PR_TITLE:+ + PR title}"
if [ "$fail" -ne 0 ]; then
  echo "::error::commit-lint failed -- see markers above."
  exit 1
fi
echo "commit-lint: OK"
