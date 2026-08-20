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

SINCE="${1:?usage: extract-breaking-changes.sh <since_ref> [until_ref]}"
UNTIL="${2:-HEAD}"

block=""
while IFS= read -r sha; do
  [[ -z "$sha" ]] && continue
  body=$(git log -1 --format="%b" "$sha" 2>/dev/null || true)
  # Uppercase footer/marker only (spec form), so body prose like "not a
  # breaking change" does not false-trigger.
  if grep -qE '\bBREAKING[[:space:] -]+CHANGE\b' <<< "$body"; then
    subj=$(git log -1 --format="%s" "$sha" 2>/dev/null || true)
    # The BREAKING CHANGE line and its paragraph (up to the next blank line),
    # flattened to one line and stripped of markdown bold.
    note=$(awk '/BREAKING[[:space:] -]+CHANGE/{p=1} p{print} p&&/^$/{exit}' <<< "$body" \
      | tr '\n' ' ' | sed 's/\*\*//g; s/  */ /g; s/ *$//')
    block="${block}- **${subj}**
  ${note}
"
  fi
done < <(git log "${SINCE}..${UNTIL}" --pretty=format:"%H" 2>/dev/null || true)

if [[ -n "$block" ]]; then
  printf '### ⚠️ Breaking Changes\n\n%s\n' "$block"
fi
