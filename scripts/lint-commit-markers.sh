#!/usr/bin/env bash
#
# Enforce the AGENTS.md "Commit conventions" rule -- documentation is not
# enforcement (#166; bonnyr-f5 #182 r3). Shared by the ci.yml `commit-lint` job,
# `make commit-lint`, and .githooks/pre-push, so a local run == CI.
#
# FAILS a commit-message range (and a pending PR title / release-notes text) on:
#
#   1. A CI-control marker anywhere in subject or body. GitHub scans the whole
#      message, so one of these sitting even in prose SUPPRESSES the workflow run
#      for that commit -- and the gates that get skipped (ShellCheck, Secret Scan,
#      Script Self-Tests) are exactly the ones that matter. Bit us on #179/#181.
#      The `skip-checks: true` commit-check trailer is caught too (bonnyr-f5 #182
#      r4): it is GitHub's documented way to suppress ALL required checks and is
#      not a bracketed token, so the fixed-string list alone would miss it.
#
#   2. A DECLARATIVE `BREAKING CHANGE:` marker (the colon form) that the release
#      detectors would MISS (bonnyr-f5 #193 M1, r3 M-6b). Rule 2 is the exact
#      COMPLEMENT of the detector: it uses _under_detected_markers from
#      scripts/lib/breaking-change-detect.sh, which shares _is_breaking_body's SAME
#      anchoring logic and the SAME single-sourced marker regex the bump and the
#      note use, rather than a second hand-written regex. A `BREAKING CHANGE:` line
#      that is NOT positioned as a real footer (needs a blank line before it, or a
#      colon after a preceding trailer) does not trigger the intended major bump,
#      so a human who typed a break there ships it silently as a patch. The gate
#      flags it BEFORE it becomes an unamendable commit -- including
#      `inputs.release_notes`, which release.yml lints through this script before
#      interpolating it into the release commit/tag. The COLON is required: a
#      colonless marker-shaped line is indistinguishable from PROSE describing the
#      concept (an already-merged body reading "- BREAKING CHANGE footer in the
#      body ..."), which the detectors treat as inert; flagging it reddened
#      unamendable release history (M-6b), so it is not flagged.
#
#      The round-1 rule 2 did the OPPOSITE and was wrong in both directions
#      (bonnyr-f5 #193 M1): it REJECTED dash-bullet `- BREAKING CHANGE:` and
#      markdown-bold `**BREAKING CHANGE:**` footers -- shapes the detectors
#      deliberately ACCEPT (positive fixtures in compute/extract self-tests) --
#      and rejected an INDENTED line the detectors IGNORE, with a false "spuriously
#      triggers a major" message. Those three shapes are now negative fixtures in
#      the self-test below (must NOT be flagged).
#
# EXEMPTION (both rules): the release bot's OWN release commit -- subject EXACTLY
# matching `^release: vX.Y.Z ... [skip ci]` (a version AND the trailing skip marker
# it appends). This is release.yml's own loop-guard fingerprint (release.yml:131),
# single-sourced as _is_release_bot_subject in scripts/lib/is-release-bot-subject.sh
# and shared here rather than a second, looser predicate; the guard's inline copy
# is byte-locked to it by scripts/tests/lint-commit-markers.test.sh. This reads the
# commit SUBJECT, which the committing client controls -- it is NOT unforgeable: a
# human could hand-write `release: v0.0.1 ... [skip ci]` to self-exempt. The
# residual is low-exposure (a squash-merged PR is linted through PR_TITLE, which
# gets NO exemption; a skip-marked push to a protected branch produces no workflow
# run for the exemption to weaken), so this is honest about the trade rather than
# claiming an unforgeability it does not have.
#
# There is deliberately NO "already-merged history" exemption (bonnyr-f5 #193 r3
# M-6a). It was removed as dead code: the scanned range is `base..head`, which by
# definition EXCLUDES every commit reachable from `base`, so no commit the script
# iterates can ever be an ancestor of the range base -- the exemption could not
# fire on any real caller. It is not needed either: rule 2 now flags ONLY a
# mis-anchored DECLARATIVE `BREAKING CHANGE:` marker (the colon form), so a
# marker-shaped PROSE line in an already-merged, unamendable body no longer reds
# the push-to-main range (that was M-6b).
#
# PR TITLE (bonnyr-f5 #193 B6b): for any PR with >=2 commits GitHub's squash
# SUBJECT is the PR title (squash_title=COMMIT_OR_PR_TITLE), linted NOWHERE else --
# a `[skip ci]` in the PR title then suppresses the merged commit's workflow run.
# PR_TITLE (set by the ci.yml commit-lint job via env, never interpolated) is
# linted through BOTH rules with NO exemption: it is a PENDING subject.
#
# RELEASE NOTES (bonnyr-f5 #193 M1): LINT_MESSAGE carries arbitrary PENDING text
# (release.yml passes inputs.release_notes) linted through both rules with no
# exemption, before it becomes the release commit body + tag message.
#
# RANGE (env): "base..head" to scan. If unset/empty, defaults to
# @{upstream}..HEAD, else just the tip commit. Never scans all history (old
# release-bot commits legitimately carry the deliberate skip marker). An
# explicitly-set RANGE that does not resolve is a HARD failure -- we never
# silently fall back to scanning the tip while claiming we scanned the range
# (bonnyr-f5 #182 r4; matches secret-scan.sh's fail-closed behaviour).
set -uo pipefail

