#!/usr/bin/env bash
set -euo pipefail

# MAF-LOCAL-PURPOSE: docker-exec psql wrapper - resolves pg user/db from the running container, no hardcoded credentials

###############################################################################
# forge-db.sh — Forge Postgres query wrapper
#
# Usage:
#   bin/local/forge-db.sh sql '<SQL>'
#   bin/local/forge-db.sh tables [pattern]
#   bin/local/forge-db.sh cols <table>
#   bin/local/forge-db.sh --help
#
# Resolves POSTGRES_USER / POSTGRES_DB from the running container's own
# environment (docker exec ... env) on every invocation — no hardcoded
# user/db guessing.
#
# Env vars:
#   FORGE_DB_CONTAINER   Container name (default: bnk-forge-postgres)
#
# Committed to this repo. Not managed by the MAF framework installer/updater —
# see bin/lib/install-engine.sh (enumerate_framework_managed_paths never
# globs bin/local/**).
###############################################################################

CONTAINER="${FORGE_DB_CONTAINER:-bnk-forge-postgres}"

usage() {
    cat <<'USAGE_EOF'
bin/local/forge-db.sh <subcommand> [args]

Subcommands:
  sql '<SQL>'       Run an arbitrary SQL statement
  tables [pattern]  List public tables, optionally filtered (ILIKE %pattern%)
  cols <table>      List columns + types for <table>

Options:
  --help, -h        Show this help

Env vars:
  FORGE_DB_CONTAINER   default bnk-forge-postgres
USAGE_EOF
}

die() {
    echo "forge-db: $*" >&2
    exit 1
}

need_bin() {
    command -v "$1" >/dev/null 2>&1 || die "'$1' is required but not found in PATH"
}

require_container() {
    docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null | grep -q true \
        || die "container '$CONTAINER' is not running (set FORGE_DB_CONTAINER to override)"
}

# resolve_pg_env — sets PG_USER / PG_DB from the container's own environment.
resolve_pg_env() {
    local env_out
    env_out="$(docker exec "$CONTAINER" env)" || die "failed to read env from container '$CONTAINER'"

    PG_USER="$(echo "$env_out" | grep '^POSTGRES_USER=' | cut -d= -f2-)"
    PG_DB="$(echo "$env_out" | grep '^POSTGRES_DB=' | cut -d= -f2-)"

    [[ -n "$PG_USER" ]] || die "POSTGRES_USER not found in container '$CONTAINER' env"
    [[ -n "$PG_DB" ]] || die "POSTGRES_DB not found in container '$CONTAINER' env"
}

run_psql() {
    local sql="$1"
    docker exec -i "$CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -v ON_ERROR_STOP=1 -c "$sql"
}

cmd_sql() {
    local sql="${1:-}"
    [[ -n "$sql" ]] || die "sql: requires a SQL string"
    run_psql "$sql"
}

cmd_tables() {
    local pattern="${1:-}" sql
    if [[ -n "$pattern" ]]; then
        pattern="${pattern//"'"/"''"}"
        sql="SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name ILIKE '%${pattern}%' ORDER BY table_name;"
    else
        sql="SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY table_name;"
    fi
    run_psql "$sql"
}

cmd_cols() {
    local table="${1:-}"
    [[ -n "$table" ]] || die "cols: requires <table>"
    table="${table//"'"/"''"}"
    run_psql "SELECT column_name, data_type, is_nullable FROM information_schema.columns WHERE table_schema='public' AND table_name='${table}' ORDER BY ordinal_position;"
}

main() {
    case "${1:-}" in
        --help|-h) usage; exit 0 ;;
    esac

    [[ $# -ge 1 ]] || { usage >&2; die "requires a subcommand"; }

    need_bin docker
    require_container
    resolve_pg_env

    local sub="$1"; shift
    case "$sub" in
        sql) cmd_sql "$@" ;;
        tables) cmd_tables "$@" ;;
        cols) cmd_cols "$@" ;;
        *) die "unknown subcommand '$sub' (see --help)" ;;
    esac
}

main "$@"
