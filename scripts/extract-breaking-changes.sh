#!/usr/bin/env bash
# Emit a markdown "Breaking Changes" block for the commits in a range.
#
# Conventional-commits declares a breaking change as a `BREAKING CHANGE` footer,
# which lives in the commit BODY. The release notes / CHANGELOG generation reads
# only subjects (%s), so footer-declared breaks — and any migration steps they
# spell out — never reach operators (PR #177 review). This surfaces them.
#
# Usage: extract-breaking-changes.sh <since_ref> [<until_ref>]
#   Prints a "### ⚠️ Breaking Changes" section, or nothing if there are none.
set -euo pipefail

# Does a commit body declare a breaking change? Uppercase footer/marker only
# (spec form), so body prose like "not a breaking change" does not false-trigger.
# MUST stay identical to the detector in compute_version_bump.sh: if this is
# narrower, a break that bumps the major produces no note at all.
_is_breaking() { grep -qE '\bBREAKING[[:space:] -]+CHANGE\b' <<< "$1"; }

# Extract the BREAKING CHANGE paragraph (up to the next blank line), flattened to
# one line and stripped of markdown bold. The start match is as loose as the
# _is_breaking trigger (marker ANYWHERE on the line, not just line-start): an
# anchored ^BREAKING here emitted an empty note whenever the detector counted a
# non-line-start marker, silently dropping the break from the CHANGELOG and
# Release body (#179 review).
_breaking_note() {
  awk '/BREAKING[[:space:] -]+CHANGE/{p=1} p{print} p&&/^$/{exit}' <<< "$1" \
    | tr '\n' ' ' | sed 's/\*\*//g; s/  */ /g; s/ *$//'
}

if [[ "${1:-}" == "--self-test" ]]; then
  fail=0
  _expect_nonempty() {  # $1=label $2=body
    local n; n=$(_breaking_note "$2")
    if _is_breaking "$2" && [[ -z "$n" ]]; then
      echo "FAIL: $1 — trigger matched but note is empty"; fail=1
    elif [[ -z "$n" ]]; then echo "FAIL: $1 — note empty"; fail=1
    else echo "  ok: $1 -> ${n:0:48}"; fi
  }
  # The regression: a marker NOT at line-start must still yield a note.
  _expect_nonempty "non-line-start marker" "This is a BREAKING CHANGE: the API moved."
  _expect_nonempty "spec footer"           $'fix: y\n\nBREAKING CHANGE: USER must become 65532.'
  _expect_nonempty "markdown-bold footer"  $'feat: z\n\n**BREAKING CHANGE:** boom.'
  # Prose that must NOT trigger.
  if _is_breaking "this is not a breaking change at all"; then
    echo "FAIL: lowercase prose false-triggered"; fail=1
  else echo "  ok: lowercase prose does not trigger"; fi
  [[ $fail -eq 0 ]] && echo "extract-breaking-changes self-test: OK"
  exit "$fail"
fi

SINCE="${1:?usage: extract-breaking-changes.sh <since_ref> [until_ref]}"
UNTIL="${2:-HEAD}"

block=""
while IFS= read -r sha; do
  [[ -z "$sha" ]] && continue
  body=$(git log -1 --format="%b" "$sha" 2>/dev/null || true)
  if _is_breaking "$body"; then
    subj=$(git log -1 --format="%s" "$sha" 2>/dev/null || true)
    note=$(_breaking_note "$body")
    # Belt-and-suspenders: never emit a bare bullet if extraction somehow yields
    # nothing — point the operator at the commit rather than staying silent.
    [[ -z "$note" ]] && note="(see commit ${sha:0:9} for the breaking-change details)"
    block="${block}- **${subj}**
  ${note}
"
  fi
done < <(git log "${SINCE}..${UNTIL}" --format='%H' 2>/dev/null || true)

if [[ -n "$block" ]]; then
  printf '### ⚠️ Breaking Changes\n\n%s\n' "$block"
fi
