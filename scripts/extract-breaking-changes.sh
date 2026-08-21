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
# INV-15: _is_breaking_subject and _is_breaking_body MUST stay byte-identical to
# the copies in compute_version_bump.sh (#182's script-selftests asserts it) --
# if the extractor is narrower a break bumps the major with no note; if wider a
# note appears with no bump.
#
# A breaking change is a `type!:` subject, a `BREAKING CHANGE:` footer, or a
# `BREAKING-CHANGE:` footer. Two robustness problems drove r4:
#  * SUBJECT folding -- with no blank line before it, git folds the footer into
#    %s and leaves %b empty, so a body-only check ships a major as a patch
#    (bonnyr-f5 #179 r4). The subject is checked for a folded `BREAKING[ -]CHANGE:`
#    footer, colon REQUIRED so subject prose ("explain the BREAKING CHANGE footer")
#    does not trigger.
#  * WRAPPED prose -- a bare `^` still matched a prose paragraph that WRAPPED so
#    the marker fell at column 1 (commit 8415ce1, this branch). The body match is
#    PARAGRAPH-initial: the marker line is the first body line or is preceded by a
#    blank line. That keeps the real #2 break (marker preceded by a blank line)
#    and rejects wrapped prose. No colon required, because #2 declares its break
#    as "BREAKING CHANGE, called out" with no colon.
_is_breaking_subject() {
  grep -qE '^[A-Za-z]+(\([^)]*\))?!:' <<< "$1" \
    || grep -qE 'BREAKING[ -]CHANGE:' <<< "$1"
}
_is_breaking_body() {
  awk '
    BEGIN { blank = 1 }
    /^[[:space:]]*$/ { blank = 1; next }
    { if (blank && $0 ~ /^(\*\*)?BREAKING[ -]CHANGE/) found = 1; blank = 0 }
    END { exit(found ? 0 : 1) }
  ' <<< "$1"
}

# Emit the BREAKING CHANGE footer paragraph(s) -- flattened, markdown-bold
# stripped. Capture starts only at a PARAGRAPH-initial marker (same rule as
# _is_breaking_body, so trigger and note never disagree) and STOPS at the first
# trailer-shaped line (`Word-Word: ...`) or at prose, so a `Co-Authored-By:`
# email or a `Claude-Session:` URL sitting under the footer is never published to
# a public release (bonnyr-f5 #179 r4). A following BREAKING CHANGE paragraph is
# kept, so a second footer is not dropped.
_breaking_note() {
  awk '
    BEGIN { blank = 1 }
    {
      if ($0 ~ /^[[:space:]]*$/) { blank = 1; next }
      marker  = ($0 ~ /^(\*\*)?BREAKING[ -]CHANGE/)
      trailer = ($0 ~ /^[A-Za-z][A-Za-z-]*: /)
      if (!p) {
        if (blank && marker) { p = 1; print }
      } else if (blank) {
        if (marker) { print ""; print } else { exit }
      } else {
        if (trailer && !marker) exit
        print
      }
      blank = 0
    }
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
    if _is_breaking_body "$body"; then trig=1; else trig=0; fi
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

  # r4 BLOCKER 1 -- WRAPPED prose: a marker at column 1 of a line that is NOT
  # paragraph-initial (mid-paragraph, the prose wrapped there) must NOT trigger.
  # This is commit 8415ce1's shape, which defeated the bare `^` anchor.
  _assert "wrapped-prose mid-paragraph" $'The detector matches a BREAKING\nCHANGE marker anywhere, but the note awk was anchored to\nline-start. A commit whose marker was not at line-start ("... a\nBREAKING CHANGE: ...") therefore bumped major yet produced an empty\nnote.' 0

  # r4 -- a real break declared paragraph-initial with NO colon (the #2 shape)
  # must still trigger.
  _assert "paragraph-initial no-colon break" $'Context line about the change.\n\nBREAKING CHANGE, called out deliberately. USER must become 1000.' 1

  # r4 MAJOR -- the note must STOP before a trailer block, or a Co-Authored-By
  # email / Claude-Session URL leaks into a public release body.
  _leak=$(_breaking_note $'feat: x\n\nBREAKING CHANGE: the key moved.\nCo-Authored-By: Someone <someone@example.com>\nClaude-Session: https://claude.ai/code/session_ABC')
  assertions=$((assertions + 1))
  if [[ "$_leak" == *"the key moved"* && "$_leak" != *"someone@example.com"* && "$_leak" != *"claude.ai"* ]]; then
    echo "  ok: note stops before trailers (no email/URL leak) -> ${_leak:0:48}"
  else echo "FAIL: note leaked a trailer -> '$_leak'"; fail=1; fi

  # r4 BLOCKER 2 -- a footer git FOLDED into the subject (no blank line before it)
  # must be seen; subject prose that merely names the marker must not.
  _assert_subject "folded footer in subject"  "fix: tighten the thing BREAKING CHANGE: the config key was renamed" 1
  _assert_subject "prose names marker in subj" "docs: explain the BREAKING CHANGE footer" 0

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
  if _is_breaking_subject "$subj" || _is_breaking_body "$body"; then
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
