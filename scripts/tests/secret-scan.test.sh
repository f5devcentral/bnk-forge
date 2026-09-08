#!/usr/bin/env bash
# Mutation tests for scripts/secret-scan.sh (bonnyr-f5 #193 r3 M-7).
#
# A fake `docker` on PATH stands in for the gitleaks container so the backstop
# logic is exercised with NO docker daemon and NO gitleaks image, against a REAL
# throwaway git repo so `git rev-list --count "$range"` resolves for real.
#
# Headline (M-7): a delete-only commit range legitimately makes gitleaks print
# "0 commits scanned" and exit 0 (it scans ADDED content; a deletion adds none).
# The old count==0 backstop RED that pushable change; the fix must PASS it while
# still failing on a real leak, a git error, a missing summary, and an unresolvable
# range.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SCAN="$HERE/../secret-scan.sh"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# ── throwaway repo with a DELETE-ONLY tip commit ──────────────────────────────
REPO="$WORK/repo"
git init -q "$REPO"
git -C "$REPO" config user.email "selftest@bnk-forge.local"
git -C "$REPO" config user.name "bnk-forge self-test"
printf 'hello\nworld\n' > "$REPO/a.txt"; printf 'more\n' > "$REPO/b.txt"
git -C "$REPO" add -A && git -C "$REPO" commit -q -m "add files"
BEFORE="$(git -C "$REPO" rev-parse HEAD)"
git -C "$REPO" rm -q a.txt b.txt && git -C "$REPO" commit -q -m "delete only"
AFTER="$(git -C "$REPO" rev-parse HEAD)"
DEL_RANGE="${BEFORE}..${AFTER}"

# ── fake docker: canned gitleaks output + exit code, driven by $SCEN ──────────
cat > "$WORK/docker" <<'FAKE'
#!/usr/bin/env bash
case "${SCEN:-}" in
  delete_only) printf '%s\n' "10:00AM INF 0 commits scanned." "10:00AM INF no leaks found"; exit 0 ;;
  real_leak)   printf '%s\n' "10:00AM INF 1 commits scanned." "Finding: AWS key ... redacted" "10:00AM WRN leaks found: 1"; exit 1 ;;
  git_err)     printf '%s\n' "10:00AM ERR [git] exit status 128 fatal: bad revision" "10:00AM INF 0 commits scanned."; exit 0 ;;
  no_summary)  printf '%s\n' "10:00AM INF nothing useful here"; exit 0 ;;
  clean_ok)    printf '%s\n' "10:00AM INF 3 commits scanned." "10:00AM INF no leaks found"; exit 0 ;;
  *) echo "unknown SCEN" >&2; exit 99 ;;
esac
FAKE
chmod +x "$WORK/docker"

run() {  # SCEN, RANGE  -> prints rc
  SCEN="$1" PATH="$WORK:$PATH" RANGE="$2" bash "$SCAN" >/dev/null 2>&1
  echo $?
}

fail=0
check() { if [ "$2" = "$3" ]; then printf 'PASS  %-52s rc=%s\n' "$1" "$2"; else printf 'FAIL  %-52s rc=%s want %s\n' "$1" "$2" "$3"; fail=1; fi; }

# M-7 headline: delete-only range (git sees 1 commit, gitleaks scanned 0) PASSES.
check "delete-only range passes (M-7)"        "$(cd "$REPO" && run delete_only "$DEL_RANGE")" 0
# A real leak still fails (gitleaks nonzero exit).
check "real leak fails"                        "$(cd "$REPO" && run real_leak "$DEL_RANGE")"   1
# A git error (blind scan) fails via the ERR backstop.
check "git error fails (blind scan)"           "$(cd "$REPO" && run git_err "$DEL_RANGE")"     1
# No summary line => no evidence the scan ran => fail.
check "missing summary line fails"             "$(cd "$REPO" && run no_summary "$DEL_RANGE")"  1
# A clean multi-commit scan passes.
check "clean multi-commit scan passes"         "$(cd "$REPO" && run clean_ok "$DEL_RANGE")"    0
# An UNRESOLVABLE range fails closed even when gitleaks exits 0 with a summary.
check "unresolvable range fails closed"        "$(cd "$REPO" && run delete_only 'deadbeef..HEAD')" 1

echo "----"
[ "$fail" = 0 ] && echo "ALL PASS" || { echo "FAILURES"; exit 1; }
