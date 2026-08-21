#!/usr/bin/env bash
# scripts/compute_version_bump.sh — Conventional-commit version bump calculator
#
# Usage:
#   compute_version_bump.sh [--since-tag <tag>] [--baseline <X.Y.Z>]
#
# Outputs (to stdout, one per line):
#   BUMP_TYPE=<major|minor|patch>
#   TARGET_VERSION=<X.Y.Z>
#   SINCE_TAG=<tag used as base>
#
# Self-test examples (run with SELF_TEST=1):
#   SELF_TEST=1 bash scripts/compute_version_bump.sh
#
# Logic:
#   1. Find the most recent FINAL release tag (vX.Y.Z, no -rc.* suffix).
#   2. Collect commit subjects since that tag.
#   3. Determine bump: BREAKING CHANGE / feat! / fix! → major; feat → minor; else → patch.
#   4. Apply bump to baseline version — the baseline is ALWAYS anchored to the
#      same tag the commit range was scanned from (SINCE_TAG), never to the
#      VERSION file.
#
# Design: tags are the single source of truth for the release baseline. The
# VERSION file is informational/derived — it reflects the last computed
# TARGET_VERSION but is never read to compute the next one, and this script
# never compares against it. It is normal (and expected) for VERSION to sit
# ahead of the last final tag until the first automated release actually
# tags the repo; after that point VERSION and the tag baseline converge and
# stay in sync going forward, since the release job writes VERSION as a
# derived artifact of the tag it just cut.
#
# Fallback when NO final tag exists at all (bootstrap / first-ever release):
#   the script REFUSES to guess a baseline by scanning all history. It exits
#   1 with an actionable message. The operator must pass --baseline <X.Y.Z>
#   explicitly to acknowledge this is intentional.
#
set -euo pipefail

# ── Argument parsing ──────────────────────────────────────────────────────────
SINCE_TAG_OVERRIDE=""
BASELINE_OVERRIDE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --since-tag) SINCE_TAG_OVERRIDE="$2"; shift 2 ;;
    --baseline)  BASELINE_OVERRIDE="$2";  shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

# ── Helpers ───────────────────────────────────────────────────────────────────
last_final_tag() {
  # Most recent tag matching vX.Y.Z exactly (no pre-release suffix).
  # `|| true` survives an empty match under `set -euo pipefail`: with zero
  # matching tags, grep exits 1, and pipefail propagates that as the
  # pipeline's status — which would otherwise trip `set -e` and abort the
  # script right here, before the caller's `-z "$SINCE_TAG"` check ever
  # runs, silently skipping the intended "no prior final tag" error message
  # below instead of reaching it with SINCE_TAG correctly empty.
  git tag -l 'v*' \
    | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' \
    | sort -V \
    | tail -1 \
    || true
}

bump_version() {
  local ver="$1" bump="$2"
  local major minor patch
  IFS='.' read -r major minor patch <<< "$ver"
  case "$bump" in
    major) echo "$((major + 1)).0.0" ;;
    minor) echo "${major}.$((minor + 1)).0" ;;
    patch) echo "${major}.${minor}.$((patch + 1))" ;;
  esac
}

# ── Breaking-change detectors ────────────────────────────────────────────────
# INV-15: _is_breaking_subject and _is_breaking_body MUST stay byte-identical to
# the copies in extract-breaking-changes.sh (#182's script-selftests asserts it).
# A marker counts as a real footer when its line is preceded by a blank line OR by
# another trailer line -- the footer block is "a blank line then a run of trailers",
# and a footer may directly follow another footer (conventional-commits' own
# canonical example, bonnyr-f5 #179 r5 Major 1). Wrapped prose (a marker after a
# PROSE line) is still rejected. _is_breaking_subject is now BANG-ONLY: the folded-
# footer-in-subject is caught by running _is_breaking_body on %B (which preserves
# the newline git folds into %s), and scanning the raw subject for the marker
# over-bumped on `docs: clarify what BREAKING CHANGE: means` (r5 Minor 1).
_is_breaking_subject() {
  grep -qE '^[A-Za-z]+(\([^)]*\))?!:' <<< "$1"
}
_is_breaking_body() {
  awk '
    BEGIN { prev_blank = 1; prev_trailer = 0 }
    /^[[:space:]]*$/ { prev_blank = 1; prev_trailer = 0; next }
    {
      is_marker  = ($0 ~ /^(\*\*)?BREAKING[ -]CHANGE/)
      is_trailer = ($0 ~ /^[A-Za-z0-9][A-Za-z0-9-]*:([[:space:]]|$)/)
      if ((prev_blank || prev_trailer) && is_marker) found = 1
      prev_blank = 0; prev_trailer = is_trailer
    }
    END { exit(found ? 0 : 1) }
  ' <<< "$1"
}

