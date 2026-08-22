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
#   2. A line SHAPED like a BREAKING CHANGE marker that the release detectors
#      would MISS (bonnyr-f5 #193 M1). Rule 2 is the exact COMPLEMENT of the
#      detector: it reuses _looks_like_breaking_marker + _is_breaking_body from
#      scripts/lib/breaking-change-detect.sh (the SAME predicate the bump and the
#      note use) rather than a second hand-written regex. A marker-shaped line
#      that is NOT positioned as a real footer (needs a blank line before it, or a
#      colon after a preceding trailer) does not trigger the intended major bump,
#      so a human who typed a break there ships it silently as a patch. The gate
#      flags it BEFORE it becomes an unamendable commit -- including
#      `inputs.release_notes`, which release.yml lints through this script before
#      interpolating it into the release commit/tag.
#
#      The round-1 rule 2 did the OPPOSITE and was wrong in both directions
#      (bonnyr-f5 #193 M1): it REJECTED dash-bullet `- BREAKING CHANGE:` and
#      markdown-bold `**BREAKING CHANGE:**` footers -- shapes the detectors
#      deliberately ACCEPT (positive fixtures in compute/extract self-tests) --
#      and rejected an INDENTED line the detectors IGNORE, with a false "spuriously
#      triggers a major" message. Those three shapes are now negative fixtures in
#      the self-test below (must NOT be flagged).
#
# EXEMPTIONS (both rules, bonnyr-f5 #193 B3/M1): a commit is exempt when it is
# unamendable machine history, keyed on properties the committing client cannot
# forge. Rule 2 gets the SAME exemption as rule 1 (the unamendability argument is
# identical -- a mis-anchored marker in an already-merged squash body cannot be
# reworded, and inputs.release_notes is linted before it is minted):
#
#   (a) the release bot's OWN release commit -- subject EXACTLY matching
#       `^release: vX.Y.Z ... [skip ci]` (a version AND the trailing skip marker
#       it appends). This is release.yml's own loop-guard fingerprint
#       (release.yml:131), shared here as _is_release_bot_subject rather than a
#       second, looser predicate. The round-1 `^release: ` prefix match was
#       SELF-SETTABLE -- strictly more spoofable than the committer identity it
#       replaced: any human could prepend `release: ` to self-exempt a marker. The
#       version+trailing-marker fingerprint catches a spoofed `release: <prose>
#       [skip ci]` subject (no version) while still exempting a real release commit.
#
#   (b) already-merged, unamendable history -- a commit already reachable from the
#       PRE-PUSH state of the branch (github.event.before / the BASE of the scanned
#       range), NOT the post-push tip. Round-1 evaluated this against the
#       post-push origin/main|origin/staging, which ALREADY contain every commit in
#       the range once the push has landed -- so the exemption fired for EVERY
#       commit and rule 1 never caught a `[skip ci]` on a push to main/staging, the
#       exact events it protects (bonnyr-f5 #193 B3). Anchoring to the range BASE
#       exempts a commit that existed BEFORE this push but lints a NEW commit the
#       push introduces. Fails CLOSED: no resolvable base => treated as NOT-merged
#       and fully linted.
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

# The BREAKING CHANGE predicate is single-sourced and shared with the bump/note
# detectors (INV-15). Resolve from THIS script's directory, not cwd.
# shellcheck source=scripts/lib/breaking-change-detect.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/breaking-change-detect.sh"

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

# The BASE of the scanned range == the pre-push state to test already-merged
# against (bonnyr-f5 #193 B3). Prefer an explicit BEFORE (github.event.before);
# otherwise derive it from RANGE's left-hand side ("base..head" -> "base"). Empty
# when there is no base (default/tip scan) -> the exemption fails closed.
BEFORE="${BEFORE:-}"
if [ -z "$BEFORE" ] && [ -n "${RANGE:-}" ] && [[ "$RANGE" == *..* ]]; then
  BEFORE="${RANGE%%..*}"
fi

# CI-control markers (matched case-insensitively, as fixed strings).
markers=('[skip ci]' '[ci skip]' '[no ci]' '[skip actions]' '[actions skip]')

fail=0

# Is <sha> already reachable from the PRE-PUSH base -- i.e. it existed before this
# push, so it is unamendable and was linted when it first landed? Keyed on a git
# FACT the committing client cannot forge (unlike committer name/email). Fails
# CLOSED: an unresolvable/empty base means "not merged" -> the commit is fully
# linted (bonnyr-f5 #193 B3).
_already_merged() {
  local sha="$1"
  [ -n "$BEFORE" ] || return 1
  git rev-parse --verify --quiet "${BEFORE}^{commit}" >/dev/null 2>&1 || return 1
  git merge-base --is-ancestor "$sha" "$BEFORE" 2>/dev/null
}

# Is <subject> the release bot's OWN release commit? EXACTLY release.yml's
# loop-guard fingerprint (release.yml:131): a version AND the trailing [skip ci]
# marker the bot itself appends. A human `release: <prose> [skip ci]` (no version)
# is NOT exempt; only a real minted release commit is (bonnyr-f5 #193 B3).
_is_release_bot_subject() {
  grep -qE '^release: v[0-9]+\.[0-9]+\.[0-9]+' <<< "$1" \
    && grep -qE '\[skip ci\]$' <<< "$1"
}

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
# hit. The exact COMPLEMENT of the detectors (bonnyr-f5 #193 M1): if the body is
# ALREADY a real breaking footer (_is_breaking_body true) there is nothing to warn
# about -- dash-bullet/markdown-bold footers pass straight through. Otherwise a
# line SHAPED like a marker (_looks_like_breaking_marker, the SAME is_marker shape
# the detector uses) is one the detectors would MISS: it will NOT trigger the
# intended major bump, so flag it. Indented lines are not marker-shaped here, so
# the detector-ignored indented case is not flagged either.
_lint_under_detected() {
  local label="$1" msg="$2" line
  _is_breaking_body "$msg" && return 0
  while IFS= read -r line; do
    if _looks_like_breaking_marker "$line"; then
      echo "::error::${label}: line \"$line\" is shaped like a BREAKING CHANGE marker but is not positioned where the release detectors recognise a footer, so it would NOT trigger the intended major bump. Put it at the START of a paragraph (a blank line before it) as 'BREAKING CHANGE: <description>', or fold it onto a trailer with a colon -- or reword if no break is intended."
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

  # BOTH rules share ONE exemption gate (bonnyr-f5 #193 B3/M1): unamendable
  # machine history (the release-bot's own commit, or a commit already in the
  # pre-push base) is skipped entirely; every NEW commit this push introduces is
  # linted through both rules.
  exempt=0
  reason=""
  if _is_release_bot_subject "$subject"; then
    exempt=1; reason="release-bot commit (version + trailing skip marker)"
  elif _already_merged "$sha"; then
    exempt=1; reason="already reachable from the pre-push base ${BEFORE} (unamendable)"
  fi

  if [ "$exempt" = 1 ]; then
    echo "commit-lint: commit $sha ($subject) is exempt ($reason)"
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
