#!/usr/bin/env bash
#
# Single source of truth for the gitleaks secret scan + its assertion backstop.
#
# Called by BOTH .github/workflows/ci.yml (the secret-scan job and the scheduled
# baseline job) AND `make secret-scan` / `make pre-push`, so a local run is
# byte-identical to CI -- #166: "a local gate that does not run the CI command is
# not a gate", and ci.yml's header claims `make pre-push` == CI.
#
# The gate must be able to tell "clean scan" from "did not run". gitleaks exits 0
# on a bad revision range OR a git dubious-ownership refusal, printing
# "ERR [git] ..." + "0 commits scanned" -- a silently blind green gate
# (bonnyr-f5 #182 r3 BLOCKER). So we capture the output and FAIL on: any
# "ERR [git]" line, a missing "commits scanned" line, 0 commits for a non-empty
# range, or a non-zero gitleaks exit (leaks found).
#
# RANGE selection:
#   * If the RANGE env var is SET (even to empty), it is used verbatim -- empty
#     means "scan all reachable history" (the scheduled baseline + first push).
#     CI computes it from the triggering event.
#   * If RANGE is UNSET, a local default is computed: everything since HEAD
#     diverged from its upstream tracking branch, falling back to full history.
set -uo pipefail

# gitleaks v8.30.1, pinned by digest so a re-tag cannot change what runs
# (bonnyr-f5 #182 r3 nit). Update the version comment when bumping the digest.
IMAGE="ghcr.io/gitleaks/gitleaks@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f"  # v8.30.1

repo_dir="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

# Resolve the range (see header). ${RANGE+set} distinguishes unset from empty.
if [ -n "${RANGE+set}" ]; then
  range="$RANGE"
else
  range=""
  if upstream="$(git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null)"; then
    if base="$(git merge-base "$upstream" HEAD 2>/dev/null)"; then
      range="${base}..HEAD"
    fi
  fi
fi

echo "gitleaks scanning range: ${range:-<all history>}  (repo: $repo_dir)"

# safe.directory whitelists the mount so git 2.35.2+ does not refuse it for
# dubious ownership (the image runs as root; the checkout is owned by another
# uid). GIT_CONFIG_* needs no writable HOME, unlike `git config --global`.
# --max-archive-depth 2: without it gitleaks defaults to 0 and NEVER looks inside
# tracked archives, so a secret shipped in a tarball is invisible (bonnyr-f5 #182
# r3 Major). Depth 2 covers e.g. a key inside a .tar.gz inside a .zip.
out="$(docker run --rm \
  -e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0=/repo \
  -v "$repo_dir:/repo:ro" -w /repo "$IMAGE" detect \
  --source=/repo --config=/repo/.gitleaks.toml --redact --verbose \
  --max-archive-depth 2 \
  ${range:+--log-opts="$range"} 2>&1)"
rc=$?
printf '%s\n' "$out"

# Strip ANSI so parsing is robust whether or not gitleaks colourises.
clean="$(printf '%s\n' "$out" | sed -E 's/\x1b\[[0-9;]*m//g')"

# 1) Any git error (bad range, dubious ownership) means the scan never saw the
#    repo/range -- fail even though gitleaks exited 0.
if grep -qE 'ERR \[git\]' <<< "$clean"; then
  echo "::error::gitleaks hit a git error (bad revision range or dubious ownership) -- the scan did not run"
  exit 1
fi

# 2) Positive evidence the scan ran: gitleaks always prints "<N> commits scanned"
#    in git mode. No such line == silence == must not pass.
scanned="$(grep -oE '[0-9]+ commits scanned' <<< "$clean" | grep -oE '^[0-9]+' | tail -n1)"
echo "gitleaks reported commits scanned: ${scanned:-<none>}"
if [ -z "$scanned" ]; then
  echo "::error::gitleaks printed no 'commits scanned' line -- no evidence the scan ran"
  exit 1
fi

# 3) Anti-vacuity backstop. gitleaks reports "0 commits scanned" in TWO cases: a
#    BLIND scan (broken range / dubious-ownership refusal — already caught by check
#    1's "ERR [git]"), AND, legitimately, a range that adds no content to scan.
#    `git log -p` shows only removed lines for a delete-only (or empty) push;
#    gitleaks scans ADDED content, so it counts 0 and exits 0 for a real, pushable
#    change (reproduced: a delete-only commit has rev-list count 1 yet "0 commits
#    scanned"). The old count==0 backstop RED that legitimate push and blocked the
#    hook (bonnyr-f5 #193 r3 M-7). So the scanned COUNT is not a reliable blind
#    signal. Instead assert the scan ran against REAL history by gitleaks' exit
#    status (checks 1/2/4) PLUS git's own ability to resolve the range: a range git
#    cannot enumerate is a broken scan (fail closed); a resolvable range that
#    gitleaks scanned cleanly is trustworthy regardless of the commit count.
if [ -n "$range" ]; then
  if ! git -C "$repo_dir" rev-list --count "$range" >/dev/null 2>&1; then
    echo "::error::gitleaks range '$range' does not resolve in git -- no evidence the scan ran against real history"
    exit 1
  fi
fi

# 4) Real leaks make gitleaks exit non-zero -- that must still fail here.
if [ "$rc" -ne 0 ]; then
  echo "::error::gitleaks exited $rc (leaks found or scan error)"
  exit "$rc"
fi

echo "secret-scan: OK"
