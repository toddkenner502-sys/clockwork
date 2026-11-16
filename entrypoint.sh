#!/bin/sh
# Derive the worker ordinal from the compose service name (worker-1 -> 0) so each
# replica picks a distinct identity and port pair.
set -e

n="${HOSTNAME##*-}"
case "$n" in
  ''|*[!0-9]*) n=1 ;;
esac
export WORKER_INDEX=$((n - 1))

mkdir -p "${DATA_DIR:-/data}"
touch /tmp/clockwork.heartbeat

exec python /app/worker.py
