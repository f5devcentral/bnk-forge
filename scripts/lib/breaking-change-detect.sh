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
# It defines two functions and NOTHING else (no `set`, no main), so sourcing it
# never changes the caller's shell options:
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

# _looks_like_breaking_marker <line> — rc 0 iff the LINE is shaped like a
# BREAKING CHANGE marker a human meant as a footer: the SAME `is_marker` shape
# _is_breaking_body uses (column 0, optional `-`/`*` bullet or `**` bold), so the
# two never disagree about what "a marker" is. The commit-lint gate uses this as
# the COMPLEMENT of _is_breaking_body (bonnyr-f5 #193 M1): a line that looks like a
# marker but sits where _is_breaking_body would MISS it (not blank-anchored, no
# trailer colon) would NOT trigger the intended major bump, so the gate flags it.
# Indented lines (leading whitespace) are deliberately NOT markers here — they are
# code/example blocks the detectors ignore, so the gate must not flag them either.
_looks_like_breaking_marker() {
  grep -qE '^([*-][[:space:]]+)?(\*\*)?BREAKING[[:space:] -]+CHANGE' <<< "$1"
}
