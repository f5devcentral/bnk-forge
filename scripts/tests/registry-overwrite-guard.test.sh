#!/usr/bin/env bash
# Tests for scripts/registry-overwrite-guard.sh (bonnyr-f5 #193 M2 + "give the
# single-sourced overwrite policy a test").
#
# A fake `docker` on PATH stands in for `docker buildx bake --print default`, and a
# fake probe stands in for registry-tag-probe.sh, so the policy is exercised with
# NO docker daemon and NO registry. The headline case is M2: the vacuity floor is
# scoped to the DEFAULT group, so a SECOND bake group does not wedge the gate.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
GUARD="$HERE/../registry-overwrite-guard.sh"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

if ! command -v jq >/dev/null 2>&1; then
  echo "SKIP  jq not installed — guard requires it to count the bake group"; exit 0
fi

# Fake docker: `docker buildx bake --print default` prints bake JSON that has the
# 7-target default group AND a SECOND group ("backend"). If the floor were unscoped
# it would count 10; scoped to .group.default.targets it must count 7 (the M2 fix).
cat > "$WORK/docker" <<'FAKE'
#!/usr/bin/env bash
echo '{"group":{"default":{"targets":["api","worker","beat","frontend","proxy","mcp","operator"]},"backend":{"targets":["api","worker","beat"]}},"target":{}}'
FAKE
chmod +x "$WORK/docker"

# Fake probe, driven by $SCEN. 7 images is the default-group count above.
cat > "$WORK/probe.sh" <<'FAKE'
#!/usr/bin/env bash
case "${SCEN:-}" in
  safe)       for i in 1 2 3 4 5 6 7; do printf 'absent\tref%s\tHTTP 404\n' "$i"; done ;;
  exists)     printf 'exists\tref1\tHTTP 200\n'; for i in 2 3 4 5 6 7; do printf 'absent\tref%s\t404\n' "$i"; done ;;
  short)      for i in 1 2 3 4 5 6; do printf 'absent\tref%s\t404\n' "$i"; done ;;  # only 6 => inconclusive
  unknown)    for i in 1 2 3 4 5 6 7; do printf 'unknown\tref%s\tHTTP 401 (auth/permission)\n' "$i"; done ;;  # 7 lines, all inconclusive
  malformed)  for i in 1 2 3 4 5 6 7; do printf 'bogus\tref%s\tunexpected token\n' "$i"; done ;; # 7 lines, unrecognised status
  empty_status) for i in 1 2 3 4 5 6 7; do printf '\tref%s\tdetail\n' "$i"; done ;;  # 7 lines, EMPTY status field
  unrunnable) exit 3 ;;
esac
FAKE
chmod +x "$WORK/probe.sh"

# A fake docker whose `bake --print` emits NON-JSON, so jq yields nothing and the
# guard's IMAGES_N is non-numeric -> the bake-parse/tooling arm fails closed, and
# FORCE must NOT rescue it (the guard refuses to guess the image count, by design).
mkdir -p "$WORK/badbin"
cat > "$WORK/badbin/docker" <<'FAKE'
#!/usr/bin/env bash
echo 'not json at all'
FAKE
chmod +x "$WORK/badbin/docker"

run() {  # SCEN, FORCE
  SCEN="$1" FORCE="${2:-}" PATH="$WORK:$PATH" \
    REGISTRY=ghcr.io/o VERSION=9.9.9 PROBE="$WORK/probe.sh" BAKE_FILE=/dev/null \
    bash "$GUARD" >/dev/null 2>&1
  echo $?
}
run_bad() {  # FORCE — the bad (non-JSON) docker wins on PATH so the bake floor can't be counted
  SCEN="safe" FORCE="${1:-}" PATH="$WORK/badbin:$WORK:$PATH" \
    REGISTRY=ghcr.io/o VERSION=9.9.9 PROBE="$WORK/probe.sh" BAKE_FILE=/dev/null \
    bash "$GUARD" >/dev/null 2>&1
  echo $?
}

fail=0
check() { if [ "$2" = "$3" ]; then printf 'PASS  %-46s rc=%s\n' "$1" "$2"; else printf 'FAIL  %-46s rc=%s want %s\n' "$1" "$2" "$3"; fail=1; fi; }

# M2 headline: default group = 7, probe classifies all 7 absent => SAFE (rc 0),
# even though a second bake group exists. Unscoped counting would make it 10 != 7
# and refuse.
check "second bake group does NOT wedge (safe)" "$(run safe)"       0
check "an existing manifest refuses"            "$(run exists)"     1
check "force overrides an existing manifest"    "$(run exists true)" 0
check "count mismatch => inconclusive refuse"   "$(run short)"      1
check "unknown status refuses (auth/net)"       "$(run unknown)"    1
check "force overrides an unknown status"       "$(run unknown true)" 0
check "malformed status fails closed"           "$(run malformed)"  1
check "empty status fails closed"               "$(run empty_status)" 1
check "force overrides a malformed status"      "$(run malformed true)" 0
check "unrunnable probe fails closed"           "$(run unrunnable)" 1
check "force overrides an unrunnable probe"      "$(run unrunnable true)" 0
check "bake parse failure fails closed"         "$(run_bad)"        1
check "force does NOT rescue a bake parse fail" "$(run_bad true)"   1

echo "----"
[ "$fail" = 0 ] && echo "ALL PASS" || { echo "FAILURES"; exit 1; }
