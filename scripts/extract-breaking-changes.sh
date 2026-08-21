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

# ── Breaking-change detectors ────────────────────────────────────────────────
# A conventional-commits BREAKING CHANGE is a FOOTER: a line that STARTS with
# `BREAKING CHANGE` or `BREAKING-CHANGE` (optionally markdown-bold). The anchor
# to line-start (^) is the whole point: an unanchored word-boundary match fired
# on uppercase prose ANYWHERE in a body ("…to describe a BREAKING CHANGE process…")
# and the note extractor then shipped that prose to operators as migration
# guidance (bonnyr-f5 #179 r3). The `type!:` subject form is a separate signal.
#
# INV-15: this footer detector MUST stay byte-identical to the one in
# compute_version_bump.sh — if the extractor is narrower, a break that bumps the
# major ships with no note; if wider, a note appears with no bump. #182's
# script-selftests job asserts the two function bodies match.
_is_breaking() { grep -qE '^(\*\*)?BREAKING[ -]CHANGE' <<< "$1"; }
# compute_version_bump.sh ALSO bumps major on a `type!:` subject; the extractor
# must trigger on it too, or a `feat!:` release ships with zero breaking-change
# docs. Case-insensitive on the type so `Feat!:` is not silently dropped.
_is_breaking_subject() { grep -qE '^[A-Za-z]+(\([^)]*\))?!:' <<< "$1"; }

# Emit EVERY BREAKING CHANGE footer paragraph (the marker line through the next
# blank line), flattened to one line and stripped of markdown bold. Anchored to
# the SAME line-start position as _is_breaking, so the trigger and the note can
# never disagree (an anchored trigger with an unanchored note, or vice versa,
# silently drops or invents a break). No line cap — an earlier n>=40 cap
# truncated long migration notes silently — and it captures ALL footers, so a
# second footer in the same body is never dropped.
_breaking_note() {
  awk '
    /^(\*\*)?BREAKING[ -]CHANGE/ { p=1 }
    p && /^[[:space:]]*$/        { p=0; next }
    p                           { print }
  ' <<< "$1" | sed 's/\*\*//g' | tr '\n' ' ' | sed 's/  */ /g; s/^ *//; s/ *$//'
}

if [[ "${1:-}" == "--self-test" ]]; then
  fail=0
  assertions=0
  # Assert both the trigger AND the note for one body against expectations.
  # $1=label $2=body $3=expect-trigger(1/0)
  _assert() {
    local label="$1" body="$2" want="$3" note trig
    note=$(_breaking_note "$body")
    if _is_breaking "$body"; then trig=1; else trig=0; fi
    assertions=$((assertions + 1))
    if [[ "$want" == 1 ]]; then
      # A positive case must BOTH trigger and yield a non-empty note — the old
      # _expect_nonempty only checked the note inside a failure conjunct, so it
      # could never actually fail (bonnyr-f5 #179 r3).
      if [[ "$trig" == 1 && -n "$note" ]]; then
        echo "  ok: $label -> ${note:0:56}"
      else
        echo "FAIL: $label — expected trigger+note, got trig=$trig note='${note}'"; fail=1
      fi
    else
      if [[ "$trig" == 0 && -z "$note" ]]; then
        echo "  ok: $label (correctly inert)"
      else
        echo "FAIL: $label — expected inert, got trig=$trig note='${note}'"; fail=1
      fi
    fi
  }
  _assert_subject() {  # $1=label $2=subject $3=expect(1/0)
    local label="$1" subj="$2" want="$3" trig
    if _is_breaking_subject "$subj"; then trig=1; else trig=0; fi
    assertions=$((assertions + 1))
    if [[ "$trig" == "$want" ]]; then echo "  ok: $label"; else echo "FAIL: $label (trig=$trig want=$want)"; fail=1; fi
  }

  # Positive: real footers at line-start, in the forms the spec/markdown allow.
  _assert "spec footer"          $'fix: y\n\nBREAKING CHANGE: USER must become 65532.' 1
  _assert "hyphen footer"        $'fix: y\n\nBREAKING-CHANGE: config key renamed.'     1
  _assert "markdown-bold footer" $'feat: z\n\n**BREAKING CHANGE:** boom.'              1
  # Positive: TWO footers in one body — the note must contain BOTH (the old
  # single-paragraph extractor dropped the second).
  _assert "two footers both kept" $'feat: q\n\nBREAKING CHANGE: first thing changed.\n\nBREAKING CHANGE: second thing changed.' 1
  _two=$(_breaking_note $'feat: q\n\nBREAKING CHANGE: first thing changed.\n\nBREAKING CHANGE: second thing changed.')
  assertions=$((assertions + 1))
  if [[ "$_two" == *"first thing"* && "$_two" == *"second thing"* ]]; then
    echo "  ok: both footer paragraphs present"
  else echo "FAIL: second footer dropped -> '$_two'"; fail=1; fi

  # Negative: uppercase marker MID-LINE is prose, not a footer — must NOT trigger
  # and must NOT produce a note (the exact false-positive of the old detector).
  _assert "mid-line prose"       "This is a BREAKING CHANGE: the API moved." 0
  _assert "lowercase prose"      "this is explicitly not a breaking change"  0
  _assert "indented non-footer"  $'fix: y\n\n    BREAKING CHANGE: indented, not a footer' 0

  # Subject-form bang detector.
  _assert_subject "bang subject triggers"    "feat!: drop the v1 API"    1
  _assert_subject "Capitalised bang triggers" "Feat!: drop the v1 API"   1
  _assert_subject "scoped bang triggers"     "fix(core)!: rename"        1
  _assert_subject "normal subject inert"     "feat: normal change"       0

  if [[ $assertions -eq 0 ]]; then
    echo "FAIL: harness ran zero assertions"; fail=1
  fi
  [[ $fail -eq 0 ]] && echo "extract-breaking-changes self-test: OK ($assertions assertions)"
  exit "$fail"
fi

SINCE="${1:?usage: extract-breaking-changes.sh <since_ref> [until_ref]}"
UNTIL="${2:-HEAD}"

# Fail CLOSED on an unresolvable range. Without this the `git log` below yields
# empty output and rc 0 for a typo'd ref, so a release ships with no breaking-
# change section and no signal that the range was never read (bonnyr-f5 #179 r3:
# a silent failure that fools a reviewer will fool a release). A VALID range with
# no breaking commits is still fine — it prints nothing and exits 0.
for _ref in "$SINCE" "$UNTIL"; do
  if ! git rev-parse --verify --quiet "${_ref}^{commit}" >/dev/null 2>&1; then
    echo "::error::extract-breaking-changes: '${_ref}' does not resolve to a commit — refusing to emit an empty breaking-changes section from a bad range." >&2
    exit 1
  fi
done

block=""
while IFS= read -r sha; do
  [[ -z "$sha" ]] && continue
  subj=$(git log -1 --format="%s" "$sha" 2>/dev/null || true)
  body=$(git log -1 --format="%b" "$sha" 2>/dev/null || true)
  if _is_breaking_subject "$subj" || _is_breaking "$body"; then
    note=$(_breaking_note "$body")
    # Belt-and-suspenders: a `type!:` subject with no body footer yields no note;
    # point the operator at the commit rather than emitting a bare bullet.
    [[ -z "$note" ]] && note="(see commit ${sha:0:9} for the breaking-change details)"
    block="${block}- **${subj}**
  ${note}
"
  fi
done < <(git log "${SINCE}..${UNTIL}" --format='%H')

if [[ -n "$block" ]]; then
  printf '### ⚠️ Breaking Changes\n\n%s\n' "$block"
fi
