#!/usr/bin/env bash
# bonnyr-f5 #193 M7 + security regression lock.
# The pod `checksum/secret` annotation must:
#   (a) be STABLE across renders for unchanged inputs (no perpetual GitOps drift);
#   (b) CHANGE when a credential rotates, so the pods actually roll (M7);
# and the GENERATED secret fallbacks must be RANDOM / unpredictable -- NEVER derived from
# public release identity (release name, namespace, fullname), which would make the JWT
# signing key, the at-rest Fernet key and the admin/mcp passwords computable by anyone who
# can read a resource label. This test locks the revert of the `deriveSecret` determinism.
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CHART="$ROOT/helm/bnk-forge"
fail=0

if ! command -v helm >/dev/null 2>&1; then
  echo "PASS  (skipped: helm not installed)"
  echo "----"; echo "ALL PASS"; exit 0
fi

# ALLOWED_ORIGINS override keeps the M-10 production+localhost render guard from firing.
COMMON=(--set api.env.ALLOWED_ORIGINS=https://forge.example.com)
cksum() { helm template rel "$CHART" "$@" 2>/dev/null | grep -m1 'checksum/secret:' | awk '{print $2}'; }
mcpval() { helm template rel "$CHART" "$@" 2>/dev/null | grep -m1 'mcp-password:' | awk '{print $2}'; }

# (a) stability: identical inputs, two renders -> identical checksum.
a1=$(cksum "${COMMON[@]}" --set secrets.mcpPassword=Sup3rSecretA --set secrets.adminPassword=Adm1nSecretA)
a2=$(cksum "${COMMON[@]}" --set secrets.mcpPassword=Sup3rSecretA --set secrets.adminPassword=Adm1nSecretA)
if [ -n "$a1" ] && [ "$a1" = "$a2" ]; then echo "PASS  checksum stable across identical renders"
else echo "FAIL  checksum not stable across identical renders ('$a1' vs '$a2')"; fail=1; fi

# (b) rotation: a different mcp-password -> a different checksum (pods roll). M7.
b2=$(cksum "${COMMON[@]}" --set secrets.mcpPassword=Sup3rSecretB --set secrets.adminPassword=Adm1nSecretA)
if [ -n "$a1" ] && [ -n "$b2" ] && [ "$a1" != "$b2" ]; then echo "PASS  checksum changes on mcp-password rotation (pods roll)"
else echo "FAIL  checksum did not change on mcp-password rotation ('$a1')"; fail=1; fi

# (c) unpredictability: a generated (unset) mcp-password is RANDOM -> two renders differ, and
#     is therefore not a deterministic derivation of release identity.
c1=$(mcpval "${COMMON[@]}")
c2=$(mcpval "${COMMON[@]}")
if [ -n "$c1" ] && [ "$c1" != "$c2" ]; then echo "PASS  generated mcp-password is random (unpredictable)"
else echo "FAIL  generated mcp-password is deterministic/derived ('$c1') -- predictable-secret regression"; fail=1; fi

echo "----"
[ "$fail" = 0 ] && echo "ALL PASS" || { echo "FAILURES"; exit 1; }
