#!/usr/bin/env bash
# INV-15 detector parity (bonnyr-f5 #179 r3; #193 M5).
#
# The BREAKING CHANGE detector MUST be byte-identical between
# scripts/extract-breaking-changes.sh and scripts/compute_version_bump.sh. If they
# drift, a major bump ships with empty notes (or a note ships with no bump). This
# asserts that identity in code, replacing the "MUST stay identical" comment.
#
# It lives here as a scripts/tests/*.test.sh so BOTH the Makefile `script-selftests`
# target AND ci.yml's `script-selftests` job run it through the SAME filesystem
# enumeration — local == CI. Previously this parity diff was inline in ci.yml only,
# so `make script-selftests` was strictly narrower than the CI job it claimed to
# mirror (bonnyr-f5 #193 M5).
#
# The extraction is version-agnostic on purpose: #179 factors detection into
# _is_breaking_subject and _is_breaking_body FUNCTIONS (the body one is a
# paragraph-aware awk, not a single grep). When those functions exist, diff their
# full bodies. On a pre-#179 tree (inline greps, no functions) fall back to
# extracting the detector regex, so the gate stays meaningful in either state.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
E="$ROOT/scripts/extract-breaking-changes.sh"
C="$ROOT/scripts/compute_version_bump.sh"

for f in "$E" "$C"; do
  [ -f "$f" ] || { echo "::error::detector-parity: $f not found"; exit 1; }
done

_fn() { sed -n "/^$2()/,/^}/p" "$1"; }        # print a function definition
_regex() {
  grep -oE "grep -qE '[^']*BREAKING[^']*CHANGE[^']*'" "$1" \
    | sed -E "s/^grep -qE '//; s/'\$//" | sort -u
}

if grep -q '^_is_breaking_body()' "$E" && grep -q '^_is_breaking_body()' "$C"; then
  for fn in _is_breaking_subject _is_breaking_body; do
    if [ "$(_fn "$E" "$fn")" != "$(_fn "$C" "$fn")" ]; then
      echo "::error::INV-15 violated: $fn differs between the two scripts"
      diff <(_fn "$E" "$fn") <(_fn "$C" "$fn") || true
      exit 1
    fi
  done
  echo "INV-15 OK: _is_breaking_subject + _is_breaking_body are byte-identical functions in both scripts"
else
  ex="$(_regex "$E")"; cv="$(_regex "$C")"
  if [ -z "$ex" ] || [ -z "$cv" ]; then
    echo "::error::could not extract a BREAKING CHANGE detector from one of the scripts (extract='$ex' compute='$cv')"; exit 1
  fi
  if [ "$ex" != "$cv" ]; then
    echo "::error::INV-15 violated: the BREAKING CHANGE detector regex differs between the two scripts"
    echo "  extract: $ex"; echo "  compute: $cv"; exit 1
  fi
  echo "INV-15 OK (pre-#179 tree): detector regex identical across both scripts -> $ex"
fi
