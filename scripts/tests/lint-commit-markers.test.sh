#!/usr/bin/env bash
# Mutation tests for scripts/lint-commit-markers.sh (bonnyr-f5 #193 B3 + M1; r3 M-6).
#
# Proves, on throwaway git repos:
#   rule 1 — a NEW [skip ci] commit anywhere in the scanned range is CAUGHT, while
#            the release-bot's OWN minted commit (version + trailing skip marker) is
#            exempt and a spoofed `release: <prose> [skip ci]` (no version) is CAUGHT.
#   rule 2 — the COMPLEMENT of the detectors: dash-bullet, markdown-bold and indented
#            shapes are NOT flagged; a mis-anchored DECLARATIVE `BREAKING CHANGE:`
#            (colon) marker IS flagged; a COLONLESS marker-shaped PROSE line the
#            detectors treat as inert is NOT flagged (M-6b — it used to red
#            unamendable release history); and EVERY mis-anchored marker in a body is
#            reported, not just the first.
#   M-6a  — there is NO already-merged exemption (removed as dead code); the release-
#            bot fingerprint is single-sourced and release.yml's inline copy matches.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
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
# run <repo> <RANGE>  -> sets global RC and OUT (BEFORE is no longer consumed)
run() {
  local d="$1" range="$2"
  OUT="$(cd "$d" && RANGE="$range" PR_TITLE="" LINT_MESSAGE="" bash "$SCRIPT" 2>&1)"
  RC=$?
}
# expect <label> <want-rc>
expect() { if [ "$RC" = "$2" ]; then pass "$1 (rc=$RC)"; else bad "$1 (rc=$RC want $2)"; echo "$OUT" | sed 's/^/    /'; fi; }

# ── R1.1 — a NEW [skip ci] commit in the range is CAUGHT ───────────────────────
D="$(new_repo)"; BASE="$(git -C "$D" rev-parse HEAD)"
commit "$D" "fix: real work" "regenerate everything [skip ci]"
run "$D" "${BASE}..HEAD"
expect "R1.1 new [skip ci] commit caught" 1
rm -rf "$D"

# ── R1.2 — spoofed `release: <prose> [skip ci]` (no version) is CAUGHT ──────────
D="$(new_repo)"; BASE="$(git -C "$D" rev-parse HEAD)"
commit "$D" "release: notes for the team [skip ci]"
run "$D" "${BASE}..HEAD"
expect "R1.2 spoofed release: prose subject caught" 1
rm -rf "$D"

# ── R1.3 — a REAL release-bot commit (version + trailing marker) is exempt ──────
D="$(new_repo)"; BASE="$(git -C "$D" rev-parse HEAD)"
commit "$D" "release: v1.2.3 [skip ci]"
run "$D" "${BASE}..HEAD"
expect "R1.3 real release-bot commit exempt" 0
rm -rf "$D"

# ── M1.1 — dash-bullet footer is NOT flagged (detector accepts it) ────────────
D="$(new_repo)"; BASE="$(git -C "$D" rev-parse HEAD)"
commit "$D" "fix: rework flags" "- BREAKING CHANGE: the --legacy flag was removed"
run "$D" "${BASE}..HEAD"
expect "M1.1 dash-bullet footer not flagged" 0
rm -rf "$D"

# ── M1.2 — markdown-bold footer is NOT flagged (detector accepts it) ──────────
D="$(new_repo)"; BASE="$(git -C "$D" rev-parse HEAD)"
commit "$D" "feat: z" "**BREAKING CHANGE:** boom."
run "$D" "${BASE}..HEAD"
expect "M1.2 markdown-bold footer not flagged" 0
rm -rf "$D"

# ── M1.3 — indented line is NOT flagged (detectors ignore it) ─────────────────
D="$(new_repo)"; BASE="$(git -C "$D" rev-parse HEAD)"
commit "$D" "fix: y" "    BREAKING CHANGE: indented, not a footer"
run "$D" "${BASE}..HEAD"
expect "M1.3 indented non-footer not flagged" 0
rm -rf "$D"

# ── M1.4 — a DECLARATIVE marker the detectors MISS (mis-anchored colon) IS flagged
D="$(new_repo)"; BASE="$(git -C "$D" rev-parse HEAD)"
commit "$D" "fix: subject" $'a prose sentence about the change\n- BREAKING CHANGE: this bullet is not blank-anchored'
run "$D" "${BASE}..HEAD"
expect "M1.4 under-detected mis-anchored colon marker flagged" 1
rm -rf "$D"

