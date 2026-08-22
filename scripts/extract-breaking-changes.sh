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
# the copies in compute_version_bump.sh. This is an invariant these two files must
# uphold themselves -- if the extractor is narrower a break bumps the major with no
# note; if wider a note appears with no bump. The CI job that DIFFS the two copies
# and fails on any drift lands with #182 (bonnyr-f5 #179 r6 F4); it is not present
# in this tree, so until #182 merges the invariant is enforced only by review and
# by the shared self-test fixtures below -- keep the two copies in lock-step by hand.
#
# A marker counts as a real footer under two anchors (byte-identical to compute):
#   * preceded by a BLANK line -> accepted with OR without a colon (keeps the #2
#     no-colon paragraph break);
#   * preceded by another TRAILER, or folded directly onto a conventional-commit
#     SUBJECT -> accepted ONLY with a colon (catches a footer folded onto a scoped
#     subject `fix(core): x`, bonnyr-f5 #179 r6 F1, while rejecting a prose header
#     `Before:` / `Note:` followed by colon-less prose, r6 F2).
# Wrapped prose (a marker after a PROSE line) is still rejected. _is_breaking_subject
# is BANG-ONLY: the folded-footer-in-subject is caught by running _is_breaking_body
# on %B (which preserves the newline git folds into %s), and scanning the raw subject
# for the marker over-bumped on `docs: clarify what BREAKING CHANGE: means` (r5 Minor 1).
_is_breaking_subject() {
  grep -qE '^[A-Za-z]+(\([^)]*\))?!:' <<< "$1"
}
_is_breaking_body() {
  awk '
    BEGIN { prev_blank = 1; prev_trailer = 0 }
    /^[[:space:]]*$/ { prev_blank = 1; prev_trailer = 0; next }
    {
      is_marker  = ($0 ~ /^([*-][[:space:]]+)?(\*\*)?BREAKING[[:space:] -]+CHANGE/)
      is_colon   = ($0 ~ /^([*-][[:space:]]+)?(\*\*)?BREAKING[[:space:] -]+CHANGE(\*\*)?:/)
      is_trailer = ($0 ~ /^[A-Za-z0-9][A-Za-z0-9-]*:([[:space:]]|$)/)
      if (prev_blank && is_marker) found = 1
      else if (prev_trailer && is_colon) found = 1
      is_subject = (NR == 1 && $0 ~ /^[A-Za-z]+(\([^)]*\))?!?:[[:space:]]/)
      prev_blank = 0; prev_trailer = (is_trailer || is_subject)
    }
    END { exit(found ? 0 : 1) }
  ' <<< "$1"
}

