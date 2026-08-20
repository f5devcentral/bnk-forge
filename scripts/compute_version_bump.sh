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
  RANGE_HASHES=$(git log --pretty=format:"%H" 2>/dev/null || true)
else
  RANGE_HASHES=$(git log "${SINCE_TAG}..HEAD" --pretty=format:"%H" 2>/dev/null || true)
fi

while IFS= read -r sha; do
  [[ -z "$sha" ]] && continue
  subject=$(git log -1 --format="%s" "$sha" 2>/dev/null || true)
  body=$(git log -1 --format="%b" "$sha" 2>/dev/null || true)

  # Major: `type!:` in the subject, OR a BREAKING CHANGE / BREAKING-CHANGE marker
  # anywhere in the message (footer or deliberate prose).
  #
  # Pipe-free on purpose (PR #177 review): `foo | grep -q` under `set -o
  # pipefail` returns SIGPIPE (141) when grep matches early and the writer still
  # has a large body to push, which pipefail turns into a failed test -- so
  # *finding* the marker made the branch evaluate false and the bump silently
  # stayed patch (non-deterministic, ~14/20 wrong on a big body). Here-strings
  # have no writer process to signal, so the result is deterministic.
  msg="${subject}"$'\n'"${body}"
  if grep -qE '^[a-z]+(\([^)]*\))?!:' <<< "$subject" \
     || grep -qE '\bBREAKING[[:space:] -]+CHANGE\b' <<< "$msg"; then
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
# Independent of the loop above and pipe-free: if any commit in the range
# declares a breaking change, the bump MUST be major. This would have caught the
# SIGPIPE race (marker present, bump silently patch) by aborting rather than
# shipping a mis-versioned release. Same shape for feat -> at least minor.
if [[ -n "$SINCE_TAG" ]]; then
  ALL_MSGS=$(git log "${SINCE_TAG}..HEAD" --format='%B' 2>/dev/null || true)
else
  ALL_MSGS=$(git log --format='%B' 2>/dev/null || true)
fi
if grep -qE '\bBREAKING[[:space:] -]+CHANGE\b' <<< "$ALL_MSGS" && [[ "$BUMP_TYPE" != "major" ]]; then
  echo "::error::Derived bump '$BUMP_TYPE' but a BREAKING CHANGE marker exists in ${SINCE_TAG:-<all>}..HEAD -- refusing to ship a mis-versioned release." >&2
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

  # SELF_TEST is an inherited environment variable — without unsetting it
  # here, run_test's recursive script invocations below would also enter
  # self-test mode and recurse indefinitely (fork bomb).
  unset SELF_TEST

  run_test() {
    local desc="$1" expected_bump="$2" expected_ver="$3"
    local since="$4" baseline="$5" commits_str="$6"
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
        git -C "$tmpdir" commit --allow-empty \
          -m "${entry%%~~BODY~~*}" -m "${entry#*~~BODY~~}" -q
      else
        git -C "$tmpdir" commit --allow-empty -m "$entry" -q
      fi
    done <<< "$(echo "$commits_str" | tr ',' '\n')"

    # Run the version computer inside the temp repo so it scans the fake range.
    local result
    result=$(cd "$tmpdir" && bash "$OLDPWD/$(dirname "$0")/$(basename "$0")" \
      ${since:+--since-tag "$since"} --baseline "$baseline" 2>/dev/null || true)

    local got_bump got_ver
    got_bump=$(echo "$result" | grep BUMP_TYPE | cut -d= -f2)
    got_ver=$(echo "$result" | grep TARGET_VERSION | cut -d= -f2)

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

  # Test 4: no commits → patch bump
  run_test "no commits → patch" "patch" "1.2.4" "v1.2.3" "1.2.3" ""

  # Test 5: BREAKING CHANGE footer in the BODY → major (the PR #177 bug: a
  # fix-subject commit whose body declares the break must still bump major).
  run_test "BREAKING CHANGE footer in body → major" "major" "2.0.0" "v1.2.3" "1.2.3" \
    "fix: harden non-root gate~~BODY~~BREAKING CHANGE: USER nonroot must become USER 65532"

  # Test 6: lowercase "breaking change" in body prose must NOT trigger major
  # (case-sensitive marker, so reading bodies can't false-positive on prose).
  run_test "lowercase breaking change in body → patch" "patch" "1.2.4" "v1.2.3" "1.2.3" \
    "fix: tidy up~~BODY~~this is explicitly not a breaking change"

  # Test 7: large body, marker early with a long tail after it -- the exact
  # SIGPIPE race from the PR #177 review. `printf | grep -q` flaked here
  # (~14/20 wrong) because grep matched then died before draining the tail; the
  # here-string form must bump major deterministically. ~94 KB single-line body.
  _hd=$(head -c 4000 </dev/zero | tr '\0' x)
  _tl=$(head -c 90000 </dev/zero | tr '\0' y)
  run_test "large body, early marker → major (no SIGPIPE)" "major" "2.0.0" "v1.2.3" "1.2.3" \
    "fix: big commit~~BODY~~${_hd} BREAKING CHANGE: boom ${_tl}"

  echo "=== END SELF-TEST ==="
  if [[ "$SELFTEST_FAILURES" -ne 0 ]]; then
    echo "SELF-TEST: ${SELFTEST_FAILURES} failure(s)" >&2
    exit 1
  fi
fi