# ── M-6b — a COLONLESS marker-shaped PROSE line is NOT flagged ─────────────────
# This is the shape of the already-merged, unamendable commit 4a52ed4 that used to
# red the push-to-main range. `- BREAKING CHANGE footer in the body …` has no colon
# after CHANGE, so it is prose describing the concept, which the detectors treat as
# inert. The gate must let it pass, or it reds unamendable history.
D="$(new_repo)"; BASE="$(git -C "$D" rev-parse HEAD)"
commit "$D" "fix: subject" $'Adds a self-test case:\n- BREAKING CHANGE footer in the BODY of a fix-subject commit -> major (non-'
run "$D" "${BASE}..HEAD"
expect "M-6b colonless marker-shaped prose not flagged" 0
rm -rf "$D"

# ── M-6b(2) — the mis-anchored-colonless case is NOT rescued by an exemption ────
# Same shape, but the marker commit is the FIRST in the range (a merge-base-like
# position). There is no already-merged exemption any more, so this must pass on the
# strength of rule 2 no longer flagging it — NOT on an exemption.
D="$(new_repo)"
commit "$D" "fix: subject" $'Adds a self-test case:\n- BREAKING CHANGE footer in the BODY of a fix-subject commit -> major (non-'
commit "$D" "fix: later clean commit"
BASE0="$(git -C "$D" rev-list --max-parents=0 HEAD)"
run "$D" "${BASE0}..HEAD"
expect "M-6b(2) colonless prose in first commit passes (no exemption needed)" 0
if printf '%s' "$OUT" | grep -q 'is exempt'; then bad "M-6b(2) must pass via rule 2, not an exemption"; else pass "M-6b(2) passed without any exemption firing"; fi
rm -rf "$D"

# ── M1.6 — TWO mis-anchored colon markers in one body are BOTH reported ────────
D="$(new_repo)"; BASE="$(git -C "$D" rev-parse HEAD)"
commit "$D" "fix: subject" $'some prose here\n- BREAKING CHANGE: first mis-anchored break\nmore prose text\n- BREAKING CHANGE: second mis-anchored break'
run "$D" "${BASE}..HEAD"
expect "M1.6 two mis-anchored markers -> fail" 1
n_first=$(printf '%s\n' "$OUT" | grep -c 'first mis-anchored break')
n_second=$(printf '%s\n' "$OUT" | grep -c 'second mis-anchored break')
if [ "$n_first" -ge 1 ] && [ "$n_second" -ge 1 ]; then
  pass "M1.6 BOTH mis-anchored markers reported (no first-footer short-circuit)"
else
  bad "M1.6 expected BOTH markers reported (first=$n_first second=$n_second)"; echo "$OUT" | sed 's/^/    /'
fi
rm -rf "$D"

# ── M6.PRTITLE (bonnyr-f5 #193 r4 M-6 / B6b) — the PR_TITLE lint has coverage ──
# A skip marker in the PR title (GitHub's squash subject for a >=2-commit PR) is
# CAUGHT, attributed to the PR-title path; a clean PR title passes. Mutation: delete
# the PR_TITLE block in lint-commit-markers.sh and this reds.
D="$(new_repo)"
OUT="$(cd "$D" && RANGE="HEAD..HEAD" PR_TITLE="feat: shiny [skip ci]" LINT_MESSAGE="" bash "$SCRIPT" 2>&1)"; RC=$?
expect "M6.PRTITLE marker in PR title caught" 1
if printf '%s' "$OUT" | grep -q 'PR title'; then pass "M6.PRTITLE flagged via the PR-title path"; else bad "M6.PRTITLE not attributed to the PR title"; fi
OUT="$(cd "$D" && RANGE="HEAD..HEAD" PR_TITLE="feat: shiny thing" LINT_MESSAGE="" bash "$SCRIPT" 2>&1)"; RC=$?
expect "M6.PRTITLE clean PR title passes" 0
rm -rf "$D"

# ── M6.LINTMSG (bonnyr-f5 #193 r4 M-6 / M1) — the LINT_MESSAGE lint has coverage ─
# Arbitrary pending text (release.yml passes inputs.release_notes) is linted through
# both rules before it becomes the release commit/tag. A skip marker in it is CAUGHT,
# attributed to the message-text path. Mutation: delete the LINT_MESSAGE block and this reds.
D="$(new_repo)"
OUT="$(cd "$D" && RANGE="HEAD..HEAD" PR_TITLE="" LINT_MESSAGE="ship notes [skip ci]" LINT_MESSAGE_LABEL="release_notes input" bash "$SCRIPT" 2>&1)"; RC=$?
expect "M6.LINTMSG marker in message text caught" 1
if printf '%s' "$OUT" | grep -q 'release_notes input'; then pass "M6.LINTMSG flagged via the message-text path"; else bad "M6.LINTMSG not attributed to the message text"; fi
rm -rf "$D"

