#!/usr/bin/env bash
# Mutation tests for scripts/verify-image-pins.sh (bonnyr-f5 #193 r3, B1).
#
# A fake manifest probe (IMAGE_PROBE) simulates the registry so the outcomes are
# proven WITHOUT a live registry, mirroring registry-tag-probe.test.sh's fake curl:
#   published tag  -> pass (exit 0) · unpublished tag -> fail (exit 1)
# plus: registry-qualified pins are probed, bare local build tags are skipped, an
# unresolved ${VAR} is a warning by default and a failure under --strict-unresolved.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$HERE/../verify-image-pins.sh"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# ─── fake manifest probe ─────────────────────────────────────────────────────
# "Published" set mimics the real GHCR contents for the bnk-forge repos plus the
# third-party pin: only 3.1.6 / latest / 0.3.0 exist; 4.0.0 does NOT.
cat > "$WORK/fakeprobe" <<'FAKE'
#!/usr/bin/env bash
ref="$1"; tag="${ref##*:}"
case "$tag" in
  3.1.6|latest|0.3.0) exit 0 ;;
  *) exit 1 ;;
esac
FAKE
chmod +x "$WORK/fakeprobe"

# ─── fixtures ────────────────────────────────────────────────────────────────
# A compose file whose bnk-forge pins default to an EXISTING tag, plus a bare
# local build tag (must be skipped) and a third-party pin (must be probed).
cat > "$WORK/good.yml" <<'YML'
services:
  api:
    image: ${BNK_FORGE_REGISTRY:-ghcr.io/f5devcentral}/bnk-forge-api:${BNK_FORGE_VERSION:-3.1.6}
  builtlocal:
    image: bnk-forge-agent:latest        # bare local build — no '/', must be SKIPPED
  proxy:
    image: tecnativa/docker-socket-proxy:0.3.0
YML

# Same, but the baked default is a tag nobody published.
cat > "$WORK/bad.yml" <<'YML'
services:
  api:
    image: ${BNK_FORGE_REGISTRY:-ghcr.io/f5devcentral}/bnk-forge-api:${BNK_FORGE_VERSION:-4.0.0}
YML

# A pin with a var that has NO default and NO override -> unresolved, alongside a
# resolvable pin (so the run is not vacuous — the unresolved one is what's under test).
cat > "$WORK/unresolved.yml" <<'YML'
services:
  proxy:
    image: tecnativa/docker-socket-proxy:0.3.0
  api:
    image: ${SOME_REGISTRY}/bnk-forge-api:${BNK_FORGE_VERSION:-3.1.6}
YML

run() { IMAGE_PROBE="$WORK/fakeprobe" bash "$SCRIPT" "$@"; }

fail=0
check() {  # name, actual-rc, want-rc
  if [ "$2" = "$3" ]; then printf 'PASS  %-40s -> rc=%s\n' "$1" "$2"
  else printf 'FAIL  %-40s -> got rc=%s want rc=%s\n' "$1" "$2" "$3"; fail=1; fi
}

# 1. All pins resolve to an existing tag -> pass.
out="$(run --file "$WORK/good.yml" 2>&1)" && rc=0 || rc=$?
check "existing tags -> pass" "$rc" 0
if printf '%s\n' "$out" | grep -q 'bnk-forge-agent'; then echo "FAIL  bare local tag was NOT skipped"; fail=1; fi

# 2. A forward-dated pin nobody published -> fail with manifest-unknown.
out="$(run --file "$WORK/bad.yml" 2>&1)" && rc=0 || rc=$?
check "unpublished tag -> fail" "$rc" 1
if ! printf '%s\n' "$out" | grep -q 'manifest unknown'; then echo "FAIL  missing 'manifest unknown' message"; fail=1; fi

# 3. Overriding the version onto an existing tag rescues the bad fixture -> pass.
run --file "$WORK/bad.yml" --version 3.1.6 >/dev/null 2>&1 && rc=0 || rc=$?
check "override to existing tag -> pass" "$rc" 0

# 4. Overriding onto a missing tag makes the good fixture fail -> fail.
run --file "$WORK/good.yml" --version 4.0.0 >/dev/null 2>&1 && rc=0 || rc=$?
check "override to missing tag -> fail" "$rc" 1

# 5. Unresolved ${VAR}: warning-only by default (pass), failure under --strict.
run --file "$WORK/unresolved.yml" >/dev/null 2>&1 && rc=0 || rc=$?
check "unresolved var -> pass (lenient)" "$rc" 0
run --file "$WORK/unresolved.yml" --strict-unresolved >/dev/null 2>&1 && rc=0 || rc=$?
check "unresolved var -> fail (strict)" "$rc" 1

echo "----"
[ "$fail" = 0 ] && echo "ALL PASS" || { echo "FAILURES"; exit 1; }
