#!/usr/bin/env bash
#
# Lose each container in turn, and check the stack comes back with its data intact.
#
#     make recovery-drill
#
# **What this does and does not test.** A restart policy covers a *crash* — a process that exits
# from an unhandled exception or an OOM — and `unless-stopped` restarts those. It does not cover
# `docker kill`, which Docker reads as operator intent and deliberately leaves stopped; that is
# true of `always` as well, which was measured rather than assumed. Killing PID 1 from inside is
# no help either: the kernel discards an in-namespace SIGKILL to PID 1.
#
# So this drills the property that is actually true and actually matters: **losing any single
# container costs no data, and the stack returns to full function when it comes back.** State
# lives in Postgres and Redis rather than in a process, and everything else reconnects.

set -euo pipefail

COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
API_URL="http://localhost:${API_PORT:-8010}"
WEB_URL="http://localhost:${WEB_PORT:-3010}"

DIM=$'\033[2m'; BOLD=$'\033[1m'; OFF=$'\033[0m'
GREEN=$'\033[38;5;108m'; RED=$'\033[38;5;167m'

fail() { echo "${RED}FAILED${OFF} $*" >&2; exit 1; }

games_recorded() {
  # Retried, because the first call after a datastore returns can still land on a connection
  # being re-established. `pool_pre_ping` discards the dead ones; this just gives it a moment.
  for _ in 1 2 3 4 5; do
    local body
    body=$(curl -sf --max-time 20 "$API_URL/games?limit=100" 2>/dev/null) || { sleep 3; continue; }
    printf '%s' "$body" \
      | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))' 2>/dev/null && return 0
    sleep 3
  done
  return 1
}

wait_for() {  # wait_for <url> <label>
  for _ in $(seq 1 60); do
    curl -sf --max-time 5 "$1" >/dev/null 2>&1 && return 0
    sleep 2
  done
  fail "$2 never came back"
}

echo "${BOLD}Recovery drill${OFF}"
wait_for "$API_URL/ready" "the API"
BEFORE=$(games_recorded)
echo "${DIM}baseline: $BEFORE games recorded${OFF}"
[ "$BEFORE" -gt 0 ] || fail "no games recorded — the drill would prove nothing"

# Least to most disruptive, so an early failure is easy to attribute.
for service in worker tournament web api redis postgres; do
  printf '  %-12s ' "$service"

  container=$($COMPOSE ps -q "$service" 2>/dev/null | head -1)
  [ -n "$container" ] || fail "$service was not running when the drill reached it"
  docker kill "$container" >/dev/null 2>&1 || true

  $COMPOSE up -d --no-build "$service" >/dev/null 2>&1 \
    || fail "$service could not be brought back"

  # `/ready` rather than `/health`: readiness checks the database and Redis, health does not.
  # Waiting on health let the drill proceed while Postgres was still starting, and the next call
  # failed on a connection that did not exist yet — the drill's bug, not the stack's. The API does
  # recover on its own, because the engine is built with `pool_pre_ping`.
  wait_for "$API_URL/ready" "the API after losing $service"

  AFTER=$(games_recorded)
  [ "$AFTER" = "$BEFORE" ] \
    || fail "$service: $BEFORE games before, $AFTER after — data was lost"

  status=$($COMPOSE ps --format '{{.Service}} {{.Status}}' | awk -v s="$service" '$1==s')
  case "$status" in
    *Up*) : ;;
    *) fail "$service is not up (status: ${status:-none})" ;;
  esac

  echo "${GREEN}recovered${OFF}${DIM} — $AFTER games intact${OFF}"
done

wait_for "$WEB_URL/" "the web app"
echo "${GREEN}every container was lost and recovered, with no data lost${OFF}"
