#!/usr/bin/env bash
# health.sh — poll each backend's readiness endpoint via the bench-host's SSH session.
# Intended to be run from inside the VPC (e.g., over an SSM session or from the bench client EC2).
#
# Usage:
#   ./health.sh <pgvector_ip> <qdrant_ip> <weaviate_ip>
#
# Exits 0 only when all three respond healthy. Polls up to ~5 minutes.

set -euo pipefail

if [ "$#" -ne 3 ]; then
  echo "usage: $0 <pgvector_ip> <qdrant_ip> <weaviate_ip>" >&2
  exit 2
fi

PG_IP="$1"
QD_IP="$2"
WV_IP="$3"

DEADLINE=$(( $(date +%s) + 300 ))

check_pg() {
  # `nc` is the cheapest reachability check; pgready needs psql which we don't ship here.
  nc -z -w 2 "$PG_IP" 5432
}

check_qd() {
  curl -sf "http://$QD_IP:6333/readyz" >/dev/null
}

check_wv() {
  curl -sf "http://$WV_IP:8080/v1/.well-known/ready" >/dev/null
}

while true; do
  pg_ok=0; qd_ok=0; wv_ok=0
  if check_pg; then pg_ok=1; fi
  if check_qd; then qd_ok=1; fi
  if check_wv; then wv_ok=1; fi

  if [ "$pg_ok$qd_ok$wv_ok" = "111" ]; then
    echo "all healthy: pgvector=$PG_IP:5432 qdrant=$QD_IP:6333 weaviate=$WV_IP:8080"
    exit 0
  fi

  if [ "$(date +%s)" -ge "$DEADLINE" ]; then
    echo "timeout: pgvector=$pg_ok qdrant=$qd_ok weaviate=$wv_ok" >&2
    exit 1
  fi

  sleep 5
done