# Emit the BREAKING CHANGE footer paragraph(s) -- flattened, markdown-bold
# stripped. Takes the FULL raw message (%B). Capture uses the SAME start rule as
# _is_breaking_body so trigger and note never disagree: a marker preceded by a
# blank line anchors with or without a colon; a marker in the trailer block
# (preceded by another trailer, or folded directly onto a conventional-commit
# subject) anchors ONLY with a colon. It then captures the WHOLE footer paragraph,
# including ordinary prose continuation lines that merely contain a colon
# (`migration: ...`). The round-4 note stopped at the FIRST trailer-shaped
# continuation line, truncating 7ece9b04's real bullet mid-sentence (bonnyr-f5
# #179 r5 Major 2).
#
# F5 (bonnyr-f5 #179 r6): the note now STOPS at the first REAL git-trailer line
# rather than merely stripping a TRAILING trailer run -- a trailer block FOLLOWED
# by prose (`BREAKING CHANGE: x` / `Co-Authored-By: a@b` / `then prose`) used to
# leak the address because the trailing-strip loop halted on the closing prose
# line. A "real" trailer is a capitalized `Word(-Word)*:` key (Co-Authored-By,
# Signed-off-by, Reviewed-by, Acked-by, Cc, Claude-Session, Change-Id, X-* and the
# no-space `Session:` form all match); a lowercase-prose colon line (`migration:`)
# does NOT match, so mid-footer prose continuations are kept (r5 Major 2 stays
# green). Marker lines are excluded from the stop so a hyphen-form `BREAKING-CHANGE:`
# is never mistaken for a trailer. A trailing CR is stripped so a CRLF commit
# message does not carry `\r` into CHANGELOG.md.
_breaking_note() {
  awk '
    BEGIN { prev_blank = 1; prev_trailer = 0 }
    {
      sub(/\r$/, "")
      if ($0 ~ /^[[:space:]]*$/) { if (p) para_end = 1; prev_blank = 1; prev_trailer = 0; next }
      is_marker  = ($0 ~ /^([*-][[:space:]]+)?(\*\*)?BREAKING[[:space:] -]+CHANGE/)
      is_colon   = ($0 ~ /^([*-][[:space:]]+)?(\*\*)?BREAKING[[:space:] -]+CHANGE(\*\*)?:/)
      is_trailer = ($0 ~ /^[A-Za-z0-9][A-Za-z0-9-]*:([[:space:]]|$)/)
      if (!p) {
        if ((prev_blank && is_marker) || (prev_trailer && is_colon)) { p = 1; print }
      } else if (para_end) {
        if (prev_blank && is_marker) { print ""; print; para_end = 0 } else { exit }
      } else { print }
      is_subject = (NR == 1 && $0 ~ /^[A-Za-z]+(\([^)]*\))?!?:[[:space:]]/)
      prev_blank = 0; prev_trailer = (is_trailer || is_subject)
    }
  ' <<< "$1" \
  | awk '{ a[NR] = $0 } END {
      n = NR
      for (i = 1; i <= NR; i++) {
        if (a[i] ~ /^[A-Z][A-Za-z0-9]*(-[A-Za-z0-9]+)*:([[:space:]]|$)/ \
            && a[i] !~ /^([*-][[:space:]]+)?(\*\*)?BREAKING[[:space:] -]+CHANGE/) { n = i - 1; break }
      }
      for (i = 1; i <= n; i++) print a[i]
    }' \
  | sed 's/\*\*//g' | tr '\n' ' ' | sed 's/  */ /g; s/^ *//; s/ *$//'
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

  # Subject detector is BANG-ONLY now (r5): prose that merely names the marker,
  # with or without a colon, must NOT trigger via the subject path.
  _assert_subject "prose names marker in subj"  "docs: explain the BREAKING CHANGE footer" 0
  _assert_subject "docs quotes marker w/ colon"  "docs: clarify what BREAKING CHANGE: means" 0

  # r4 BLOCKER 2 / r5 -- a footer git FOLDED into the subject is a real TWO-LINE
  # message with no blank line. Derivation/extraction read %B; the subject line is
  # trailer-shaped, so the marker on the next line anchors as a footer → trigger.
  _assert "folded footer via %B (two-line)" $'fix: tighten the thing\nBREAKING CHANGE: the config key was renamed' 1

  # r5 Major 1 -- a BREAKING CHANGE footer stacked directly after another trailer
  # with NO blank line between (conventional-commits' canonical example) triggers,
  # and the note starts at the marker (the preceding trailer is not captured).
  _assert "stacked footer (after trailer)" $'feat: x\n\nReviewed-by: Z\nBREAKING CHANGE: drops the old API' 1
  _stk=$(_breaking_note $'feat: x\n\nReviewed-by: Z\nBREAKING CHANGE: drops the old API')
  assertions=$((assertions + 1))
  if [[ "$_stk" == *"drops the old API"* && "$_stk" != *"Reviewed-by"* ]]; then
    echo "  ok: stacked footer note starts at marker -> ${_stk:0:48}"
  else echo "FAIL: stacked footer note wrong -> '$_stk'"; fail=1; fi

  # r5 Major 2 -- the note must NOT truncate at the first prose `word:` line. A
  # footer whose continuation contains ordinary prose colons (`migration:`) keeps
  # the WHOLE paragraph; only a TRAILING run of real trailers is stripped.
  _notrunc=$(_breaking_note $'feat: y\n\nBREAKING CHANGE: the runner changed.\nmigration: run the tool first.\nThat is the whole story.')
  assertions=$((assertions + 1))
  if [[ "$_notrunc" == *"migration: run the tool first."* && "$_notrunc" == *"That is the whole story."* ]]; then
    echo "  ok: note keeps prose continuation -> ${_notrunc:0:56}"
  else echo "FAIL: note truncated on prose colon -> '$_notrunc'"; fail=1; fi

  # r5 Minor 2 -- a trailer with a digit/dot token (`X-Session-1:`) or a no-space
  # colon (`Session:` at line end) directly under the footer must be stripped, not
  # leaked. The old `[A-Za-z][A-Za-z-]*: ` stop missed both.
  _leak2=$(_breaking_note $'feat: x\n\nBREAKING CHANGE: the key moved.\nX-Session-1: 0decafbad\nClaude-Session: https://claude.ai/code/session_XYZ\nSession:')
  assertions=$((assertions + 1))
  if [[ "$_leak2" == *"the key moved"* && "$_leak2" != *"0decafbad"* && "$_leak2" != *"claude.ai"* && "$_leak2" != *"Session"* ]]; then
    echo "  ok: digit-token / no-space trailers stripped -> ${_leak2:0:48}"
  else echo "FAIL: non-standard trailer leaked -> '$_leak2'"; fail=1; fi

  # r6 F1 BLOCKER -- a footer FOLDED onto a SCOPED subject (`fix(core): x` on the
  # first line, marker on the second, no blank) must trigger. The r5 anchor reached
  # the colon only on UNSCOPED subjects because `(` broke the trailer regex; the r6
  # is_subject anchor arms the trailer->colon path for scoped subjects too.
  _assert "F1 scoped folded footer" $'feat(api): drop v1\nBREAKING CHANGE: all /api/v1 removed' 1
  # r6 F1 control -- the SAME scoped fold WITHOUT a colon is prose, not a footer.
  _assert "F1 scoped folded colonless (inert)" $'feat(api): drop v1\nBREAKING CHANGE happened here' 0

  # r6 F2 MAJOR -- a prose section header (`Before:`) is trailer-shaped, but a
  # colon-LESS marker following it is prose. Only the trailer->COLON rule anchors a
  # marker in the trailer block, so this must stay inert (the round-4 false positive).
  _assert "F2 prose header + colonless marker (inert)" $'docs: x\n\nBefore:\nBREAKING CHANGE was matched anywhere.' 0

  # r6 F7 MINOR -- widened separator/bullet: a DOUBLE-space marker and a dash-bullet
  # marker are real footers and must trigger with a non-empty note.
  _assert "F7 double-space marker" $'fix: y\n\nBREAKING  CHANGE: the --legacy flag was removed' 1
  _assert "F7 dash-bullet marker"  $'fix: y\n\n- BREAKING CHANGE: the --legacy flag was removed' 1

  # r6 F5 MINOR (note leak) -- a footer FOLLOWED by a `migration:` prose line and
  # THEN a Co-Authored-By trailer: the note must keep the lowercase-prose `migration:`
  # continuation but STOP at the first real git-trailer, so the email never leaks.
  # The r5 trailing-strip halted on the closing `Co-Authored-By` (it was the last
  # line) here it would leak because prose could follow; the r6 stop-at-first-trailer
  # cuts it regardless of what follows.
  _f5=$(_breaking_note $'feat: x\n\nBREAKING CHANGE: the key moved.\nmigration: see the upgrade guide.\nCo-Authored-By: Someone <someone@example.com>\nthen a trailing prose line.')
  assertions=$((assertions + 1))
  if [[ "$_f5" == *"the key moved"* && "$_f5" == *"migration: see the upgrade guide."* \
        && "$_f5" != *"someone@example.com"* && "$_f5" != *"Co-Authored-By"* && "$_f5" != *"trailing prose"* ]]; then
    echo "  ok: note keeps migration prose, stops at Co-Authored-By -> ${_f5:0:56}"
  else echo "FAIL: F5 note leaked/truncated wrongly -> '$_f5'"; fail=1; fi

  if [[ $assertions -eq 0 ]]; then
    echo "FAIL: harness ran zero assertions"; fail=1
  fi
  # END marker mirroring compute_version_bump.sh's self-test: it prints only after
  # the LAST assertion, so the script-selftests gate can assert the harness reached
  # the end (an early `exit 0` or a deleted assertion block is caught) WITHOUT the
  # gate having to grep this script for its own --self-test flag (bonnyr-f5 #193 M6).
  echo "=== END SELF-TEST ==="
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
#
# FAIL-CLOSED IS NOW EFFECTIVE (bonnyr-f5 #179 r6 F3; #193 minor): the three
# call sites in .github/workflows/release.yml invoke this script WITHOUT `|| true`,
# so this rc=1 propagates and fails the release instead of being swallowed into
# BREAKING="". (The old note here said to "merge #179 WITH or AFTER #181" — that
# already happened; both are in-tree and the `|| true` on the extract call is gone.)
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
  subj=${subj%$'\r'}  # strip a trailing CR so a CRLF subject never reaches CHANGELOG.md
  # Full raw message (%B): a folded footer keeps its newline here, and the footer
  # anchor / note extractor both need the whole message (bonnyr-f5 #179 r5).
  message=$(git log -1 --format='%B' "$sha" 2>/dev/null || true)
  if _is_breaking_subject "$subj" || _is_breaking_body "$message"; then
    note=$(_breaking_note "$message")
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
