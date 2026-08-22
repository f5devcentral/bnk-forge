#!/usr/bin/env bash
# scripts/lib/breaking-change-detect.sh — the ONE BREAKING CHANGE predicate.
#
# INV-15 (bonnyr-f5 #179 r6 F4 / #193 M5): the detector used to live as two
# byte-identical inline copies in compute_version_bump.sh and
# extract-breaking-changes.sh, kept in step "by hand" and diffed by
# scripts/tests/detector-parity.test.sh. That diff is now structural: this file
# is the single source, and both scripts (plus the commit-lint gate, #193 M1)
# source it, so the copies CANNOT drift. detector-parity.test.sh now asserts the
# single-source wiring instead of diffing two copies.
#
# It defines the ONE marker regex, two detector functions and one gate helper and
# NOTHING else (no `set`, no main), so sourcing it never changes the caller's
# shell options:
#
#   _is_breaking_subject <subject>   rc 0 iff the SUBJECT declares a break
#                                    (Conventional-Commits `type!:` bang — BODY
#                                    footers are the _is_breaking_body job).
#   _is_breaking_body <raw-%B>       rc 0 iff the MESSAGE carries a real
#                                    BREAKING CHANGE footer.
#
# A marker counts as a real footer under two anchors:
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
#
# INV-15 marker regex — SINGLE SOURCE OF RECORD (bonnyr-f5 #193, r3 release/CI).
# The marker shape is hand-copied into the awk of _is_breaking_body and
# _under_detected_markers below and the awk of extract-breaking-changes.sh (a
# literal ERE cannot be passed through `awk -v` — that mangles `\*` on gawk/mawk/BSD
# alike — so the copies stay embedded rather than parametrised). This variable names
# the canonical form ONCE, and scripts/tests/detector-parity.test.sh asserts every
# embedded copy is byte-identical to it, so the sites cannot drift.
# shellcheck disable=SC2034  # read by scripts/tests/detector-parity.test.sh, which sources this lib
_BREAKING_MARKER_ERE='^([*-][[:space:]]+)?(\*\*)?BREAKING[[:space:] -]+CHANGE'
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

# _under_detected_markers <raw-%B> — PRINT every line that is a DECLARATIVE
# BREAKING CHANGE marker (the colon form `BREAKING CHANGE:`) sitting where the
# release detectors would MISS it: mis-anchored, i.e. NOT blank-anchored and NOT a
# trailer+colon footer. This is the EXACT COMPLEMENT of _is_breaking_body's footer
# recognition, computed line-by-line with the SAME anchoring state (prev_blank /
# prev_trailer / subject), so the commit-lint gate that consumes it:
#
#   * never flags a line the detector already accepts as a real footer — a
#     properly-anchored `BREAKING CHANGE:` is `recognised` here and NOT printed, so
#     a valid footer is never mis-reported as "you mis-anchored it"; and
#   * reports EVERY mis-anchored marker in the body, not just the first, and even
#     when another real footer coexists in the same message (bonnyr-f5 #193 r3:
#     scan the whole body, report every marker — the old first-footer short-circuit
#     hid a second mis-anchored marker).
#
# The COLON is load-bearing (bonnyr-f5 #193 r3 M-6b). Without it the gate also
# fired on COLONLESS marker-shaped prose that merely DESCRIBES the concept — e.g.
# an already-merged, unamendable body containing
#   "- BREAKING CHANGE footer in the BODY of a fix-subject commit → major"
# which every detector deliberately treats as inert. That reddened commit-lint (an
# ALWAYS_RUN gate) on the merge that cuts the release, unfixable without a history
# rewrite. A colonless marker that IS blank-anchored is still a real footer, caught
# by _is_breaking_body and thus `recognised` (never printed); requiring the colon
# narrows the gate to ONLY the mis-anchored-colon case the detectors miss, making
# "flagged by gate" the exact complement of "recognised by detector". Indented
# lines (leading whitespace) are NOT markers here — code/example blocks the
# detectors ignore, so the gate must not flag them. Embeds the canonical marker
# literal; detector-parity.test.sh locks every copy to _BREAKING_MARKER_ERE.
_under_detected_markers() {
  awk '
    BEGIN { prev_blank = 1; prev_trailer = 0 }
    /^[[:space:]]*$/ { prev_blank = 1; prev_trailer = 0; next }
    {
      is_marker  = ($0 ~ /^([*-][[:space:]]+)?(\*\*)?BREAKING[[:space:] -]+CHANGE/)
      is_colon   = ($0 ~ /^([*-][[:space:]]+)?(\*\*)?BREAKING[[:space:] -]+CHANGE(\*\*)?:/)
      is_trailer = ($0 ~ /^[A-Za-z0-9][A-Za-z0-9-]*:([[:space:]]|$)/)
      recognised = ((prev_blank && is_marker) || (prev_trailer && is_colon))
      if (is_colon && !recognised) print
      is_subject = (NR == 1 && $0 ~ /^[A-Za-z]+(\([^)]*\))?!?:[[:space:]]/)
      prev_blank = 0; prev_trailer = (is_trailer || is_subject)
    }
  ' <<< "$1"
}
