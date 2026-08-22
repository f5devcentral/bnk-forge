#!/usr/bin/env bash
# Mutation tests for scripts/registry-tag-probe.sh (bonnyr-f5 #181 round 5, F1).
#
# A fake `curl` on PATH simulates the registry HTTP API so the four required
# outcomes are proven WITHOUT a live registry:
#     absent → publish · no-permission → refuse · network → refuse · exists → refuse
# plus the first-publish case F1 is about: a NONEXISTENT repo classifies absent
# (safe), NOT unknown — which the old CLI-text probe got wrong.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PROBE="$HERE/../registry-tag-probe.sh"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# ─── fake curl ───────────────────────────────────────────────────────────────
# Behaviour is driven by $SCENARIO. It recognises three call shapes the probe
# makes: (1) initial manifest GET (has -D <hdrfile>), (2) token fetch (URL
# contains /token), (3) authed manifest GET (has "Authorization: Bearer").
cat > "$WORK/curl" <<'FAKE'
#!/usr/bin/env bash
hdrfile=""; is_token=0; is_authed=0
prev=""
for a in "$@"; do
  case "$prev" in -D) hdrfile="$a" ;; esac
  case "$a" in
    */token*|*"/token?"*) is_token=1 ;;
    "Authorization: Bearer "*) is_authed=1 ;;
  esac
  case "$a" in *"/token"*) is_token=1 ;; esac
  prev="$a"
done

emit_challenge() {
  [ -n "$hdrfile" ] && printf 'www-authenticate: Bearer realm="https://ghcr.io/token",service="ghcr.io",scope="repository:o/i:pull"\r\n' > "$hdrfile"
}

case "$SCENARIO" in
  exists)
    if [ "$is_token" = 1 ]; then echo '{"token":"T"}';
    elif [ "$is_authed" = 1 ]; then echo 200;
    else emit_challenge; echo 401; fi ;;
  absent|nonexistent_repo)
    if [ "$is_token" = 1 ]; then echo '{"token":"T"}';
    elif [ "$is_authed" = 1 ]; then echo 404;
    else emit_challenge; echo 401; fi ;;
  no_permission_token)          # token endpoint denies (bonnyr's `denied`)
    if [ "$is_token" = 1 ]; then echo '{"errors":[{"code":"DENIED"}]}';
    elif [ "$is_authed" = 1 ]; then echo 200;   # never reached (no token)
    else emit_challenge; echo 401; fi ;;
  no_permission_manifest)       # token issued but manifest read forbidden
    if [ "$is_token" = 1 ]; then echo '{"token":"T"}';
    elif [ "$is_authed" = 1 ]; then echo 403;
    else emit_challenge; echo 401; fi ;;
  network)
    echo 000 ;;
  ratelimit)
    if [ "$is_token" = 1 ]; then echo '{"token":"T"}';
    elif [ "$is_authed" = 1 ]; then echo 429;
    else emit_challenge; echo 401; fi ;;
esac
FAKE
chmod +x "$WORK/curl"

run() { SCENARIO="$1" PATH="$WORK:$PInitial" REGISTRY=ghcr.io/o VERSION=3.1.6 bash "$PROBE"; }
PInitial="$PATH"

# aggregate one scenario into the caller's verdict: EXISTS/UNKNOWN/SAFE
verdict() {
  local out; out="$(run "$1")"
  if printf '%s' "$out" | grep -q '^exists'; then echo EXISTS
  elif printf '%s' "$out" | grep -q '^unknown'; then echo UNKNOWN
  else echo SAFE; fi
}

fail=0
check() {
  local name="$1" got="$2" want="$3"
  if [ "$got" = "$want" ]; then printf 'PASS  %-28s -> %s\n' "$name" "$got"
  else printf 'FAIL  %-28s -> got %s want %s\n' "$name" "$got" "$want"; fail=1; fi
}

# The caller policy: SAFE => publish; EXISTS/UNKNOWN => refuse (unless force).
check "absent -> publish"            "$(verdict absent)"               SAFE
check "nonexistent-repo -> publish"  "$(verdict nonexistent_repo)"     SAFE
check "no-permission(token)->refuse" "$(verdict no_permission_token)"  UNKNOWN
check "no-permission(manifest)->ref" "$(verdict no_permission_manifest)" UNKNOWN
check "network -> refuse"            "$(verdict network)"              UNKNOWN
check "rate-limit -> refuse"         "$(verdict ratelimit)"            UNKNOWN
check "exists -> refuse"             "$(verdict exists)"               EXISTS

# ─── F6: image-list single-source parity with docker-bake.hcl ────────────────
# `while read` not `mapfile`: mapfile is bash 4+, and this file is wired into
# `make script-selftests` -> `ci-gates` -> `pre-push`, which stock macOS runs
# under bash 3.2.57 (Makefile SHELL := /bin/bash). mapfile there is rc=127 and
# BLOCKS every push (bonnyr-f5 #193 M3).
LIST=()
while IFS= read -r _img; do LIST+=("$_img"); done < <(bash "$PROBE" --images)
check "image count is 7"             "${#LIST[@]}"                     7
BAKE_TARGETS="$(sed -n 's/.*targets = \[\(.*\)\].*/\1/p' "$HERE/../../docker-bake.hcl" | tr -d '" ' | tr ',' '\n' | sort)"
LIST_TARGETS="$(printf '%s\n' "${LIST[@]}" | sed 's/^bnk-forge-//' | sort)"
if [ "$BAKE_TARGETS" = "$LIST_TARGETS" ]; then
  check "image list == bake group"   match                            match
else
  printf 'FAIL  image list vs bake group differ:\n--bake--\n%s\n--list--\n%s\n' "$BAKE_TARGETS" "$LIST_TARGETS"; fail=1
fi

echo "----"
[ "$fail" = 0 ] && echo "ALL PASS" || { echo "FAILURES"; exit 1; }
