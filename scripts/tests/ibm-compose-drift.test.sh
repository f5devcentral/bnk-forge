#!/usr/bin/env bash
# IBM installer / dist compose drift guard (bonnyr-f5 #193 r4 deploy minor).
#
# scripts/ibm_cloud_bnk_forge.sh embeds its OWN copy of the deploy compose (a
# heredoc), separate from dist/docker-compose.yml. The two are meant to deliver the
# SAME backend credential/security contract, but nothing enforced it and they had
# drifted — exactly the B-1 class (a fix applied to one compose path but not the
# other consumer). This test freezes the security-critical env treatment: for every
# key below, the value form (verbatim right-hand side) MUST be byte-identical in
# both files, so a future edit to one that is not mirrored in the other fails CI.
#
# It deliberately does NOT diff the whole file — the two legitimately differ
# (networking, build stanzas, placeholders). It pins only the credential/hardening
# env whose divergence is a security regression. The image-version pins are covered
# separately by deploy-version-lockstep.test.sh.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
DIST="$ROOT/dist/docker-compose.yml"
IBM="$ROOT/scripts/ibm_cloud_bnk_forge.sh"

# The credential/hardening env keys that MUST stay in lockstep across both paths.
KEYS=(
  DEFAULT_ADMIN_PASSWORD
  DEFAULT_ADMIN_MUST_CHANGE
  MCP_SERVICE_USERNAME
  MCP_SERVICE_PASSWORD
  ENVIRONMENT
  JWT_SECRET_KEY
  ENCRYPTION_KEY
  ALLOWED_ORIGINS
)

# Extract the value form (everything after the first `  <KEY>:`), trimmed. A
# passthrough key (`DEFAULT_ADMIN_PASSWORD:` with no value) yields the empty string
# in BOTH files, which still compares equal — so a drift to `${...:-}` on one side
# is caught.
_val() {  # file, key
  sed -nE "s/^[[:space:]]*$2:[[:space:]]*(.*)$/\1/p" "$1" | head -1
}

fail=0
for f in "$DIST" "$IBM"; do
  [ -f "$f" ] || { echo "FAIL  missing file: $f"; exit 1; }
done

for k in "${KEYS[@]}"; do
  dv="$(_val "$DIST" "$k")"
  iv="$(_val "$IBM" "$k")"
  # Both must actually contain the key (guard against a key being dropped from one).
  if ! grep -qE "^[[:space:]]*$k:" "$DIST"; then echo "FAIL  $k absent from dist/docker-compose.yml"; fail=1; continue; fi
  if ! grep -qE "^[[:space:]]*$k:" "$IBM";  then echo "FAIL  $k absent from IBM embedded compose"; fail=1; continue; fi
  if [ "$dv" = "$iv" ]; then
    printf 'PASS  %-26s = %s\n' "$k" "${dv:-<passthrough/empty>}"
  else
    printf 'FAIL  %-26s dist=[%s] ibm=[%s] (drift)\n' "$k" "$dv" "$iv"; fail=1
  fi
done

echo "----"
[ "$fail" = 0 ] && echo "ALL PASS — IBM embedded compose matches dist on the credential/hardening env" \
  || { echo "FAILURES — reconcile the IBM embedded compose with dist/docker-compose.yml"; exit 1; }
