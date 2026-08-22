#!/usr/bin/env bash
# INV-15 detector parity (bonnyr-f5 #179 r3; #193 M1/M5).
#
# The BREAKING CHANGE detector MUST be identical everywhere it is used:
# scripts/compute_version_bump.sh (the bump), scripts/extract-breaking-changes.sh
# (the note), and scripts/lint-commit-markers.sh (the commit-lint gate). If they
# drift, a major bump ships with empty notes, or a note ships with no bump, or the
# gate flags a shape the detectors accept.
#
# It USED to diff two byte-identical inline copies. Round-2 single-sources the
# predicate into scripts/lib/breaking-change-detect.sh, so drift is now
# structurally impossible; this test asserts that wiring:
#   1. the shared lib exists and defines BOTH functions;
#   2. every consumer SOURCES the lib and does NOT redefine either function
#      inline (an inline copy would silently shadow the shared one and reopen the
#      drift the single-source closes);
#   3. the shared predicate behaves — a representative positive and negative body,
#      so an empty/stubbed lib cannot pass vacuously.
#
# It lives here as a scripts/tests/*.test.sh so BOTH the Makefile `script-selftests`
# target AND ci.yml's `script-selftests` job run it through the SAME filesystem
# enumeration — local == CI (bonnyr-f5 #193 M5).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
LIB="$ROOT/scripts/lib/breaking-change-detect.sh"
CONSUMERS=(
  "$ROOT/scripts/compute_version_bump.sh"
  "$ROOT/scripts/extract-breaking-changes.sh"
  "$ROOT/scripts/lint-commit-markers.sh"
)

fail=0
# Standard self-test output convention (PASS lines + ALL PASS / FAILURES terminal),
# matching the other scripts/tests/*.test.sh so the Makefile `script-selftests`
# harness can assert on it uniformly (bonnyr-f5 #193 r4 M-4).
pass() { printf 'PASS  %s\n' "$1"; }
bad()  { printf 'FAIL  %s\n' "$1"; fail=1; }

# 1. The shared lib exists and defines both functions.
[ -f "$LIB" ] || { echo "::error::detector-parity: shared lib $LIB not found"; echo "FAILURES"; exit 1; }
for fn in _is_breaking_subject _is_breaking_body; do
  if grep -qE "^${fn}\(\)" "$LIB"; then pass "shared lib defines ${fn}()"
  else bad "$LIB does not define ${fn}()"; fi
done

# 2. Every consumer sources the lib and does NOT redefine either function inline.
for c in "${CONSUMERS[@]}"; do
  [ -f "$c" ] || { bad "consumer $c not found"; continue; }
  if grep -q 'lib/breaking-change-detect.sh' "$c"; then pass "$(basename "$c") sources the shared detector lib"
  else bad "$(basename "$c") does not source the shared detector lib"; fi
  for fn in _is_breaking_subject _is_breaking_body; do
    if grep -qE "^${fn}\(\)" "$c"; then
      bad "$(basename "$c") redefines ${fn}() inline — it would shadow the shared lib and can drift"
    else pass "$(basename "$c") does not shadow ${fn}()"; fi
  done
done

# 3. Behavioural smoke test: the shared predicate actually classifies. Guards
# against an emptied/stubbed lib passing the wiring checks vacuously.
# shellcheck source=scripts/lib/breaking-change-detect.sh
. "$LIB"
if _is_breaking_body $'fix: y\n\nBREAKING CHANGE: the flag was removed'; then pass "shared _is_breaking_body accepts a real footer"
else bad "shared _is_breaking_body missed a real footer"; fi
if _is_breaking_body $'fix: y\n\nthis is not a breaking change at all'; then bad "shared _is_breaking_body false-positived on prose"
else pass "shared _is_breaking_body rejects prose"; fi
if _is_breaking_subject 'feat!: drop v1'; then pass "shared _is_breaking_subject accepts a bang subject"
else bad "shared _is_breaking_subject missed a bang subject"; fi
if _is_breaking_subject 'feat: normal change'; then bad "shared _is_breaking_subject false-positived on a normal subject"
else pass "shared _is_breaking_subject rejects a normal subject"; fi

