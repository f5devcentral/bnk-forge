#!/usr/bin/env bash
# scripts/lib/is-release-bot-subject.sh — the ONE release-bot own-commit
# fingerprint (bonnyr-f5 #193 r3 release/CI).
#
# The release bot's own automated release commit has a subject EXACTLY of the
# shape `release: vX.Y.Z ... [skip ci]` — a real version AND the trailing skip
# marker it appends (see the two `git commit` calls in .github/workflows/
# release.yml). Two places must recognise it and MUST NOT drift:
#   * the commit-lint gate (scripts/lint-commit-markers.sh), which exempts the
#     bot's own release commit from the marker rules; and
#   * release.yml's loop guard (the `guard` job), which decides should_run.
#
# release.yml's `guard` job runs WITHOUT a repo checkout (it reads only the event
# payload), so it cannot source this file; it therefore keeps a byte-identical
# INLINE copy of the two grep predicates below, and
# scripts/tests/lint-commit-markers.test.sh asserts that inline copy is
# byte-identical to this one so the two cannot silently diverge.
#
# NOTE ON FORGEABILITY: this reads the commit SUBJECT, which the committing client
# controls — it is NOT an unforgeable property. A human could hand-write
# `release: v0.0.1 ... [skip ci]` to self-exempt. The residual exposure is low: a
# squash-merged PR is linted through PR_TITLE (which gets NO exemption), and a
# skip-marked push to a protected branch produces no workflow run at all, so there
# is no run for the exemption to weaken. Do not describe this as unforgeable.
_is_release_bot_subject() {
  grep -qE '^release: v[0-9]+\.[0-9]+\.[0-9]+' <<< "$1" \
    && grep -qE '\[skip ci\]$' <<< "$1"
}
