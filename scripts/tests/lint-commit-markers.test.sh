#!/usr/bin/env bash
# Mutation tests for scripts/lint-commit-markers.sh (bonnyr-f5 #193 B3 + M1).
#
# Proves, on throwaway git repos, the two round-2 behaviours:
#   B3 — already-merged is keyed on the PRE-PUSH BASE (github.event.before), not
#        the post-push tip, so a NEW [skip ci] commit on a push to main/staging is
#        CAUGHT while a commit that was already in the base stays exempt; and the
#        release-bot exemption is the version+trailing-marker fingerprint, so a
#        spoofed `release: <prose> [skip ci]` subject is CAUGHT.
#   M1 — rule 2 is the COMPLEMENT of the detectors: dash-bullet, markdown-bold and
#        indented shapes are NOT flagged, while a marker the detectors would MISS
#        (a mis-anchored bullet) IS flagged, and the flag is exemptible.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$HERE/../lint-commit-markers.sh"

fail=0
pass() { printf 'PASS  %s\n' "$1"; }
bad()  { printf 'FAIL  %s\n' "$1"; fail=1; }

# new_repo -> prints the repo path; caller adds commits then runs `lint`.
new_repo() {
  local d; d="$(mktemp -d)"
  git init -q "$d"
  git -C "$d" config user.email "selftest@bnk-forge.local"
  git -C "$d" config user.name "bnk-forge self-test"
  git -C "$d" commit --allow-empty -q --cleanup=verbatim -m "initial"
  printf '%s' "$d"
}
# commit <repo> <subject> [body]   (verbatim: exact body, leading spaces kept)
commit() {
  local d="$1" subj="$2" body="${3:-}"
  if [ -n "$body" ]; then
    git -C "$d" commit --allow-empty -q --cleanup=verbatim -m "$subj" -m "$body"
  else
    git -C "$d" commit --allow-empty -q --cleanup=verbatim -m "$subj"
  fi
}
# run <repo> <RANGE> <BEFORE>  -> sets global RC and OUT
run() {
  local d="$1" range="$2" before="$3"
  OUT="$(cd "$d" && RANGE="$range" BEFORE="$before" PR_TITLE="" LINT_MESSAGE="" bash "$SCRIPT" 2>&1)"
  RC=$?
}
# expect <label> <want-rc>
expect() { if [ "$RC" = "$2" ]; then pass "$1 (rc=$RC)"; else bad "$1 (rc=$RC want $2)"; echo "$OUT" | sed 's/^/    /'; fi; }

# ── B3.1 — NEW [skip ci] commit on a push (base = pre-push tip) is CAUGHT ──────
D="$(new_repo)"; BASE="$(git -C "$D" rev-parse HEAD)"
commit "$D" "fix: real work" "regenerate everything [skip ci]"
run "$D" "${BASE}..HEAD" "$BASE"
expect "B3.1 new [skip ci] commit caught (not exempted by post-push tip)" 1
rm -rf "$D"

# ── B3.2 — a commit already in the PRE-PUSH base stays exempt ──────────────────
# Range scans C1..C3 but the branch's pre-push tip was C1, so C1 (which carries a
# marker) is exempt while the clean C2/C3 pass. Proves base-anchoring exempts
# genuinely-already-merged history without re-linting it.
D="$(new_repo)"
commit "$D" "chore: merged earlier" "old note [skip ci]"; C1="$(git -C "$D" rev-parse HEAD)"
commit "$D" "fix: two"
commit "$D" "fix: three"
BASE0="$(git -C "$D" rev-list --max-parents=0 HEAD)"
run "$D" "${BASE0}..HEAD" "$C1"
expect "B3.2 commit already in pre-push base is exempt" 0
rm -rf "$D"

# ── B3.3 — spoofed `release: <prose> [skip ci]` (no version) is CAUGHT ─────────
D="$(new_repo)"; BASE="$(git -C "$D" rev-parse HEAD)"
commit "$D" "release: notes for the team [skip ci]"
run "$D" "${BASE}..HEAD" "$BASE"
expect "B3.3 spoofed release: prose subject caught" 1
rm -rf "$D"

# ── B3.4 — a REAL release-bot commit (version + trailing marker) is exempt ─────
D="$(new_repo)"; BASE="$(git -C "$D" rev-parse HEAD)"
commit "$D" "release: v1.2.3 [skip ci]"
run "$D" "${BASE}..HEAD" "$BASE"
expect "B3.4 real release-bot commit exempt" 0
rm -rf "$D"

# ── M1.1 — dash-bullet footer is NOT flagged (detector accepts it) ────────────
D="$(new_repo)"; BASE="$(git -C "$D" rev-parse HEAD)"
commit "$D" "fix: rework flags" "- BREAKING CHANGE: the --legacy flag was removed"
run "$D" "${BASE}..HEAD" "$BASE"
expect "M1.1 dash-bullet footer not flagged" 0
rm -rf "$D"

# ── M1.2 — markdown-bold footer is NOT flagged (detector accepts it) ──────────
D="$(new_repo)"; BASE="$(git -C "$D" rev-parse HEAD)"
commit "$D" "feat: z" "**BREAKING CHANGE:** boom."
run "$D" "${BASE}..HEAD" "$BASE"
expect "M1.2 markdown-bold footer not flagged" 0
rm -rf "$D"

# ── M1.3 — indented line is NOT flagged (detectors ignore it) ─────────────────
D="$(new_repo)"; BASE="$(git -C "$D" rev-parse HEAD)"
commit "$D" "fix: y" "    BREAKING CHANGE: indented, not a footer"
run "$D" "${BASE}..HEAD" "$BASE"
expect "M1.3 indented non-footer not flagged" 0
rm -rf "$D"

# ── M1.4 — a marker the detectors MISS (mis-anchored bullet) IS flagged ───────
D="$(new_repo)"; BASE="$(git -C "$D" rev-parse HEAD)"
commit "$D" "fix: subject" $'a prose sentence about the change\n- BREAKING CHANGE: this bullet is not blank-anchored'
run "$D" "${BASE}..HEAD" "$BASE"
expect "M1.4 under-detected mis-anchored marker flagged" 1
rm -rf "$D"

# ── M1.5 — the same under-detected marker is EXEMPT when already-merged ────────
D="$(new_repo)"
commit "$D" "fix: subject" $'a prose sentence about the change\n- BREAKING CHANGE: this bullet is not blank-anchored'
CX="$(git -C "$D" rev-parse HEAD)"
commit "$D" "fix: later clean commit"
BASE0="$(git -C "$D" rev-list --max-parents=0 HEAD)"
run "$D" "${BASE0}..HEAD" "$CX"
expect "M1.5 under-detected marker exempt when in pre-push base" 0
rm -rf "$D"

echo "----"
[ "$fail" = 0 ] && echo "ALL PASS" || { echo "FAILURES"; exit 1; }
