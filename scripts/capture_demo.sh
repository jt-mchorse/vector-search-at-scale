#!/usr/bin/env bash
# Deterministic driver for the 60-second README demo (issue #12).
#
# Runs the two demo surfaces from the README's Demo section in sequence
# on a fresh clone with no AWS account, no Docker, no API key:
#
#   1. vector-bench run --backend stub      — the same hermetic harness
#                      path CI exercises. Prints the JSON shape that
#                      drives the real-backend studies (recall@k,
#                      p50/p95/p99 latency, build time, run_id).
#
#   2. cost_table.py --dry                  — generates the per-tier
#                      $/query markdown table that the README's
#                      "Costs" section locks against, then cats the
#                      file so the table is on camera.
#
# The output is the recording — when JT records the GIF/video, this
# script's stdout is what gets captured. Hermetic: no AWS account, no
# Docker, no Terraform required (the optional `make validate` third
# surface lives outside this driver because Terraform isn't a CI dep
# and the smoke test can't assume it).
#
# Variables:
#   CAPTURE_PACE_SECONDS  pause between sections (default 2 for
#                         recording; tests/test_capture_demo_smoke.py
#                         sets this to 0).
#   CAPTURE_DEMO_N        vector count for `vector-bench run`
#                         (default 1000 — matches README Demo block).
#   CAPTURE_DEMO_QUERIES  query count for `vector-bench run`
#                         (default 100 — matches README Demo block).
#
# Exit: 0 on full success; non-zero on any sub-step failure.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PACE="${CAPTURE_PACE_SECONDS:-2}"
N_VECTORS="${CAPTURE_DEMO_N:-1000}"
N_QUERIES="${CAPTURE_DEMO_QUERIES:-100}"

banner() {
  printf '\n'
  printf '═══ %s\n' "$1"
  printf '\n'
}

pace() {
  if [ "$PACE" != "0" ]; then
    sleep "$PACE"
  fi
}

cd "$REPO_ROOT"

# Per-run scratch so concurrent recordings (and the smoke test) don't
# collide. Cleaned up on exit including error paths.
TMPDIR_DEMO="$(mktemp -d -t vsc-capture-XXXXXX)"
cleanup() {
  rm -rf "$TMPDIR_DEMO"
}
trap cleanup EXIT INT TERM

# Resolve the Python interpreter from the active venv if one is present.
if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
  PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  PYTHON_BIN="python3"
fi

RESULTS_DIR="$TMPDIR_DEMO/results"
COST_MD="$TMPDIR_DEMO/cost_per_query.md"

banner "vector-search-at-scale · 60-second demo"
printf 'two surfaces · stub backend · no AWS, no Docker, no Terraform\n'
printf 'the same hermetic path CI exercises — drives the real-backend studies later.\n'
pace

banner "1/2 · vector-bench run · stub backend · same JSON shape real backends emit"
printf 'vector-bench run --backend stub --n %s --dim 768 --queries %s --top-k 10 \\\n' "$N_VECTORS" "$N_QUERIES"
printf '  --run-id demo-capture --results-dir <tmp> --force\n'
printf '  prints recall@k + p50/p95/p99 latency + ingest stats on stdout.\n\n'
vector-bench run \
  --backend     stub \
  --n           "$N_VECTORS" \
  --dim         768 \
  --queries     "$N_QUERIES" \
  --top-k       10 \
  --run-id      demo-capture \
  --results-dir "$RESULTS_DIR" \
  --force
pace

banner "2/2 · cost_table.py --dry · per-tier \$/query markdown (sourced from terraform/envs/benchmark)"
printf 'python scripts/cost_table.py --dry --out <tmp>/cost_per_query.md\n'
printf '  same format docs/cost_per_query.md ships (locked by test_cost_table.py).\n\n'
"$PYTHON_BIN" scripts/cost_table.py --dry --out "$COST_MD"
printf '\n─── rendered cost table ─────────────────────────────────────────────\n\n'
# Show the assumptions block + per-tier table only; trailing methodology
# notes are useful in the file but verbose for a 60s recording.
sed -n '1,30p' "$COST_MD"
pace

banner "done · stub-mode harness + cost table are wired end-to-end"
printf 'next stop for real backends:\n'
printf '  cd terraform/envs/benchmark && terraform init && terraform apply\n'
printf '  vector-bench run --backend pgvector --n 1000000 ...\n'
printf '  python scripts/cost_table.py --results-dir results/load  # real qps\n'
printf 'optional third surface JT runs locally before recording:\n'
printf '  make validate                # terraform validate across modules\n'