# 4. Marker-regex single source (bonnyr-f5 #193 r3; enumeration hardened r4 M-3).
# The marker shape is named ONCE as _BREAKING_MARKER_ERE; the awk copies embed the
# identical literal (an ERE through `awk -v` mangles `\*`). Assert it is defined and
# canonical:
EXPECT='^([*-][[:space:]]+)?(\*\*)?BREAKING[[:space:] -]+CHANGE'
if [ -z "${_BREAKING_MARKER_ERE:-}" ]; then
  bad "_BREAKING_MARKER_ERE is not defined in the shared lib"
elif [ "$_BREAKING_MARKER_ERE" != "$EXPECT" ]; then
  bad "_BREAKING_MARKER_ERE drifted from the canonical marker shape"
else
  pass "_BREAKING_MARKER_ERE == canonical marker shape"
fi

# Enumerate the embedded copies by POSITION / COUNT, NOT by matching the driftable
# token (bonnyr-f5 #193 r4 M-3). The old check enumerated with
# `grep -F 'BREAKING[[:space:] -]+CHANGE'` — the VERY token that drifts — so a copy
# that drifted IN the token vanished from the enumeration and was never compared
# (drifting :96-97 dropped the count 5->3 yet the test stayed green). Two independent
# guards close it:
#   (a) EXACT COUNT of byte-identical canonical strings per file — a copy that drifts,
#       or is added / removed, changes the count away from the structural expectation;
#   (b) LOOSE enumeration of every marker-regex SITE via a STABLE anchor that does NOT
#       contain the driftable char class (an awk `~ /^…CHANGE…/` or `!~ /^…CHANGE…/`
#       match, or the `_BREAKING_MARKER_ERE=` assignment), asserting each site embeds
#       the canonical string. A drift keeps both `CHANGE` and the `~ /^` context, so
#       the site stays enumerated and fails the embed check even under a compensating
#       add that keeps the count unchanged. Prose comments carry no `~ /^…CHANGE` and
#       are not enumerated.
# The per-file count is a deliberate STRUCTURAL invariant: adding or removing a copy is
# a reviewable event that must update it here.  spec = file:expected-canonical-count
for spec in \
  "scripts/lib/breaking-change-detect.sh:5" \
  "scripts/extract-breaking-changes.sh:3"
do
  f="${spec%:*}"; want="${spec##*:}"; p="$ROOT/$f"; b="$(basename "$f")"
  if [ ! -f "$p" ]; then bad "marker consumer $f not found"; continue; fi
  got="$(grep -cF "$EXPECT" "$p" || true)"
  if [ "$got" -eq "$want" ]; then
    pass "$b embeds exactly $want byte-identical canonical marker regex(es)"
  else
    bad "$b embeds $got byte-identical marker regex(es), expected $want — a copy drifted from, or was added to / removed from, the canonical shape"
  fi
  drift=0
  while IFS= read -r ln; do
    case "$ln" in
      *"$EXPECT"*) : ;;
      *) bad "$b: marker-regex site drifted from canonical: $ln"; drift=1 ;;
    esac
  done < <(grep -E '(~|!~)[[:space:]]*/\^[^/]*CHANGE|_BREAKING_MARKER_ERE=' "$p")
  [ "$drift" -eq 0 ] && pass "$b: every marker-regex site embeds the canonical shape"
done

if [ "$fail" -eq 0 ]; then
  echo "INV-15 OK: detector + marker regex single-sourced in scripts/lib/breaking-change-detect.sh; all consumers source/embed the one definition, none shadow it, predicate behaves"
  echo "ALL PASS"
else
  echo "::error::detector-parity: one or more checks failed"
  echo "FAILURES"; exit 1
fi