# ── M6.RANGE (bonnyr-f5 #193 r4 M-6) — an explicit unresolvable RANGE fails CLOSED
# rather than silently falling back to scanning the tip while claiming the range was
# scanned. Mutation: replace the fail-closed branch with a `|| true` fallback and this
# reds (an unresolvable range would then scan nothing and pass rc=0).
D="$(new_repo)"
OUT="$(cd "$D" && RANGE="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef..HEAD" PR_TITLE="" LINT_MESSAGE="" bash "$SCRIPT" 2>&1)"; RC=$?
expect "M6.RANGE unresolvable RANGE fails closed" 1
if printf '%s' "$OUT" | grep -q 'not a resolvable revision range'; then pass "M6.RANGE reported the range as unresolvable"; else bad "M6.RANGE wrong/absent unresolvable message"; fi
rm -rf "$D"

# ── M6.SKIPCHECKS (bonnyr-f5 #193 r4 M-6) — GitHub's `skip-checks: true` commit
# trailer is CAUGHT even though it is NOT a bracketed token (the fixed-string marker
# list alone misses it). Mutation: delete the skip-checks rule and this reds.
D="$(new_repo)"; BASE="$(git -C "$D" rev-parse HEAD)"
commit "$D" "fix: real work" $'legitimate body\nskip-checks: true'
run "$D" "${BASE}..HEAD"
expect "M6.SKIPCHECKS skip-checks:true trailer caught" 1
if printf '%s' "$OUT" | grep -q "skip-checks: true"; then pass "M6.SKIPCHECKS flagged the trailer"; else bad "M6.SKIPCHECKS message missing"; fi
rm -rf "$D"

# ── R2.TIP (bonnyr-f5 #193 r4) — a forged release-bot subject buried MID-RANGE is
# NOT exempt (the exemption is scoped to the range TIP). Its skip marker is CAUGHT.
# Mutation: drop the `sha == range_tip` scoping and this reds (the forgery escapes).
D="$(new_repo)"; BASE="$(git -C "$D" rev-parse HEAD)"
commit "$D" "release: v9.9.9 [skip ci]" "sneaky marker [skip ci] buried in a mid-range forgery"
commit "$D" "fix: clean tip commit"
run "$D" "${BASE}..HEAD"
expect "R2.TIP forged release-bot subject mid-range caught" 1
rm -rf "$D"

# ── R2.TIP2 — the REAL release-bot commit AS the tip is still exempt ────────────
D="$(new_repo)"; BASE="$(git -C "$D" rev-parse HEAD)"
commit "$D" "release: v1.2.3 [skip ci]"
run "$D" "${BASE}..HEAD"
expect "R2.TIP2 real release-bot commit at tip still exempt" 0
if printf '%s' "$OUT" | grep -q 'range tip'; then pass "R2.TIP2 exempted via the tip-scoped path"; else bad "R2.TIP2 not exempted at the tip"; fi
rm -rf "$D"

# ── PUB1 (bonnyr-f5 #193 r4) — PUBLISHED, unamendable history is exempt. A commit
# reachable from the last release tag carrying a mis-anchored colon marker is NOT
# flagged when a later commit is the tip. Mutation: drop exemption 1 and this reds.
D="$(new_repo)"; BASE="$(git -C "$D" rev-parse HEAD)"
commit "$D" "fix: shipped" $'prose about the change\n- BREAKING CHANGE: this mis-anchored marker already shipped'
git -C "$D" tag -a "v1.0.0" -m "release v1.0.0"
commit "$D" "fix: later clean work on the next release"
run "$D" "${BASE}..HEAD"
expect "PUB1 mis-anchored marker in published history is exempt" 0
if printf '%s' "$OUT" | grep -q 'published history'; then pass "PUB1 exempted via the published-history path"; else bad "PUB1 not exempted as published history"; fi
rm -rf "$D"

# ── SS-1 — release-bot fingerprint single-source: release.yml's inline copy of the
# two grep predicates is byte-identical to scripts/lib/is-release-bot-subject.sh ──
LIB="$ROOT/scripts/lib/is-release-bot-subject.sh"
YML="$ROOT/.github/workflows/release.yml"
P1="grep -qE '^release: v[0-9]+\\.[0-9]+\\.[0-9]+'"
P2="grep -qE '\\[skip ci\\]\$'"
ok=1
for f in "$LIB" "$YML"; do
  grep -qF -- "$P1" "$f" || { bad "SS-1 $(basename "$f") missing version predicate '$P1'"; ok=0; }
  grep -qF -- "$P2" "$f" || { bad "SS-1 $(basename "$f") missing skip-marker predicate"; ok=0; }
done
[ "$ok" = 1 ] && pass "SS-1 release-bot predicate byte-identical in lib and release.yml"

echo "----"
[ "$fail" = 0 ] && echo "ALL PASS" || { echo "FAILURES"; exit 1; }
