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
    # Real curl on a connection failure writes "000" via -w (NO trailing newline,
    # it is a -w format string) AND exits non-zero, so the probe's `... || echo 000`
    # appends a second "000" and command substitution yields exactly "000000".
    # Reproduce that byte-for-byte (printf, not echo) so the probe's ^000 network
    # arm actually fires instead of the `*)` fallback (bonnyr-f5 #193 r3).
    printf '000'; exit 7 ;;
  ratelimit)
    if [ "$is_token" = 1 ]; then echo '{"token":"T"}';
    elif [ "$is_authed" = 1 ]; then echo 429;
    else emit_challenge; echo 401; fi ;;
  server_error)                 # 5xx -> the `5??` arm -> unknown (fail closed)
    if [ "$is_token" = 1 ]; then echo '{"token":"T"}';
    elif [ "$is_authed" = 1 ]; then echo 503;
    else emit_challenge; echo 401; fi ;;
  unexpected)                   # any other code -> the `*)` arm -> unknown (fail closed)
    if [ "$is_token" = 1 ]; then echo '{"token":"T"}';
    elif [ "$is_authed" = 1 ]; then echo 418;
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
check "5xx -> refuse"                "$(verdict server_error)"         UNKNOWN
check "unexpected code -> refuse"    "$(verdict unexpected)"           UNKNOWN
check "exists -> refuse"             "$(verdict exists)"               EXISTS

# The 5xx and the catch-all `*)` arms must classify via their OWN messages, not leak
# into a wrong bucket (bonnyr-f5 #193 r4: untested arms). Assert each detail fires.
SE_DETAIL="$(run server_error)"; SE_DETAIL="${SE_DETAIL%%$'\n'*}"
case "$SE_DETAIL" in
  *"HTTP 503 (registry error"*) check "5xx arm fires (registry-error msg)" fires fires ;;
  *) printf 'FAIL  5xx arm fires -> got detail: %s\n' "$SE_DETAIL"; fail=1 ;;
esac
UE_DETAIL="$(run unexpected)"; UE_DETAIL="${UE_DETAIL%%$'\n'*}"
case "$UE_DETAIL" in
  *"HTTP 418 (unexpected"*) check "catch-all arm fires (unexpected msg)" fires fires ;;
  *) printf 'FAIL  catch-all arm fires -> got detail: %s\n' "$UE_DETAIL"; fail=1 ;;
esac

# The network failure must be classified by the dedicated ^000 arm, NOT the `*)`
# unexpected-code fallback: real curl yields the doubled "000000" shape and the old
# `000)` arm matched neither, so it was dead code (bonnyr-f5 #193 r3). Assert the
# arm's own message fires so a regression to the exact-match arm is caught.
NET_OUT="$(run network)"; NET_DETAIL="${NET_OUT%%$'\n'*}"
case "$NET_DETAIL" in
  *"network/curl failure (code '000000')"*) check "network arm fires (^000, not fallback)" fires fires ;;
  *) printf 'FAIL  network arm fires -> got detail: %s\n' "$NET_DETAIL"; fail=1 ;;
esac

# ─── F6: image-list single-source parity with docker-bake.hcl ────────────────
# `while read` not `mapfile`: mapfile is bash 4+, and this file is wired into
# `make script-selftests` -> `ci-gates` -> `pre-push`, which stock macOS runs
# under bash 3.2.57 (Makefile SHELL := /bin/bash). mapfile there is rc=127 and
# BLOCKS every push (bonnyr-f5 #193 M3).
LIST=()
while IFS= read -r _img; do LIST+=("$_img"); done < <(bash "$PROBE" --images)
check "image count is 7"             "${#LIST[@]}"                     7
# Enumerate the SAME group the guard counts — the DEFAULT group (the guard uses
# `docker buildx bake --print default | jq '.group.default.targets'`). The old test
# used an UNSCOPED `targets = [` sed that matched EVERY group's targets line, so
# adding any second bake group would make the test and the guard disagree about how
# targets are enumerated (bonnyr-f5 #193 r3 minor). Scope the HCL parse to the
# `group "default"` block so both read the same set without needing docker/buildx.
BAKE_TARGETS="$(awk '
  /^group[[:space:]]+"default"[[:space:]]*\{/ { ing = 1 }
  ing && /targets[[:space:]]*=/ {
    line = $0
    sub(/.*targets[[:space:]]*=[[:space:]]*\[/, "", line)
    sub(/\].*/, "", line)
    print line
    exit
  }
  ing && /^\}/ { ing = 0 }
' "$HERE/../../docker-bake.hcl" | tr -d '" ' | tr ',' '\n' | sort)"
LIST_TARGETS="$(printf '%s\n' "${LIST[@]}" | sed 's/^bnk-forge-//' | sort)"
if [ "$BAKE_TARGETS" = "$LIST_TARGETS" ]; then
  check "image list == bake group"   match                            match
else
  printf 'FAIL  image list vs bake group differ:\n--bake--\n%s\n--list--\n%s\n' "$BAKE_TARGETS" "$LIST_TARGETS"; fail=1
fi

echo "----"
[ "$fail" = 0 ] && echo "ALL PASS" || { echo "FAILURES"; exit 1; }