# ── Resolve baseline + since-tag ─────────────────────────────────────────────
# Skipped entirely in SELF_TEST mode: the self-test runner below exercises
# this same script recursively against isolated temp repos, so evaluating it
# here against the ambient (real) repo first is unnecessary — and now that
# the fail-safe guard below can legitimately exit 1 against real repo state,
# doing so would abort before the self-tests ever run.
if [[ "${SELF_TEST:-0}" != "1" ]]; then
if [[ -n "$SINCE_TAG_OVERRIDE" ]]; then
  SINCE_TAG="$SINCE_TAG_OVERRIDE"
else
  SINCE_TAG=$(last_final_tag)
fi

# Fail closed on an unresolvable SINCE_TAG. Without this, an unknown ref makes
# `git log <bad>..HEAD` empty (2>/dev/null || true), so BOTH the bump loop and
# the consistency guard read nothing and silently return patch -- a typo in the
# floor tag would ship a release derived from a range that was never read.
if [[ -n "$SINCE_TAG" ]] && ! git rev-parse --verify --quiet "${SINCE_TAG}^{commit}" >/dev/null; then
  echo "::error::SINCE_TAG '${SINCE_TAG}' does not resolve to a commit in this repo -- refusing to derive a version from an empty range." >&2
  exit 1
fi

# A resolvable SINCE_TAG whose range is empty (tag == HEAD) still slips through as
# patch -- a phantom duplicate release (bonnyr-f5 #179). Refuse the empty range.
if [[ -n "$SINCE_TAG" ]] && [[ -z "$(git log "${SINCE_TAG}..HEAD" --format='%H' 2>/dev/null)" ]]; then
  echo "::error::Range ${SINCE_TAG}..HEAD is empty (tag == HEAD?) -- refusing to derive a duplicate release." >&2
  exit 1
fi

if [[ -n "$BASELINE_OVERRIDE" ]]; then
  BASELINE="$BASELINE_OVERRIDE"
elif [[ -n "$SINCE_TAG" ]]; then
  # Anchor to the SAME tag the commit range above was scanned from.
  BASELINE="${SINCE_TAG#v}"
else
  echo "::error::No prior final release tag (vX.Y.Z) found and no --baseline given." >&2
  echo "Refusing to guess a baseline by scanning all history — pass --baseline <X.Y.Z> explicitly if this is genuinely the first release." >&2
  exit 1
fi

# ── Determine bump type ───────────────────────────────────────────────────────
# Conventional-commits: a breaking change is declared EITHER as `type!:` in the
# subject OR as a `BREAKING CHANGE` footer, which by definition lives in the
# body. The subject determines feat/fix. So we must read the body, not just the
# subject (%s) -- reading %s alone made the footer branch unreachable and shipped
# footer-declared breaking changes as patches (PR #177 review).
BUMP_TYPE="patch"

if [[ -z "$SINCE_TAG" ]]; then
  RANGE_HASHES=$(git log --format='%H' 2>/dev/null || true)
else
  RANGE_HASHES=$(git log "${SINCE_TAG}..HEAD" --format='%H' 2>/dev/null || true)
fi

while IFS= read -r sha; do
  [[ -z "$sha" ]] && continue
  subject=$(git log -1 --format="%s" "$sha" 2>/dev/null || true)
  # Read the FULL raw message (%B), not just %b: a footer git folded into %s
  # keeps its newline in %B, so _is_breaking_body catches a folded footer there,
  # and scanning %B (not the subject) for the marker stops subject prose from
  # over-matching (bonnyr-f5 #179 r5).
  message=$(git log -1 --format='%B' "$sha" 2>/dev/null || true)

  # Major: a `type!:` bang subject, OR a footer-anchored BREAKING CHANGE in the
  # full message. Both checks are pipe-free here-strings on purpose (PR #177
  # review): `foo | grep -q` under `set -o pipefail` takes SIGPIPE (141) when grep
  # matches early on a large body, which pipefail turns into a failed test, so
  # *finding* the marker silently kept the bump at patch. Detection lives in
  # _is_breaking_subject / _is_breaking_body (byte-identical to the extractor).
  if _is_breaking_subject "$subject" || _is_breaking_body "$message"; then
    BUMP_TYPE="major"
    break
  fi

  # Minor: feat: in the subject (type is declared in the subject, never the body).
  if [[ "$BUMP_TYPE" != "major" ]]; then
    if grep -qE '^feat(\([^)]*\))?:' <<< "$subject"; then
      BUMP_TYPE="minor"
    fi
  fi
