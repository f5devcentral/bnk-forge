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

# 1. The shared lib exists and defines both functions.
[ -f "$LIB" ] || { echo "::error::detector-parity: shared lib $LIB not found"; exit 1; }
for fn in _is_breaking_subject _is_breaking_body; do
  if ! grep -qE "^${fn}\(\)" "$LIB"; then
    echo "::error::detector-parity: $LIB does not define ${fn}()"; fail=1
  fi
done

# 2. Every consumer sources the lib and does NOT redefine either function inline.
for c in "${CONSUMERS[@]}"; do
  [ -f "$c" ] || { echo "::error::detector-parity: consumer $c not found"; fail=1; continue; }
  if ! grep -q 'lib/breaking-change-detect.sh' "$c"; then
    echo "::error::detector-parity: $(basename "$c") does not source the shared detector lib"; fail=1
  fi
  for fn in _is_breaking_subject _is_breaking_body; do
    if grep -qE "^${fn}\(\)" "$c"; then
      echo "::error::detector-parity: $(basename "$c") redefines ${fn}() inline — it would shadow the shared lib and can drift"; fail=1
    fi
  done
done

# 3. Behavioural smoke test: the shared predicate actually classifies. Guards
# against an emptied/stubbed lib passing the wiring checks vacuously.
# shellcheck source=scripts/lib/breaking-change-detect.sh
. "$LIB"
if ! _is_breaking_body $'fix: y\n\nBREAKING CHANGE: the flag was removed'; then
  echo "::error::detector-parity: shared _is_breaking_body missed a real footer"; fail=1
fi
if _is_breaking_body $'fix: y\n\nthis is not a breaking change at all'; then
  echo "::error::detector-parity: shared _is_breaking_body false-positived on prose"; fail=1
fi
if ! _is_breaking_subject 'feat!: drop v1'; then
  echo "::error::detector-parity: shared _is_breaking_subject missed a bang subject"; fail=1
fi
if _is_breaking_subject 'feat: normal change'; then
  echo "::error::detector-parity: shared _is_breaking_subject false-positived on a normal subject"; fail=1
fi

# 4. Marker-regex single source (bonnyr-f5 #193 r3): the marker shape used to be
# hand-copied ~6 times. It is now named ONCE as _BREAKING_MARKER_ERE; the grep
# predicates interpolate it and the awk copies embed the identical literal (an ERE
# through `awk -v` mangles `\*`). Assert (a) the variable is defined, and (b) every
# place that uses the marker AS A REGEX embeds the byte-identical canonical string,
# so the copies cannot drift. The regex form carries the distinctive
# `BREAKING[[:space:] -]+CHANGE` token that prose comments never contain.
EXPECT='^([*-][[:space:]]+)?(\*\*)?BREAKING[[:space:] -]+CHANGE'
if [ -z "${_BREAKING_MARKER_ERE:-}" ]; then
  echo "::error::detector-parity: _BREAKING_MARKER_ERE is not defined in the shared lib"; fail=1
elif [ "$_BREAKING_MARKER_ERE" != "$EXPECT" ]; then
  echo "::error::detector-parity: _BREAKING_MARKER_ERE drifted from the canonical marker shape"; fail=1
fi
# Every file that embeds the marker regex must embed EXACTLY the canonical string.
MARKER_CONSUMERS=(
  "$ROOT/scripts/lib/breaking-change-detect.sh"
  "$ROOT/scripts/extract-breaking-changes.sh"
)
for f in "${MARKER_CONSUMERS[@]}"; do
  [ -f "$f" ] || { echo "::error::detector-parity: marker consumer $f not found"; fail=1; continue; }
  n_uses=0
  while IFS= read -r ln; do
    n_uses=$((n_uses + 1))
    case "$ln" in
      *"$EXPECT"*) : ;;
      *) echo "::error::detector-parity: $(basename "$f") has a DRIFTED marker regex: $ln"; fail=1 ;;
    esac
  done < <(grep -F 'BREAKING[[:space:] -]+CHANGE' "$f")
  if [ "$n_uses" -eq 0 ]; then
    echo "::error::detector-parity: $(basename "$f") has no marker-regex use — enumeration broke (vacuous parity)"; fail=1
  fi
done

if [ "$fail" -eq 0 ]; then
  echo "INV-15 OK: detector + marker regex single-sourced in scripts/lib/breaking-change-detect.sh; all consumers source/embed the one definition, none shadow it, predicate behaves"
else
  echo "::error::detector-parity: one or more checks failed"; exit 1
fi