# Single-sourced predicates. Resolve from THIS script's directory, not cwd.
# The BREAKING CHANGE detector/gate is shared with the bump/note detectors (INV-15);
# the release-bot fingerprint is shared with release.yml's loop guard.
_LIBDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib"
# shellcheck source=scripts/lib/breaking-change-detect.sh
. "$_LIBDIR/breaking-change-detect.sh"
# shellcheck source=scripts/lib/is-release-bot-subject.sh
. "$_LIBDIR/is-release-bot-subject.sh"

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

# rule 1 -- CI-control MARKER checks (fixed-string markers + skip-checks trailer).
# $1=label $2=message. Sets `fail=1` on a hit.
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

# rule 2 -- UNDER-DETECTED-MARKER check. $1=label $2=message. Sets `fail=1` on a
# hit. The exact COMPLEMENT of the detectors (bonnyr-f5 #193 M1, r3 M-6b):
# _under_detected_markers (in the shared lib) prints EVERY line that is a
# DECLARATIVE `BREAKING CHANGE:` marker sitting where the detectors would MISS it
# (mis-anchored), and NEVER a line the detector already accepts as a real footer.
# So a properly-anchored footer (blank-anchored, or trailer+colon -- incl.
# dash-bullet/markdown-bold) is passed through untouched, a COLONLESS marker-shaped
# prose line the detectors treat as inert is NOT flagged (that reddened unamendable
# release history), and a SECOND mis-anchored marker later in the same body is
# reported too (no first-footer short-circuit).
_lint_under_detected() {
  local label="$1" msg="$2" line
  while IFS= read -r line; do
    [ -z "$line" ] && continue
    echo "::error::${label}: line \"$line\" is shaped like a BREAKING CHANGE marker but is not positioned where the release detectors recognise a footer, so it would NOT trigger the intended major bump. Put it at the START of a paragraph (a blank line before it) as 'BREAKING CHANGE: <description>', or fold it onto a trailer with a colon -- or reword if no break is intended."
    fail=1
  done <<< "$(_under_detected_markers "$msg")"
}

n=0
while IFS= read -r sha; do
  [ -z "$sha" ] && continue
  n=$((n + 1))
  msg="$(git log -1 --format='%B' "$sha")"
  subject="$(git log -1 --format='%s' "$sha")"

  # BOTH rules share ONE exemption: the release-bot's OWN minted release commit
  # (version + trailing skip marker). Every other commit is linted through both
  # rules (bonnyr-f5 #193 r3 M-6a: there is no already-merged exemption -- see the
  # header -- because the scanned range excludes the base by construction).
  if _is_release_bot_subject "$subject"; then
    echo "commit-lint: commit $sha ($subject) is exempt (release-bot commit: version + trailing skip marker)"
  else
    _lint_markers "commit $sha ($subject)" "$msg"
    _lint_under_detected "commit $sha ($subject)" "$msg"
  fi
done <<< "$commits"

# bonnyr-f5 #193 B6b: lint the PR TITLE, the one input UNLINTED elsewhere. A
# PENDING subject, not already-merged, so BOTH rules apply with NO exemption.
if [ -n "${PR_TITLE:-}" ]; then
  echo "commit-lint: linting PR title -- $PR_TITLE"
  _lint_markers "PR title \"$PR_TITLE\"" "$PR_TITLE"
  _lint_under_detected "PR title \"$PR_TITLE\"" "$PR_TITLE"
fi

# bonnyr-f5 #193 M1: lint arbitrary PENDING message text (release.yml passes
# inputs.release_notes here) BEFORE it becomes the release commit body + tag
# message. Both rules, no exemption.
if [ -n "${LINT_MESSAGE:-}" ]; then
  echo "commit-lint: linting pending message text (${LINT_MESSAGE_LABEL:-LINT_MESSAGE})"
  _lint_markers "${LINT_MESSAGE_LABEL:-message text}" "$LINT_MESSAGE"
  _lint_under_detected "${LINT_MESSAGE_LABEL:-message text}" "$LINT_MESSAGE"
fi

echo "commit-lint: scanned $n commit(s) in range '${RANGE:-<local default>}'${PR_TITLE:+ + PR title}${LINT_MESSAGE:+ + message text}"
if [ "$fail" -ne 0 ]; then
  echo "::error::commit-lint failed -- see markers above."
  exit 1
fi
echo "commit-lint: OK"