done <<< "$RANGE_HASHES"

# ── Consistency guard (defence-in-depth) ──────────────────────────────────────
# Re-derive "is any commit in the range breaking" INDEPENDENTLY of the loop above,
# so a control-flow bug there (a bad break/assignment) can't ship a mis-versioned
# release. It iterates PER COMMIT and calls the same detectors: paragraph-initial
# body detection cannot run on concatenated %b (commit boundaries would form false
# paragraphs), and folding means the signal can be in %s -- so this reads each
# commit's %s AND %B, exactly as the loop does (bonnyr-f5 #179 r3/r4/r5).
guard_breaking=0
while IFS= read -r sha; do
  [[ -z "$sha" ]] && continue
  gsub=$(git log -1 --format="%s" "$sha" 2>/dev/null || true)
  gmsg=$(git log -1 --format='%B' "$sha" 2>/dev/null || true)
  if _is_breaking_subject "$gsub" || _is_breaking_body "$gmsg"; then
    guard_breaking=1
    break
  fi
done <<< "$RANGE_HASHES"
if [[ "$guard_breaking" -eq 1 && "$BUMP_TYPE" != "major" ]]; then
  echo "::error::Derived bump '$BUMP_TYPE' but a breaking change (type!: / folded footer subject, or a paragraph-initial BREAKING CHANGE body footer) exists in ${SINCE_TAG:-<all>}..HEAD -- refusing to ship a mis-versioned release." >&2
  exit 1
fi

# ── Compute target version ────────────────────────────────────────────────────
TARGET_VERSION=$(bump_version "$BASELINE" "$BUMP_TYPE")

# ── Output ────────────────────────────────────────────────────────────────────
echo "BUMP_TYPE=${BUMP_TYPE}"
echo "TARGET_VERSION=${TARGET_VERSION}"
echo "SINCE_TAG=${SINCE_TAG}"
fi

# ── Self-test mode ────────────────────────────────────────────────────────────
if [[ "${SELF_TEST:-0}" == "1" ]]; then
  echo ""
  echo "=== SELF-TEST ==="
  SELFTEST_FAILURES=0
  SELFTEST_ASSERTIONS=0

  # Resolve this script's own absolute path from BASH_SOURCE BEFORE unsetting
  # SELF_TEST, so the recursive invocations below can find it regardless of cwd
  # (the old "$OLDPWD/$(dirname "$0")" broke any non-cwd-relative call).
  SELFTEST_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"

  # SELF_TEST is an inherited environment variable — without unsetting it
  # here, run_test's recursive script invocations below would also enter
  # self-test mode and recurse indefinitely (fork bomb).
  unset SELF_TEST

  run_test() {
    local desc="$1" expected_bump="$2" expected_ver="$3"
    local since="$4" baseline="$5" commits_str="$6"
    local _b
    SELFTEST_ASSERTIONS=$((SELFTEST_ASSERTIONS + 1))
    # Create a temp dir with a fake git repo for deterministic testing
    local tmpdir
    tmpdir=$(mktemp -d)
    git init -q "$tmpdir"
    # Self-contained identity so the self-test runs anywhere (fresh runners,
    # no global git config).
    git -C "$tmpdir" config user.email "selftest@bnk-forge.local"
    git -C "$tmpdir" config user.name "bnk-forge self-test"
    git -C "$tmpdir" commit --allow-empty -m "initial" -q
    if [[ -n "$since" ]]; then
      git -C "$tmpdir" tag "$since"
    fi
    # Add fake commits. An entry may carry a body via "subject~~BODY~~body"
    # so tests can exercise a footer-declared BREAKING CHANGE (bodies, not
    # subjects, are where the spec puts it). Entries are comma-separated, so
    # test strings must not contain commas.
    while IFS= read -r entry; do
      [[ -z "$entry" ]] && continue
      if [[ "$entry" == *"~~BODY~~"* ]]; then
        # A body may use a literal \n escape for a real newline: entries are
        # split on newlines, so a raw one would fork into extra commits.
        _b="${entry#*~~BODY~~}"
        _b=${_b//\\n/$'\n'}
        git -C "$tmpdir" commit --allow-empty \
          -m "${entry%%~~BODY~~*}" -m "$_b" -q
      else
        # A plain entry may embed a literal \n for a FOLDED single message:
        # subject and footer on consecutive lines with NO blank between, so git
        # folds them into %s (leaving %b empty) but %B keeps the newline. One -m
        # with an embedded newline reproduces exactly that shape.
        git -C "$tmpdir" commit --allow-empty -m "${entry//\\n/$'\n'}" -q
      fi
    done <<< "$(echo "$commits_str" | tr ',' '\n')"

    # Run the version computer inside the temp repo so it scans the fake range.
    local result
    # Invoke by absolute path resolved from BASH_SOURCE, so the self-test works
    # regardless of cwd or how the script was called ($OLDPWD/$0 broke any
    # non-cwd-relative invocation -- bonnyr-f5 #179 r3 nit).
    result=$(cd "$tmpdir" && bash "$SELFTEST_SCRIPT" \
      ${since:+--since-tag "$since"} --baseline "$baseline" 2>/dev/null || true)

    local got_bump got_ver
    got_bump=$(echo "$result" | grep BUMP_TYPE | cut -d= -f2 || true)
    got_ver=$(echo "$result" | grep TARGET_VERSION | cut -d= -f2 || true)

    rm -rf "$tmpdir"

    if [[ "$got_bump" == "$expected_bump" && "$got_ver" == "$expected_ver" ]]; then
      echo "  PASS: $desc (bump=$got_bump, ver=$got_ver)"
    else
      echo "  FAIL: $desc"
      echo "        expected bump=$expected_bump ver=$expected_ver"
      echo "        got     bump=$got_bump ver=$got_ver"
      SELFTEST_FAILURES=$((SELFTEST_FAILURES + 1))
    fi
  }

  # Test 1: only fix commits → patch bump
  run_test "fix commits → patch" "patch" "1.2.4" "v1.2.3" "1.2.3" \
    "fix(auth): token refresh,fix(ui): icon alignment"

  # Test 2: feat commit → minor bump
  run_test "feat commit → minor" "minor" "1.3.0" "v1.2.3" "1.2.3" \
    "feat(api): new endpoint,fix(db): connection pool"

  # Test 3: breaking change → major bump
  run_test "breaking ! → major" "major" "2.0.0" "v1.2.3" "1.2.3" \
    "feat!: redesign API,fix(ui): icon"

  # Test 4: empty range (tag == HEAD) → the guard refuses (no output).
  run_test "empty range (tag==HEAD) → refused" "" "" "v1.2.3" "1.2.3" ""

  # Test 4b (bonnyr-f5 INV-15): a marker in SUBJECT prose only must NOT bump.
  run_test "marker in subject prose → patch" "patch" "1.2.4" "v1.2.3" "1.2.3" "docs: explain the BREAKING CHANGE footer"

  # Test 4c (r5 Minor 1): a docs subject that quotes `BREAKING CHANGE:` with a
  # colon must NOT over-bump. The old subject match was unanchored across the
  # whole subject; the detector is now bang-only, and %B is a single subject line
  # so the body anchor finds no footer either.
  run_test "docs subject quotes marker → patch" "patch" "1.2.4" "v1.2.3" "1.2.3" "docs: clarify what BREAKING CHANGE: means"

  # Test 5: BREAKING CHANGE footer in the BODY → major (the PR #177 bug: a
  # fix-subject commit whose body declares the break must still bump major).
  run_test "BREAKING CHANGE footer in body → major" "major" "2.0.0" "v1.2.3" "1.2.3" \
    "fix: harden non-root gate~~BODY~~BREAKING CHANGE: USER nonroot must become USER 65532"

  # Test 6: lowercase "breaking change" in body prose must NOT trigger major
  # (case-sensitive marker, so reading bodies can't false-positive on prose).
  run_test "lowercase breaking change in body → patch" "patch" "1.2.4" "v1.2.3" "1.2.3" \
    "fix: tidy up~~BODY~~this is explicitly not a breaking change"

  # Test 7: marker on an early LINE with ~90 KB of lines after it -- the real
  # SIGPIPE bug (PR #177 review). grep is line-oriented, so with the marker on
  # line 1 it matches and exits while the writer still has the tail to push; the
  # pipe form takes SIGPIPE (141) and reads it as "no match". Deterministically
  # wrong once the tail clears the 64 KB pipe buffer. A single ~94 KB *line*
  # would NOT reproduce it (grep must read the whole line before deciding), so
  # the tail must be many lines.
  _line=$(head -c 60 </dev/zero | tr '\0' y)
  _tl=$(for _ in $(seq 1 1500); do printf '%s\\n' "$_line"; done)
  run_test "early marker + long multi-line tail → major (no SIGPIPE)" "major" "2.0.0" "v1.2.3" "1.2.3" \
    "fix: big commit~~BODY~~BREAKING CHANGE: boom\\n${_tl}"

  # Test 8a (r4 BLOCKER 2 / r5): a footer git FOLDED into the subject -- a real
  # TWO-LINE message with no blank line, so %s folds subject+footer and %b is
  # empty, but %B keeps the newline. Derivation reads %B, and the subject line is
  # trailer-shaped, so the marker on the next line anchors as a footer → major.
  run_test "folded footer in subject (two-line) → major" "major" "2.0.0" "v1.2.3" "1.2.3" \
    "fix: tighten the thing\\nBREAKING CHANGE: the config key was renamed"

  # Test 8a2 (r5 Major 1): a BREAKING CHANGE footer stacked directly after another
  # trailer with NO blank line between them (conventional-commits' own canonical
  # example) must still derive major.
  run_test "stacked footer (after another trailer) → major" "major" "2.0.0" "v1.2.3" "1.2.3" \
    "feat: x~~BODY~~Reviewed-by: Z\\nBREAKING CHANGE: drops the old API"

  # Test 8b (r4 BLOCKER 1): a marker at column 1 of a WRAPPED prose line
  # (mid-paragraph) must NOT trigger -- the bare `^` anchor false-positived on
  # exactly this shape (commit 8415ce1). No commas (run_test splits on them).
  run_test "wrapped-prose body marker → patch" "patch" "1.2.4" "v1.2.3" "1.2.3" \
    "fix: describe the detector~~BODY~~the detector matched a BREAKING\\nCHANGE marker anywhere but the note awk used\\nline-start so a commit whose marker was mid-line like\\nBREAKING CHANGE: this one produced an empty note"

  # Test 8c (r4): a real break declared PARAGRAPH-initial with NO colon (the #2
  # shape) must still derive major.
  run_test "paragraph-initial no-colon break → major" "major" "2.0.0" "v1.2.3" "1.2.3" \
    "fix: harden the gate~~BODY~~context line about the change\\n\\nBREAKING CHANGE called out deliberately USER must become 1000"

  # Test 8 (guard coverage — bonnyr-f5 #179 r3): an unresolvable --since-tag must
  # fail CLOSED (rc 1, no BUMP_TYPE output), not derive patch from an empty range.
  # This exercises the unresolvable-SINCE_TAG guard, which previously had none.
  _grd_tmp=$(mktemp -d)
  git init -q "$_grd_tmp"
  git -C "$_grd_tmp" config user.email "selftest@bnk-forge.local"
  git -C "$_grd_tmp" config user.name "bnk-forge self-test"
  git -C "$_grd_tmp" commit --allow-empty -m "initial" -q
  SELFTEST_ASSERTIONS=$((SELFTEST_ASSERTIONS + 1))
  _grd_rc=0
  _grd_out=$(cd "$_grd_tmp" && bash "$SELFTEST_SCRIPT" --since-tag v9.9.9 --baseline 1.0.0 2>/dev/null) || _grd_rc=$?
  rm -rf "$_grd_tmp"
  if [[ "$_grd_rc" -ne 0 && -z "$_grd_out" ]]; then
    echo "  PASS: unresolvable --since-tag fails closed (rc=$_grd_rc, no output)"
  else
    echo "  FAIL: unresolvable --since-tag should refuse (got rc=$_grd_rc out='$_grd_out')"
    SELFTEST_FAILURES=$((SELFTEST_FAILURES + 1))
  fi

  echo "=== END SELF-TEST ==="
  # INV-16: a harness that runs zero assertions must not report success. This
  # catches a self-test that silently no-ops (e.g. a renamed run_test); #182's
  # script-selftests job additionally asserts the PASS count and this END marker
  # externally, so renaming the SELF_TEST guard itself is caught there too.
  if [[ "$SELFTEST_ASSERTIONS" -eq 0 ]]; then
    echo "SELF-TEST: zero assertions ran — harness is dead" >&2
    exit 1
  fi
  if [[ "$SELFTEST_FAILURES" -ne 0 ]]; then
    echo "SELF-TEST: ${SELFTEST_FAILURES} failure(s)" >&2
    exit 1
  fi
  echo "compute_version_bump self-test: OK (${SELFTEST_ASSERTIONS} assertions)"
fi
